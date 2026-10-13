"""conf: where things stand. The resolved configuration and the build state,
in a few seconds; clean: throw away the sim/fsim builds and start clean."""
from __future__ import annotations

import os

from .. import hier
from ..shell import safe_rmtree
from ..ui import blank, say, sub
from . import Flow
from .comp import stamp_read


def add(sub_parsers) -> None:
    p = sub_parsers.add_parser("conf", help="show the resolved configuration and build state")
    p.set_defaults(func=run_conf)
    p = sub_parsers.add_parser(
        "clean", help="throw away the sim/fsim builds",
        description="Throw away the sim and/or fsim builds of the selected synthesis. Never "
                    "touches the synthesis, the netlist, the technology cache or the "
                    "generated RTL.")
    p.add_argument("what", nargs="?", choices=("sim", "fsim", "all"), default="all")
    p.set_defaults(func=run_clean)


def run_conf(flow: Flow, args) -> int:
    c, p = flow.conf, flow.paths
    dut_full = hier.join(c.strobe_root, hier.resolve(c.dut, c.aliases))
    fault_rel = hier.resolve(c.faults or c.dut, c.aliases)
    say(f"CONFIGURATION  ({c.file})")
    sub(f"binary      : {c.binary}")
    sub(f"              {p.test(c.binary)}")
    blank()
    sub(f"DUT         : {c.dut}")
    sub(f"              {dut_full}")
    sub(f"faults      : {c.faults or c.dut + ' (= DUT)'}")
    sub(f"              {hier.join(c.fault_root, fault_rel)}.**")
    blank()
    sub(f"strobe      : scope={c.strobe_scope}")
    if c.strobe_scope == "module":
        sub(f"              $fs_strobe({dut_full})   -> every port of the module")
    else:
        for port in c.strobe_ports:
            sub(f"              $fs_strobe({dut_full}.{port})")
    sub(f"inject at   : {c.inject_at} (t2main_cycles={c.t2main_cycles})")
    sub(f"model       : {c.fault_model}")
    blank()
    from .par import dump_describe
    sub(f"dump        : {dump_describe(flow)}   ('par -w' only)")
    sub(f"initreg     : compile={c.initreg_compile}  runtime={c.initreg_run}")
    sub(f"force_regs  : {'on' if c.force_regs else 'off'}")
    blank()
    sub(f"cycle caps  : par={c.maxcyc_par}  fsim={c.maxcyc_fsim}  probe={c.maxcyc_probe}")
    sub(f"jobs        : {c.jobs}")
    blank()
    say("BUILD STATE")
    st_flavor, st_initreg = stamp_read(flow)
    if os.access(p.rundir / "simv", os.X_OK):
        sub(f"simv        : {p.rundir / 'simv'}")
        if st_flavor:
            sub(f"flavor      : {st_flavor}")
            if st_initreg == c.initreg_compile:
                sub(f"initreg cmp : {st_initreg} — matches the configuration")
            else:
                sub(f"initreg cmp : {st_initreg or '?'} but the configuration says "
                    f"{c.initreg_compile}")
                sub("              -> the next command rebuilds")
        else:
            sub("flavor      : unknown (no stamp) -> the next command rebuilds")
    else:
        sub("simv        : not built -> the next command builds it")
    sub(f"repo        : {p.repo}")
    sub(f"build       : {p.tag or '(untagged)'}  {p.obj_dir}")
    if p.sim_models.is_file():
        sub(f"sram models : {p.sim_models} (frozen with this build)")
    if p.sram_shells.is_file():
        sub(f"sram shells : {p.sram_shells} (from the last build)")
    return 0


def run_clean(flow: Flow, args) -> int:
    phases = ("sim", "fsim") if args.what == "all" else (args.what,)
    for phase in phases:
        _clean(flow, phase)
    say("Done. Synthesis and generated RTL untouched.")
    return 0


def _clean(flow: Flow, phase: str) -> None:
    """Never touched: syn-rundir (the netlist: redoing it takes hours and
    makes coverage numbers incomparable), the technology cache, the generated
    RTL (the input to synthesis), and hammer.d (make regenerates it anyway)."""
    p = flow.paths
    obj = p.obj_dir
    say(f"CLEAN {phase}")
    if phase == "sim":
        sub(f"rm -rf {p.rundir}")
        safe_rmtree(p.rundir, obj)
        names = ("sim-inputs.yml", "sim-debug-inputs.yml", "sim-timing-inputs.yml",
                 "sim-syn-input.json")
        sub(f"rm -f  {obj}/{{{','.join(names)}}}")
        for n in names:
            safe_rmtree(obj / n, obj)
        sub(f"rm -rf {p.dump_gen_dir}  {p.output_dir}/*.fsdb")
        safe_rmtree(p.dump_gen_dir, obj)
        if p.output_dir.is_dir():
            for f in p.output_dir.glob("*.fsdb"):
                safe_rmtree(f, p.output_dir)
    else:
        sub(f"rm -rf {p.fsim_rundir}")
        safe_rmtree(p.fsim_rundir, obj)
        sub(f"rm -f  {obj}/{{fsim-inputs.yml,fsim-syn-input.json}}")
        for n in ("fsim-inputs.yml", "fsim-syn-input.json"):
            safe_rmtree(obj / n, obj)
        sub(f"rm -rf {p.fsim_gen_dir}  {p.fsim_out_root}/*/{p.fsim_out_leaf}")
        safe_rmtree(p.fsim_gen_dir, obj)
        if p.fsim_out_root.is_dir():
            for d in p.fsim_out_root.glob(f"*/{p.fsim_out_leaf}"):
                safe_rmtree(d, p.fsim_out_root)
