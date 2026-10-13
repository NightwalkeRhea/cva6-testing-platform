"""Fitness cache keyed by the binary itself, inside an inputs-hash namespace.

The namespace is what makes it safe rather than dangerous: change the simv,
the fault list, the strobe, the cycle cap or the simulation seed and the
namespace changes, so no stale number can be reused by accident.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

from .model import Outcome


class Cache:
    def __init__(self, root: Path, enabled: bool = True):
        self.root = root
        self.enabled = enabled
        self.hits = 0
        self.misses = 0
        self._lock = threading.Lock()
        if enabled:
            self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, sha: str) -> Path:
        return self.root / sha[:2] / f"{sha}.json"

    def get(self, sha: str) -> Outcome | None:
        if not self.enabled or not sha:
            return None
        path = self._path(sha)
        try:
            data = json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            with self._lock:
                self.misses += 1
            return None
        with self._lock:
            self.hits += 1
        return Outcome.from_cache(data)

    def put(self, sha: str, outcome: Outcome) -> None:
        if not self.enabled or not sha:
            return
        path = self._path(sha)
        path.parent.mkdir(parents=True, exist_ok=True)
        # A temporary of its own: two clones can finish at the same time.
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
        with os.fdopen(fd, "w") as fh:
            json.dump(outcome.to_cache(), fh, indent=1, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)

    @property
    def rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0
