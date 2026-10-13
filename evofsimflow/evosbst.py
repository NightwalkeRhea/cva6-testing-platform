#!/usr/bin/env python3
"""evosbst: evolve SBST programs against a fault-simulation campaign.

    evosbst.py check    conf/campaigns/alu.toml       is this machine ready? reads only
    evosbst.py setup    conf/campaigns/alu.toml       check and freeze the inputs
    evosbst.py run      conf/campaigns/alu.toml       start an evolution
    evosbst.py resume   <run-id>                 continue an interrupted one
    evosbst.py reseed   <run-id> [--seed N]      new trajectory, same cache
    evosbst.py status   [run-id]                 what is happening right now
    evosbst.py stop     <run-id>                 ask a run to wind down
    evosbst.py cores    <run-id> N [--per-ind M] change the cores of a running run
    evosbst.py selftest                          exercise everything, no tools

One run is one DUT in one process, holding the core budget it is given. To
work on two DUTs at once, start two runs; splitting the machine between them
is a decision made outside, not inside.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import signal
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from evokit import __version__                                       # noqa: E402
from evokit import genome as gen                                     # noqa: E402
from evokit import manifest as mf                                    # noqa: E402
from evokit import preflight as pre                                  # noqa: E402
from evokit import workspace as wsmod                                # noqa: E402
from evokit.campaign import Campaign, EvalServer, Runner            # noqa: E402
from evokit.config import Campaign as CampaignCfg                    # noqa: E402
from evokit.config import ConfigError, Cores, RunLayout, Site, make_run_id  # noqa: E402
from evokit.fitness import score                                     # noqa: E402
from evokit.journal import Journal                                   # noqa: E402
from flowkit.shell import LocalExecutor, safe_rmtree                  # noqa: E402

DEFAULT_SITE = HERE / "conf" / "site.toml"
EXAMPLE_SITE = HERE / "conf" / "site.example.toml"


# --------------------------------------------------------------------- setup


def prepare(site: Site, camp: CampaignCfg, layout: RunLayout, force: bool = False):
    ws = wsmod.ensure(camp, site, layout.workspaces(), camp.workspace_mode, force)
    camp.inputs_hash = mf.inputs_hash(mf.hash_parts(site, camp, ws))
    scaffold = gen.Scaffold(
        boot=camp.boot,
        cc=site.tools.cc,
        sbst_dir=HERE / "genome" / "sbst",
        linker_script=site.repo / "tests" / "htif.ld",
    )
    scaffold.check()
    return ws, scaffold, gen.load(camp.genome_file)


def cmd_check(args) -> int:
    """Is this machine ready? Reads only, creates nothing."""
    site = Site.load(Path(args.site), args.set)
    camp = CampaignCfg.load(Path(args.campaign), args.set)
    items = pre.inspect(site, camp, LocalExecutor())
    print(pre.report(items, site))
    gaps = pre.missing(items)
    if gaps:
        print(f"\n{len(gaps)} missing.\n\n{pre.advice(gaps)}")
    broken = ""
    if camp.genome_file.exists():
        try:
            gen.load(camp.genome_file)
        except ValueError as exc:
            broken = f"genome {exc}"
            print(f"\n{broken}")
    if gaps or broken:
        return 1
    print("\nready.")
    return 0


def cmd_setup(args) -> int:
    site = Site.load(Path(args.site), args.set)
    camp = CampaignCfg.load(Path(args.campaign), args.set)
    # Before anything is created, and against the flow's own files rather than
    # the copies made from them.
    items = pre.inspect(site, camp, LocalExecutor())
    gaps = pre.missing(items)
    if gaps:
        print(pre.report(items, site))
        print(f"\n{len(gaps)} missing, nothing was created.\n\n{pre.advice(gaps)}")
        return 1
    layout = RunLayout(site.runs, "_setup")
    layout.workspaces().mkdir(parents=True, exist_ok=True)
    ws, _scaffold, spec = prepare(site, camp, layout, args.force)

    print(f"repo   : {site.repo}")
    print(f"runs   : {site.runs}")
    print(f"cores  : {site.cores.total} total, {site.cores.per_ind} per individual "
          f"=> {site.cores.in_flight} individuals in flight")
    print(f"\ncampaign '{camp.name}'   dut: {camp.dut}")
    print(f"  workspace   : {ws.root}  ({ws.mode})")
    print(f"  simv        : {ws.simv}")
    print(f"  fault list  : {ws.sff}")
    print(f"  inputs-hash : {camp.inputs_hash}")
    print(f"  boot        : {camp.boot}   genome: {camp.genome_file.name} ({spec.summary})")
    print(f"  pipeline    : {' -> '.join(camp.pipeline)}")
    print(f"  GA          : mu={camp.ga.mu} lambda={camp.ga.lam} "
          f"generations={camp.ga.generations}")
    if camp.ga.lam % site.cores.in_flight:
        print(f"  note        : lambda={camp.ga.lam} is not a multiple of "
              f"{site.cores.in_flight} individuals in flight, so the last wave "
              f"of each generation leaves cores idle")

    print("\nready.")
    return 0


# ----------------------------------------------------------------------- run


class Run:
    def __init__(self, site: Site, camp: CampaignCfg, layout: RunLayout,
                 seed: int, resume: bool, use_cache: bool):
        self.site = site
        self.camp = camp
        self.layout = layout
        self.seed = seed
        self.resume = resume
        self.ex = LocalExecutor()
        self.stopping = threading.Event()
        self.hard = False
        layout.create()
        self.journal = Journal(layout.journal)
        self.log = open(layout.root / "run.log", "a")
        self.runner = Runner(site, camp, layout, self.journal, self.ex,
                             use_cache, report=self._say)
        self.campaign: Campaign | None = None
        self.server: EvalServer | None = None

    # ------------------------------------------------------------- reporting

    def _say(self, line: str) -> None:
        print(line, flush=True)

    def _event(self, kind: str, data: dict) -> None:
        self.log.write(f"{time.strftime('%H:%M:%S')} {kind} {json.dumps(data)}\n")
        self.log.flush()

    # ------------------------------------------------------------- lifecycle

    def start(self, ws, scaffold, spec) -> None:
        _write_lock(self.layout)
        if self.resume:
            self._gc_orphans()

        self.runner.setup(ws, scaffold)
        self.campaign = Campaign(self.camp, ws, self.site, self.layout,
                                 self.runner, self.seed)
        self.campaign.prepare(HERE, spec, resume=self.resume)
        if not self.resume:     # a resumed run keeps the record of how it started
            mf.write_json(
                self.layout.manifest,
                mf.build_manifest(self.site, self.camp, ws, self.seed, self.layout.run_id),
            )
        self.server = EvalServer(self.layout.socket, {self.camp.name: self.campaign})
        self.server.start()
        self.campaign.start(self.layout.socket)
        self._event("started", {"campaign": self.camp.name, "seed": self.seed})

    def _gc_orphans(self) -> None:
        """A directory left directly under individuals/ was interrupted: the
        tools leave a live database and a half-written dump behind, and neither
        can be restarted from. Throw it away and let it be re-evaluated."""
        removed = 0
        for d in sorted(self.layout.individuals.glob("*")):
            if d.is_dir() and d != self.layout.processed:
                safe_rmtree(d, self.layout.individuals, ignore_errors=True)
                removed += 1
        self._event("gc", {"removed": removed})

    def wait(self) -> int:
        writer = threading.Thread(target=self._state_loop, daemon=True)
        writer.start()
        try:
            while not self.stopping.is_set():
                if not self.campaign.alive():
                    break
                time.sleep(2.0)
        finally:
            # A stop that was asked for ends uGP3 with a signal: not a failure.
            requested = self.stopping.is_set()
            self.shutdown()

        rc = 0
        code = self.campaign.proc.poll() if self.campaign.proc else None
        if code and not requested:
            print(f"uGP3 exited with {code}; see {self.campaign.dir / 'ugp3.log'}",
                  file=sys.stderr)
            rc = 1
        self.collect_best()
        self.report()
        return rc

    def request_stop(self, hard: bool = False) -> None:
        self.hard = self.hard or hard
        self.stopping.set()

    def shutdown(self) -> None:
        self.stopping.set()
        if self.campaign:
            self.campaign.stop()
        # A second signal must cut short a wait that is already under way.
        deadline = time.monotonic() + (0 if self.hard else 900)
        while time.monotonic() < deadline and not self.hard:
            if not any(p.snapshot()["running"] for p in self.campaign.pools):
                break
            time.sleep(2.0)
        killed = self.ex.kill_all()
        self._event("stop", {"hard": self.hard, "killed": killed})
        if self.server:
            self.server.stop()
        # One last write, so a finished run does not leave a state file that
        # still claims it is halfway through a generation.
        self._write_state()
        self.layout.lock.unlink(missing_ok=True)
        self.log.close()

    # ---------------------------------------------------------------- output

    def _score(self, row: dict) -> float:
        """A journal row, ranked the way the search ranked it."""
        return score(float(row["coverage"] or 0.0), int(row["cycles"] or 0),
                     self.camp.cycle_price)

    def collect_best(self) -> None:
        """The winner, kept where it can be found without reading the journal.

        Ranked by score and not by coverage alone: the search spent the whole
        run preferring one of them, and a best/ that disagreed with it would
        make the run contradict itself.
        """
        rows = [r for r in self.journal.read() if r["status"] == "ok"]
        if not rows:
            return
        best = max(rows, key=self._score)
        src = _processed_dir(self.layout, best)
        dest = self.layout.best
        safe_rmtree(dest, self.layout.root, ignore_errors=True)
        if src and src.exists():
            shutil.copytree(src, dest)
        else:
            dest.mkdir(parents=True, exist_ok=True)
        mf.write_json(dest / "best.json",
                      dict(best, score=round(self._score(best), 6),
                           cycle_price=self.camp.cycle_price))

    def report(self) -> None:
        rows = self.journal.read()
        ok = [r for r in rows if r["status"] == "ok"]
        print(f"\nevaluated {len(rows)}: {len(ok)} scored, "
              f"{len([r for r in rows if r['status'] == 'malformed'])} malformed, "
              f"{len([r for r in rows if r['status'] == 'infra'])} infrastructure")
        if ok:
            best = max(ok, key=self._score)
            print(f"best {float(best['coverage']):.2f}%  "
                  f"(score {self._score(best):.2f}, gen {best['generation']} "
                  f"{best['uid']}, {best['cycles']} cycles) -> {self.layout.best}")
        print(f"run {self.layout.run_id}  in {self.layout.root}")

    def _state(self) -> dict:
        return {
            "run_id": self.layout.run_id,
            "dut": self.camp.dut,
            "pid": os.getpid(),
            "cores": {"total": self.site.cores.total,
                      "per_ind": self.site.cores.per_ind,
                      "in_flight": self.site.cores.in_flight},
            "generation": self.runner.generation,
            "progress": [self.runner.seen, self.runner.expected],
            "campaign": self.campaign.snapshot() if self.campaign else {},
            "cache": self.runner.stats(),
        }

    def _write_state(self) -> None:
        try:
            mf.write_json(self.layout.state, self._state())
        except OSError:
            pass

    def _state_loop(self) -> None:
        while not self.stopping.is_set():
            self._take_cores_request()
            self._write_state()
            time.sleep(5.0)

    def _take_cores_request(self) -> None:
        """A budget left by 'evosbst.py cores'. Free slots start work at once,
        extra ones end when their individual does, per_ind counts from the next
        generation. A resume starts again from site.toml and --set."""
        path = self.layout.cores_request
        try:
            want = json.loads(path.read_text())
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            want = {"error": str(exc)}
        path.unlink(missing_ok=True)
        try:
            cores = Cores(total=int(want["total"]),
                          per_ind=int(want.get("per_ind", self.site.cores.per_ind)))
            cores.check()
        except (AttributeError, KeyError, TypeError, ValueError, ConfigError) as exc:
            self._event("cores-refused", {"request": want, "error": str(exc)})
            return
        self.site.cores = cores
        if self.campaign:
            self.campaign.resize(cores.total)
        self._event("cores", {"total": cores.total, "per_ind": cores.per_ind})
        self._say(f"cores  : now {cores.total} in {cores.in_flight} slots of {cores.per_ind}")


def _processed_dir(layout: RunLayout, row: dict) -> Path | None:
    name = f"g{int(row['generation']):03d}_i{int(row.get('index') or 0):03d}_{row['uid']}"
    p = layout.processed / name
    return p if p.exists() else None


def cmd_run(args) -> int:
    site = Site.load(Path(args.site), args.set)
    camp = CampaignCfg.load(Path(args.campaign), args.set)
    seed = args.seed or camp.ga.seed or random.SystemRandom().randrange(1, 2**31)

    tmp = RunLayout(site.runs, "_setup")
    tmp.workspaces().mkdir(parents=True, exist_ok=True)
    ws, scaffold, spec = prepare(site, camp, tmp)

    site.runs.mkdir(parents=True, exist_ok=True)
    layout = RunLayout(site.runs, make_run_id(args.label or camp.dut, site.runs))
    return _launch(site, camp, layout, seed, ws, scaffold, spec,
                   resume=False, use_cache=not args.no_cache)


def cmd_resume(args) -> int:
    site = Site.load(Path(args.site), args.set)
    layout = RunLayout(site.runs, args.run_id)
    data = json.loads(layout.manifest.read_text())["campaign"]
    camp = CampaignCfg.load(Path(data["config"]), args.set)
    tmp = RunLayout(site.runs, "_setup")
    ws, scaffold, spec = prepare(site, camp, tmp)
    # Only with the inputs the run started with: a campaign file edited since,
    # or other --set values, would change the experiment halfway through.
    now = {"inputs_hash": camp.inputs_hash, "ga": json.loads(json.dumps(vars(camp.ga)))}
    changed = [k for k, v in now.items() if data.get(k) != v]
    if changed:
        print(f"not resuming {args.run_id}: {', '.join(changed)} differ from its manifest.",
              file=sys.stderr)
        print("Pass the --set values it started with, or start a new run with 'reseed'.",
              file=sys.stderr)
        return 2
    return _launch(site, camp, layout, data["evolutionary_seed"], ws, scaffold, spec,
                   resume=True, use_cache=True)


def cmd_reseed(args) -> int:
    site = Site.load(Path(args.site), args.set)
    old = RunLayout(site.runs, args.run_id)
    data = json.loads(old.manifest.read_text())["campaign"]
    camp = CampaignCfg.load(Path(data["config"]), args.set)
    seed = args.seed or random.SystemRandom().randrange(1, 2**31)
    tmp = RunLayout(site.runs, "_setup")
    ws, scaffold, spec = prepare(site, camp, tmp)
    layout = RunLayout(site.runs, make_run_id(camp.dut, site.runs))
    print(f"reseeding from {args.run_id}: same frozen inputs, same cache, seed {seed}")
    return _launch(site, camp, layout, seed, ws, scaffold, spec,
                   resume=False, use_cache=True)


def _launch(site, camp, layout, seed, ws, scaffold, spec,
            resume: bool, use_cache: bool) -> int:
    if layout.lock.exists() and _alive(layout.lock):
        print(f"run {layout.run_id} is already running (see {layout.lock})")
        return 1
    if not layout.socket_fits():
        print(f"run directory too deep for a unix socket: {layout.socket}", file=sys.stderr)
        print("set paths.runs in conf/site.toml to a shorter path.", file=sys.stderr)
        return 2
    # Checked up front. Missing pieces would otherwise show up hours later as
    # every individual failing for no visible reason -- and a missing probe
    # simulator counts as an infrastructure failure, so it would be retried for
    # half an hour per individual before anyone was told.
    gaps = pre.missing(pre.inspect(site, camp, LocalExecutor()))
    if gaps:
        for item in gaps:
            print(f"missing: {item.what} -- {item.where}", file=sys.stderr)
        print("run 'evosbst.py check' for the whole list.", file=sys.stderr)
        return 2
    run = Run(site, camp, layout, seed, resume, use_cache)

    def handler(signum, _frame):
        hard = run.stopping.is_set()
        print("\nstopping" + (" now" if hard else "; press again to kill running jobs"))
        run.request_stop(hard)

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)

    print(f"run    : {layout.run_id}")
    print(f"dir    : {layout.root}")
    print(f"dut    : {camp.dut}    seed {seed}    inputs-hash {camp.inputs_hash}")
    print(f"cores  : {site.cores.total} in {site.cores.in_flight} slots of "
          f"{site.cores.per_ind}")
    run.start(ws, scaffold, spec)
    return run.wait()


# ------------------------------------------------------------------- status


def cmd_status(args) -> int:
    site = Site.load(Path(args.site), args.set)
    runs = sorted(
        (p.parent for p in site.runs.glob("*/manifest.json")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if args.run_id:
        one = site.runs / args.run_id
        if not one.is_dir():
            print(f"no such run: {one}", file=sys.stderr)
            return 1
        runs = [one]
    elif not runs:
        print(f"no runs under {site.runs}")
        return 0
    for root in runs[: (1 if args.run_id else args.last)]:
        _print_run(root)
    return 0


def _print_run(root: Path) -> None:
    layout = RunLayout(root.parent, root.name)
    print(f"\n=== {root.name} ===")
    state = {}
    if layout.state.exists():
        try:
            state = json.loads(layout.state.read_text())
        except json.JSONDecodeError:
            pass
    live = layout.lock.exists() and _alive(layout.lock)
    running = sorted(
        d.name for d in layout.individuals.glob("*")
        if d.is_dir() and d != layout.processed
    )
    print(f"  state       : {'running' if live else 'not running'}"
          f"{'  pid ' + str(state['pid']) if live and state.get('pid') else ''}")
    if state:
        cores = state.get("cores", {})
        seen, expected = state.get("progress", [0, 0])
        print(f"  cores       : {cores.get('total')} in {cores.get('in_flight')} slots "
              f"of {cores.get('per_ind')}")
        print(f"  generation  : {state.get('generation')}   {seen}/{expected} done")
        cache = state.get("cache", {})
        if cache.get("faults"):
            print(f"  fault list  : {cache['faults']} faults, k_min={cache['k_min']} "
                  f"(workers are free up to there)")
        if cache.get("cache_hits") or cache.get("cache_misses"):
            print(f"  cache       : {cache['cache_hits']} hits / "
                  f"{cache['cache_hits'] + cache['cache_misses']} lookups")
        for pool in state.get("campaign", {}).get("pools", []):
            if pool.get("degraded"):
                print(f"  DEGRADED    : {pool['degraded']}")
    if running:
        shown = ", ".join(running[:6]) + (" ..." if len(running) > 6 else "")
        print(f"  in flight   : {len(running)}  ({shown})")

    rows = Journal(layout.journal).read()
    if not rows:
        print("  evaluated   : none yet")
        return
    ok = [r for r in rows if r["status"] == "ok"]
    bad = [r for r in rows if r["status"] == "malformed"]
    infra = [r for r in rows if r["status"] == "infra"]
    print(f"  evaluated   : {len(rows)}  ({len(ok)} scored, {len(bad)} malformed, "
          f"{len(infra)} infrastructure)")
    if ok:
        # From the manifest, so a run is always read back at the price it was
        # scored with. A run made before there was one ranks on coverage alone,
        # which is what it did.
        price = _price_of(layout)
        best = max(ok, key=lambda r: score(float(r["coverage"] or 0.0),
                                           int(r["cycles"] or 0), price))
        span = max(float(r["ts"]) for r in rows) - min(float(r["ts"]) for r in rows)
        rate = 3600 * len(rows) / span if span > 0 else 0.0
        print(f"  best        : {float(best['coverage']):.2f}%  "
              f"(score {score(float(best['coverage'] or 0.0), int(best['cycles'] or 0), price):.2f}"
              f", gen {best['generation']} {best['uid']}, {best['cycles']} cycles)")
        print(f"  throughput  : {rate:.1f} individuals/h")


def _price_of(layout: RunLayout) -> float:
    try:
        data = json.loads(layout.manifest.read_text())
        return float(data["campaign"].get("cycle_price", 0.0))
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return 0.0


def cmd_stop(args) -> int:
    site = Site.load(Path(args.site), args.set)
    layout = RunLayout(site.runs, args.run_id)
    if not _alive(layout.lock):
        print("that run is not running (no lock, or a stale one)")
        return 1
    pid = int(layout.lock.read_text().split()[0])
    os.kill(pid, signal.SIGTERM)
    print(f"asked pid {pid} to wind down; in-flight jobs are allowed to finish")
    return 0


def cmd_cores(args) -> int:
    site = Site.load(Path(args.site), args.set)
    layout = RunLayout(site.runs, args.run_id)
    if not _alive(layout.lock):
        print("that run is not running (no lock, or a stale one)")
        return 1
    per_ind = args.per_ind
    if not per_ind:             # keep the run's own, which may differ from site.toml
        try:
            per_ind = int(json.loads(layout.state.read_text())["cores"]["per_ind"])
        except (OSError, ValueError, KeyError, TypeError):
            per_ind = site.cores.per_ind
    cores = Cores(total=args.total, per_ind=per_ind)
    cores.check()
    tmp = layout.cores_request.with_suffix(".tmp")
    tmp.write_text(json.dumps({"total": cores.total, "per_ind": cores.per_ind}))
    os.replace(tmp, layout.cores_request)
    print(f"asked {args.run_id} for {cores.total} cores in {cores.in_flight} slots of "
          f"{cores.per_ind}; taken within seconds, a slot too many ends with its individual")
    return 0


def cmd_selftest(args) -> int:
    sys.path.insert(0, str(HERE / "tests"))
    import selftest

    return selftest.main(verbose=args.verbose)


# ------------------------------------------------------------------- helpers


def _write_lock(layout: RunLayout) -> None:
    mf.write_text(layout.lock, f"{os.getpid()} {time.time():.0f}\n")


def _alive(lock: Path) -> bool:
    """The lock's pid is running and is an evosbst: after a crash the pid
    can belong to another process."""
    try:
        pid = int(lock.read_text().split()[0])
        return b"evosbst" in Path(f"/proc/{pid}/cmdline").read_bytes()
    except (OSError, ValueError, IndexError):
        return False


def parse(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="evosbst.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"evosbst {__version__}")
    p.add_argument("--site", default=str(DEFAULT_SITE), help="site configuration")
    p.add_argument("--set", action="append", default=[], metavar="k.k=v",
                   help="override any configuration key")

    # The same two options again on every subcommand, because that is where they
    # get typed. They need their own destinations: a subparser parses into a
    # fresh namespace, so sharing a name would drop whatever came before the
    # subcommand instead of adding to it.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--site", dest="_site", default=None,
                        help="site configuration")
    common.add_argument("--set", action="append", dest="_set", default=None,
                        metavar="k.k=v", help="override any configuration key")

    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("check", parents=[common])
    s.add_argument("campaign")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("setup", parents=[common])
    s.add_argument("campaign")
    s.add_argument("--force", action="store_true",
                   help="rebuild the workspace from its source")
    s.set_defaults(func=cmd_setup)

    s = sub.add_parser("run", parents=[common])
    s.add_argument("campaign")
    s.add_argument("--seed", type=int, default=0)
    s.add_argument("--label", default="", help="name the run after this instead of the dut")
    s.add_argument("--no-cache", action="store_true")
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("resume", parents=[common])
    s.add_argument("run_id")
    s.set_defaults(func=cmd_resume)

    s = sub.add_parser("reseed", parents=[common])
    s.add_argument("run_id")
    s.add_argument("--seed", type=int, default=0)
    s.set_defaults(func=cmd_reseed)

    s = sub.add_parser("status", parents=[common])
    s.add_argument("run_id", nargs="?", default="")
    s.add_argument("--last", type=int, default=3)
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("stop", parents=[common])
    s.add_argument("run_id")
    s.set_defaults(func=cmd_stop)

    s = sub.add_parser("cores", parents=[common])
    s.add_argument("run_id")
    s.add_argument("total", type=int)
    s.add_argument("--per-ind", type=int, default=0)
    s.set_defaults(func=cmd_cores)

    s = sub.add_parser("selftest")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(func=cmd_selftest)

    args = p.parse_args(argv[1:])
    args.set = list(args.set) + list(getattr(args, "_set", None) or [])
    args.site = getattr(args, "_site", None) or args.site
    # No site file of its own: the shipped example holds the defaults.
    if Path(args.site) == DEFAULT_SITE and not DEFAULT_SITE.exists():
        args.site = str(EXAMPLE_SITE)
    return args


def main(argv: list[str]) -> int:
    args = parse(argv)
    try:
        return args.func(args)
    except (ConfigError, wsmod.WorkspaceError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
