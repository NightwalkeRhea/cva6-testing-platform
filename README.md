# CVA6 testing platform
A unified validation platform for CVA6. It brings together CAD Chipyard, CAD Hammer, and MicroGP3 to run experiments with CVA6, so you can use, modify, and connect the flow easily without needing to manage the outer projects as forks or submodules.

## Getting Started
First you need to read this README to know how the repository is managed, and then, to dive in, you can start with [testyard/README.md](testyard/README.md) to know how to configure and run Chipyard.

If you want to run the RAW experiment, follow the commands in
[raw-evolution/README.md](raw-evolution/README.md). It explains the environment,
campaign, validation, and MicroGP flow in order.

### How the repositories are managed

Chipyard, Hammer, and MicroGP3 are imported using Git subtree.
Their outer files belong to this repository and can be edited and
committed together.

Chipyard's internal dependencies remain Git submodules. Their paths
are declared in the [.gitmodules](.gitmodules) at the root of this repository.

### Clone
Simple! 

```bash
git clone git@github.com:NightwalkeRhea/cva6-testing-platform.git
cd cva6-testing-platform
```

### Set up Testyard

Run this once after a fresh clone. It creates the Conda environment and
initializes the common Chipyard modules, the CVA6 wrapper, and its RTL.

Do not activate the EDA tools yet. Their libraries can interfere with Conda.

```bash
cd "$HOME/cva6-testing-platform"
activate_proxies

./testyard/build-setup.sh --use-lean-conda --cva6-only
source testyard/env.sh

python -m pip install -e ./hammer
python -c 'import hammer.fsim.vc_zoix as p; print(p.__file__)'

deactivate_proxies
```

`activate_proxies` and `deactivate_proxies` are helpers on the CAD server. Skip
those two lines on a machine that does not need a proxy.

The final Python command must print a path inside this repository's `hammer/`
directory. The setup can take a while, but it only needs to run once for each
clone.

### Make changes

Just like you're managing a normal repo.
```bash
git pull
# Edit the files.
git add <files-you-changed>
git commit -m "Describe the changes"
git push origin <your-branch-name>
```
Consider that any change inside a retained submodule inside Testyard must be committed and published in that submodule first. Then commit its updated reference here.

If you need to import recent changes from Testyard upstream, you can go with
```bash
git subtree pull --prefix=testyard \
https://github.com/cad-polito-it/chipyard.git \
working/cad_servers --squash
```
### Using the CAD guide

Follow [testyard/README_cad.md](testyard/README_cad.md) for the CAD flow. Chipyard and Hammer
are already included here, so skip its instructions to clone them.

In this platform, Chipyard lives in [testyard/](testyard/) and Hammer lives in
[hammer/](hammer/). Adjust paths in the guide accordingly.


## Licenses

The imported projects retain their original licenses. See the license
files within each project.
