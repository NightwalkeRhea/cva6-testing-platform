"""Configuration: TOML in, dataclasses out, plus the directory layout of a run.

Two files, split by what makes them change:
  site.toml    where the machine is (paths, tools, core budget)
  campaigns/*.toml  what the experiment is (workspace, genome, GA knobs)
"""
from __future__ import annotations

import os
try:
    import tomllib
except ModuleNotFoundError:  # interpreters older than 3.11
    from flowkit import _toml as tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from flowkit import config as flowconfig
from flowkit import paths as flowpaths
from flowkit.ui import FlowError

from .fitness import DEFAULT_PRICE
from .genome import population_values

TOOL_ROOT = Path(__file__).resolve().parent.parent
SOCKET_PATH_MAX = 107


class ConfigError(Exception):
    pass


def _relative_to_config_or_tool(config_dir: Path, value: str) -> Path:
    """A relative path is looked for next to the config first, then next to the
    tool, so a config kept in conf/ can still name genome/ as a sibling."""
    p = Path(value)
    if p.is_absolute():
        return p
    beside = (config_dir / p).resolve()
    if beside.exists():
        return beside
    return (TOOL_ROOT / p).resolve()


def _load(path: Path) -> dict:
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError:
        raise ConfigError(f"config not found: {path}") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from None


def _get(d: dict, dotted: str, default: Any = None, required: bool = False) -> Any:
    node: Any = d
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            if required:
                raise ConfigError(f"missing required key '{dotted}'")
            return default
        node = node[part]
    return node


def apply_overrides(data: dict, overrides: list[str]) -> dict:
    """CLI overrides in 'a.b.c=value' form. Values are parsed as TOML scalars."""
    for item in overrides:
        if "=" not in item:
            raise ConfigError(f"override must be key=value: {item!r}")
        dotted, raw = item.split("=", 1)
        try:
            value = tomllib.loads(f"v = {raw}")["v"]
        except tomllib.TOMLDecodeError:
            value = raw
        node = data
        parts = dotted.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return data


@dataclass
class Cores:
    """The budget of this run, and how finely it is cut.

    per_ind is 1 by default because that is what measured best: the fault
    simulation of one individual splits into unequal tasks that cannot be
    balanced across many workers, while many individuals average out. One
    individual on the whole budget leaves 59% of it idle; one core each leaves
    6%. Raise it only to shorten the latency of a single individual.
    """

    total: int = 16
    per_ind: int = 1

    @property
    def in_flight(self) -> int:
        return max(1, self.total // self.per_ind)

    def check(self) -> None:
        if not 1 <= self.per_ind <= self.total:
            raise ConfigError("cores.per_ind must be between 1 and cores.total")


@dataclass
class Tools:
    ugp3: str = "ugp3"
    vc_fcc: str = "vc_fcc"
    vc_fcm: str = "vc_fcm"
    cc: str = "riscv64-unknown-elf-gcc"
    objdump: str = "riscv64-unknown-elf-objdump"


@dataclass
class Site:
    root: Path                  # directory holding site.toml
    repo: Path
    build: Path
    runs: Path
    tools: Tools
    cores: Cores
    sim_seed: int = 20260806
    clock_ns: float = 1.0
    infra_retries: int = 2
    infra_stall_seconds: float = 1800.0
    infra_on_stall: str = "skip"     # skip | abort

    @classmethod
    def load(cls, path: Path, overrides: list[str] | None = None) -> "Site":
        data = apply_overrides(_load(path), overrides or [])
        root = path.parent.resolve()
        # This platform keeps Chipyard in testyard/. A plain Chipyard checkout
        # still uses the parent directory, as Piero's original flow expects.
        platform_repo = TOOL_ROOT.parent / "testyard"
        default_repo = platform_repo if platform_repo.is_dir() else TOOL_ROOT.parent
        repo = Path(_get(data, "paths.repo", str(default_repo))).expanduser()
        # The one path that cannot be worked out is the checkout. The build
        # directory under it is named after the technology and the
        # configuration, so it is looked for when it was not written down.
        # A build tag names one synthesis among several, the same way the flow
        # driver does; CY_BUILD is the same choice made for both at once.
        told = _get(data, "paths.build", "")
        tag = str(_get(data, "paths.build_tag", os.environ.get("CY_BUILD", "")))
        if told:
            build = repo / told
        elif tag:
            try:
                build = flowpaths.Paths(repo, flowpaths.build_tag(tag), flowconfig.load()).obj_dir
            except FlowError as exc:
                raise ConfigError(exc.render()) from None
        else:
            try:
                build = flowpaths.find_build(repo)
            except flowpaths.NotFound as exc:
                raise ConfigError(str(exc)) from None
        runs = Path(_get(data, "paths.runs", "evosbst-runs")).expanduser()
        if not runs.is_absolute():
            # Beside the checkout, never inside it: run data is not part of that
            # repository, and a clean of the build must not reach it. An
            # absolute path moves it anywhere, a disk of its own included.
            runs = repo.parent / runs
        site = cls(
            root=root,
            repo=repo,
            build=build,
            runs=runs,
            tools=Tools(**{k: v for k, v in _get(data, "tools", {}).items()}),
            cores=Cores(**{k: v for k, v in _get(data, "cores", {}).items()}),
            sim_seed=int(_get(data, "sim.seed", 20260806)),
            clock_ns=float(_get(data, "sim.clock_ns", 1.0)),
            infra_retries=int(_get(data, "infra.retries", 2)),
            infra_stall_seconds=float(_get(data, "infra.stall_seconds", 1800.0)),
            infra_on_stall=str(_get(data, "infra.on_stall", "skip")),
        )
        site.cores.check()
        if site.infra_on_stall not in ("skip", "abort"):
            raise ConfigError("infra.on_stall must be 'skip' or 'abort'")
        return site


@dataclass
class Ga:
    mu: int = 30
    nu: int = 60
    lam: int = 48
    sigma: float = 0.9
    inertia: float = 0.9
    max_age: int = 3
    elite: int = 0
    clone_scaling: float = 0.0
    tau: int = 2
    generations: int = 30
    max_evaluations: int = 100000
    max_hours: int = 168
    seed: int = 0                # 0 = pick one and record it


@dataclass
class Campaign:
    name: str
    dut: str                     # what is under test, as written on the command line
    file: Path
    workspace: str
    workspace_mode: str
    # workspace source, all relative to Site.build unless absolute
    src_rundir: str
    src_tcl: str
    src_sff: str
    zoix_campaign: str
    probe_rundir: str
    probe_max_cycles: int
    fsim_cycles_factor: float
    fsim_cycles_floor: int
    fsim_cycles_cap: int
    coats: bool
    force_regs: bool
    genome_file: Path
    # The uGP3 population settings written by hand, or None to render them
    # from [ga] alone.
    ga_file: Path | None
    boot: str
    pipeline: list[str]
    ga: Ga
    # Coverage points charged per simulated cycle. Not part of the inputs-hash:
    # it changes which individual is preferred, never a measured number, so a
    # run that only changes it still reuses the cache.
    cycle_price: float = DEFAULT_PRICE
    inputs_hash: str = ""        # filled by manifest.freeze()

    @classmethod
    def load(cls, path: Path, overrides: list[str] | None = None) -> "Campaign":
        data = apply_overrides(_load(path), overrides or [])
        root = path.parent.resolve()
        genome = _relative_to_config_or_tool(
            root, _get(data, "genome.file", "genome/rv64_alu.xml")
        )
        ga_raw = dict(_get(data, "ga", {}))
        if "lambda" in ga_raw:
            ga_raw["lam"] = ga_raw.pop("lambda")
        # The file says the GA; a key in [ga] or on the command line overrides it.
        ga_file_s = str(ga_raw.pop("file", "conf/population.settings.xml"))
        ga_file = _relative_to_config_or_tool(root, ga_file_s) if ga_file_s else None
        if ga_file is not None:
            ga_raw = {**population_values(ga_file), **ga_raw}
        boot = str(_get(data, "genome.boot", "newlib"))
        if boot not in ("newlib", "bare"):
            raise ConfigError("genome.boot must be 'newlib' or 'bare'")
        camp = cls(
            name=str(_get(data, "name", required=True)),
            dut=str(_get(data, "dut", _get(data, "name"))),
            file=path.resolve(),
            workspace=str(_get(data, "workspace", _get(data, "name"))),
            workspace_mode=str(_get(data, "workspace_mode", "snapshot")),
            src_rundir=str(_get(data, "source.rundir", "fsim-syn-rundir")),
            # Left out on purpose = look for it in the rundir; see sources.py.
            src_tcl=str(_get(data, "source.tcl", "")),
            src_sff=str(_get(data, "source.sff", "")),
            zoix_campaign=str(_get(data, "source.campaign", "chiptop0")),
            probe_rundir=str(_get(data, "probe.rundir", "sim-syn-rundir")),
            probe_max_cycles=int(_get(data, "probe.max_cycles", 400000)),
            fsim_cycles_factor=float(_get(data, "fsim.cycles_factor", 1.5)),
            fsim_cycles_floor=int(_get(data, "fsim.cycles_floor", 60000)),
            fsim_cycles_cap=int(_get(data, "fsim.cycles_cap", 4000000)),
            coats=bool(_get(data, "fsim.coats", True)),
            force_regs=bool(_get(data, "force_regs", False)),
            genome_file=genome,
            ga_file=ga_file,
            boot=boot,
            pipeline=list(_get(data, "pipeline", ["build", "probe", "fsim"])),
            ga=Ga(**ga_raw),
            cycle_price=float(_get(data, "fitness.cycle_price", DEFAULT_PRICE)),
        )
        if camp.ga.lam % 1 or camp.ga.lam < 1:
            raise ConfigError("ga.lambda must be a positive integer")
        if camp.cycle_price < 0:
            raise ConfigError("fitness.cycle_price must not be negative")
        return camp


@dataclass
class RunLayout:
    """Where a run keeps its things, and nothing of it inside the build tree.

    An individual lives in `individuals/<uid>` while it is being evaluated and
    is moved to `individuals/processed/<uid>` when it is done. The move is a
    rename, so it is atomic and free, and it makes `ls` the status display:
    whatever is still directly under `individuals/` is what is running now.
    """

    runs_root: Path
    run_id: str

    @property
    def root(self) -> Path:
        return self.runs_root / self.run_id

    @property
    def input(self) -> Path:
        """The frozen experiment: genome, GA settings, scaffolding."""
        return self.root / "input"

    @property
    def ugp3(self) -> Path:
        """uGP3's own working directory: status.xml, statistics.csv, its log."""
        return self.root / "ugp3"

    @property
    def individuals(self) -> Path:
        return self.root / "individuals"

    @property
    def processed(self) -> Path:
        return self.individuals / "processed"

    @property
    def best(self) -> Path:
        return self.root / "best"

    @property
    def journal(self) -> Path:
        return self.root / "journal.csv"

    @property
    def events(self) -> Path:
        """Retries and give-ups, one JSON object per line."""
        return self.root / "events.jsonl"

    @property
    def manifest(self) -> Path:
        return self.root / "manifest.json"

    @property
    def state(self) -> Path:
        return self.root / "state.json"

    @property
    def lock(self) -> Path:
        return self.root / "run.lock"

    @property
    def cores_request(self) -> Path:
        """A new core budget left by 'evosbst.py cores', taken by the run."""
        return self.root / "cores.json"

    @property
    def socket_dir(self) -> Path:
        """Private to the user (0700): whoever reaches the socket can have the
        run evaluate, so it never sits in a shared directory."""
        return self.root / ".sock"

    @property
    def socket(self) -> Path:
        return self.socket_dir / "eval.sock"

    def socket_fits(self) -> bool:
        """A unix socket address is a fixed field of 108 bytes, terminator
        included; a longer path fails only at bind, after the run has started."""
        return len(os.fsencode(self.socket)) <= SOCKET_PATH_MAX

    def workspaces(self) -> Path:
        return self.runs_root / "_ws"

    def cache(self, inputs_hash: str) -> Path:
        return self.runs_root / "_cache" / inputs_hash

    def create(self) -> None:
        for d in (self.root, self.input, self.ugp3, self.individuals, self.processed):
            d.mkdir(parents=True, exist_ok=True)


def slug(text: str) -> str:
    """A DUT is written 'alu/div'; a directory cannot be."""
    return "".join(c if c.isalnum() or c in "._-" else "-" for c in text).strip("-") or "run"


def make_run_id(dut: str, runs_root: Path) -> str:
    """<date>_<dut>, with a counter when that name is already taken."""
    base = f"{datetime.now().strftime('%Y%m%d')}_{slug(dut)}"
    run_id = base
    n = 2
    while (runs_root / run_id).exists():
        run_id = f"{base}_{n}"
        n += 1
    return run_id
