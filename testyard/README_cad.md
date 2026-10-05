# Table of Contents
- [Table of Contents](#table-of-contents)
- [Setup instruction for testyard and hammer for the CAD's servers](#setup-instruction-for-testyard-and-hammer-for-the-cads-servers)
- [Compile the tests](#compile-the-tests)
  - [Compiling additional tests (SBSTs)](#compiling-additional-tests-sbsts)
- [Simulating the RTL](#simulating-the-rtl)
- [Synthesis](#synthesis)
- [Static timing analysis](#static-timing-analysis)
- [Simulating the Gate-level](#simulating-the-gate-level)
- [Fault simulation](#fault-simulation)
  - [Fault models](#fault-models)
  - [Auto-update the program counter from which the VC-Z01X injection shall start](#auto-update-the-program-counter-from-which-the-vc-z01x-injection-shall-start)
  - [Fault Simulating the RTL-level](#fault-simulating-the-rtl-level)
  - [Fault Simulating the Gate-level](#fault-simulating-the-gate-level)
  - [The FSIM folder in VLSI](#the-fsim-folder-in-vlsi)
  - [Notes and Caveats](#notes-and-caveats)
- [ATPG (Automatic Test Pattern Generation)](#atpg-automatic-test-pattern-generation)
  - [Running ATPG](#running-atpg)
  - [Fault models](#fault-models-1)
  - [Custom patterns and faults files](#custom-patterns-and-faults-files)
  - [Configuring ATPG](#configuring-atpg)
- [Useful information](#useful-information)
  - [Reuse ATPG fault list for functional fault simulation](#reuse-atpg-fault-list-for-functional-fault-simulation)
- [Notes on Specific Designs](#notes-on-specific-designs)
- [Contacts](#contacts)

---

# Setup instruction for testyard and hammer for the CAD's servers
This is a guide for using the designs in the testyard framework and synthesize them using a custom library.

> **_NOTE:_** We DO NOT CARE  about the PAR (Place and Route) since it is not the focus of our research. Thus, those flows may not work (we try our best, as always).

In order to setup the environment for running testyard (simulation and synthesis) you need to obseerve the following steps:

In the following ``git clone`` commands, it is assumed that SSH-keys are configured. If you just want to use the repositories, please substitute every ``git clone git@github.com:cad-polito-it/REPO_TO_DOWNLOAD.git`` with ``git clone https://github.com/cad-polito-it/REPO_TO_DOWNLOAD``.

1. Download the hammer repository 
    ```bash 
    $ git clone git@github.com:cad-polito-it/hammer.git
    $ cd hammer 
    $ git checkout working/cad_servers
    ```
2. Install conda environment from ``$HOME`` dir:
    ```bash 
    wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
    bash Miniconda3-latest-Linux-x86_64.sh  
    rm -f Miniconda3-latest-Linux-x86_64.sh  
    ``` 
    You may need to export the binaries path (you can add the following line in your ``.bashrc``)
    ```bash
    export PATH=${PATH}:${HOME}/miniconda3/bin/                                    
    ```
3. Download the testyard repository  
    ```bash 
    $ git clone git@github.com:cad-polito-it/testyard.git
    $ cd testyard
    $ git checkout working/cad_servers
    ```

4. Install the neeeded tools for testyard by running 
    ```bash 
    $ cd testyard 
    $ ./scripts/build-setup.sh riscv-tools -s 6 -s 7 -s 8 -s 9 
    ```
    You do not need all the tools, just the basic ones and converters from chisel to verilog.

    **It may take a while** 

    **Now you can activate your conda environment by sourcing the env.sh file in the testyard folder**


5. Download the OpenRoad repository for generating technology-related rams 
    ```bash 
    cd ~
    git clone --recursive https://github.com/The-OpenROAD-Project/OpenROAD.git 
    echo "export OPENROAD=~/OpenROAD" >> ~/testyard/env.sh
    conda create -c litex-hub --prefix ~/.conda-openroad openroad=2.0_7070_g0264023b6
    conda create -c litex-hub --prefix ~/.conda-klayout klayout=0.28.5_98_g87e2def28
    conda create -c litex-hub --prefix ~/.conda-signoff magic=8.3.376_0_g5e5879c netgen=1.5.250_0_g178b172
    ```
    **Note the APPEND to the ``env.sh`` file !**

    **Add the conda environments to the PATH**
    ```bash 
    export PATH=${PATH}:~/.conda-openroad/bin:~/.conda-klayout/bin:~/.conda-signoff/bin
    ```
   OR Add the following YAML keys to the top of env.yml to specify the locations of the tool binaries. Note that this is not required if the tools are already on your PATH.
   ```yaml
    # all ~ should be replaced with absolute paths to these directories
    # tool binary paths
    synthesis.yosys.yosys_bin: ~/.conda-yosys/bin/yosys
    par.openroad.openroad_bin: ~/.conda-openroad/bin/openroad
    par.openroad.klayout_bin: ~/.conda-klayout/bin/klayout  # binary that OpenROAD calls for final GDS writeout
    drc.klayout.klayout_bin: ~/.conda-klayout/bin/klayout   # binary that runs for DRC step
    drc.magic.magic_bin: ~/.conda-signoff/bin/magic
    lvs.netgen.netgen_bin: ~/.conda-signoff/bin/netgen
   ```

    **This is a hard hack for the moment**
6. Install and use the specific patched hammer version (**IMPORTANT**):
   ```bash 
    cd testyard/vlsi/
    ./install_plugins.sh hammer_root_dir
    ```
    
7. In order to use the ASAP7 technology library, you need to install `gdstk` or `gdspy`.
    Either the `gdstk` or `gdspy` GDS manipulation utility is required for 4x database downscaling. `gdstk` (available [here on GitHub](https://github.com/heitzmann/gdstk), version >0.6) is highly recommended; however, because it is more difficult to install, `gdspy` (available [here on Github](https://github.com/heitzmann/gdspy/releases), specifically version 1.4 can also be used instead, but it is much slower.
    You can install `gdstk` as following:
    ```bash 
    $ cd testyard 
    $ source env.sh 
    $ conda install conda-forge::gdstk
    ```

Please also refer to:
-   [Hammer Docs](https://hammer-vlsi.readthedocs.io/)
-   [Chipyard setup docs](https://chipyard.readthedocs.io/en/latest/Chipyard-Basics/Initial-Repo-Setup.html)
-   [Chipyard docs](https://chipyard.readthedocs.io/en/latest/index.html)


# Technology Libraries Setup
For PDK you should download them and then set the installation path in technology file ``vlsi/technology/yourTech``:
```yaml
# Technology Setup
vlsi.core.technology: "hammer.technology.cnfet5"
# Technology files installation directory
technology.cnfet5.install_dir: "/data/libraries/CNFET-OCL/CNFET5"
```

Currently available Tehcnology library:
- [ASAP7](https://github.com/The-OpenROAD-Project/asap7) 
- [CNFET5](https://github.com/uec-hpc-lab/CNFET-OCL/tree/main) 
- [CNFET7](https://github.com/uec-hpc-lab/CNFET-OCL/tree/main) 
- [NANGATE15](https://si2.org/open-cell-and-free-pdk-libraries/)
- [NANGATE45](https://si2.org/open-cell-and-free-pdk-libraries/)

You can set the used technology library by acting on the ``technology_name`` variable:
```bash 
$ make syn benchmark=rocket technology_name=asap7
```
It will use the ASAP7 technology library instead of the NANGATE45

> **_NOTE:_** SRAMS can be generated by a generic generator in hammer! 
> In technology file (in ``vlsi/technology`` dir):  
> ```yaml
> # For generating fake srams
> vlsi.core.sram_generator_tool: "hammer.sram_generator.generic_generator"
> ```

> **_NOTE:_** For CNFET technology library, the Design for Testability insertion is not available (scan flip-flops are not present in the library)!

Please refer to: [Hammer Docs](https://hammer-vlsi.readthedocs.io/)

# Compile the tests
For compiling a set of hello world applications:

```bash
$ cd testyard
$ cd tests
$ cmake .
$ make 
```


## Compiling additional tests (SBSTs)
For compiling custom SBSTs you can:
- Add an additional folder named sbst1
- Add in the ``tests/CMakeLists.txt`` the libe ``add_subdirectory(sbst1)``
- Add/Modify the ``tests/sbst1/CMakeLists.txt`` accordingly.
- Add/Modify source files.

You can see an example in the ``tests/sbst`` folder.

For example:
```bash 
$ cd tests
$ cmake .
$ make sbst1
```
You will find the executable in ``tests/sbst1/`` named ``sbst1.riscv``

# Simulating the RTL 
For running a simulation for a given configuration in [``variables.mk``](https://github.com/cad-polito-it/testyard/blob/working/cad_servers/variables.mk) and a specified program (BINARY var points to the compiled program) from tests folder:
```bash 
$ cd sims/vcs
$ make verilog CONFIG=SmallBoomV3Config
$ make run-binary BINARY=../../tests/hello.riscv CONFIG=SmallBoomV3Config LOADMEM=1
```
It generates the verilog file and run the binary 

# Synthesis 

For generating the files for the synthesis using the technology and deesign files defined in [``benchmarks.mk``](https://github.com/cad-polito-it/testyard/blob/working/cad_servers/vlsi/benchmarks.mk) file:
```bash 
$ cd vlsi
$ make buildfile benchmark=rocket
```

For running the synthesis:
```bash 
$ cd vlsi
$ make syn benchmark=rocket
```
For modifying the synthesis script you can add hook pre/post/substitute for the steps.

# Static timing analysis 

For generating Static timing analysis reports (including slack-based report for generating small delay faults):
```bash 
$ cd vlsi
$ make timing-syn benchmark=rocket
```

The file used for both the ATPG and FSIM flow for the Small Delay Faults is ``build_dir/timing-syn-rundir/reports/report_global_slack.rpt``. 

# Simulating the Gate-level
For running post synthesis simulation:
```bash 
$ cd vlsi
$ export TEST_PATH=absolute_path/tests/hello.riscv
$ make sim-syn benchmark=rocket BINARY=${TEST_PATH} LOADMEM=${TEST_PATH}
```
**Note: You need the absolute path for the BINARY!**

For running post synthesis simulation and dumping the FSBD/EVCD:
```bash 
$ cd vlsi
$ export TEST_PATH=absolute_path/tests/hello.riscv
$ make sim-syn-debug benchmark=rocket BINARY=${TEST_PATH} LOADMEM=${TEST_PATH}
```

If you want to use a GUI for visualizing the waveforms (espeecially in debug mode), you must export the following variable:

```bash 
export SIM_USE_GUI=true
```
Or you can use GTKWave for opening the waveforms.

# Fault simulation

For fault simulation, vc-zoix is used. Under vlsi/fsim-utilities the sff files, strobe and tcl for fault simulation are present. Modify only the SystemVerilog strobe file and the sff files. Modify the ```FAULT_MODEL_FSIM``` in the fsim.mk file to choose between the 3 available fault models.


## Fault models

The currently available fault models are (for atpg and fsim):
- **Stuck-at fault (SAF)**: specify ``FAULT_MODEL=saf``
- **Transition delay fault (TDF)**: specify ``FAULT_MODEL=tdf``
- **Small Delay faults (SDF)**: specify ``FAULT_MODEL=sdf``
- **Transient faults (TRN)**: specify ``FAULT_MODEL=tn`` (only for functional fault simulation)

## Auto-update the program counter from which the VC-Z01X injection shall start

The script `vlsi/fsim/strobe/find_main.py` can automatically set the address used by `START_INJECTION` in `vlsi/fsim/strobe/strobe_rocket.sv`.

Given a RISC-V ELF/binary and a label substring, it:
- runs RISC-V objdump (`-t`) on the binary,
- finds the symbol containing the label,
- rewrites `START_INJECTION` with the resolved address.

Example:
```bash
$ cd testyard
$ python3 vlsi/fsim/strobe/find_main.py tests/hello.riscv main
```

Using a custom objdump binary and strobe file:
```bash
$ python3 vlsi/fsim/strobe/find_main.py tests/hello.riscv main --objdump riscv64-unknown-linux-gnu-objdump --strobe-file vlsi/fsim/strobe/strobe_rocket.sv
```

If multiple symbols match, the script exits and prints candidate symbols so you can pass a more specific label. 
```bash
$ make fsim-syn benchmark=rocket START_INJECTION_LABEL=0x80000230
```

## Fault Simulating the RTL-level
For running RTL-Level fault simulation:
```bash 
$ cd vlsi
$ export TEST_PATH=absolute_path/tests/hello.riscv
$ make fsim-rtl benchmark=rocket BINARY=${TEST_PATH} LOADMEM=${TEST_PATH}
```

## Fault Simulating the Gate-level
For running post synthesis fault simulation:
```bash 
$ cd vlsi
$ export TEST_PATH=absolute_path/tests/hello.riscv
$ make fsim-syn benchmark=rocket BINARY=${TEST_PATH} LOADMEM=${TEST_PATH}
```

You can reuse fault list from the ATPG step (see next section for more details):
```bash 
$ cd vlsi
$ export TEST_PATH=absolute_path/tests/hello.riscv
$ make fsim-syn benchmark=rocket BINARY=${TEST_PATH} LOADMEM=${TEST_PATH} STANDARD_FAULT_FORMAT=/path/to/atpg_fault_list
```

> **_NOTE:_** For functional fault simulation of delay-based fault models the testbench clock period must be overwritten by the synthesis clock defined in the yaml file instead of the clock defined in the makefiles, in nanoseconds (without including the unit of measure):
> ```bash 
> $ cd vlsi
> $ export TEST_PATH=absolute_path/tests/hello.riscv
> $ make fsim-syn benchmark=rocket CLOCK_PERIOD=SYNTHESIS_CLOCK BINARY=${TEST_PATH} LOADMEM=${TEST_PATH} STANDARD_FAULT_FORMAT=/path/to/atpg_fault_list FSIM_CONF_FILE=vlsi_dir/fsim/example-fsim-rocket-sdf.yml 
> ```

You can increase the timeout cycles by setting the ``TIMEOUT_CYCLES=xx`` in the CLI (as for the ``CLOCK_PERIOD``). 
Their default values are:
* ``TIMEOUT_CYCLES=10000000`` in [./variables.mk](https://github.com/cad-polito-it/testyard/blob/working/cad_servers/variables.mk)
* ``CLOCK_PERIOD=1`` nanosecond in  [./sims/common-sim-flags.mk](https://github.com/cad-polito-it/testyard/blob/working/cad_servers/sims/common-sim-flags.mk)

You can use a custom TCL script for your fault simulation campaign, for example:
```bash
$ cd vlsi
$ export TEST_PATH=absolute_path/tests/hello.riscv
$ make fsim-syn benchmark=rocket FSIM_CAMPAIGN_TCL=vlsi_dir/fsim/script/fsim_sdf.tcl BINARY=${TEST_PATH} LOADMEM=${TEST_PATH} STANDARD_FAULT_FORMAT=/path/to/atpg_fault_list CLOCK_PERIOD=SYNTHESIS_CLOCK FSIM_CONF_FILE=vlsi_dir/fsim/example-fsim-rocket-sdf.yml FAULT_MODEL=sdf
```
## The FSIM folder in VLSI 

This repository packages fault-simulation collateral (fault lists, Tcl runtime scripts, and strobe logic) that can be consumed by Hammer:
- `fault_list/`
  - Pre-generated fault-definition files (`*.sff`) used by Zoix/VC FSim campaigns.
  - Includes examples for different fault models:
    - `gen_saf_*.sff`: stuck-at faults
    - `gen_tdf_*.sff`: transition-delay faults
    - `gen_tf_*.sff`: timing-window style generation

- `script/`
  - Tcl scripts that execute or assist fault simulation campaigns.
  - `fsim.tcl`: primary campaign runtime (concurrent run, fallback serial run, summary and hierarchical reports).

- `strobe/`
  - SystemVerilog strobe modules with `$fs_inject` and `$fs_strobe` hooks.
  - `strobe_rocket.sv`: Rocket-specific strobe and optional label-gated injection.
  - `strobe_boom.sv`, `strobe_boomv4.sv`: BOOM-specific strobe variants.
  - `find_injection_label.py`: utility to extract a symbol address from an ELF and patch `START_INJECTION_LABEL` in `strobe_rocket.sv`.

- `example-fsim-*.yml`
  - Example high-level input configs (`fsim.inputs`) for SAF/TDF/SDF style setups.
  - These describe fault locations, coverage formulae, optional elaboration flags, strobe modules, and constraints.

You can use the provided `example-fsim-*.yml` files as templates to create your own input configurations for different fault models and DUTs. The YAML format allows you to specify various parameters such as the fault list file, strobe module, coverage formula, and any additional constraints or filters for fault locations. Make sure to adjust the paths and parameters according to your specific design and verification needs.

You can use your custom strobe files, fault lists by passing the appropriate variables to the make command. In this case, the fault list definitions in the yml **are ignored**, for example:

```bash
$ make fsim \
    FSIM_INPUTS=example-fsim-saf.yml \
    FSIM_STROBE=strobe_rocket.sv \
    FSIM_FAULT_LIST=fault_list/gen_saf_rocket.sff
```

## Notes and Caveats

- Hierarchical paths in YAML/SFF/strobe files are design-specific (`ChipTop...`). Update if your elaborated hierarchy differs.
- `strobe_rocket.sv` depends on `wb_reg_pc` path and the `START_INJECTION_LABEL` macro; verify signal naming in your build.
- Keep fault location filters (`exclude: True/False`) aligned between your YAML and generated SFF to avoid mismatches.



# ATPG (Automatic Test Pattern Generation)

For ATPG, Synopsys TestMAX is used. The ATPG flow runs on the gate-level netlist produced by synthesis. The configuration is defined in [``atpg.mk``](https://github.com/cad-polito-it/testyard/blob/working/cad_servers/vlsi/atpg.mk) and the tool binary/version in [``example-tools.yml``](https://github.com/cad-polito-it/testyard/blob/working/cad_servers/vlsi/example-tools.yml).

## Running ATPG
For running ATPG on the post-synthesis netlist:
```bash 
$ cd vlsi
$ make atpg-syn benchmark=rocket
```

To re-run only the ATPG step (without re-running synthesis):
```bash 
$ cd vlsi
$ make redo-atpg-syn benchmark=rocket
```

## Fault models

The currently available fault models are (for atpg and fsim):
- **Stuck-at fault (SAF)**: specify ``FAULT_MODEL=saf``
- **Transition delay fault (TDF)**: specify ``FAULT_MODEL=tdf``
- **Small Delay faults (SDF)**: specify ``FAULT_MODEL=sdf``

By default, the fault model is **stuck-at fault (saf)**. To use a different fault model, pass the ``FAULT_MODEL`` variable:
```bash 
$ cd vlsi
$ make atpg-syn benchmark=rocket FAULT_MODEL=tdf
```

> **_NOTE:_** For correctly fault simulating SDF model you must use a timing annotated fault simulation (including standard delay format and slack based report from timing tool)

## Custom patterns and faults files

You can provide a custom patterns file or faults file via the ``PATTERNS_FILE`` and ``FAULTS_FILE`` variables:
```bash 
$ cd vlsi
$ make atpg-syn benchmark=rocket PATTERNS_FILE=path/to/patterns_file FAULTS_FILE=path/to/faults_file
```

> **_NOTE:_** For SDF you must generate the slack based report with a timing tool.

## Configuring ATPG

ATPG-related settings can be configured in [```testyard/vlsi/example-designs/nangate45-commercial.yml```](https://github.com/cad-polito-it/chipyard/blob/working/cad_servers/vlsi/example-designs/nangate45-commercial.yml). The main options are:

```yaml
# Set targeted fault coverage and number of generated test patterns from atpg
atpg:
  target_coverage: 98.5  
  max_test_patterns: 0 # Maximum values (0 = no constraints)

# Args for the "set_drc" command in TestMAX
atpg.testmax.set_drc_args: [""]
# Args for the "run_drc" command in TestMAX
atpg.testmax.run_drc_args: [""]
# Args for the "set_atpg" command in TestMAX (e.g., target coverage)
atpg.testmax.set_atpg_args: [""]
# Args for the "run_atpg" command in TestMAX (e.g., auto compression)
atpg.testmax.run_atpg_args: ["-auto_compression"]
# Args for the "run_fault_sim" command in TestMAX
atpg.testmax.run_fault_sim_args: [""]
# Options for the "stil2verilog" command (STIL and testbench name are already embedded)
atpg.testmax.stil2verilog_options: [""]
# To set the severity of rules during the build in TestMAX
atpg.testmax.build_rules: [
  {"rule_code" : "B5", "severity" : "warning"},
]
# Args for the "add_faults" command in TestMAX
atpg.testmax.add_faults_args: ["-all"]
```

# Useful information
The core unit is at the following hierarchy:

* For RTL-Level:
    ```verilog 
    TestDriver.testHarness.chiptop0.system.tile_prci_domain.element_reset_domain_${CORENAME}.core
    ```

* For Gate-Level:
    ```verilog 
    TestDriver.TestHarness.chiptop0.system.tile_prci_domain.element_reset_domain_${CORE_NAME}.core
    ```
The entire CPU (with branch prediction, fetch unit etc.) is at:
```verilog 
TestDriver.TestHarness.chiptop0.system.tile_prci_domain.element_reset_domain_${CORE_NAME}
```

The core name can have different values (depending on the designs), such as ``rockettile`` or ``boom_tile`` or ``ibex_tile``.

If you would like to execute a single action without runnign additional steps (e.g, in the following case the normal run would run the synthesis as well), you can run:
```bash
make redo-fsim-syn  benchmark=boom-small BINARY=${TEST_PATH} LOADMEM=${TEST_PATH} args="--only_step fsim"
```
> **_NOTE:_** The dependancies must be present.

For different benchmarks please see:
- [./vlsi/benchmarks.mk](https://github.com/cad-polito-it/testyard/blob/working/cad_servers/vlsi/benchmarks.mk)

For avoiding useless RTL generation or yml generation you can set the following variables to null:
``bash 
make redo-fsim-syn HAMMER_D_DEPS="" HAMMER_DEPENDENCIES="" benchmark=boom-small BINARY=${TEST_PATH} LOADMEM=${TEST_PATH}
``

## Reuse ATPG fault list for functional fault simulation
You can reuse the ATPG fault list for a functional fault simulation with the following command:
```bash 
make fsim-syn benchmark=boom-small BINARY=${TEST_PATH} LOADMEM=${TEST_PATH} STANDARD_FAULT_FORMAT=/PATH/to/atpg/fault_listlfa.fau
```
> **_NOTE:_** The fault list can be a custom fault list (fau or standard fault format).

# Notes on Specific Designs

The following designs require special consideration during synthesis and simulation:

* **Ibex Core**: Depending on the configuration, internal RAMs may be present and must be configured as black boxes during synthesis. Additionally, Design for Test (DfT) insertion requires careful routing due to clock gating and pending signals. 
    * Please refer to [this additional script](./vlsi/vlsi-ibex), which adds extra steps to the synthesis and ATPG flows.
    * You need to compile binary in 32-bit with the ``-march=rv32imc_zicsr -mabi=ilp32`` compiler's options (see [CMakeLists.txt](./tests/ibex/CMakeLists.txt) in ``tests/ibex``)
* **NVDLA**: Depending on the configuration, internal RAMs may be present and must be configured as black boxes during synthesis.
    * Please refer to [this additional script](./vlsi/vlsi-nvdla), which adds extra steps to the synthesis and ATPG flows.
* **CVA6**: Depending on the configuration, internal RAMs may be present and must be configured as black boxes during synthesis.
    * Please refer to [this additional script](./vlsi/vlsi-cva6), which adds extra steps to the synthesis and ATPG flows.

# Contacts 
Feel free to contribute with issues, PRs.
You can contact us at:
- Francesco Angione (francesco.angione@polito.it)
- Nicola di Gruttola giardino (nicola,.digruttola@polito.it)
- Gabriele Filipponi (gabriele.filipponi@polito.it)
- Giusy Iaria (giusy.iaria@polito.it)
