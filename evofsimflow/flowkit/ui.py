"""What the flow prints, in one voice: a headline, its details, warnings and
the error that ends a command."""
from __future__ import annotations

import sys


class FlowError(Exception):
    """Ends the command with exit status 2: first line, then detail lines."""

    def __init__(self, first: str, *details: str):
        super().__init__(first)
        self.first = first
        self.details = details

    def render(self) -> str:
        lines = [f"ERROR: {self.first}"] + [f"        {d}" for d in self.details]
        return "\n".join(lines)


def say(text: str) -> None:
    print(f">>> {text}", flush=True)


def sub(text: str) -> None:
    print(f"      {text}", flush=True)


def warn(text: str) -> None:
    print(f"WARNING: {text}", file=sys.stderr, flush=True)


def blank() -> None:
    print(flush=True)
