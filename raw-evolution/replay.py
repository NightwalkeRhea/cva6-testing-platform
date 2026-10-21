#!/usr/bin/env python3
"""Replay the seed and two missed RAW faults, then compare their traces."""
import csv
import json
import tempfile
from collections import Counter
from pathlib import Path

import config
import evaluate


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


def main():
    evaluate.check()
    # Freeze the targets to the two missed pointer faults already reviewed.
    selected = {}
    for line in config.SFF.read_text(errors="replace").splitlines():
        match = evaluate.SFF_FAULT.match(line)
        if match:
            fid = len(selected) + 1
            selected[fid] = {"fid": str(fid), **match.groupdict()}
    for fid in (645, 648):
        row = selected.get(fid)
        if row is None or "fwd_i.issue_pointer[" not in row["path"]:
            raise RuntimeError(f"FID {fid} must be an issue_pointer fault in the current SFF")

    evaluate.RUNS.mkdir(exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="monitor-replay-", dir=evaluate.RUNS))
    print(f"Replay directory: {root}", flush=True)
    # Keep the inputs alongside the evidence so later edits cannot obscure them.
    import shutil
    for name in ("seed.S", "harness.c", "config.py"):
        shutil.copy2(evaluate.HERE / name, root / name)
    shutil.copy2(config.SFF, root / "faults.sff")
    binary = evaluate.compile_program(root / "seed.S", root)
    pc = evaluate.raw_body_address(binary)
    clean = root / "clean"
    clean.mkdir()
    command = [str(config.SIMV), "+permissive", "+dramsim",
               f"+dramsim_ini_dir={config.DRAMSIM_INI}",
               f"+max-cycles={config.MAX_CYCLES}", f"+ntb_random_seed={config.SEED}",
               f"+loadmem={binary}", f"+raw_body_pc={pc:016x}",
               "+disable_instr_trace", f"+dependency_trace_file={clean / 'dependency.csv'}",
               f"+raw_checker_trace_file={clean / 'raw-checker.csv'}",
               "+verbose", "+permissive-off", str(binary)]
    print("Running clean seed", flush=True)
    evaluate.run(command, clean, clean / "sim.log")
    text = (clean / "sim.log").read_text(errors="replace")
    if "*** PASSED ***" not in text or text.count("CVA6 RAW: injecting faults") != 1:
        raise RuntimeError(f"clean run did not pass with one trigger; see {clean / 'sim.log'}")

    summary = {"source": str(root / "seed.S"), "raw_body_pc": f"0x{pc:016x}",
               "faults": {}}
    for fid in (645, 648):
        work = root / f"fid-{fid}"
        work.mkdir()
        print(f"Copying database and replaying FID {fid}", flush=True)
        evaluate.copy_campaign(work)
        evaluate.write_tcl(work / "fsim.tcl", binary, [fid], work / "summary.rpt", pc,
                           trace_dir=work)
        evaluate.run_fcm(work, work / "fsim.tcl", work / "fcm.log")
        require_fault_injection(work)
        report = (work / "summary.rpt").read_text()
        if "Illegal File I/O" in report:
            raise RuntimeError(f"monitor output caused IF; inspect {work}; no comparison accepted")
        summary["faults"][str(fid)] = {"target": selected[fid], "traces": {
            name: compare(clean / name, work / name)
            for name in ("dependency.csv", "raw-checker.csv")}}
        # Save partial progress too, in case the next replay fails.
        (root / "comparison.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"Done. Share {root / 'comparison.json'}", flush=True)


if __name__ == "__main__":
    main()
