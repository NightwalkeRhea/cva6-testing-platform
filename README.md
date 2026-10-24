# CVA6 testing platform
A unified validation platform for CVA6. It brings together CAD Chipyard, CAD Hammer, and MicroGP3 to run experiments with CVA6, so you can use, modify, and connect the flow easily without needing to manage the outer projects as forks or submodules.

The current flow evolves programs that exercise RAW hazards and uses
Z01X to measure their fault coverage.

Fault simulation currently runs on RTL. I'm also working on getting it running
on the synthesized gate-level netlist.

## 1. Install

Use a machine with VCS, Z01X, and the Nangate45 files configured for Testyard.
Install CMake and a C++ compiler, and check that you can access the Git
submodules. Use VCS and Z01X 2025.06-SP2 to match the supplied configuration.

Run the long setup and build steps inside tmux:

```bash
tmux new -s raw
```

First install Miniconda. On Linux x86_64, run the following and accept the
default installation path, `$HOME/miniconda3`. Skip this installation if
`conda --version` already works.

```bash
cd "$HOME"
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
bash Miniconda3-latest-Linux-x86_64.sh
```

Make Conda available in the current shell before running setup:

```bash
export PATH="$HOME/miniconda3/bin:$PATH"
conda --version
```

Continue only when it prints a version. Add the export to `~/.bashrc` if you
want it available in new shells too. If you chose another installation path,
use that path instead.

```bash
git clone git@github.com:NightwalkeRhea/cva6-testing-platform.git
cd cva6-testing-platform
export CVA6_PLATFORM="$PWD"

./testyard/build-setup.sh --use-lean-conda --cva6-only
source testyard/env.sh
./testyard/.conda-env/bin/python -m pip install -e ./hammer &&
./testyard/.conda-env/bin/python -c 'import hammer.fsim.vc_zoix as p; print(p.__file__)'

cmake -S microgp/src -B microgp/build \
  -D CMAKE_BUILD_TYPE=RELEASE \
  -D CMAKE_POLICY_VERSION_MINIMUM=3.5
cmake --build microgp/build --target ugp3 --parallel 16
```

Use the Python in `testyard/.conda-env` to install Hammer, rather than another
Python on your `PATH`. Check the path printed by the import: it must be inside this
clone's `hammer/` directory.

Before building, check these files against your installation. Change only the
paths or tool versions that differ on your machine:

- `testyard/vlsi/env.yml`: tool installation paths and licence settings.
- `testyard/vlsi/example-tools.yml`: tool versions and executable paths.
- `testyard/vlsi/technology/nangate45.yml`: the Nangate45 library path and SRAM generator.

Install OpenROAD and KLayout in separate Conda environments, using the versions
from [README_cad.md](testyard/README_cad.md). Also download the OpenROAD repository:
the Nangate45 SRAM generator needs its library files.

```bash
git clone --recursive https://github.com/The-OpenROAD-Project/OpenROAD.git "$HOME/OpenROAD"
conda create -c litex-hub -c conda-forge -c defaults --no-channel-priority \
  --prefix "$HOME/.conda-openroad" openroad=2.0_7070_g0264023b6
conda create -c litex-hub -c conda-forge -c defaults --no-channel-priority \
  --prefix "$HOME/.conda-klayout" klayout=0.28.5_98_g87e2def28
```

If these are already installed, skip their installation and use their paths below.

## 2. Activate the environment

In each new shell or tmux session, run the block below. Replace the path with
the location of your clone.

```bash
cd /path/to/cva6-testing-platform
export CVA6_PLATFORM="$PWD"
export RISCV="$CVA6_PLATFORM/testyard/.conda-env/riscv-tools"
export OPENROAD="$HOME/OpenROAD"
export PATH="$HOME/miniconda3/bin:$HOME/.conda-openroad/bin:$HOME/.conda-klayout/bin:$RISCV/bin:$CVA6_PLATFORM/microgp/build/Frontends/ugp3:$PATH"
source testyard/env.sh
export PATH="$CVA6_PLATFORM/testyard/.conda-env/bin:$PATH"
hash -r
set -o pipefail

python3 -c 'import sys, hammer.fsim.vc_zoix as p; print(sys.executable); print(p.__file__)'
command -v riscv64-unknown-elf-gcc vcs vc_fcc vc_fcm ugp3 openroad klayout
```

Check that all seven tools print a path. Load the EDA tools as required on your
machine before continuing. Check that Python points to `testyard/.conda-env/bin/`
and Hammer points inside this clone's `hammer/` directory. If you installed Miniconda elsewhere, adjust its
path above. If you prefer to keep the tool exports in `~/.bashrc`, use the
absolute path of your clone.

Detach with **Ctrl+B**, then **D**. Return with `tmux attach -t raw`.
After each command piped through `tee`, check the printed status. Continue only
when it is zero.

## 3. Build the simulator and generate faults

Check generation and compilation before starting the long build, then compile
the provided seed, `seed.S`. `make smoke` uses dummy fitness values.

```bash
cd "$CVA6_PLATFORM/raw-evolution"
make smoke
python3 evaluate.py --compile seed.S
```

Build CVA6 with the strobe and generate the stuck-at fault list. Allow time for
the build and fault generation to finish. Stop at `fgen` for now; run the faults
in the following steps.

```bash
cd "$CVA6_PLATFORM/testyard/vlsi"
make fsim-rtl benchmark=cva6 \
  HAMMER_D_DEPS= HAMMER_DEPENDENCIES= \
  BINARY="$CVA6_PLATFORM/raw-evolution/runs/compile-check/candidate.riscv" LOADMEM=1 HAMMER_EXTRA_ARGS="--to_step fgen" \
  2>&1 | tee "$HOME/cva6-raw-fgen.log"
echo "Status: ${PIPESTATUS[0]}"
```

Keep the arguments above to use the compiled seed, load it into memory, select
the CVA6 strobe and SAF configuration, and stop after generating faults.

Keep `HAMMER_D_DEPS= HAMMER_DEPENDENCIES=` to avoid the observed loop where Make
keeps rebuilding the Hammer dependencies and rerunning Java/Scala indefinitely.

Before continuing, check that the build produced:

- `simv`: the executable simulator.
- `simv.daidir/`: compiled design data used by Z01X.
- `saf_chiptop0.sff`: the faults and their initial classifications.

Look under:

```text
testyard/vlsi/build-nangate45-commercial-cva6/chipyard.harness.TestHarness.CVA6Config-ChipTop/fsim-rtl-rundir/
```

Reuse these outputs when testing different assembly programs.

After changing the RTL, strobe, or fault configuration, replace `fsim-rtl` with
`redo-fsim-rtl` to run the stage again. Keep both empty overrides in that command.

If you rerun Hammer with the same design, strobe, and fault configuration, add
this setting to `testyard/vlsi/fsim/example-fsim-cva6-saf.yml` to reuse the
existing `saf_chiptop0.sff`:

```yaml
fsim.vc_zoix.reuse_generated_faults: true
```

Enable it only after generating the list once. Disable it when you change the
design, strobe, or fault configuration. A missing list, an empty list, or a
`FaultGenerate` recipe will be rejected rather than regenerated.

## 4. Prepare the database and test four faults

Run `make campaign` once to compile the SFF into a Z01X database in
`campaign-base/`. If you already have a matching base, use the same command to
verify it instead of recompiling.

Then validate injection with four testable faults. The `&&` below prevents
validation from starting if campaign preparation fails.

```bash
cd "$CVA6_PLATFORM/raw-evolution"
make campaign 2>&1 | tee "$HOME/cva6-raw-campaign.log" &&
make validate 2>&1 | tee "$HOME/cva6-raw-validate.log"
echo "Status: ${PIPESTATUS[0]}"
```

Continue only with status zero. Keep the validation reports and injection
evidence under `runs/validate/`. A zero status confirms that the evaluator
accepted injection; the percentage applies only to these four faults.

## 5. Run the full seed baseline

Run the seed against all 37,184 faults:

```bash
make seed 2>&1 | tee "$HOME/cva6-raw-seed-full.log"
echo "Status: ${PIPESTATUS[0]}"

find runs/full -name summary.rpt -print
```

Find this evaluation's directory under `runs/full/`; older runs may also be
present. Replace `RUN` below with its directory name, then save its summary,
detailed report, and injection evidence.

```bash
mkdir -p results/axi-seed-baseline
cp -n runs/full/RUN/{source.S,summary.rpt,faults.rpt,detected-ids.json,injection-markers.txt} \
  results/axi-seed-baseline/
```

Choose a new destination if `results/axi-seed-baseline/` already contains a
different baseline. Keep `cp -n` to avoid overwriting reports already saved there.

Freeze the seed's detected IDs as the reference for evolution:

```bash
python3 evaluate.py --set-reference results/axi-seed-baseline/faults.rpt
```

This saves `results/fitness-reference.json` and refuses to overwrite an existing
reference. MicroGP will maximize coverage first, then use detections beyond this
reference to break ties. Keep the reference unchanged throughout a run.

## 6. Preserve the original inputs

Keep the original inputs for the final comparison without monitor guidance.
Every candidate will use the full campaign; do not select a subset.

```bash
mkdir -p results/original-inputs
cp -n seed.S genome.xml population.settings.xml ugp3.settings.xml config.py \
  results/original-inputs/
```

## 7. Replay faults with the monitors

Replay the provided seed with the two example faults, 645 and 648:

```bash
make replay 2>&1 | tee "$HOME/cva6-raw-replay.log"
echo "Status: ${PIPESTATUS[0]}"
```

Wait for the clean seed and both serial replays to finish. Open the printed
`runs/monitor-replay-*` directory and read `comparison.json` for changed fields
and the first mismatch in the sequence of events. Use the saved inputs, logs,
and CSV traces to investigate those differences.

These two replays are diagnostic examples, not evidence that the monitors
explain every fault. Scoring still uses the entire campaign. There is no command
yet that turns the traces into revised tests or templates automatically.

`make evolve` in step 8 already replays each run's best program. To trace
another program by hand, give its assembly file and the IDs you want to inspect:

```bash
make replay CANDIDATE=results/guided-axi-01/BEST_raw FIDS="645 648"
```

Use the candidate's `detected-ids.json` and `faults.rpt` to inspect which faults
it detects or misses. Each replay preserves a copy of that exact program as
`source.S`. It compares the faulty traces with a clean run of the same program.

## 8. Develop with monitor guidance and run evolution

Give the traces and the current genome to the LLM, review its proposed changes,
and apply the changes you want to test. Run `make smoke` after editing the genome.
Keep the seed, campaign, observation points, and scoring policy fixed.

Give each MicroGP run a new name. One command saves the inputs, runs the
search, collects MicroGP's outputs, writes `report.json`, and replays the best
program's missed faults:

```bash
make evolve RUN=guided-axi-01 2>&1 | tee "$HOME/cva6-raw-guided.log"
echo "Status: ${PIPESTATUS[0]}"
```

Everything lands in `results/guided-axi-01/`. Hand that directory to the LLM
step; [raw-evolution/README.md](raw-evolution/README.md#what-each-run-produces)
lists its contents. The command refuses an existing name and refuses to start
while MicroGP files from an older run are still in `raw-evolution/`.

If MicroGP or a replay fails, its files stay where they are for review. After
fixing a failed replay, move `results/RUN/replays` aside and run
`make report RUN=guided-axi-01` to redo only the report and replays.

Run only one evolution in this directory to avoid sharing MicroGP's output
files between runs. Keep the seed, genome, settings, and evaluator unchanged
until it finishes.

Allow up to 16 candidates to run together, sharing 16 Z01X jobs. Before
fault simulation, each candidate must compile and pass a clean simulation.

You can run replay and evolution in separate tmux sessions if resources and
licences permit. In a new session, enter the environment from step 2 and change
to `raw-evolution/` before running either command.

When development is complete, freeze the guided method. Run the final unguided
search from the preserved original genome and seed, without using the monitor
feedback. Use the same campaign, evaluator, and budget for the comparison.
Report the effort spent tuning the guided method separately; one final run
without guidance is a comparison for this case, not proof of general superiority.

## Logs and failed runs

You can follow a log from outside tmux:

```bash
tail -f "$HOME/cva6-raw-guided.log"
```

If an evaluation fails, open the log named in the error. For a simulation failure,
also inspect the task's `out.log` below that run's `fcm.dir/`. Preserve incomplete
runs before retrying. Existing
complete results may be cached; a command returning quickly can be a cache hit.

Keep the reports from successful evaluations; their private databases are
removed after the server stops. For a failed or older run, use Z01X to stop its
server before moving or removing the database. Back up generated results
separately, since Git ignores them.

## Repository layout

- `testyard/`: Chipyard, CVA6 integration, and the CAD flow.
- `hammer/`: Hammer and the Z01X plugin.
- `microgp/`: MicroGP3.
- `raw-evolution/`: seed, genome, evaluator, replay, and experiment settings.

See [raw-evolution/README.md](raw-evolution/README.md) for the measurement
and settings. The broader CAD flow is documented in
[testyard/README_cad.md](testyard/README_cad.md).

Chipyard, Hammer, and MicroGP3 are imported with Git subtree. Dependencies listed
in [.gitmodules](.gitmodules) remain submodules. Publish changes inside a
submodule first, then commit its updated reference here.

The imported projects retain their original licences.

## Troubleshooting

If `make campaign` says the database contains results, do not continue to
validation. Stop that database's server before replacing `campaign-base/`.
Keep the existing simulator and SFF; they do not need rebuilding for this error.

Conda packages can require newer system libraries than your machine provides.
For example, if `config.log` reports `GLIBC_2.34 not found`, check your system
version with `getconf GNU_LIBC_VERSION`. For GLIBC **2.28**, adjust the sysroot
and its kernel headers together:

```bash
conda install -p "$CVA6_PLATFORM/testyard/.conda-env" -c conda-forge \
  "sysroot_linux-64=2.28" "kernel-headers_linux-64<5.14"
```

This changes only the project's Conda environment, not the system libraries.
After it succeeds, resume from the failed build step without recreating the
environment or fetching the submodules again:

```bash
cd "$CVA6_PLATFORM"
source testyard/env.sh
export RISCV="$CVA6_PLATFORM/testyard/.conda-env/riscv-tools"
./testyard/build-setup.sh --use-lean-conda --cva6-only -s 1 -s 2
```

If you hit an error, try loading or unloading the EDA tools as needed on your machine, and check that you installed the required dependencies and versions. If it’s still not resolved, please let me know so I can dive deeper into it.
This repo is still under development, so I can’t guarantee a seamless setup everywhere yet (I’M TRYING), but at least it works on my machine! XD
