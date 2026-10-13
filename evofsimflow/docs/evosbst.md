# evosbst — evolving SBST programs against a fault campaign

Evolutionary search (uGP3) over RISC-V self-test programs, each scored by the
fault coverage a VC-Z01X campaign measures on it. Version 2.0. This file is how
it is built; `docs/evosbst-usage.md` is how to run it.

## Where it sits

```
chipyard/              the flow: RTL, synthesis, the elaborated simulators
  evofsimflow/         flow.py builds and runs them; evosbst.py is this tool
evosbst-runs/          everything runs produce, beside the checkout
```

evosbst **consumes** the flow: it takes the elaborated fault-aware simulator
and the fault list `flow.py fsim` leaves, and never synthesises, edits RTL or
writes inside `chipyard/`. Run data lives outside the checkout, so a clean of
the build tree cannot reach it and a checkout carries no results.

## The loop

```
ugp3  ──writes a whole generation, then blocks──►  evalclient.py
                                                        │ unix socket
                                                        ▼
                                          build ─► probe   │   fsim
                                              phase 1      │  phase 2
                                                        ▼
                                                 one number each
```

uGP3 asks for a whole generation in one call. Phase 1 builds and probes every
individual, one core each; phase 2 runs the campaigns, longest first. The
probe, ~2% of a generation, measures the cycle count that sizes each
campaign's cap and orders the queue.

## The folder

| | |
|---|---|
| `evosbst.py` | the command line, and the lifetime of a run |
| `evalclient.py` | what uGP3 executes: it forwards the request to the run |
| `conf/site.toml` | this machine: paths, tools, core budget |
| `conf/campaigns/*.toml` | one experiment: workspace, caps, genome, price of a cycle |
| `conf/population.settings.xml` | the uGP3 population settings: mu, lambda, selection, operators |
| `genome/rv64_alu.xml` | the uGP3 constraints: what the evolved region may contain |
| `genome/rv64_alu.toml` | the same genome in short form, rendered into constraints |
| `genome/sbst/` | the fixed code around it: `main_newlib.c`, `boot_bare.S` |

`evokit/`, the evolutionary part:

| | |
|---|---|
| `config.py` | TOML in, dataclasses out, and the layout of a run directory |
| `model.py` | individual, job, outcome |
| `genome.py` | the genome and population settings → the XML uGP3 reads, and its checks |
| `campaign.py` | the uGP3 process, the socket that answers it, the two phases |
| `pool.py` | one queue, N slots |
| `stages/` | `build`, `probe`, `fsim`, and the pipe that runs them |
| `workspace.py` | the hardlink snapshot of the flow |
| `reports.py` | reading what the tools leave behind |
| `fitness.py` | an outcome → the vector uGP3 compares |
| `journal.py` | the append-only record, the only thing trusted after a crash |
| `cache.py` | results keyed by the binary, inside an inputs-hash namespace |
| `manifest.py` | what a run measured, and the hash that says so |
| `preflight.py` | everything that must exist before a run, in one pass |
| `sources.py` | which rundir, fault list and campaign script |

Shared with `flow.py`, in `flowkit/`: running programs, the simv command line,
the verdict of a log, the per-individual campaign script, the RISC-V build
flags, the build paths, and a TOML reader for Python below 3.11.

`tests/`: `selftest.py` (no EDA tool), `smoke.py` (the smallest real run),
`lengths.py` (what lengths the genome draws; build only), `ugp3/` (a minimal
genome and `catalogue.py`, which breaks it to see what uGP3 says).

## Four rules

**1. Standard library only.** The machine it runs on has no package manager
reachable. Python 3.10 and later.

**2. One run is one DUT in one process**, holding the cores it is given: no
scheduler. Two DUTs are two processes; the split is decided on the command
line.

**3. The tool never writes inside the flow.** A campaign runs against a
*workspace*, a hardlink snapshot of the fault-aware rundir and of the probe's
simulator made by `setup`: no disk, a few seconds, and a later rebuild of the
flow cannot change what a run measures. Every write is a temporary file and a
rename: in a hardlinked tree, writing in place would reach the original.

**4. What decides a number is frozen and hashed.** The **inputs-hash** covers
both simulators and the probe's flags, the fault list, the campaign script and
the Z01X campaign name, the strobe, the pipeline, the cycle caps, `coats`,
`force_regs`, the clock and the simulation seed: everything that can change a
number, nothing that only changes which programs are tried (genome, GA, price).
It names the cache directory; two runs are comparable exactly when it matches.

## A run on disk

```
evosbst-runs/20260807_alu/
  manifest.json        inputs, shas, seed, budget, version
  journal.csv          one line per individual, appended and fsynced
  state.json           live: generation, progress, cache, k_min
  run.log              lifecycle, core budget changes included
  events.jsonl         the pool's events: retries, give-ups
  input/               what uGP3 was given: constraints, population settings
  ugp3/                uGP3's directory: status.xml, statistics.csv, its log
  individuals/         what is being evaluated now
    processed/g000_i017_individualQ/
                       ind.S ind.riscv ind.dump.gz outcome.json
                       fsim_out_summary.rpt  fsim_out{,_hier}.rpt.gz
  best/                the winner, and best.json
  .sock/eval.sock      while running (0700, this user only)
```

Shared by the runs: `_ws/` the workspaces, `_cache/<inputs-hash>/`, `_log/`.

A finished individual is **renamed** into `processed/`, so `ls individuals/`
shows what is in flight. The journal is written before that rename: on
recovery, anything left under `individuals/` was interrupted and is redone.
Names are `g<generation>_i<position>_<uGP3 name>`, the position counted per
generation (uGP3 may ask for one generation in several calls).

| question | file |
|---|---|
| is it running, how far | `state.json`, `evosbst.py status` |
| every score | `journal.csv` |
| what this run measured | `manifest.json` |
| why this individual scored that | `processed/<tag>/outcome.json` and the reports |
| which program to keep | `best/` |
| what the evolution did | `ugp3/statistics.csv`, `ugp3/status.xml` |

## Cores

`total / per_ind` individuals run at once. A campaign splits into
`max(workers, ceil(faults / 2046))` tasks of unequal length; ten such tasks do
not balance on sixteen workers, sixteen individuals do. Same work, ALU fault
list:

| individuals × cores | machine busy |
|---|---|
| 1 × 16 | 41% |
| 4 × 4 | 78% |
| 8 × 2 | 86% |
| **16 × 1** | **94%** |

Raise `per_ind` only to finish one individual sooner, and not above
`ceil(faults / 2046)` (shown as `k_min`): below it extra workers are free,
above it each adds a whole task.

## Fitness

```
[ validity , coverage points - price × cycles ]
```

compared lexicographically. Validity keeps a program that does not run below
one that does, and grades the broken ones by how far they got. The price is
inside the second component because coverage never ties: a component after it
would never be read. It is recorded in the manifest, and `best/` and `status`
rank by it.

## Not here

* Building the flow: synthesis, elaboration and the fault list come from
  `flow.py`.
* Moving cores between runs: two `evosbst.py cores` commands.
* A DUT resolved through a hierarchy, and replaying one individual alone.
