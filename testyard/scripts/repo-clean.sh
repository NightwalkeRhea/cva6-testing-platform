#!/usr/bin/env bash

set -e

# Find Testyard from this script, not from the outer Git repository.
RDIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
GIT_PREFIX=$(git -C "$RDIR" rev-parse --show-prefix)

rm -rf $RDIR/toolchains/libgloss/build.log
rm -rf $RDIR/toolchains/riscv-tools/riscv-isa-sim/build.log
rm -rf $RDIR/toolchains/riscv-tools/riscv-pk/build.log
rm -rf $RDIR/toolchains/riscv-tools/riscv-tests/build.log
(
    pushd $RDIR/generators/constellation
    if [ -d espresso ]
    then
	git submodule deinit -f espresso
    fi
    popd
)
(
    pushd $RDIR/tools/cde
    git config --local status.showUntrackedFiles no
    popd
)
# A standalone Testyard checkout keeps its original cleanup behavior. Inside
# the platform, CVA6 is part of the main flow and must remain initialized.
if [[ -z "$GIT_PREFIX" ]]; then
    (
        if [ -d $RDIR/generators/cva6/src/main/resources/cva6/vsrc ]
        then
            pushd $RDIR/generators/cva6/src/main/resources/cva6/vsrc
            if [ -d cva6 ]
            then
	        git submodule deinit -f cva6
            fi
            popd
        fi
    )
fi
