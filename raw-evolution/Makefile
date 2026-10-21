# Use Bash so recipes behave the same on the server.
SHELL := /usr/bin/env bash

# These names are commands, not files to create.
.PHONY: help check prepare campaign validate seed smoke evolve replay

# Show the short path through this flow.
help:
	@printf '%s\n' \
	  'make check   Check tools and campaign files' \
	  'make prepare Compile the seed and write Tcl without running it' \
	  'make campaign Compile one pristine fault database' \
	  'make validate Run four faults before a long campaign' \
	  'make seed    Score the program placed in generation zero' \
	  'make smoke   Generate and compile two programs without faults' \
	  'make evolve  Evolve programs on the complete fault campaign'

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

# Compare the clean seed with two missed faults using optional monitor traces.
replay:
	python3 replay.py

# Check the MicroGP files and compiler without calling Z01X.
smoke:
	python3 smoke.py

# Run the bounded search that population.settings.xml describes.
evolve:
	@test -f seed.S || { echo 'missing seed.S'; false; }
	ugp3 --settingsFile ugp3.settings.xml
