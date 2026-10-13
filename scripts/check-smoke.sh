#!/usr/bin/env bash
# will check the RAW result and the two trace files produced by the run
set -euo pipefail

if (( $# != 2 )); then
    echo "usage: $0 TRACE_DIR VCS_DIR" >&2
    exit 2
fi

TRACE_DIR=$1
VCS_DIR=$2
DEPENDENCY_TRACE="$TRACE_DIR/dependency.csv"
RAW_TRACE="$TRACE_DIR/raw-checker.csv"
SIM_LOG="$VCS_DIR/output/chipyard.harness.TestHarness.CVA6Config/raw-hazard.log"

# Require a clean result from the RAW test.
grep -q 'RAW failures:[[:space:]]*0' "$SIM_LOG" || {
    echo "RAW test failed; inspect $SIM_LOG" >&2
    exit 1
}

# Remove each CSV header before comparing row counts.
dependency_rows=$(( $(wc -l < "$DEPENDENCY_TRACE") - 1 ))
raw_rows=$(( $(wc -l < "$RAW_TRACE") - 1 ))

if (( dependency_rows <= 0 )); then
    echo "Dependency trace has no data rows" >&2
    exit 1
fi
# Each dependency row must produce one row for each of two operands.
if (( raw_rows != 2 * dependency_rows )); then
    echo "Trace row mismatch: dependency=$dependency_rows raw=$raw_rows" >&2
    exit 1
fi
echo "RAW smoke test passed: dependency=$dependency_rows raw=$raw_rows"
