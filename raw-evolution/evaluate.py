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
TESTABLE = re.compile(r"^# Testable Faults:\s+(\d+)", re.MULTILINE)
NOT_ATTEMPTED = re.compile(r"^#\s+Not Attempted\s+NA\s+(\d+)", re.MULTILINE)

# VCS and Z01X install two launchers for the same fault compiler.
# Compare their release instead of their different launcher paths.
TOOL_RELEASE = re.compile(r"(?:VCS|Z01X)_([^/]+)")


def require(path: Path, label: str) -> None:
    """Fail early when one required file is missing."""
    if not path.exists():
        raise FileNotFoundError(f"{label} not found: {path}")


def check() -> None:
    """Check every file and tool before a long run."""
    # These files come from the platform and its earlier Z01X build.
    for path, label in (
        (config.CC, "compiler"),
        (config.SIMV, "fault simulator"),
        (config.DAIDIR, "simulator data"),
        (config.SFF, "fault list"),
        (config.LINKER, "linker script"),
        (HERE / "faults.csv", "selected faults"),
    ):
        require(path, label)

    # These commands come from the active Synopsys setup.
    for tool in ("vc_fcc", "vc_fcm"):
        if shutil.which(tool) is None:
            raise FileNotFoundError(f"tool not found on PATH: {tool}")


def fault_ids() -> list[int]:
    """Read the 500 fault IDs chosen by select_faults.py."""
    with (HERE / "faults.csv").open(newline="") as stream:
        return [int(row["fid"]) for row in csv.DictReader(stream)]


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


def build_campaign() -> Path:
    """Run the fault compiler once and keep its untouched database."""
    check()

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
    for name in ("fdb", "fcm.dir", "fcm_tsim_fsdb"):
        path = work / name
        if path.is_dir():
            shutil.rmtree(path, onerror=ignore_missing_file)


def clone_campaign(work: Path) -> None:
    """Give one candidate a private copy of the pristine database."""
    if not campaign_is_ready():
        raise RuntimeError("campaign is not ready; run make campaign first")

    # A retry must not inherit partial results from its failed run.
    remove_run_data(work)

    # Reflinks use little extra space when the file system supports them.
    # A normal copy remains safe when reflinks are unavailable.
    run(
        [
            "cp", "-a", "--reflink=auto",
            str(CAMPAIGN_BASE / "fdb"),
            str(work / "fdb"),
        ],
        work,
        work / "clone.log",
    )


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


def write_tcl(path: Path, binary: Path, ids: list[int], report: Path) -> None:
    """Write the small Tcl script that runs one candidate."""
    # Pass the same fixed seed and cycle limit to every program.
    args = " ".join(
        (
            "+permissive",
            "+dramsim",
            f"+dramsim_ini_dir={config.DRAMSIM_INI}",
            f"+max-cycles={config.MAX_CYCLES}",
            f"+ntb_random_seed={config.SEED}",
            f"+loadmem={binary}",
            "+verbose",
            "+permissive-off",
            str(binary),
        )
    )

    # Z01X expects the IDs as one space-separated Tcl list.
    selected = " ".join(str(fid) for fid in ids)

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
# Skip COATS and run only our selected fault IDs.
fsim -verbose -no_coats -fids {{{selected}}}
# Save the coverage that becomes the fitness value.
report -campaign {config.CAMPAIGN} -summaryonly -report {{{report}}} -overwrite
"""
    )


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


def prepare(source: Path) -> tuple[Path, Path]:
    """Compile a candidate and write its Tcl without running faults."""
    # The hash gives the same program the same directory every time.
    digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
    work = RUNS / digest
    # Make the directory once and reuse it on later checks.
    work.mkdir(parents=True, exist_ok=True)

    # Build the binary before we create the Tcl that refers to it.
    binary = compile_program(source, work)
    tcl = work / "fsim.tcl"
    write_tcl(tcl, binary, fault_ids(), work / "summary.rpt")
    return work, tcl


def compile_smoke(source: Path) -> Path:
    """Compile one MicroGP program without preparing or running faults."""
    digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
    work = RUNS / "smoke" / digest
    work.mkdir(parents=True, exist_ok=True)
    return compile_program(source, work)


def score(source: Path) -> float:
    """Run the selected faults and return the coverage percentage."""
    ids = fault_ids()

    # Use the program text as the cache key.
    digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
    work = RUNS / digest
    report = work / "summary.rpt"
    # A finished report means this exact program was scored before.
    if report.exists():
        coverage = parse_coverage(report, len(ids))
        # A previous run may have stopped after writing the report but before
        # it removed its private database. Finish that cleanup on a cache hit.
        remove_run_data(work)
        return coverage

    # Compile the new program and make its Tcl file.
    work, tcl = prepare(source)

    # Start from the same untouched database for every program.
    # This keeps scores independent without rerunning vc_fcc.
    clone_campaign(work)

    # Connect to the campaign and simulate only the chosen faults.
    run(
        ["vc_fcm", "-tcl_script", str(tcl), "-campaign", config.CAMPAIGN, "-connect"],
        work,
        work / "fcm.log",
    )

    # Read the result before removing the large temporary directories.
    coverage = parse_coverage(report, len(ids))
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

    # Score every program in the order MicroGP supplied them.
    values: list[float] = []
    for source in args.sources:
        value = score(source)
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
