# RAW evolution

Some small RISC-V programs to stress how cva6 handles RAW hazards. (MicroGP changes the programs, Z01X runs the faults and returns their coverage)

As for the simulation, the cva6 is built once (because it's slow) and then, every candidate reuses the same simulator, the same fault list, and the clean fault database.

## What the experiment measures

The faults are inside the scoreboard and the block that reads operands.

`raw_body` is the assembly function that we evolve. The C harness calls it 256
times, with inputs from 0 through 255, and combines its return values into one
signature. MicroGP changes the instructions inside this function. (so the boot
code, the C harness, and the code that prints the signature stay the same.)

Injection starts when the first instruction in `raw_body` retires. The evaluator
reads the 64-bit address of `raw_body` from each binary. The strobe checks both
commit ports and rejects a run if injection never starts.

The strobe observes the ports of `CVA6CoreBlackbox`. In practice, the useful
output is its AXI traffic. A fault counts as detected only when it changes the
value or timing of that traffic.

Running all 37,184 faults for every candidate would make the search too slow.
We therefore run the seed against all faults once, then `select_faults.py`
chooses a smaller set of 500 from that report. It chooses 250 faults that the
seed did not detect (ND), 100 that it did not control (NC), 50 that it did not
observe (NO), and 100 that it detected (DD). For now, we're using a fixed random seed to keep the list unchanged for every candidate.

Fitness is the percentage of the fixed 500 faults that Z01X detects. Every
candidate gets a private copy of the clean database, so it cannot reuse results
from another candidate.

Fault runs disable CVA6's ordinary instruction trace. Z01X treats its `$fwrite`
calls as illegal after injection. Optional monitor traces are independent and
remain disabled unless a separate replay requests them explicitly.

### Small numerical example

Suppose `raw_body` starts at `0x10200`. Its first instruction retires on commit
port 1 at cycle 12,340, so injection starts at that cycle. One injected fault
changes a bit inside the scoreboard at cycle 12,350.

- If the AXI traffic stays unchanged, Z01X does not count the fault as detected.
- If the normal run writes `0x25` at cycle 12,400, but the faulty run writes
  `0x24`, Z01X counts the fault as detected.
- Z01X also counts it if both runs write `0x25`, but the faulty run writes it at
  cycle 12,401. We treat a change in timing as visible behavior.

If Z01X detects 96 of the fixed 500 faults, the fitness is
`96 / 500 * 100 = 19.2%`. A candidate that detects 101 faults gets `20.2%`.
The addresses, cycles, and values above are only an example.

## Folder Contenr

- `seed.S` is the hand-written starting program.
- `genome.xml` describes the RAW patterns that MicroGP can add or change.
- `evaluate.py` compiles a program and runs its faults.
- `select_faults.py` chooses the fixed set of 500 faults.
- `config.py` keeps the paths, quotas, seed, and limits in one place.

The genome contains the complete `seed.S` as its fixed base. Generation zero is
therefore the exact seed, with no random instructions added before evaluation.

## Before you start

Run this flow on the CAD server. You need:

- a working `testyard/env.sh` in this checkout;
- access to VCS and Z01X;
- access to the Nangate45 files used by Testyard;
- CMake and a C++ compiler.

For a fresh clone, first follow **Set up Testyard** in the main README. If you've done it, you can proceed with the rest of te commands below =)

## Build MicroGP once

Do this before activating the EDA tools. Some EDA libraries can break the
system copy of CMake.

```bash
cd "$HOME/cva6-testing-platform"

cmake \
  -S microgp/src -B microgp/build -D CMAKE_BUILD_TYPE=RELEASE \
  -D CMAKE_POLICY_VERSION_MINIMUM=3.5

cmake --build microgp/build --target ugp3 --parallel 16

export PATH="$PWD/microgp/build/Frontends/ugp3:$PATH"
command -v ugp3
ugp3 --version
```
The last two commands must find and
run `ugp3`.

## Enter the working environment

Run these commands in every new shell or tmux session:

```bash
cd "$HOME/cva6-testing-platform"

source testyard/env.sh
deactivate_proxies
activate_eda_tools

export RISCV="$PWD/testyard/.conda-env/riscv-tools"
export PATH="$RISCV/bin:$PWD/microgp/build/Frontends/ugp3:$PATH"
hash -r

command -v riscv64-unknown-elf-gcc
command -v vc_fcc
command -v vc_fcm
command -v ugp3
```

Of course, don't continue if one of those four commands prints nothing.

## Check the existing CVA6 build

The candidate loop expects a fault-aware simulator and its SFF file:

```bash
cd "$HOME/cva6-testing-platform/raw-evolution"

python3 evaluate.py --compile seed.S
make check
```

`make check` will print `RAW evaluator is ready` when the required files and tools
are present.

### Troubleshooting

If it reports that `simv`, `simv.daidir`, or `saf_chiptop0.sff` is
missing, build the CVA6 fault simulator before continuing. That build is slow,
but it only needs to happen when the RTL, strobe, or fault setup changes.

## Prepare and validate the campaign

Use tmux for this part. The fault compiler is going to take a while...

```bash
cd "$HOME/cva6-testing-platform/raw-evolution"
set -o pipefail

make prepare
make campaign 2>&1 | tee "$HOME/raw-axi-campaign.log"
CAMPAIGN_STATUS=${PIPESTATUS[0]}

echo "Campaign status: $CAMPAIGN_STATUS"
python3 -c 'import evaluate; print("Campaign ready:", evaluate.campaign_is_ready())'
```

Stop if the status is not zero or the final line says `False`.

Now test only four faults:

```bash
make validate 2>&1 | tee "$HOME/raw-axi-validate.log"
VALIDATE_STATUS=${PIPESTATUS[0]}
echo "Validation status: $VALIDATE_STATUS"
```

The run above must finish with status zero. Its run directory must also contain
`injection-markers.txt`, where these markers show the cycle, commit port, and address
that started injection.

## Create the baseline before selecting 500 faults

First run the seed on the complete set of 37,184 faults:

```bash
cd "$HOME/cva6-testing-platform/raw-evolution"
set -o pipefail

python3 evaluate.py --full seed.S 2>&1 | tee "$HOME/raw-axi-seed-full.log"
FULL_STATUS=${PIPESTATUS[0]}
echo "Full seed status: $FULL_STATUS"
```

This will create a summary and a detailed report under `runs/full/`. Keep both.
Choose the fixed 500 faults from this detailed report. Its labels describe how
the seed behaves with the current injection point and AXI observation.

Now find that detailed report and create the fixed list:

```bash
FULL_REPORT=$(find "$PWD/runs/full" -type f -name faults.rpt -print -quit)

test -f "$FULL_REPORT" || {
    echo "ERROR: the detailed report is missing"
    exit 1
}

echo "Selecting faults from: $FULL_REPORT"
python3 select_faults.py "$FULL_REPORT"
wc -l faults.csv
```

The selector creates `raw-evolution/faults.csv`. Keep this file unchanged throughout the
evolution so every candidate runs against the same faults.

## Check MicroGP without running faults

```bash
cd "$HOME/cva6-testing-platform/raw-evolution"
make smoke
```

This is the smoke test that will check generates and compile a few programs. It uses dummy scores and it also checks that generation zero still matches `seed.S` exactly.

## Run the evolution

After you create `faults.csv`:

```bash
cd "$HOME/cva6-testing-platform/raw-evolution"
make seed
make evolve
```

`make seed` gives us the score of generation zero. `make evolve` then runs the
real search.

The search evaluates one program at a time. Each evaluation can use up to 16
workers. It stops after 20 generations, 100 unique programs, 24 hours, or five
generations without improvement. The first limit that is reached stops the run.

Results stay under `runs/`.
