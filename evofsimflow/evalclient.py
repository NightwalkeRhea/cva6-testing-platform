#!/usr/bin/env python3
"""The evaluator uGP3 launches. It does no work: it asks the run.

uGP3 calls it once per generation with the individual file names as arguments,
waits for it to return, and then reads the fitness file. Two things it must
never do: read standard input (it inherits a terminal, so a read would block for
good), and time out while waiting (a generation legitimately takes hours).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from evokit.campaign import ask, write_fitness  # noqa: E402


def main(argv: list[str]) -> int:
    ids = [a for a in argv[1:] if a]
    if not ids:
        offspring = os.environ.get("UGP3_OFFSPRING", "")
        ids = offspring.split()
    if not ids:
        print("no individuals to evaluate", file=sys.stderr)
        return 2

    sock = os.environ.get("EVOSBST_SOCKET")
    campaign = os.environ.get("EVOSBST_CAMPAIGN")
    if not sock or not campaign:
        print("EVOSBST_SOCKET and EVOSBST_CAMPAIGN must be set by the run", file=sys.stderr)
        return 2

    generation = int(os.environ.get("UGP3_GENERATION", "0"))
    out = Path(os.environ.get("UGP3_FITNESS_FILE", "fitness.output"))

    reply = ask(Path(sock), campaign, Path.cwd(), generation, ids)
    if not reply.get("ok"):
        print(f"evaluation refused: {reply.get('error')}", file=sys.stderr)
        return 1

    values = reply["fitness"]
    if len(values) != len(ids):
        print(f"expected {len(ids)} fitness values, got {len(values)}", file=sys.stderr)
        return 1
    write_fitness(out, values)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
