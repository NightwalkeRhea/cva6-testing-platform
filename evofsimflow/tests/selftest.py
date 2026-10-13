#!/usr/bin/env python3
"""Exercise the whole machine with no EDA tool present.

A fake repository and a scripted executor stand in for the flow, so the parts
that can be got wrong quietly - the slot pool, the journal, the cache, the move
into processed/, the recovery of an interrupted run, the rewriting of the
campaign script - are all tested on a laptop. What this cannot check is the
content of the command lines, which only the real tools can confirm.
"""
from __future__ import annotations

import contextlib
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import textwrap
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from evokit import __version__ as TOOL_VERSION                    # noqa: E402
from evokit import genome as gen                                 # noqa: E402
from evokit import manifest as mf                                # noqa: E402
from evokit import workspace as wsmod                            # noqa: E402
from evokit.campaign import (BadRequest, Campaign, EvalServer, Runner, ask,  # noqa: E402
                             k_min, parse_request)
from evokit.config import Campaign as CampaignCfg                # noqa: E402
from evokit.config import RunLayout, Site, make_run_id, slug     # noqa: E402
from evokit.fitness import DIM, fitness, score                   # noqa: E402
from evokit.journal import Journal                               # noqa: E402
from evokit.model import Individual, Job, Outcome, Status        # noqa: E402
from evokit.pool import Pool                                     # noqa: E402
from evokit.reports import parse_summary                         # noqa: E402
from flowkit import verdict                                      # noqa: E402
from flowkit.fsimgen import Rewrite, rewrite                     # noqa: E402
from flowkit.shell import FakeExecutor                           # noqa: E402

FAILURES: list[str] = []
CHECKS = 0


def check(cond: bool, what: str) -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        FAILURES.append(what)


FSIM_TCL = """set_config -global_max_jobs 16
set_config -fsim_std_args "-fsim=limit+hyperactive+20"
set_config -fsim_mode concurrent
## DYNAMIC RUNTIME - Do not modify from this!
create_testcases -name {"custom_alu"} \\
    -exec /flow/fsim-syn-rundir/simv \\
    -args "+permissive +dramsim +dramsim_ini_dir=/flow/ini +max-cycles=400000 \
+dramsim +dramsim_ini_dir=/flow/ini +max-cycles=400000 +ntb_random_seed_automatic \
+loadmem=/flow/tests/custom_alu.riscv +verbose +vcs+initreg+0 -ucli -do /flow/fsim-syn-rundir/run.tcl \
+permissive-off /flow/tests/custom_alu.riscv"

report -format standard -campaign chiptop0 -report /flow/saf/b/pre_fsim_out.rpt -overwrite
report -campaign chiptop0 -print -summaryonly ;  # Print on STDOUT
fsim -verbose
set_config -fsim_mode serial
catch {fsim -selected_status {HA IA IF DE DF}}
report -campaign chiptop0 -summaryonly -report /flow/saf/b/fsim_out_summary.rpt -overwrite
report -format standard -campaign chiptop0 -report /flow/saf/b/fsim_out.rpt -overwrite
report -campaign chiptop0 -report /flow/saf/b/fsim_out_hier.rpt -overwrite -hierarchical 100
"""

SUMMARY = """#------------------------------------------------------------------------
# Fault Coverage Summary for Default List
#                                                  Total
#------------------------------------------------------------------------
# Number of Faults:                                18970 100.00%
#
# Testable Faults:                                 18970 100.00%  100.00%
#   Dropped Detected                         DD     {dd}  41.86%   41.86%
#   Dropped Potential                        PD        0   0.00%    0.00%
#   Not Detected                             ND    {nd}  56.36%   56.36%
#   Not Observed                             NO      132   0.70%    0.70%
#   Not Controlled                           NC      206   1.09%    1.09%
#
# Status Groups ---------------------------------------------------------
#   Detected                                 DG     {dd}  41.86%
#   Unselected                               NG      338   1.78%
#
# Coverage --------------------------------------------------------------
#   Test Coverage                                         {cov}%
#   Fault Coverage                                        {cov}%
#------------------------------------------------------------------------
"""


# ----------------------------------------------------------------- fake flow


def build_fake_repo(root: Path) -> Path:
    repo = root / "chipyard"
    build = repo / "vlsi/build-x/TOP-ChipTop"
    fsim = build / "fsim-syn-rundir"
    sim = build / "sim-syn-rundir"
    (fsim / "saf/custom_alu").mkdir(parents=True)
    (fsim / "simv.daidir").mkdir(parents=True)
    sim.mkdir(parents=True)
    (repo / "tests").mkdir(parents=True)

    (fsim / "simv").write_text("fault-aware simv")
    (fsim / "simv.daidir/lib.so").write_text("x")
    (fsim / "force_regs.ucli").write_text("force -deposit a 0\n")
    (fsim / "run.tcl").write_text(f"source {fsim / 'force_regs.ucli'}\nrun\nexit")
    (fsim / "saf_chiptop0.sff").write_text("FaultList {}\n")
    (fsim / "saf/custom_alu/fsim.tcl").write_text(FSIM_TCL)
    (fsim / "saf/custom_alu/strobe.sv").write_text("`define INJECT_CYCLE 12000\n")

    (sim / "simv").write_text("plain simv")
    (sim / "simv.daidir").mkdir()
    (sim / "simv.daidir/lib.so").write_text("y")
    # Output of earlier runs, which a snapshot of the probe must leave behind.
    (sim / "par-20260101_000000-1/000_hello.riscv").mkdir(parents=True)
    (sim / "run.tcl").write_text(f"source {sim / 'force_regs.ucli'}\nrun\nexit")
    (sim / "force_regs.ucli").write_text("force -deposit a 0\n")
    (sim / "sim-output-full.json").write_text(json.dumps({
        "sim.inputs.execution_flags_prepend": ["+permissive"],
        "sim.inputs.execution_flags": ["+dramsim", "+max-cycles=99", "+loadmem=/old",
                                       "+ntb_random_seed_automatic", "+verbose"],
        "sim.inputs.execution_flags_append": ["+permissive-off"],
    }))
    (repo / "tests/htif.ld").write_text("/* linker script */\n")
    return repo


def write_conf(root: Path, repo: Path, name: str, tool_root: Path) -> tuple[Path, Path]:
    site = root / "site.toml"
    site.write_text(textwrap.dedent(f"""
        [paths]
        repo  = "{repo}"
        build = "vlsi/build-x/TOP-ChipTop"
        runs  = "{root / 'evosbst-runs'}"
        [tools]
        ugp3 = "ugp3"
        vc_fcc = "vc_fcc"
        vc_fcm = "vc_fcm"
        cc = "riscv64-unknown-elf-gcc"
        objdump = "riscv64-unknown-elf-objdump"
        [cores]
        total = 8
        per_ind = 2
        [sim]
        seed = 7
        [infra]
        retries = 1
        stall_seconds = 0
    """))
    camp = root / f"camp_{name}.toml"
    camp.write_text(textwrap.dedent(f"""
        name = "{name}"
        dut = "{name}"
        workspace = "ws_{name}"
        workspace_mode = "snapshot"
        pipeline = ["build", "probe", "fsim"]
        [source]
        rundir = "fsim-syn-rundir"
        tcl = "saf/custom_alu/fsim.tcl"
        sff = "saf_chiptop0.sff"
        campaign = "chiptop0"
        [probe]
        rundir = "sim-syn-rundir"
        max_cycles = 400000
        [fsim]
        cycles_factor = 1.5
        cycles_floor = 1000
        [genome]
        file = "{tool_root / 'genome/rv64_alu.xml'}"
        boot = "newlib"
        [ga]
        mu = 4
        lambda = 4
        generations = 2
    """))
    return site, camp


def make_executor(fail_first: set[str] | None = None) -> FakeExecutor:
    """Stands in for gcc, objdump, the simulator and the two campaign tools."""
    failed: set[str] = set()
    fail_first = fail_first or set()

    def gcc(argv, cwd, log):
        """Assemble to objects then link, the same shape as the real build."""
        out = Path(argv[argv.index("-o") + 1])
        if out.suffix == ".o":
            src = Path(argv[-1])
            text = src.read_text() if src.exists() else ""
            if "BROKEN" in text:
                Path(log).write_text("Error: unknown opcode 'BROKEN'\n")
                return 1
            out.write_text(text)
            return 0
        blob = "".join(Path(a).read_text() for a in argv if a.endswith(".o"))
        marker = "HANG:" if "HANG" in blob else ""
        out.write_text(marker + "ELF:" + hashlib.sha256(blob.encode()).hexdigest())
        return 0

    def objdump(argv, cwd, log):
        Path(log).write_text("Disassembly of section .text:\n0: 00000013 nop\n")
        return 0

    def simv(argv, cwd, log):
        binary = next(a.split("=", 1)[1] for a in argv if a.startswith("+loadmem="))
        text = Path(binary).read_text()
        cycles = 12000 + 40 * (int(text[-4:], 16) % 300)
        if "HANG" in text:
            Path(log).write_text(
                "*** FAILED ***    (timeout) after 400000 simulation cycles\n"
            )
            return 0
        Path(log).write_text(
            f"*** PASSED *** Completed after {cycles} simulation cycles\n"
            "$finish called from file TestDriver.v line 158.\n"
        )
        return 0

    def fcc(argv, cwd, log):
        if "fcc" in fail_first and "fcc" not in failed:
            failed.add("fcc")
            Path(log).write_text("Error: Cannot find license file\n")
            return 1
        (cwd / "fdb").mkdir(exist_ok=True)
        return 0

    def fcm(argv, cwd, log):
        tcl = Path(argv[argv.index("-tcl_script") + 1]).read_text()
        reports = {}
        for line in tcl.splitlines():
            if line.strip().startswith("report") and "-report" in line:
                path = Path(line.split("-report")[1].split()[0])
                reports[path.name] = path
        dd = 7000 + (len(tcl) % 900)
        reports["fsim_out_summary.rpt"].write_text(
            SUMMARY.format(dd=f"{dd:5d}", nd=f"{18632 - dd:5d}", cov=f"{dd / 189.70:.2f}")
        )
        for name in ("fsim_out.rpt", "fsim_out_hier.rpt"):
            if name in reports:
                reports[name].write_text("# per-fault detail\n" + "NA 0 {PORT x}\n" * 200)
        # The real task tree is four levels below the session, and the header
        # of summary.csv does not line up with its own data rows.
        sess = cwd / "fcm.dir/session0001_2026-08-06-00:00:00_host_PID1"
        task = sess / "tasks/0xxxx/0xx/tsim:custom_alu:task1"
        task.mkdir(parents=True, exist_ok=True)
        (task / "out.log").write_text(
            "*** PASSED *** Completed after                20000 simulation cycles\n"
            "$finish called from file TestDriver.v line 158.\nExit:0\n"
        )
        (sess / "fcm_debug.log").write_text("all good\n")
        rows = [",1,1,host,yes,tsim,0,0,t,0,t,58,57,764,1041,0,0,0,custom_alu,1,"]
        rows += [
            f",{i + 2},1,host,yes,fsim,0,0,t,0,t,{100 + i},{99 + i},741,966,0,0,0,"
            "custom_alu,2046,2046, ND:1000"
            for i in range(10)
        ]
        (sess / "tasks/summary.csv").write_text(
            ", task, worker, stage, valid data, hostname\n" + "\n".join(rows) + "\n"
        )
        (cwd / "fcm_tsim_fsdb").mkdir(exist_ok=True)
        # What the real tools drop in the working directory alongside the run.
        for junk in ("novas_dump.log", "ucli.key", "fdb_fdb.log", "vc_fdb_report.log"):
            (cwd / junk).write_text("noise\n" * 500)
        return 0

    return FakeExecutor({
        "riscv64-unknown-elf-gcc": gcc,
        "riscv64-unknown-elf-objdump": objdump,
        "simv": simv,
        "vc_fcc": fcc,
        "vc_fcm": fcm,
    })


# ---------------------------------------------------------------------- test


def make_individuals(where: Path, n: int, tag: str = "") -> list[str]:
    where.mkdir(parents=True, exist_ok=True)
    ids = []
    for i in range(n):
        uid = f"individual{tag}{i}"
        filler = "    nop\n" * i
        (where / uid).write_text(f"# BEGIN {tag}{i}\n    add t0, t1, t2\n" + filler)
        ids.append(uid)
    return ids


class Stack:
    """Everything a run needs, wired the same way evosbst.py wires it."""

    def __init__(self, site_p: Path, camp_p: Path, tool_root: Path, run_id: str,
                 executor, resume: bool = False):
        self.site = Site.load(site_p)
        self.camp = CampaignCfg.load(camp_p)
        self.layout = RunLayout(self.site.runs, run_id)
        self.layout.create()
        self.ws = wsmod.ensure(self.camp, self.site, self.layout.workspaces(),
                               self.camp.workspace_mode)
        self.camp.inputs_hash = mf.inputs_hash(mf.hash_parts(self.site, self.camp, self.ws))
        scaffold = gen.Scaffold(boot=self.camp.boot, cc=self.site.tools.cc,
                                sbst_dir=tool_root / "genome/sbst",
                                linker_script=self.site.repo / "tests/htif.ld")
        spec = gen.load(self.camp.genome_file)
        self.journal = Journal(self.layout.journal)
        self.lines: list[str] = []
        self.runner = Runner(self.site, self.camp, self.layout, self.journal, executor,
                             report=self.lines.append)
        self.runner.setup(self.ws, scaffold)
        self.campaign = Campaign(self.camp, self.ws, self.site, self.layout,
                                 self.runner, seed=1)
        self.campaign.prepare(tool_root, spec, resume=resume)

    def evaluate(self, generation: int, ids: list[str]) -> list[list[float]]:
        return self.campaign.evaluate(self.layout.ugp3, generation, ids)


def test_units() -> None:
    s = parse_summary(SUMMARY.format(dd=" 7940", nd="10692", cov="41.86"))
    check(s.total == 18970, "summary total")
    check(abs(s.coverage - 41.86) < 1e-6, "summary coverage")
    check(s.classes["DD"] == 7940 and s.classes["ND"] == 10692, "summary classes")
    check(s.consistent() == "", f"summary consistency: {s.consistent()}")
    check("DG" not in [k for k in s.classes if k not in ("DG", "NG")], "groups separated")

    v = verdict.parse("*** PASSED *** Completed after 100436 simulation cycles", 0)
    check(v.kind == "PASS" and v.cycles(1.0) == 100436, "sim verdict pass")
    v = verdict.parse("*** FAILED ***  (timeout) after 400000 simulation cycles", 0)
    check(v.kind == "FAIL" and v.timed_out, "sim verdict timeout")
    check(verdict.parse("", 0).kind == "ERROR", "exit 0 with no verdict is not a pass")

    out = rewrite(FSIM_TCL, Rewrite(
        simv=Path("/ws/simv"), binary=Path("/w/ind.riscv"), run_tcl=Path("/ws/run.tcl"),
        report_dir=Path("/w"), max_cycles=31337, seed=7, jobs=2, coats=True,
        keep_reports={"fsim_out_summary.rpt", "fsim_out.rpt", "fsim_out_hier.rpt"}))
    check("-global_max_jobs 2" in out, "tcl: worker count")
    check("-exec /ws/simv" in out, "tcl: simulator")
    check(out.count("+loadmem=/w/ind.riscv") == 1, "tcl: one loadmem")
    check("+max-cycles=31337" in out and out.count("+max-cycles=") == 1, "tcl: one cycle cap")
    check("+ntb_random_seed=7" in out and "automatic" not in out, "tcl: fixed seed")
    check("custom_alu.riscv" not in out, "tcl: old binary gone everywhere")
    check("-do /ws/run.tcl" in out, "tcl: ucli script")
    check("pre_fsim_out.rpt" not in out, "tcl: pre-simulation report dropped")
    check("-report /w/fsim_out_summary.rpt" in out, "tcl: summary redirected")
    check("-report /w/fsim_out.rpt" in out and "-report /w/fsim_out_hier.rpt" in out,
          "tcl: the big reports are kept and redirected too")
    nc = rewrite(FSIM_TCL, Rewrite(Path("/s"), Path("/b"), Path("/r"), Path("/w"),
                                   1, 1, 1, coats=False))
    check("fsim -verbose -no_coats" in nc, "tcl: coats switch")

    # The fault list forces this many tasks whatever is asked for: below it,
    # extra workers are free; above it, each one adds a whole task.
    check(k_min(18970) == 10, "k_min of the measured ALU fault list is 10")
    check(k_min(2046) == 1 and k_min(2047) == 2, "k_min rounds up at the task size")
    check(k_min(0) == 0, "k_min is unknown until a campaign has run")

    check(slug("alu/div") == "alu-div", "a dut path becomes a directory name")
    check(make_run_id("alu", Path("/nonexistent")).endswith("_alu"), "run id is date_dut")

    # A unix socket address is 108 bytes; a deep run directory passes it.
    near = RunLayout(Path("/short"), "run")
    deep = RunLayout(Path("/" + "d" * 40 + "/" + "e" * 40 + "/" + "f" * 40), "run")
    check(near.socket.parent == near.root / ".sock", "the socket lives in the run, in .sock/")
    check(near.socket_fits() and not deep.socket_fits(),
          "a socket path over the address size is caught up front")

    # The price of a cycle, checked on the two real cases that set it. uGP3
    # reads the first component that differs, so a length term of its own would
    # never be reached and the price has to live inside the score.
    price = 1e-5
    long_ = Outcome(status=Status.OK, coverage=41.71, cycles=35575)   # 610 insns
    short = Outcome(status=Status.OK, coverage=41.62, cycles=24295)   # 188 insns
    check(fitness(short, price) > fitness(long_, price),
          "0.09 coverage points do not pay for 11300 cycles")
    check(fitness(long_, 0.0) > fitness(short, 0.0),
          "with the price at zero the long one wins again, as it did on the server")
    # Coverage is in points, so the score is too. A hundredfold anywhere in
    # here divides the price by a hundred without changing any ordering by
    # coverage, which is why it survived a whole run before being noticed.
    check(41.0 < fitness(short, price)[1] < 42.0, "the score is in coverage points")
    rich = Outcome(status=Status.OK, coverage=41.71, cycles=30405)
    lean = Outcome(status=Status.OK, coverage=41.64, cycles=25235)
    check(fitness(rich, price) > fitness(lean, price),
          "a coverage gain that is worth its cycles still wins")
    check(len(fitness(lean, price)) == DIM, "the fitness vector is as wide as declared")
    far = Outcome(status=Status.MALFORMED, cycles=9000)
    near = Outcome(status=Status.MALFORMED, cycles=3000)
    check(fitness(far, price) < fitness(lean, price), "malformed sorts below valid")
    check(fitness(far, price) > fitness(near, price),
          "and the malformed ones are graded by how far they got")

    # The shipped campaign, and the one coupling that can break in silence:
    # uGP3 reads as many numbers per individual as the population file declares.
    shipped = CampaignCfg.load(HERE.parent / "conf/campaigns/alu.toml")
    check(shipped.cycle_price == 1e-5, "the shipped campaign prices a cycle")
    pop = gen.render_population(shipped, Path("/x/constraints.xml"), "/x/evalclient.py")
    check(re.search(rf'<fitnessParameters value="{DIM}"\s*/>', pop) is not None,
          "uGP3 is told the same width the evaluator sends")

    spec = gen.GenomeSpec.load(HERE.parent / "genome/rv64_alu.toml")
    xml = gen.render_constraints(spec)
    check("<constraints" in xml and "sbst_body" in xml, "genome renders")
    check(xml.count("<macro id=") == len(spec.classes) + 1, "genome macro count")
    check(f"add {spec.acc_reg}, {spec.acc_reg}" in xml, "genome folds into the accumulator")
    check(spec.acc_reg not in spec.free_regs, "accumulator is reserved")


def test_preflight(root: Path, tool_root: Path) -> None:
    """The first command someone new runs. It has to name every missing piece,
    name it where the flow puts it, and say so before anything is created."""
    from evokit import preflight as pre

    repo = build_fake_repo(root)
    site_p, camp_p = write_conf(root, repo, "alu", tool_root)
    site, camp = Site.load(site_p), CampaignCfg.load(camp_p)
    ex = make_executor()
    site.tools.ugp3 = str(repo / "ugp3")        # absolute, as on the real machine
    (repo / "ugp3").write_text("#!/bin/sh\n")

    items = pre.inspect(site, camp, ex)
    check(not pre.missing(items), f"a complete flow passes: {[i.what for i in pre.missing(items)]}")
    check(any(i.what == "strobe" and not i.required for i in items),
          "the strobe is optional, not a blocker")

    # The one that used to be found only during a run, as an infrastructure
    # failure, and therefore retried for half an hour before anyone was told.
    (repo / "vlsi/build-x/TOP-ChipTop/sim-syn-rundir/simv").unlink()
    (repo / "vlsi/build-x/TOP-ChipTop/fsim-syn-rundir/force_regs.ucli").unlink()
    gaps = pre.missing(pre.inspect(site, camp, ex))
    check({i.what for i in gaps} == {"fault-free simulator", "register forcing"},
          f"both gaps found in one pass, and only those: {[i.what for i in gaps]}")
    check(all("_ws" not in i.where for i in gaps),
          "and named where the flow puts them, not in the workspace copied from it")
    check("rundirs" in pre.advice(gaps) and "PATH" not in pre.advice(gaps),
          "the advice is about the flow when the flow is what is missing")
    tool_gap = [pre.Item("tool", "ugp3", "ugp3 (on PATH)", False)]
    check("PATH" in pre.advice(tool_gap) and "rundirs" not in pre.advice(tool_gap),
          "and about the shell when a tool is")

    text = pre.report(pre.inspect(site, camp, ex), site)
    check("<build>/sim-syn-rundir/simv" in text, "the report factors out the long roots")
    check(str(site.build) in text.splitlines()[0], "and says once what they stand for")

    # Left out of the configuration = looked for. The point of this is that a
    # second machine should need the checkout and nothing else.
    from evokit import sources as srcs

    camp.src_tcl = camp.src_sff = ""
    found = srcs.locate(site.build, camp)
    check(found.tcl and found.tcl.name == "fsim.tcl", "the campaign script is found")
    check(found.sff and found.sff.name == "saf_chiptop0.sff", "so is the fault list")
    check(found.found.get("fault list") == "*.sff", "and it says how it found it")

    # Two of anything is a question, not a coin toss: it is recorded in the
    # inputs-hash, so guessing would silently decide what is being measured.
    (found.sff.parent / "saf_other.sff").write_text("x")
    again = srcs.locate(site.build, camp)
    check(again.sff is None and "2 matches" in again.found["fault list"],
          "two fault lists are refused, and counted")

    from flowkit import paths as flowpaths

    build = site.build
    check(flowpaths.find_build(repo, "vlsi/build-*/*-ChipTop") == build,
          "the build directory is found from the checkout alone")
    (build.parent.parent / "build-other" / "X-ChipTop").mkdir(parents=True)
    try:
        flowpaths.find_build(repo, "vlsi/build-*/*-ChipTop")
        check(False, "two build directories are refused")
    except flowpaths.NotFound as exc:
        check("build-other" in str(exc) and "paths.build" in str(exc),
              "two build directories are refused, listed, and the fix is named")


def test_cores(root: Path, tool_root: Path) -> None:
    """How the budget is cut, and that the pool never exceeds it."""
    from evokit.config import Cores

    check(Cores(total=16, per_ind=1).in_flight == 16, "one core each: sixteen in flight")
    check(Cores(total=16, per_ind=2).in_flight == 8, "two cores each: eight in flight")
    check(Cores(total=16, per_ind=16).in_flight == 1, "the whole budget: one at a time")
    check(Cores(total=3, per_ind=2).in_flight == 1, "a slot is never a fraction")

    peak = {"n": 0}
    live = {"n": 0}
    lock = threading.Lock()
    order: list[str] = []
    finished: list[str] = []

    def work(job: Job) -> Status:
        with lock:
            live["n"] += 1
            peak["n"] = max(peak["n"], live["n"])
            order.append(job.ind.uid)
        time.sleep(0.02)
        with lock:
            live["n"] -= 1
        return Status.INFRA if job.ind.uid == "bad" else Status.OK

    jobs = []
    for i, uid in enumerate(["a", "b", "c", "d", "e", "bad"]):
        ind = Individual(campaign="c", generation=0, uid=uid, source=root / uid)
        jobs.append(Job(ind=ind, workdir=root / uid, cost_hint=float(i)))

    pool = Pool(slots=2, cores_each=3, work=work, on_done=lambda j: finished.append(j.ind.uid),
                retries=1, backoff=0.01, stall_seconds=0)
    pool.run(jobs)

    check(peak["n"] <= 2, f"the pool never runs more than its slots ({peak['n']})")
    check(peak["n"] == 2, "the pool actually fills its slots")
    check(order[:2] == ["a", "b"], "jobs are taken in the order given")
    check(sorted(finished) == sorted(j.ind.uid for j in jobs), "every job finished once")
    check(len(finished) == len(jobs), "no job finished twice")
    check(order.count("bad") == 2, "an infrastructure failure is retried, then given up")
    check(all(j.cores == 3 for j in jobs), "the pool stamps the cores each job was given")


def test_resize(root: Path, tool_root: Path) -> None:
    """The budget changed under a running pool: used at once, never exceeded,
    nothing lost; and the request file a run takes it from."""
    lock = threading.Lock()
    live = {"n": 0}
    seen: list[int] = []        # how many were running when each job started
    after = {"on": False}

    def work(job: Job) -> Status:
        with lock:
            live["n"] += 1
            if after["on"]:
                seen.append(live["n"])
        time.sleep(0.05)
        with lock:
            live["n"] -= 1
        return Status.OK

    def batch(n: int, tag: str) -> list[Job]:
        return [Job(ind=Individual(campaign="c", generation=0, uid=f"{tag}{i}",
                                   source=root / f"{tag}{i}"), workdir=root / f"{tag}{i}")
                for i in range(n)]

    for start, to, tag in ((1, 3, "grow"), (4, 1, "shrink")):
        seen.clear()
        after["on"] = False
        finished: list[str] = []
        jobs = batch(12, tag)
        pool = Pool(slots=start, cores_each=1, work=work,
                    on_done=lambda j: finished.append(j.ind.uid))
        t = threading.Thread(target=pool.run, args=(jobs,), daemon=True)
        t.start()
        # Every slot busy first: a job taken before the change is not after it.
        deadline = time.monotonic() + 5
        while live["n"] < start and time.monotonic() < deadline:
            time.sleep(0.002)
        pool.resize(to)
        after["on"] = True
        t.join(20)
        check(not t.is_alive(), f"{tag}: the pool still ends")
        check(bool(seen) and max(seen) == to,
              f"{tag}: jobs started after the change run {to} at a time ({max(seen or [0])})")
        check(sorted(finished) == sorted(j.ind.uid for j in jobs) and len(finished) == len(jobs),
              f"{tag}: every job finished once, none killed")

    repo = build_fake_repo(root)
    site_p, camp_p = write_conf(root, repo, "alu", tool_root)
    st = Stack(site_p, camp_p, tool_root, "20260806_alu", make_executor())
    sys.argv = ["evosbst.py"]
    import evosbst as cli

    run = cli.Run(st.site, st.camp, st.layout, seed=1, resume=False, use_cache=True)
    per_ind = st.site.cores.per_ind
    req = st.layout.cores_request
    with contextlib.redirect_stdout(io.StringIO()):
        req.write_text(json.dumps({"total": 4 * per_ind}))
        run._take_cores_request()
        check(st.site.cores.total == 4 * per_ind and st.site.cores.per_ind == per_ind,
              "a request changes the total and keeps per_ind")
        check(not req.exists(), "a request is taken once")
        req.write_text(json.dumps({"total": 1, "per_ind": 2}))
        run._take_cores_request()
        check(st.site.cores.total == 4 * per_ind, "a total below per_ind is refused")
        req.write_text("not json")
        run._take_cores_request()
        check(st.site.cores.total == 4 * per_ind and not req.exists(),
              "a garbled request is dropped and the budget kept")
    run.log.close()


def test_pipeline(root: Path, tool_root: Path) -> None:
    repo = build_fake_repo(root)
    site_p, camp_p = write_conf(root, repo, "alu", tool_root)
    ex = make_executor()
    st = Stack(site_p, camp_p, tool_root, "20260806_alu", ex)
    layout = st.layout

    ws_rundir = layout.workspaces() / "ws_alu" / "rundir"
    check(ws_rundir.exists(), "snapshot workspace created")
    check((layout.input / "constraints.xml").read_text()
          == (tool_root / "genome/rv64_alu.xml").read_text(),
          "a hand-written genome reaches uGP3 exactly as written")
    check((ws_rundir / "simv").stat().st_ino
          == (repo / "vlsi/build-x/TOP-ChipTop/fsim-syn-rundir/simv").stat().st_ino,
          "snapshot shares inodes, so it costs no disk")
    orig_tcl = (repo / "vlsi/build-x/TOP-ChipTop/fsim-syn-rundir/run.tcl").read_text()
    check(str(ws_rundir) in (ws_rundir / "run.tcl").read_text(),
          "snapshot run.tcl points at itself")
    check("build-x" in orig_tcl and str(ws_rundir) not in orig_tcl,
          "rewriting the snapshot did not reach through into the original")

    ids = make_individuals(layout.ugp3, 6)
    (layout.ugp3 / ids[4]).write_text("# BROKEN\n  BROKEN t0\n")
    (layout.ugp3 / ids[5]).write_text("# HANG\n  nop\n")

    t0 = time.monotonic()
    values = st.evaluate(0, ids)
    elapsed = time.monotonic() - t0
    check(len(values) == 6, "one fitness per individual")
    check(all(len(v) == DIM for v in values), "fitness has the declared width")
    check(values[0][0] == 1.0, "a good individual is valid")
    check(values[4][0] == 0.0, "an individual that does not assemble is invalid")
    check(values[5][0] == 0.0, "an individual that never finishes is invalid")
    check(values[4] < values[0], "invalid sorts below valid")
    check(elapsed < 30, f"generation did not deadlock ({elapsed:.1f}s)")

    rows = st.journal.read()
    check(len(rows) == 6, f"journal has one line per individual, got {len(rows)}")

    # The one number that must agree on both paths: what uGP3 was handed, and
    # what best/ and status re-derive from the journal afterwards. They are
    # computed from different objects in different units, so nothing but a test
    # keeps them together.
    by_uid = {r["uid"]: r for r in rows}
    check(all(abs(values[i][1] - score(float(by_uid[uid]["coverage"]),
                                       int(by_uid[uid]["cycles"]), st.camp.cycle_price)) < 1e-6
              for i, uid in enumerate(ids) if by_uid[uid]["status"] == "ok"),
          "the score uGP3 was given is the one the journal re-derives")
    scored = [r for r in rows if r["status"] == "ok"]
    check(len(scored) == 4, f"four scored, got {len(scored)}")
    check(all(float(r["coverage"]) > 0 for r in scored), "coverage recorded")
    check(all(r["gold"] == "PASS" for r in scored), "gold verdict found in the session tree")
    check(all(int(r["tasks"]) == 10 for r in scored), "task count read from the summary")
    check(st.runner.faults == 18970, "fault-list size learnt from the campaign")
    check(st.runner.stats()["k_min"] == 10, "k_min derived from it")
    check(len(st.lines) == 6, "one progress line per individual")

    check(not list(p for p in layout.individuals.glob("*")
                   if p.is_dir() and p != layout.processed),
          "nothing left directly under individuals/ once the generation is done")
    names = sorted(p.name for p in layout.processed.glob("*") if p.is_dir())
    check(names == [f"g000_i{i:03d}_individual{i}" for i in range(6)],
          f"processed/ sorts by generation then arrival: {names[:2]}")
    wd = layout.processed / "g000_i000_individual0"
    check(wd.is_dir(), "a finished individual moved into processed/")
    check((wd / "ind.riscv").exists(), "the binary is kept")
    check((wd / "ind.dump.gz").exists(), "the disassembly is kept, compressed")
    check(not any((wd / j).exists() for j in
                  ("novas_dump.log", "ucli.key", "fdb_fdb.log", "vc_fdb_report.log")),
          "the debris the tools leave in the workdir is dropped")
    check((wd / "outcome.json").exists(), "the outcome is kept next to it")
    check((wd / "fsim_out_summary.rpt").exists(), "summary kept uncompressed")
    check((wd / "fsim_out.rpt.gz").exists(), "the per-fault report is kept, compressed")
    check(not (wd / "fsim_out.rpt").exists(), "and the uncompressed copy is gone")
    check((wd / "fsim_out_hier.rpt.gz").exists(), "the hierarchical report too")
    with gzip.open(wd / "fsim_out.rpt.gz", "rt") as fh:
        check("per-fault detail" in fh.read(), "the compressed report is readable")
    check(not (wd / "fdb").exists(), "campaign database pruned when nothing was odd")
    check(not (wd / "fcm.dir").exists(), "session pruned when nothing was odd")
    check((layout.processed / "g000_i004_individual4" / "ind.S").exists(),
          "an individual that does not assemble is archived too")
    lengths = {int(r["insns"]) for r in rows if r["insns"]}
    check(len(lengths) > 1, f"the length of each individual is recorded: {sorted(lengths)}")

    # Same individuals again: everything must come from the cache.
    calls_before = len(ex.calls)
    values2 = st.evaluate(1, ids)
    after = [Path(c[0]).name for c in ex.calls[calls_before:]]
    check(values2[0] == values[0], "cache returns the same number")
    check("vc_fcm" not in after, "cache avoided the campaign")
    check("simv" not in after, "cache avoided the probe too, not only the campaign")
    check(st.runner.cache.hits >= 4, "cache hits counted")
    check(st.runner.cache.hits + st.runner.cache.misses == 10,
          "the cache is asked once per individual, not once per stage")
    check((layout.processed / "g001_i000_individual0").is_dir(),
          "the next generation lands beside the first, not on top of it")

    # uGP3 does not always ask for a generation in one call: it split
    # generation 3 of the first real run in two. The second half must continue
    # the numbering, not restart it on top of the first.
    more = make_individuals(layout.ugp3, 2, tag="late")
    st.evaluate(1, more)
    check((layout.processed / "g001_i006_individuallate0").is_dir(),
          "a second call for the same generation continues the numbering")
    check(st.runner.expected == 8, "and the progress counter counts the whole generation")


def test_run_output(root: Path, tool_root: Path) -> None:
    """best/ must hold the winner, found without reading the journal."""
    repo = build_fake_repo(root)
    site_p, camp_p = write_conf(root, repo, "alu", tool_root)
    ex = make_executor()
    st = Stack(site_p, camp_p, tool_root, "20260806_alu", ex)
    ids = make_individuals(st.layout.ugp3, 4)
    st.evaluate(0, ids)

    sys.argv = ["evosbst.py"]
    import evosbst as cli

    run = cli.Run(st.site, st.camp, st.layout, seed=1, resume=False, use_cache=True)
    run.journal = st.journal
    run.collect_best()
    run.log.close()

    best = st.layout.best
    check(best.is_dir(), "best/ created")
    check((best / "best.json").exists(), "best/ records which individual it was")
    picked = json.loads((best / "best.json").read_text())
    scored = [r for r in st.journal.read() if r["status"] == "ok"]
    top = max(run._score(r) for r in scored)
    check(abs(picked["score"] - top) < 1e-6,
          "best/ holds the top score, the same order the search used")
    check(picked["cycle_price"] == st.camp.cycle_price,
          "best/ says at what price that ranking was made")
    check((best / "ind.riscv").exists(), "best/ carries the binary")
    check((best / "fsim_out_summary.rpt").exists(), "best/ carries its report")

    man = mf.build_manifest(st.site, st.camp, st.ws, seed=1, run_id="20260806_alu")
    check(man["tool_version"] == TOOL_VERSION, "the run records which evosbst produced it")
    check(man["campaign"]["cycle_price"] == st.camp.cycle_price,
          "and at what price it was scoring cycles")


def test_recovery(root: Path, tool_root: Path) -> None:
    """Anything still under individuals/ was interrupted: it cannot be resumed."""
    repo = build_fake_repo(root)
    site_p, camp_p = write_conf(root, repo, "alu", tool_root)
    ex = make_executor()
    st = Stack(site_p, camp_p, tool_root, "20260806_alu", ex)
    ids = make_individuals(st.layout.ugp3, 2)
    st.evaluate(0, ids)

    orphan = st.layout.individuals / "g000_i009_individualZ"
    (orphan / "fdb").mkdir(parents=True)
    kept = st.layout.processed / "g000_i000_individual0"

    sys.argv = ["evosbst.py"]
    import evosbst as cli

    run = cli.Run(st.site, st.camp, st.layout, seed=1, resume=True, use_cache=True)
    run._gc_orphans()
    check(not orphan.exists(), "an interrupted workdir is removed on resume")
    check(kept.exists(), "a finished one is kept")
    check(st.layout.processed.exists(), "processed/ itself is never removed")
    run.log.close()


def test_infra_retry(root: Path, tool_root: Path) -> None:
    """A tool failure is not the individual's fault: retry, never score it."""
    repo = build_fake_repo(root)
    site_p, camp_p = write_conf(root, repo, "alu", tool_root)
    ex = make_executor(fail_first={"fcc"})
    st = Stack(site_p, camp_p, tool_root, "20260806_alu", ex)
    ids = make_individuals(st.layout.ugp3, 2)
    values = st.evaluate(0, ids)
    check(all(v[0] == 1.0 for v in values), "the retried individual still got a real number")
    check(len([c for c in ex.calls if "vc_fcc" in c[0]]) == 3,
          "the failed campaign was retried")


def test_cli(root: Path, tool_root: Path) -> None:
    """Options must work where they get typed: after the subcommand."""
    sys.argv = ["evosbst.py"]
    import evosbst as cli

    for argv in (["evosbst.py", "status", "--set", "a.b=1", "--site", "/s.toml"],
                 ["evosbst.py", "--set", "a.b=1", "--site", "/s.toml", "status"]):
        parsed = cli.parse(argv)
        check(parsed.set == ["a.b=1"], f"--set accepted: {' '.join(argv[1:])}")
        check(parsed.site == "/s.toml", f"--site accepted: {' '.join(argv[1:])}")
    parsed = cli.parse(["evosbst.py", "status"])
    default = cli.DEFAULT_SITE if cli.DEFAULT_SITE.exists() else cli.EXAMPLE_SITE
    check(parsed.set == [] and parsed.site == str(default), "defaults survive")
    parsed = cli.parse(["evosbst.py", "--set", "a=1", "run", "c.toml", "--set", "b=2"])
    check(parsed.set == ["a=1", "b=2"], "overrides from both positions are merged")

    # 'check' reads the genome too: a broken one is reported before any run.
    repo = build_fake_repo(root)
    site_p, camp_p = write_conf(root, repo, "alu", tool_root)
    bad = root / "bad.xml"
    bad.write_text((tool_root / "genome/rv64_alu.xml").read_text()
                   .replace('maximum="2047"', 'maximum="0x7ff"'))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = cli.main(["evosbst.py", "check", str(camp_p), "--site", str(site_p),
                       "--set", f"genome.file={bad}"])
    check(rc == 1 and "maximum=\"0x7ff\"" in out.getvalue(), "check stops on a broken genome")


def test_socket(root: Path, tool_root: Path) -> None:
    """The client uGP3 launches must reach the server and get an answer."""
    repo = build_fake_repo(root)
    site_p, camp_p = write_conf(root, repo, "alu", tool_root)
    ex = make_executor()
    st = Stack(site_p, camp_p, tool_root, "20260806_alu", ex)
    server = EvalServer(st.layout.socket, {"alu": st.campaign})
    server.start()
    ids = make_individuals(st.layout.ugp3, 2)
    reply = ask(st.layout.socket, "alu", st.layout.ugp3, 0, ids)
    check(reply.get("ok") is True, f"socket round trip: {reply}")
    check(len(reply.get("fitness", [])) == 2, "socket returned both values")
    bad = ask(st.layout.socket, "nope", st.layout.ugp3, 0, ids)
    check(bad.get("ok") is False, "unknown campaign is refused, not guessed")
    check(stat.S_IMODE(st.layout.socket_dir.stat().st_mode) == 0o700, "socket directory is 0700")
    check(stat.S_IMODE(st.layout.socket.stat().st_mode) == 0o600, "socket is 0600")
    check(st.layout.socket.is_relative_to(st.layout.root), "the socket stays inside the run")
    for what, cwd, gen_, req_ids in (
        ("a cwd other than uGP3's", st.layout.root, 0, ids),
        ("an id that climbs out", st.layout.ugp3, 0, ["../manifest.json"]),
        ("an absolute id", st.layout.ugp3, 0, [str(st.layout.ugp3 / ids[0])]),
        ("a negative generation", st.layout.ugp3, -1, ids),
        ("no ids at all", st.layout.ugp3, 0, []),
    ):
        reply = ask(st.layout.socket, "alu", cwd, gen_, req_ids)
        check(reply.get("ok") is False and "BadRequest" in reply.get("error", ""),
              f"socket refuses {what}: {reply}")
    camps = {"alu": st.campaign}
    for what, line in (("an unterminated request", b'{"campaign": "alu"}'),
                       ("a request that is not an object", b"[1, 2]\n"),
                       ("a generation that is not an integer",
                        json.dumps({"campaign": "alu", "cwd": str(st.layout.ugp3),
                                    "generation": True, "ids": ids}).encode() + b"\n")):
        try:
            parse_request(line, camps)
            check(False, f"parse_request refuses {what}")
        except BadRequest:
            check(True, f"parse_request refuses {what}")
    server.stop()
    check(not st.layout.socket_dir.exists(), "the socket directory goes with the server")


def test_frozen_inputs(root: Path, tool_root: Path) -> None:
    """The probe runs on the workspace's copy, and the inputs-hash sees it."""
    repo = build_fake_repo(root)
    site_p, camp_p = write_conf(root, repo, "alu", tool_root)
    camp = CampaignCfg.load(camp_p)
    check(camp.workspace_mode == "snapshot", "snapshot is the default workspace mode")
    site = Site.load(site_p)
    layout = RunLayout(site.runs, "20260806_alu")
    ws = wsmod.ensure(camp, site, layout.workspaces(), "snapshot")
    live = repo / "vlsi/build-x/TOP-ChipTop/sim-syn-rundir"
    check(ws.probe_rundir.is_relative_to(ws.root), "the probe simv lives in the workspace")
    check(ws.probe_simv.stat().st_ino == (live / "simv").stat().st_ino,
          "the probe snapshot shares inodes")
    check((ws.probe_rundir / "simv.daidir/lib.so").exists(), "the probe daidir comes along")
    check(not any(ws.probe_rundir.glob("par-*")), "other runs' output stays behind")
    check(str(ws.probe_rundir) in ws.probe_run_tcl.read_text(),
          "the probe ucli script points at its own copy")
    h0 = mf.inputs_hash(mf.hash_parts(site, camp, ws))

    # A rebuild writes new files; the snapshot must not notice.
    (live / "simv").unlink()
    (live / "simv").write_text("plain simv, rebuilt")
    check(ws.probe_simv.read_text() == "plain simv", "a rebuild does not reach the snapshot")
    ws2 = wsmod.ensure(camp, site, layout.workspaces(), "snapshot", force=True)
    h1 = mf.inputs_hash(mf.hash_parts(site, camp, ws2))
    check(h0 != h1, "a different probe simv is a different inputs-hash")

    camp.fsim_cycles_cap += 1
    check(mf.inputs_hash(mf.hash_parts(site, camp, ws2)) != h1, "the cycle cap is in the hash")
    camp.fsim_cycles_cap -= 1
    camp.pipeline = ["build", "fsim"]
    check(mf.inputs_hash(mf.hash_parts(site, camp, ws2)) != h1, "the pipeline is in the hash")
    camp.pipeline = ["build", "probe", "fsim"]
    camp.probe_max_cycles += 1
    check(mf.inputs_hash(mf.hash_parts(site, camp, ws2)) != h1, "the probe cap is in the hash")
    camp.probe_max_cycles -= 1
    camp.force_regs = not camp.force_regs
    check(mf.inputs_hash(mf.hash_parts(site, camp, ws2)) != h1, "force_regs is in the hash")


def test_force_regs_off(root: Path, tool_root: Path) -> None:
    """force_regs = false: neither the probe nor the campaign sources the script."""
    repo = build_fake_repo(root)
    site_p, camp_p = write_conf(root, repo, "alu", tool_root)
    camp_p.write_text(camp_p.read_text().replace(
        'pipeline = ["build", "probe", "fsim"]\n',
        'pipeline = ["build", "probe", "fsim"]\nforce_regs = false\n'))
    ex = make_executor()
    st = Stack(site_p, camp_p, tool_root, "20260806_alu", ex)
    check(st.camp.force_regs is False, "force_regs = false is read")
    st.evaluate(0, make_individuals(st.layout.ugp3, 1))
    probes = [c for c in ex.calls if Path(c[0]).name == "simv"]
    check(bool(probes) and all("-ucli" not in c for c in probes),
          "force_regs off: the probe runs without UCLI")
    tcls = list(st.layout.processed.glob("*/fsim.tcl"))
    check(bool(tcls) and all("-ucli" not in p.read_text() for p in tcls),
          "force_regs off: the campaign runs without UCLI")


def test_bookkeeping(root: Path, tool_root: Path) -> None:
    """Journal before cache, and the pool's events reach the run directory."""
    repo = build_fake_repo(root)
    site_p, camp_p = write_conf(root, repo, "alu", tool_root)
    st = Stack(site_p, camp_p, tool_root, "20260806_alu", make_executor())
    order: list[str] = []
    record, put = st.runner.journal.record, st.runner.cache.put
    st.runner.journal.record = lambda *a: (order.append("journal"), record(*a))[1]
    st.runner.cache.put = lambda *a: (order.append("cache"), put(*a))[1]
    st.evaluate(0, make_individuals(st.layout.ugp3, 1))
    check(order == ["journal", "cache"], f"the journal is written before the cache ({order})")

    st.runner.event("give-up", {"job": "g000-i000", "detail": "licence"})
    st.runner.event("done", {"job": "g000-i000"})
    lines = st.layout.events.read_text().splitlines()
    check(len(lines) == 1 and json.loads(lines[0])["event"] == "give-up",
          "events are logged, 'done' is left to the journal")
    check(any("give-up" in ln for ln in st.lines), "a give-up is reported on the console")


def test_genome_output(root: Path, tool_root: Path) -> None:
    """What reaches uGP3 and the assembler."""
    repo = build_fake_repo(root)
    _site_p, camp_p = write_conf(root, repo, "alu", tool_root)
    camp = CampaignCfg.load(camp_p)
    pop = gen.render_population(camp, root / "constraints.xml", "python3 evalclient.py")
    check('ref="scanMutation' not in pop, "no scan mutation among the operators")

    # The GA: the hand-written population settings, the campaign's keys on top.
    check(camp.ga_file is not None and camp.ga.lam == 4 and camp.ga.nu == 60,
          "population settings come from the file, with the campaign's keys on top")
    check('<lambda value="4"' in pop and '<concurrentEvaluations value="4"' in pop
          and 'value="python3 evalclient.py"' in pop and str(root / "constraints.xml") in pop,
          "the run's copy says what ran, and how uGP3 reaches evosbst")
    check("Filled in by evosbst" in pop, "and keeps the file's comments")
    plain = CampaignCfg.load(camp_p, ['ga.file=""'])
    plain_pop = gen.render_population(plain, root / "constraints.xml", "python3 evalclient.py")
    check(plain.ga_file is None and '<lambda value="4"/>' in plain_pop
          and "Filled in by evosbst" not in plain_pop, "file = \"\" renders [ga] alone")
    shipped_ga = gen.population_values(tool_root / "conf/population.settings.xml")
    check(shipped_ga["lam"] == 48 and shipped_ga["tau"] == 2 and shipped_ga["max_hours"] == 168,
          "the shipped population settings are read by name")
    boot = (tool_root / "genome/sbst/boot_bare.S").read_text()
    head = boot[: boot.index("la      gp")]
    check(head.rfind(".option norelax") > head.rfind(".option pop"),
          "gp is loaded with relaxation off")

    # The genome as a hand-written constraints file: the default, and checked
    # for what uGP3 would accept in silence, each named by its line.
    shipped = (tool_root / "genome/rv64_alu.xml").read_text()
    xml_genome = gen.load(tool_root / "genome/rv64_alu.xml")
    check(xml_genome.macros == 6 and xml_genome.widest == (16, 384),
          "the shipped constraints file loads")
    check(isinstance(gen.load(tool_root / "genome/rv64_alu.toml"), gen.GenomeSpec),
          "a .toml genome is still rendered")

    def lint(text: str) -> str:
        try:
            gen.check_constraints(text, "g.xml")
        except ValueError as exc:
            return str(exc)
        return ""

    imm = 'minimum="-2048" maximum="2047"'
    line = shipped[: shipped.index(imm)].count("\n") + 1
    check(f"line {line}: maximum=\"0x7ff\"" in lint(shipped.replace(imm, 'minimum="-2048" maximum="0x7ff"')),
          "a hexadecimal range is stopped, with its line")
    check("would hang" in lint(shipped.replace(imm, 'minimum="2047" maximum="-2048"')),
          "a minimum above its maximum is stopped")
    check("does not end with a newline" in lint(shipped.replace("    .size sbst_body, .-sbst_body\n</expression>",
                                                                "    .size sbst_body, .-sbst_body</expression>")),
          "an expression without its newline is stopped")
    check("averageOccurs" in lint(shipped.replace('averageOccurs="128"', 'averageOccurs="500"')),
          "a macro count outside its range is stopped")
    check("not well-formed" in lint(shipped.replace("</constraints>", "")), "broken XML is stopped")
    check("not <constraints>" in lint("<parameters/>"), "a file that is not a genome is stopped")
    check(lint(shipped) == "", "the shipped genome passes")

    from evokit.stages.build import _count_insns, _fp_insn
    body = root / "ind.S"
    body.write_text("# x\n  addi t0, t0, 1\n  fence\n  .word 0\n")
    check(_fp_insn(body) == "" and _count_insns(body) == 2, "integer code and fences pass")
    body.write_text("# x\n  addi t0, t0, 1\n  fadd.d f0, f1, f2\n")
    check(_fp_insn(body) == "fadd.d", "a floating-point instruction is caught")


# ------------------------------------------------------------------ flow.py

FLOW_TEMPLATE = """set_config -global_max_jobs 16
fsim -verbose
set_config -fsim_mode serial
catch {fsim -selected_status {HA IA IF DE DF}}
"""


def test_flow_units(root: Path) -> None:
    from flowkit import config as fconf
    from flowkit import fsimgen, hier, make, simv
    from flowkit.paths import Paths
    from flowkit.shell import safe_rmtree
    from flowkit.ui import FlowError

    def refused(fn, *a) -> bool:
        try:
            fn(*a)
        except (FlowError, ValueError):
            return True
        return False

    # Verdicts, with the cycle count read from the end time.
    v = verdict.parse("$finish at simulation time 456789000\nTime: 45678900 ps\n", 0)
    check(v.line("t.riscv", 1.0) == "PASS  t.riscv  [45678 cycles]", "flow: pass line")
    check(verdict.parse("", 0).line("t", 1.0).endswith("(exit=0 but no verdict: it never simulated)"),
          "flow: exit 0 without a verdict is an error")
    check(verdict.parse("", 139).line("t", 1.0) == "ERROR t  (simv exit=139)", "flow: crash line")
    t = verdict.parse("*** FAILED *** (timeout) after 9 simulation cycles\n$finish\nTime: 10 ns\n", 0)
    check(t.kind == "FAIL" and t.timed_out and t.end_text == "10.0", "flow: a timeout still ends")
    check(verdict.parse("Time: 5 ns\n", 0).end_text == "", "flow: no $finish, no end time")

    # The campaign script: serial pass without coats, split block before fsim.
    out = fsimgen.tcl_text(FLOW_TEMPLATE, True, "chiptop0")
    check("catch {fsim -no_coats -selected_status" in out, "flow: serial pass gets -no_coats")
    check(out.index("getobj_campaigns -campaign chiptop0") < out.index("\nfsim -verbose"),
          "flow: task split sized before the campaign starts")
    check("regexp {(\\d+)\\s*$}" in out, "flow: Tcl regexp kept literal")
    check("getobj_campaigns" not in fsimgen.tcl_text(FLOW_TEMPLATE, False, "c"),
          "flow: no split block unless asked")
    check(refused(fsimgen.tcl_text, "fsim -verbose\n", False, "c"),
          "flow: a template without the serial pass is refused")
    st = fsimgen.strobe_text(b"BODY\n", "12000", ["a.b", "a.c"]).decode()
    check(st == "`define STROBE_LIST \\\n    $fs_strobe(a.b); \\\n    $fs_strobe(a.c);\n"
               "`define INJECT_CYCLE 12000\nBODY\n", "flow: strobe header")
    check(not fsimgen.strobe_far("ChipTop.x.y", ["TB.top.x.y"], "TB.top"), "flow: strobe on the module")
    check(fsimgen.strobe_far("ChipTop.x.y", ["TB.top.x"], "TB.top"), "flow: strobe away from it")

    # Names that reach SV, Tcl, YAML and make.
    check(hier.check_hier("i_ariane.gen[2].alu_i", "x") == "i_ariane.gen[2].alu_i", "flow: hier ok")
    for bad in ("a b", "a;b", "a.$x", "a}", "a.", "1a"):
        check(refused(hier.check_hier, bad, "x"), f"flow: hierarchy '{bad}' refused")
    for bad in ("a b.riscv", "x$(rm).riscv", "a;b", "a\nb", 'a"b', "{a}"):
        check(refused(hier.check_test_path, bad), f"flow: test path {bad!r} refused")

    # The one way anything is deleted.
    box = root / "box"
    (box / "d").mkdir(parents=True)
    outside = root / "outside"
    outside.mkdir()
    (outside / "keep").write_text("x")
    (box / "link").symlink_to(outside)
    check(safe_rmtree(box / "link", box) and (outside / "keep").exists(),
          "flow: a link is removed, never followed")
    check(refused(safe_rmtree, outside, box), "flow: nothing outside the root")
    check(refused(safe_rmtree, box / "d" / "..", box), "flow: no '..' tricks")
    check(refused(safe_rmtree, box, box), "flow: not the root itself")

    # Configuration: values checked once, overrides typed.
    c = fconf.load(None, ["cycles.par=7", "study.binary=sub/x.riscv"])
    check(c.maxcyc_par == 7 and c.binary == "sub/x.riscv", "flow: overrides")
    for bad in ("initreg.run=2", "dump.mode=huge", "cycles.par=0", "study.dut=a;b",
                "fsim.inject_at=soon", "fsim.model=xyz", "run.force_regs=1"):
        check(refused(fconf.load, None, [bad]), f"flow: '{bad}' refused")

    # Make arguments: synthesis reused, overlay always, frozen SRAM models.
    repo = root / "repo"
    p = Paths(repo, "v9", c)
    (p.obj_dir / "sim-models").mkdir(parents=True)
    check(make.comp_target(p, "debug") == "sim-syn-debug", "flow: first build regenerates its input")
    (p.obj_dir / "sim-syn-input.json").write_text("{}")
    check(make.comp_target(p, "fast") == "redo-sim-syn", "flow: later builds reuse it")
    check("EXTRA_SIM_SOURCES=" not in " ".join(make.build_args(p)), "flow: no models, no override")
    p.sim_models.write_text("x")
    args = make.build_args(p)
    check(f"EXTRA_SIM_SOURCES={p.sim_models}" in args and
          "VLSI_OBJ_DIR=build-nangate45-commercial-cva6-v9" in args, "flow: tagged build args")
    check(make.overlay_args(p) == [f"EXTRA_CONFS={c.overlay}"], "flow: overlay in EXTRA_CONFS")

    # simv flags: the flow's own, per-test ones dropped, caps imposed.
    rd = root / "rd"
    rd.mkdir()
    (rd / "sim-output-full.json").write_text(json.dumps({
        "sim.inputs.execution_flags": ["+dramsim", "+max-cycles=1000000000", "+loadmem=/x",
                                       "+verbose", "+vcs+initreg+1", "+ntb_random_seed_automatic"]}))
    f = simv.load_flags(rd, root).with_max_cycles(5).with_initreg("0").with_seed(3)
    check(f.from_flow and f.base == ["+dramsim", "+max-cycles=5", "+vcs+initreg+0",
                                      "+ntb_random_seed=3"], f"flow: simv flags {f.base}")
    check(not simv.load_flags(root / "none", root).from_flow, "flow: template when Hammer said nothing")
    argv = simv.command(Path("/s/simv"), f, "/t.riscv")
    check("-ucli" not in argv and argv[-1] == "/t.riscv", "flow: no UCLI unless there is a script")
    check(simv.command(Path("/s"), f, "/t", None, Path("/d.tcl"))[-4:-2] == ["-do", "/d.tcl"],
          "flow: the script goes through -do")


SRAM_P = "00000020_00000030_00000008_00000001_00000001_none_0"   # one of the shells


def _sram_stub(name: str) -> str:
    """An empty module as DC writes it, header over two lines."""
    return (f"module {name} ( clk_i, \n        rst_ni, req_i, we_i, addr_i, wdata_i, be_i, rdata_o );\n"
            "  input [4:0] addr_i;\n  output [47:0] rdata_o;\n  input clk_i, rst_ni;\n\n\nendmodule\n\n")


def test_flow_srams(root: Path) -> None:
    import contextlib
    import io

    from flowkit import config as fconf
    from flowkit import make, srams
    from flowkit.cmds import Flow
    from flowkit.cmds import comp as compcmd
    from flowkit.paths import Paths
    from flowkit.ui import FlowError

    c = fconf.load()
    p = Paths(root / "repo", "v9", c)
    p.netlist.parent.mkdir(parents=True)
    p.sim_models.parent.mkdir(parents=True)

    def user(sram: str) -> str:
        return (f"module wrap ( clk );\n  input clk;\n  {sram} i_sram ( .clk_i(clk) );\n"
                "  assign x = clk;\nendmodule\n\n")

    def prepared(netlist: str, models: str) -> str:
        """The error of the check before a build, or ''."""
        p.netlist.write_text(netlist)
        p.sim_models.write_text(models)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                srams.prepare(p)
        except FlowError as e:
            return e.render()
        return ""

    # A stub a model has the name of: replaced as it is, no shells.
    name = f"tc_sram_{SRAM_P}"
    e = prepared(_sram_stub(name) + user(name), f"module {name} #(\n);\nendmodule\n")
    check(not e and not p.sram_shells.exists(), f"srams: covered stub, no shells {e}")
    check(make.build_args(p)[-1] == f"EXTRA_SIM_SOURCES={p.sim_models}", "srams: covered, args as before")

    # A stub with DC's suffix: the shells file, after the models.
    e = prepared(_sram_stub(name + "_2") + user(name + "_2"), f"module {name} #(\n);\nendmodule\n")
    check(not e and p.sram_shells.exists() and
          p.sram_shells.read_text() == srams.SHELLS.read_text(), f"srams: shells copied {e}")
    check(make.build_args(p)[-1] == f"EXTRA_SIM_SOURCES={p.sim_models} {p.sram_shells}",
          "srams: shells after the models they instantiate")

    # Covered again: the copy goes.
    prepared(_sram_stub(name) + user(name), f"module {name} #(\n);\nendmodule\n")
    check(not p.sram_shells.exists(), "srams: stale shells removed")

    # An empty SRAM, or one nobody defines, that neither covers: no make call.
    e = prepared(_sram_stub("tc_sram_other_2") + user("tc_sram_other_2"), "")
    check("tc_sram_other_2" in e, f"srams: uncovered stub stops {e}")
    e = prepared(user("tc_sram_other"), "")
    check("tc_sram_other" in e, f"srams: undefined SRAM stops {e}")
    ex = FakeExecutor()
    flow = Flow(conf=c, paths=p, ex=ex, dump_mode=c.dump)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            compcmd.build(flow, "fast", "/t/hello.riscv")
        stopped = False
    except FlowError:
        stopped = True
    check(stopped and not ex.calls, f"srams: no build with an uncovered SRAM {ex.calls}")


def _flow_tree(root: Path) -> Path:
    """A checkout with a built simv, as far as flow.py can tell."""
    repo = root / "chipyard"
    for rel in ("vlsi/fsim/strobe/strobe_cva6.sv", "vlsi/fsim/script/fsim.tcl"):
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / "vlsi/fsim/strobe/strobe_cva6.sv").write_text("// strobe\n")
    (repo / "vlsi/fsim/script/fsim.tcl").write_text(FLOW_TEMPLATE)
    (repo / "tests").mkdir()
    for name in ("hello.riscv", "custom_alu.riscv"):
        (repo / "tests" / name).write_text("elf")
    rundir = (repo / "vlsi/build-nangate45-commercial-cva6"
              / "chipyard.harness.TestHarness.CVA6Config-ChipTop/sim-syn-rundir")
    rundir.mkdir(parents=True)
    (rundir / "simv").write_text("")
    (rundir / "simv").chmod(0o755)
    (rundir / ".flow-build.stamp").write_text("fast random\n")
    return repo


def test_flow_commands(root: Path) -> None:
    import contextlib
    import io

    from flowkit import cli
    from flowkit import config as fconf
    from flowkit.cmds import Flow
    from flowkit.cmds import par as parcmd
    from flowkit.paths import Paths

    repo = _flow_tree(root)
    c = fconf.load()
    p = Paths(repo, "", c)

    def simv(argv, cwd, log):
        name = Path(argv[-1]).name
        log.write_text("$finish\nTime: 2000 ns\n" if name == "hello.riscv" else "")
        return 0

    ex = FakeExecutor({"simv": simv})
    flow = Flow(conf=c, paths=p, ex=ex, dump_mode=c.dump)
    args = cli.parser().parse_args(["par", "-j", "2", "hello.riscv", "custom_alu.riscv"])
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        rc = parcmd.run(flow, args)
    text = buf.getvalue()
    check(rc == 0 and "PASS  hello.riscv  [2000 cycles]" in text, "flow: par runs and reads verdicts")
    check("ERROR custom_alu.riscv  (exit=0 but no verdict" in text, "flow: and reports the silent one")
    check(not any(a == "make" for call in ex.calls for a in call[:1]), "flow: a valid simv is not rebuilt")
    check(all("-ucli" not in call for call in ex.calls), "flow: plain runs need no UCLI")
    check(all(f"+max-cycles={c.maxcyc_par}" in call for call in ex.calls), "flow: every run is capped")

    # fsim -n: the generated campaign files, nothing launched.
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        rc = cli.main(["--repo", str(repo), "fsim", "-n", "hello.riscv", "--strobe", "ex"])
    gen = p.fsim_gen_dir
    check(rc == 0 and (gen / "fsim-tasks_split.tcl").exists(), "flow: fsim -n writes the campaign")
    yml = (gen / "fsim-saf-f_alu-s_ex_module.yml").read_text()
    check('location: "ChipTop.system.tile_prci_domain.element_reset_domain_cva6_tile.core.'
          'i_ariane.i_cva6.ex_stage_i.alu_i.**"' in yml, "flow: faults where the dut is")
    check(f"EXTRA_CONFS={c.overlay}" in buf.getvalue() and "DRY-RUN" in buf.getvalue(),
          "flow: the make line carries the overlay")
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        check(cli.main(["--repo", str(repo), "fsim", "-n", "--steps", "--to_step x; rm -rf ~"]) == 2,
              "flow: --steps takes a step option, nothing else")
        check(cli.main(["--repo", str(repo), "fsim", "-n", "--strobe", "alu;x"]) == 2,
              "flow: a strobe that is not a hierarchy is refused")
        check(cli.main(["--repo", str(repo), "syn"]) == 2, "flow: syn wants a tag")
        done = Paths(repo, "t1", c).netlist
        done.parent.mkdir(parents=True)
        done.write_text("module ChipTop; endmodule\n")
        check(cli.main(["--repo", str(repo), "--build", "t1", "syn", "-n"]) == 2,
              "flow: syn never overwrites a finished netlist")

    # A fresh synthesis has no simulation filelist: made once, the jar kept old.
    from flowkit.cmds import comp as compcmd
    ex2 = FakeExecutor()
    flow2 = Flow(conf=c, paths=p, ex=ex2, dump_mode=c.dump)
    with contextlib.redirect_stdout(io.StringIO()):
        compcmd.complete_sim_rtl(flow2)
    made = ex2.calls[0] if ex2.calls else []
    check(made[:3] == ["make", "-o", str(p.generator_jar)] and str(p.simfiles_common) in made,
          f"flow: a missing simulation filelist is generated, elaboration reused: {made}")
    p.simfiles_common.parent.mkdir(parents=True, exist_ok=True)
    p.simfiles_common.write_text("rtl.sv\n")
    with contextlib.redirect_stdout(io.StringIO()):
        compcmd.complete_sim_rtl(flow2)
    check(len(ex2.calls) == 1, "flow: an existing filelist is left alone")


def main(verbose: bool = False) -> int:
    # The flow reads the checkout and the synthesis from these: the tests build
    # their own and must not see the caller's.
    for var in ("CY_REPO", "CY_BUILD"):
        os.environ.pop(var, None)
    tool_root = HERE.parent
    tests = [
        ("units", lambda r: test_units()),
        ("preflight", lambda r: test_preflight(r, tool_root)),
        ("core budget and pool", lambda r: test_cores(r, tool_root)),
        ("core budget changed live", lambda r: test_resize(r, tool_root)),
        ("pipeline", lambda r: test_pipeline(r, tool_root)),
        ("run output", lambda r: test_run_output(r, tool_root)),
        ("recovery", lambda r: test_recovery(r, tool_root)),
        ("infrastructure retry", lambda r: test_infra_retry(r, tool_root)),
        ("command line", lambda r: test_cli(r, tool_root)),
        ("socket", lambda r: test_socket(r, tool_root)),
        ("frozen inputs", lambda r: test_frozen_inputs(r, tool_root)),
        ("force_regs off", lambda r: test_force_regs_off(r, tool_root)),
        ("bookkeeping", lambda r: test_bookkeeping(r, tool_root)),
        ("genome output", lambda r: test_genome_output(r, tool_root)),
        ("flow units", test_flow_units),
        ("flow srams", test_flow_srams),
        ("flow commands", test_flow_commands),
    ]
    for name, fn in tests:
        before = len(FAILURES)
        tmp = Path(tempfile.mkdtemp(prefix="evosbst-selftest-"))
        try:
            fn(tmp)
        except Exception as exc:
            FAILURES.append(f"{name}: {type(exc).__name__}: {exc}")
            if verbose:
                import traceback

                traceback.print_exc()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        new = FAILURES[before:]
        print(f"  {'FAIL' if new else 'ok  '}  {name}")
        for f in new:
            print(f"          - {f}")

    print(f"\n{CHECKS - len(FAILURES)}/{CHECKS} checks passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main("-v" in sys.argv))
