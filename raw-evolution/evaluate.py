#!/usr/bin/env python3
"""Compile and score RAW programs against the complete fault campaign."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import config
from select_faults import read_faults

# Keep generated files beside this script.
HERE = Path(__file__).resolve().parent
# Give each unique program its own directory.
RUNS = HERE / "runs"
# Build this pristine database once, before any candidate runs.
CAMPAIGN_BASE = HERE / "campaign-base"
# This file proves which simulator produced the pristine database.
CAMPAIGN_INFO = CAMPAIGN_BASE / "campaign.json"
# These options must stay identical for every candidate.
FCC_OPTIONS = ("-uncontrollability", "-prune", "on", "-collapse", "off")
# Read counts from the Z01X summary.
DETECTED = re.compile(r"^#\s+Detected\s+DG\s+(\d+)", re.MULTILINE)
TOTAL = re.compile(r"^# Number of Faults:\s+(\d+)", re.MULTILINE)
TESTABLE = re.compile(r"^# Testable Faults:\s+(\d+)", re.MULTILINE)
NOT_ATTEMPTED = re.compile(r"^#\s+Not Attempted\s+NA\s+(\d+)", re.MULTILINE)
# Read the status of each port fault from the current SFF.
SFF_FAULT = re.compile(
    r'^\s*(?P<status>[A-Z0-9]+)\s+(?P<stuck_at>[01])\s+'
    r'\{PORT\s+"(?P<path>[^"]+)"'
)
# This line proves that the strobe reached raw_body and injected faults.
INJECTION_MARKER = "Info : Injecting "

# VCS and Z01X install two launchers for the same fault compiler.
# Compare their release instead of their different launcher paths.
TOOL_RELEASE = re.compile(r"(?:VCS|Z01X)_([^/]+)")


class InvalidProgram(RuntimeError):
    """The assembler rejected the candidate, rather than the tool setup."""


def require(path: Path, label: str) -> None:
    """Fail early when one required file is missing."""
    if not path.exists():
        raise FileNotFoundError(f"{label} not found: {path}")


def check() -> None:
    """Check every file and tool before a long run."""
    # These files come from the platform and its Z01X build.
    required = [
        (config.CC, "compiler"),
        (config.NM, "symbol reader"),
        (config.SIMV, "fault simulator"),
        (config.DAIDIR, "simulator data"),
        (config.SFF, "fault list"),
        (config.LINKER, "linker script"),
    ]

    for path, label in required:
        require(path, label)

    # Use the exported list, not gen_*.sff, which asks FCC to generate faults.
    text = config.SFF.read_text(errors="replace")
    if (re.search(r"(?m)^\s*FaultGenerate\b", text)
            or not re.search(r"(?m)^\s*FaultList\s*\{", text)):
        raise ValueError(f"expected a generated FaultList without FaultGenerate: {config.SFF}")

    # These commands come from the active Synopsys setup.
    for tool in ("vc_fcc", "vc_fcm"):
        if shutil.which(tool) is None:
            raise FileNotFoundError(f"tool not found on PATH: {tool}")


def validation_fault_ids(count: int) -> list[int]:
    """Choose faults that are testable in the current campaign."""
    chosen: list[int] = []
    fid = 0

    for line in config.SFF.read_text(errors="replace").splitlines():
        match = SFF_FAULT.match(line)
        if not match:
            continue

        fid += 1
        if match.group("status") == "NA":
            chosen.append(fid)
            if len(chosen) == count:
                return chosen

    raise ValueError(f"requested {count} validation faults, but only found {len(chosen)}")


def run(command: list[str], cwd: Path, log: Path) -> None:
    """Run one command and save all of its output."""
    # Write to a log because Z01X can print a lot of text.
    env = os.environ.copy()
    if Path(command[0]).name in ("vc_fcc", "vc_fcm"):
        # A shell's connection settings must not redirect a private campaign.
        env = {key: value for key, value in env.items() if not key.startswith("SNPS_FDB_")}
    with log.open("w") as stream:
        result = subprocess.run(command, cwd=cwd, env=env,
                                stdout=stream, stderr=subprocess.STDOUT)
    # Never turn a tool failure into a low fitness score.
    if result.returncode:
        raise RuntimeError(f"command failed with {result.returncode}; see {log}")


def fault_compiler_identity(path: str) -> dict[str, str]:
    """Return the part of the fault compiler identity that affects its output."""
    match = TOOL_RELEASE.search(path)
    if not match:
        # An unfamiliar installation remains path-sensitive for safety.
        return {"release": "unknown", "launcher": str(Path(path).resolve())}
    return {"release": match.group(1), "tool": "vc_fcc"}


def normalize_fingerprint(value: dict[str, object]) -> dict[str, object]:
    """Read both the old path-based marker and the new release-based marker."""
    normalized = dict(value)
    compiler = normalized.get("vc_fcc")

    # Databases made by the first version stored the complete launcher path.
    if isinstance(compiler, str):
        normalized["vc_fcc"] = fault_compiler_identity(compiler)

    normalized["format"] = 2
    return normalized


def campaign_fingerprint() -> dict[str, object]:
    """Describe the files that affect fault compilation."""
    inputs: dict[str, dict[str, int | str]] = {}

    # A changed simulator or fault list needs a new database.
    for name, path in (
        ("simv", config.SIMV),
        ("daidir", config.DAIDIR),
        ("sff", config.SFF),
    ):
        stat = path.stat()
        inputs[name] = {
            "path": str(path.resolve()),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }

    # Also reject a database made by another tool or option set.
    return {
        "format": 2,
        "campaign": config.CAMPAIGN,
        "vc_fcc": fault_compiler_identity(shutil.which("vc_fcc") or "vc_fcc"),
        "options": list(FCC_OPTIONS),
        "inputs": inputs,
    }


def campaign_is_ready() -> bool:
    """Return true only when the saved database matches this build."""
    if not (CAMPAIGN_BASE / "fdb").is_dir() or not CAMPAIGN_INFO.is_file():
        return False

    try:
        saved = json.loads(CAMPAIGN_INFO.read_text())
    except (json.JSONDecodeError, OSError):
        return False
    return normalize_fingerprint(saved) == campaign_fingerprint()


def evaluation_digest(
    source: Path,
    full: bool,
    selected_ids: list[int] | None = None,
) -> str:
    """Key a result by its program and every input that changes its meaning."""
    digest = hashlib.sha256()

    # Changes to the program, harness, or evaluator must not reuse old scores.
    for label, path in (
        ("source", source),
        ("harness", HERE / "harness.c"),
        ("evaluator", Path(__file__)),
        ("parser", HERE / "select_faults.py"),
        ("config", HERE / "config.py"),
    ):
        digest.update(label.encode())
        digest.update(path.read_bytes())

    # A sampled run also depends on the exact selected fault IDs.
    if not full:
        digest.update(b"faults")
        if not selected_ids:
            raise ValueError("a partial run requires explicit fault IDs")
        digest.update(json.dumps(selected_ids).encode())

    # This includes simv, its data directory, the SFF, and compiler options.
    fingerprint = json.dumps(campaign_fingerprint(), sort_keys=True).encode()
    digest.update(fingerprint)
    digest.update(b"full" if full else b"sampled")
    return digest.hexdigest()[:16]


def build_campaign() -> Path:
    """Run the fault compiler once and keep its untouched database."""
    check()

    # Reuse a complete database when it still matches the simulator.
    if campaign_is_ready():
        with (CAMPAIGN_BASE / "copy.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            verify_unused_database(CAMPAIGN_BASE)
        print(f"campaign is already ready: {CAMPAIGN_BASE}")
        return CAMPAIGN_BASE

    # Never overwrite a partial or stale database without review.
    if CAMPAIGN_BASE.exists():
        raise RuntimeError(
            f"campaign is incomplete or stale: {CAMPAIGN_BASE}; "
            "move it aside, then run make campaign again"
        )

    CAMPAIGN_BASE.mkdir()

    # This is the slow step. Every later candidate reuses its output.
    run(
        [
            "vc_fcc", "-full64", "-daidir", str(config.DAIDIR),
            "-sff", str(config.SFF), "-campaign", config.CAMPAIGN,
            *FCC_OPTIONS, "-overwrite",
        ],
        CAMPAIGN_BASE,
        CAMPAIGN_BASE / "fcc.log",
    )
    require(CAMPAIGN_BASE / "fdb", "compiled fault database")

    verify_unused_database(CAMPAIGN_BASE)

    # Write this marker only after vc_fcc finishes successfully.
    CAMPAIGN_INFO.write_text(json.dumps(campaign_fingerprint(), indent=2) + "\n")
    print(f"campaign prepared: {CAMPAIGN_BASE}")
    return CAMPAIGN_BASE


def stop_database(work: Path) -> None:
    """Stop only this database, and refuse to copy or delete it while live."""
    database = (work / "fdb").resolve()
    pid = database / "postmaster.pid"
    if not pid.exists():
        return
    command = f"fcm::stop_server -fdb_storage_path {{{database}}}"
    script = work / "stop-fdb.tcl"
    script.write_text(
        f"if {{[catch {{{command}}} message]}} {{\n"
        "    puts stderr $message\n"
        "    exit 1\n"
        "}\n"
    )
    run(["vc_fcm", "-fdb_mgmt_only", "-disable_auto_setup", "-tcl_script",
         str(script.resolve())],
        work, work / "stop-fdb.log")
    if pid.exists():
        raise RuntimeError(f"FDB server still has a PID file: {pid}; preserving database")


def run_fcm(work: Path, script: Path, log: Path) -> None:
    """Open this run's database explicitly and close its server afterwards."""
    try:
        run(["vc_fcm", "-fdb_path", str((work / "fdb").resolve()),
             "-fdb_project", "default", "-campaign", config.CAMPAIGN,
             "-connect", "-stop_server_on_exit", "-tcl_script", str(script)],
            work, log)
    finally:
        # The exit flag only stops servers that this FCM process started.
        stop_database(work)


def verify_unused_database(work: Path) -> None:
    """Require all testable faults to be NA, then leave the database stopped."""
    report = work / "unrun-summary.rpt"
    report.unlink(missing_ok=True)
    script = work / "check-unrun.tcl"
    script.write_text(
        f"report -campaign {config.CAMPAIGN} -summaryonly "
        f"-report {{{report}}} -overwrite\n"
    )
    run_fcm(work, script, work / "check-unrun.log")
    text = report.read_text(errors="replace")
    testable = TESTABLE.search(text)
    unattempted = NOT_ATTEMPTED.search(text)
    if (not testable or not unattempted or int(testable.group(1)) == 0
            or int(testable.group(1)) != int(unattempted.group(1))):
        raise RuntimeError(f"database contains results or lacks NA counts: {report}; preserving it")


def remove_run_data(work: Path) -> None:
    """Remove database files from an earlier or completed attempt."""
    stop_database(work)
    # Only remove database files after its server has stopped.
    for name in ("fdb", "fcm.dir", "fcm_tsim_fsdb"):
        path = work / name
        if path.is_dir():
            shutil.rmtree(path)


def copy_campaign(work: Path) -> None:
    """Copy a stopped, unused database and verify the copy before simulation."""
    if not campaign_is_ready():
        raise RuntimeError("campaign is not ready; run make campaign first")

    if (work / "fdb").exists():
        raise RuntimeError(f"database already exists in {work}; preserve this run before retrying")

    # Replay and evolution can share the base; never open it during a copy.
    with (CAMPAIGN_BASE / "copy.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        verify_unused_database(CAMPAIGN_BASE)
        source = CAMPAIGN_BASE / "fdb"
        if any(path.is_symlink() for path in source.rglob("*")):
            raise RuntimeError(f"database contains symlinks: {source}; refusing raw copy")
        run(["cp", "-a", "--reflink=auto", str(source), str(work / "fdb")],
            work, work / "copy-fdb.log")
    verify_unused_database(work)


def compile_program(source: Path, work: Path) -> Path:
    """Link one generated assembly file with the small C harness."""
    binary = work / "candidate.riscv"

    # Match the flags used by the other bare-metal CVA6 tests.
    command = [
        str(config.CC),
        "-O2", "-static", "-march=rv64imafd", "-mabi=lp64d", "-mcmodel=medany",
        "-specs=htif_nano.specs", "-T", str(config.LINKER),
        str(HERE / "harness.c"),
        # MicroGP produces names such as "individual0" without a .S suffix.
        # Tell GCC that this input is assembly instead of relying on its name.
        "-x", "assembler-with-cpp", str(source.resolve()), "-x", "none",
        "-o", str(binary),
    ]
    log = work / "compile.log"
    try:
        run(command, work, log)
    except RuntimeError as error:
        text = log.read_text(errors="replace")
        if "Assembler messages:" in text and re.search(r"\bError:", text):
            raise InvalidProgram(f"assembly rejected; see {log}") from error
        raise
    return binary


def raw_body_address(binary: Path) -> int:
    """Read the unique 64-bit address of raw_body from one candidate."""
    result = subprocess.run(
        [str(config.NM), "--defined-only", str(binary)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode:
        raise RuntimeError(f"nm failed for {binary}: {result.stderr.strip()}")

    matches: list[int] = []
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[-1] == "raw_body":
            matches.append(int(fields[0], 16))

    if len(matches) != 1:
        raise ValueError(f"expected one raw_body symbol in {binary}, found {len(matches)}")
    if not 0 <= matches[0] < 2**64:
        raise ValueError(f"raw_body address does not fit in 64 bits: {matches[0]:x}")
    return matches[0]


def simulation_args(binary: Path, raw_body_pc: int, max_cycles: int) -> list[str]:
    return [
        "+permissive", "+dramsim",
        f"+dramsim_ini_dir={config.DRAMSIM_INI}",
        f"+max-cycles={max_cycles}", f"+ntb_random_seed={config.SEED}",
        f"+loadmem={binary}", f"+raw_body_pc={raw_body_pc:016x}",
        "+disable_instr_trace", "+verbose", "+permissive-off", str(binary),
    ]


def probe(binary: Path, work: Path) -> int | None:
    """Run without faults; return cycles, or None for a failed program."""
    log = work / "probe.log"
    command = [str(config.SIMV), *simulation_args(
        binary, raw_body_address(binary), config.PROBE_MAX_CYCLES)]
    with log.open("w") as stream:
        result = subprocess.run(command, cwd=work, stdout=stream, stderr=subprocess.STDOUT)
    text = log.read_text(errors="replace")
    if "*** FAILED ***" in text:
        return None
    passed = re.search(r"\*\*\* PASSED \*\*\* Completed after\s+(\d+) simulation cycles", text)
    if result.returncode or not passed:
        raise RuntimeError(f"probe did not produce a valid result; see {log}")
    return int(passed.group(1))


def write_tcl(
    path: Path,
    binary: Path,
    ids: list[int] | None,
    report: Path,
    raw_body_pc: int,
    trace_dir: Path | None = None,
    max_cycles: int | None = None,
) -> None:
    """Write the small Tcl script that runs one candidate."""
    # Refuse a stale or hand-written address before simulation starts.
    actual_pc = raw_body_address(binary)
    if raw_body_pc != actual_pc:
        raise ValueError(
            f"raw_body address mismatch for {binary}: "
            f"requested 0x{raw_body_pc:016x}, binary has 0x{actual_pc:016x}"
        )

    # Keep every fault result for later comparisons and monitor replays.
    fault_report = report.with_name("faults.rpt")

    args = " ".join(simulation_args(
        binary, raw_body_pc, config.MAX_CYCLES if max_cycles is None else max_cycles))

    # Replay one fault at a time; ordinary fitness runs leave tracing off.
    if trace_dir is not None:
        args = args.replace(
            "+permissive-off",
            f"+dependency_trace_file={trace_dir / 'dependency.csv'} "
            f"+raw_checker_trace_file={trace_dir / 'raw-checker.csv'} +permissive-off",
        )

    # Omit -fids only for the final comparison against the complete list.
    if ids is None:
        fsim_command = "fsim -verbose -no_coats"
    else:
        selected = " ".join(str(fid) for fid in ids)
        fsim_command = f"fsim -verbose -no_coats -fids {{{selected}}}"

    # Keep one test case, one run of the faults, and one final report.
    path.write_text(
        f"""# Limit how many jobs Z01X may run at once.
set_config -global_max_jobs {config.WORKERS}
# Run ordinary faults in parallel.
set_config -fsim_mode {'serial' if trace_dir is not None else 'concurrent'}
# Print progress every two minutes.
set_config -update_interval 120
# Use the candidate as the only test case.
create_testcases -name raw_candidate -exec {{{config.SIMV}}} -args {{{args}}}
# Skip COATS and run the requested fault set.
{fsim_command}
# Save every fault before the private database is removed.
report -format standard -campaign {config.CAMPAIGN} -report {{{fault_report}}} -overwrite
# Save the short coverage report that becomes the fitness value.
report -campaign {config.CAMPAIGN} -summaryonly -report {{{report}}} -overwrite
"""
    )


def require_injection_marker(work: Path) -> None:
    """Require Z01X to confirm injection once in every simulator task."""
    task_logs = list((work / "fcm.dir").rglob("out.log"))
    simulator_logs: list[Path] = []
    evidence: list[str] = []

    for log in task_logs:
        text = log.read_text(errors="replace")
        if "Chronologic VCS simulator" not in text:
            continue
        simulator_logs.append(log)
        count = text.count(INJECTION_MARKER)
        if count != 1:
            raise RuntimeError(
                f"expected one Z01X injection marker in {log}, found {count}; "
                "preserving the run for review"
            )
        marker_line = next(line for line in text.splitlines() if INJECTION_MARKER in line)
        evidence.append(f"{log}: {marker_line}")

    if not simulator_logs:
        raise RuntimeError(f"no simulator task logs found below {work / 'fcm.dir'}")

    # Keep the proof after the large task directory is removed.
    (work / "injection-markers.txt").write_text("\n".join(evidence) + "\n")


def parse_coverage(report: Path, selected: int) -> float:
    """Return detected faults as a percentage of the selected set."""
    text = report.read_text(errors="replace")
    detected_match = DETECTED.search(text)
    testable_match = TESTABLE.search(text)
    not_attempted_match = NOT_ATTEMPTED.search(text)

    # All three counts are needed to prove that every selected fault ran.
    if not detected_match or not testable_match or not not_attempted_match:
        raise ValueError(f"required fault counts not found in {report}")

    detected = int(detected_match.group(1))
    attempted = int(testable_match.group(1)) - int(not_attempted_match.group(1))

    # Reject incomplete runs instead of giving them misleading scores.
    if attempted != selected:
        raise ValueError(
            f"report attempted {attempted} faults, but {selected} were selected: {report}"
        )

    # Illegal results stay in the denominator and therefore reduce fitness.
    return 100.0 * detected / selected


def parse_full_coverage(report: Path) -> float:
    """Return detected faults as a percentage of the complete fault list."""
    text = report.read_text(errors="replace")
    detected_match = DETECTED.search(text)
    total_match = TOTAL.search(text)
    not_attempted_match = NOT_ATTEMPTED.search(text)

    if not detected_match or not total_match:
        raise ValueError(f"required full-list counts not found in {report}")
    if not_attempted_match and int(not_attempted_match.group(1)) != 0:
        raise ValueError(f"full run left faults unattempted: {report}")

    return 100.0 * int(detected_match.group(1)) / int(total_match.group(1))


def detection_evidence(report: Path) -> dict:
    """Reuse the report parser and verify IDs against the full summary."""
    summary = report.with_name("summary.rpt")
    parse_full_coverage(summary)
    faults = read_faults(report)
    universe = read_faults(config.SFF)
    if [(f.path, f.stuck_at) for f in faults] != [(f.path, f.stuck_at) for f in universe]:
        raise ValueError(f"fault IDs do not match the current SFF: {report}")
    text = summary.read_text()
    total = int(TOTAL.search(text).group(1))
    ids = [f.fid for f in faults if f.status == "DD"]
    if len(faults) != total or len(ids) != int(DETECTED.search(text).group(1)):
        raise ValueError(f"detailed detections do not match DG in {summary}; refusing to guess IDs")
    if any(f.status == "IF" for f in faults):
        raise ValueError(f"illegal file I/O in {report}; refusing to score it")
    return {"format": 1, "total": total, "detected_ids": ids,
            "sff_sha256": hashlib.sha256(config.SFF.read_bytes()).hexdigest(),
            "report_sha256": hashlib.sha256(report.read_bytes()).hexdigest()}


def save_detected_ids(work: Path) -> None:
    evidence = detection_evidence(work / "faults.rpt")
    temporary = work / "detected-ids.json.tmp"
    temporary.write_text(json.dumps(evidence, indent=2) + "\n")
    temporary.replace(work / "detected-ids.json")


def freeze_reference(report: Path) -> None:
    """Freeze a chosen full report; never replace a reference implicitly."""
    markers = report.with_name("injection-markers.txt")
    require(markers, "evidence of injection")
    if INJECTION_MARKER not in markers.read_text():
        raise ValueError(f"no Z01X injection evidence in {markers}")
    evidence = detection_evidence(report)
    evidence.update({"report": str(report.resolve()), "campaign": campaign_fingerprint()})
    destination = config.FITNESS_REFERENCE
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x") as stream:
        stream.write(json.dumps(evidence, indent=2) + "\n")
    print(f"Frozen {len(evidence['detected_ids'])} reference detections: {destination}")


def load_reference(required: bool = False) -> dict | None:
    path = config.FITNESS_REFERENCE
    if not path.exists():
        if required:
            raise FileNotFoundError("freeze a full report before evolution: "
                                    "python3 evaluate.py --set-reference PATH/faults.rpt")
        return None
    reference = json.loads(path.read_text())
    if (reference.get("format") != 1
            or reference.get("campaign") != campaign_fingerprint()
            or reference.get("sff_sha256") != hashlib.sha256(config.SFF.read_bytes()).hexdigest()):
        raise ValueError(f"reference does not match the current campaign: {path}")
    ids = reference["detected_ids"]
    if (len(ids) != len(set(ids)) or any(type(fid) is not int
            or not 1 <= fid <= reference["total"] for fid in ids)):
        raise ValueError(f"invalid detected IDs in {path}")
    return reference


def complementary_detections(source: Path, reference: dict | None) -> int:
    if reference is None:
        return 0
    work = RUNS / "full" / evaluation_digest(source, True)
    if (work / "rejected.txt").exists():
        return 0
    evidence = json.loads((work / "detected-ids.json").read_text())
    if (evidence["total"] != reference["total"]
            or evidence["sff_sha256"] != reference["sff_sha256"]):
        raise ValueError(f"candidate and reference use different faults: {work}")
    return len(set(evidence["detected_ids"]) - set(reference["detected_ids"]))


def prepare(
    source: Path,
    full: bool = True,
    digest: str | None = None,
    selected_ids: list[int] | None = None,
    result_group: str | None = None,
) -> tuple[Path, Path]:
    """Compile a candidate and write its Tcl without running faults."""
    # The key changes when the program or experiment changes.
    digest = digest or evaluation_digest(source, full, selected_ids)
    if result_group:
        work = RUNS / result_group / digest
    else:
        work = RUNS / "full" / digest if full else RUNS / digest
    # Make the directory once and reuse it on later checks.
    work.mkdir(parents=True, exist_ok=True)

    # Build the binary before we create the Tcl that refers to it.
    snapshot = work / "source.S"
    if source.resolve() != snapshot.resolve():
        shutil.copy2(source, snapshot)
    binary = compile_program(snapshot, work)
    raw_body_pc = raw_body_address(binary)
    tcl = work / "fsim.tcl"
    ids = None if full else selected_ids
    write_tcl(tcl, binary, ids, work / "summary.rpt", raw_body_pc)
    return work, tcl


def compile_smoke(source: Path) -> Path:
    """Compile one MicroGP program without preparing or running faults."""
    digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
    work = RUNS / "smoke" / digest
    work.mkdir(parents=True, exist_ok=True)
    return compile_program(source, work)


def score(
    source: Path,
    full: bool = True,
    selected_ids: list[int] | None = None,
    result_group: str | None = None,
) -> float:
    """Run the full campaign, or explicit IDs for a validation check."""
    if full and selected_ids is not None:
        raise ValueError("full evaluation cannot also select individual faults")
    ids = None if full else selected_ids

    # Never reuse a score from another program, simulator, or fault set.
    digest = evaluation_digest(source, full, ids)
    if result_group:
        work = RUNS / result_group / digest
    else:
        work = RUNS / "full" / digest if full else RUNS / digest
    work.mkdir(parents=True, exist_ok=True)
    with (work / "evaluation.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return score_locked(source, full, ids, result_group, work, digest)


def score_locked(source, full, ids, result_group, work, digest) -> float:
    report = work / "summary.rpt"
    fault_report = work / "faults.rpt"
    marker_report = work / "injection-markers.txt"
    if (work / "rejected.txt").exists():
        return 0.0

    # Never combine evidence from different attempts or restart an old run
    # merely because the earlier version saved only its summary.
    permanent_files = (report, fault_report, marker_report)
    completed_files = sum(path.exists() for path in permanent_files)
    if completed_files not in (0, len(permanent_files)):
        raise RuntimeError(
            f"incomplete cached result in {work}; preserve it for review, "
            "then remove summary.rpt, faults.rpt, and injection-markers.txt "
            "before an intentional rerun"
        )

    # Reuse a score only when both permanent reports were saved.
    if completed_files == len(permanent_files):
        coverage = parse_full_coverage(report) if full else parse_coverage(report, len(ids))
        if full:
            save_detected_ids(work)
        # A previous run may have stopped after writing the report but before
        # it removed its private database. Finish that cleanup on a cache hit.
        remove_run_data(work)
        return coverage

    # A new attempt must not inherit either report from an interrupted run.
    report.unlink(missing_ok=True)
    fault_report.unlink(missing_ok=True)
    marker_report.unlink(missing_ok=True)

    # Compile the new program and make its Tcl file.
    try:
        work, tcl = prepare(
            source,
            full=full,
            digest=digest,
            selected_ids=ids,
            result_group=result_group,
        )
    except InvalidProgram as error:
        (work / "rejected.txt").write_text(str(error) + "\n")
        return 0.0

    binary = work / "candidate.riscv"
    cycles = probe(binary, work)
    if cycles is None:
        (work / "rejected.txt").write_text("fault-free program failed; see probe.log\n")
        return 0.0
    max_cycles = min(config.MAX_CYCLES, max(config.CYCLE_FLOOR,
                                         int(cycles * config.CYCLE_FACTOR)))
    write_tcl(tcl, binary, ids, report, raw_body_address(binary), max_cycles=max_cycles)

    # Copy the same stopped database without inheriting earlier results.
    copy_campaign(work)

    # Connect to the campaign and simulate only the chosen faults.
    run_fcm(work, tcl, work / "fcm.log")

    # A valid report is not enough if the commit trigger never fired.
    require_injection_marker(work)

    # Keep the detailed results before removing the private database.
    require(fault_report, "detailed fault report")

    # Read the score before removing the large temporary directories.
    coverage = parse_full_coverage(report) if full else parse_coverage(report, len(ids))
    if full:
        save_detected_ids(work)
    remove_run_data(work)

    # MicroGP maximizes the returned coverage value.
    return coverage


def main() -> int:
    """Handle checks, preparation, or normal scoring."""
    # The helper modes let us test each step on its own.
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--campaign", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--full", action="store_true",
                        help="evaluate the complete campaign (already the default)")
    parser.add_argument("--validate-faults", type=int, metavar="N")
    parser.add_argument("--compile", type=Path, metavar="SOURCE")
    parser.add_argument("--prepare", type=Path, metavar="SOURCE")
    parser.add_argument("--set-reference", type=Path, metavar="REPORT")
    parser.add_argument("--require-reference", action="store_true")
    parser.add_argument("sources", nargs="*", type=Path)
    args = parser.parse_args()

    if args.set_reference:
        freeze_reference(args.set_reference)
        return 0

    # Check paths without compiling or simulating anything.
    if args.check:
        check()
        print("RAW evaluator is ready")
        return 0
    # Compile or verify the stopped database used for later copies.
    if args.campaign:
        build_campaign()
        return 0
    # Compile one hand-written program without running faults.
    if args.compile:
        work = RUNS / "compile-check"
        work.mkdir(parents=True, exist_ok=True)
        print(compile_program(args.compile, work))
        return 0
    # Compile and show the Tcl so we can review it safely.
    if args.prepare:
        work, tcl = prepare(args.prepare)
        print(f"prepared {work}")
        print(f"review {tcl} before running faults")
        return 0
    # Compile generated programs and return dummy scores without using Z01X.
    if args.smoke:
        if not args.sources:
            parser.error("--smoke needs at least one assembly file")
        values = []
        for source in args.sources:
            binary = compile_smoke(source)
            values.append(0.0)
            print(f"compiled {source}: {binary}")
        output = Path(os.environ.get("UGP3_FITNESS_FILE", "fitness.output"))
        output.write_text("".join(f"{value:.6f} 0\n" for value in values))
        return 0
    # Normal MicroGP use must provide one or more programs.
    if not args.sources:
        parser.error("give at least one assembly file")

    validation_ids: list[int] | None = None
    result_group: str | None = None
    if args.validate_faults is not None:
        if args.full:
            parser.error("--validate-faults cannot be combined with --full")
        if args.validate_faults < 1:
            parser.error("--validate-faults must be at least 1")
        validation_ids = validation_fault_ids(args.validate_faults)
        result_group = "validate"

    reference = load_reference(args.require_reference) if validation_ids is None else None

    def evaluate_source(source):
        value = score(
            source,
            full=validation_ids is None,
            selected_ids=validation_ids,
            result_group=result_group,
        )
        extra = complementary_detections(source, reference)
        print(f"{source}: {value:.2f}%, {extra} beyond reference", flush=True)
        return value, extra

    # map keeps scores in the order MicroGP supplied the programs.
    with ThreadPoolExecutor(max_workers=config.EVALUATIONS) as pool:
        values = list(pool.map(evaluate_source, args.sources))

    # MicroGP tells us where to place the fitness values.
    # Use a local file when the variable is absent.
    output = Path(os.environ.get("UGP3_FITNESS_FILE", "fitness.output"))
    output.write_text("".join(f"{coverage:.12f} {extra}\n" for coverage, extra in values))
    return 0


if __name__ == "__main__":
    # Return a useful status code to the shell and to MicroGP.
    raise SystemExit(main())
