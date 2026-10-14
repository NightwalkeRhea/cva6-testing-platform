"""Settings for the RAW experiment.

Every path starts at the repository root. Change values here only when the
server layout or the experiment changes.
"""
from pathlib import Path

# Find the repository from this file
ROOT = Path(__file__).resolve().parent.parent

# Testyard path that has the simulator, tests, and RISC-V tools
TESTYARD = ROOT / "testyard"

# Hammer puts every file for this CVA6 build below this directory
BUILD = (
    TESTYARD
    / "vlsi/build-nangate45-commercial-cva6"
    / "chipyard.harness.TestHarness.CVA6Config-ChipTop"
)
# We reuse the simulator from the completed RTL campaign
RUNDIR = BUILD / "fsim-rtl-rundir"

# The completed campaign supplies both the fault list and the old results.
# sff file will setup and define the "fault universe" so that Z01X can inject
SFF = RUNDIR / "saf_chiptop0.sff"

#record the old result for every fault, and the selector will read this report when it creates fault.csv
REPORT = RUNDIR / "saf/raw-hazard/fsim_out.rpt"

# SIMV runs the candidate program with injected faults
SIMV = RUNDIR / "simv"

# DAIDIR holds data that the fault compiler needs for SIMV.
DAIDIR = RUNDIR / "simv.daidir"

# Use the compiler that belongs to this Testyard checkout.
RISCV = TESTYARD / ".conda-env/riscv-tools"
CC = RISCV / "bin/riscv64-unknown-elf-gcc"
# Read the address of raw_body from each compiled candidate.
NM = RISCV / "bin/riscv64-unknown-elf-nm"

# The linker places a bare-metal program where the harness expects it.
LINKER = TESTYARD / "tests/htif.ld"

# The simulator reads its DRAM setup from this directory.
DRAMSIM_INI = TESTYARD / "generators/testchipip/src/main/resources/dramsim2_ini"

# Keep every random choice repeatable.
SEED = 946094937

# Z01X may run this many jobs during one evaluation.
WORKERS = 16

# Stop a broken candidate that never exits.
MAX_CYCLES = 10_000_000

# The existing fault database uses this campaign name.
CAMPAIGN = "chiptop0"

# Start with difficult faults, plus detected faults as a control group.
# The values add up to 500.
QUOTAS = {"ND": 250, "NC": 100, "NO": 50, "DD": 100}

# Ignore faults outside the two blocks that handle RAW behavior.
FOCUS = ("i_issue_read_operands", "i_scoreboard")
