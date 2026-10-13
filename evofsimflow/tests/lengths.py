#!/usr/bin/env python3
"""What length distribution does the genome actually draw?

One generation, built and no more: no probe, no campaign, no EDA licence. It
answers the one question a population of two cannot -- whether generation 0
explores the length axis -- in about a minute, and it is the measurement to
repeat after any change to the length of the body in the genome.

    tests/lengths.py [LAMBDA] [--runs DIR]
"""
from __future__ import annotations

import argparse
import csv
import os
import statistics
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL = HERE.parent


def histogram(journal: Path) -> int:
    with open(journal, newline="") as fh:
        rows = list(csv.DictReader(fh))
    v = sorted(int(r["insns"]) for r in rows
               if (r.get("insns") or "0").isdigit() and int(r["insns"]) > 0)
    if not v:
        print("no individuals were built")
        return 1
    print(f"n={len(v)}  min={v[0]}  max={v[-1]}  mean={statistics.mean(v):.0f}  "
          f"median={statistics.median(v):.0f}  stdev={statistics.pstdev(v):.0f}")
    step = 50
    for lo in range(0, v[-1] + step, step):
        n = sum(1 for x in v if lo <= x < lo + step)
        if n:
            print(f"{lo:5d}-{lo + step - 1:<5d} {'#' * n} {n}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("lam", nargs="?", type=int, default=80, metavar="LAMBDA")
    ap.add_argument("--runs", type=Path, default=Path(os.environ.get(
        "EVOSBST_RUNS", TOOL.parent.parent / "evosbst-runs")))
    a = ap.parse_args()
    runs = a.runs.resolve()
    runs.mkdir(parents=True, exist_ok=True)
    print(f"### generation 0 only, lambda={a.lam}, pipeline=build  (no simulator)", flush=True)
    rc = subprocess.call(
        [sys.executable, str(TOOL / "evosbst.py"), "run", "conf/campaigns/alu.toml",
         "--seed", "42", "--label", "alu-lengths", "--set", f"paths.runs={runs}",
         "--set", "cores.total=16", "--set", "cores.per_ind=1",
         "--set", 'pipeline=["build"]', "--set", f"ga.lambda={a.lam}",
         "--set", f"ga.mu={a.lam}", "--set", f"ga.nu={a.lam}", "--set", "ga.generations=0"],
        cwd=TOOL, stdin=subprocess.DEVNULL)
    # Runs are named by date, so a leading digit tells them from _ws, _cache, _log.
    dated = [d for d in runs.iterdir() if d.is_dir() and d.name[:1].isdigit()]
    if not dated:
        print("no run directory was created")
        return rc or 1
    run = max(dated, key=lambda d: d.stat().st_mtime)
    print(f"\n### instructions per individual, from {run / 'journal.csv'}")
    return histogram(run / "journal.csv") or rc


if __name__ == "__main__":
    sys.exit(main())
