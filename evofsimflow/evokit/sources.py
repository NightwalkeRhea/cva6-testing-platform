"""Where the flow's files are.

One rule: **a path written in the configuration is used exactly as written; a
path left out is looked for.** Most of them follow the flow's own conventions,
and typing them again is a chance to get them wrong.

`check`, `setup` and a run all resolve through here, so they cannot disagree
about which files they are looking at.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Sources:
    """The flow's files, resolved. `None` means looked for and not found."""

    rundir: Path
    tcl: Path | None
    sff: Path | None
    strobe: Path | None
    probe: Path
    # what was looked for rather than given, and where: shown by `check`, so
    # nobody has to guess whether a path came from the config or from a glob.
    found: dict[str, str] = field(default_factory=dict)


def locate(build: Path, camp) -> Sources:
    rundir = resolve(build, camp.src_rundir)
    probe = resolve(build, camp.probe_rundir)
    found: dict[str, str] = {}

    tcl = _pick(rundir, camp.src_tcl, "saf/*/fsim.tcl", "campaign script", found)
    sff = _pick(rundir, camp.src_sff, "*.sff", "fault list", found)
    strobe = tcl.parent / "strobe.sv" if tcl else None
    return Sources(rundir=rundir, tcl=tcl, sff=sff,
                   strobe=strobe if strobe and strobe.exists() else None,
                   probe=probe, found=found)


def _pick(rundir: Path, given: str, pattern: str, what: str,
          found: dict[str, str]) -> Path | None:
    if given:
        return resolve(rundir, given)
    hits = sorted(p for p in rundir.glob(pattern) if p.is_file())
    if len(hits) == 1:
        found[what] = pattern
        return hits[0]
    # None, or several with no way to choose: say which, and let the caller
    # report it as missing. Guessing here would be a coin toss recorded in the
    # inputs-hash as if it were a decision.
    found[what] = f"{pattern} -> {len(hits)} matches" if hits else f"{pattern} -> none"
    return None


def resolve(base: Path, value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else base / value
