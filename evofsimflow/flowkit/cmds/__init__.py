"""The commands of flow.py, one module each. Every module has add(subparsers)
and run(flow, args)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..config import FlowConf
from ..paths import Paths
from ..shell import Executor
from ..ui import FlowError


class MakeFailed(Exception):
    """make returned non-zero: the command ends with the same status."""

    def __init__(self, rc: int):
        super().__init__(f"make exited with {rc}")
        self.rc = rc


@dataclass
class Flow:
    conf: FlowConf
    paths: Paths
    ex: Executor
    dump_mode: str
    comp_force: bool = False    # --rebuild
    comp_auto: bool = True      # not --no-build
    regen_rtl: bool = False     # --regen-rtl

    def make(self, argv: list[str]) -> None:
        res = self.ex.run(["make", *argv], cwd=self.paths.vlsi, show=True)
        if res.rc != 0:
            raise MakeFailed(res.rc)

    def require_test(self, name: str) -> str:
        """A test that must exist. Every bare word is taken as a test name, so
        without this a typo would surface only at the end, as a missing simv."""
        from ..hier import check_test_path
        path = check_test_path(self.paths.test(name))
        if not os.path.isfile(path):
            raise FlowError(f"test not found: {path}",
                            f"'{name}' was taken as a test name.",
                            "Options need a dash (e.g. -j 10), and commands are run one at a time.")
        return path


def stamped_dir(parent: Path, prefix: str) -> Path:
    """<prefix>-<date>_<time>-<pid>: the pid keeps two commands started in the
    same second from ever sharing a directory."""
    return parent / f"{prefix}-{datetime.now().strftime('%Y%m%d_%H%M%S')}-{os.getpid()}"


def int_min(minimum: int):
    def parse(text: str) -> int:
        if not text.isdigit() or int(text) < minimum:
            raise ValueError
        return int(text)
    parse.__name__ = f"integer >= {minimum}"
    return parse
