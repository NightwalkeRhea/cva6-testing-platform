"""Data the pipeline moves around: one individual, one job, one outcome."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Status(str, Enum):
    """Why a job ended. The three cases are handled differently on purpose.

    OK        a number was produced and validated.
    MALFORMED the individual itself is bad (does not assemble, does not finish).
              Legitimate information: it goes to the GA as a graded low fitness.
    INFRA     something outside the individual broke (tool, licence, disk).
              Says nothing about the individual, so it is retried, never scored.
    """

    OK = "ok"
    MALFORMED = "malformed"
    INFRA = "infra"


@dataclass
class Individual:
    campaign: str
    generation: int
    uid: str            # uGP3 file name, an opaque token
    source: Path        # the file uGP3 wrote
    index: int = 0      # position within its generation, so directories sort
    sha: str = ""       # sha256 of the linked binary, set by the build stage

    @property
    def tag(self) -> str:
        """g007_i042_individualQ: sorts by generation then by order of arrival,
        and still says which individual uGP3 thinks it is."""
        return f"g{self.generation:03d}_i{self.index:03d}_{self.uid}"


@dataclass
class Outcome:
    status: Status = Status.INFRA
    detail: str = ""
    coverage: float = 0.0       # percentage points, as the report states it
    cycles: int = 0
    classes: dict[str, int] = field(default_factory=dict)
    gold: str = ""              # verdict of the fault-free run inside the campaign
    tasks: int = 0
    faults: int = 0             # size of the fault list; sets the free-widening floor
    insns: int = 0              # instructions in the body: the length gene, readable
    seconds: float = 0.0
    signature: int | None = None

    def to_cache(self) -> dict:
        return {
            "status": self.status.value,
            "detail": self.detail,
            "coverage": self.coverage,
            "cycles": self.cycles,
            "classes": self.classes,
            "gold": self.gold,
            "tasks": self.tasks,
            "faults": self.faults,
            "insns": self.insns,
            "seconds": self.seconds,
            "signature": self.signature,
        }

    @classmethod
    def from_cache(cls, d: dict) -> "Outcome":
        return cls(
            status=Status(d["status"]),
            detail=d.get("detail", ""),
            coverage=float(d.get("coverage", 0.0)),
            cycles=int(d.get("cycles", 0)),
            classes=dict(d.get("classes", {})),
            gold=d.get("gold", ""),
            tasks=int(d.get("tasks", 0)),
            faults=int(d.get("faults", 0)),
            insns=int(d.get("insns", 0)),
            seconds=float(d.get("seconds", 0.0)),
            signature=d.get("signature"),
        )


@dataclass
class Job:
    """One individual on its way through the stages."""

    ind: Individual
    workdir: Path
    outcome: Outcome = field(default_factory=Outcome)
    cores: int = 0              # cores held right now
    attempts: int = 0           # infra retries already spent
    cost_hint: float = 0.0      # predicted fsim cost, from the probe; drives LPT
    done: bool = False
    resolved: bool = False      # answered from cache: skip the heavy phase

    @property
    def key(self) -> str:
        return f"{self.ind.campaign}/{self.ind.generation}/{self.ind.uid}"


@dataclass
class StageResult:
    """What a stage hands back. `data` is merged into the job outcome."""

    status: Status
    detail: str = ""
    data: dict = field(default_factory=dict)
