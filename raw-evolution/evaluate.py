#!/usr/bin/env python3
"""Compile and score RAW programs against the selected faults."""
from __future__ import annotations

import argparse
import csv
import hashlib
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
# Capture the percentage from the Z01X summary.
FITNESS = re.compile(r"Fault Coverage\s+([0-9]+(?:\.[0-9]+)?)%")


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


def compile_program(source: Path, work: Path) -> Path:
    """Link one generated assembly file with the small C harness."""
    binary = work / "candidate.riscv"

    # Match the flags used by the other bare-metal CVA6 tests.
    command = [
        str(config.CC),
        "-O2", "-static", "-march=rv64imafd", "-mabi=lp64d", "-mcmodel=medany",
        "-specs=htif_nano.specs", "-T", str(config.LINKER),
        str(HERE / "harness.c"), str(source.resolve()), "-o", str(binary),
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


def parse_coverage(report: Path) -> float:
    """Read the fault coverage from a Z01X summary."""
    match = FITNESS.search(report.read_text(errors="replace"))
    if not match:
        raise ValueError(f"fault coverage not found in {report}")
    return float(match.group(1))


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


def score(source: Path) -> float:
    """Run the selected faults and return the coverage percentage."""
    # Use the program text as the cache key.
    digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
    work = RUNS / digest
    report = work / "summary.rpt"
    # A finished report means this exact program was scored before.
    if report.exists():
        return parse_coverage(report)

    # Compile the new program and make its Tcl file.
    work, tcl = prepare(source)

    # Build the data for the fault campaign in this directory.
    # Keep the same options as the known manual campaign.
    run(
        [
            "vc_fcc", "-full64", "-daidir", str(config.DAIDIR),
            "-sff", str(config.SFF), "-campaign", config.CAMPAIGN,
            "-uncontrollability", "-prune", "on", "-collapse", "off",
            "-overwrite",
        ],
        work,
        work / "fcc.log",
    )

    # Connect to the campaign and simulate only the chosen faults.
    run(
        ["vc_fcm", "-tcl_script", str(tcl), "-campaign", config.CAMPAIGN, "-connect"],
        work,
        work / "fcm.log",
    )

    # MicroGP maximizes the returned coverage value.
    return parse_coverage(report)


def main() -> int:
    """Handle checks, preparation, or normal scoring."""
    # The helper modes let us test each step on its own.
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--compile", type=Path, metavar="SOURCE")
    parser.add_argument("--prepare", type=Path, metavar="SOURCE")
    parser.add_argument("sources", nargs="*", type=Path)
    args = parser.parse_args()

    # Check paths without compiling or simulating anything.
    if args.check:
        check()
        print("RAW evaluator is ready")
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
