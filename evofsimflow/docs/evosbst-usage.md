# Using evosbst

How to run it. `docs/evosbst.md` is how it is built.

## What it needs

evosbst drives a fault-simulation flow and never builds one: **if you can run
one fault campaign by hand, you have everything it needs.** From the flow,
read and never written:

| | where the flow puts it | what evosbst does with it |
|---|---|---|
| fault-aware simulator and `.daidir` | `<build>/fsim-syn-rundir/simv` | campaigns run against a hardlink snapshot |
| fault list | `…/saf_chiptop0.sff` | copied and hashed |
| campaign script | `…/saf/<test>/fsim.tcl` | rewritten per individual: binary, cycle cap, seed, workers |
| strobe | `…/saf/<test>/strobe.sv` | hashed: a new observation point is a new experiment |
| fault-free simulator | `<build>/sim-syn-rundir/simv` | the probe: one clean run per individual, for its cycle count |
| register-forcing script | `…/force_regs.ucli` | only with `force_regs = true` |
| linker script | `<repo>/tests/htif.ld` | links every individual |

`flow.py fsim` leaves all of them. The tools come from the shell it starts in:
`vc_fcc`, `vc_fcm`, the RISC-V compiler and `objdump`, and `ugp3` (MicroGP++
v3.4.0, built separately: on the PATH, or by absolute path in
`conf/site.toml`). evosbst sets no licence variable, loads no module and opens
no network connection.

`conf/site.toml` (a copy of `conf/site.example.toml`, not versioned) is the
only file with anything about this machine. Everything has a default or is
looked for:

| | if left out |
|---|---|
| `paths.repo` | the folder above this one |
| `paths.build_tag` | `CY_BUILD`, the tag `flow.py --build` takes |
| `paths.build` | `vlsi/build-*/*-ChipTop`; with several, name one by its tag |
| `source.tcl`, `source.sff` | `saf/*/fsim.tcl` and `*.sff` in the rundir |
| `paths.runs` | `evosbst-runs/`, beside the checkout |
| `tools.*` | the PATH |

Two matches are never a guess: it stops and lists them, since the choice
decides what is measured.

## First run

```bash
cd chipyard/evofsimflow
python3 evosbst.py selftest                        # no EDA tool touched
python3 evosbst.py check conf/campaigns/alu.toml   # what is missing, all at once; creates nothing
python3 evosbst.py setup conf/campaigns/alu.toml   # snapshot the flow, print the inputs-hash
python3 evosbst.py run   conf/campaigns/alu.toml   # evolve
```

A run refuses to start on what `check` would report. The **inputs-hash** is
the identity of the experiment: two runs are comparable exactly when it
matches. A run lasts hours or days: start it under `tmux`. It takes exactly
`cores.total` and knows nothing else of the machine.

**Ctrl-C** stops it: running individuals finish (at most 15 minutes), a second
Ctrl-C kills them. `evosbst.py stop <run-id>` does the same from another shell.

## While it runs

```bash
python3 evosbst.py status                          # every run, newest first
python3 evosbst.py status 20260807_alu             # one
ls ../../evosbst-runs/20260807_alu/individuals/    # in flight now
tail -f ../../evosbst-runs/20260807_alu/journal.csv
```

```
=== 20260807_alu ===
  state       : running  pid 31337
  cores       : 16 in 16 slots of 1
  generation  : 4   37/48 done
  fault list  : 18970 faults, k_min=10 (workers are free up to there)
  cache       : 12 hits / 185 lookups
  evaluated   : 185  (181 scored, 4 malformed, 0 infrastructure)
  best        : 41.62%  (score 41.38, gen 3 individualQ, 24295 cycles)
  throughput  : 31.4 individuals/h
```

`journal.csv`, one fsynced line per individual: `ts campaign generation index
uid sha status insns coverage cycles gold tasks seconds detail`.

## The genome and the GA

Two uGP3 files, written by hand, are the experiment:

* `genome/rv64_alu.xml`, the **constraints**: what the evolved code may
  contain. Its comments say what each part is for.
* `conf/population.settings.xml`, the **population settings**: mu, lambda,
  selection, operators, when to stop.

uGP3 reads them as written, with evosbst filling in only how uGP3 reaches it
(evaluator, constraints path, fitness width, concurrency). A key in the
campaign's `[ga]` or a `--set ga.<key>=` overrides the settings file for that
run; the run keeps what it used in `input/`.

uGP3 has no schema and its messages never name a line of your file. Three
mistakes it accepts in silence — a non-decimal integer range, a `minimum` above
its `maximum`, an expression without a trailing newline — evosbst stops on, by
line, in `check` and before a run; `docs/ugp3-errors.md` has the rest.

The shipped genome follows one rule: **a broken program impossible to write,
not expensive to discover.** Register and immediate arithmetic only, no memory
with a computed address, no branch to a computed target, a prologue and
epilogue that guarantee the return. After each instruction the result is
**folded** into an accumulator that the epilogue returns: without it most of
what a program computes is dead before it can be observed.

The other way: `genome/rv64_alu.toml` describes the same genome in a few lines
and evosbst renders it (`[genome] file = "genome/rv64_alu.toml"`); with
`[ga] file = ""` the population settings come from `[ga]` alone.

After changing the length of the body, `tests/lengths.py` shows in a minute
what generation 0 actually draws.

## Knobs

Any key, before or after the subcommand: `--set cores.total=8 --set ga.lambda=24`.

| knob | file | |
|---|---|---|
| `cores.total` | site | the share of the machine |
| `cores.per_ind` | site | cores per individual. **1** is the throughput optimum; more only finishes one individual sooner |
| `lambda`, `maximumGenerations`, … | population settings | or `ga.lambda`, `ga.generations`, … in the campaign or on the command line. Keep lambda a multiple of `cores.total / cores.per_ind` |
| `fitness.cycle_price` | campaign | coverage points per cycle; `0` scores coverage alone |
| `genome.file`, `ga.file` | campaign | which constraints and population settings |
| `genome.boot` | campaign | `newlib` or `bare`, below |
| `pipeline` | campaign | the stages; `["build"]` needs no licence |
| `force_regs` | campaign | source `force_regs.ucli` before probe and campaign; off by default, it changes no number |

## Recipes

**Two DUTs at once.** Two processes, two `tmux` panes, the cores split by hand
(`lsu.toml` stands for your own campaign):

```bash
python3 evosbst.py run conf/campaigns/alu.toml --set cores.total=8
python3 evosbst.py run conf/campaigns/lsu.toml --set cores.total=8
```

Campaigns differing only in the **fault list** share a simulator
(`workspace_mode = "reference"`, different `source.sff`). A different **strobe
or injection point** is compiled in: build the flow for one and `setup` it,
then for the other.

**After a crash or a reboot:** `python3 evosbst.py resume 20260807_alu`.
Unfinished individuals are evaluated again; the run goes on only with the
inputs and `--set` values it started with.

**More or fewer cores without stopping:**

```bash
python3 evosbst.py cores 20260807_alu 8
python3 evosbst.py cores 20260807_alu 16 --per-ind 2
```

Taken within seconds. More cores start individuals at once; fewer let the
extra slots finish what they hold. A new `per_ind` counts from the next
generation; `resume` goes back to `site.toml` and `--set`.

**Same experiment, another trajectory:** `python3 evosbst.py reseed
20260807_alu --seed 7`: same frozen inputs, same cache.

**Is the build still deterministic?** Run something small twice with the same
seed; the second must be all cache hits, seconds instead of an hour. Otherwise
every number after it is suspect. `tests/smoke.py` is that run.

```bash
python3 evosbst.py run conf/campaigns/alu.toml --seed 42 --label check \
  --set ga.lambda=2 --set ga.mu=2 --set ga.nu=2 --set ga.generations=3
```

**One individual again:** its directory has the binary `ind.riscv`, the source
`ind.S`, the disassembly `ind.dump.gz`, `outcome.json` and the reports.

## When something goes wrong

| state | meaning | |
|---|---|---|
| `ok` | a validated number | scored, cached |
| `malformed` | the individual: does not assemble, does not finish | scored low, cached |
| `infra` | outside it: licence, disk, tool | retried, never scored or cached |

An `infra` job keeps its slot and retries until `infra.retries` and
`infra.stall_seconds` (half an hour) are both spent; `status` shows `DEGRADED`
with the reason. Infra failures are about the machine, not the search.

A campaign result is believed only if the fault-free run inside it passed, the
toggle dump was accepted and the fault classes add up to the total: a job fails
on proof of failure, never on absence of proof.

## What a run costs

```
cycles ≈ 19 170 + 27.2 × instructions        (straight-line programs)
```

The intercept is the boot, about half of a typical individual. The slope, an
IPC of 0.04, is the CVA6 configuration leaving both caches unused
(`NrCachedRegionRules: 0`): every fetch goes to the bus, so a loop buys almost
nothing. At 16 cores and `per_ind = 1`: 96–137 s per individual, 26–37
individuals/h, about two days for 30 generations of 48, ~150 KB of disk each.

Not applied, both needing the fault-aware simulator rebuilt: the caches on
(with a loop under 512 instructions, ~6.6x estimated) and the `bare` boot:

| `genome.boot` | `newlib` | `bare` |
|---|---|---|
| C runtime | crt0 and newlib, 12 528 cycles | none |
| fixed cost before the body | ~19 200 cycles | ~6 600 (reset and boot ROM) |
| needs | nothing: the calibrated one | the injection cycle remeasured, the simulator rebuilt |
