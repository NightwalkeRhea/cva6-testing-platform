#!/usr/bin/env bash

# Sets up FireSim for use as a library within Chipyard

set -e
set -o pipefail

CYDIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
GIT_PREFIX=$(git -C "$CYDIR" rev-parse --show-prefix)

cd "$CYDIR"

# Reenable the FireSim submodule
git config --unset "submodule.${GIT_PREFIX}sims/firesim.update" || true
pushd sims/firesim
./build-setup.sh "$@" --library
popd
