"""par: N tests in parallel, simv invoked directly, one workdir each.

Not through make, for two reasons. The program is loaded with +loadmem, which
the make flow fixes to a single path, so Hammer's 'benchmarks' list would run
one program N times. And N parallel make runs share one rundir and trample
each other's bookkeeping files. Run directly, every test has its own workdir
and its own +loadmem; the simv is shared read-only.
"""
from __future__ import annotations

import fnmatch
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .. import simv
from ..hier import check_test_path
from ..ui import FlowError, blank, say, sub
from . import Flow, int_min, stamped_dir
from . import comp

HELP = "run tests in parallel on the simv, building it first if needed"
DESCRIPTION = """\
Run tests in parallel, simv invoked directly, each in its own workdir under
the sim rundir. Verdicts:

  FAIL   "*** FAILED ***" in the log
  PASS   "*** PASSED ***" or "$finish" in the log
  ERROR  neither: simv died, or it exited 0 without ever simulating
  MISS   the test file does not exist

Every verdict also reports the finishing cycle, where measurable."""


def add(sub_parsers) -> None:
    p = sub_parsers.add_parser("par", help=HELP, description=DESCRIPTION)
    p.add_argument("tests", nargs="*", metavar="test",
                   help="relative to tests/, or absolute")
    p.add_argument("-d", "--dir", help="also every *.riscv in DIR (links included)")
    p.add_argument("-j", dest="jobs", type=int_min(1), help="runs in parallel (default run.jobs)")
    p.add_argument("-w", "--wave", action="store_true",
                   help="dump the waveform (forces a DEBUG simv); see dump.mode")
    p.add_argument("--dump", choices=("off", "lean", "full"), help="dump mode, this run only")
    p.add_argument("--max-cycles", type=int_min(1), help="cycle cap (default cycles.par)")
    p.add_argument("--seed", type=int_min(0),
                   help="fix +ntb_random_seed (default: automatic, runs not reproducible)")
    add_build_options(p)
    p.set_defaults(func=run)


def add_build_options(p) -> None:
    p.add_argument("--rebuild", action="store_true", help="rebuild the simv even if it looks valid")
    p.add_argument("--no-build", action="store_true", help="never rebuild (use what is there, and warn)")


def apply_build_options(flow: Flow, args) -> None:
    flow.comp_force = args.rebuild
    flow.comp_auto = not args.no_build


def dump_describe(flow: Flow) -> str:
    return {
        "off": "off (no waveform)",
        "full": 'full ("+all": the entire testbench — enormous and slow)',
        "lean": f"lean (core ports + all of {flow.conf.dut})",
    }[flow.dump_mode]


def dump_script(flow: Flow, fsdb: Path) -> str:
    """The UCLI script of one dumping run, one per test so parallel runs
    cannot overwrite each other's script or waveform.

    The dump is opened here rather than with +fsdbfile=, which would make the
    testbench dump everything. 'lean' needs UCLI's own dump command: 'scope'
    followed by fsdbDumpvars ignores the scope and dumps everything too."""
    from .. import hier
    c, rundir = flow.conf, flow.paths.rundir
    dut = hier.join(c.strobe_root, hier.resolve(c.dut, c.aliases))
    lines = []
    if c.force_regs:
        lines.append(f"source {rundir / 'force_regs.ucli'}")
    if flow.dump_mode == "full":
        lines += [f'fsdbDumpfile "{fsdb}"', f"scope {c.tb_root}", "fsdbDumpvars 0"]
    else:
        lines += [f'set fid [dump -file "{fsdb}" -type FSDB]',
                  f"dump -add {c.strobe_root} -depth {c.dump_core_depth} -fid $fid",
                  f"dump -add {dut} -depth 0 -fid $fid"]
    lines += [f"scope {c.tb_root}", "run", "exit"]
    return "\n".join(lines) + "\n"


def run(flow: Flow, args) -> int:
    c, p = flow.conf, flow.paths
    apply_build_options(flow, args)
    if args.dump:
        flow.dump_mode = args.dump
    jobs = args.jobs or c.jobs
    maxcyc = args.max_cycles or c.maxcyc_par
    tests = [flow.require_test(t) for t in args.tests]
    if args.dir:
        d = p.test(args.dir)
        if not os.path.isdir(d):
            raise FlowError(f"directory not found: {d}")
        d = d.rstrip("/") or "/"
        # Links count: many tests are links, and skipping them silently would
        # make the list shorter than the directory.
        found = sorted(f"{d}/{n}" for n in os.listdir(d)
                       if fnmatch.fnmatchcase(n, "*.riscv") and os.path.isfile(f"{d}/{n}"))
        tests += [check_test_path(t) for t in found]
    if not tests:
        raise FlowError("no test passed to 'par'.")

    # -w needs a debug simv: a fast one never writes the dump, silently.
    comp.ensure(flow, "debug" if args.wave else "any", tests[0])
    comp.require_built(flow)
    script = comp.run_script(flow)
    flags = simv.load_flags(p.rundir, p.dramsim_ini)
    if not flags.from_flow:
        from ..ui import warn
        warn("sim-output-full.json is missing: falling back to template flags.")
    flags = flags.with_max_cycles(maxcyc).with_initreg(c.initreg_run)
    if args.seed is not None:
        flags = flags.with_seed(args.seed)

    par_dir = stamped_dir(p.rundir, "par")
    par_dir.mkdir(parents=True)
    if args.wave:
        p.output_dir.mkdir(parents=True, exist_ok=True)

    eff = min(jobs, len(tests))
    wtxt = f"yes — {dump_describe(flow)}" if args.wave else "no"
    say(f"PAR: {len(tests)} tests, {eff} in parallel (-j cap={jobs})")
    sub(f"waveform   : {wtxt}")
    sub(f"max-cycles : {maxcyc}")
    sub(f"initreg    : compile={c.initreg_compile} runtime={c.initreg_run}")
    sub(f"seed       : {args.seed if args.seed is not None else 'automatic (runs are NOT reproducible)'}")
    sub(f"workdir    : {par_dir}")

    def one(idx: int, test: str) -> None:
        name = os.path.basename(test)
        tag = f"{idx:03d}_{name}"
        wdir = par_dir / tag
        wdir.mkdir(parents=True, exist_ok=True)
        if not os.path.isfile(test):
            (wdir / "verdict").write_text(f"MISS  {name}  (not found: {test})\n")
            return
        extra: list[str] = []
        run_tcl = script
        if args.wave:
            extra.append("+verbose")
            if flow.dump_mode != "off":
                run_tcl = wdir / "dump.tcl"
                run_tcl.write_text(dump_script(flow, p.output_dir / f"{tag}.fsdb"))
        argv = simv.command(p.rundir / "simv", flags, test, extra, run_tcl)
        res = simv.execute(flow.ex, argv, wdir)
        (wdir / "verdict").write_text(res.verdict.line(name, c.clock_ns) + "\n")

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for f in [pool.submit(one, i, t) for i, t in enumerate(tests)]:
            f.result()

    report(flow, par_dir, len(tests), args.wave)
    return 0


def report(flow: Flow, par_dir: Path, total: int, wave: bool) -> None:
    """Verdict summary, plus the tail of every log that did not pass, so it
    can be quoted without shipping whole files around."""
    verdicts: list[tuple[Path, str]] = []
    for vf in sorted(par_dir.glob("*/verdict")):
        try:
            verdicts.append((vf.parent, vf.read_text().rstrip("\n")))
        except OSError:
            verdicts.append((vf.parent, ""))
    blank()
    say("RESULTS:")
    for line in sorted(v for _, v in verdicts if v):
        print(line)
    counts = {k: sum(1 for _, v in verdicts if v.startswith(k))
              for k in ("PASS", "FAIL", "ERROR", "MISS")}
    for wdir, v in verdicts:
        cls = v.split(None, 1)[0] if v.strip() else ""
        if cls == "PASS":
            continue
        blank()
        print(f"----- {wdir.name}: {cls or '<empty verdict>'} — last lines of sim.log -----")
        try:
            print("\n".join((wdir / "sim.log").read_text(errors="replace").splitlines()[-20:]))
        except OSError:
            print("(no sim.log)")
    blank()
    say(f"{counts['PASS']}/{total} PASSED  (fail={counts['FAIL']} error={counts['ERROR']} "
        f"miss={counts['MISS']})")
    say(f"Per-test logs: {par_dir}/<nnn>_<test>/sim.log")
    if wave:
        say(f"Waveforms in {flow.paths.output_dir}/<nnn>_<test>.fsdb")
