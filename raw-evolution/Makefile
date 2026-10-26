# Use Bash so recipes behave the same on the server.
SHELL := /usr/bin/env bash
# Stop a recipe at its first failure, also inside pipes such as "| tee".
.SHELLFLAGS := -eo pipefail -c
CANDIDATE ?= seed.S
FIDS ?= 645 648
# Name of one MicroGP run; its files go to results/$(RUN)/.
RUN ?=
OUT := results/$(RUN)

# These names are commands, not files to create.
.PHONY: help check prepare campaign validate seed smoke evolve report replay

# Show the short path through this flow.
help:
	@printf '%s\n' \
	  'make check   Check tools and campaign files' \
	  'make prepare Compile the seed and write Tcl without running it' \
	  'make campaign Compile one pristine fault database' \
	  'make validate Run four faults before a long campaign' \
	  'make seed    Score the program placed in generation zero' \
	  'make smoke   Generate and compile two programs without faults' \
	  'make evolve RUN=name  One MicroGP run, then report.json and replays' \
	  'make report RUN=name  Redo only the report and replays of a run' \
	  'make replay CANDIDATE=BEST_raw FIDS="645 648"  Trace a candidate'

# Confirm that all files and Synopsys tools are available.
check:
	python3 evaluate.py --check

# Compile the seed and create Tcl, but do not start Z01X.
prepare:
	python3 evaluate.py --prepare seed.S

# Compile once and verify that the base has no simulation results.
campaign:
	python3 evaluate.py --campaign

# Prove that injection works before a long run.
validate:
	python3 evaluate.py --validate-faults 4 seed.S

# Measure the exact program that starts generation zero.
seed:
	python3 evaluate.py seed.S

# Trace any candidate with explicit fault IDs; retain the old defaults.
replay:
	python3 replay.py "$(CANDIDATE)" --fids $(FIDS)

# Check the MicroGP files and compiler without calling Z01X.
smoke:
	python3 smoke.py

# One complete MicroGP run: save its inputs, evolve, collect its outputs,
# then write report.json and replay the best program's missed faults.
evolve:
	@test -n "$(RUN)" || { echo 'usage: make evolve RUN=name'; false; }
	@test -f seed.S || { echo 'missing seed.S'; false; }
	@test -f results/fitness-reference.json || \
	  { echo 'freeze a reference first: python3 evaluate.py --set-reference PATH/faults.rpt'; false; }
	@test ! -e "$(OUT)" || { echo "$(OUT) already exists; choose a new RUN"; false; }
	@# Leftovers would mix an older run into this one.
	@for file in individual* status.xml BEST_raw; do test ! -e "$$file" || \
	  { echo "$$file is from an older MicroGP run; move it first"; exit 1; }; done
	mkdir -p "$(OUT)/individuals"
	cp seed.S genome.xml population.settings.xml ugp3.settings.xml config.py \
	  results/fitness-reference.json "$(OUT)/"
	@# evaluate.py appends one line per candidate to this manifest.
	RAW_MANIFEST="$$PWD/$(OUT)/manifest.jsonl" \
	  ugp3 --settingsFile ugp3.settings.xml 2>&1 | tee "$(OUT)/ugp3.log"
	mv individual* "$(OUT)/individuals/"
	mv BEST_raw status.xml statistics*.csv "$(OUT)/"
	python3 replay.py --run "$(OUT)"

# Rebuild report.json and the replays of a finished run.
report:
	@test -n "$(RUN)" || { echo 'usage: make report RUN=name'; false; }
	@# A failed replay may hold a live database; never delete it from here.
	@test ! -e "$(OUT)/replays" || { echo "delete $(OUT)/replays first (no postmaster.pid inside)"; false; }
	python3 replay.py --run "$(OUT)"
