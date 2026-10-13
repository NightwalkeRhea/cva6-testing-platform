# Use Bash so recipes behave the same on the server.
SHELL := /usr/bin/env bash

# These names are commands, not files to create.
.PHONY: help select check prepare campaign seed evolve

# Show the short path through this flow.
help:
	@printf '%s\n' \
	  'make select  Choose the 500 faults from the saved report' \
	  'make check   Check tools and campaign files' \
	  'make prepare Compile the seed and write Tcl without running it' \
	  'make campaign Compile one pristine fault database' \
	  'make seed    Compile and score the starting RAW program' \
	  'make evolve  Run one small MicroGP generation'

# Choose a stable set of 500 faults from the saved report.
select:
	python3 select_faults.py

# Confirm that all files and Synopsys tools are available.
check:
	python3 evaluate.py --check

# Compile the seed and create Tcl, but do not start Z01X.
prepare:
	python3 evaluate.py --prepare seed.S

# Run vc_fcc once. Candidates copy this clean database.
campaign:
	python3 evaluate.py --campaign

# Measure the hand-written program before evolution starts.
seed:
	python3 evaluate.py seed.S

# Run the small trial that population.settings.xml describes.
evolve:
	ugp3 --settingsFile ugp3.settings.xml
