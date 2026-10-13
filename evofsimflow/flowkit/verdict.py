"""What a simulation log says: the verdict, and when it ended.

The testbench prints "*** PASSED ***" only with +verbose; without it a passing
test prints nothing and just calls $finish. "*** FAILED ***" is always printed,
and the TestDriver's only $finish is the one on the success path. Hence:

    FAIL   "*** FAILED ***" in the log
    PASS   "*** PASSED ***" or "$finish" in the log
    ERROR  neither: the simulator died (exit != 0), or it exited 0 without
           ever simulating (a rejected plusarg, an empty script). Not a PASS:
           a program with this verdict is discarded, never scored.

A run that hits +max-cycles prints "*** FAILED *** (timeout) after N
simulation cycles" and then calls $finish like the success path does, so
"there is a $finish" is not enough for anything that measures time.

"$finish at simulation time N" is in the global precision, not in ns. The
"Time: <n> <unit>" line states its unit, so that is the one read.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_UNIT_NS = {"fs": 1e-6, "ps": 1e-3, "ns": 1.0, "us": 1e3, "ms": 1e6}
_NUM_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?")
_COUNT_RE = re.compile(rb"after\s+(\d+)\s+simulation cycles")


@dataclass
class Verdict:
    kind: str                     # PASS | FAIL | ERROR
    rc: int
    end_ns: float | None = None   # from the last "Time:" line, only if $finish was reached
    last_ns: float | None = None  # the same line, whether or not $finish was reached
    counted: int | None = None    # the testbench's own "after N simulation cycles"
    timed_out: bool = False

    @property
    def end_text(self) -> str:
        """The end time as the flow prints it: ns, to a tenth. Empty when the
        run never reached $finish."""
        return "" if self.end_ns is None else f"{self.end_ns:.1f}"

    def end_cycles(self, clock_ns: float) -> int | None:
        """Cycles from t=0, counted from the printed end time."""
        if self.end_ns is None:
            return None
        return int(float(self.end_text) / clock_ns)

    def cycles(self, clock_ns: float) -> int:
        """The testbench's own count when it printed one, else the last time
        seen. What the evolutionary loop charges an individual."""
        if self.counted is not None:
            return self.counted
        if self.last_ns is None:
            return 0
        return int(self.last_ns / clock_ns)

    @property
    def detail(self) -> str:
        if self.kind == "FAIL":
            return "cycle cap reached" if self.timed_out else "testbench reported a failure"
        if self.kind == "ERROR":
            if self.rc != 0:
                return f"simulator exit={self.rc}"
            return "exit=0 with no verdict: it never simulated"
        return ""

    def line(self, name: str, clock_ns: float) -> str:
        """The one-line verdict the flow prints and writes next to a run."""
        if self.kind in ("PASS", "FAIL"):
            cyc = self.end_cycles(clock_ns)
            extra = f"  [{cyc} cycles]" if cyc is not None else ""
            return f"{self.kind:<5} {name}{extra}"
        if self.rc != 0:
            return f"ERROR {name}  (simv exit={self.rc})"
        return f"ERROR {name}  (exit=0 but no verdict: it never simulated)"


def parse(data: bytes | str, rc: int) -> Verdict:
    if isinstance(data, str):
        data = data.encode("utf-8", "replace")
    if b"*** FAILED ***" in data:
        kind = "FAIL"
    elif b"*** PASSED ***" in data or b"$finish" in data:
        kind = "PASS"
    else:
        kind = "ERROR"
    v = Verdict(kind=kind, rc=rc, timed_out=b"(timeout) after" in data)
    v.last_ns = _time_ns(data)
    if b"$finish" in data:
        v.end_ns = v.last_ns
    m = _COUNT_RE.search(data)
    if m:
        v.counted = int(m.group(1))
    return v


def parse_file(log: Path, rc: int) -> Verdict:
    try:
        data = log.read_bytes()
    except OSError:
        data = b""
    return parse(data, rc)


def _time_ns(data: bytes) -> float | None:
    """The last 'Time: <n> <unit>' line, in ns."""
    value, unit = None, None
    for raw in data.split(b"\n"):
        if raw.startswith(b"Time: "):
            fields = raw.decode("ascii", "replace").split()
            value = fields[1] if len(fields) > 1 else ""
            unit = fields[2] if len(fields) > 2 else ""
    if unit not in _UNIT_NS:
        return None
    m = _NUM_RE.match(value or "")
    number = float(m.group(0)) if m else 0.0
    return number * _UNIT_NS[unit]
