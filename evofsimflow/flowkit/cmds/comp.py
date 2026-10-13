"""comp / comp-fast: build the simv and stop there (Hammer's run_vcs step).

The other commands never call make to build: they call ensure(), which
decides whether the simv on disk is still suitable and rebuilds only if not.
"""
from __future__ import annotations

import os

from .. import make, simv, srams
from ..shell import safe_rmtree
from ..ui import FlowError, say, sub, warn
from . import Flow

HELP = "build the simv only (comp: debug, comp-fast: non-debug)"
DESCRIPTION = """\
Build the simv and stop: nothing is simulated. 'comp' builds a DEBUG simv,
which can dump waveforms; 'comp-fast' a leaner one that runs faster but
cannot dump (no 'par -w'). Both always rebuild. Running them by hand is rarely
needed: every simulating command builds what it is missing.

The test only feeds BINARY=/LOADMEM= on the make line; the build does not
depend on the program."""


def add(sub_parsers) -> None:
    for name, flavor in (("comp", "debug"), ("comp-fast", "fast")):
        p = sub_parsers.add_parser(name, help=HELP if name == "comp" else None,
                                   description=DESCRIPTION)
        p.add_argument("test", nargs="?", help="default: study.binary")
        p.add_argument("--regen-rtl", action="store_true",
                       help="also regenerate the RTL with sbt/Chisel (almost never needed: "
                            "the RTL is the input to synthesis, which is not redone)")
        p.set_defaults(func=run, flavor=flavor)


def run(flow: Flow, args) -> int:
    flow.regen_rtl = args.regen_rtl
    build(flow, args.flavor, flow.paths.test(args.test or flow.conf.binary))
    return 0


# ------------------------------------------------------------------ stamp
# One line in the rundir, "<flavor> <initreg>". Without it there is no way to
# know how the simv on disk was made, and getting that wrong costs a wasted
# run, silently: a fast simv asked to dump does not dump and says nothing.

def stamp_read(flow: Flow) -> tuple[str, str]:
    for path in (flow.paths.stamp, flow.paths.rundir / ".do-build.stamp"):
        try:
            fields = path.read_text().split()
        except OSError:
            continue
        return (fields + ["", ""])[0], (fields + ["", ""])[1]
    return "", ""


def stamp_write(flow: Flow, flavor: str) -> None:
    flow.paths.rundir.mkdir(parents=True, exist_ok=True)
    flow.paths.stamp.write_text(f"{flavor} {flow.conf.initreg_compile}\n")


# ------------------------------------------------------------------ smoke

def smoke_ok(flow: Flow, rundir, label: str) -> bool:
    """True if the simv in rundir really executes a program. Prints what it
    found. A campaign of weeks on a simv that does not execute produces
    meaningless numbers, and nothing in the report gives it away."""
    c = flow.conf
    if not c.smoke:
        return True
    binary = flow.paths.test(c.smoke_test or c.binary)
    if not os.path.isfile(binary):
        warn(f"smoke test skipped: {binary} is missing")
        return True
    wdir = rundir / f"smoke-{os.getpid()}"
    line = "ERROR interrupted"
    try:
        line = _smoke_line(flow, rundir, binary, wdir)
        sub(f"smoke test {label}: {line}")
    finally:
        # A failure keeps its log: the next attempt deletes the simv it ran on.
        if line.startswith("PASS"):
            safe_rmtree(wdir, rundir)
        elif (wdir / "smoke.log").is_file():
            sub(f"smoke log kept: {wdir / 'smoke.log'}")
    return line.startswith("PASS")


def _smoke_line(flow: Flow, rundir, binary: str, wdir) -> str:
    wdir.mkdir(parents=True, exist_ok=True)
    if not os.access(rundir / "simv", os.X_OK):
        return f"ERROR no simv in {rundir}"
    script = None
    if flow.conf.force_regs:
        if not (rundir / "force_regs.ucli").is_file():
            return "ERROR force_regs.ucli missing"
        script = wdir / "smoke.tcl"
        script.write_text(simv.force_regs_script(rundir))
    argv = simv.smoke_command(rundir, binary, flow.conf.smoke_max_cycles,
                              flow.conf.initreg_run, flow.paths.dramsim_ini, script)
    run = simv.execute(flow.ex, argv, wdir, "smoke.log")
    return run.verdict.line(os.path.basename(binary), flow.conf.clock_ns)


# ------------------------------------------------------------------ build

def clean_artifacts(rundir) -> None:
    """The Hammer plugins remove csrc and the simv before rebuilding, but not
    simv.daidir, and a stale daidir yields simvs that behave differently for
    the same command line. All three go before every compile."""
    if not rundir.is_dir():
        return
    for name in ("csrc", "simv.daidir", "simv"):
        safe_rmtree(rundir / name, rundir)


def complete_sim_rtl(flow: Flow) -> None:
    """A fresh synthesis has no RTL filelist for simulation, and keep_rtl
    would hide that from make until Hammer finds the harness memories
    missing."""
    p = flow.paths
    if flow.regen_rtl or p.simfiles_common.is_file():
        return
    say("RTL filelist for simulation missing: generating it, the elaboration is reused")
    flow.make(make.sim_rtl_args(p))


def build(flow: Flow, flavor: str, test: str) -> None:
    p, c = flow.paths, flow.conf
    srams.prepare(p)
    target = make.comp_target(p, flavor)
    keep: list[str] = [] if flow.regen_rtl else make.keep_rtl(p)
    argv = [*make.common_args(p, test),
            f"HAMMER_EXTRA_ARGS=--to_step run_vcs {make.tools_hammer_args(p)}"]
    if flow.dump_mode == "off":
        argv.append("get_waveform_flag=")
    argv += make.initreg_args(c.initreg_compile, c.initreg_run)
    argv += make.overlay_args(p)

    if not target.startswith("redo-"):
        sub(f"(first build after a clean: using '{target}', sim-syn-input.json is missing)")
    say("BUILD  DEBUG simv (waveform-capable)" if flavor == "debug"
        else "BUILD  FAST simv (non-debug)")
    sub(f"test      : {os.path.basename(test)}  (BINARY=/LOADMEM= only)")
    sub(f"initreg   : compile={c.initreg_compile}  runtime={c.initreg_run}")

    complete_sim_rtl(flow)
    # The stamp describes the simv on disk: it goes with it, and comes back
    # only once a build has passed.
    for stamp in (p.stamp, p.rundir / ".do-build.stamp"):
        stamp.unlink(missing_ok=True)
    # Build, smoke-test, and rebuild if the smoke test fails: a safety net
    # against a build that is not reproducible, which has happened.
    attempts = c.smoke_retries + 1 if c.smoke else 1
    for attempt in range(1, attempts + 1):
        if attempts > 1:
            sub(f"attempt {attempt}/{attempts}")
        clean_artifacts(p.rundir)
        # Asked again each time: once the first attempt has written
        # sim-syn-input.json, the file-based target would rebuild nothing.
        flow.make([*keep, make.comp_target(p, flavor), *argv])
        if smoke_ok(flow, p.rundir, f"simv {flavor}"):
            break
        if not os.access(p.rundir / "simv", os.X_OK):
            raise FlowError(f"the build produced no simv in {p.rundir}.",
                            "This is not the smoke test failing: 'make' did not get to the end.",
                            "Re-read the make output above.")
        if attempt == attempts:
            raise FlowError(
                f"the compiled simv does not execute the program, after {attempts} attempts.",
                "This is not the test: the build itself is not reproducible.",
                "The smoke test can be turned off with smoke.enabled = false.")
        warn("the simv does not execute the program: rebuilding.")

    stamp_write(flow, flavor)
    say(f"simv ready in: {p.rundir / 'simv'}")


def ensure(flow: Flow, need: str, test: str | None = None) -> None:
    """Return once the simv exists and is suitable; build only if needed.

    'fast' by default; need='debug' raises it, since only a debug simv can
    dump. The dump itself is runtime (a UCLI script), so changing the dut or
    the dump mode never invalidates the simv."""
    c = flow.conf
    test = flow.paths.test(test or c.binary)
    st_flavor, st_initreg = stamp_read(flow)
    flavor = "debug" if need == "debug" else (st_flavor or "fast")
    simv_ok = os.access(flow.paths.rundir / "simv", os.X_OK)

    reason = ""
    if flow.comp_force:
        reason = "requested with --rebuild"
    elif not simv_ok:
        reason = "there is no simv"
    elif not st_flavor:
        reason = "a simv exists but nothing records how it was built (no stamp): rebuilding once"
    elif need == "debug" and st_flavor != "debug":
        reason = f"a DEBUG simv is needed and the one on disk is '{st_flavor}'"
    elif st_initreg != c.initreg_compile:
        # An elaboration flag: changing it changes the simv.
        reason = (f"initreg.compile is '{c.initreg_compile}' but the simv was built "
                  f"with '{st_initreg or '?'}'")

    if not reason:
        say(f"simv still valid ({st_flavor}), not rebuilding.")
        return
    if not flow.comp_auto:
        warn(f"the simv should be rebuilt ({reason}), but --no-build is set: "
             "using the existing one.")
        if not simv_ok:
            raise FlowError("...and there is none.", "Drop --no-build, or run 'flow.py comp'.")
        return
    say(f"AUTOMATIC BUILD: {reason}")
    build(flow, flavor, test)


def require_built(flow: Flow) -> None:
    rundir = flow.paths.rundir
    if not os.access(rundir / "simv", os.X_OK):
        raise FlowError(f"simv not found in {rundir}.", "Run 'flow.py comp' first.")
    if flow.conf.force_regs and not (rundir / "force_regs.ucli").is_file():
        raise FlowError(f"force_regs.ucli missing in {rundir}.", "Run 'flow.py comp' first.")


def run_script(flow: Flow):
    """The UCLI script of a plain run, or None when there is nothing for UCLI
    to do. Written atomically when missing: two concurrent runs must never
    see a truncated one, which makes simv exit 0 without simulating."""
    if not flow.conf.force_regs:
        return None
    rundir = flow.paths.rundir
    path = rundir / "run.tcl"
    if path.is_file():
        return path
    tmp = rundir / f"run.tcl.{os.getpid()}"
    tmp.write_text(simv.force_regs_script(rundir))
    tmp.replace(path)
    say("run.tcl generated (it was missing).")
    return path
