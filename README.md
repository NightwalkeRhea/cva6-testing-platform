# CVA6 testing platform
A unified validation platform for CVA6. It brings together CAD Chipyard, CAD Hammer, and MicroGP3 to run experiments with CVA6, so you can use, modify, and connect the flow easily without needing to manage the outer projects as forks or submodules.

## Start here

This is the complete path from a fresh clone to the RAW experiment. You do not
need to open another README to discover the next command.

Run this flow on the CAD server. It needs VCS, Z01X, and the Nangate45 files.

### 1. Clone the platform

```bash
git clone git@github.com:NightwalkeRhea/cva6-testing-platform.git
cd cva6-testing-platform
export CVA6_PLATFORM="$PWD"
```

`CVA6_PLATFORM` is the root of this clone. The commands below use it, so the
clone does not need to be inside your home directory.

### 2. Set up Testyard once

Do not activate the EDA tools yet. Their libraries can interfere with Conda.

```bash
cd "$CVA6_PLATFORM"
activate_proxies

./testyard/build-setup.sh --use-lean-conda --cva6-only
source testyard/env.sh

python -m pip install -e ./hammer
python -c 'import hammer.fsim.vc_zoix as p; print(p.__file__)'

deactivate_proxies
```

The last command from Python must print a path inside this clone's `hammer/`
directory. The setup takes a while, but each clone needs it only once.

`activate_proxies` and `deactivate_proxies` are helpers on the CAD server. Skip
those two lines on a machine that does not need a proxy.

### 3. Build MicroGP once

Keep the EDA tools inactive while CMake runs.

```bash
cd "$CVA6_PLATFORM"

cmake -S microgp/src -B microgp/build \
  -D CMAKE_BUILD_TYPE=RELEASE \
  -D CMAKE_POLICY_VERSION_MINIMUM=3.5

cmake --build microgp/build --target ugp3 --parallel 16

export PATH="$CVA6_PLATFORM/microgp/build/Frontends/ugp3:$PATH"
command -v ugp3
ugp3 --version
```

The last two commands must find and run `ugp3`.

### 4. Enter the environment

Run this block in every new shell or tmux session:

```bash
cd /path/to/cva6-testing-platform
export CVA6_PLATFORM="$PWD"

source testyard/env.sh
deactivate_proxies 2>/dev/null || true
activate_eda_tools

export RISCV="$CVA6_PLATFORM/testyard/.conda-env/riscv-tools"
export PATH="$RISCV/bin:$CVA6_PLATFORM/microgp/build/Frontends/ugp3:$PATH"
hash -r

command -v riscv64-unknown-elf-gcc
command -v vc_fcc
command -v vc_fcm
command -v ugp3
```

Replace `/path/to/cva6-testing-platform` with the path of your clone. All four
checks must print a path.

### 5. Compile the seed

The seed contains the first hand-written RAW test. MicroGP later changes the
instructions inside its `raw_body` function.

```bash
cd "$CVA6_PLATFORM/raw-evolution"
python3 evaluate.py --compile seed.S
```

The command must print the path of `candidate.riscv`.

### 6. Build the fault simulator

Run this in tmux. Fault generation takes about two hours on the current CAD
server. You only need to repeat it after the RTL, strobe, or fault setup changes.

```bash
SEED_BIN="$CVA6_PLATFORM/raw-evolution/runs/compile-check/candidate.riscv"
cd "$CVA6_PLATFORM/testyard/vlsi"
set -o pipefail

make redo-fsim-rtl \
  benchmark=cva6 \
  technology_name=nangate45 \
  toolchain=commercial \
  BINARY="$SEED_BIN" \
  LOADMEM="$SEED_BIN" \
  RISCV="$RISCV" \
  FAULT_MODEL=saf \
  FSIM_CONF_FILE=./fsim/example-fsim-cva6-saf.yml \
  FSIM_STROBE_FILE="$PWD/fsim/strobe/strobe_cva6.sv" \
  HAMMER_D_DEPS= \
  HAMMER_DEPENDENCIES= \
  HAMMER_EXTRA_ARGS="--to_step fgen" \
  2>&1 | tee "$HOME/cva6-raw-fgen.log"

FGEN_STATUS=${PIPESTATUS[0]}
echo "Fault-generation status: $FGEN_STATUS"
```

Do not continue unless the status is zero.

### 7. Create and validate the campaign

The campaign keeps one clean database. Every generated program receives its
own copy, so results cannot leak from one program into another.

```bash
cd "$CVA6_PLATFORM/raw-evolution"
set -o pipefail

make campaign 2>&1 | tee "$HOME/cva6-raw-campaign.log"
CAMPAIGN_STATUS=${PIPESTATUS[0]}
echo "Campaign status: $CAMPAIGN_STATUS"

make validate 2>&1 | tee "$HOME/cva6-raw-validate.log"
VALIDATE_STATUS=${PIPESTATUS[0]}
echo "Validation status: $VALIDATE_STATUS"
```

Both status values must be zero. The validation runs only four faults and
checks that injection begins when the first instruction in `raw_body` retires.

### 8. Run the baseline and choose 500 faults

First, run the seed against all 37,184 RTL faults. This produces the baseline
and tells us which faults are useful for the faster search.

```bash
cd "$CVA6_PLATFORM/raw-evolution"
set -o pipefail

python3 evaluate.py --full seed.S 2>&1 | tee "$HOME/cva6-raw-seed-full.log"
FULL_STATUS=${PIPESTATUS[0]}
echo "Full seed status: $FULL_STATUS"

FULL_REPORT=$(find "$PWD/runs/full" -type f -name faults.rpt -print -quit)
echo "Detailed report: ${FULL_REPORT:-not found}"

python3 select_faults.py "$FULL_REPORT"
wc -l faults.csv
```

Do not run the selector unless the full status is zero and the report exists.
`faults.csv` must contain 501 lines: one header and 500 faults. Keep this file
unchanged during one evolution, so every program sees the same faults.

### 9. Check MicroGP and start the search

```bash
cd "$CVA6_PLATFORM/raw-evolution"

make smoke
make seed
make evolve
```

`make smoke` only generates and compiles programs. It uses dummy scores.
`make seed` and `make evolve` run real fault simulations with Z01X.

The search at RTL develops the method. The final result for the paper must also
evaluate the chosen programs on the synthesized netlist.

For an explanation of the experiment and its files, see
[raw-evolution/README.md](raw-evolution/README.md). You do not need that guide
to complete the setup above.

### How the repositories are managed

Chipyard, Hammer, and MicroGP3 are imported using Git subtree.
Their outer files belong to this repository and can be edited and
committed together.

Chipyard's internal dependencies remain Git submodules. Their paths
are declared in the [.gitmodules](.gitmodules) at the root of this repository.

### Make changes

Just like you're managing a normal repo.
```bash
git pull
# Edit the files.
git add <files-you-changed>
git commit -m "Describe the changes"
git push origin <your-branch-name>
```
Consider that any change inside a retained submodule inside Testyard must be committed and published in that submodule first. Then commit its updated reference here.

If you need to import recent changes from Testyard upstream, you can go with
```bash
git subtree pull --prefix=testyard \
https://github.com/cad-polito-it/chipyard.git \
working/cad_servers --squash
```
### Using the CAD guide

Follow [testyard/README_cad.md](testyard/README_cad.md) for the CAD flow. Chipyard and Hammer
are already included here, so skip its instructions to clone them.

In this platform, Chipyard lives in [testyard/](testyard/) and Hammer lives in
[hammer/](hammer/). Adjust paths in the guide accordingly.


## Licenses

The imported projects retain their original licenses. See the license
files within each project.
