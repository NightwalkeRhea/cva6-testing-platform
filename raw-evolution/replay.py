#!/usr/bin/env python3
"""Replay chosen faults on a candidate, or summarize a finished MicroGP run."""
import argparse
import csv
import json
import random
import re
import shutil
import tempfile
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import config
import evaluate
from select_faults import pick_balanced, read_faults

# A passing clean run prints this line with its cycle count.
PASSED = re.compile(r"\*\*\* PASSED \*\*\* Completed after\s+(\d+) simulation cycles")
# Order fault statuses from best to worst, to keep each fault's best result in a run.
RANK = {"DD": 0, "PD": 1, "NO": 2, "ND": 3, "NC": 4}


def rank(status: str) -> int:
    """Lower is better; untestable (U*) faults come last."""
    return 5 if status.startswith("U") else RANK.get(status, 3)


def compare(clean: Path, faulty: Path) -> dict:
    """Compare matching events; never pair unrelated rows after divergence."""
    with evaluate.open_trace(clean) as stream:
        reference = csv.DictReader(stream)
        with evaluate.open_trace(faulty) as other:
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
        with evaluate.open_trace(path) as stream:
            return sum(1 for _ in csv.DictReader(stream))
    clean_count, fault_count = count(clean), count(faulty)
    if not clean_count or not fault_count:
        raise RuntimeError(f"trace has no data: {clean} or {faulty}")
    return {"clean_rows": clean_count, "fault_rows": fault_count,
            "changed_fields": dict(changed), "first_changes": examples}


def passed_cycles(log: Path) -> int | None:
    """Cycles of a clean run that passed and triggered injection exactly once."""
    text = log.read_text(errors="replace")
    passed = PASSED.search(text)
    if not passed or text.count("CVA6 RAW: injecting faults") != 1:
        return None
    return int(passed.group(1))


def clean_traces(source: Path, root: Path, work: Path | None = None) -> tuple[Path, Path, int]:
    """Return (trace folder, binary, cycles) of a clean run of source."""
    # Reuse the clean run that evaluation already made for this exact program.
    # A run report passes the folder from its manifest; the folder name
    # changes whenever evaluate.py or config.py changes, so recomputing it can miss.
    work = work or evaluate.RUNS / "full" / evaluate.evaluation_digest(source, True)
    evidence = work / "detected-ids.json"
    files = [work / "profile.json", work / "candidate.riscv", evidence]
    traces = [work / "clean" / name for name in evaluate.TRACES]
    # Reuse only a complete run made by this same simulator build; otherwise
    # run the clean simulation again, so clean and faulty traces match.
    if (all(path.exists() for path in files)
            and all(path.exists() or Path(f"{path}.gz").exists() for path in traces)
            and json.loads(evidence.read_text()).get("campaign")
            == evaluate.campaign_fingerprint()):
        cycles = passed_cycles(work / "probe.log")
        if cycles is not None:
            return work / "clean", work / "candidate.riscv", cycles

    # Otherwise (e.g. a hand-written program) run it once here with the traces on.
    binary = evaluate.compile_program(source, root)
    clean = root / "clean"
    clean.mkdir()
    command = [str(config.SIMV), *evaluate.simulation_args(
        binary, evaluate.raw_body_address(binary), config.PROBE_MAX_CYCLES, trace_dir=clean)]
    evaluate.run(command, clean, clean / "sim.log")
    cycles = passed_cycles(clean / "sim.log")
    if cycles is None:
        raise RuntimeError(f"clean run did not pass with one trigger; see {clean / 'sim.log'}")
    return clean, binary, cycles


def replay_faults(source: Path, fids: list[int], root: Path,
                  evaluated: Path | None = None) -> dict:
    """Run each fault alone with traces on, and compare it with the clean traces."""
    faults = {fault.fid: fault for fault in read_faults(config.SFF)}
    # Keep the inputs alongside the evidence so later edits cannot obscure them.
    shutil.copy2(source, root / "source.S")
    for name in ("harness.c", "config.py"):
        shutil.copy2(evaluate.HERE / name, root / name)
    shutil.copy2(config.SFF, root / "faults.sff")
    clean, binary, cycles = clean_traces(root / "source.S", root, evaluated)
    pc = evaluate.raw_body_address(binary)
    max_cycles = evaluate.fault_cycle_limit(cycles)

    summary = {"source": str(root / "source.S"), "original_source": str(source.resolve()),
               "raw_body_pc": f"0x{pc:016x}", "max_cycles": max_cycles,
               "clean_traces": str(clean), "faults": {}}
    # The streams run in threads; only one may update the summary at a time.
    lock = threading.Lock()

    def replay_stream(stream: Path, stream_fids: list[int]) -> None:
        """Replay some faults one after another on this stream's own database copy."""
        # One copy serves all faults of the stream: each run simulates only its own
        # -fids, so results already stored for other faults do not matter.
        stream.mkdir()
        with evaluate.measure(stream, "prepare_fdb"):
            evaluate.copy_campaign(stream)
        for index, fid in enumerate(stream_fids):
            work = root / f"fid-{fid}"
            work.mkdir()
            print(f"Replaying FID {fid} in {stream.name}", flush=True)
            # Only the first run sees a fresh copy, so only it can prove the copy was unused.
            evaluate.write_tcl(work / "fsim.tcl", binary, [fid], work / "summary.rpt", pc,
                               trace_dir=work, max_cycles=max_cycles, check_unused=index == 0)
            evaluate.run_fcm(work, work / "fsim.tcl", work / "fcm.log",
                             database=stream / "fdb")
            if index == 0:
                evaluate.require_unused(work / "unrun-summary.rpt")
            evaluate.require_injection_marker(work)
            report = (work / "summary.rpt").read_text()
            if "Illegal File I/O" in report:
                raise RuntimeError(f"monitor output caused IF; inspect {work}; no comparison accepted")
            target = faults[fid]
            result = {
                "target": {"fid": fid, "initial_status": target.status,
                           "stuck_at": target.stuck_at, "path": target.path},
                # DD here means this fault alone was detected at the boundary.
                "replay_status": read_faults(work / "faults.rpt")[fid - 1].status,
                "traces": {name: compare(clean / name, work / name)
                           for name in evaluate.TRACES}}
            with lock:
                summary["faults"][str(fid)] = result
                # Save partial progress too, in case a later replay fails.
                (root / "comparison.json").write_text(json.dumps(summary, indent=2) + "\n")
            # Remove this fault's task files; the stream's database stays for its next fault.
            evaluate.remove_run_data(work)
        # This stream is done: stop its database and delete it.
        evaluate.remove_run_data(stream)

    # Deal the faults out like cards: stream 0 gets faults 0, 4, 8, ...; stream 1 gets 1, 5, 9, ...
    count = max(1, min(config.REPLAY_STREAMS, len(fids)))
    streams = [fids[index::count] for index in range(count)]
    print(f"Replaying {len(fids)} faults in {count} parallel streams", flush=True)
    with ThreadPoolExecutor(max_workers=count) as pool:
        # list() re-raises the first error; a failed stream keeps its database for review.
        list(pool.map(replay_stream, [root / f"stream-{index}" for index in range(count)], streams))

    # Write the final file with the faults in the order they were requested.
    summary["faults"] = {str(fid): summary["faults"][str(fid)] for fid in fids}
    (root / "comparison.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def report_run(run: Path) -> None:
    """Merge every candidate of one make evolve run, then replay its best program."""
    entries = [json.loads(line) for line in (run / "manifest.jsonl").read_text().splitlines()]
    scored = [entry for entry in entries if not entry["rejected"]]
    if not scored:
        raise RuntimeError(f"no scored candidates in {run / 'manifest.jsonl'}")
    best = max(scored, key=lambda entry: (entry["coverage"], entry["beyond_reference"]))
    faults = read_faults(config.SFF)

    # Count each program once, even when MicroGP produced it several times.
    programs = {entry["digest"]: evaluate.HERE / entry["run_dir"] for entry in scored}
    best_status = {}
    seen = Counter()
    build = evaluate.campaign_fingerprint()
    for work in programs.values():
        # Keep each fault's best status over all programs (DD beats NO beats NC).
        evidence = json.loads((work / "detected-ids.json").read_text())
        # Every program must come from the current build and fault list, or the
        # merged statuses and the replays would mix two different designs.
        if evidence.get("campaign") != build:
            raise RuntimeError(f"{work} was evaluated with another simulator build or fault list")
        for status, ids in evidence["ids_by_status"].items():
            for fid in ids:
                if fid not in best_status or rank(status) < rank(best_status[fid]):
                    best_status[fid] = status
        # Count how many programs produced each kind of forwarding event.
        for event in json.loads((work / "profile.json").read_text())["events"]:
            seen[(event["operand"], event["producer_unit"], event["distance"],
                  event["port"], event["stall"])] += 1

    # Faults no program detected, grouped by signal (bits of one bus together).
    missed = defaultdict(Counter)
    missed_ids = defaultdict(list)
    for fault in faults:
        status = best_status.get(fault.fid, fault.status)
        if status == "DD" or status.startswith("U"):
            continue
        missed[fault.family.split("issue_stage_i.", 1)[-1]][status] += 1
        missed_ids[status].append(fault.fid)

    # Combinations of the units and ports seen in this run that no program produced.
    # Some may be impossible on CVA6; they are hints, not targets.
    units = sorted({key[1] for key in seen})
    ports = sorted({key[3] for key in seen})
    never_seen = [
        {"operand": operand, "producer_unit": unit, "distance": distance,
         "port": port, "stall": stall}
        for operand in ("rs1", "rs2") for unit in units
        for distance in ("1", "2", "3", "4+") for port in ports
        for stall in ("no stall", "stalled")
        if (operand, unit, distance, port, stall) not in seen]

    report = {
        "run": run.name,
        "legend": {
            "best": "highest coverage, then most detections beyond the reference; "
                    "find its genome in status.xml by its individual name",
            "faults_by_best_status": "each fault counted once, with the best status any program "
                                     "reached: DD detected, NO excited but not seen at the "
                                     "boundary, NC never excited, U* untestable",
            "missed_by_signal": "faults no program detected, per signal",
            "events_seen": "forwarding events, with how many programs produced each",
            "replays": "traces of single faults that the best program excited but missed",
        },
        "candidates": len(entries),
        "rejected": len(entries) - len(scored),
        "unique_programs": len(programs),
        "best": best,
        "faults_by_best_status": dict(Counter(best_status.values())),
        "missed_by_signal": dict(sorted(((family, dict(counts)) for family, counts in missed.items()),
                                        key=lambda item: -sum(item[1].values()))),
        "missed_ids": dict(missed_ids),
        "events_seen": [{"operand": key[0], "producer_unit": key[1], "distance": key[2],
                         "port": key[3], "stall": key[4], "programs": count}
                        for key, count in sorted(seen.items())],
        "events_never_seen": never_seen,
        "replays": "replays/comparison.json",
    }
    (run / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"Wrote {run / 'report.json'}", flush=True)

    # Pick the best program's missed faults that were excited (NO), spread over
    # signals. Without NO labels, fall back to all undetected faults (ND).
    best_work = evaluate.HERE / best["run_dir"]
    by_status = json.loads((best_work / "detected-ids.json").read_text())["ids_by_status"]
    status = "NO" if by_status.get("NO") else "ND"
    by_id = {fault.fid: fault for fault in faults}
    pool = [by_id[fid] for fid in by_status.get(status, [])]
    chosen = pick_balanced(pool, min(config.REPLAY_FAULTS, len(pool)), random.Random(config.SEED))
    if not chosen:
        print("No excited but missed faults to replay", flush=True)
        return
    root = run / "replays"
    root.mkdir()
    replay_faults(best_work / "source.S", sorted(fault.fid for fault in chosen), root, best_work)
    print(f"Done. Share {run}", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?", type=Path, default=evaluate.HERE / "seed.S")
    parser.add_argument("--fids", nargs="+", type=int, default=[645, 648], metavar="FID")
    parser.add_argument("--run", type=Path, metavar="DIR",
                        help="summarize a finished make evolve run and replay its best program")
    args = parser.parse_args(argv)
    evaluate.check()
    if args.run:
        # Make the path absolute: the tools run inside the stream folders.
        report_run(args.run.resolve())
        return
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
    replay_faults(args.source, args.fids, root)
    print(f"Done. Share {root / 'comparison.json'}", flush=True)


if __name__ == "__main__":
    main()
