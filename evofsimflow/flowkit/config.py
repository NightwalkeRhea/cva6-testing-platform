"""conf/flow.toml in, a checked FlowConf out.

Values are checked here, once, so a command never discovers half way through
that a knob holds something the tools cannot take.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # interpreters older than 3.11
    from . import _toml as tomllib

from . import hier
from .ui import FlowError

TOOL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONF = TOOL_ROOT / "conf" / "flow.toml"

INITREG_COMPILE = ("random", "off")
INITREG_RUN = ("0", "1", "random")
DUMP_MODES = ("off", "lean", "full")
STROBE_SCOPES = ("module", "ports")
TASK_SPLITS = ("auto", "on", "off")


@dataclass
class FlowConf:
    file: Path
    # part A
    binary: str
    dut: str
    faults: str
    strobe_scope: str
    strobe_ports: list[str]
    dump: str
    dump_core_depth: int
    initreg_compile: str
    initreg_run: str
    smoke: bool
    smoke_test: str
    smoke_max_cycles: int
    smoke_retries: int
    fault_model: str
    inject_at: str
    task_split: str
    maxcyc_par: int
    maxcyc_fsim: int
    maxcyc_probe: int
    jobs: int
    force_regs: bool
    # part B
    benchmark: str
    technology: str
    long_name: str
    build_prefix: str
    gen_src_prefix: str
    overlay: str
    tools_conf: str
    strobe_template: str
    fsim_tcl_template: str
    fault_models: list[str]
    reset_delay_ns: float
    clock_ns: float
    t2main_cycles: int
    tb_top: str
    core_rel: str
    aliases: dict[str, str] = field(default_factory=dict)

    @property
    def fault_root(self) -> str:
        """Z01X wants fault locations relative to ChipTop."""
        return f"ChipTop.{self.core_rel}"

    @property
    def strobe_root(self) -> str:
        """$fs_strobe and the dump want the full testbench path."""
        return f"{self.tb_top}.{self.core_rel}"

    @property
    def tb_root(self) -> str:
        return self.tb_top.split(".", 1)[0]


def load(path: Path | None = None, overrides: list[str] | None = None) -> FlowConf:
    path = path or DEFAULT_CONF
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError:
        raise FlowError(f"configuration not found: {path}") from None
    except tomllib.TOMLDecodeError as exc:
        raise FlowError(f"{path}: {exc}") from None
    _apply(data, overrides or [])

    g = _Getter(data, path)
    conf = FlowConf(
        file=path,
        binary=g.text("study.binary"),
        dut=g.text("study.dut"),
        faults=g.text("study.faults", ""),
        strobe_scope=g.choice("study.strobe_scope", STROBE_SCOPES),
        strobe_ports=g.strlist("study.strobe_ports", []),
        dump=g.choice("dump.mode", DUMP_MODES),
        dump_core_depth=g.integer("dump.core_depth", 0),
        initreg_compile=g.choice("initreg.compile", INITREG_COMPILE),
        initreg_run=g.choice("initreg.run", INITREG_RUN),
        smoke=g.flag("smoke.enabled"),
        smoke_test=g.text("smoke.test", ""),
        smoke_max_cycles=g.integer("smoke.max_cycles"),
        smoke_retries=g.integer("smoke.retries", 0),
        fault_model=g.text("fsim.model"),
        inject_at=g.text("fsim.inject_at"),
        task_split=g.choice("fsim.task_split", TASK_SPLITS),
        maxcyc_par=g.integer("cycles.par"),
        maxcyc_fsim=g.integer("cycles.fsim"),
        maxcyc_probe=g.integer("cycles.probe"),
        jobs=g.integer("run.jobs"),
        force_regs=g.flag("run.force_regs"),
        benchmark=g.word("design.benchmark"),
        technology=g.word("design.technology"),
        long_name=g.word("design.long_name"),
        build_prefix=g.word("design.build_prefix"),
        gen_src_prefix=g.word("design.gen_src_prefix"),
        overlay=g.relpath("design.overlay"),
        tools_conf=g.relpath("design.tools_conf"),
        strobe_template=g.relpath("design.strobe_template"),
        fsim_tcl_template=g.relpath("design.fsim_tcl_template"),
        fault_models=g.strlist("design.fault_models"),
        reset_delay_ns=g.number("timing.reset_delay_ns"),
        clock_ns=g.number("timing.clock_ns", positive=True),
        t2main_cycles=g.integer("timing.t2main_cycles", 0),
        tb_top=hier.check_hier(g.text("hierarchy.tb_top"), "hierarchy.tb_top"),
        core_rel=hier.check_hier(g.text("hierarchy.core"), "hierarchy.core"),
        aliases={k: hier.check_hier(str(v), f"hierarchy.aliases.{k}")
                 for k, v in g.table("hierarchy.aliases").items()},
    )
    if conf.fault_model not in conf.fault_models:
        raise FlowError(f"{path}: fsim.model wants one of: {' '.join(conf.fault_models)}"
                        f"  (given: '{conf.fault_model}')")
    for port in conf.strobe_ports:
        hier.check_hier(port, "study.strobe_ports")
    hier.check_hier(conf.dut, "study.dut")
    hier.check_hier(conf.faults, "study.faults")
    check_inject(conf.inject_at)
    hier.check_test_path(conf.binary)
    if conf.smoke_test:
        hier.check_test_path(conf.smoke_test)
    return conf


def check_inject(spec: str) -> str:
    if spec in ("main", "reset") or spec.isdigit():
        return spec
    raise FlowError(f"--inject-at wants 'main', 'reset' or a number: {spec}")


def _apply(data: dict, overrides: list[str]) -> None:
    """'a.b=value' pairs, values read as TOML scalars (bare words as strings)."""
    for item in overrides:
        if "=" not in item:
            raise FlowError(f"--set wants key=value: '{item}'")
        dotted, raw = item.split("=", 1)
        try:
            value = tomllib.loads(f"v = {raw}")["v"]
        except tomllib.TOMLDecodeError:
            value = raw
        node = data
        parts = dotted.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                raise FlowError(f"--set {dotted}: '{part}' is not a table")
        node[parts[-1]] = value


class _Getter:
    _MISSING = object()

    def __init__(self, data: dict, path: Path):
        self.data = data
        self.path = path

    def _raw(self, dotted: str, default: Any) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                if default is self._MISSING:
                    raise FlowError(f"{self.path}: missing key '{dotted}'")
                return default
            node = node[part]
        return node

    def _bad(self, dotted: str, want: str, value: Any) -> FlowError:
        return FlowError(f"{self.path}: {dotted} wants {want}: {value!r}")

    def text(self, dotted: str, default: Any = _MISSING) -> str:
        v = self._raw(dotted, default)
        if isinstance(v, bool) or not isinstance(v, (str, int)):
            raise self._bad(dotted, "a string", v)
        v = str(v)
        if "\n" in v or "\0" in v:
            raise self._bad(dotted, "a single line", v)
        return v

    def word(self, dotted: str) -> str:
        v = self.text(dotted)
        if not v or not all(c.isalnum() or c in "._-" for c in v):
            raise self._bad(dotted, "a plain name", v)
        return v

    def relpath(self, dotted: str) -> str:
        v = self.text(dotted)
        hier.check_test_path(v)
        if v.startswith("/") or ".." in Path(v).parts:
            raise self._bad(dotted, "a path inside vlsi/", v)
        return v

    def choice(self, dotted: str, allowed: tuple[str, ...]) -> str:
        v = self.text(dotted)
        if v not in allowed:
            raise FlowError(f"{self.path}: {dotted} wants one of: {' '.join(allowed)}"
                            f"  (given: '{v}')")
        return v

    def integer(self, dotted: str, minimum: int = 1) -> int:
        v = self._raw(dotted, self._MISSING)
        if isinstance(v, bool) or not isinstance(v, int) or v < minimum:
            raise self._bad(dotted, f"an integer >= {minimum}", v)
        return v

    def number(self, dotted: str, positive: bool = False) -> float:
        v = self._raw(dotted, self._MISSING)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0 \
                or (positive and v == 0):
            raise self._bad(dotted, "a positive number", v)
        return float(v)

    def flag(self, dotted: str) -> bool:
        v = self._raw(dotted, self._MISSING)
        if not isinstance(v, bool):
            raise self._bad(dotted, "true or false", v)
        return v

    def strlist(self, dotted: str, default: Any = _MISSING) -> list[str]:
        v = self._raw(dotted, default)
        if not isinstance(v, list) or not all(isinstance(i, str) for i in v):
            raise self._bad(dotted, "a list of strings", v)
        return list(v)

    def table(self, dotted: str) -> dict:
        v = self._raw(dotted, {})
        if not isinstance(v, dict):
            raise self._bad(dotted, "a table", v)
        return v
