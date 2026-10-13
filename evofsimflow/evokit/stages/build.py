"""Turn what uGP3 wrote into a linkable binary, and into something readable.

The individual file already carries its own prologue and epilogue, because
those live in the genome. The disassembly costs a fraction of a second and is
the only artifact in which what evolved can actually be read, so it is always
produced and kept.
"""
from __future__ import annotations

import shutil

from ..manifest import sha256_file
from ..model import Job, StageResult, Status
from . import PREP, Stage, workdir_for


class BuildStage(Stage):
    name = "build"
    phase = PREP

    def run(self, job: Job) -> StageResult:
        ctx = self.ctx
        wd = workdir_for(ctx.layout, job.ind)
        wd.mkdir(parents=True, exist_ok=True)
        job.workdir = wd

        body = wd / "ind.S"
        try:
            shutil.copyfile(job.ind.source, body)
        except OSError as exc:
            return StageResult(Status.INFRA, f"cannot read the individual: {exc}")

        fp = _fp_insn(body)
        if fp:
            # The core has no FPU: such an instruction traps instead of testing
            # anything, so the program is malformed whatever it scores.
            return StageResult(Status.MALFORMED, f"floating-point instruction '{fp}'")

        binary = wd / "ind.riscv"
        log = wd / "build.log"
        try:
            for argv in ctx.scaffold.compile_steps(body, binary):
                res = ctx.ex.run(argv, cwd=wd, log=log, timeout=300)
                if res.rc == 127:
                    # No compiler is not a bad program. Calling it one would teach
                    # the search that a whole region of the space is bad, for a
                    # reason that has nothing to do with the programs in it.
                    return StageResult(Status.INFRA, f"compiler not found: {argv[0]}")
                if not res.ok:
                    return StageResult(Status.MALFORMED, f"does not assemble: {_tail(log)}")
            if not binary.exists():
                return StageResult(Status.MALFORMED, f"nothing linked: {_tail(log)}")
        finally:
            # The binary is what is kept, not the way there -- and a build that
            # stopped halfway leaves objects behind just the same.
            for obj in wd.glob("*.o"):
                obj.unlink(missing_ok=True)

        job.ind.sha = sha256_file(binary)
        self._disassemble(binary, wd)
        return StageResult(Status.OK, data={"insns": _count_insns(body)})

    def _disassemble(self, binary, wd) -> None:
        """Never fatal: a missing objdump costs a convenience, not a result."""
        ex = self.ctx.ex
        res = ex.run(
            [self.ctx.site.tools.objdump, "-d", "-S", str(binary)],
            cwd=wd,
            log=wd / "ind.dump",
            timeout=120,
        )
        if not res.ok:
            (wd / "ind.dump").unlink(missing_ok=True)


def _count_insns(body) -> int:
    """How long this individual is, known before anything is simulated.

    Genome-agnostic on purpose: an indented line that is not a directive is an
    instruction, whatever the genome calls its macros. It is the length gene,
    it is what an evaluation costs, and it belongs in the journal next to the
    coverage it bought.
    """
    return len(_insn_lines(body))


def _insn_lines(body) -> list[str]:
    """The instruction lines of an individual, by the same rule as _count_insns."""
    try:
        lines = body.read_text(errors="replace").splitlines()
    except OSError:
        return []
    return [ln.strip() for ln in lines
            if ln[:1].isspace() and ln.strip() and not ln.lstrip().startswith((".", "#"))]


def _fp_insn(body) -> str:
    """First F/D mnemonic in the body, or ''. Every RISC-V mnemonic starting
    with 'f' is floating point, except the fences."""
    for ln in _insn_lines(body):
        op = ln.split(None, 1)[0].lower()
        if op.startswith("f") and not op.startswith("fence"):
            return op
    return ""


def _tail(path, lines: int = 6) -> str:
    try:
        text = path.read_text(errors="replace").strip().splitlines()
    except OSError:
        return "no build log"
    return " | ".join(text[-lines:])[:300]
