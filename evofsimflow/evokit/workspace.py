"""A workspace is the frozen half of a campaign: the elaborated simv, its
daidir, the fault list and the campaign tcl.

Two modes, because two campaigns can differ in two very different ways:

  reference  the live fsim rundir is used in place. One simv, so several
             campaigns can share it as long as they only differ by fault list.
  snapshot   a hardlink copy of the rundir. Costs no disk and no time, and
             survives a later rebuild of the flow, which is what lets two
             campaigns use two differently elaborated simvs (different strobe
             or injection point) at the same time.

A snapshot is a tree of hardlinks: nothing here may ever be rewritten in
place, or the write would reach through into the live rundir.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from flowkit.shell import safe_rmtree

from . import sources as srcs
from .manifest import sha256_file, write_json, write_text


class WorkspaceError(Exception):
    pass


@dataclass
class Workspace:
    name: str
    root: Path
    rundir: Path          # holds simv, simv.daidir, force_regs.ucli
    simv: Path
    daidir: Path
    run_tcl: Path
    sff: Path
    tcl_template: Path
    strobe: Path | None
    zoix_campaign: str
    mode: str
    probe_rundir: Path    # the fault-free simv the probe stage runs
    probe_run_tcl: Path
    shas: dict[str, str] = field(default_factory=dict)

    @property
    def probe_simv(self) -> Path:
        return self.probe_rundir / "simv"

    def check(self) -> None:
        missing = [
            str(p)
            for p in (self.simv, self.daidir, self.run_tcl, self.sff, self.tcl_template,
                      self.probe_simv, self.probe_run_tcl)
            if not p.exists()
        ]
        if missing:
            raise WorkspaceError(
                f"workspace '{self.name}' is incomplete, missing: {', '.join(missing)}"
            )


def _resolve(build: Path, value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else build / value


def ensure(camp, site, ws_root: Path, mode: str = "reference", force: bool = False) -> Workspace:
    """Create the workspace if needed and return it. Idempotent."""
    if mode not in ("reference", "snapshot"):
        raise WorkspaceError("workspace mode must be 'reference' or 'snapshot'")

    found = srcs.locate(site.build, camp)
    src_rundir, src_tcl, src_sff = found.rundir, found.tcl, found.sff
    if src_tcl is None or src_sff is None:
        raise WorkspaceError(
            f"workspace '{camp.workspace}': "
            + ", ".join(f"{k} not resolved ({v})" for k, v in found.found.items()
                        if "->" in v)
        )
    for p in (src_rundir, src_tcl, src_sff):
        if not p.exists():
            raise WorkspaceError(f"source for workspace '{camp.workspace}' not found: {p}")

    root = ws_root / camp.workspace
    root.mkdir(parents=True, exist_ok=True)

    if mode == "snapshot":
        rundir = root / "rundir"
        if force and rundir.exists():
            safe_rmtree(rundir, root)
        if not rundir.exists():
            # Into place only once complete: an interrupted copy is redone.
            tmp = rundir.with_name(rundir.name + ".tmp")
            safe_rmtree(tmp, root, ignore_errors=True)
            _hardlink_tree(src_rundir, tmp)
            tmp.replace(rundir)
        # run.tcl points at force_regs.ucli by absolute path: rewrite it for the
        # snapshot, replacing the file rather than editing the shared inode.
        run_tcl = rundir / "run.tcl"
        write_text(run_tcl, f"source {rundir / 'force_regs.ucli'}\nrun\nexit")
    else:
        rundir = src_rundir
        run_tcl = src_rundir / "run.tcl"
        if not run_tcl.exists():
            write_text(run_tcl, f"source {src_rundir / 'force_regs.ucli'}\nrun\nexit")

    # The probe's simulator is frozen the same way: the live sim rundir is
    # rebuilt by the flow, and a probe that changes halfway through a run
    # changes the cycle counts that size every campaign after it.
    if mode == "snapshot":
        probe_rundir = root / "probe"
        if force and probe_rundir.exists():
            safe_rmtree(probe_rundir, root)
        if not probe_rundir.exists():
            _snapshot_probe(found.probe, probe_rundir)
        probe_run_tcl = probe_rundir / "run.tcl"
        write_text(probe_run_tcl, f"source {probe_rundir / 'force_regs.ucli'}\nrun\nexit")
    else:
        probe_rundir = found.probe
        probe_run_tcl = found.probe / "run.tcl"
        if not probe_run_tcl.exists():
            write_text(probe_run_tcl, f"source {probe_rundir / 'force_regs.ucli'}\nrun\nexit")

    # Small inputs are copied, always: they are what a run is reproducible against.
    sff = root / "faults.sff"
    tcl = root / "fsim.tcl.in"
    _copy_new(src_sff, sff, force)
    _copy_new(src_tcl, tcl, force)
    strobe_src = found.strobe or src_tcl.parent / "strobe.sv"
    strobe = None
    if strobe_src.exists():
        strobe = root / "strobe.sv"
        _copy_new(strobe_src, strobe, force)

    ws = Workspace(
        name=camp.workspace,
        root=root,
        rundir=rundir,
        simv=rundir / "simv",
        daidir=rundir / "simv.daidir",
        run_tcl=run_tcl,
        sff=sff,
        tcl_template=tcl,
        strobe=strobe,
        zoix_campaign=camp.zoix_campaign,
        mode=mode,
        probe_rundir=probe_rundir,
        probe_run_tcl=probe_run_tcl,
    )
    ws.check()
    flags = ws.probe_rundir / PROBE_FLAGS
    ws.shas = {
        "simv": sha256_file(ws.simv),
        "faults.sff": sha256_file(ws.sff),
        "fsim.tcl.in": sha256_file(ws.tcl_template),
        "strobe.sv": sha256_file(strobe) if strobe else "",
        "probe.simv": sha256_file(ws.probe_simv),
        "probe.flags": sha256_file(flags) if flags.exists() else "",
    }
    write_json(
        root / "meta.json",
        {
            "name": ws.name,
            "mode": mode,
            "source_rundir": str(src_rundir),
            "rundir": str(rundir),
            "source_probe": str(found.probe),
            "probe_rundir": str(probe_rundir),
            "zoix_campaign": camp.zoix_campaign,
            "shas": ws.shas,
        },
    )
    return ws


# What a direct simv run needs from its rundir. The rest of a sim rundir is
# other runs' output (par-*, t2main-*, waveforms) and has no business here.
PROBE_FLAGS = "sim-output-full.json"
_PROBE_FILES = ("simv", "force_regs.ucli")


def _snapshot_probe(src: Path, dst: Path) -> None:
    if not (src / "simv").exists():
        raise WorkspaceError(f"probe simulator not found: {src / 'simv'}")
    tmp = dst.with_name(dst.name + ".tmp")
    safe_rmtree(tmp, dst.parent, ignore_errors=True)
    tmp.mkdir(parents=True)
    for name in _PROBE_FILES:
        if (src / name).exists():
            _link_or_copy(str(src / name), str(tmp / name))
    if (src / "simv.daidir").is_dir():
        _hardlink_tree(src / "simv.daidir", tmp / "simv.daidir")
    if (src / PROBE_FLAGS).exists():
        shutil.copy2(src / PROBE_FLAGS, tmp / PROBE_FLAGS)
    tmp.replace(dst)


def _hardlink_tree(src: Path, dst: Path) -> None:
    """cp -al: same content, no extra disk, immune to a later rebuild because a
    rebuild creates new files rather than editing the old ones."""
    shutil.copytree(src, dst, copy_function=_link_or_copy, symlinks=True, dirs_exist_ok=True)


def _link_or_copy(src: str, dst: str) -> None:
    try:
        import os

        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _copy_new(src: Path, dst: Path, force: bool) -> None:
    if force or not dst.exists():
        tmp = dst.with_name(dst.name + ".tmp")
        shutil.copy2(src, tmp)
        tmp.replace(dst)
