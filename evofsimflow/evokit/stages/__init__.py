"""Pipeline stages. The list of them is configuration, so the pipe can be
changed without touching what runs it.

Each stage declares a phase. PREP is cheap and holds one core: it is what
turns a text file into a binary and finds out whether that binary runs at all.
HEAVY is the fault campaign. A generation goes through PREP entirely before any
HEAVY starts, which costs about 2% of the generation and buys two things: the
cycle count that sizes the campaign cap, and an order for the heavy queue.

A new stage is a class plus a line in the campaign config.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..model import Job, StageResult, Status

PREP = "prep"
HEAVY = "heavy"


@dataclass
class Ctx:
    site: object
    camp: object
    ws: object
    layout: object
    ex: object
    scaffold: object


class Stage:
    name = "stage"
    phase = PREP

    def __init__(self, ctx: Ctx):
        self.ctx = ctx

    def run(self, job: Job) -> StageResult:
        raise NotImplementedError


class Pipeline:
    def __init__(self, ctx: Ctx, names: list[str]):
        from .build import BuildStage
        from .fsim import FsimStage
        from .probe import ProbeStage

        registry: dict[str, type[Stage]] = {
            "build": BuildStage,
            "probe": ProbeStage,
            "fsim": FsimStage,
        }
        unknown = [n for n in names if n not in registry]
        if unknown:
            raise ValueError(f"unknown pipeline stage(s): {', '.join(unknown)}")
        self.stages = [registry[n](ctx) for n in names]

    def of_phase(self, phase: str) -> list[Stage]:
        return [s for s in self.stages if s.phase == phase]

    def run_phase(self, job: Job, phase: str, after=None) -> Status:
        """`after(job, stage)` runs between stages and may end the phase early,
        which is how a cached result skips the stages it makes pointless."""
        for stage in self.of_phase(phase):
            result = stage.run(job)
            job.outcome.status = result.status
            if result.detail:
                job.outcome.detail = result.detail
            for key, value in result.data.items():
                setattr(job.outcome, key, value)
            if result.status is not Status.OK:
                return result.status
            if after and after(job, stage):
                return Status.OK
        return Status.OK


def workdir_for(layout, ind) -> Path:
    """Generation and position first, so `ls` reads as a timeline: which
    generation is running, and how many of it are done. uGP3's own name comes
    last, because it is the only way back to its status.xml."""
    return layout.individuals / ind.tag


__all__ = ["Ctx", "Stage", "Pipeline", "PREP", "HEAVY", "workdir_for"]
