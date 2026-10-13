"""The netlist's SRAM stubs against the models that replace them in simulation.

Design Compiler writes every SRAM it kept as a black box as an empty module,
and the behavioural models (sim-models/memories.sv) replace them name for
name, coming after the netlist on the vcs line. A stub DC named differently
from every model stays empty, its outputs X, and the core executes nothing:
a build only shows it as a smoke test timing out. conf/cva6-sram-shells.sv
covers the names CVA6Config gets today; anything else stops the build.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from .config import TOOL_ROOT
from .paths import Paths
from .shell import safe_rmtree
from .ui import FlowError, sub

SHELLS = TOOL_ROOT / "conf" / "cva6-sram-shells.sv"

_MOD = re.compile(r"^\s*module\s+(\w+)")
_INST = re.compile(r"^\s*(tc_sram\w*)\s+\S+\s*\(")


def scan_netlist(path: Path) -> tuple[set[str], set[str]]:
    """The tc_sram* modules instantiated, and the modules with a body (an
    instance or an assign, not only declarations)."""
    used: set[str] = set()
    full: set[str] = set()
    name, header = None, False
    with open(path, errors="replace") as f:
        for line in f:
            m = _MOD.match(line)
            if m:
                name, header = m.group(1), ");" not in line
            elif name is None:
                continue
            elif header:
                header = ");" not in line
            elif line.lstrip().startswith("endmodule"):
                name = None
            elif "(" in line or "assign " in line:
                full.add(name)
                i = _INST.match(line)
                if i:
                    used.add(i.group(1))
    return used, full


def modules(path: Path) -> set[str]:
    with open(path, errors="replace") as f:
        return {m.group(1) for line in f if (m := _MOD.match(line))}


def prepare(p: Paths) -> None:
    """Before a build: every SRAM the netlist leaves empty must have a model or
    a shell; the shells are copied next to the models only when needed."""
    if not p.netlist.is_file():
        return
    used, full = scan_netlist(p.netlist)
    models = modules(p.models_file) if p.models_file.is_file() else set()
    empty = used - full - models
    missing = sorted(empty - modules(SHELLS))
    if missing:
        raise FlowError("SRAMs in the netlist that no model replaces:", *missing,
                        f"models: {p.models_file}", f"shells: {SHELLS}",
                        "Not building: the simv would read X from them and execute nothing.")
    if not empty:
        if safe_rmtree(p.sram_shells, p.obj_dir):
            sub(f"sram shells : none needed, {p.sram_shells.name} removed")
        return
    p.sram_shells.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SHELLS, p.sram_shells)
    sub(f"sram shells : {len(empty)} stub(s) run on their models, {p.sram_shells}")
