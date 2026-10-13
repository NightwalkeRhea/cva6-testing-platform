# RAW evolution

This folder evolves short RISC-V programs that stress RAW handling in CVA6.
It reuses the existing RTL simulator and its existing fault list. It does not
run synthesis or rebuild the simulator.

## Files

- `select_faults.py` chooses 500 faults from the completed report.
- `seed.S` is the first program that we score.
- `genome.xml` gives MicroGP safe producer-consumer patterns.
- `evaluate.py` compiles one program and measures coverage.
- `config.py` holds paths, the fixed seed, and the fault quotas.

## First check on the server

Start from the shell that can run the EDA tools:

```bash
cd "$HOME/cva6-testing-platform"
source testyard/env.sh
deactivate_proxies
activate_eda_tools

export RISCV="$HOME/cva6-testing-platform/testyard/.conda-env/riscv-tools"
export PATH="$RISCV/bin:$PATH"

cd raw-evolution
make select
make check
make prepare
```

`make prepare` compiles the seed and writes its Tcl file, but runs no faults.
Review that Tcl file first. After it passes review, `make seed` runs only the
selected 500 faults. Inspect that result before starting MicroGP.

## One small generation

```bash
make evolve
```

The first setup evaluates one program at a time. Each campaign may use up to
16 workers. Results remain under `runs/`, indexed by the program hash.
