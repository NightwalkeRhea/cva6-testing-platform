"""The make command lines, built in one place.

Every command reuses the existing synthesis: HAMMER_D_DEPS and
HAMMER_DEPENDENCIES are passed empty, so make never decides on its own that
the netlist is stale. The CVA6 overlay travels in EXTRA_CONFS on every call,
so Hammer resolves the same inputs whichever command asked.
"""
from __future__ import annotations

from .paths import Paths


def design_args(p: Paths) -> list[str]:
    return [f"benchmark={p.conf.benchmark}", f"technology_name={p.conf.technology}"]


def build_args(p: Paths) -> list[str]:
    """Which synthesis. Untagged, make keeps its own defaults. A command-line
    EXTRA_SIM_SOURCES replaces the wrapper's '+=', so a frozen copy of the
    SRAM models takes the place of the live one, and the SRAM shells, when
    the netlist needs them, come after the models they instantiate."""
    out: list[str] = []
    if p.tag:
        out += [f"VLSI_OBJ_DIR={p.obj_name}", f"generated_src_name={p.gen_src_name}",
                f"output_dir={p.output_dir}"]
    if p.sram_shells.is_file():
        out.append(f"EXTRA_SIM_SOURCES={p.models_file} {p.sram_shells}")
    elif p.sim_models.is_file():
        out.append(f"EXTRA_SIM_SOURCES={p.sim_models}")
    return out


def common_args(p: Paths, test: str) -> list[str]:
    """Reuse the synthesis, and the same binary to build with and to load."""
    return [*design_args(p), f"BINARY={test}", f"LOADMEM={test}",
            "HAMMER_D_DEPS=", "HAMMER_DEPENDENCIES=", *build_args(p)]


def overlay_args(p: Paths, *extra: str) -> list[str]:
    confs = " ".join([p.conf.overlay, *extra])
    return [f"EXTRA_CONFS={confs}"]


def tools_hammer_args(p: Paths) -> str:
    """The tools named in the tool configuration today. The sim and fsim
    inputs derive from the synthesis output, which records the tool versions
    of the day the netlist was made: without this, an older netlist would
    point at tools that are no longer installed."""
    return f"-p {p.tools_conf}"


def initreg_args(compile_: str, run: str) -> list[str]:
    return [f"SIM_INITREG_ELAB={compile_}", f"SIM_INITREG_RUN={run or 'off'}"]


def comp_target(p: Paths, flavor: str) -> str:
    """'redo-*' reuses the sim-syn-input.json of an earlier build; after a
    clean that file is gone and make would die at once, so the first build
    regenerates it. Neither restarts synthesis."""
    suffix = "-debug" if flavor == "debug" else ""
    if (p.obj_dir / "sim-syn-input.json").is_file():
        return f"redo-sim-syn{suffix}"
    return f"sim-syn{suffix}"


def fsim_target(p: Paths) -> str:
    if (p.obj_dir / "fsim-syn-input.json").is_file():
        return "redo-fsim-syn"
    return "fsim-syn"


def sim_rtl_args(p: Paths) -> list[str]:
    """The simulation-only RTL of a synthesis that has none yet: the testbench
    driver, the harness memories and the filelist naming them, which 'make
    syn' stops short of. The generator jar is declared old, so the elaboration
    behind the netlist is reused as it is."""
    return ["-o", str(p.generator_jar), str(p.simfiles_common), *design_args(p),
            "HAMMER_D_DEPS=", "HAMMER_DEPENDENCIES=", *build_args(p)]


def keep_rtl(p: Paths) -> list[str]:
    """Declare the RTL filelist old, so make does not rerun sbt and the Chisel
    generator to rebuild it: the RTL is the input to synthesis, and
    regenerating it can only desynchronise it from the netlist."""
    return ["-o", str(p.simfiles_common)]
