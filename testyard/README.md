![TESTYARD](.img/TESTYARD_v1.png)

# VLSI Benchmarks for Hardware Testing

![Status](https://img.shields.io/badge/Status-Research--Prototype-orange.svg)![Badge](https://hitscounter.dev/api/hit?url=https%3A%2F%2Fgithub.com%2Fcad-polito-it%2Fchipyard&label=Visitors&icon=github&color=%23198754&message=&style=flat&tz=UTC)

This repository implements a playground for an **Agile Framework for Prototyping Hardware Testing Structural and Functional Methodologies**. 

It leverages the [Chipyard](https://github.com/ucb-bar/chipyard) ecosystem and extends the [HAMMER](https://github.com/ucb-bar/hammer) VLSI flow. This repository is a fork of the main Chipyard and Hammer repositories (please see the Chipyard [README](https://github.com/cad-polito-it/chipyard/blob/working/cad_servers/README_chipyard.md) for further details).

---

## Overview
As modern silicon devices increase in complexity, traditional benchmarks (ISCAS-85, ITC-99, etc.) are becoming insufficient. Furthermore, test engineers often spend excessive time manually adapting EDA-dependent scripts, which is error-prone and inefficient.

This framework provides an **Unified Hardware Testing Support** for both **Structural** and **Functional** testing:
* **Modern Benchmarks:** Access to complex, open-source SoC designs (Rocket Chip, BOOM, hardware accelerators) via Chipyard.
* **Agile VLSI Flow:** Automation and abstraction of EDA tool-dependent commands using Python-based wrappers in Hammer.

The framework is built on two primary pillars:

1. **Chipyard:** Handles system-to-RTL design. It provides a library of ready-to-use IPs, including in-order/out-of-order processors, vector processors, and various interconnects.
2. **Hammer (Highly Agile Masks Made Effortlessly from RTL):** Handles the RTL-to-physical design flow. The following steps have been introduced for hardware testing, covering different fault models (Stuck-at faults, Transition Delay faults, Small Delay faults, Transient faults):
    * **DfT Insertion:** Automated Design-for-Testability (scan chain insertion) during the synthesis phase.
    * **ATPG Flow:** A dedicated Automatic Test Pattern Generation infrastructure.
    * **Functional Fault Simulation:** Evaluation of firmware-based tests through a specialized infrastructure.

> **NOTE:** This framework aims to allow researchers to focus on testing methodologies rather than "bug-hunting" in adapted scripts and "benchmark-hunting".

---

## Getting Started

To get started with these VLSI benchmarks, you can proceed in two ways:
* **Full Setup:** If you are ready to dive in, please visit this [README](https://github.com/cad-polito-it/chipyard/blob/working/cad_servers/README_cad.md) for a comprehensive guide on installing the framework and using the various steps. Both Structural and Functional testing flows are supported.
* **Pre-synthesized Benchmarks:** If you would like to use your existing workspace, you can find a set of synthesized benchmarks [here](https://github.com/cad-polito-it/chipyard/releases). For logic simulation of existing released benchmarks, you will need to set up your own simulation infrastructure.


### How to use the full setup

After completing environment installation (see [README_cad.md](README_cad.md)), you can drive the VLSI flow directly from `vlsi/` using the benchmark selectors defined in `vlsi/benchmarks.mk`.

Common selectors:
- `benchmark` (default: `none`, must be set)
- `technology_name` (default: `nangate45`)
- `toolchain` (default: `commercial`)
- `EXTRA_CONFS` (optional extra Hammer config files)

Example shell setup:

```bash
cd vlsi
export BENCH=rocket
export TECH=nangate45
export TOOLCHAIN=commercial
export TEST_PATH=$(realpath ../tests/hello.riscv)
```

Please refer to [README_cad.md](README_cad.md) for in-depth explanations.

#### 1) Synthesis

Run synthesis for the selected benchmark/technology/toolchain:

```bash
make syn benchmark=${BENCH} technology_name=${TECH} toolchain=${TOOLCHAIN}
```

Run only the synthesis stage again (assuming dependencies already exist):

```bash
make redo-syn benchmark=${BENCH} technology_name=${TECH} toolchain=${TOOLCHAIN}
```

#### 2) Simulation

RTL simulation:

```bash
make sim-rtl benchmark=${BENCH} technology_name=${TECH} toolchain=${TOOLCHAIN} BINARY=${TEST_PATH} LOADMEM=1
```

Gate-level (post-synthesis) simulation:

```bash
make sim-syn benchmark=${BENCH} technology_name=${TECH} toolchain=${TOOLCHAIN} BINARY=${TEST_PATH} LOADMEM=${TEST_PATH}
```

Debug waveform variants:

```bash
make sim-rtl-debug benchmark=${BENCH} technology_name=${TECH} toolchain=${TOOLCHAIN} BINARY=${TEST_PATH} LOADMEM=1
make sim-syn-debug benchmark=${BENCH} technology_name=${TECH} toolchain=${TOOLCHAIN} BINARY=${TEST_PATH} LOADMEM=${TEST_PATH}
```

To enable GUI-based simulation:

```bash
export SIM_USE_GUI=true
```

#### 3) Fault simulation (FSIM)

Supported fault models in this flow: `saf`, `tdf`, `sdf`, `tn`.

RTL fault simulation:

```bash
make fsim-rtl benchmark=${BENCH} technology_name=${TECH} toolchain=${TOOLCHAIN} \
  BINARY=${TEST_PATH} LOADMEM=${TEST_PATH} FAULT_MODEL=saf
```

Gate-level fault simulation:

```bash
make fsim-syn benchmark=${BENCH} technology_name=${TECH} toolchain=${TOOLCHAIN} \
  BINARY=${TEST_PATH} LOADMEM=${TEST_PATH} FAULT_MODEL=saf
```

Reuse an ATPG-generated or custom fault list:

```bash
make fsim-syn benchmark=${BENCH} technology_name=${TECH} toolchain=${TOOLCHAIN} \
  BINARY=${TEST_PATH} LOADMEM=${TEST_PATH} FAULT_MODEL=saf \
  STANDARD_FAULT_FORMAT=/absolute/path/to/fault_list.sff
```

For SDF campaigns, include timing data generated by timing analysis and use an SDF-oriented FSIM config:

```bash
make fsim-syn benchmark=${BENCH} technology_name=${TECH} toolchain=${TOOLCHAIN} \
  BINARY=${TEST_PATH} LOADMEM=${TEST_PATH} FAULT_MODEL=sdf \
  CLOCK_PERIOD=<synthesis_clock_ns> FSIM_CONF_FILE=./fsim/example-fsim-sdf.yml
```

#### 4) ATPG

Run ATPG on the post-synthesis netlist:

```bash
make atpg-syn benchmark=${BENCH} technology_name=${TECH} toolchain=${TOOLCHAIN} FAULT_MODEL=saf
```

Run ATPG only (without re-running synthesis), when synthesis artifacts are already available:

```bash
make redo-atpg-syn benchmark=${BENCH} technology_name=${TECH} toolchain=${TOOLCHAIN} FAULT_MODEL=saf
```

Select other ATPG fault models (`tdf` or `sdf`) with:

```bash
make atpg-syn benchmark=${BENCH} technology_name=${TECH} toolchain=${TOOLCHAIN} FAULT_MODEL=tdf
```

Optionally provide custom patterns/fault files:

```bash
make atpg-syn benchmark=${BENCH} technology_name=${TECH} toolchain=${TOOLCHAIN} \
  FAULT_MODEL=saf PATTERNS_FILE=/absolute/path/patterns.stil FAULTS_FILE=/absolute/path/faults.list
```

---
## Citation

If you use this framework in your research, please cite the following publication (not yet available in IEEExplorer, presented at [IEEE ETS 2026](https://ets2026.uniwa.gr/conference-program/)):

```
@INPROCEEDINGS{11591740,
  author={Angione, Francesco and Bernardi, Paolo and Giardino, Nicola Di Gruttola and Filipponi, Gabriele and Iaria, Giusy and Perlo, Giacomo and Pomeranz, Irith and Porsia, Antonio and Ruospo, Annachiara and Sanchez, Ernesto and Turco, Vittorio},
  booktitle={2026 IEEE European Test Symposium (ETS)}, 
  title={Advances in Testing and Reliability Benchmarks}, 
  year={2026},
  volume={},
  number={},
  pages={1-10},
  keywords={Modeling;Testing;Design methodology;Hardware;Complexity theory;Printing;Simulation;Accuracy;Computer architecture;Architecture;Testing;Reliability;Benchmarks;RISC-V},
  doi={10.1109/ETS69887.2026.11591740}}

```


If used for research, please cite Chipyard and Hammer by the following publications:

```
@article{chipyard,
  author={Amid, Alon and Biancolin, David and Gonzalez, Abraham and Grubb, Daniel and Karandikar, Sagar and Liew, Harrison and Magyar,   Albert and Mao, Howard and Ou, Albert and Pemberton, Nathan and Rigge, Paul and Schmidt, Colin and Wright, John and Zhao, Jerry and Shao, Yakun Sophia and Asanovi\'{c}, Krste and Nikoli\'{c}, Borivoje},
  journal={IEEE Micro},
  title={Chipyard: Integrated Design, Simulation, and Implementation Framework for Custom SoCs},
  year={2020},
  volume={40},
  number={4},
  pages={10-21},
  doi={10.1109/MM.2020.2996616},
  ISSN={1937-4143},
}
```


```
@inproceedings{10.1145/3489517.3530672,
author = {Liew, Harrison and Grubb, Daniel and Wright, John and Schmidt, Colin and Krzysztofowicz, Nayiri and Izraelevitz, Adam and Wang, Edward and Asanovi\'{c}, Krste and Bachrach, Jonathan and Nikoli\'{c}, Borivoje},
title = {Hammer: a modular and reusable physical design flow tool: invited},
year = {2022},
isbn = {9781450391429},
publisher = {Association for Computing Machinery},
address = {New York, NY, USA},
url = {https://doi.org/10.1145/3489517.3530672},
doi = {10.1145/3489517.3530672},
abstract = {Process technology scaling and hardware architecture specialization have vastly increased the need for chip design space exploration, while optimizing for power, performance, and area. Hammer is an open-source, reusable physical design (PD) flow generator that reduces design effort and increases portability by enforcing a separation among design-, tool-, and process technology-specific concerns with a modular software architecture. In this work, we outline Hammer's structure and highlight recent extensions that support both physical chip designers and hardware architects evaluating the merit and feasibility of their proposed designs. This is accomplished through the integration of more tools and process technologies---some open-source---and the designer-driven development of flow step generators. An evaluation of chip designs in process technologies ranging from 130nm down to 12nm across a series of RISC-V-based chips shows how Hammer-generated flows are reusable and enable efficient optimization for diverse applications.},
booktitle = {Proceedings of the 59th ACM/IEEE Design Automation Conference},
pages = {1335–1338},
numpages = {4},
location = {San Francisco, California},
series = {DAC '22}
}
```

# Contacts 
Feel free to contribute with issues, PRs.
You can contact us at:
- Francesco Angione (francesco.angione@polito.it)
- Nicola di Gruttola Giardino (nicola.digruttola@polito.it)
- Gabriele Filipponi (gabriele.filipponi@polito.it)
- Giusy Iaria (giusy.iaria@polito.it)
