# flow.py — gate-level simulation and fault campaigns for CVA6

`flow.py` drives the Chipyard/Hammer flow for the CVA6 SoC on Nangate45 from
outside: variables on the `make` line and files generated inside the build
directory. No file of the flow is edited.

```
flow.py conf                     the resolved configuration and build state
flow.py comp | comp-fast         build the simv (debug | non-debug)
flow.py par [-w] <test>...       run tests in parallel, simv invoked directly
flow.py t2main                   measure the cycle at which main is entered
flow.py fsim [-n]                one VC-Z01X fault campaign
flow.py --build <tag> syn        a new synthesis, beside the others
flow.py clean [sim|fsim|all]     throw away the sim/fsim builds
```

`flow.py <command> -h` lists the options.

## Before you start

The tools come from the shell: VCS and Verdi, VC-Z01X, Design Compiler for
`syn`, the RISC-V compiler for `t2main`. `flow.py` loads no module, sets no
licence variable and opens no network connection. The checkout is the folder
above `evofsimflow/` (`--repo` or `CY_REPO` otherwise).

`conf/flow.toml` holds what is studied (program, module, strobe, dump, caps)
and the project constants (hierarchy, timing, file names). Any key can be
overridden for one command:

```bash
python3 flow.py --set study.dut=ex --set cycles.fsim=50000000 fsim -n
```

## Syntheses

A tag selects one synthesis among several: `--build v25` or `CY_BUILD=v25`.
Its netlist, generated RTL, waveforms and reports carry the suffix `-v25`;
without a tag, the untagged directories.

`flow.py --build <tag> syn [--cores N]` makes one:

* always tagged, never over a finished netlist; `--cores` at most 16 (Design
  Compiler rejects more and falls back to one core);
* `make syn` with the generator jar declared old (`-o`): the CVA6 wrapper
  rewrites Verilog files the jar depends on, and make would loop;
* the SRAM models it was elaborated with are frozen next to the netlist
  (`sim-models/memories.sv`), since the wrapper regenerates them in its source
  tree; `syn --freeze-only` does only this, for a synthesis run by hand.

The models replace the netlist's empty SRAM stubs name for name.
`conf/cva6-sram-shells.sv` covers the four stubs `tc_sram_<p>_none_0_2` that a
CVA6Config synthesis leaves with no model of that name, and is copied to
`sim-models/sram-shells.sv` when needed. Every build first checks that each
empty `tc_sram*` module has a model or a shell, and stops otherwise: its
outputs would be X and the core would run nothing.

The CVA6 settings (clock, no DfT, `preserve_spare_ff: false`) are in
`vlsi/example-designs/cva6-nangate45-commercial.yml`, passed in `EXTRA_CONFS` by
every `make` call. `preserve_spare_ff: false` turns DC's
`hdlin_preserve_sequential` off: on, CVA6Config does not finish synthesis in a
working day; off, the unloaded TileLink monitor registers and their faults are
not in the netlist.

No simulating command redoes synthesis or regenerates the RTL behind the
netlist: `HAMMER_D_DEPS=` and `HAMMER_DEPENDENCIES=` are passed empty and the RTL
filelist is declared old (`comp --regen-rtl` lifts the latter). The first
simulating command after `syn` generates the simulation-only RTL.

## Building the simv

Every simulating command builds the simv it needs, or reuses it. It rebuilds
when there is none, when a debug simv is needed (`par -w`) and the one on disk
is `fast`, when `initreg.compile` changed, or when nothing records how it was
built (`.flow-build.stamp` in the rundir: `<flavor> <initreg>`). Dump mode and
module are runtime and never cost a rebuild. `--rebuild` forces one,
`--no-build` forbids it. Before a build `csrc/`, `simv.daidir/` and the simv are
removed: a stale daidir makes a simv behave differently.

After every build `smoke.test` (`hello.riscv`) must pass, or the simv is
rebuilt up to `smoke.retries` times: a simv that executes nothing is otherwise
invisible in a coverage report. `fsim` checks its fault-aware simv the same way.

## Register initialisation

Both halves, passed on every build as `SIM_INITREG_ELAB` and
`SIM_INITREG_RUN`; `vlsi/sim.mk` and `vlsi/fsim.mk` then replace the
generators' `+vcs+initreg+*` (unset, upstream behaviour):

* `initreg.compile = random`: `+vcs+initreg+random` at elaboration, the only
  form accepted there and what turns the feature on;
* `initreg.run = 0`: `+vcs+initreg+0` at run time, every register at 0.

Without the first the second is ignored (VCS warns `VICIU`), the netlist starts
full of X, and every run ends at the cycle cap. With both, the plugins'
`force_regs.ucli` changes nothing and costs time: direct runs do not source it
(`run.force_regs = true` brings it back); campaigns through `fsim` keep the
plugin's script.

## par: tests in parallel

```bash
python3 flow.py par hello.riscv other.riscv     # relative to tests/, or absolute
python3 flow.py par -d mydir -j 8               # every *.riscv in tests/mydir
```

The simv runs directly, one workdir per test
(`sim-syn-rundir/par-<date>-<pid>/<nnn>_<test>/`): through make, every run
would load the same `+loadmem` and share one rundir.

| | |
|---|---|
| `PASS` | `*** PASSED ***` or `$finish` in the log (PASSED is printed only with `+verbose`) |
| `FAIL` | `*** FAILED ***` |
| `ERROR` | neither: the simv died, or exited 0 without simulating |
| `MISS` | no such test file |

Each verdict carries the finishing cycle. `--seed N` fixes `+ntb_random_seed`.

`par -w` dumps an FSDB per test into `vlsi/output/<long name>[-tag]/`, with a
debug simv. `dump.mode`: `off`; `lean` (default), the core's top level to
`dump.core_depth` plus the whole `study.dut`; `full`, the whole testbench, huge.
The dump is opened from UCLI with `dump -add <instance> -depth N -fid <file>`:
`scope` + `fsdbDumpvars` ignores the scope and dumps everything. The dumped
module is the one `fsim` strobes by default. Fault campaigns need no dump.

## t2main: where main starts

`fsim --inject-at main` injects just before `main`, skipping the boot, which is
the same for every program. `t2main` measures that cycle with two probes
(`probe/`) that differ only in the C runtime and write `tohost` first thing:

```
probe_start  ->  T0 = t_start + eps     no crt0: straight after the boot ROM
probe_main   ->  T1 = t_main  + eps     full crt0, stops on entering main
```

The host latency `eps` cancels; since `t_start >= RESET_DELAY`,
`t_main >= T1 - T0 + RESET_DELAY`, floored to the thousand and printed as the
value for `timing.t2main_cycles`. It depends on the netlist and the crt0:
remeasure it after a new synthesis.

## fsim: one fault campaign

```bash
python3 flow.py fsim -n                         # print what would run
python3 flow.py fsim --strobe ex --faults alu   # inject in the ALU, observe ex_stage
python3 flow.py fsim --strobe-scope ports --strobe-ports result_o
```

The strobe, the campaign yml and the campaign script are generated from the
shared templates (`vlsi/fsim/strobe/strobe_cva6.sv`, `vlsi/fsim/script/fsim.tcl`)
into `<build>/fsim-gen/`; the templates are never edited.

* **Faults** — `--faults`, default `study.faults`, else `study.dut`: an alias
  from `[hierarchy.aliases]`, a path relative to the core, or a full path from
  `ChipTop.`/`TestDriver.`.
* **Observation** — `--strobe` (modules, comma-separated), `--strobe-scope
  module` (every port: the observability ceiling) or `ports` (`--strobe-ports`
  only: a realistic signature).
* **Injection** — `--inject-at main` (default), `reset`, or a cycle. Strobe and
  injection cycle are compiled into the fault-aware simv.
* **Tasks** — `--task-split auto|on|off`: two tasks per worker when task
  lengths spread (strobe away from the faulted module); `auto` decides.
* **Cap** — `--max-cycles`, default `cycles.fsim`, 3-4x the fault-free run.

Before each campaign the toggle FSDB of the test is removed: Z01X validates it
against the command line only and would reuse a stale one after a rebuild.

`--steps "--to_step run_vcs"` rebuilds the fault-aware simv and runs no
campaign. Only `--to_step` and `--until_step` are accepted: the plugin sets
itself up in its first steps, so `--only_step`, `--from_step` and
`--after_step` stop with an `AttributeError`.

Reports: `vlsi/fsim-output/nangate45/<model>/<long name>-ChipTop[-tag]/`,
`fsim_out_summary.rpt` first.

## Another module

1. Name it in `[hierarchy.aliases]` (a path relative to the core: the
   synthesis keeps the hierarchy) and set `study.dut`, or pass `--faults` and
   `--strobe`.
2. `flow.py fsim` builds the fault-aware simv for it; `par -w` dumps it.
3. For evosbst: a campaign file and a genome that exercises that module (the
   shipped genome is ALU arithmetic only).

## Cycle caps

The flow's own `TIMEOUT_CYCLES` means "forever". Every command imposes its own:
`cycles.par`, `cycles.fsim` (each faulty simulation), `cycles.probe`
(`t2main`), each overridable with `--max-cycles`.

## Pitfalls

* `$finish at simulation time N` is in the global precision (100 fs here), not
  ns; `flow.py` reads the `Time: <n> <unit>` line.
* A `fast` simv cannot dump: `par -w` forces a debug one.
* A word without a dash is a test: `par 10` looks for `tests/10`.
* Hierarchy paths must be dotted Verilog identifiers, and test paths may not
  hold blanks, quotes, braces, `$` or shell characters.

## Where things go

| | |
|---|---|
| simv, daidir, stamp, `par-*/`, `t2main-*/` | `vlsi/build-…[-tag]/<long name>-ChipTop/sim-syn-rundir/` |
| fault-aware simv | `…-ChipTop/fsim-syn-rundir/` |
| generated campaign files | `…-ChipTop/fsim-gen/` |
| frozen SRAM models, shells | `…-ChipTop/sim-models/` |
| waveforms | `vlsi/output/<long name>[-tag]/<nnn>_<test>.fsdb` |
| campaign reports | `vlsi/fsim-output/nangate45/<model>/<long name>-ChipTop[-tag]/` |

`clean` removes the sim and/or fsim parts of the selected build, never the
synthesis, the technology cache or the generated RTL.
