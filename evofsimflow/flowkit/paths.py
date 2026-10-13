"""Where the flow's files are, for one checkout and one synthesis.

Several syntheses can sit side by side. A build tag (CY_BUILD, or --build)
selects one: its netlist, generated RTL, waveforms and reports all carry the
suffix -<tag>, so a new synthesis never overwrites an older one. Untagged, the
original directories are used.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from .config import TOOL_ROOT, FlowConf
from .ui import FlowError

_TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class NotFound(Exception):
    pass


def repo_root(explicit: str | None = None) -> Path:
    """The checkout this folder lives in, unless told otherwise.

    Resolved, because make names its targets after the physical directory and
    'make -o <file>' only protects a file whose name matches exactly.
    """
    given = explicit or os.environ.get("CY_REPO") or str(TOOL_ROOT.parent)
    return Path(given).expanduser().resolve()


def build_tag(explicit: str | None = None) -> str:
    tag = explicit if explicit is not None else os.environ.get("CY_BUILD", "")
    if tag and not _TAG_RE.match(tag):
        raise FlowError(f"build tag must be a plain name: '{tag}'")
    return tag


@dataclass(frozen=True)
class Paths:
    repo: Path
    tag: str
    conf: FlowConf

    @property
    def sfx(self) -> str:
        return f"-{self.tag}" if self.tag else ""

    @property
    def vlsi(self) -> Path:
        return self.repo / "vlsi"

    @property
    def tests(self) -> Path:
        return self.repo / "tests"

    @property
    def obj_name(self) -> str:
        return f"{self.conf.build_prefix}{self.sfx}"

    @property
    def gen_src_name(self) -> str:
        return f"{self.conf.gen_src_prefix}{self.sfx}"

    @property
    def build_root(self) -> Path:
        """The VLSI_OBJ_DIR: one per synthesis."""
        return self.vlsi / self.obj_name

    @property
    def obj_dir(self) -> Path:
        return self.build_root / f"{self.conf.long_name}-ChipTop"

    @property
    def rundir(self) -> Path:
        """The plain simv, its daidir and the UCLI scripts Hammer writes."""
        return self.obj_dir / "sim-syn-rundir"

    @property
    def fsim_rundir(self) -> Path:
        return self.obj_dir / "fsim-syn-rundir"

    @property
    def syn_rundir(self) -> Path:
        return self.obj_dir / "syn-rundir"

    @property
    def netlist(self) -> Path:
        return self.syn_rundir / "results" / "ChipTop.mapped.v"

    @property
    def output_dir(self) -> Path:
        """Waveforms."""
        return self.vlsi / "output" / f"{self.conf.long_name}{self.sfx}"

    @property
    def fsim_gen_dir(self) -> Path:
        """Generated yml, strobe and campaign script of each fsim."""
        return self.obj_dir / "fsim-gen"

    @property
    def dump_gen_dir(self) -> Path:
        return self.obj_dir / "dump-gen"

    @property
    def fsim_out_root(self) -> Path:
        return self.vlsi / "fsim-output" / self.conf.technology

    @property
    def fsim_out_leaf(self) -> str:
        return f"{self.conf.long_name}-ChipTop{self.sfx}"

    def fsim_out(self, model: str) -> Path:
        """Campaign reports, laid out as fsim.mk does: one directory per model."""
        return self.fsim_out_root / model / self.fsim_out_leaf

    @property
    def sim_models(self) -> Path:
        """SRAM behavioural models frozen next to a netlist. The CVA6 wrapper
        generates them inside its own source tree, so a newer elaboration
        rewrites them; a build that keeps its own copy is simulated with it."""
        return self.obj_dir / "sim-models" / "memories.sv"

    @property
    def live_sim_models(self) -> Path:
        return self.repo / "generators/cva6/src/main/resources/cva6/vsrc/memories.sv"

    @property
    def models_file(self) -> Path:
        """The SRAM models this build is simulated with."""
        return self.sim_models if self.sim_models.is_file() else self.live_sim_models

    @property
    def sram_shells(self) -> Path:
        """conf/cva6-sram-shells.sv, copied here before a build whose netlist
        has SRAM stubs no model has the name of."""
        return self.obj_dir / "sim-models" / "sram-shells.sv"

    @property
    def gen_src_dir(self) -> Path:
        return self.vlsi / self.gen_src_name / self.conf.long_name

    @property
    def simfiles_common(self) -> Path:
        """The RTL filelist of the Chisel front end. Declared old to make (-o)
        so a run does not regenerate the RTL; the name must be the one make
        uses, or the protection silently does nothing."""
        return self.gen_src_dir / "sim_files.common.f"

    @property
    def generator_jar(self) -> Path:
        return self.repo / ".classpath_cache" / "chipyard.jar"

    @property
    def stamp(self) -> Path:
        return self.rundir / ".flow-build.stamp"

    @property
    def strobe_template(self) -> Path:
        return self.vlsi / self.conf.strobe_template

    @property
    def fsim_tcl_template(self) -> Path:
        return self.vlsi / self.conf.fsim_tcl_template

    @property
    def tools_conf(self) -> Path:
        return self.vlsi / self.conf.tools_conf

    @property
    def dramsim_ini(self) -> Path:
        return self.repo / "generators/testchipip/src/main/resources/dramsim2_ini"

    @property
    def probe_dir(self) -> Path:
        return TOOL_ROOT / "probe"

    def test(self, name: str) -> str:
        """A test name: absolute as is, otherwise relative to tests/. Plain
        string concatenation, so the path reaches make exactly as written."""
        return name if name.startswith("/") else f"{self.tests}/{name}"


def find_build(repo: Path, pattern: str = "vlsi/build-*/*-ChipTop") -> Path:
    """The build directory of a Chipyard flow, when it was not written down.

    One match is an answer. Several are a question, and the answer to it is a
    line of configuration: picking one here would silently decide which
    technology and which configuration everything after is measured against.
    """
    hits = sorted(p for p in repo.glob(pattern) if p.is_dir())
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise NotFound(
            f"no build directory under {repo} matching '{pattern}'.\n"
            "Build the flow first, or name the build (paths.build_tag in conf/site.toml)."
        )
    listed = "\n".join(f"  {p.relative_to(repo)}" for p in hits)
    raise NotFound(
        f"{len(hits)} build directories under {repo}:\n{listed}\n"
        "Name the one you mean: paths.build_tag in conf/site.toml, or CY_BUILD."
    )
