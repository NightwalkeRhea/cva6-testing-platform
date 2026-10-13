"""Reading what the tools leave behind.

Two of the three things worth knowing about a campaign never reach stdout: the
verdict of the fault-free run and the FSDB-invalid error, which a catch{} in
the campaign script swallows. Both are dug out here.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

from flowkit import verdict

# Group rows, not fault classes: they must not be summed with the classes.
GROUP_CODES = {"DG", "NG", "PG", "UG", "AG"}

_CLASS_RE = re.compile(r"^#\s+\S.*?\b([A-Z]{2})\s+(\d+)\s+[\d.]+%")
_TOTAL_RE = re.compile(r"^#\s+Number of Faults:\s+(\d+)")
_COV_RE = re.compile(r"^#\s+Fault Coverage\s+([\d.]+)%")
_TESTCOV_RE = re.compile(r"^#\s+Test Coverage\s+([\d.]+)%")


@dataclass
class Summary:
    total: int = 0
    coverage: float = 0.0
    test_coverage: float = 0.0
    classes: dict[str, int] = field(default_factory=dict)

    @property
    def counted(self) -> int:
        return sum(v for k, v in self.classes.items() if k not in GROUP_CODES)

    def consistent(self) -> str:
        """Empty when the numbers hold together, otherwise what is wrong."""
        if self.total <= 0:
            return "no fault total in the summary"
        if not 0.0 <= self.coverage <= 100.0:
            return f"coverage out of range: {self.coverage}"
        if self.counted != self.total:
            return f"classes sum to {self.counted}, total says {self.total}"
        return ""


def parse_summary(text: str) -> Summary:
    s = Summary()
    in_classes = False
    for line in text.splitlines():
        m = _TOTAL_RE.match(line)
        if m:
            s.total = int(m.group(1))
            continue
        if "Testable Faults:" in line:
            in_classes = True
            continue
        if "Status Groups" in line:
            in_classes = False
        m = _COV_RE.match(line)
        if m:
            s.coverage = float(m.group(1))
            continue
        m = _TESTCOV_RE.match(line)
        if m:
            s.test_coverage = float(m.group(1))
            continue
        if in_classes:
            m = _CLASS_RE.match(line)
            if m:
                s.classes[m.group(1)] = int(m.group(2))
    return s


@dataclass
class CampaignFacts:
    session: Path | None = None
    gold: str = "unknown"       # PASS | FAIL | unknown
    gold_cycles: int = 0
    fsdb_invalid: bool = False
    tasks: int = 0
    task_seconds: list[float] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        """Evidence of failure. Absence of evidence is not one: refusing a
        result because a log could not be found would throw away good work."""
        return self.fsdb_invalid or self.gold == "FAIL"

    @property
    def anomalous(self) -> bool:
        return self.failed or self.gold != "PASS"


def campaign_facts(workdir: Path, clock_ns: float = 1.0) -> CampaignFacts:
    facts = CampaignFacts()
    sessions = sorted((workdir / "fcm.dir").glob("session*"), key=lambda p: p.stat().st_mtime)
    if not sessions:
        return facts
    facts.session = sessions[-1]

    debug = facts.session / "fcm_debug.log"
    if debug.exists():
        facts.fsdb_invalid = "FCM-FSDB-INVALID" in _read(debug)

    # The task tree is tasks/<bucket>/<bucket>/<stage>:<testcase>:taskN/out.log.
    # The toggle simulation is the fault-free run; when it is switched off, any
    # fault-simulation task also runs the good machine and will do instead.
    for pattern in ("tasks/**/tsim:*/out.log", "tasks/**/fsim:*/out.log"):
        logs = sorted(facts.session.glob(pattern))
        if not logs:
            continue
        v = verdict.parse(_read(logs[0]), 0)
        if v.kind != "ERROR":
            facts.gold = v.kind
            facts.gold_cycles = v.cycles(clock_ns)
            break

    csv_path = facts.session / "tasks" / "summary.csv"
    if csv_path.exists():
        with open(csv_path, newline="") as fh:
            for row in csv.reader(fh):
                # Data rows, whose header line does not match them field for
                # field: ,task,worker,host,valid,stage,...,elapsed,...
                if len(row) > 11 and row[5].strip() == "fsim":
                    facts.tasks += 1
                    try:
                        facts.task_seconds.append(float(row[11]))
                    except ValueError:
                        pass
    return facts


def _read(path: Path, limit: int = 4 << 20) -> str:
    try:
        with open(path, errors="replace") as fh:
            return fh.read(limit)
    except OSError:
        return ""
