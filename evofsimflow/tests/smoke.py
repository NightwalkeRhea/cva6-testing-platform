#!/usr/bin/env python3
"""The evolutionary loop against the real tools, as small as it goes.

Two individuals per generation, three generations, fixed seed. The point is
not the search: it is that uGP3, the socket, the two phases, vc_fcc/vc_fcm,
the validation, the journal and the artifacts all hold together on the real
flow. Six individuals are enough to see every one of them.

Everything it writes goes under the runs root, in a run of its own labelled
alu-smoke, so it is never mistaken for a real one and can be deleted whole.
The whole output is also kept in <runs>/_log/.

    tests/smoke.py [--runs DIR]
"""
from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL = HERE.parent


def tee(argv: list[str], log) -> int:
    """Run, showing the output and keeping it."""
    proc = subprocess.Popen(argv, cwd=TOOL, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for line in proc.stdout:
        sys.stdout.buffer.write(line)
        sys.stdout.flush()
        log.write(line)
        log.flush()
    return proc.wait()


def say(text: str, log) -> None:
    print(text, flush=True)
    log.write((text + "\n").encode())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--runs", type=Path, default=Path(os.environ.get(
        "EVOSBST_RUNS", TOOL.parent.parent / "evosbst-runs")))
    runs = ap.parse_args().runs.resolve()
    (runs / "_log").mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    # 10 cores in 2 slots of 5. The ALU campaign splits into 10 tasks, so 5
    # workers run two each and finish when 8 would, two of which run two anyway.
    # Below that task count each individual costs the work it would on one
    # core and only finishes sooner: minutes instead of hours.
    common = ["--set", f"paths.runs={runs}", "--set", "cores.total=10",
              "--set", "cores.per_ind=5"]
    evosbst = [sys.executable, str(TOOL / "evosbst.py")]
    camp = "conf/campaigns/alu.toml"

    with open(runs / "_log" / f"evosbst_smoke_{stamp}.log", "ab") as log:
        say(f"### {time.strftime('%F %T')}  host {platform.node()}  "
            f"python {platform.python_version()}", log)
        say(f"### cores: {os.cpu_count()} total, load "
            f"{' '.join(f'{x:.2f}' for x in os.getloadavg())}", log)
        say("\n=== 1/3  selftest (no EDA tool touched) ===", log)
        if tee([*evosbst, "selftest"], log):
            return 1
        say("\n=== 2/3  setup: freeze the inputs, snapshot the workspace ===", log)
        if tee([*evosbst, "setup", camp, *common], log):
            return 1
        say("\n=== 3/3  run: lambda=2, 3 generations, seed 42 ===", log)
        t0 = time.monotonic()
        rc = tee([*evosbst, "run", camp, "--seed", "42", "--label", "alu-smoke", *common,
                  "--set", "ga.lambda=2", "--set", "ga.mu=2", "--set", "ga.nu=2",
                  "--set", "ga.generations=3", "--set", "ga.max_age=100"], log)
        say(f"### run finished rc={rc} in {time.monotonic() - t0:.0f}s", log)
        say("\n=== what it left behind ===", log)
        tee([*evosbst, "status", *common], log)
    return rc


if __name__ == "__main__":
    sys.exit(main())
