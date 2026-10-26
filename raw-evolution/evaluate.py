#!/usr/bin/env python3
"""Compile and score RAW programs against the complete fault campaign."""
from __future__ import annotations

import argparse
import csv
import fcntl
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
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

# The two monitor traces written by issue_read_operands.sv.
TRACES = ("dependency.csv", "raw-checker.csv")
# Names for the numbers in rs1_source/rs2_source (see issue_read_operands.sv).
SOURCES = {0: "regfile", 1: "forward", 8: "wait", 9: "pc", 10: "zimm", 11: "imm"}
# Names for the numbers in the fu column, in the order of fu_t in ariane_pkg.sv.
UNITS = ("NONE", "LOAD", "STORE", "ALU", "CTRL_FLOW", "MULT", "CSR",
         "FPU", "FPU_VEC", "CVXIF", "ACCEL")


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
        (config.OBJDUMP, "disassembler"),
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
    # The vc_fcm on PATH is a wrapper that runs $VCS_HOME/bin/vc_fcm; without
    # the Synopsys module loaded it fails with exit code 127.
    require(Path(os.environ.get("VCS_HOME", "/missing-VCS_HOME")) / "bin/vc_fcm",
            "vc_fcm under $VCS_HOME (load the Synopsys module)")


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


def record_timing(work: Path, entry: dict) -> None:
    with (work / "timings.jsonl").open("a") as stream:
        stream.write(json.dumps({"host": os.uname().nodename, "pid": os.getpid(),
                                 **entry}) + "\n")


@contextmanager
def measure(work: Path, stage: str, **details):
    started = time.perf_counter()
    entry = {"stage": stage, "started_utc": datetime.now(timezone.utc).isoformat(),
             **details}
    try:
        yield entry
    except BaseException:
        entry["status"] = "error"
        raise
    else:
        entry["status"] = "ok"
    finally:
        entry["elapsed_seconds"] = time.perf_counter() - started
        record_timing(work, entry)


def record_task_timings(work: Path, since: float = 0.0) -> None:
    """Keep Z01X's per-task times before temporary task files are removed."""
    for summary in (work / "fcm.dir").rglob("summary.csv"):
        # Skip summaries left by an earlier FCM launch, so nothing is counted twice.
        if summary.stat().st_mtime < since:
            continue
        with summary.open(newline="") as stream:
            header = None
            for row in csv.reader(stream):
                fields = [value.strip() for value in row]
                if "elapsed time" in fields and "CPU time" in fields:
                    header = fields
                    continue
                if header is None or len(fields) < len(header):
                    continue
                values = dict(zip(header, fields))
                try:
                    elapsed = float(values["elapsed time"])
                    cpu = float(values["CPU time"])
                except (ValueError, KeyError):
                    continue
                record_timing(work, {"stage": "z01x_task", "summary": str(summary),
                    "task": values.get("task"), "testcase": values.get("testcase"),
                    "faults": values.get("num faults"), "details": values.get("details"),
                    "elapsed_seconds": elapsed, "cpu_seconds": cpu})


def run(command: list[str], cwd: Path, log: Path) -> None:
    """Run one command and save all of its output."""
    # Write to a log because Z01X can print a lot of text.
    env = os.environ.copy()
    if Path(command[0]).name in ("vc_fcc", "vc_fcm"):
        # A shell's connection settings must not redirect a private campaign.
        env = {key: value for key, value in env.items() if not key.startswith("SNPS_FDB_")}
    with measure(cwd, log.stem, executable=str(command[0])) as timing:
        with log.open("w") as stream:
            result = subprocess.run(command, cwd=cwd, env=env,
                                    stdout=stream, stderr=subprocess.STDOUT)
        timing["returncode"] = result.returncode
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


def stop_database(work: Path, database: Path | None = None) -> None:
    """Stop only this database, and refuse to copy or delete it while live."""
    # Replay keeps one database for several faults, so it can live outside work.
    database = (database or work / "fdb").resolve()
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


def run_fcm(work: Path, script: Path, log: Path, database: Path | None = None) -> None:
    """Open this run's database explicitly and close its server afterwards."""
    # FCM is vc_fcm: it opens the fault database, runs our Tcl, and writes reports.
    database = database or work / "fdb"
    started = time.time()
    try:
        run(["vc_fcm", "-fdb_path", str(database.resolve()),
             "-fdb_project", "default", "-campaign", config.CAMPAIGN,
             "-connect", "-stop_server_on_exit", "-tcl_script", str(script)],
            work, log)
    finally:
        # The exit flag only stops servers that this FCM process started.
        try:
            record_task_timings(work, since=started)
        finally:
            stop_database(work, database)


def unused_report_command(report: Path) -> str:
    """Tcl that writes fault counts; an unused database has every testable fault NA."""
    # Stop right here if the copy already has results, before hours of fsim.
    # require_unused() repeats the same check in Python and gives the message.
    return (f"report -campaign {config.CAMPAIGN} -summaryonly "
            f"-report {{{report}}} -overwrite\n"
            f"set stream [open {{{report}}}]\n"
            "set counts [read $stream]\n"
            "close $stream\n"
            "if {![regexp -line {^# Testable Faults:\\s+(\\d+)} $counts -> testable]\n"
            "    || ![regexp -line {^#\\s+Not Attempted\\s+NA\\s+(\\d+)} $counts -> unattempted]\n"
            "    || $testable == 0 || $testable != $unattempted} {\n"
            "    puts stderr \"database contains results or lacks NA counts; not simulating\"\n"
            "    exit 1\n"
            "}\n")


def require_unused(report: Path) -> None:
    """Fail unless the counts were taken before any fault had a result."""
    text = report.read_text(errors="replace")
    testable = TESTABLE.search(text)
    unattempted = NOT_ATTEMPTED.search(text)
    if (not testable or not unattempted or int(testable.group(1)) == 0
            or int(testable.group(1)) != int(unattempted.group(1))):
        raise RuntimeError(f"database contains results or lacks NA counts: {report}; preserving it")


def verify_unused_database(work: Path) -> None:
    """Require all testable faults to be NA, then leave the database stopped."""
    report = work / "unrun-summary.rpt"
    report.unlink(missing_ok=True)
    script = work / "check-unrun.tcl"
    script.write_text(unused_report_command(report))
    run_fcm(work, script, work / "check-unrun.log")
    require_unused(report)


def remove_run_data(work: Path) -> None:
    """Remove database files from an earlier or completed attempt."""
    stop_database(work)

    def ignore_vanished(function, path, error):
        # A stopping server may still delete its own files (postmaster.host); that is fine.
        if not isinstance(error[1], FileNotFoundError):
            raise error[1]

    # Only remove database files after its server has stopped.
    for name in ("fdb", "fcm.dir", "fcm_tsim_fsdb"):
        path = work / name
        if path.is_dir():
            shutil.rmtree(path, onerror=ignore_vanished)


def copy_campaign(work: Path) -> None:
    """Copy the stopped base database; the fault-sim Tcl checks the copy is unused."""
    if not campaign_is_ready():
        raise RuntimeError("campaign is not ready; run make campaign first")

    if (work / "fdb").exists():
        raise RuntimeError(f"database already exists in {work}; preserve this run before retrying")

    # make campaign checked the base once. Copies only read it, so a shared
    # lock lets all candidates copy at the same time; make campaign takes the
    # exclusive lock, so nobody copies while it has the base open.
    with (CAMPAIGN_BASE / "copy.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_SH)
        source = CAMPAIGN_BASE / "fdb"
        # A PID file means a server has the base open; copying it could give a broken copy.
        if (source / "postmaster.pid").exists():
            raise RuntimeError(f"base database is open: {source}; stop its server first")
        if any(path.is_symlink() for path in source.rglob("*")):
            raise RuntimeError(f"database contains symlinks: {source}; refusing raw copy")
        run(["cp", "-a", "--reflink=auto", str(source), str(work / "fdb")],
            work, work / "copy-fdb.log")


def write_linker_script(work: Path) -> Path:
    """Keep normal data below HTIF instead of relocating everything after it."""
    text = config.LINKER.read_text()
    text, count = re.subn(r"(?m)^[ \t]*\.htif\b[^{}]*\{[^{}]*\}", "", text)
    if count != 1 or not re.search(r"\}\s*$", text):
        raise RuntimeError(f"expected one .htif block in {config.LINKER}; refusing to guess its layout")
    # Newlib uses sections such as .data._impure_data; do not leave them orphaned.
    text = text.replace("*(.data)", "*(.data .data.*)")
    end = text.rfind("}")
    section = f"""
    ASSERT(MAX(_end, __heap_end) <= 0x{config.HTIF_ADDRESS:x},
           "RAW memory overlaps HTIF")
    ASSERT(__stack_start + __stack_size <= 0x{config.HTIF_ADDRESS:x},
           "RAW memory overlaps HTIF")
    .htif 0x{config.HTIF_ADDRESS:x} : {{
        KEEP(*(.htif .htif.*))
    }}
    ASSERT(SIZEOF(.htif) <= 0x1000, "RAW HTIF exceeds uncached page")
"""
    path = work / "candidate.ld"
    path.write_text(text[:end] + section + text[end:])
    return path


def compile_program(source: Path, work: Path) -> Path:
    """Link one generated assembly file with the small C harness."""
    binary = work / "candidate.riscv"
    linker = write_linker_script(work)

    # Match the flags used by the other bare-metal CVA6 tests.
    command = [
        str(config.CC),
        "-O2", "-static", "-march=rv64imafd", "-mabi=lp64d", "-mcmodel=medany",
        "-specs=htif_nano.specs", "-T", str(linker),
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
        if "RAW memory overlaps HTIF" in text or "RAW HTIF exceeds uncached page" in text:
            raise InvalidProgram(f"program overlaps the HTIF page; see {log}") from error
        raise
    check_htif(binary)
    return binary


def check_htif(binary: Path) -> None:
    """Make sure tohost sits at HTIF_ADDRESS and nothing else reaches that page."""
    result = subprocess.run([str(config.NM), "--defined-only", str(binary)],
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise RuntimeError(f"nm failed for {binary}: {result.stderr.strip()}")
    symbols = {fields[-1]: int(fields[0], 16)
               for fields in map(str.split, result.stdout.splitlines()) if len(fields) >= 3}
    if symbols.get("tohost") != config.HTIF_ADDRESS:
        raise RuntimeError(f"tohost is not at 0x{config.HTIF_ADDRESS:x} in {binary}")
    if not config.HTIF_ADDRESS <= symbols.get("fromhost", -1) <= config.HTIF_ADDRESS + 0x1000 - 8:
        raise RuntimeError(f"fromhost is outside the uncached HTIF page in {binary}")
    # The linker does not check the heap and stack, so check that they end below the page.
    stack_end = symbols["__stack_start"] + symbols["__stack_size"]
    if max(symbols["_end"], stack_end) > config.HTIF_ADDRESS:
        raise InvalidProgram(f"program, heap, or stack reaches 0x{config.HTIF_ADDRESS:x}")


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


def simulation_args(binary: Path, raw_body_pc: int, max_cycles: int,
                    trace_dir: Path | None = None) -> list[str]:
    # With trace_dir, the monitors write their two CSV traces into that folder.
    traces = [] if trace_dir is None else [
        f"+dependency_trace_file={trace_dir / TRACES[0]}",
        f"+raw_checker_trace_file={trace_dir / TRACES[1]}",
    ]
    return [
        "+permissive", "+dramsim",
        f"+dramsim_ini_dir={config.DRAMSIM_INI}",
        f"+max-cycles={max_cycles}", f"+ntb_random_seed={config.SEED}",
        f"+loadmem={binary}", f"+raw_body_pc={raw_body_pc:016x}",
        "+disable_instr_trace", "+verbose", *traces, "+permissive-off", str(binary),
    ]


def fault_cycle_limit(cycles: int) -> int:
    """Give fault simulation 1.5x the clean run's cycles, within the floor and ceiling."""
    return min(config.MAX_CYCLES, max(config.CYCLE_FLOOR, int(cycles * config.CYCLE_FACTOR)))


def probe(binary: Path, work: Path) -> int | None:
    """Run without faults; return cycles, or None for a failed program."""
    log = work / "probe.log"
    # This clean run also records the traces used for the program's profile.
    clean = work / "clean"
    clean.mkdir(exist_ok=True)
    command = [str(config.SIMV), *simulation_args(
        binary, raw_body_address(binary), config.PROBE_MAX_CYCLES, trace_dir=clean)]
    with measure(work, "probe") as timing:
        with log.open("w") as stream:
            result = subprocess.run(command, cwd=work, stdout=stream, stderr=subprocess.STDOUT)
        timing["returncode"] = result.returncode
    text = log.read_text(errors="replace")
    if "*** FAILED ***" in text:
        return None
    passed = re.search(r"\*\*\* PASSED \*\*\* Completed after\s+(\d+) simulation cycles", text)
    if result.returncode or not passed:
        raise RuntimeError(f"probe did not produce a valid result; see {log}")
    return int(passed.group(1))


def disassemble(binary: Path) -> dict[int, str]:
    """Return {pc: instruction text} for every instruction in raw_body."""
    result = subprocess.run(
        [str(config.OBJDUMP), "-d", "--no-show-raw-insn", "--disassemble=raw_body", str(binary)],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise RuntimeError(f"objdump failed for {binary}: {result.stderr.strip()}")
    body = {}
    for line in result.stdout.splitlines():
        # Instruction lines look like "    80000290:\taddi\tsp,sp,-16".
        match = re.match(r"^\s*([0-9a-f]+):\s+(\S.*)$", line)
        if match:
            body[int(match.group(1), 16)] = " ".join(match.group(2).split())
    if not body:
        raise ValueError(f"objdump found no raw_body instructions in {binary}")
    return body


def open_trace(path: Path):
    """Open a trace CSV, or its compressed .gz copy if the CSV was compressed."""
    if path.exists():
        return path.open(newline="")
    return gzip.open(f"{path}.gz", "rt", newline="")


def write_profile(binary: Path, work: Path, cycles: int) -> None:
    """Summarize, per raw_body instruction, what the clean run did (Layer 1)."""
    body = disassemble(binary)
    # One summary per instruction, in program order.
    profile = {pc: {"index": index, "pc": f"0x{pc:x}", "asm": body[pc], "issued": 0,
                    "ports": Counter(), "stall_cycles": Counter(),
                    "rs1": Counter(), "rs2": Counter(),
                    "rs1_producers": Counter(), "rs2_producers": Counter(),
                    "unattributed_forwarding": Counter()}
               for index, pc in enumerate(sorted(body))}
    # (operand, producer unit, distance, port, stalled?) -> how often it happened.
    events = Counter()
    # Scoreboard ID -> (pc, unit, issue order) of the last instruction issued with it.
    producers = {}
    # Per issue port: operands that had to wait for their producer before issuing.
    waited = defaultdict(set)
    waiting_instruction = {}

    with open_trace(work / "clean" / TRACES[0]) as stream:
        rows = csv.DictReader(stream)
        required = {"rs1_producer_valid", "rs2_producer_valid"}
        if not required.issubset(rows.fieldnames or []):
            raise RuntimeError("trace lacks producer validity fields; rebuild with the fixed "
                               "monitor and collect new traces; old producer IDs are unreliable")
        # The trace has one row per cycle that an instruction tries to issue.
        for row in rows:
            if row["flush"] != "0":
                # This flush discards unissued instructions, not live producers.
                waited.clear()
                waiting_instruction.clear()
                continue
            pc = int(row["pc"], 16)
            port = int(row["issue_port"])
            identity = (pc, int(row["consumer_id"]))
            if waiting_instruction.get(port) != identity:
                waited[port].clear()
                waiting_instruction[port] = identity
            entry = profile.get(pc)
            for operand in ("rs1", "rs2"):
                if row[f"raw_stall_{operand}"] == "1":
                    waited[port].add(operand)
            if entry is not None:
                # Count cycles spent waiting, by reason.
                if "1" in (row["raw_stall_rs1"], row["raw_stall_rs2"]):
                    entry["stall_cycles"]["waiting for a producer"] += 1
                if row["structural_stall"] == "1":
                    entry["stall_cycles"]["unit busy"] += 1
                if row["external_stall"] == "1":
                    entry["stall_cycles"]["pipeline stalled"] += 1

            # Only issued instructions count; flushed ones were thrown away.
            if row["issue_accepted"] != "1":
                continue
            order = int(row["accepted_order"])
            if entry is not None:
                entry["issued"] += 1
                entry["ports"][port] += 1
                for operand in ("rs1", "rs2"):
                    code = int(row[f"{operand}_source"])
                    source = SOURCES.get(code, str(code))
                    entry[operand][source] += 1
                    # "forward" means the value came from an older, unfinished instruction: a RAW.
                    if source != "forward":
                        continue
                    if row[f"{operand}_producer_valid"] != "1":
                        entry["unattributed_forwarding"][f"{operand}: monitor found no producer"] += 1
                        continue
                    producer = producers.get(int(row[f"{operand}_producer_id"]))
                    if producer is None:
                        entry["unattributed_forwarding"][f"{operand}: producer absent from trace"] += 1
                        continue
                    producer_pc, unit, producer_order = producer
                    distance = order - producer_order
                    if distance <= 0:
                        entry["unattributed_forwarding"][f"{operand}: producer not older"] += 1
                        continue
                    name = body.get(producer_pc, "outside raw_body")
                    entry[f"{operand}_producers"][
                        f"0x{producer_pc:x} {name} ({unit}, distance {distance})"] += 1
                    stalled = "stalled" if operand in waited[port] else "no stall"
                    events[(operand, unit, str(distance) if distance < 4 else "4+",
                            port, stalled)] += 1
            # Remember this instruction as the producer for its scoreboard ID.
            code = int(row["fu"])
            producers[int(row["consumer_id"])] = (
                pc, UNITS[code] if code < len(UNITS) else str(code), order)
            waited[port].clear()

    result = {
        "format": 2,
        "clean_cycles": cycles,
        "legend": {
            "issued": "times the instruction issued (raw_body runs 256 times)",
            "rs1/rs2": "where the operand came from when it issued",
            "rs1_producers/rs2_producers": "older instruction that forwarded the operand; "
                                           "distance counts issued instructions between them",
            "events": "every forwarded operand, grouped by operand, producer unit, "
                      "distance, issue port, and whether it had to wait",
            "unattributed_forwarding": "forwarding seen without a reliable producer; "
                                       "excluded from producer names and events",
        },
        "instructions": [{key: dict(value) if isinstance(value, Counter) else value
                          for key, value in entry.items()} for entry in profile.values()],
        "events": [{"operand": operand, "producer_unit": unit, "distance": distance,
                    "port": port, "stall": stalled, "count": count}
                   for (operand, unit, distance, port, stalled), count in sorted(events.items())],
    }
    (work / "profile.json").write_text(json.dumps(result, indent=2) + "\n")

    # Keep the raw traces for replays, but compressed; they can be large.
    for name in TRACES:
        path = work / "clean" / name
        with path.open("rb") as source, gzip.open(f"{path}.gz", "wb") as target:
            shutil.copyfileobj(source, target)
        path.unlink()


def write_tcl(
    path: Path,
    binary: Path,
    ids: list[int] | None,
    report: Path,
    raw_body_pc: int,
    trace_dir: Path | None = None,
    max_cycles: int | None = None,
    check_unused: bool = True,
    jobs: int = 1,
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

    # Replay one fault at a time; ordinary fitness runs leave tracing off.
    args = " ".join(simulation_args(
        binary, raw_body_pc, config.MAX_CYCLES if max_cycles is None else max_cycles,
        trace_dir=trace_dir))

    # Normal runs keep Z01X's default COATS step, which labels each missed fault
    # as never excited (NC) or excited but never seen at the boundary (NO).
    # Replays and validation skip COATS so only the selected faults get results.
    fsim_command = ("fsim -verbose" if trace_dir is None and ids is None
                    else "fsim -verbose -no_coats")
    # Omit -fids only for the final comparison against the complete list.
    if ids is not None:
        selected = " ".join(str(fid) for fid in ids)
        fsim_command += f" -fids {{{selected}}}"

    # Before simulating, save counts that prove this database copy is unused.
    # Doing it in the same FCM launch saves starting the database server twice.
    unused = ("# Prove the database has no results yet.\n"
              + unused_report_command(report.with_name("unrun-summary.rpt"))
              if check_unused else "")

    # Keep one test case, one run of the faults, and one final report.
    path.write_text(
        f"""{unused}# Run this many of Z01X's fault tasks at the same time.
set_config -global_max_jobs {jobs}
# Run ordinary faults in parallel.
set_config -fsim_mode {'serial' if trace_dir is not None else 'concurrent'}
# Print progress every two minutes.
set_config -update_interval 120
# Use the candidate as the only test case.
create_testcases -name raw_candidate -exec {{{config.SIMV}}} -args {{{args}}}
# Run the requested fault set.
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
        # tsim tasks are fault-free runs (COATS, serial mode); they never inject.
        if log.parent.name.startswith("tsim"):
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
    # Every fault ID grouped by its final status (DD, NC, NO, ND, ...).
    by_status = defaultdict(list)
    for fault in faults:
        by_status[fault.status].append(fault.fid)
    return {"format": 1, "total": total, "detected_ids": ids,
            "ids_by_status": dict(sorted(by_status.items())),
            "sff_sha256": hashlib.sha256(config.SFF.read_bytes()).hexdigest(),
            "report_sha256": hashlib.sha256(report.read_bytes()).hexdigest()}


def save_detected_ids(work: Path) -> None:
    evidence = detection_evidence(work / "faults.rpt")
    # Record the simulator build, so a replay never mixes traces from two builds.
    evidence["campaign"] = campaign_fingerprint()
    temporary = work / "detected-ids.json.tmp"
    temporary.write_text(json.dumps(evidence, indent=2) + "\n")
    temporary.replace(work / "detected-ids.json")


def freeze_reference(report: Path) -> None:
    """Freeze a chosen full report; never replace a reference implicitly."""
    markers = report.with_name("injection-markers.txt")
    require(markers, "evidence of injection")
    if INJECTION_MARKER not in markers.read_text():
        raise ValueError(f"no Z01X injection evidence in {markers}")
    # The report must come from the current simulator build; its run saved the
    # build in detected-ids.json. Otherwise an old report would get a new label.
    saved = report.with_name("detected-ids.json")
    require(saved, "the run's detected-ids.json")
    if json.loads(saved.read_text()).get("campaign") != campaign_fingerprint():
        raise ValueError(f"{report} was made by another simulator build; "
                         "freeze a report evaluated with the current build")
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
    jobs: int = 1,
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
    with measure(work, "candidate", source=str(source.resolve()), full=full,
                 workers=jobs) as timing:
        with (work / "evaluation.lock").open("w") as lock:
            with measure(work, "wait_for_lock"):
                fcntl.flock(lock, fcntl.LOCK_EX)
            timing["cache_hit"] = all((work / name).exists() for name in
                ("summary.rpt", "faults.rpt", "injection-markers.txt"))
            timing["coverage"] = score_locked(source, full, ids, result_group, work, digest,
                                              jobs)
            timing["rejected"] = (work / "rejected.txt").exists()
            return timing["coverage"]


def score_locked(source, full, ids, result_group, work, digest, jobs=1) -> float:
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
        # A failing program's traces are useless and can be large.
        shutil.rmtree(work / "clean", ignore_errors=True)
        (work / "rejected.txt").write_text("fault-free program failed; see probe.log\n")
        return 0.0
    # Layer 1: what this program exercises, from the clean run's traces.
    with measure(work, "profile"):
        write_profile(binary, work, cycles)
    write_tcl(tcl, binary, ids, report, raw_body_address(binary),
              max_cycles=fault_cycle_limit(cycles), jobs=jobs)

    # Copy the same stopped database without inheriting earlier results.
    with measure(work, "prepare_fdb"):
        copy_campaign(work)

    # Connect to the campaign and simulate only the chosen faults.
    run_fcm(work, tcl, work / "fcm.log")

    # The Tcl saved fault counts before simulating; they prove the copy was unused.
    require_unused(work / "unrun-summary.rpt")

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

    # Delete the previous batch's scores first. If this batch fails at any point,
    # even while loading the reference, MicroGP must find no file, not old scores.
    output = Path(os.environ.get("UGP3_FITNESS_FILE", "fitness.output"))
    output.unlink(missing_ok=True)

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

    # Share the Z01X jobs among the programs that run together. The faults are
    # split into the same tasks either way, so this changes speed, not results.
    jobs = max(1, config.Z01X_JOBS // min(len(args.sources), config.EVALUATIONS))

    def evaluate_source(source):
        value = score(
            source,
            full=validation_ids is None,
            selected_ids=validation_ids,
            result_group=result_group,
            jobs=jobs,
        )
        extra = complementary_detections(source, reference)
        print(f"{source}: {value:.2f}%, {extra} beyond reference", flush=True)
        return value, extra

    # map keeps scores in the order MicroGP supplied the programs.
    with ThreadPoolExecutor(max_workers=config.EVALUATIONS) as pool:
        values = list(pool.map(evaluate_source, args.sources))

    # Write the scores only after every program succeeded.
    output.write_text("".join(f"{coverage:.12f} {extra}\n" for coverage, extra in values))

    # make evolve sets RAW_MANIFEST so every candidate of the run is listed.
    manifest = os.environ.get("RAW_MANIFEST")
    if manifest and validation_ids is None:
        write_manifest(Path(manifest), args.sources, values)
    return 0


def write_manifest(manifest: Path, sources: list[Path], values: list[tuple]) -> None:
    """Add one line per candidate: its MicroGP name, scores, and result folder."""
    lines = []
    for source, (coverage, extra) in zip(sources, values):
        work = RUNS / "full" / evaluation_digest(source, True)
        rejected = work / "rejected.txt"
        lines.append(json.dumps({
            "individual": source.name,
            "digest": work.name,
            # Relative to raw-evolution/; holds source.S, profile.json, reports, and timings.
            "run_dir": str(work.relative_to(HERE)),
            "coverage": coverage,
            "beyond_reference": extra,
            "rejected": rejected.read_text().strip() if rejected.exists() else None,
        }) + "\n")
    # Lock in case two evaluator processes ever finish together.
    with manifest.open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.write("".join(lines))


if __name__ == "__main__":
    # Return a useful status code to the shell and to MicroGP.
    raise SystemExit(main())
