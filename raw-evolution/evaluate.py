#!/usr/bin/env python3
"""Compile and score RAW programs against the selected faults."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import config

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
SFF_FAULT = re.compile(r'^\s*(?P<status>[A-Z0-9]+)\s+[01]\s+\{PORT\s+"')
# This line proves that the strobe reached raw_body and injected faults.
INJECTION_MARKER = "Info : Injecting "

# VCS and Z01X install two launchers for the same fault compiler.
# Compare their release instead of their different launcher paths.
TOOL_RELEASE = re.compile(r"(?:VCS|Z01X)_([^/]+)")


def require(path: Path, label: str) -> None:
    """Fail early when one required file is missing."""
    if not path.exists():
        raise FileNotFoundError(f"{label} not found: {path}")


def check(require_faults: bool = True) -> None:
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

    # The first campaign is built before the seed selects its 500 faults.
    if require_faults:
        required.append((HERE / "faults.csv", "selected faults"))

    for path, label in required:
        require(path, label)

    # These commands come from the active Synopsys setup.
    for tool in ("vc_fcc", "vc_fcm"):
        if shutil.which(tool) is None:
            raise FileNotFoundError(f"tool not found on PATH: {tool}")


def fault_ids() -> list[int]:
    """Read the 500 fault IDs chosen by select_faults.py."""
    with (HERE / "faults.csv").open(newline="") as stream:
        return [int(row["fid"]) for row in csv.DictReader(stream)]


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
    with log.open("w") as stream:
        result = subprocess.run(command, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT)
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
        ("config", HERE / "config.py"),
    ):
        digest.update(label.encode())
        digest.update(path.read_bytes())

    # A sampled run also depends on the exact selected fault IDs.
    if not full:
        digest.update(b"faults")
        ids = selected_ids if selected_ids is not None else fault_ids()
        digest.update(json.dumps(ids).encode())

    # This includes simv, its data directory, the SFF, and compiler options.
    fingerprint = json.dumps(campaign_fingerprint(), sort_keys=True).encode()
    digest.update(fingerprint)
    digest.update(b"full" if full else b"sampled")
    return digest.hexdigest()[:16]


def build_campaign() -> Path:
    """Run the fault compiler once and keep its untouched database."""
    check(require_faults=False)

    # Reuse a complete database when it still matches the simulator.
    if campaign_is_ready():
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

    # Write this marker only after vc_fcc finishes successfully.
    CAMPAIGN_INFO.write_text(json.dumps(campaign_fingerprint(), indent=2) + "\n")
    print(f"campaign prepared: {CAMPAIGN_BASE}")
    return CAMPAIGN_BASE


def remove_run_data(work: Path) -> None:
    """Remove database files from an earlier or completed attempt."""
    # The database server may remove postmaster.pid while rmtree scans it.
    # Treat an already missing file as cleaned, but keep every other error.
    def ignore_missing_file(_function, _path, error) -> None:
        problem = error[1]
        if isinstance(problem, FileNotFoundError):
            return
        raise problem

    # These names are generated by Z01X inside one candidate directory.
    # Keep FDB intact: vc_fcm can leave its PostgreSQL server running.
    # Deleting or copying that directory while it is live is unsafe.
    for name in ("fcm.dir", "fcm_tsim_fsdb"):
        path = work / name
        if path.is_dir():
            shutil.rmtree(path, onerror=ignore_missing_file)


def compile_campaign(work: Path) -> None:
    """Compile fresh results for this candidate instead of copying a live FDB."""
    if not campaign_is_ready():
        raise RuntimeError("campaign is not ready; run make campaign first")

    if (work / "fdb").exists():
        raise RuntimeError(f"database already exists in {work}; preserve this run before retrying")

    # The SFF defines the same faults, but no previous candidate's results.
    run(
        [
            "vc_fcc", "-full64", "-daidir", str(config.DAIDIR),
            "-sff", str(config.SFF), "-campaign", config.CAMPAIGN,
            *FCC_OPTIONS, "-overwrite",
        ],
        work,
        work / "fcc.log",
    )
    require(work / "fdb", "compiled fault database")


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
    run(command, work, work / "compile.log")
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


def write_tcl(
    path: Path,
    binary: Path,
    ids: list[int] | None,
    report: Path,
    raw_body_pc: int,
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

    # Pass the same fixed seed and cycle limit to every program.
    args = " ".join(
        (
            "+permissive",
            "+dramsim",
            f"+dramsim_ini_dir={config.DRAMSIM_INI}",
            f"+max-cycles={config.MAX_CYCLES}",
            f"+ntb_random_seed={config.SEED}",
            f"+loadmem={binary}",
            f"+raw_body_pc={raw_body_pc:016x}",
            # Z01X rejects the built-in tracer's $fwrite after injection.
            # Optional monitor traces use separate plusargs when needed.
            "+disable_instr_trace",
            "+verbose",
            "+permissive-off",
            str(binary),
        )
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
set_config -fsim_mode concurrent
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


def prepare(
    source: Path,
    full: bool = False,
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
    binary = compile_program(source, work)
    raw_body_pc = raw_body_address(binary)
    tcl = work / "fsim.tcl"
    ids = None if full else (selected_ids if selected_ids is not None else fault_ids())
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
    full: bool = False,
    selected_ids: list[int] | None = None,
    result_group: str | None = None,
) -> float:
    """Run the selected faults and return the coverage percentage."""
    ids = None if full else (selected_ids if selected_ids is not None else fault_ids())

    # Never reuse a score from another program, simulator, or fault set.
    digest = evaluation_digest(source, full, ids)
    if result_group:
        work = RUNS / result_group / digest
    else:
        work = RUNS / "full" / digest if full else RUNS / digest
    report = work / "summary.rpt"
    fault_report = work / "faults.rpt"
    marker_report = work / "injection-markers.txt"

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
        # A previous run may have stopped after writing the report but before
        # it removed its private database. Finish that cleanup on a cache hit.
        remove_run_data(work)
        return coverage

    # A new attempt must not inherit either report from an interrupted run.
    report.unlink(missing_ok=True)
    fault_report.unlink(missing_ok=True)
    marker_report.unlink(missing_ok=True)

    # Compile the new program and make its Tcl file.
    work, tcl = prepare(
        source,
        full=full,
        digest=digest,
        selected_ids=ids,
        result_group=result_group,
    )

    # Compile from the same SFF without inheriting earlier results.
    compile_campaign(work)

    # Connect to the campaign and simulate only the chosen faults.
    run(
        ["vc_fcm", "-tcl_script", str(tcl), "-campaign", config.CAMPAIGN, "-connect"],
        work,
        work / "fcm.log",
    )

    # A valid report is not enough if the commit trigger never fired.
    require_injection_marker(work)

    # Keep the detailed results before removing the private database.
    require(fault_report, "detailed fault report")

    # Read the score before removing the large temporary directories.
    coverage = parse_full_coverage(report) if full else parse_coverage(report, len(ids))
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
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--validate-faults", type=int, metavar="N")
    parser.add_argument("--compile", type=Path, metavar="SOURCE")
    parser.add_argument("--prepare", type=Path, metavar="SOURCE")
    parser.add_argument("sources", nargs="*", type=Path)
    args = parser.parse_args()

    # Check paths without compiling or simulating anything.
    if args.check:
        check()
        print("RAW evaluator is ready")
        return 0
    # Run the fault compiler once for all later candidates.
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
        output.write_text("".join(f"{value:.6f}\n" for value in values))
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

    # Score every program in the order MicroGP supplied them.
    values: list[float] = []
    for source in args.sources:
        value = score(
            source,
            full=args.full,
            selected_ids=validation_ids,
            result_group=result_group,
        )
        values.append(value)
        print(f"{source}: {value:.2f}%")

    # MicroGP tells us where to place the fitness values.
    # Use a local file when the variable is absent.
    output = Path(os.environ.get("UGP3_FITNESS_FILE", "fitness.output"))
    output.write_text("".join(f"{value:.6f}\n" for value in values))
    return 0


if __name__ == "__main__":
    # Return a useful status code to the shell and to MicroGP.
    raise SystemExit(main())
