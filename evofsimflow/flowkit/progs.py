"""Building the small RISC-V programs this folder owns: the boot-calibration
probes and the evolved individuals.

One set of flags, the one tests/CMakeLists.txt uses, so a probe or an
individual sees the same crt0 and newlib as every test it is compared with:
otherwise the probes would measure a boot that does not exist.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ARCH = ["-march=rv64imafd", "-mabi=lp64d", "-mcmodel=medany"]
SPECS = ["-specs=htif_nano.specs"]
C_FLAGS = ["-std=gnu99", "-O2", "-Wall", "-Wextra", "-fno-common", "-fno-builtin-printf"]
PROBES = ("probe_main", "probe_start")


def link_flags(linker_script: Path) -> list[str]:
    return ["-static", "-T", str(linker_script)]


def probe_steps(cc: str, linker_script: Path) -> list[list[str]]:
    """Run from the probe folder, with bare file names, so the binaries do not
    depend on where the checkout is.

    probe_main: the full crt0 and newlib, like a real test, stopping on
    entering main. probe_start: no runtime at all, straight from the jump out
    of the boot ROM. Each writes tohost as its very first act."""
    ld = link_flags(linker_script)
    return [
        [cc, *C_FLAGS, *ARCH, *SPECS, *ld, "-o", "probe_main.riscv", "probe_main.c"],
        [cc, *ARCH, "-nostdlib", "-nostartfiles", *ld, "-o", "probe_start.riscv", "probe_start.S"],
    ]


@dataclass
class Scaffold:
    """The fixed code around an evolved body, and how to link the two."""

    boot: str              # newlib | bare
    cc: str
    sbst_dir: Path
    linker_script: Path

    @property
    def companion(self) -> Path:
        return self.sbst_dir / ("boot_bare.S" if self.boot == "bare" else "main_newlib.c")

    def compile_steps(self, body: Path, out: Path) -> list[list[str]]:
        """Assemble to named objects, then link.

        Not for tidiness: given one command, the compiler names its temporary
        object files at random and records those names in the symbol table, so
        the same source produces a different binary every time. Six bytes, and
        enough to make a cache keyed on the binary miss every time.
        """
        wd = out.parent
        extra = [] if self.boot == "bare" else ["-std=gnu99", "-O2", "-fno-common"]
        objs = [wd / "companion.o", wd / "ind.o"]
        steps = [
            [self.cc, *ARCH, *extra, "-c", "-o", str(objs[0]), str(self.companion)],
            [self.cc, *ARCH, "-c", "-o", str(objs[1]), str(body)],
        ]
        link = [self.cc, *ARCH, *link_flags(self.linker_script), "-o", str(out)]
        link += ["-nostdlib", "-nostartfiles"] if self.boot == "bare" else SPECS
        steps.append(link + [str(o) for o in objs])
        return steps

    def check(self) -> None:
        missing = [str(p) for p in (self.linker_script, self.companion) if not p.exists()]
        if missing:
            raise FileNotFoundError("scaffolding is incomplete: " + ", ".join(missing))
