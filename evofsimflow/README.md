# evofsimflow

Gate-level simulation, VC-Z01X fault campaigns and evolutionary SBST for the
CVA6 SoC of this Chipyard checkout, on Nangate45. Two programs, Python 3.10+
and nothing outside its standard library:

| | |
|---|---|
| `flow.py` | drives the flow: synthesis side by side, the simv, parallel runs, the injection point, one fault campaign |
| `evosbst.py` | evolves RISC-V self-test programs with uGP3, each scored by a fault campaign of its own |

From a fresh checkout to an evolutionary run on the ALU:

```bash
cd evofsimflow
python3 flow.py --build v1 syn                     # a synthesis of its own, ~40 min
python3 flow.py --build v1 par hello.riscv         # builds and smoke-tests the simv, runs hello
python3 flow.py --build v1 t2main                  # the cycle main starts at: copy it into
                                                   #   conf/flow.toml, timing.t2main_cycles
python3 flow.py --build v1 fsim                    # faults in the ALU, every ALU port observed
cp conf/site.example.toml conf/site.toml           # set paths.build_tag = "v1" and tools.ugp3
python3 evosbst.py selftest                        # offline, no EDA tool
python3 evosbst.py check conf/campaigns/alu.toml   # what is missing, all at once
python3 evosbst.py setup conf/campaigns/alu.toml   # freeze the flow into a workspace
python3 evosbst.py run   conf/campaigns/alu.toml   # evolve; Ctrl-C stops
```

`flow.py conf` shows the configuration and what is built; `flow.py fsim -n`
prints a campaign without running it.

## Before you start

* A Chipyard checkout with the CVA6 SoC synthesised for Nangate45 (or
  `flow.py --build <tag> syn` to make one), and the Hammer of the same flow.
* The tools in the environment you start from: VCS and Verdi, VC-Z01X, Design
  Compiler for `syn`, the RISC-V cross compiler. Nothing here loads a module,
  sets a licence variable or opens a network connection.
* For `evosbst.py` only: uGP3 (MicroGP++ v3, free software, built separately),
  named in `conf/site.toml`.

## The folder

| | |
|---|---|
| `conf/flow.toml` | what `flow.py` studies (program, module, strobe, caps) and the project constants |
| `conf/site.example.toml` | copy to `conf/site.toml` (not versioned): where this machine keeps uGP3, which synthesis, cores |
| `conf/campaigns/*.toml` | one evolutionary experiment each |
| `conf/population.settings.xml` | the uGP3 population settings: mu, lambda, selection, operators |
| `flowkit/` | the flow driver, and what both programs share: running tools, simv flags, log verdicts, campaign files, build paths |
| `evokit/` | the evolutionary loop: uGP3, the pool, the pipeline, journal, cache |
| `genome/` | what an evolved program may contain (`rv64_alu.xml`, the uGP3 constraints), and the fixed code around it |
| `probe/` | the two programs `flow.py t2main` measures the boot with |
| `tests/` | `selftest.py` (offline), `smoke.py` and `lengths.py` (real tools), `ugp3/` |

Outside this folder `flow.py` writes only into the selected build directory
and `vlsi/output`, `vlsi/fsim-output`; `evosbst.py` writes only into
`evosbst-runs/` beside the checkout.

## Documentation

* `docs/flow.md` — `flow.py`: builds, verdicts, waveforms, the injection
  point, fault campaigns, pitfalls.
* `docs/evosbst-usage.md` — running `evosbst.py`: what it needs, a first
  run, the knobs, recipes, costs.
* `docs/evosbst.md` — how `evosbst.py` is built, and why.
* `docs/ugp3-errors.md` — what uGP3 rejects in a constraints file, and the
  three mistakes it accepts in silence.
