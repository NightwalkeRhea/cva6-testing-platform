"""Names: hierarchy paths in the simulated design, and test paths on disk.

Everything named here ends up inside generated SystemVerilog, Tcl, YAML or a
make command line, so it is checked against what those can carry before it is
written anywhere.
"""
from __future__ import annotations

import re

from .ui import FlowError

# A dotted path of plain Verilog identifiers, each optionally indexed.
_SEGMENT = r"[A-Za-z_][A-Za-z0-9_$]*(?:\[\d+\])*"
_HIER_RE = re.compile(rf"^{_SEGMENT}(?:\.{_SEGMENT})*$")
# Characters a test path may not hold: they would be read as syntax by make,
# YAML or Tcl before they ever reach the simulator.
_PATH_BAD = re.compile(r"[\s\"'`{}$\\;|&<>*?\[\]#=,]")
_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]")


def check_hier(path: str, what: str) -> str:
    if path and not _HIER_RE.match(path):
        raise FlowError(f"{what} is not a hierarchy path: '{path}'",
                        "Dotted Verilog identifiers only, e.g. i_ariane.i_cva6.ex_stage_i.")
    return path


def check_test_path(path: str) -> str:
    if not path or _PATH_BAD.search(path):
        raise FlowError(f"unusable test path: '{path}'",
                        "No blanks, quotes, braces, '$', '=' or shell/glob characters:"
                        " it travels through make, YAML and Tcl.")
    return path


def resolve(name: str, aliases: dict[str, str]) -> str:
    """An alias, or the name itself; either way relative to the CVA6 core."""
    return aliases.get(name, name)


def join(root: str, suffix: str) -> str:
    """Root plus suffix. A suffix that already starts at ChipTop or at the
    testbench is a full path and is taken as is."""
    if not suffix:
        return root
    if suffix.startswith(("ChipTop.", "TestDriver.")):
        return suffix
    return f"{root}.{suffix}"


def slug(text: str) -> str:
    """Make a string usable as part of a file name."""
    return _SLUG_RE.sub("_", text)
