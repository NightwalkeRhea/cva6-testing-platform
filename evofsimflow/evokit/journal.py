"""Append-only journal: the only thing that survives a crash, so the only
thing trusted on restart. A directory on disk proves nothing; a line here does.
"""
from __future__ import annotations

import csv
import os
import threading
import time
from pathlib import Path

from .model import Individual, Outcome, Status

FIELDS = [
    "ts",
    "campaign",
    "generation",
    "index",
    "uid",
    "sha",
    "status",
    "insns",
    "coverage",
    "cycles",
    "gold",
    "tasks",
    "seconds",
    "detail",
]


class Journal:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            with open(self.path, "w", newline="") as fh:
                csv.writer(fh).writerow(FIELDS)
                fh.flush()
                os.fsync(fh.fileno())

    def record(self, ind: Individual, outcome: Outcome) -> None:
        row = [
            f"{time.time():.3f}",
            ind.campaign,
            ind.generation,
            ind.index,
            ind.uid,
            ind.sha,
            outcome.status.value,
            outcome.insns,
            f"{outcome.coverage:.4f}",
            outcome.cycles,
            outcome.gold,
            outcome.tasks,
            f"{outcome.seconds:.1f}",
            outcome.detail.replace("\n", " ")[:200],
        ]
        with self._lock:
            with open(self.path, "a", newline="") as fh:
                csv.writer(fh).writerow(row)
                fh.flush()
                os.fsync(fh.fileno())

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        with open(self.path, newline="") as fh:
            return [r for r in csv.DictReader(fh) if r.get("uid")]

    def scored(self) -> dict[str, Outcome]:
        """sha -> outcome, for every individual that produced a real number."""
        out: dict[str, Outcome] = {}
        for row in self.read():
            if row["status"] != Status.OK.value or not row["sha"]:
                continue
            out[row["sha"]] = Outcome(
                status=Status.OK,
                coverage=float(row["coverage"] or 0.0),
                cycles=int(row["cycles"] or 0),
                gold=row["gold"],
                tasks=int(row["tasks"] or 0),
                seconds=float(row["seconds"] or 0.0),
                detail=row["detail"],
            )
        return out

    def keys(self) -> set[str]:
        """campaign/generation/uid of everything already journalled."""
        return {f"{r['campaign']}/{r['generation']}/{r['uid']}" for r in self.read()}
