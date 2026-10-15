#!/usr/bin/env bash

# exit script if any command fails
set -e
set -o pipefail

RDIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
GIT_PREFIX=$(git -C "$RDIR" rev-parse --show-prefix)

if [ -z "${RISCV}" ] ; then
    ! [ -r "${RDIR}/env.sh" ] || . "${RDIR}/env.sh"
    if [ -z "${RISCV}" ] ; then
        echo "${0}: set the RISCV environment variable to desired install path"
        exit 1
    fi
fi

SRCDIR="${RDIR}/toolchains/riscv-tools"
. "${RDIR}/scripts/build-util.sh"

git -C "$RDIR" config --unset "submodule.${GIT_PREFIX}toolchains/riscv-tools/riscv-openocd.update" || :
module_prepare riscv-openocd
module_run riscv-openocd ./bootstrap
module_build riscv-openocd --prefix="${RISCV}" \
    --enable-remote-bitbang --enable-jtag_vpi --disable-werror
