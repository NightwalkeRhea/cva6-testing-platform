"""t2main: when is main entered? The injection point of 'fsim --inject-at main'.

Two probes, identical apart from the crt0. Each writes tohost as the very
first thing it does, so the simulation ends right there:

  probe_start  ->  T0 = t_start + eps   (no crt0: first instruction straight
                                         after the boot ROM)
  probe_main   ->  T1 = t_main  + eps   (full crt0, stops on entering main)

eps is the latency with which the host sees the tohost write: unknown, but the
same in both, so it cancels. Since t_start >= the reset delay,

  eps <= T0 - RESET_DELAY   =>   t_main >= T1 - T0 + RESET_DELAY

a true lower bound on when main is entered, which is the number to floor.
Only worth redoing when the boot changes (netlist, cache configuration, crt0),
never when the program does.
"""
from __future__ import annotations

import os
import sys

from .. import progs, simv
from ..ui import FlowError, blank, say, sub
from . import Flow, int_min, stamped_dir
from . import comp
from .par import add_build_options, apply_build_options

HELP = "measure the cycle at which main is entered"


def add(sub_parsers) -> None:
    p = sub_parsers.add_parser("t2main", help=HELP, description=__doc__.split("\n\n", 1)[1])
    p.add_argument("--no-probe-build", action="store_true",
                   help="use the probes already built in probe/")
    p.add_argument("--max-cycles", type=int_min(1), help="cap for the probes (default cycles.probe)")
    add_build_options(p)
    p.set_defaults(func=run)


def build_probes(flow: Flow) -> None:
    probe_dir = flow.paths.probe_dir
    cc = os.environ.get("RISCV_CC", "riscv64-unknown-elf-gcc")
    objdump = os.environ.get("RISCV_OBJDUMP", "riscv64-unknown-elf-objdump")
    if not flow.ex.which(cc):
        raise FlowError(f"{cc} is not in PATH.", "Load the RISC-V toolchain environment first.")
    for argv in progs.probe_steps(cc, flow.paths.tests / "htif.ld"):
        res = flow.ex.run(argv, cwd=probe_dir, show=True)
        if res.rc != 0:
            raise FlowError(f"probe build failed: {' '.join(argv[-3:])}")
    say("probes ready:")
    for name in progs.PROBES:
        binary = f"{name}.riscv"
        flow.ex.run([objdump, "-D", binary], cwd=probe_dir, log=probe_dir / f"{name}.dump")
        syms = probe_dir / f".{name}.syms"
        flow.ex.run([objdump, "-t", binary], cwd=probe_dir, log=syms)
        addr = _symbols(syms)
        syms.unlink(missing_ok=True)
        print(f"    {name + '.riscv':<20} _start={addr.get('_start', '')}  "
              f"tohost={addr.get('tohost', '')}")


def _symbols(path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for line in path.read_text(errors="replace").splitlines():
            fields = line.split()
            if fields and fields[-1] in ("_start", "tohost"):
                out.setdefault(fields[-1], fields[0])
    except OSError:
        pass
    return out


def run(flow: Flow, args) -> int:
    c, p = flow.conf, flow.paths
    apply_build_options(flow, args)
    maxcyc = args.max_cycles or c.maxcyc_probe

    comp.ensure(flow, "any")
    comp.require_built(flow)
    script = comp.run_script(flow)
    if not args.no_probe_build:
        build_probes(flow)

    flags = simv.load_flags(p.rundir, p.dramsim_ini)
    if not flags.from_flow:
        from ..ui import warn
        warn("sim-output-full.json is missing: falling back to template flags.")
    # Without a cap of its own, a probe that never ends would hang forever.
    flags = flags.with_max_cycles(maxcyc).with_initreg(c.initreg_run)

    t2dir = stamped_dir(p.rundir, "t2main")
    t2dir.mkdir(parents=True)
    say(f"T2MAIN: workdir {t2dir}  (max-cycles {maxcyc})")

    times: dict[str, str] = {}
    for name in ("probe_start", "probe_main"):
        binary = p.probe_dir / f"{name}.riscv"
        if not binary.is_file():
            raise FlowError(f"probe missing: {binary}", "Re-run without --no-probe-build.")
        print(f">>>   run {name} ...", file=sys.stderr, flush=True)
        res = simv.execute(flow.ex, simv.command(p.rundir / "simv", flags, str(binary),
                                                 None, script), t2dir / name)
        # Only a pass is a measurement: a timed-out probe still has a $finish
        # and a "Time:" line, and a failed one ends wherever it trapped.
        ok = res.verdict.kind == "PASS" and not res.verdict.timed_out
        times[name] = res.verdict.end_text if ok else ""

    for name in ("probe_start", "probe_main"):
        if times[name]:
            continue
        log = t2dir / name / "sim.log"
        if "(timeout) after" in log.read_text(errors="replace"):
            print(f"ERROR: {name} TIMED OUT (cap {maxcyc} cycles).\n"
                  "       The probe writes tohost as its first instruction: if it never\n"
                  "       gets there, the core is not executing at all — the cap is not\n"
                  "       too low.", file=sys.stderr)
        else:
            print(f"ERROR: {name} did not pass.", file=sys.stderr)
        print("Log tail:", file=sys.stderr)
        print("\n".join(log.read_text(errors="replace").splitlines()[-25:]), file=sys.stderr)
        return 2

    t0, t1 = float(times["probe_start"]), float(times["probe_main"])
    rst, per = c.reset_delay_ns, c.clock_ns
    crt0 = t1 - t0                    # eps cancels in the difference
    epsmax = t0 - rst                 # if the boot ROM were free
    lb = (crt0 + rst) / per           # lower bound on t_main, in cycles
    sugg = max(0, int(lb / 1000) * 1000)

    blank()
    say("MEASUREMENT (absolute cycles from t=0)")
    print(f"      T0  probe_start  = {times['probe_start']:>10} ns   (reset + bootrom + handshake + eps)")
    print(f"      T1  probe_main   = {times['probe_main']:>10} ns   (T0 + crt0 + eps)")
    print(f"      cost of the crt0 = {crt0 / per:>10.1f}      (T1 - T0: eps cancels)")
    print(f"      eps <=             {epsmax / per:>10.1f}      (T0 - RESET_DELAY)")
    print(f"      t_main >=          {lb:>10.1f}      (crt0 + RESET_DELAY {rst:g})")
    blank()
    say(f"SUGGESTED:  timing.t2main_cycles = {sugg}")
    sub("The lower bound floored to the thousand. No further margin is needed: the")
    sub("bound already assumes the worst case (boot ROM and handshake for free, all")
    sub(f"of the host wait charged to eps), so the real t_main lies between {lb:.1f}")
    sub(f"and {times['probe_main']}.")
    sub("Put it in conf/flow.toml ([timing]); 'flow.py fsim' will inject there.")
    sub(f"Quick check:  flow.py fsim -n --inject-at {sugg}")
    return 0
