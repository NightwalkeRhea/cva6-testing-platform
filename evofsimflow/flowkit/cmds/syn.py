"""syn: a new synthesis, side by side with the existing ones.

Always into a tagged build (--build TAG or CY_BUILD), so a new netlist never
overwrites one that results were measured on.

One 'make syn', with the generator jar declared old (-o). Without it make
loops: the jar depends on every Verilog file under generators/, and the CVA6
wrapper rewrites some of those while elaborating, after the jar was
assembled; the jar then looks stale, the build file is remade and make
restarts from the top, forever. A missing jar is built first, on its own.

When the synthesis succeeds, the SRAM behavioural models it was elaborated
with are frozen next to the netlist (sim-models/memories.sv): the wrapper
generates them inside its own source tree, where the next elaboration of any
build rewrites them.
"""
from __future__ import annotations

import filecmp
import shlex

from .. import make
from ..ui import FlowError, say, sub, warn
from . import Flow, int_min

HELP = "synthesise a new netlist into a tagged build"
# DC rejects a larger -max_cores (CMD-019, "not in range (1 to 16)") and then
# carries on with its default of one core, hours slower, without stopping.
MAX_CORES = 16


def add(sub_parsers) -> None:
    import argparse
    p = sub_parsers.add_parser("syn", help=HELP, description=__doc__.split("\n\n", 1)[1],
                               formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cores", type=int_min(1),
                   help=f"cores for the synthesis tool, at most {MAX_CORES} "
                        "(default: the tool configuration's)")
    p.add_argument("--freeze-only", action="store_true",
                   help="only freeze the SRAM models of a finished synthesis")
    p.add_argument("-n", "--dry-run", action="store_true", help="print what would run, run nothing")
    p.set_defaults(func=run)


def run(flow: Flow, args) -> int:
    p = flow.paths
    if not p.tag:
        raise FlowError("syn needs a build tag: flow.py --build <tag> syn",
                        "A synthesis always goes to a build of its own.")
    netlist = p.netlist
    if args.freeze_only:
        freeze_models(flow)
        return 0
    if netlist.exists():
        raise FlowError(f"build '{p.tag}' already has a netlist: {netlist}",
                        "Pick a new tag; results measured on this one stay comparable.")

    if args.cores and args.cores > MAX_CORES:
        raise FlowError(f"--cores {args.cores}: the synthesis tool takes at most {MAX_CORES}.",
                        "Asked for more, it would run on one core instead.")
    extra: list[str] = []
    cores_yml = p.build_root / "flow-cores.yml"
    if args.cores:
        extra.append(str(cores_yml))
    jar_argv = ["make", "-C", str(p.repo / "sims" / "vcs"), str(p.generator_jar)]
    syn_argv = ["-o", str(p.generator_jar), "syn", *make.design_args(p),
                f"VLSI_OBJ_DIR={p.obj_name}", f"generated_src_name={p.gen_src_name}",
                *make.overlay_args(p, *extra)]

    say(f"SYN  build '{p.tag}'  ->  {p.obj_dir}")
    sub(f"cores     : {args.cores or 'from the tool configuration'}")
    sub(f"overlay   : {p.conf.overlay}")
    if not p.generator_jar.is_file():
        sub(f"jar       : missing, built first ({p.generator_jar})")
    if args.dry_run:
        if args.cores:
            print(f"----- {cores_yml} -----\nvlsi.core.max_threads: {args.cores}")
        if not p.generator_jar.is_file():
            print(shlex.join(jar_argv))
        print(f"(cd {p.vlsi} && {shlex.join(['make', *syn_argv])})")
        say("DRY-RUN: nothing launched.")
        return 0

    if args.cores:
        p.build_root.mkdir(parents=True, exist_ok=True)
        cores_yml.write_text(
            "# Written by flow.py syn: cores for this synthesis only.\n"
            f"vlsi.core.max_threads: {args.cores}\n")
    if not p.generator_jar.is_file():
        res = flow.ex.run(jar_argv, cwd=p.repo, show=True)
        if res.rc != 0 or not p.generator_jar.is_file():
            raise FlowError("the generator jar was not built; see the output above.")
    flow.make(syn_argv)
    if not netlist.exists():
        raise FlowError(f"make finished but there is no netlist: {netlist}")
    freeze_models(flow)
    say(f"Netlist: {netlist}")
    sub(f"Simulate it with:  flow.py --build {p.tag} comp")
    return 0


def freeze_models(flow: Flow) -> None:
    p = flow.paths
    live, frozen = p.live_sim_models, p.sim_models
    script = p.syn_rundir / "scripts" / "dc.tcl"
    if not p.netlist.exists():
        raise FlowError(f"no finished synthesis in {p.syn_rundir}")
    if not live.is_file():
        raise FlowError(f"SRAM models not found: {live}")
    if frozen.is_file():
        if filecmp.cmp(live, frozen, shallow=False):
            sub(f"sram models : already frozen, identical ({frozen})")
        else:
            warn(f"{frozen} exists and differs from {live}: left as it is.")
        return
    # Rewritten after the synthesis started: by another elaboration, so not
    # what this netlist was made from.
    if script.is_file() and live.stat().st_mtime > script.stat().st_mtime:
        raise FlowError(f"{live} changed after this synthesis started.",
                        "Not freezing it: it may come from another elaboration.",
                        f"Copy the right one to {frozen} by hand.")
    frozen.parent.mkdir(parents=True, exist_ok=True)
    tmp = frozen.with_name(frozen.name + ".tmp")
    tmp.write_bytes(live.read_bytes())
    tmp.replace(frozen)
    say(f"sram models frozen: {frozen}")
