#!/usr/bin/env python3
"""flow.py: gate-level simulation and VC-Z01X fault simulation of CVA6.

    flow.py conf                     the resolved configuration and build state
    flow.py comp | comp-fast         build the simv
    flow.py par [-w] <test>...       run tests in parallel
    flow.py t2main                   measure when main is entered
    flow.py fsim [-n]                one fault campaign
    flow.py --build <tag> syn        a new synthesis, side by side
    flow.py clean [sim|fsim|all]     throw away the sim/fsim builds

'flow.py <command> -h' for each.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from flowkit.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
