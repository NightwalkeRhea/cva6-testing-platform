#!/usr/bin/env python3
"""Replay any candidate with chosen faults and compare its monitor traces."""
import argparse
import csv
import json
import re
import shutil
import tempfile
from collections import Counter
from pathlib import Path

import config
import evaluate
from select_faults import read_faults


def require_fault_injection(work: Path) -> None:
    """Check injected tasks, excluding serial mode's fault-free tsim tasks."""
    logs = [log for log in (work / "fcm.dir").rglob("out.log")
            if log.parent.name.startswith("fsim:")]
    if not logs:
        raise RuntimeError(f"no fsim task logs below {work}; inspect fcm.log")
    evidence = []
    for log in logs:
        lines = [line for line in log.read_text(errors="replace").splitlines()
                 if evaluate.INJECTION_MARKER in line]
        if len(lines) != 1:
            raise RuntimeError(f"expected one injection marker in {log}, found {len(lines)}")
        evidence.append(f"{log}: {lines[0]}")
    (work / "injection-markers.txt").write_text("\n".join(evidence) + "\n")


def compare(clean: Path, faulty: Path) -> dict:
    """Compare matching events; never pair unrelated rows after divergence."""
    with clean.open(newline="") as stream:
        reference = csv.DictReader(stream)
        with faulty.open(newline="") as other:
            observed = csv.DictReader(other)
            if reference.fieldnames != observed.fieldnames:
                raise RuntimeError(f"different CSV headers: {faulty}")
            changed = Counter()
            examples = []
            rows = 0
            for left, right in zip(reference, observed):
                rows += 1
                keys = ("cycle", "issue_port", "pc", "operand")
                if any(left.get(key) != right.get(key) for key in keys):
                    return {"aligned_rows": rows - 1, "diverged_at_row": rows,
                            "clean_event": left, "fault_event": right,
                            "changed_fields_before_divergence": dict(changed)}
                differences = {key: [left[key], right[key]] for key in left
                               if left[key] != right[key]}
                changed.update(differences.keys())
                if differences and len(examples) < 5:
                    examples.append({"cycle": left["cycle"], "pc": left["pc"],
                                     "changes": differences})
    # Count rows separately because zip stops at the shorter file.
    def count(path):
        with path.open(newline="") as stream:
            return sum(1 for _ in csv.DictReader(stream))
    clean_count, fault_count = count(clean), count(faulty)
    if not clean_count or not fault_count:
        raise RuntimeError(f"trace has no data: {clean} or {faulty}")
    return {"clean_rows": clean_count, "fault_rows": fault_count,
            "changed_fields": dict(changed), "first_changes": examples}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?", type=Path, default=evaluate.HERE / "seed.S")
    parser.add_argument("--fids", nargs="+", type=int, default=[645, 648], metavar="FID")
    args = parser.parse_args(argv)
    evaluate.check()
    evaluate.require(args.source, "candidate assembly")
    selected = {fault.fid: fault for fault in read_faults(config.SFF)}
    if len(args.fids) != len(set(args.fids)):
        parser.error("--fids must not contain duplicate IDs")
    for fid in args.fids:
        fault = selected.get(fid)
        if fault is None or fault.status.startswith("U"):
            parser.error(f"FID {fid} is missing or classified as untestable in the current SFF")

    evaluate.RUNS.mkdir(exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="monitor-replay-", dir=evaluate.RUNS))
    print(f"Replay directory: {root}", flush=True)
    # Keep the inputs alongside the evidence so later edits cannot obscure them.
    source = root / "source.S"
    shutil.copy2(args.source, source)
    for name in ("harness.c", "config.py"):
        shutil.copy2(evaluate.HERE / name, root / name)
    shutil.copy2(config.SFF, root / "faults.sff")
    binary = evaluate.compile_program(source, root)
    pc = evaluate.raw_body_address(binary)
    clean = root / "clean"
    clean.mkdir()
    sim_args = evaluate.simulation_args(binary, pc, config.PROBE_MAX_CYCLES)
    at = sim_args.index("+permissive-off")
    sim_args[at:at] = [f"+dependency_trace_file={clean / 'dependency.csv'}",
                       f"+raw_checker_trace_file={clean / 'raw-checker.csv'}"]
    command = [str(config.SIMV), *sim_args]
    print(f"Running clean candidate {args.source}", flush=True)
    evaluate.run(command, clean, clean / "sim.log")
    text = (clean / "sim.log").read_text(errors="replace")
    passed = re.search(r"\*\*\* PASSED \*\*\* Completed after\s+(\d+) simulation cycles", text)
    if not passed or text.count("CVA6 RAW: injecting faults") != 1:
        raise RuntimeError(f"clean run did not pass with one trigger; see {clean / 'sim.log'}")
    max_cycles = min(config.MAX_CYCLES, max(config.CYCLE_FLOOR,
                                         int(int(passed.group(1)) * config.CYCLE_FACTOR)))

    summary = {"source": str(source), "original_source": str(args.source.resolve()),
               "raw_body_pc": f"0x{pc:016x}", "max_cycles": max_cycles,
               "faults": {}}
    for fid in args.fids:
        work = root / f"fid-{fid}"
        work.mkdir()
        print(f"Copying database and replaying FID {fid}", flush=True)
        evaluate.copy_campaign(work)
        evaluate.write_tcl(work / "fsim.tcl", binary, [fid], work / "summary.rpt", pc,
                           trace_dir=work, max_cycles=max_cycles)
        evaluate.run_fcm(work, work / "fsim.tcl", work / "fcm.log")
        require_fault_injection(work)
        report = (work / "summary.rpt").read_text()
        if "Illegal File I/O" in report:
            raise RuntimeError(f"monitor output caused IF; inspect {work}; no comparison accepted")
        target = selected[fid]
        summary["faults"][str(fid)] = {"target": {
            "fid": fid, "initial_status": target.status,
            "stuck_at": target.stuck_at, "path": target.path}, "traces": {
            name: compare(clean / name, work / name)
            for name in ("dependency.csv", "raw-checker.csv")}}
        # Save partial progress too, in case the next replay fails.
        (root / "comparison.json").write_text(json.dumps(summary, indent=2) + "\n")
        evaluate.remove_run_data(work)
    print(f"Done. Share {root / 'comparison.json'}", flush=True)


if __name__ == "__main__":
    main()
