"""flow.py: gate-level simulation and VC-Z01X fault simulation of CVA6.

The tools come from the environment: load VCS, Verdi, VC-Z01X and the RISC-V
toolchain before running anything that simulates. Synthesis is never redone
by a simulating command: every make call passes the build dependencies
empty, so the existing netlist is reused.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import config, paths
from .cmds import MakeFailed, Flow
from .cmds import comp, conf, fsim, par, syn, t2main
from .hier import check_test_path
from .shell import LocalExecutor
from .ui import FlowError

EPILOG = """\
configuration : conf/flow.toml (every value also --set section.key=value)
checkout      : the folder above this one; CY_REPO=<dir> or --repo to override
synthesis     : CY_BUILD=<tag> or --build <tag> selects build-...-<tag>

Test names are relative to tests/ (hello.riscv), or absolute."""


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="flow.py", description=__doc__.split("\n\n", 1)[0].split(": ", 1)[1],
        epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--conf", type=Path, help="configuration file (default conf/flow.toml)")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="override one configuration value; repeatable")
    ap.add_argument("--build", metavar="TAG", help="which synthesis (default: CY_BUILD, or untagged)")
    ap.add_argument("--repo", metavar="DIR", help="the chipyard checkout (default: CY_REPO, or ..)")
    subs = ap.add_subparsers(dest="command", metavar="<command>")
    conf.add(subs)
    comp.add(subs)
    par.add(subs)
    t2main.add(subs)
    fsim.add(subs)
    syn.add(subs)
    return ap


def _join_steps(argv: list[str]) -> list[str]:
    """'--steps --to_step run_vcs' as one value: argparse would read the value,
    which starts with a dash, as an option of its own."""
    out, i = [], 0
    while i < len(argv):
        if argv[i] == "--steps" and i + 1 < len(argv):
            out.append(f"--steps={argv[i + 1]}")
            i += 2
            continue
        out.append(argv[i])
        i += 1
    return out


def main(argv: list[str] | None = None) -> int:
    ap = parser()
    args = ap.parse_args(_join_steps(sys.argv[1:] if argv is None else argv))
    if not args.command:
        ap.print_help()
        return 2
    try:
        cfg = config.load(args.conf, args.set)
        repo = paths.repo_root(args.repo)
        check_test_path(str(repo))
        p = paths.Paths(repo, paths.build_tag(args.build), cfg)
        if not p.vlsi.is_dir():
            raise FlowError(f"vlsi directory not found: {p.vlsi}",
                            "Wrong machine? Otherwise: CY_REPO=/path/to/chipyard flow.py ...")
        flow = Flow(conf=cfg, paths=p, ex=LocalExecutor(new_session=False), dump_mode=cfg.dump)
        return args.func(flow, args)
    except FlowError as exc:
        print(exc.render(), file=sys.stderr)
        return 2
    except MakeFailed as exc:
        return exc.rc
    except KeyboardInterrupt:
        print("\ninterrupted.", file=sys.stderr)
        return 130
    except BrokenPipeError:
        # The reader went away (| head): nothing left to say, and nowhere to say it.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 141
