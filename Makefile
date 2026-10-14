# Small entry point for the CVA6 test flow.
SHELL := /usr/bin/env bash

# Find every project path from this file
ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
TESTYARD := $(ROOT)/testyard
VCS_DIR := $(TESTYARD)/sims/vcs
VLSI_DIR := $(TESTYARD)/vlsi
CVA6_VSRC := $(TESTYARD)/generators/cva6/src/main/resources/cva6/vsrc

# Allow each path or seed to be overridden on the command line, otherwise default
RISCV ?= $(TESTYARD)/.conda-env/riscv-tools
TEST ?= $(TESTYARD)/tests/raw-hazard/raw-hazard.riscv
TRACE_DIR ?= $(ROOT)/traces/monitored-raw
SEED ?= 946094937
RAW_BODY_PC = $(shell $(RISCV)/bin/riscv64-unknown-elf-nm --defined-only $(TEST) 2>/dev/null | awk '$$3 == "raw_body" {print $$1}')

.PHONY: help check blackbox simv smoke fsim

# List the public commands in this file.
help:
	@printf '%s\n' \
	  'make check     Check the required tools and files' \
	  'make blackbox  Rebuild the CVA6 Verilog blackbox' \
	  'make simv      Rebuild the normal VCS simulator' \
	  'make smoke     Run the RAW test and check its traces' \
	  'make fsim      Run faults for a binary that defines raw_body'

# this one will fail early when the shell or checkout is incomplete
check:
	@$(ROOT)/scripts/check-cva6.sh

# Combine the CVA6 sources into the Verilog file used by Testyard
blackbox:
	$(MAKE) -C $(CVA6_VSRC) -B EXTRA_PREPROC_DEFINES=SYNTHESIS default

# Regenerate Testyard Verilog and then compile the VCS simulator
simv: blackbox
	$(MAKE) -C $(VCS_DIR) clean-sim CONFIG=CVA6Config
	$(MAKE) -C $(VCS_DIR) verilog CONFIG=CVA6Config RISCV=$(RISCV)
	$(MAKE) -C $(VCS_DIR) CONFIG=CVA6Config RISCV=$(RISCV)

# Run the small RAW program and write both passive traces
smoke:
	mkdir -p $(TRACE_DIR)
	$(MAKE) -C $(VCS_DIR) run-binary CONFIG=CVA6Config BINARY=$(TEST) \
	  LOADMEM=1 RANDOM_SEED=$(SEED) RISCV=$(RISCV) \
	  EXTRA_SIM_FLAGS="+dependency_trace_file=$(TRACE_DIR)/dependency.csv +raw_checker_trace_file=$(TRACE_DIR)/raw-checker.csv"
	@$(ROOT)/scripts/check-smoke.sh $(TRACE_DIR) $(VCS_DIR)

# Run the saved CVA6 campaign through Hammer and Z01X (otherwise hammer will loop and loop and loop...)
fsim:
	@test -n "$(RAW_BODY_PC)" || { \
	  echo 'ERROR: TEST must define raw_body; use the raw-evolution flow'; \
	  false; \
	}
	$(MAKE) -C $(VLSI_DIR) fsim-rtl HAMMER_D_DEPS="" HAMMER_DEPENDENCIES="" \
	  benchmark=cva6 BINARY=$(TEST) LOADMEM=$(TEST) RISCV=$(RISCV) FAULT_MODEL=saf \
	  EXTRA_SIM_FLAGS="+raw_body_pc=$(RAW_BODY_PC)" \
	  FSIM_CONF_FILE=./fsim/example-fsim-cva6-saf.yml \
	  FSIM_STROBE_FILE=$(VLSI_DIR)/fsim/strobe/strobe_cva6.sv
