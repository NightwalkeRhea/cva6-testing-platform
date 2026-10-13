"""Running a simv directly, without make.

Nothing else builds a simv command line: cycle caps, seed, register
initialisation and the UCLI script are decided here, so two commands cannot
drift apart on them.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .shell import Executor
from .verdict import Verdict, parse_file

# Per-test flags: re-injected for every run, never inherited from the flow.
_PER_TEST = ("+loadmem=", "+fsdbfile=", "+dumpfile=", "+vcdplusfile=")
FLAGS_FILE = "sim-output-full.json"


@dataclass
class Flags:
    prep: list[str]
    base: list[str]
    app: list[str]
    # False when sim-output-full.json was missing and a template stood in.
    from_flow: bool = True

    def with_max_cycles(self, n: int) -> "Flags":
        """The cycle cap, always: the flow's own TIMEOUT_CYCLES is a "forever",
        and a hung run would hold a licence for days."""
        base = [t for t in self.base if not t.startswith("+max-cycles=")]
        return Flags(self.prep, base + [f"+max-cycles={n}"], self.app, self.from_flow)

    def with_initreg(self, value: str) -> "Flags":
        base = [t for t in self.base if not t.startswith("+vcs+initreg+")]
        tail = [f"+vcs+initreg+{value}"] if value else []
        return Flags(self.prep, base + tail, self.app, self.from_flow)

    def with_seed(self, n: int) -> "Flags":
        """A fixed seed in place of +ntb_random_seed_automatic, which changes on
        every invocation and makes two runs of one program incomparable."""
        base = [t for t in self.base
                if t != "+ntb_random_seed_automatic" and not t.startswith("+ntb_random_seed=")]
        return Flags(self.prep, base + [f"+ntb_random_seed={n}"], self.app, self.from_flow)


def load_flags(rundir: Path, dramsim_ini: Path) -> Flags:
    """The design-stable execution flags, from what Hammer computed for this
    rundir; a template when it computed nothing."""
    src = rundir / FLAGS_FILE
    try:
        data = json.loads(src.read_text())
    except (OSError, ValueError):
        data = None
    if isinstance(data, dict):
        base = [t for t in _strs(data.get("sim.inputs.execution_flags", []))
                if not t.startswith(_PER_TEST) and t != "+verbose"]
        if base:
            return Flags(
                prep=_strs(data.get("sim.inputs.execution_flags_prepend", ["+permissive"])),
                base=base,
                app=_strs(data.get("sim.inputs.execution_flags_append", ["+permissive-off"])),
            )
    return Flags(
        prep=["+permissive"],
        base=["+dramsim", f"+dramsim_ini_dir={dramsim_ini}", "+vcs+initreg+1",
              "+ntb_random_seed_automatic"],
        app=["+permissive-off"],
        from_flow=False,
    )


def _strs(value) -> list[str]:
    return [str(v) for v in value] if isinstance(value, list) else []


def command(simv: Path, flags: Flags, binary: str, extra: list[str] | None = None,
            script: Path | None = None) -> list[str]:
    """One run's argv. A UCLI script is passed only when there is one: without
    it simv runs straight through, which is also the fastest way to run."""
    ucli = ["-ucli", "-do", str(script)] if script else []
    return [str(simv), *flags.prep, *flags.base, f"+loadmem={binary}", *(extra or []),
            *ucli, *flags.app, binary]


@dataclass
class Run:
    wdir: Path
    log: Path
    rc: int
    verdict: Verdict


def execute(ex: Executor, argv: list[str], wdir: Path, log_name: str = "sim.log") -> Run:
    """Run in its own workdir, everything into <wdir>/sim.log. stdin is
    /dev/null: a simv that lands on the UCLI prompt gets EOF and exits instead
    of waiting on a keyboard."""
    wdir.mkdir(parents=True, exist_ok=True)
    log = wdir / log_name
    res = ex.run(argv, cwd=wdir, log=log)
    return Run(wdir, log, res.rc, parse_file(log, res.rc))


def smoke_command(rundir: Path, binary: str, max_cycles: int, initreg_run: str,
                  dramsim_ini: Path, script: Path | None) -> list[str]:
    """The minimal flags, built here rather than read from the flow, so the
    check works on any rundir right after a build: sim or fsim alike."""
    flags = ["+permissive", "+dramsim", f"+dramsim_ini_dir={dramsim_ini}",
             f"+max-cycles={max_cycles}", f"+loadmem={binary}"]
    if initreg_run:
        flags.append(f"+vcs+initreg+{initreg_run}")
    if script:
        flags += ["-ucli", "-do", str(script)]
    return [str(rundir / "simv"), *flags, "+permissive-off", binary]


def force_regs_script(rundir: Path) -> str:
    """The UCLI script Hammer would run: the register forcing, then run."""
    return f"source {rundir / 'force_regs.ucli'}\nrun\nexit\n"
