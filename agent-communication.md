Before working on these problems, read this file, the relevant code, and the
user's latest data. Update the findings here after checking them. Do not rerun
simulations when the saved traces can answer the question.

1. Adjacent instructions rarely show RAW activity.

The supplied profile has 291,976 cycles. Adjacent ALU instructions read the
register file without waiting; only two instructions record forwarding.
This could mean instructions arrive too far apart to overlap.

Look in `testyard/generators/cva6/src/main/scala/cva6/CVA6Tile.scala`:
The original generator wrote zero `NrCachedRegionRules`, declaring no addresses cacheable.
The user confirmed the server also has no cacheable regions. The supplied CSV
shows a 12-cycle gap between the load issuing and its consumer first appearing.

Fix implemented locally: generate the cache rules from the RAM ranges already
calculated in `CVA6TileModuleImp`, rather than writing zero rules. Keep the
existing exclusion for tohost/fromhost unless their caching is explicitly enabled.
Both caches already reset to enabled in `core/csr_regfile.sv`; no change there.
Standalone Scala checks passed for the generator, including the host-address
exclusion. The full Chipyard build was not run locally.
Still needs Chipyard elaboration and a clean run on the server to check that
instructions now overlap. No rebuild has been started. New fault generation,
a new baseline, and a new reference are required after the configuration changes.

Review of the fix (Claude): the change looks correct. The rules are packed in
the right order, the package is written before the CVA6 Makefile copies it
into `CVA6CoreBlackbox.preprocessed.sv`, and that Makefile always rebuilds.
Three things to check on the server:
- The change is inside the `testyard/generators/cva6` submodule. Commit it
  there and push it where cattivo can fetch it, then commit the new submodule
  pointer in the main repo. Otherwise cattivo builds the old code.
- After the build, open `generators/cva6/src/main/resources/cva6/vsrc/cva6_config_pkg.sv`.
  It should say `NrCachedRegionRules: unsigned'(2)` with the RAM split around
  0x80001000-0x8000107f.
- The wrapper assumes `tohost` is at 0x80001000 and `fromhost` at 0x80001040.
  Check a candidate with `riscv64-unknown-elf-nm candidate.riscv | grep host`.
  If they sit elsewhere, they become cacheable and the program may hang
  while printing.

Checked on 26 October: they sit elsewhere. In the best program, `tohost` is
at 0x80001e00 and `fromhost` at 0x80001e08. Both fall inside the new cached
region, so the wrapper's exclusion (0x80001000-0x8000107f) does not protect
them. Their address also depends on program size, so a fixed exclusion
cannot work for every candidate.
Why it matters: the host writes `fromhost` (and clears `tohost`) straight in
memory. If the core reads them through the data cache, it can keep seeing the
old value and wait forever when printing the signature.
Both live in the `.htif` section (16 bytes), confirmed with `objdump -t`.

Fix: pin `.htif` to one fixed address and leave exactly that address uncached.
- In `compile_program()` in `raw-evolution/evaluate.py`, add the link flag
  `-Wl,--section-start=.htif=ADDR`.
- In `CVA6TileModuleImp` (`testyard/generators/cva6/src/main/scala/cva6/CVA6Tile.scala`),
  set `tohostAddr = ADDR` and `fromhostAddr = ADDR + 8`. The wrapper then
  leaves ADDR to ADDR+0x48 uncached, which covers both.
- ADDR cannot be 0x80001000: the program's code already reaches past it.
  Memory map of the best program (from `nm`): program data ends at
  0x80002240 (`_end`), the heap runs up to 0x80023000, and the stack
  takes the next 0x8000 bytes, up to about 0x8002b000.
  Use ADDR = 0x80100000. It is 1 MB into RAM, far above all of that, and
  leaves room for much larger programs.
- After the change, check one candidate: `objdump -t` must show `tohost` at ADDR.
Do not rebuild until this is settled.

Done (Claude, 26 October), not committed, not built:
- `raw-evolution/config.py`: `HTIF_ADDRESS = 0x80100000`.
- `raw-evolution/evaluate.py`, `compile_program()`: links `.htif` there, then
  `check_htif()` reads `nm` and stops if `tohost` is not at that address, and
  rejects a program whose data, heap, or stack reaches it (the linker does not
  check those). One new test; all 49 tests pass.
- `CVA6Tile.scala`: `tohostAddr = 0x80100000`; the uncached window is now the
  whole 4 KiB page 0x80100000-0x80100fff, so no cache line is shared.
  `fromhostAddr` is gone (no longer used).
Watch out: this applies to every CVA6 program, not just raw-evolution. Any
other program run on this build (for example the testyard `vlsi` fsim flow)
must also be linked with `-Wl,--section-start=.htif=0x80100000`, or its
tohost/fromhost will be cached.

Correction (Codex, 26 October): the section-start flag alone is not safe.
Cattivo showed `.data._impure_data` at 0x80100010 and `_end` at 0x80100440:
the old script puts `.htif` before normal data, so moving it moves data too.
`compile_program()` now generates `candidate.ld` in each run from the existing
script. It puts `.htif` last at 0x80100000, collects `.data.*` with normal data,
and asserts that the stack stays below the page and HTIF fits inside it.
It no longer passes `--section-start`. `check_htif()` also checks `fromhost`.
The shared Testyard script and RTL are unchanged. Local GNU ld tests pass;
compiling the real seed on cattivo is still required. Other programs must use
an equivalent layout, not blindly apply the old section-start command.

2. The profile names the wrong instruction as the producer.

In the supplied `profile.json`, `ld t0` at `0x800002b4` is immediately followed
by `addi t1,t0,31` at `0x800002b8`. The profile attributes the forwarded value
to other instructions, including ones that do not write t0.

Look in `raw-evolution/evaluate.py`, function `write_profile()`: the `producers`
dictionary maps scoreboard IDs to instructions. Also check the fields written
by `life_story_write` and `life_story_classify` in
`testyard/generators/cva6/src/main/resources/cva6/vsrc/cva6/core/issue_read_operands.sv`.
Compare the load's `consumer_id` with the consumer's `rs1_producer_id` and
`raw_matches_rs1` in the saved CSV.

Confirmed from the CSV supplied on 26 October: the load issues at cycle 7283
with ID 1. The addi first appears at 7295, waits seven cycles, then issues at
7302 with forwarding enabled, but producer ID 0 and no matches. The next
iterations repeat this with load IDs 7 and 5, still reporting producer ID 0.
So the wrong attribution starts in the monitor, not just the Python mapping.

Cause in the code: the real forwarding logic accepts values directly from
writeback ports before their scoreboard entries become valid. The monitor's
`life_story_classify` only searches valid scoreboard entries; it misses that
path and leaves the producer ID at its default zero. Python then treats zero
as a real ID and names an unrelated instruction.
Fix implemented locally (Codex, 26 October): the monitor now reads the actual
forwarding requests, checking WB ports first and scoreboard entries second.
It appends `rs1_producer_valid` and `rs2_producer_valid` to the CSV. Valid ID
zero remains usable; an unfound producer is no longer treated as ID zero.
Python records unattributed forwarding instead of inventing a producer. It
clears waiting history on a flush, keeps already-issued producers, and keeps
stalls with the instruction that waited. Old CSVs without the validity fields are rejected
when rebuilding profiles; their producer names cannot be repaired by this fix.

An isolated Verilator test of the actual observer block passed for direct WB,
WB priority, scoreboard ID zero, and no producer. Python regression tests cover
ID reuse, flushes, and invalid IDs. Full CVA6 simulation is still pending.
After the shared rebuild, check the same load-use pair: its producer must be
the load's ID, with validity 1. Do not use new profiles for guidance until that
check passes. Existing AXI coverage is not invalidated just by this reporting bug.

Review of the fix (Claude): the monitor change is correct. It follows the same
requests and the same "lowest index wins" order as the real forwarding
arbiter (`i_sel_rs1`/`i_sel_rs2`), so it reports the producer the hardware
actually used. All 46 Python tests pass locally, including the Verilator one.

One thing to change in Python (`write_profile()` in `raw-evolution/evaluate.py`):
on a flush row it now runs `producers.clear()`. That is too much. The monitor's
`flush` column is `flush_unissued_instr` (see `issue_stage.sv`, the
`i_issue_read_operands` instance): it throws away only instructions that
were not issued yet. Instructions already issued, like a load still waiting
for memory, stay in the scoreboard and can still forward their value after the
flush. Clearing `producers` makes those later forwards show up as
"producer absent from trace" after every mispredicted branch.
Fix: on flush, clear only `waited` and `waiting_instruction`, not `producers`.
The ID map stays correct, because a flushed instruction was never accepted and
so never entered it.

Corrected (Codex, 26 October): removed `producers.clear()` after checking the
connection in `issue_stage.sv`. The regression test now keeps a load across
the flush, then checks that its consumer gets the right producer and does not
inherit a discarded instruction's stall. All 47 tests pass, including Verilator.

Commit order: the RTL change is two submodules deep. Commit
`issue_read_operands.sv` inside `.../vsrc/cva6`, then the new pointer in
`testyard/generators/cva6` (together with the `CVA6Tile.scala` change), then
the pointer in the main repo. Push each level so cattivo can fetch it.

3. The report can give Sandro misleading or unhelpful targets.

Look in `raw-evolution/replay.py`, function `report_run()`.
`events_never_seen` uses only units and ports already observed, so it hides
completely absent ones and can include impossible combinations. Fix problem 2
first, then use the actual configuration to distinguish missing events from
unsupported ones. Do not assume two commit ports means two issue ports.

The same function selects NO faults across signals without checking whether
the monitors can explain them. The supplied replay results reportedly show no
differences for all 20 faults; their full files still need checking here.
Record what each monitor observes and distinguish an unobserved effect from
an inactive interface. Do not call a fault untestable just because its traces
match. Any revised selection policy must be explicit, not cherry-picked.

Note: the replays say `ND`, while the same faults were `NO` in the run. That is
expected. `NO` is a label from Z01X's COATS step, and replays skip COATS. `ND`
just means "not detected".

4. Replay ran the clean simulation again instead of reusing it. (Claude)

In `results/guided-axi-01/replays/comparison.json`, `clean_traces` points to
`replays/clean`. That means the replay could not find the clean run that
the evaluation had already saved, so it ran a new one. This wastes time but
does not change results.

Cause: look in `raw-evolution/replay.py`, function `clean_traces()`. It finds
the saved run by recomputing the folder name (`evaluation_digest()` in
`evaluate.py`). That name changes whenever `evaluate.py` or `config.py`
changes. Most likely cause: the job-sharing commit (3e6ef50a) changed both files
and was pulled on cattivo after the run, so the recomputed name no longer
matched `runs/full/aa133997037e7234`.

Fix: `report_run()` already knows the best program's folder from
`manifest.jsonl` (`best_work`). Pass that folder to `clean_traces()` instead
of recomputing the name. Keep the recompute only for hand-run replays.

Fixed (Claude, 26 October): `report_run()` passes `best_work` through
`replay_faults()` to `clean_traces()`. Hand-run replays still recompute the
name. One new test; all 47 tests pass.

Review (Codex, 26 October): passing the saved folder fixes the unnecessary
rerun after Python edits. Two checks are still missing in `clean_traces()`
(lines 80-83):
- It returns a binary and traces after checking only the profile and probe
  log. If the binary or a CSV was removed, it fails later. Check that the
  binary and both CSVs (plain or gzip) exist before accepting reuse.
- A saved folder is now accepted even after the simulator changes. After
  the cache/monitor rebuild, this could compare old clean traces with new
  faulty traces. Check the saved simulator/trace identity before reuse;
  refuse a different build rather than silently mixing runs. This guard
  must not reject safe reuse just because the Python code changed.
These are review findings, not changes to Claude's replay fix.

Done (Claude, 26 October): both points.
- `save_detected_ids()` in `evaluate.py` now also saves the simulator build
  (`campaign_fingerprint()`) in each run's `detected-ids.json`.
- `clean_traces()` in `replay.py` reuses a folder only if the profile, the
  binary, both traces (plain or .gz) and `detected-ids.json` exist, and the
  saved build equals the current one. Python edits do not affect this.
- If not, it does not refuse: it runs the clean simulation again on the
  current build. The faulty replays also use the current build, so clean and
  faulty traces always come from the same build.
- Runs made before this change have no saved build, so their replays run
  clean again once. All 48 tests pass.

5. The `stall` field in the events is always "stalled". (Claude)

Not a bug in the code, but it carries no information right now. An operand
counts as "forward" only when the monitor saw a RAW hazard at issue, and in
this run that only happens after waiting for a load. Check again after
problem 1 is settled. If it is still always "stalled", drop it from the
events so the LLM does not read meaning into it.

Flow check before the rebuild (Claude, 26 October)

Only RTL/Scala mistakes force a rebuild; Python mistakes do not. So check
the build in this order, and stop at the first failure:
1. Build only `simv`. Open `cva6_config_pkg.sv`: 2 cached regions, the
   second one starting at 0x80101000.
2. Compile the seed: `objdump -t candidate.riscv | grep host` shows `tohost`
   at 0x80100000 (`check_htif()` also stops otherwise).
3. Clean-run the seed with traces. It must print PASSED (proves HTIF works
   with caches on and `+loadmem` loads the moved section).
4. In `dependency.csv`: ALU pairs now show forwarding, and the `ld t0` ->
   `addi` pair has the load's ID as producer, with `rs1_producer_valid` 1.
5. Only then: new fault list, `make campaign`, seed evaluation, reference,
   `make evolve`.
A failure in 1-4 costs only a `simv` rebuild, not a fault campaign.

Checked and fine: the strobe has no fixed addresses (it uses `+raw_body_pc`
and observes the core boundary); nothing else in `raw-evolution` or
`testyard/vlsi` uses the old tohost address; the D-cache is write-through
(`dCacheType = "WT"`), so stores and the signature still reach AXI;
`CVA6Config` does not enable `WithToFromHostCaching`.
Expected change, not a bug: with caches on, loads and fetches mostly stay
inside the core, so fewer faults reach AXI. Coverage will not be comparable
with the old baseline; it is replaced anyway.
`SuperscalarEn` is 0, so there is one issue port: the `port` field in the
events is always 0 and can be dropped with problem 3.
