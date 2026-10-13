#!/usr/bin/env bash

# explanation: the script will perform a basic check on your cva6 setup before running a command
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TESTYARD="$ROOT/testyard"
CVA6="$TESTYARD/generators/cva6/src/main/resources/cva6/vsrc/cva6"

# Report every missing command instead of failing at the first one
missing=0
for command_name in make firtool vcs riscv64-unknown-elf-gcc; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
        echo "MISSING command: $command_name" >&2
        missing=1
    fi
done

# Check only files used by the CVA6 flow in our case
for required_file in \
    "$CVA6/core/issue_read_operands.sv" \
    "$TESTYARD/tests/raw-hazard/raw-hazard.riscv" \
    "$TESTYARD/vlsi/fsim/example-fsim-cva6-saf.yml" \
    "$TESTYARD/vlsi/fsim/strobe/strobe_cva6.sv"
do
    if [[ ! -r "$required_file" ]]; then
        echo "MISSING file: $required_file" >&2
        missing=1
    fi
done

# Confirm that this CVA6 contains the fault life-cycle tracer
if ! grep -q 'dependency_trace_file' "$CVA6/core/issue_read_operands.sv"; then
    echo "MISSING tracer in issue_read_operands.sv" >&2
    missing=1
fi

if (( missing != 0 )); then
    exit 1
fi

echo "CVA6 environment check passed"
