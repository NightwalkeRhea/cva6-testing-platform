# What uGP3 says about a broken constraints file — and what it doesn't

uGP3 ships no XML schema: the `schemaLocation` of its samples is never fetched,
and the only schema is the C++ reader in `Libs/Constraints/*.xml.cc`. What
follows was read from that reader and reproduced on uGP3 v3.4.0 "Camellia", the
version the flow uses. Three properties of its messages:

1. **No XML line number**, except for well-formedness. The `[method@file:line]` in
   brackets is uGP3's *own source*, not your file.
2. **Paths are schema paths, not locations.** `mandatory element
   ".../subSection/macros" not found` does not say *which* subSection.
3. **Three failures produce no message at all** (second section). evosbst
   checks a constraints file for them, by line, before uGP3 sees it.

## Diagnosed: it stops and tells you

| what you wrote | what uGP3 prints |
|---|---|
| a `subSection` with no `<macros>` | `xml schema error: mandatory element "/constraints/sections/section/subSections/subSection/macros" not found.` |
| `<macros minOccurs="6" maxOccurs="4">` | `The attribute minMacroOccurs cannot be greater than maxMacroOccurs` |
| `averageOccurs` outside `[minOccurs, maxOccurs]` | `Invalid value for averageMacroInstances attribute.` |
| `<param ref="immediate"/>` with no such parameter | `xml schema error: the foreign key ./expression/param/[@ref="immediate"] does not match the ./../parameters/item/@name primary key.` |
| `<item type="definedType" ref="source"/>` with no such type | `xml schema error: the foreign key ./parameters/item/@ref (@ref = "source") does not match the primary key /constraints/typeDefinitions/item/@name` |
| two parameters of one macro sharing a `name` | `xml schema error: A parameter with the specified id "rs" already exists.` |
| `<commentFormat>` missing | `Coluld not find comment format` (sic) |
| any unexpected element inside a `subSection` | `xml schema error: unexpected element "oops"` |
| an attribute misspelt — `minimun="0"` | `xml schema error: Attribute "minimum" not found in element "item".` |
| an unclosed tag | `Error in file "constraints.xml": Error parsing document at line 33, column 9` |

The last row is the only one that names a line in *your* file, because it comes
from tinyxml before the reader sees anything.

Every one of these is preceded by
`Unable to load constraints '<file>' specified in the population parameters file`
and exits non-zero, so a wrapper can detect them without parsing the message.

## Silent: it runs, and the run is worthless

These are the expensive ones. uGP3 starts, evolves, reports fitness, and finishes.

**A hexadecimal range collapses to zero.** `minimum` and `maximum` are read with
`istringstream >> long`, which stops at the first character it cannot use and
reports failure only when it consumed *nothing*. So `maximum="0x11111"` extracts
`0`, succeeds, and the parameter becomes the range `[0, 0]`:

```xml
<item type="integer" name="imm" minimum="0" maximum="0x11111"/>
```
```asm
addi s0,t1,0        addi s0,t1,0        addi s0,t0,0        ...
```

against the same genome written in decimal:

```xml
<item type="integer" name="imm" minimum="0" maximum="69905"/>
```
```asm
addi s0,t1,41723    addi s0,t0,6988     addi s0,t3,23328    ...
```

The register field varies in both, so the genome looks alive. Only the immediate
is dead, and nothing says so. The same truncation silently accepts `1e6` (→ 1),
`1_000` (→ 1), `1,000` (→ 1) and a trailing space (harmless). Only a value with no
leading digits at all — `abc`, empty — raises `conversion failed`.

**`minimum` greater than `maximum` hangs forever.** The only guard is an `assert`,
which a release build compiles out: the process spins with no output and no
exit.

**A macro expression without a trailing newline emits the whole program on one
line.** The expression is copied out literally, so the newline has to be inside
the element:

```xml
<expression>  addi s0,<param ref="rs"/>,<param ref="imm"/>
</expression>
```

Assembly on one line is a syntax error at build time rather than a wrong number,
so this one is caught downstream — but by the assembler, which blames your
scaffolding.

Two accepted spellings worth knowing: `maximum="infinity"` is a magic value
meaning the type's maximum, and it produces immediates around 10^18; a negative
`minimum` works as written.

## Reproducing this

uGP3 is GPL-3 and needs only a C++11 compiler (tinyxml is vendored):

```bash
git clone --depth 1 https://github.com/microgp/microgp3
cmake -S microgp3/src -B build -DCMAKE_BUILD_TYPE=RELEASE -DCMAKE_POLICY_VERSION_MINIMUM=3.5
make -C build -j8            # -> build/Frontends/ugp3/ugp3
```

`CMAKE_BUILD_TYPE` must be upper case; the policy flag is for CMake 4.x.
`tests/ugp3/` holds a minimal genome, its settings and a constant fitness stub
(`ugp3` reads `ugp3.settings.xml` from the working directory: there is no
`--settings` option); `tests/ugp3/catalogue.py` breaks the genome one way at a
time and prints what uGP3 answers. No licence, no server.
