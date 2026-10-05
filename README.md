# CVA6 testing platform
A unified validation platform for CVA6. It brings together CAD Chipyard, CAD Hammer, and MicroGP3 to run experiments with CVA6, so you can use, modify, and connect the flow easily without needing to manage the outer projects as forks or submodules.

## Getting Started
First you need to read this README to know how the repository is managed, and then, to dive in, you can start with [testyard/README.md](testyard/README.md) to know how to configure and run Chipyard.
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