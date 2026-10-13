"""The genome, and the fixed code around it.

Everything the evolved region is allowed to do is decided here, and the
decisions are all of the same kind: make a broken program impossible to write
rather than expensive to discover. Only register-register and immediate
arithmetic, no memory with a computed address, no branch to a computed target,
and a prologue and epilogue that guarantee the return.

The signature is part of that scaffolding. Without a fold after each generated
instruction only the last value left in each register survives to the end, so
most of what the program computed would be dead before it could be observed,
and a fault that corrupted it would be masked by construction.

A genome comes in one of two forms, told apart by the file name: the uGP3
constraints file itself (genome/*.xml), used as written once it has been
checked for the mistakes uGP3 accepts in silence; or a short description
(genome/*.toml) that is rendered into one.
"""
from __future__ import annotations

try:
    import tomllib
except ModuleNotFoundError:  # interpreters older than 3.11
    from flowkit import _toml as tomllib
import re
import xml.etree.ElementTree as ET
import xml.parsers.expat
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

from flowkit import progs

from .fitness import DIM

FOLDS = {
    "none": "",
    "add": "    add {acc}, {acc}, {dst}\n",
    "mul": "    mul {acc}, {acc}, {mix}\n    add {acc}, {acc}, {dst}\n",
}


@dataclass
class InstrClass:
    name: str
    kind: str            # reg | imm12 | shamt6 | shamt5 | upper
    ops: list[str]
    weight: int = 1


@dataclass
class GenomeSpec:
    free_regs: list[str]
    acc_reg: str
    mix_reg: str
    mix_const: int
    fold: str
    min_macros: int
    max_macros: int
    avg_macros: int
    sigma: float
    classes: list[InstrClass] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "GenomeSpec":
        with open(path, "rb") as fh:
            d = tomllib.load(fh)
        classes = []
        for name, c in d.get("classes", {}).items():
            classes.append(
                InstrClass(
                    name=name,
                    kind=c.get("kind", name),
                    ops=list(c["ops"]),
                    weight=int(c.get("weight", 1)),
                )
            )
        spec = cls(
            free_regs=list(d["free_regs"]),
            acc_reg=str(d.get("acc_reg", "s11")),
            mix_reg=str(d.get("mix_reg", "s10")),
            mix_const=int(d.get("mix_const", 0x9E3779B1)),
            fold=str(d.get("fold", "add")),
            min_macros=int(d["length"]["min"]),
            max_macros=int(d["length"]["max"]),
            avg_macros=int(d["length"]["average"]),
            sigma=float(d["length"].get("sigma", 32)),
            classes=classes,
        )
        spec.check()
        return spec

    def check(self) -> None:
        if self.fold not in FOLDS:
            raise ValueError(f"fold must be one of {sorted(FOLDS)}")
        reserved = {self.acc_reg, self.mix_reg, "sp", "gp", "tp", "ra", "zero", "x0"}
        clash = reserved & set(self.free_regs)
        if clash:
            raise ValueError(f"reserved registers cannot be evolved: {sorted(clash)}")
        if not self.classes:
            raise ValueError("the genome defines no instruction classes")
        if not self.min_macros <= self.avg_macros <= self.max_macros:
            raise ValueError("length must satisfy min <= average <= max")

    @property
    def saved_regs(self) -> list[str]:
        """Callee-saved registers the scaffolding uses and must restore."""
        regs = [self.acc_reg]
        if self.fold == "mul":
            regs.append(self.mix_reg)
        return [r for r in regs if r.startswith("s")]

    def fold_text(self, dst_ref: str) -> str:
        return FOLDS[self.fold].format(acc=self.acc_reg, mix=self.mix_reg, dst=dst_ref)

    def constraints(self, ident: str) -> str:
        return render_constraints(self, ident)

    @property
    def summary(self) -> str:
        return f"fold={self.fold}, {self.min_macros}-{self.max_macros} macros"


class XmlGenome:
    """A constraints file written by hand: uGP3 gets it exactly as it is."""

    def __init__(self, path: Path):
        self.text = path.read_text()
        self.macros, self.widest = check_constraints(self.text, path.name)

    def constraints(self, ident: str) -> str:
        return self.text

    @property
    def summary(self) -> str:
        lo, hi = self.widest
        return f"uGP3 constraints, {self.macros} macros, {lo}-{hi} per subSection at most"


def load(path: Path) -> GenomeSpec | XmlGenome:
    if path.suffix == ".xml":
        return XmlGenome(path)
    if path.suffix == ".toml":
        return GenomeSpec.load(path)
    raise ValueError(f"{path.name}: a genome is a uGP3 constraints file (.xml) "
                     "or a description of one (.toml)")


_DECIMAL = re.compile(r"-?\d+")


def check_constraints(text: str, name: str) -> tuple[int, tuple[int, int]]:
    """Stop on what uGP3 would accept and then get wrong (docs/ugp3-errors.md),
    naming the line, which uGP3's own messages never do. Returns the number of
    macros and the widest macro count of a subSection, for the setup summary."""
    p = xml.parsers.expat.ParserCreate()
    problems: list[str] = []
    root: list[str] = []
    expr: list = []         # [line, text after the last child, has content]
    macros = 0
    widest = (0, 0)

    def start(tag: str, attrs: dict) -> None:
        nonlocal macros, widest
        line = p.CurrentLineNumber
        root[:] = root or [tag]
        if expr:
            expr[1], expr[2] = "", True
        if tag == "expression":
            expr[:] = [line, "", False]
        elif tag == "macro":
            macros += 1
        elif tag == "macros":
            occ = [attrs.get(k, "") for k in ("minOccurs", "averageOccurs", "maxOccurs")]
            if all(_DECIMAL.fullmatch(v.strip()) for v in occ):
                lo, avg, hi = (int(v) for v in occ)
                if not lo <= avg <= hi:
                    problems.append(f"line {line}: <macros> needs minOccurs <= averageOccurs "
                                    f"<= maxOccurs, has {lo}, {avg}, {hi}")
                if hi > widest[1]:
                    widest = (lo, hi)
        elif tag == "item" and attrs.get("type") == "integer":
            bounds = {k: attrs.get(k, "").strip() for k in ("minimum", "maximum")}
            for key, v in bounds.items():
                if not _DECIMAL.fullmatch(v) and not (key == "maximum" and v == "infinity"):
                    problems.append(f"line {line}: {key}=\"{v}\" is not a decimal integer: "
                                    "uGP3 keeps the leading digits, so \"0x7ff\" becomes 0")
            if all(_DECIMAL.fullmatch(v) for v in bounds.values()) \
                    and int(bounds["minimum"]) > int(bounds["maximum"]):
                problems.append(f"line {line}: minimum {bounds['minimum']} is above maximum "
                                f"{bounds['maximum']}: uGP3 would hang without a word")

    def end(tag: str) -> None:
        if tag == "expression" and expr:
            if (expr[2] or expr[1].strip()) and not expr[1].endswith("\n"):
                problems.append(f"line {expr[0]}: the expression does not end with a newline: "
                                "uGP3 copies it out literally, so the program comes out on one line")
            expr.clear()

    def chars(data: str) -> None:
        if expr:
            expr[1] += data

    p.StartElementHandler, p.EndElementHandler, p.CharacterDataHandler = start, end, chars
    try:
        p.Parse(text, True)
    except xml.parsers.expat.ExpatError as exc:
        raise ValueError(f"{name}: not well-formed XML: {exc}") from None
    if root != ["constraints"]:
        problems.insert(0, f"the root element is <{root[0] if root else ''}>, not <constraints>")
    if problems:
        raise ValueError(f"{name}:\n  " + "\n  ".join(problems))
    return macros, widest


# --------------------------------------------------------------- scaffolding

# The fixed code around the body, and how the two are linked, lives with the
# other programs this folder builds, so every one of them uses one set of flags.
Scaffold = progs.Scaffold


def prologue_text(spec: GenomeSpec) -> str:
    saves = "".join(
        f"    sd {r}, {8 * i}(sp)\n" for i, r in enumerate(spec.saved_regs)
    )
    frame = 16 * ((len(spec.saved_regs) + 1) // 2)
    setup = f"    li {spec.acc_reg}, 0\n"
    if spec.fold == "mul":
        setup += f"    li {spec.mix_reg}, {spec.mix_const}\n"
    return (
        "    .option nopic\n"
        "    .text\n"
        "    .align 2\n"
        "    .globl sbst_body\n"
        "    .type sbst_body, @function\n"
        "sbst_body:\n"
        f"    addi sp, sp, -{frame}\n"
        f"{saves}"
        f"{setup}"
    )


def epilogue_text(spec: GenomeSpec) -> str:
    restores = "".join(
        f"    ld {r}, {8 * i}(sp)\n" for i, r in enumerate(spec.saved_regs)
    )
    frame = 16 * ((len(spec.saved_regs) + 1) // 2)
    return (
        f"    mv a0, {spec.acc_reg}\n"
        f"{restores}"
        f"    addi sp, sp, {frame}\n"
        "    ret\n"
        "    .size sbst_body, .-sbst_body\n"
    )


# ------------------------------------------------------------- uGP3 rendering


def render_constraints(spec: GenomeSpec, ident: str = "SBST") -> str:
    types = [_const_item("reg", spec.free_regs)]
    for c in spec.classes:
        types.append(_const_item(f"op_{c.name}", c.ops))

    init_expr = "".join(
        f"    li {r}, 0x<param ref=\"k{i}\"/>\n" for i, r in enumerate(spec.free_regs)
    )
    init_params = "".join(
        f'                <item type="bitArray" length="32" base="hex" name="k{i}"/>\n'
        for i in range(len(spec.free_regs))
    )

    macros = "\n".join(_macro(spec, c) for c in spec.classes)

    return f"""<?xml version="1.0" encoding="utf-8"?>
<!-- generated: edit the genome description, not this file -->
<constraints id="{ident}">
  <typeDefinitions>
{"".join(types)}  </typeDefinitions>
  <commentFormat>#<value /></commentFormat>
  <identifierFormat>n<value /></identifierFormat>
  <labelFormat><value />:</labelFormat>
  <uniqueTagFormat><value /></uniqueTagFormat>
  <prologue id="globalPrologue" />
  <epilogue id="globalEpilogue" />
  <sections>
    <section id="sbst" prologueEpilogueCompulsory="false">
      <prologue id="sectionPrologue">
        <expression>{escape(prologue_text(spec))}</expression>
      </prologue>
      <epilogue id="sectionEpilogue">
        <expression>{escape(epilogue_text(spec))}</expression>
      </epilogue>
      <subSections>
        <subSection id="init" maxOccurs="1" minOccurs="1" maxReferences="0">
          <prologue id="initPrologue"/>
          <epilogue id="initEpilogue"/>
          <macros maxOccurs="1" minOccurs="1" averageOccurs="1" sigma="1">
            <macro id="seedRegs">
              <expression>{init_expr}</expression>
              <parameters>
{init_params}              </parameters>
            </macro>
          </macros>
        </subSection>
        <subSection id="body" maxOccurs="1" minOccurs="1" maxReferences="0">
          <prologue id="bodyPrologue"/>
          <epilogue id="bodyEpilogue"/>
          <macros minOccurs="{spec.min_macros}" maxOccurs="{spec.max_macros}" \
averageOccurs="{spec.avg_macros}" sigma="{spec.sigma}">
{macros}
          </macros>
        </subSection>
      </subSections>
    </section>
  </sections>
</constraints>
"""


def _const_item(name: str, values: list[str]) -> str:
    body = "".join(f"      <value>{escape(v)}</value>\n" for v in values)
    return f'    <item type="constant" name="{name}">\n{body}    </item>\n'


def _macro(spec: GenomeSpec, c: InstrClass) -> str:
    op = '<param ref="op"/>'
    dst = '<param ref="dst"/>'
    fold = spec.fold_text(dst)
    params = [
        f'                <item type="definedType" ref="op_{c.name}" name="op"/>',
        '                <item type="definedType" ref="reg" name="dst"/>',
    ]

    if c.kind == "reg":
        expr = f'    {op} {dst}, <param ref="s1"/>, <param ref="s2"/>\n{fold}'
        params += [
            '                <item type="definedType" ref="reg" name="s1"/>',
            '                <item type="definedType" ref="reg" name="s2"/>',
        ]
    elif c.kind == "imm12":
        expr = f'    {op} {dst}, <param ref="s1"/>, <param ref="imm"/>\n{fold}'
        params += [
            '                <item type="definedType" ref="reg" name="s1"/>',
            '                <item type="integer" minimum="-2048" maximum="2047" name="imm"/>',
        ]
    elif c.kind in ("shamt6", "shamt5"):
        bits = 6 if c.kind == "shamt6" else 5
        expr = f'    {op} {dst}, <param ref="s1"/>, 0b<param ref="sh"/>\n{fold}'
        params += [
            '                <item type="definedType" ref="reg" name="s1"/>',
            f'                <item type="bitArray" length="{bits}" base="bin" name="sh"/>',
        ]
    elif c.kind == "upper":
        expr = f'    {op} {dst}, 0x<param ref="imm20"/>\n{fold}'
        params += [
            '                <item type="bitArray" length="20" base="hex" name="imm20"/>'
        ]
    else:
        raise ValueError(f"unknown instruction class kind: {c.kind}")

    joined = "\n".join(params)
    return f"""            <macro id="{c.name}" weight="{c.weight}">
              <expression>{expr}</expression>
              <parameters>
{joined}
              </parameters>
            </macro>"""


# The GA values evosbst knows by name, and where a population settings file
# keeps each: (element, attribute).
POPULATION_FIELDS = {
    "mu": ("mu", "value"),
    "nu": ("nu", "value"),
    "lam": ("lambda", "value"),
    "generations": ("maximumGenerations", "value"),
    "max_evaluations": ("maximumEvaluations", "value"),
    "max_hours": ("maximumTime", "hours"),
    "sigma": ("sigma", "value"),
    "inertia": ("inertia", "value"),
    "max_age": ("maximumAge", "value"),
    "elite": ("eliteSize", "value"),
    "clone_scaling": ("cloneScalingFactor", "value"),
    "tau": ("selection", "tau"),
}


def _read_population(path: Path) -> tuple[ET.Element, str]:
    try:
        root = ET.parse(path, parser=ET.XMLParser(
            target=ET.TreeBuilder(insert_comments=True))).getroot()
    except ET.ParseError as exc:
        raise ValueError(f"{path.name}: not well-formed XML: {exc}") from None
    ns = root.tag[: root.tag.index("}") + 1] if root.tag.startswith("{") else ""
    if root.tag != ns + "parameters":
        raise ValueError(f"{path.name}: the root element is <{root.tag[len(ns):]}>, "
                         "not <parameters>: not a population settings file")
    return root, ns


def population_values(path: Path) -> dict[str, int | float]:
    """The GA values a population settings file sets, by their [ga] names."""
    root, ns = _read_population(path)
    out: dict[str, int | float] = {}
    for name, (tag, attr) in POPULATION_FIELDS.items():
        el = root.find(ns + tag)
        text = el.get(attr) if el is not None else None
        if text is None:
            continue
        try:
            out[name] = float(text) if any(c in text for c in ".eE") else int(text)
        except ValueError:
            raise ValueError(f"{path.name}: <{tag} {attr}=\"{text}\"> is not a number") from None
    return out


def render_population(camp, constraints: Path, evaluator: str, source: Path | None = None) -> str:
    """The population settings of a run: the file the campaign names (or the
    frozen copy of a resumed run), else the [ga] section rendered."""
    source = source or camp.ga_file
    if source is None:
        return _population_from_ga(camp, constraints, evaluator)
    root, ns = _read_population(source)
    if ns:
        ET.register_namespace("", ns[1:-1])

    def child(parent: ET.Element, tag: str) -> ET.Element:
        el = parent.find(ns + tag)
        return el if el is not None else ET.SubElement(parent, ns + tag)

    # Every value evosbst knows is written back, so the run's copy says
    # exactly what ran; what the file says beyond them is left as it is.
    for name, (tag, attr) in POPULATION_FIELDS.items():
        child(root, tag).set(attr, str(getattr(camp.ga, name)))
    # What the protocol with evosbst fixes: these are not settings.
    child(root, "fitnessParameters").set("value", str(DIM))
    child(root, "constraints").set("value", str(constraints))
    evaluation = child(root, "evaluation")
    for tag, value in (("concurrentEvaluations", camp.ga.lam), ("removeTempFiles", "false"),
                       ("evaluatorPathName", evaluator),
                       ("evaluatorInputPathName", "individual%s"),
                       ("evaluatorOutputPathName", "fitness.output")):
        child(evaluation, tag).set("value", str(value))
    return '<?xml version="1.0" encoding="utf-8" ?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def _population_from_ga(camp, constraints: Path, evaluator: str) -> str:
    ga = camp.ga
    return f"""<?xml version="1.0" encoding="utf-8" ?>
<parameters type="enhanced">
  <cloneScalingFactor value="{ga.clone_scaling}"/>
  <eliteSize value="{ga.elite}"/>
  <maximumTime hours="{ga.max_hours}" />
  <mu value="{ga.mu}"/>
  <nu value="{ga.nu}"/>
  <lambda value="{ga.lam}"/>
  <inertia value="{ga.inertia}"/>
  <fitnessParameters value="{DIM}"/>
  <maximumAge value="{ga.max_age}"/>
  <sigma value="{ga.sigma}"/>
  <invalidateFitnessAfterGeneration value="0"/>
  <constraints value="{constraints}"/>
  <maximumGenerations value="{ga.generations}"/>
  <maximumEvaluations value="{ga.max_evaluations}"/>
  <selection type="tournamentWithFitnessHole" tau="{ga.tau}" tauMin="1" tauMax="1" fitnessHole="0" />
  <evaluation>
    <!-- the whole generation in one call: the queue is what keeps the machine full -->
    <concurrentEvaluations value="{ga.lam}" />
    <removeTempFiles value="false" />
    <evaluatorPathName value="{evaluator}"/>
    <evaluatorInputPathName value="individual%s"/>
    <evaluatorOutputPathName value="fitness.output"/>
  </evaluation>
  <operators default="none">
    <!-- no scanMutation*: one application breeds a child per value of a parameter
         (28 at once was seen), near-identical programs that each cost a campaign -->
    <operator ref="onePointImpreciseCrossover"/>
    <operator ref="twoPointImpreciseCrossover"/>
    <operator ref="uniformCrossover"/>
    <operator ref="singleParameterAlterationMutation"/>
    <operator ref="alterationMutation"/>
    <operator ref="insertionMutation"/>
    <operator ref="removalMutation"/>
    <operator ref="replacementMutation"/>
    <operator ref="swapMutation"/>
    <operator ref="randomizer"/>
  </operators>
</parameters>
"""


def render_settings(population: Path, seed: int, recovery_input: str = "") -> str:
    return f"""<?xml version="1.0" encoding="utf-8" ?>
<settings>
  <context name="evolution">
    <option name="populations">
      <population name="SBST" value="{population}" />
    </option>
    <option name="statisticsPathName" value="statistics.csv" />
    <option name="randomSeed" value="{seed}"/>
  </context>
  <context name="recovery">
    <option name="recoveryInput" value="{recovery_input}" />
    <option name="recoveryInputPopulations" value="" />
    <option name="recoveryOutput" value="status.xml" />
    <option name="recoveryOverwriteOutput" value="true" />
    <!-- false: a resumed run must not pay again for what it already knows -->
    <option name="recoveryDiscardFitness" value="false" />
  </context>
  <context name="logging">
    <option name="std::cout" value="info; brief" />
    <option name="verbose.log" value="verbose; brief" />
  </context>
</settings>
"""
