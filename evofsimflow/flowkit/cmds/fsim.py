"""fsim: one VC-Z01X fault campaign through Hammer.

Generates the strobe, the campaign yml and the campaign script from the shared
templates, then runs the flow's fsim target with the copies. This command does
not use the plain simv: the fsim flow elaborates its own, fault-aware one with
the strobe compiled in.
"""
from __future__ import annotations

import difflib
import os
import re
import shlex
import sys

from .. import fsimgen, hier, make, srams
from ..config import INITREG_COMPILE, INITREG_RUN, STROBE_SCOPES, TASK_SPLITS, check_inject
from ..shell import safe_rmtree
from ..ui import FlowError, blank, say, sub, warn
from . import Flow, int_min
from . import comp

HELP = "run a VC-Z01X fault campaign"
EPILOG = """\
--inject-at: main = just before entering main (skips the boot, which is the
same for every program and so measures nothing); reset = at reset release;
<N> = an absolute cycle.

--strobe-scope: module = $fs_strobe(<module>), every port of it, the
observability ceiling; ports = $fs_strobe(<module>.<port>) for each port in
--strobe-ports, a realistic signature. The coverage difference between the two
is the observability given up.

--task-split: on = two tasks per worker, sized from the campaign at run time.
With the strobe away from the faulted module most faults run the whole
program, task lengths spread, and one task per worker leaves workers idle
behind the longest. auto = on exactly in that case.

--steps stops the flow early: "--to_step run_vcs" rebuilds the fault-aware
simv and runs no campaign. Only --to_step and --until_step are accepted: the
plugin sets itself up in its first steps, so --only_step, --from_step and
--after_step stop at once. Options acting in a step that is not reached do
nothing.

TARGET for --faults/--strobe: an alias from [hierarchy.aliases], or a path
relative to the CVA6 core, or a full path from ChipTop. or TestDriver."""

_STEP_RE = re.compile(r"^--(to|until)_step [a-z_]+$")


def add(sub_parsers) -> None:
    p = sub_parsers.add_parser("fsim", help=HELP, description=__doc__.split("\n\n", 1)[1],
                               epilog=EPILOG, formatter_class=_raw())
    p.add_argument("test", nargs="?", help="binary under test (default study.binary)")
    p.add_argument("-t", "--test", dest="test_opt", metavar="TEST", help="same as the positional")
    p.add_argument("--faults", metavar="TARGET", help="where to inject (default study.faults or dut)")
    p.add_argument("--strobe", metavar="T[,T..]", help="where to observe (default study.dut)")
    p.add_argument("--strobe-scope", choices=STROBE_SCOPES)
    p.add_argument("--strobe-ports", metavar="P[,P..]", help="ports for --strobe-scope ports")
    p.add_argument("--model", help="fault model (default fsim.model)")
    p.add_argument("--max-cycles", type=int_min(1),
                   help="cap for every faulty simulation (default cycles.fsim; 3-4x the fault-free run)")
    p.add_argument("--inject-at", metavar="main|reset|N")
    p.add_argument("--initreg", metavar="C[,R]", help="compile (random|off)[,runtime (0|1|random)]")
    p.add_argument("--task-split", choices=TASK_SPLITS)
    p.add_argument("--no-clean", action="store_true",
                   help="keep fsim-syn-rundir's simv and daidir before rebuilding")
    p.add_argument("--steps", metavar='"--to_step S"',
                   help='stop the flow early, e.g. "--to_step run_vcs"')
    p.add_argument("-n", "--dry-run", action="store_true", help="print what would run, run nothing")
    p.set_defaults(func=run)


def _raw():
    import argparse
    return argparse.RawDescriptionHelpFormatter


def _risky(args) -> list[str]:
    names = [("test", "-t"), ("faults", "--faults"), ("strobe", "--strobe"),
             ("strobe_scope", "--strobe-scope"), ("strobe_ports", "--strobe-ports"),
             ("inject_at", "--inject-at"), ("initreg", "--initreg"), ("task_split", "--task-split")]
    risky = {"--strobe", "--strobe-scope", "--strobe-ports", "--inject-at", "--initreg",
             "--faults", "--task-split"}
    return [opt for attr, opt in names if getattr(args, attr, None) and opt in risky]


def run(flow: Flow, args) -> int:
    c, p = flow.conf, flow.paths
    if args.test and args.test_opt:
        raise FlowError(f"'fsim' takes one test, but got '{args.test_opt}' and '{args.test}'.",
                        "Parallelism inside a campaign is Z01X's, not this tool's.")
    test = args.test or args.test_opt
    faults = args.faults or c.faults or c.dut
    strobe = args.strobe or c.dut
    model = args.model or c.fault_model
    scope = args.strobe_scope or c.strobe_scope
    split_opt = args.task_split or c.task_split
    maxcyc = args.max_cycles or c.maxcyc_fsim
    inject = check_inject(args.inject_at or c.inject_at)
    steps = args.steps or ""
    ireg_c, ireg_r = c.initreg_compile, c.initreg_run
    if args.initreg:
        ireg_c, _, rest = args.initreg.partition(",")
        if "," in args.initreg:
            ireg_r = rest

    if model not in c.fault_models:
        raise FlowError(f"--model wants one of: {' '.join(c.fault_models)}  (given: '{model}')")
    if ireg_c not in INITREG_COMPILE:
        raise FlowError(f"--initreg (compile) wants one of: {' '.join(INITREG_COMPILE)}"
                        f"  (given: '{ireg_c}')")
    if ireg_r not in INITREG_RUN:
        raise FlowError(f"--initreg (runtime) wants one of: {' '.join(INITREG_RUN)}"
                        f"  (given: '{ireg_r}')")
    if steps and not _STEP_RE.match(steps):
        raise FlowError(f"--steps wants --to_step or --until_step: '{steps}'",
                        'e.g. "--to_step run_vcs". The plugin sets itself up in its first steps.')
    inject_cycle = _inject_cycle(flow, inject)
    fsim_out = p.fsim_out(model)

    test = hier.check_test_path(p.test(test or c.binary))
    if not os.path.isfile(test):
        raise FlowError(f"test not found: {test}")
    if not p.strobe_template.is_file():
        raise FlowError(f"strobe template missing: {p.strobe_template}")

    if args.strobe_ports:
        ports = args.strobe_ports.split(",")
    else:
        ports = list(c.strobe_ports)
    ports = [hier.check_hier(x, "--strobe-ports") for x in ports if x]
    if scope == "ports" and not ports:
        raise FlowError("--strobe-scope ports but no ports given.",
                        "Pass --strobe-ports a,b or fill study.strobe_ports in conf/flow.toml.")

    fault_path = hier.check_hier(hier.join(c.fault_root, hier.resolve(faults, c.aliases)),
                                 "--faults")
    strobe_bases: list[str] = []
    strobe_paths: list[str] = []
    for s in strobe.split(","):
        if not s:
            continue
        base = hier.check_hier(hier.join(c.strobe_root, hier.resolve(s, c.aliases)), "--strobe")
        strobe_bases.append(base)
        strobe_paths += [base] if scope == "module" else [f"{base}.{x}" for x in ports]
    if not strobe_paths:
        raise FlowError("--strobe is empty.")

    gen = p.fsim_gen_dir
    gen.mkdir(parents=True, exist_ok=True)
    tag_s = f"{hier.slug(strobe)}_{scope}"
    gen_yml = gen / f"fsim-{model}-f_{hier.slug(faults)}-s_{tag_s}.yml"
    gen_strobe = gen / f"strobe-{tag_s}-i{inject_cycle}.sv"
    # The compile-time initreg comes from fsim.mk (SIM_INITREG_ELAB), so only
    # +vcs+fsdbon is left here.
    elab = '"+vcs+fsdbon"'
    # The campaign is named after the leaf of the campaign DUT, the chip top;
    # the plugin names the test after the binary.
    campaign = c.tb_top.rsplit(".", 1)[-1]
    testname = os.path.splitext(os.path.basename(test))[0]
    if split_opt == "on":
        split, why = True, "forced with --task-split on"
    elif split_opt == "off":
        split, why = False, "forced with --task-split off"
    elif fsimgen.strobe_far(fault_path, strobe_bases, c.tb_top):
        split, why = True, "strobe away from the faulted module"
    else:
        split, why = False, "strobe on the faulted module"
    gen_tcl = gen / f"fsim-tasks_{'split' if split else 'default'}.tcl"

    gen_strobe.write_bytes(fsimgen.strobe_text(p.strobe_template.read_bytes(),
                                               inject_cycle, strobe_paths))
    gen_yml.write_text(fsimgen.yml_text(elab, fault_path, strobe_paths))
    template = p.fsim_tcl_template.read_text()
    gen_tcl.write_text(fsimgen.tcl_text(template, split, campaign))

    say(f"FSIM ({model})  TEST={os.path.basename(test)}")
    sub(f"faults in : {fault_path}.**")
    sub(f"strobe    : scope={scope}, {len(strobe_paths)} point(s)")
    for i, s in enumerate(strobe_paths, 1):
        print(f"        {i:2d}) {s}")
    if int(inject_cycle) <= 0:
        sub("inject at : reset release (boot included in the campaign)")
    elif inject == "main":
        sub(f"inject at : cycle {inject_cycle}  (just before main; boot excluded)")
    else:
        sub(f"inject at : cycle {inject_cycle}  (chosen by hand)")
    sub(f"initreg   : compile={ireg_c}  runtime={ireg_r}")
    sub(f"max-cycles: {maxcyc}  (rule of thumb: 3-4x the fault-free run)")
    if split:
        sub(f"tasks     : two per worker, sized at run time ({why})")
    else:
        sub(f"tasks     : one per worker, the Z01X default ({why})")
    sub(f"yml       : {gen_yml}")
    sub(f"strobe.sv : {gen_strobe}")
    sub(f"fsim.tcl  : {gen_tcl}  (serial pass with -no_coats)")
    sub(f"output    : {fsim_out}  (look for fsim_out_summary.rpt)")

    target = make.fsim_target(p)
    if target == "fsim-syn":
        sub("(first fsim: using 'fsim-syn', fsim-syn-input.json is missing)")
    risky = _risky(args)
    if steps and risky:
        warn(f"--steps '{steps}' skips steps of the flow.")
        print("""\
            These options act in steps you may have skipped; in that case they
            are no-ops and the campaign reuses what was already generated:
              --strobe* / --inject-at  -> generate_files + run_vcs
                                          (the strobe is compiled into the simv)
              --initreg                -> run_vcs (elaboration flag)
              --faults                 -> generate_tcl
              --task-split             -> generate_files (copies the script)""",
              file=sys.stderr)
        print(f"            Passed now: {' '.join(risky)}", file=sys.stderr)

    srams.prepare(p)
    argv = [*make.keep_rtl(p), target, *make.design_args(p),
            f"BINARY={test}", f"LOADMEM={test}", f"FAULT_MODEL={model}",
            f"TIMEOUT_CYCLES={maxcyc}", f"FSIM_CONF_FILE={gen_yml}",
            f"FSIM_STROBE_FILE={gen_strobe}", f"FSIM_CAMPAIGN_TCL={gen_tcl}",
            f"FSIM_OUTPUT_FOLDER={fsim_out}", "HAMMER_D_DEPS=", "HAMMER_DEPENDENCIES=",
            *make.build_args(p), f"SIM_INITREG_ELAB={ireg_c}", f"SIM_INITREG_RUN={ireg_r}",
            f"HAMMER_EXTRA_ARGS={make.tools_hammer_args(p)}{' ' + steps if steps else ''}",
            *make.overlay_args(p)]

    if args.dry_run:
        blank()
        print(f"----- {gen_yml} -----")
        print(gen_yml.read_text(), end="")
        blank()
        print(f"----- {gen_strobe} (head) -----")
        print("\n".join(gen_strobe.read_text(errors="replace").splitlines()[:12]))
        blank()
        print(f"----- {gen_tcl} (diff from the template) -----")
        sys.stdout.writelines(difflib.unified_diff(
            template.splitlines(True), gen_tcl.read_text().splitlines(True),
            str(p.fsim_tcl_template), str(gen_tcl)))
        blank()
        print("----- make command -----")
        print(shlex.join(["make", *argv]))
        blank()
        say("DRY-RUN: nothing launched.")
        return 0

    # A rebuild of the fault-aware simv starts from a clean slate.
    if not args.no_clean:
        say("cleaning fsim-syn-rundir (csrc, simv.daidir, simv) before rebuilding")
        comp.clean_artifacts(p.fsim_rundir)
    say(f"removing the toggle-simulation FSDB of '{testname}': the fault-free run is redone")
    _clean_tsim(flow, campaign, testname)
    comp.complete_sim_rtl(flow)
    flow.make(argv)

    if not comp.smoke_ok(flow, p.fsim_rundir, "simv fault-aware"):
        warn("the fault-aware simv does NOT execute the program: the coverage")
        print("            just computed is meaningless. Re-run this same\n"
              "            command: the build is not reproducible and the next\n"
              "            attempt may produce a healthy simv.", file=sys.stderr)
    say(f"Report in {fsim_out} (fsim_out_summary.rpt)")
    return 0


def _inject_cycle(flow: Flow, spec: str) -> str:
    if spec == "main":
        if flow.conf.t2main_cycles <= 0:
            raise FlowError("--inject-at main but timing.t2main_cycles is not calibrated.",
                            "Run 'flow.py t2main' and put the value in conf/flow.toml,",
                            "or use '--inject-at reset'.")
        return str(flow.conf.t2main_cycles)
    if spec == "reset":
        return "0"
    return str(int(spec))


def _clean_tsim(flow: Flow, campaign: str, testname: str) -> None:
    """Z01X validates the toggle-simulation FSDB against the testcase command
    line only, so a rebuilt simv, or a new binary at the same path, would reuse
    a stale one and coats would classify faults on the wrong activity. Every
    campaign starts from a fresh fault-free run."""
    d = flow.paths.fsim_rundir / "fcm_tsim_fsdb" / campaign
    if not d.is_dir():
        return
    for f in d.iterdir():
        if f.is_dir() and not f.is_symlink():
            continue
        if f.name.startswith(f"{testname}.") or f.name == f"{testname}_filter_messages.txt":
            safe_rmtree(f, d)
