#!/usr/bin/env python3
"""Choose a stable set of faults from a Z01X report."""
from __future__ import annotations

import argparse
import csv
import random
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import config

# One line in the report looks like this:
#   < 1> ND 0 {PORT "...fwd_i.issue_pointer[0]"}
# We keep its old status, stuck value, and full path.
FAULT = re.compile(
    r'^\s*<\s*\d+>\s+(?P<status>[A-Z0-9]+)\s+'
    r'(?P<stuck>[01])\s+\{PORT\s+"(?P<path>[^"]+)"\}'
)


@dataclass(frozen=True)
class Fault:
    # Z01X uses this number when fsim receives -fids.
    fid: int

    # Examples are ND, NC, NO, and DD.
    status: str

    # A stuck-at fault forces either zero or one.
    stuck_at: int

    # This is the complete signal path in the CVA6 harness.
    path: str

    @property
    def family(self) -> str:
        """Group bits of the same signal together."""
        # signal[0] and signal[7] belong to one family.
        return re.sub(r"\[\d+\]", "[]", self.path)


def read_faults(report: Path) -> list[Fault]:
    """Read only entries inside the FaultList block."""
    faults: list[Fault] = []
    in_list = False

    for line in report.read_text(errors="replace").splitlines():
        # Ignore the report header until the list begins.
        if line.startswith("FaultList"):
            in_list = True
            continue

        # The first closing brace after FaultList ends the list.
        if in_list and line.strip() == "}":
            break

        if not in_list:
            continue

        # Ignore summary lines and any line that is not a port fault.
        match = FAULT.match(line)
        if match:
            # Z01X uses one-based IDs. The report keeps the same order.
            faults.append(
                Fault(
                    fid=len(faults) + 1,
                    status=match.group("status"),
                    stuck_at=int(match.group("stuck")),
                    path=match.group("path"),
                )
            )

    if not faults:
        # An empty list usually means the wrong report was given.
        raise ValueError(f"no faults found in {report}")
    return faults


def pick_balanced(faults: list[Fault], count: int, rng: random.Random) -> list[Fault]:
    """Spread a quota across stuck values and signal families."""
    # Each group contains one stuck value from one signal family.
    groups: dict[tuple[int, str], list[Fault]] = defaultdict(list)
    for fault in faults:
        groups[(fault.stuck_at, fault.family)].append(fault)

    # Shuffle within each group with the fixed seed.
    for group in groups.values():
        rng.shuffle(group)

    # Shuffle the order in which families contribute a fault.
    keys = sorted(groups)
    rng.shuffle(keys)
    chosen: list[Fault] = []

    # Take one fault from every family before taking a second one.
    # This stops wide buses from filling the complete set.
    while keys and len(chosen) < count:
        next_keys: list[tuple[int, str]] = []
        for key in keys:
            group = groups[key]
            if group and len(chosen) < count:
                chosen.append(group.pop())
            if group:
                next_keys.append(key)
        keys = next_keys

    if len(chosen) != count:
        # Never hide a shortage by returning a smaller set.
        raise ValueError(f"requested {count} faults, but only {len(chosen)} match")
    return chosen


def select(faults: list[Fault]) -> list[Fault]:
    """Apply the block filter and every status quota."""
    # Keep only the scoreboard and the block that reads operands.
    focused = [fault for fault in faults if any(name in fault.path for name in config.FOCUS)]

    # The fixed seed makes repeated selection produce the same CSV file.
    rng = random.Random(config.SEED)
    chosen: list[Fault] = []

    # Fill each quota independently so one large class cannot dominate.
    for status, count in config.QUOTAS.items():
        pool = [fault for fault in focused if fault.status == status]
        chosen.extend(pick_balanced(pool, count, rng))

    # Sorting makes the file easy to compare and inspect.
    return sorted(chosen, key=lambda fault: fault.fid)


def write_csv(path: Path, faults: list[Fault]) -> None:
    """Write both the IDs used by Z01X and their human context."""
    with path.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("fid", "status", "stuck_at", "path"))
        for fault in faults:
            writer.writerow((fault.fid, fault.status, fault.stuck_at, fault.path))


def main() -> int:
    # With no arguments, use the report and output from config.py.
    parser = argparse.ArgumentParser()
    parser.add_argument("report", nargs="?", type=Path, default=config.REPORT)
    parser.add_argument("-o", "--output", type=Path, default=Path(__file__).with_name("faults.csv"))
    args = parser.parse_args()

    # Parse, select, and save in one repeatable operation.
    chosen = select(read_faults(args.report))
    write_csv(args.output, chosen)

    # Print a short check that the requested quotas were met.
    totals: dict[str, int] = defaultdict(int)
    for fault in chosen:
        totals[fault.status] += 1
    summary = ", ".join(f"{name}={totals[name]}" for name in config.QUOTAS)
    print(f"wrote {len(chosen)} faults to {args.output} ({summary})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
