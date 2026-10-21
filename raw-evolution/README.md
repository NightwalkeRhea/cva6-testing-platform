# RAW evolution

MicroGP evolves RISC-V programs, and Z01X scores their fault coverage. The
experiment asks whether information from passive monitors helps an LLM produce
better tests or templates for that search.

Follow the [main README](../README.md) to install the tools and run the experiment.

## Measurement

For this experiment, use the supplied RTL campaign: 37,184 uncollapsed stuck-at
faults on ports inside `i_scoreboard` and `i_issue_read_operands`. Evaluation on
the synthesized netlist and a completed comparison with monitor guidance remain
work in progress.

`raw_body` is the assembly function in `seed.S`. The C harness calls it 256
times with inputs 0 through 255, XORs the returned values, and prints a signature.
The genome includes the seed as its fixed base; MicroGP can add or alter patterns
around that base. The smoke check verifies that generation zero matches `seed.S`.

Expect injection at retirement of the first instruction of `raw_body` on
either commit port, then throughout the remaining calls and printing. Find the
saved confirmation from Z01X in `injection-markers.txt`.

Read coverage as detection at the AXI boundary of `CVA6CoreBlackbox`, including
changes in values or timing. A changed signature is not required. Use internal
traces to explain behavior, while keeping AXI detection as the coverage measure.

For example, if the clean run writes `0x25` at cycle 12,400, a faulty write of
`0x24` is detected. Writing `0x25` at cycle 12,401 also counts as detection.

## Fitness

Evaluate every candidate against the complete campaign. No `faults.csv` is
required. MicroGP compares two values in order:

1. Coverage: `100 * DG / total faults`.
2. Complementary detections: the number of detected IDs absent from a fixed reference.

The second value only breaks ties in coverage; it cannot compensate for lower
coverage. DG counts definite detections at the AXI boundary. Potential detections do not earn points.
Untestable faults remain in the denominator, so 100% may be unreachable.

Freeze a full report with `python3 evaluate.py --set-reference PATH/faults.rpt`
before evolution. The reference must have a complete summary, evidence of
injection, and the same faults in the same order as the current SFF. The saved
reference is checked against the current campaign before scoring. Missing or
mismatched references stop evolution rather than silently disabling the second
objective. A standalone seed evaluation can run before a reference exists;
its second value is then zero.

Each successful full evaluation saves `detected-ids.json` alongside `faults.rpt`.
The parser verifies the IDs against the SFF and checks their count against DG.
Unknown entries, mismatched counts, and illegal file I/O stop evaluation. These
saved IDs also let you change the reference between experiments without rerunning
fault simulation. Do not resume an old MicroGP state after changing the reference
or the meaning of fitness; start a new run.

With the supplied campaign, divide DG by 37,184. The evaluator reads the total
from each report rather than assuming that number. Use the same campaign and
observation points throughout the comparison. `--full` remains accepted but is
no longer needed; only the four-fault validation uses a subset.

Each candidate reuses the simulator and SFF and receives a copy of the stopped
database in `campaign-base/`. The base and its copy must have all testable faults
marked NA before simulation. Each run opens its own database by its explicit
path. Successful evaluations stop their server and remove the private database
after saving reports; failed runs retain their files.

Before copying the database, the evaluator runs the candidate without faults.
A failed program or one that exceeds one million cycles receives zero fitness.
Other simulator errors stop evaluation. Passing programs get a fault simulation
limit of 1.5 times their measured cycles, with a floor of 60,000 and a ceiling
of ten million. These values live in `config.py`; `probe.log` records the run.
Reject assembly syntax errors before probing. Keep the same cycle policy for
both searches and recheck the seed when changing it; shorter limits can change
fault outcomes.

## MicroGP settings

Develop the method with monitor guidance first. Preserve the original genome
and seed before changing them. When the method is ready, freeze it and run the
final comparison without guidance from those original inputs. Use the same
campaign, evaluator, settings, and budget for that comparison, and report the
additional effort spent developing the guided method.

| Setting | Value | Effect |
| --- | --- | --- |
| Initial population (`nu`) | 1 | Start with the exact seed |
| Parents (`mu`) | 8 | Retain up to eight parents |
| Offspring (`lambda`) | 16 | Generate sixteen children per generation |
| Elite | 1 | Preserve the best program |
| Maximum age | 3 | Limit how long ordinary individuals survive |
| Tournament (`tau`) | 1 | No competition within the tournament; survival still selects |
| Mutation (`sigma`) | 0.25 | Initial strength of variation |
| Inertia | 0.9 | Controls adaptation |
| Concurrent evaluations | 16 | Evaluate up to sixteen candidates at once |
| Z01X workers | 1 | One worker per candidate |

MicroGP passes a batch to `evaluate.py`, which runs the programs in parallel
and writes one fitness file in the original order. Identical programs share a
locked directory and reuse the completed result. The initial seed runs alone.

Allow up to 20 generations, 100 evaluations, 24 hours, or five generations
without improvement, whichever limit is reached first. Record actual evaluations
and elapsed time when comparing searches; the limits may take effect only after
an evaluation or generation finishes.

Enabled operators alter parameters, insert patterns, or replace patterns. There
is no explicit removal mutation or crossover. Keep the settings identical for
the unguided and guided searches unless the change itself is part of the study.

## Files and results

| File | Purpose |
| --- | --- |
| `seed.S`, `harness.c` | Starting assembly and fixed C harness |
| `genome.xml` | Seed base and allowed patterns |
| `population.settings.xml`, `ugp3.settings.xml` | Search settings |
| `config.py` | Paths, random seed, and simulation limits |
| `evaluate.py` | Compile programs, run faults, and return fitness |
| `replay.py` | Compare any candidate with explicitly chosen faults |
| `select_faults.py` | Shared parser for reports; also retains the old sampling utility |
| `smoke.py` | Check generation and compilation with dummy fitness |
| `runs/` | Per-program logs, reports, databases, and replay traces |
| `results/` | Preserved inputs and results for each experiment |
| `BEST_raw`, `statistics.csv`, `status.xml` | Best program, progress, and recovery state |

Read `comparison.json` first, then inspect `dependency.csv` and `raw-checker.csv`
for the full traces. Compare rows only while cycle, PC, issue port, and operand
agree. After the first mismatch, investigate the sequence before pairing later
rows. Use changes in internal fields to explain the fault's behavior, and use
the AXI result to decide whether the fault was detected.

Use `python3 replay.py BEST_raw --fids 645 648` to replay a winner. Replace the
IDs with the faults you want to inspect. Without arguments, replay retains the
seed and the two original example IDs. Clean and faulty runs use the same limits
as ordinary evaluation. Every replay gets a new directory and keeps its own
`source.S`, reports, and traces, so a later winner cannot overwrite its evidence.
