#!/usr/bin/env python3
"""What uGP3 says about a broken constraints file.

Takes the genome next to this script, breaks it one way at a time, and prints
what comes back. No licence and no EDA tool: uGP3 is GPL and builds from
source in a minute. See docs/ugp3-errors.md for the results and for how to
build it.

    tests/ugp3/catalogue.py [path/to/ugp3]
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FITNESS = "#!/usr/bin/env python3\nimport os\nopen(os.environ.get('UGP3_FITNESS_FILE', 'fitness.out'), 'w').write('1\\n')\n"


def _drop_block(start: str, end: str):
    return lambda t: re.sub(rf"[^\n]*{start}.*?{end}[^\n]*\n", "", t, count=1, flags=re.S)


def _join_after(pattern: str):
    """The line matching pattern, joined with the one after it."""
    return lambda t: re.sub(rf"({pattern})\n", r"\1", t, count=1, flags=re.M)


# A break is a function from the good file to a broken one. No output at all
# is the interesting answer.
CASES = [
    ("01  <macros> block missing", _drop_block("<macros", "</macros>")),
    ("02  minOccurs > maxOccurs on <macros>",
     lambda t: t.replace('maxOccurs="6" minOccurs="4"', 'maxOccurs="4" minOccurs="6"')),
    ("03  averageOccurs outside [min,max]",
     lambda t: t.replace('averageOccurs="5"', 'averageOccurs="99"')),
    ("04  <param ref> names no parameter",
     lambda t: t.replace('<param ref="imm"/>', '<param ref="immediate"/>')),
    ("05  definedType ref names no type",
     lambda t: t.replace('ref="src" name="rs"', 'ref="source" name="rs"')),
    ("06  two parameters share a name",
     lambda t: t.replace('name="imm" minimum', 'name="rs" minimum')),
    ("07  <commentFormat> missing", lambda t: re.sub(r"[^\n]*<commentFormat>[^\n]*\n", "", t)),
    ("08  unexpected element in subSection", lambda t: t.replace("<macros ", "<oops/><macros ")),
    ("09  attribute misspelt: minimun", lambda t: t.replace('minimum="-2048"', 'minimun="-2048"')),
    ("10  hexadecimal range",
     lambda t: t.replace('minimum="-2048" maximum="2047"', 'minimum="0" maximum="0x11111"')),
    ("11  minimum > maximum",
     lambda t: t.replace('minimum="-2048" maximum="2047"', 'minimum="100" maximum="10"')),
    ("12  unclosed tag", lambda t: t.replace("</macros>", "")),
    ("13  expression without a newline", _join_after(r'.*<param ref="imm"/>$')),
]


def run_case(ugp3: str, work: Path, base: str, name: str, brk) -> None:
    for f in list(work.glob("i*.s")) + list(work.glob("BEST*.s")):
        f.unlink()
    (work / "constraints.xml").write_text(brk(base))
    print(f"\n{name}")
    try:
        res = subprocess.run([ugp3], cwd=work, stdin=subprocess.DEVNULL, capture_output=True,
                             text=True, errors="replace", timeout=25)
        out, rc = res.stdout + res.stderr, res.returncode
    except subprocess.TimeoutExpired:
        out, rc = "", None
    errors = [ln for ln in out.splitlines() if re.search(r"\bERROR", ln)]
    individuals = sorted(work.glob("i*.s"))
    if errors:
        print(f"    {errors[-1].split('ERROR:: ', 1)[-1]}")
    elif rc is None:
        print("    NO MESSAGE, and it never returned: killed after 25s.")
    elif individuals and individuals[0].stat().st_size:
        print("    NO MESSAGE. It ran. First individual:")
        for line in individuals[0].read_text(errors="replace").splitlines():
            print(f"      {line}")
    else:
        print(f"    NO MESSAGE and no individual (exit {rc}).")


def main() -> int:
    ugp3 = shutil.which(sys.argv[1] if len(sys.argv) > 1 else "ugp3")
    if not ugp3:
        print(f"ugp3 not found: {sys.argv[1] if len(sys.argv) > 1 else 'ugp3'}", file=sys.stderr)
        return 2
    base = (HERE / "constraints.xml").read_text()
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        for name in ("population.settings.xml", "ugp3.settings.xml"):
            shutil.copyfile(HERE / name, work / name)
        (work / "fitness.py").write_text(FITNESS)
        (work / "fitness.py").chmod(0o755)
        for name, brk in CASES:
            run_case(ugp3, work, base, name, brk)
    return 0


if __name__ == "__main__":
    sys.exit(main())
