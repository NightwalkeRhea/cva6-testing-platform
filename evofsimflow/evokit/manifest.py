"""Freezing the inputs by reference, and the namespace hash that follows.

The netlist and the elaborated simv are far too big to copy per run, so a run
records their SHAs and paths instead. The same SHAs feed the inputs-hash, which
is both the cache namespace and the "are these two runs comparable" test.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from . import __version__


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def sha256_text(*parts: object) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(str(p).encode())
        h.update(b"\x00")
    return h.hexdigest()


def inputs_hash(parts: dict[str, object]) -> str:
    """Everything that can change a number, and nothing that cannot.

    Named parts, in key order, so adding one is a visible change rather than a
    shifted position. The evolutionary seed is deliberately absent: two runs
    that differ only by it must share the cache, which is what makes reseeding
    cheap. The program itself is not here either: results are keyed on the
    binary's own SHA, which already covers the genome, the boot and the flags.
    """
    flat = []
    for key in sorted(parts):
        value = parts[key]
        if isinstance(value, (list, tuple)):
            value = " ".join(str(v) for v in value)
        flat += [key, value]
    return sha256_text(*flat)[:32]


def hash_parts(site, camp, ws) -> dict[str, object]:
    """What a campaign's numbers depend on, beyond the individual."""
    return {
        "simv": ws.shas["simv"],
        "faults": ws.shas["faults.sff"],
        "tcl": ws.shas["fsim.tcl.in"],
        "strobe": ws.shas["strobe.sv"],
        "probe_simv": ws.shas["probe.simv"],
        "probe_flags": ws.shas["probe.flags"],
        "zoix_campaign": camp.zoix_campaign,
        "sim_seed": site.sim_seed,
        "clock_ns": site.clock_ns,
        "pipeline": camp.pipeline,
        "cycles_factor": camp.fsim_cycles_factor,
        "cycles_floor": camp.fsim_cycles_floor,
        "cycles_cap": camp.fsim_cycles_cap,
        "probe_max_cycles": camp.probe_max_cycles,
        "coats": camp.coats,
        "force_regs": camp.force_regs,
    }


def write_json(path: Path, data: dict) -> None:
    """Atomic write. Never rewrite a file in place: a workspace tree is made of
    hardlinks, and an in-place write would reach through to the original."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(path)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(path)


def build_manifest(site, camp, ws, seed: int, run_id: str) -> dict:
    """One run, one DUT. Everything needed to say what this run measured."""
    return {
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "run_id": run_id,
        "tool_version": __version__,
        "host": os.uname().nodename,
        "repo": str(site.repo),
        "build": str(site.build),
        "cores": {
            "total": site.cores.total,
            "per_ind": site.cores.per_ind,
            "in_flight": site.cores.in_flight,
        },
        "sim_seed": site.sim_seed,
        "campaign": {
            "name": camp.name,
            "dut": camp.dut,
            "config": str(camp.file),
            "workspace": str(ws.root),
            "inputs_hash": camp.inputs_hash,
            "zoix_campaign": camp.zoix_campaign,
            "boot": camp.boot,
            "genome": str(camp.genome_file),
            "population": str(camp.ga_file or ""),
            "cycle_price": camp.cycle_price,
            "evolutionary_seed": seed,
            "inputs": ws.shas,
            "ga": vars(camp.ga),
        },
    }
