#########################################################################################
# makefile variables for VLSI benchmarks
#########################################################################################
benchmark ?= none
technology_name ?= nangate45
toolchain ?= commercial

EXTRA_CONFS ?=

ifneq ($(benchmark),none)
    tech_name ?= $(technology_name)
endif 

ifeq ($(benchmark),ibex)
    CONFIG            = IbexConfig
    generated_src_name ?= generated-src-$(technology_name)
    HAMMER_EXEC       = ./vlsi-ibex
    TOOLS_CONF        ?= example-tools.yml
    TECH_CONF         ?= ./technology/$(technology_name).yml
    FSIM_CONF_FILE    ?= ./fsim/example-fsim-$(FAULT_MODEL).yml
    DESIGN_CONFS      ?= ./example-designs/$(technology_name)-$(toolchain).yml
    VLSI_OBJ_DIR      ?= build-$(technology_name)-$(toolchain)-$(benchmark)
    INPUT_CONFS       ?= $(TOOLS_CONF) $(TECH_CONF) $(DESIGN_CONFS) $(EXTRA_CONFS)
endif



ifeq ($(benchmark),rocket)
    CONFIG            = RocketConfig
    generated_src_name ?= generated-src-$(technology_name)
    HAMMER_EXEC       = ./example-vlsi
    TOOLS_CONF        ?= example-tools.yml
    TECH_CONF         ?= ./technology/$(technology_name).yml
    FSIM_CONF_FILE    ?= ./fsim/example-fsim-$(FAULT_MODEL).yml
    DESIGN_CONFS      ?= ./example-designs/$(technology_name)-$(toolchain).yml
    VLSI_OBJ_DIR      ?= build-$(technology_name)-$(toolchain)-$(benchmark)
    INPUT_CONFS       ?= $(TOOLS_CONF) $(TECH_CONF) $(DESIGN_CONFS) $(EXTRA_CONFS)
endif


ifeq ($(benchmark),boom-small)
    CONFIG            = SmallBoomV3Config
    generated_src_name ?= generated-src-$(technology_name)
    HAMMER_EXEC       = ./example-vlsi
    TOOLS_CONF        ?= example-tools.yml
    TECH_CONF         ?= ./technology/$(technology_name).yml
    FSIM_CONF_FILE    ?= ./fsim/example-fsim-$(FAULT_MODEL).yml
    DESIGN_CONFS      ?= ./example-designs/$(technology_name)-$(toolchain).yml
    VLSI_OBJ_DIR      ?= build-$(technology_name)-$(toolchain)-$(benchmark)
    INPUT_CONFS       ?= $(TOOLS_CONF) $(TECH_CONF) $(DESIGN_CONFS) $(EXTRA_CONFS)
endif

ifeq ($(benchmark),nvdla)
    CONFIG            = SmallNVDLARocketConfig
    generated_src_name ?= generated-src-$(technology_name)
    HAMMER_EXEC       =  ./vlsi-nvdla
    TOOLS_CONF        ?= example-tools.yml
    TECH_CONF         ?= ./technology/$(technology_name).yml
    FSIM_CONF_FILE    ?= ./fsim/example-fsim-$(FAULT_MODEL).yml
    DESIGN_CONFS      ?= ./example-designs/$(technology_name)-$(toolchain).yml
    VLSI_OBJ_DIR      ?= build-$(technology_name)-$(toolchain)-$(benchmark)
    INPUT_CONFS       ?= $(TOOLS_CONF) $(TECH_CONF) $(DESIGN_CONFS) $(EXTRA_CONFS)
endif

ifeq ($(benchmark),cva6)
    CONFIG            = CVA6Config
    generated_src_name ?= generated-src-$(technology_name)
    HAMMER_EXEC       =  ./vlsi-cva6
    TOOLS_CONF        ?= example-tools.yml
    TECH_CONF         ?= ./technology/$(technology_name).yml
    FSIM_CONF_FILE    ?= ./fsim/example-fsim-$(FAULT_MODEL).yml
    DESIGN_CONFS      ?= ./example-designs/$(technology_name)-$(toolchain).yml
    VLSI_OBJ_DIR      ?= build-$(technology_name)-$(toolchain)-$(benchmark)
    INPUT_CONFS       ?= $(TOOLS_CONF) $(TECH_CONF) $(DESIGN_CONFS) $(EXTRA_CONFS)
endif


ifeq ($(benchmark),radiance)
    CONFIG            = RadianceTapeoutSimConfig 
    generated_src_name ?= generated-src-$(technology_name)
    HAMMER_EXEC       =  ./example-vlsi
    TOOLS_CONF        ?= example-tools.yml
    TECH_CONF         ?= ./technology/$(technology_name).yml
    FSIM_CONF_FILE    ?= ./fsim/example-fsim-$(FAULT_MODEL).yml
    DESIGN_CONFS      ?= ./example-designs/$(technology_name)-$(toolchain).yml
    VLSI_OBJ_DIR      ?= build-$(technology_name)-$(toolchain)-$(benchmark)
    INPUT_CONFS       ?= $(TOOLS_CONF) $(TECH_CONF) $(DESIGN_CONFS) $(EXTRA_CONFS)
endif


