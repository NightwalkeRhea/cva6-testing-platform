"""One campaign: a uGP3 process, the files it needs, and the server that
answers its evaluation requests.

uGP3 hands out a whole generation in a single call and blocks until every one
of them has a number. That call is where the two phases live: the whole
generation is built and probed first, one core each, and only then are the
campaigns run, longest first. The probe pass costs about 2% of a generation and
pays for itself twice -- it sizes each campaign's cycle cap, and it is what lets
the expensive queue be ordered at all.
"""
from __future__ import annotations

import gzip
import json
import os
import re
import shutil
import socket
import socketserver
import stat
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

from flowkit.shell import safe_rmtree

from . import genome as gen
from .cache import Cache
from .fitness import fitness, format_line, score
from .journal import Journal
from .model import Individual, Job, Status
from .manifest import write_json, write_text
from .pool import Pool
from .stages import HEAVY, PREP, Ctx, Pipeline, workdir_for

# Big, highly repetitive text: measured 48.9:1 and 24.2:1 on the real reports.
# Compressed there is no longer a reason to throw any of them away.
COMPRESS = ("fsim_out.rpt", "fsim_out_hier.rpt", "ind.dump")

# Written into the working directory by the tools themselves and worth nothing
# afterwards: a Verdi dump log with no waveform to go with it, a UCLI keystroke
# file, and two report-generator logs whose content is already in the reports.
DISCARD = ("novas_dump.log", "ucli.key", "fdb_fdb.log", "vc_fdb_report.log")


class Runner:
    """What a pool calls into: stages, cache, journal, bookkeeping."""

    def __init__(self, site, camp, layout, journal: Journal, executor,
                 use_cache: bool = True, report=None):
        self.site = site
        self.camp = camp
        self.layout = layout
        self.journal = journal
        self.ex = executor
        self.report = report or (lambda _line: None)
        self.cache = Cache(layout.cache(camp.inputs_hash), enabled=use_cache)
        self.pipeline: Pipeline | None = None
        self.generation = 0
        self.expected = 0
        self.seen = 0
        self.best = None            # best score so far, for the "<- best" mark
        self.faults = 0             # size of the fault list, learnt from the first campaign
        self._handed: dict[int, int] = {}   # generation -> individuals numbered so far

    def setup(self, ws, scaffold) -> None:
        ctx = Ctx(site=self.site, camp=self.camp, ws=ws, layout=self.layout,
                  ex=self.ex, scaffold=scaffold)
        self.pipeline = Pipeline(ctx, self.camp.pipeline)

    def number(self, generation: int, n: int) -> int:
        """First index for the next n individuals of this generation.

        uGP3 does not always ask for a generation in one go: it called the
        evaluator twice for generation 3 of the first real run. Numbering per
        call would then repeat i000 inside one generation and lose exactly the
        ordering the directory names exist for.
        """
        start = self._handed.get(generation, 0)
        self._handed[generation] = start + n
        return start

    def begin(self, generation: int, expected: int) -> None:
        """A second call for the same generation continues it, not restarts it."""
        if generation != self.generation:
            self.generation = generation
            self.seen = 0
            self.expected = 0
        self.expected += expected

    # -------------------------------------------------------------- the work

    def run_phase(self, job: Job, phase: str) -> Status:
        looked = False

        def cached(j: Job, _stage) -> bool:
            """As soon as the binary exists it has a name, and a name that has
            already been scored makes every stage after it pointless.

            Asked once: the callback runs between every pair of stages, but the
            name is set by the build and does not change afterwards, so a second
            question is a guaranteed miss that only spoils the hit rate.
            """
            nonlocal looked
            if phase != PREP or looked or not j.ind.sha or j.resolved:
                return False
            looked = True
            hit = self.cache.get(j.ind.sha)
            if hit is None:
                return False
            j.outcome = hit
            j.resolved = True
            return True

        return self.pipeline.run_phase(job, phase, cached)

    def finish(self, job: Job) -> None:
        """Terminal bookkeeping. Called once per individual, never twice."""
        if job.outcome.faults:
            self.faults = job.outcome.faults
        if job.resolved and not job.outcome.detail:
            job.outcome.detail = "cache hit"
        # Journal first: a crash between the two must not leave the cache
        # holding a result that the journal, the run's record, never saw.
        self.journal.record(job.ind, job.outcome)
        if not job.resolved and job.outcome.status is not Status.INFRA and job.ind.sha:
            # A malformed individual is as deterministic as a good one, so it is
            # worth remembering; an infrastructure failure says nothing about
            # the individual and must never be cached.
            self.cache.put(job.ind.sha, job.outcome)
        self._archive(job)
        self._progress(job)

    def event(self, kind: str, data: dict) -> None:
        """What the pool reports besides results. 'done' is already in the
        journal; the rest would otherwise be lost."""
        if kind == "done":
            return
        line = {"t": round(time.time(), 1), "generation": self.generation,
                "event": kind, **data}
        try:
            with open(self.layout.events, "a") as fh:
                fh.write(json.dumps(line) + "\n")
        except OSError:
            pass
        if kind in ("give-up", "bookkeeping-failed"):
            self.report(f"{kind}: {data.get('job', '?')} "
                        f"{data.get('detail') or data.get('error') or ''}"[:160])

    # ------------------------------------------------------------- artifacts

    def _archive(self, job: Job) -> None:
        wd = job.workdir
        if not wd or not wd.exists() or wd.parent != self.layout.individuals:
            return
        for name in DISCARD:
            (wd / name).unlink(missing_ok=True)
        for name in COMPRESS:
            _compress(wd / name)
        write_json(wd / "outcome.json", {
            "uid": job.ind.uid,
            "generation": job.ind.generation,
            "index": job.ind.index,
            "sha": job.ind.sha,
            "cores": job.cores,
            "fitness": fitness(job.outcome, self.camp.cycle_price),
            **job.outcome.to_cache(),
        })
        dest = self.layout.processed / wd.name
        if dest.exists():
            safe_rmtree(dest, self.layout.processed, ignore_errors=True)
        try:
            wd.replace(dest)        # a rename: atomic, and free
            job.workdir = dest
        except OSError:
            pass                    # a different filesystem, or already gone

    def _progress(self, job: Job) -> None:
        self.seen += 1
        row = job.outcome
        mark = {Status.OK: "ok", Status.MALFORMED: "malformed", Status.INFRA: "infra"}
        head = (f"g{self.generation:03d} i{job.ind.index:03d} "
                f"{self.seen:3d}/{self.expected:<3d} {job.ind.uid:<14}")
        body = f"{mark[row.status]:<9} {row.insns:>4d} insn"
        if row.status is Status.OK:
            body += f"  {row.coverage:6.2f}%  {row.cycles:>8d} cyc"
            # Marked by score, not by coverage: the line that says "best" while
            # it runs has to mean the same thing as the best/ left at the end.
            s = score(row.coverage, row.cycles, self.camp.cycle_price)
            if self.best is None or s > self.best:
                self.best = s
                body += "   <- best"
        elif row.detail:
            body += f"  {row.detail[:60]}"
        self.report(f"{head} {body}")

    def stats(self) -> dict:
        return {
            "cache_hits": self.cache.hits,
            "cache_misses": self.cache.misses,
            "hit_rate": round(self.cache.rate, 3),
            "faults": self.faults,
            "k_min": k_min(self.faults),
        }


def k_min(faults: int, per_task: int = 2046) -> int:
    """Tasks the fault list forces regardless of how many workers are asked for.

    Below this many workers the campaign costs the same total work and only
    finishes sooner; above it, every extra worker adds one more fixed cost.
    """
    return max(1, -(-faults // per_task)) if faults else 0


def _compress(path: Path) -> None:
    if not path.exists():
        return
    tmp = path.with_name(path.name + ".gz.tmp")
    try:
        with open(path, "rb") as src, gzip.open(tmp, "wb", compresslevel=6) as dst:
            shutil.copyfileobj(src, dst, length=1 << 20)
    except OSError:
        tmp.unlink(missing_ok=True)
        return
    tmp.replace(path.with_name(path.name + ".gz"))
    path.unlink(missing_ok=True)


class Campaign:
    def __init__(self, camp, ws, site, layout, runner: Runner, seed: int):
        self.camp = camp
        self.ws = ws
        self.site = site
        self.layout = layout
        self.runner = runner
        self.seed = seed
        self.dir = layout.ugp3
        self.proc: subprocess.Popen | None = None
        self.pools: list[Pool] = []
        self.stopping = False

    # ------------------------------------------------------------- preparing

    def prepare(self, tool_root: Path, genome, resume: bool = False) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        constraints = self.dir / "constraints.xml"
        population = self.dir / "population.settings.xml"
        settings = self.dir / "ugp3.settings.xml"
        # The interpreter running evosbst: the python3 on PATH may be older.
        evaluator = f"{sys.executable} {tool_root / 'evalclient.py'}"

        # On resume the genome comes back from the run's own frozen copy, never
        # from the current file: a genome edited in the meantime would change
        # the experiment halfway through without saying so.
        frozen = self.layout.input / "constraints.xml"
        if resume and frozen.exists():
            write_text(constraints, frozen.read_text())
        else:
            write_text(constraints, genome.constraints(ident=self.camp.name.upper()))
        # Same for the GA: a resumed run keeps the settings it started with.
        frozen_pop = self.layout.input / "population.settings.xml"
        source = frozen_pop if resume and frozen_pop.exists() else None
        write_text(population, gen.render_population(self.camp, constraints, evaluator, source))
        status = self.dir / "status.xml"
        recovery = str(status) if resume and status.exists() else ""
        write_text(settings, gen.render_settings(population, self.seed, recovery))

        # Kept next to the run: an experiment must be readable without the repo.
        for src in (constraints, population, settings):
            write_text(self.layout.input / src.name, src.read_text())

    # -------------------------------------------------------------- the tool

    def start(self, socket_path: Path) -> None:
        settings = self.dir / "ugp3.settings.xml"
        env = dict(os.environ)
        env["EVOSBST_SOCKET"] = str(socket_path)
        env["EVOSBST_CAMPAIGN"] = self.camp.name
        log = open(self.dir / "ugp3.log", "wb")
        self.proc = subprocess.Popen(
            [self.site.tools.ugp3, "--settingsFile", str(settings)],
            cwd=str(self.dir),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def resize(self, total: int) -> None:
        """A new core budget for the phases under way; the next ones read it
        from the site."""
        for pool in list(self.pools):
            pool.resize(total // pool.cores_each)

    def stop(self) -> None:
        self.stopping = True
        for pool in list(self.pools):
            pool.stop()
        if not self.proc:
            return
        if self.alive():
            try:
                os.killpg(self.proc.pid, 15)
            except (ProcessLookupError, PermissionError):
                pass
        try:
            self.proc.wait(timeout=30)      # reap it, or it lingers as a zombie
        except subprocess.TimeoutExpired:
            try:
                os.killpg(self.proc.pid, 9)
                self.proc.wait(timeout=10)
            except (ProcessLookupError, PermissionError, subprocess.TimeoutExpired):
                pass

    def snapshot(self) -> dict:
        pools = [p.snapshot() for p in self.pools]
        return {"alive": self.alive(), "seed": self.seed, "pools": pools}

    # ---------------------------------------------------------- the evaluator

    def evaluate(self, cwd: Path, generation: int, ids: list[str]) -> list[list[float]]:
        base = self.runner.number(generation, len(ids))
        jobs = []
        for i, uid in enumerate(ids):
            ind = Individual(
                campaign=self.camp.name,
                generation=generation,
                uid=uid,
                source=cwd / uid,
                index=base + i,
            )
            jobs.append(Job(ind=ind, workdir=workdir_for(self.layout, ind)))
        self.runner.begin(generation, len(jobs))

        self._phase(PREP, jobs, slots=self.site.cores.total, cores_each=1,
                    on_done=self._prep_done)

        heavy = [j for j in jobs if not j.resolved and j.outcome.status is Status.OK]
        heavy.sort(key=lambda j: -j.cost_hint)   # longest first: the right order for the tail
        cores = self.site.cores                  # read again: 'evosbst.py cores' may have run
        self._phase(HEAVY, heavy, slots=cores.in_flight, cores_each=cores.per_ind,
                    on_done=self.runner.finish)

        return [fitness(j.outcome, self.camp.cycle_price) for j in jobs]

    def _prep_done(self, job: Job) -> None:
        """Only the ones that stop here are finished; the rest go to the campaign."""
        if job.resolved or job.outcome.status is not Status.OK:
            self.runner.finish(job)

    def _phase(self, phase: str, jobs: list[Job], slots: int, cores_each: int, on_done) -> None:
        if not jobs:
            return
        pool = Pool(
            slots=slots,
            cores_each=cores_each,
            work=lambda job: self.runner.run_phase(job, phase),
            on_done=on_done,
            on_event=self.runner.event,
            retries=self.site.infra_retries,
            stall_seconds=self.site.infra_stall_seconds,
        )
        if self.stopping:
            pool.stop()
        self.pools.append(pool)
        try:
            pool.run(jobs)
        finally:
            self.pools.remove(pool)


# A request is one JSON line naming a generation's files: kilobytes.
REQUEST_MAX = 1 << 20
REQUEST_READ_SECONDS = 30.0
GENERATION_MAX = 1_000_000
IDS_MAX = 100_000
_UID = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}")


class BadRequest(ValueError):
    pass


def parse_request(line: bytes, campaigns: dict[str, Campaign]) -> tuple[Campaign, int, list[str]]:
    """The request uGP3's client sends, or BadRequest. Individuals are read
    from the campaign's own uGP3 directory and nowhere else: cwd must be that
    directory, and every id a bare file name in it."""
    if not line.endswith(b"\n"):
        raise BadRequest(f"request cut short, or over {REQUEST_MAX} bytes")
    try:
        req = json.loads(line)
    except ValueError as exc:
        raise BadRequest(f"not JSON: {exc}") from None
    if not isinstance(req, dict):
        raise BadRequest("not a JSON object")
    name = req.get("campaign")
    camp = campaigns.get(name) if isinstance(name, str) else None
    if camp is None:
        raise BadRequest(f"unknown campaign: {name!r}")
    cwd = req.get("cwd")
    if not isinstance(cwd, str) or Path(cwd).resolve() != camp.dir.resolve():
        raise BadRequest(f"cwd is not the campaign's uGP3 directory: {cwd!r}")
    generation = req.get("generation", 0)
    if type(generation) is not int or not 0 <= generation <= GENERATION_MAX:
        raise BadRequest(f"generation out of range: {generation!r}")
    ids = req.get("ids")
    if not isinstance(ids, list) or not 0 < len(ids) <= IDS_MAX:
        raise BadRequest(f"ids must be a list of 1 to {IDS_MAX} names")
    for uid in ids:
        if not isinstance(uid, str) or not _UID.fullmatch(uid):
            raise BadRequest(f"not a bare file name: {uid!r}")
    return camp, generation, ids


def _private_dir(d: Path) -> None:
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = os.lstat(d)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        raise PermissionError(f"{d} is not a directory of this user")
    os.chmod(d, 0o700)


def _same_user(conn: socket.socket) -> None:
    if not hasattr(socket, "SO_PEERCRED"):
        return
    creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    uid = struct.unpack("3i", creds)[1]
    if uid != os.getuid():
        raise PermissionError(f"peer uid {uid} is not this user")


class EvalServer:
    """One unix socket per run; the campaign name comes in with the request.

    Whoever connects can have the run build and simulate, so the socket sits
    in a directory only the user can enter, and any other peer is refused."""

    def __init__(self, path: Path, campaigns: dict[str, Campaign]):
        self.path = path
        self.campaigns = campaigns
        _private_dir(path.parent)
        if path.exists() or path.is_symlink():
            path.unlink()
        outer = self

        class Handler(socketserver.StreamRequestHandler):
            timeout = REQUEST_READ_SECONDS      # reading the request only

            def handle(self) -> None:
                try:
                    _same_user(self.request)
                    line = self.rfile.readline(REQUEST_MAX)
                    if not line:
                        return
                    camp, generation, ids = parse_request(line, outer.campaigns)
                    self.request.settimeout(None)    # the answer takes a generation
                    values = camp.evaluate(camp.dir, generation, ids)
                    reply = {"ok": True, "fitness": values}
                except Exception as exc:
                    reply = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                try:
                    self.wfile.write((json.dumps(reply) + "\n").encode())
                    self.wfile.flush()
                except OSError:
                    pass    # the client went away while the generation was running

        class Server(socketserver.ThreadingUnixStreamServer):
            daemon_threads = True
            allow_reuse_address = True

        self.server = Server(str(path), Handler)
        os.chmod(path, 0o600)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.path.unlink(missing_ok=True)
        try:
            self.path.parent.rmdir()
        except OSError:
            pass


def ask(socket_path: Path, campaign: str, cwd: Path, generation: int, ids: list[str]) -> dict:
    """Client side of the protocol. Kept here so both sides stay in step."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(None)
        sock.connect(str(socket_path))
        req = {
            "campaign": campaign,
            "cwd": str(cwd),
            "generation": generation,
            "ids": ids,
        }
        sock.sendall((json.dumps(req) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
    return json.loads(buf.decode())


def write_fitness(path: Path, values: list[list[float]]) -> None:
    write_text(path, "".join(format_line(v) + "\n" for v in values))
