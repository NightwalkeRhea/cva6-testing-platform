"""Everything that has to exist before a run, checked in one pass.

Missing pieces used to surface one at a time, as whichever stage reached them
first -- and the worst of them only after the first individual had been built,
as an infrastructure failure, which is retried for half an hour before anyone
is told. Someone setting this up on a new machine needs the whole list at once,
and needs it to name the files the flow produces rather than the copies made
from them.

Nothing here is created or repaired. This only looks.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import sources as srcs

MISSING = "MISSING"
OK = "ok"
ABSENT = "absent"


@dataclass
class Item:
    group: str          # flow | campaign | probe | build | tool
    what: str           # what it is, in words
    where: str          # where it was looked for
    ok: bool
    required: bool = True

    @property
    def verdict(self) -> str:
        if self.ok:
            return OK
        return MISSING if self.required else ABSENT


def inspect(site, camp, ex) -> list[Item]:
    """The whole precondition list, in the order someone works through it."""
    items: list[Item] = []

    def add(group: str, what: str, path: Path, required: bool = True) -> None:
        items.append(Item(group, what, str(path), path.exists(), required))

    add("flow", "the checkout", site.repo)
    add("flow", "the build directory", site.build)

    # The same resolution a run uses, so check and setup cannot disagree.
    found = srcs.locate(site.build, camp)
    fsim = found.rundir
    add("campaign", "fault-aware simulator", fsim / "simv")
    add("campaign", "its daidir", fsim / "simv.daidir")
    # The run.tcl of a snapshot is rewritten to source this, so a campaign
    # cannot start without it even though nothing reads it until then.
    add("campaign", "register forcing", fsim / "force_regs.ucli")
    _add_found(items, "campaign", "fault list", found.sff, fsim, found, "fault list")
    _add_found(items, "campaign", "campaign script", found.tcl, fsim, found,
               "campaign script")
    strobe = (found.tcl.parent / "strobe.sv") if found.tcl else fsim / "strobe.sv"
    add("campaign", "strobe", strobe, required=False)

    probe = found.probe
    add("probe", "fault-free simulator", probe / "simv")
    add("probe", "its register forcing", probe / "force_regs.ucli", required=camp.force_regs)
    add("probe", "the flow's own plusargs", probe / "sim-output-full.json", required=False)

    add("build", "linker script", site.repo / "tests" / "htif.ld")
    add("build", "genome", camp.genome_file)
    if camp.ga_file is not None:
        add("build", "population settings", camp.ga_file)
    scaffold = "boot_bare.S" if camp.boot == "bare" else "main_newlib.c"
    add("build", f"scaffolding ({camp.boot})", _tool_root() / "genome" / "sbst" / scaffold)

    for name, path in (("ugp3", site.tools.ugp3), ("vc_fcc", site.tools.vc_fcc),
                       ("vc_fcm", site.tools.vc_fcm), ("compiler", site.tools.cc),
                       ("disassembler", site.tools.objdump)):
        if Path(path).is_absolute():
            add("tool", name, Path(path))
        else:
            items.append(Item("tool", name, f"{path} (on PATH)", bool(ex.which(path))))

    return items


def _add_found(items: list[Item], group: str, what: str, path: Path | None,
               base: Path, found, key: str) -> None:
    """A path that may have been looked for rather than given. When the search
    failed, the line says what was searched for -- which is the only thing that
    tells you whether to build something or to write a line of config."""
    how = found.found.get(key, "")
    if path is None:
        items.append(Item(group, what, f"{base}/{how}", False))
        return
    where = str(path) + (f"   (found by {how})" if how and "->" not in how else "")
    items.append(Item(group, what, where, path.exists()))


def missing(items: list[Item]) -> list[Item]:
    return [i for i in items if i.required and not i.ok]


def report(items: list[Item], site=None) -> str:
    """One line each, with the two long roots factored out of every line.

    A reader needs to see which piece is missing, not the same 120-character
    prefix fourteen times.
    """
    roots = [("<here>", str(_tool_root()))]
    if site is not None:
        roots = [("<build>", str(site.build)), ("<repo>", str(site.repo))] + roots

    def short(where: str) -> str:
        for name, root in roots:
            if where == root:
                return name
            if where.startswith(root + "/"):
                return name + where[len(root):]
        return where

    wide = max(len(i.what) for i in items)
    lines = [f"  {name:<8} {root}" for name, root in roots]
    if lines:
        lines.append("")
    group = ""
    for i in items:
        head = i.group if i.group != group else ""
        group = i.group
        lines.append(f"  {head:<9} {i.what:<{wide}}  {i.verdict:<7}  {short(i.where)}")
    return "\n".join(lines)


FROM_FLOW = """\
What the flow leaves behind is read here and never written: the two rundirs
come from a synthesis and a fault campaign (flow.py fsim makes both).

  conf/site.toml            says which checkout and which build directory
  conf/campaigns/*.toml     says which rundir, which fault list, which campaign

If you can run one fault campaign by hand, you have everything it needs."""

TOOLS = """\
A tool marked MISSING is not on the PATH of this shell: start from the shell
you run the EDA tools from, or name it by absolute path in conf/site.toml
[tools]. uGP3 is not one of the EDA tools: it is built or loaded separately."""

BUILD = """\
The genome and the scaffolding come with this folder, the linker script with
the checkout (tests/htif.ld); conf/campaigns/*.toml names the genome."""


def advice(gaps: list[Item]) -> str:
    """What to do about the gaps actually found, and nothing else: a missing
    compiler is not something the flow will produce."""
    groups = {i.group for i in gaps}
    parts = [FROM_FLOW] if groups & {"flow", "campaign", "probe"} else []
    if "tool" in groups:
        parts.append(TOOLS)
    if "build" in groups:
        parts.append(BUILD)
    parts.append('docs/evosbst-usage.md, "What it needs", says it in full.')
    return "\n\n".join(parts)


def _tool_root() -> Path:
    return Path(__file__).resolve().parent.parent
