"""Turning an outcome into the vector uGP3 compares.

uGP3 compares vectors lexicographically: only the first component that differs
is ever read. That is why the price of a program cannot be a component of its
own. Coverage is continuous and practically never ties, so anything placed
after it is dead -- measured on a real run, an individual won by 0.09 coverage
points while costing 3.2x the instructions, and had colonised the population
one generation later.

So the price lives inside the score:

    [validity, coverage points - price x cycles]

Validity keeps a program that does not run strictly below one that does, and
the broken ones are still ordered among themselves by how far they got: a flat
plateau of zeros gives a genetic algorithm no gradient to climb out of.

`price` is an exchange rate, in coverage points per cycle. At 1e-5 it reads
"ten thousand cycles must buy at least a tenth of a point", which is where the
two individuals above swap places, while a real gain in coverage still wins:
0.07 points bought with 5000 extra cycles is still worth it.
"""
from __future__ import annotations

from .model import Outcome, Status

DIM = 2

# Charged per cycle, in coverage points. Set per campaign; see conf/campaigns/*.toml.
DEFAULT_PRICE = 1e-5


def score(coverage_points: float, cycles: int, price: float) -> float:
    """What an individual is worth: coverage, less what it costs to measure.

    Cycles, not instructions: an evaluation is paid in simulated time, and a
    loop is many cycles of few instructions.

    Coverage is in points, 41.86 and not 0.4186: the unit the report states it
    in, the one the outcome carries and the one the journal keeps. A
    hundredfold here would not change the ranking by coverage at all and would
    quietly divide the price by a hundred, which is a bug that looks like a
    working program.
    """
    return coverage_points - price * cycles


def fitness(outcome: Outcome, price: float) -> list[float]:
    if outcome.status is Status.OK:
        return [1.0, round(score(outcome.coverage, outcome.cycles, price), 6)]
    if outcome.status is Status.MALFORMED:
        # Validity has already separated these from the valid ones, so this
        # slot changes meaning with it: here it is "cycles survived".
        return [0.0, float(outcome.cycles)]
    return [0.0, 0.0]


def format_line(values: list[float]) -> str:
    # Ten significant digits: the default of %g is six, which at a score of
    # ~40 points leaves four decimals and would round two programs a few cycles
    # apart into a tie.
    return " ".join(f"{v:.10g}" for v in values)
