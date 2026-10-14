#!/usr/bin/env python3
"""Generate and compile a few MicroGP programs without running faults."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


# Keep paths independent of the user who cloned the repository.
HERE = Path(__file__).resolve().parent
RUNS = HERE / "runs"


def replace_once(text: str, old: str, new: str) -> str:
    """Replace one known setting and fail if the file changed shape."""
    if text.count(old) != 1:
        raise ValueError(f"expected one setting: {old}")
    return text.replace(old, new)


def small_population(destination: Path) -> None:
    """Write a temporary population that compiles only three programs."""
    text = (HERE / "population.settings.xml").read_text()

    # Keep enough individuals for the tournament selector.
    text = replace_once(text, '<mu value="8" />', '<mu value="2" />')
    text = replace_once(text, '<nu value="8" />', '<nu value="2" />')
    text = replace_once(text, '<lambda value="4" />', '<lambda value="1" />')

    # Three compilations are enough to exercise generation and mutation.
    text = replace_once(
        text,
        '<maximumEvaluations value="12" />',
        '<maximumEvaluations value="3" />',
    )

    # Call the real compiler path, but enable its safe smoke mode.
    evaluator = f"python3 {HERE / 'evaluate.py'} --smoke"
    text = replace_once(
        text,
        '<evaluatorPathName value="python3 evaluate.py" />',
        f'<evaluatorPathName value="{evaluator}" />',
    )
    destination.write_text(text)


def main() -> int:
    """Run MicroGP in a new generated directory and keep its evidence."""
    if shutil.which("ugp3") is None:
        raise FileNotFoundError("ugp3 is not on PATH")

    RUNS.mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="microgp-smoke-", dir=RUNS))

    # MicroGP reads these three files from its current directory.
    shutil.copy2(HERE / "genome.xml", work / "genome.xml")
    shutil.copy2(HERE / "ugp3.settings.xml", work / "ugp3.settings.xml")
    small_population(work / "population.settings.xml")

    # Save the complete output while also showing it to the user.
    result = subprocess.run(
        ["ugp3", "--settingsFile", "ugp3.settings.xml"],
        cwd=work,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
    )
    (work / "ugp3.log").write_text(result.stdout)
    print(result.stdout, end="")
    print(f"smoke directory: {work}")

    if result.returncode:
        print(f"MicroGP failed with status {result.returncode}")
        return result.returncode

    # At least one generated input proves that MicroGP read the genome.
    generated = sorted(work.glob("individual*"))
    if not generated:
        print("MicroGP returned success but generated no programs")
        return 1

    print(f"generated and compiled {len(generated)} programs")
    for path in generated:
        print(f"  {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
