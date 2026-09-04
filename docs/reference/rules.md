# Rules and defaults

## Rule names

```text
<scope>.<metric>       routine.CyclomaticStrict, project.AVG:CountLineCode
structure.<rule>       structure.file_cycle, structure.fan_out
codecheck.<check_id>   a CodeCheck check id
analysis.<rule>        analysis.parse_error -- the analysis itself failed
```

Scopes are `routine`, `class`, `file`, `project` and `arch`. The last two have no entities of
their own: a threshold on them is checked against the population of its scope and yields one
project-level finding with no entity and no path.

## Threshold defaults

Every default has a limit. Not every default has a ratchet.

### Routine

| Metric | Limit | Severity | Ratchet | What it counts |
| --- | ---: | --- | --- | --- |
| `CyclomaticStrict` | 10 | error | yes | Decision points, counting `&&` and `\|\|` |
| `CyclomaticModified` | 8 | error | yes | Decision points, a `switch` counting once |
| `Essential` | 4 | **warning** | yes | Unstructured control flow. [Why it only warns](#two-limits-that-were-demoted-to-warnings) |
| `MaxNesting` | 3 | error | yes | Deepest nesting level |
| `CountLineCode` | 60 | error | yes | Lines containing code |
| `CountStmt` | 40 | error | yes | Statements |
| `CountParams` | 5 | error | yes | Declared parameters. **Synthetic** — Understand's own is unset for every language |
| `CountPath` | 100 | error | yes | Acyclic execution paths |

### Class

| Metric | Limit | Severity | Ratchet |
| --- | ---: | --- | --- |
| `CountDeclMethod` | 20 | error | **no** |
| `CountDeclMethodNonStub` | 15 | error | **no** |
| `CountDeclInstanceVariable` | 10 | error | **no** |
| `MaxInheritanceTree` | 4 | **warning** | yes |
| `CountClassDerived` | 8 | error | **no** |
| `CountClassCoupled` | 12 | error | **no** |
| `PercentLackOfCohesion` | 70 | **warning** | yes |

`CountDeclMethodNonStub` is synthetic: `CountDeclMethod - 2 * CountDeclPropertyAuto`.
`CountDeclPropertyAuto` exists for C# alone, so on every other language the two numbers are
equal. `PercentLackOfCohesion` is unavailable for Python and is dropped, with a report, on a
Python-only project.

### File

| Metric | Limit | Severity | Ratchet |
| --- | ---: | --- | --- |
| `CountLineCode` | 500 | error | **no** |
| `CountDeclFunction` | 25 | error | **no** |
| `CountDeclClass` | 3 | error | **no** |
| `MaxCyclomaticStrict` | 10 | error | yes |
| `RatioCommentToCode` | min 0.1 | **warning** | yes |

### Project

Reduced over the population of the scope.

| Metric | Limit | Severity |
| --- | ---: | --- |
| `AVG:CyclomaticStrict` | 3 | error |
| `AVG:CountLineCode` | 30 | error |
| `MaxCyclomaticStrict` | 15 | error |
| `MaxNesting` | 5 | error |

Available stats prefixes: `AVG`, `MEDIAN`, `MEDIANHIGH`, `MEDIANLOW`, `MEDIANGROUPED`,
`MODE`, `STDEV`, `VARIANCE`. `STDEV` and `VARIANCE` are the population forms.

### The eight rules with the ratchet off

```text
file.CountDeclFunction     class.CountDeclMethod
file.CountDeclClass        class.CountDeclMethodNonStub
file.CountLineCode         class.CountDeclInstanceVariable
                           class.CountClassCoupled
                           class.CountClassDerived
```

Each of these counts the declarations, collaborators or lines *of the container*, and each
goes up when you split the container's contents — which is the remedy the gate's own hints
name. Ratcheting them would make the gate refuse the refactoring it just asked for. The
absolute limits are untouched: a file with 40 functions still fails `file.CountDeclFunction`
at 25.

Full reasoning and the measurements are on
[The ratchet](../argument/ratchet.md#the-ratchet-does-not-refuse-the-refactoring-it-just-asked-for).

`class.MaxInheritanceTree` is deliberately **not** in that list, even though extracting a
superclass raises it. No hint in the catalogue asks for another inheritance layer;
`MaxInheritanceTree`'s own hint asks for one fewer.

## Structural rules

| Rule | Default severity | What it reports |
| --- | --- | --- |
| `structure.file_cycle` | error | A strongly connected component of two or more files in the after-side dependency graph, that is not contained in any before-side component |
| `structure.arch_cycle` | error | The same, between architecture nodes |
| `structure.layer` | error | A **new** edge that a declared layer rule does not allow |
| `structure.new_dependencies` | error | A file that gained more than `max_new_dependencies_per_file` distinct new targets (default 5), not counting targets that hold no code |
| `structure.coupling` | error | More references between two architecture nodes than a declared rule allows |
| `structure.fan_in` | warning | A file or class depended on by more than the limit |
| `structure.fan_out` | warning | A file or class depending on more than the limit, **and** any affected entity whose fan-out grew |
| `structure.duplicate_definition` | warning | A module-level name bound to the **same value** in more files than `duplicate_definitions` (off by default) |
| `codecheck.<id>` | warning | A finding from an Understand CodeCheck configuration, if one is named |

Fan defaults: `file_fan_in` 50, `file_fan_out` 20, `class_fan_in` 30, `class_fan_out` 12.

Fan-out is ratcheted; **fan-in is not**, because being used more is not a regression. An
entity that grew *and* broke its limit yields both findings. A direction with no configured
limit is switched off entirely, ratchet included.

### A file that only just became readable

When the **before** side of a file could not be parsed and the after side can, every violation
in it is reported as `pre-existing` and does **not** block, with the sentence *measured here
for the first time*.

The code was there; only the measurement is new. Blocking would mean that converting a file so
Understand can finally read it costs one blocking finding per routine it revealed — for code
the commit did not write. Nobody pays that twice, so the file stays unmeasured forever, which
is the outcome the rule exists to prevent.

The exemption is narrow. A file appears in the before side's parse errors only if it was there
and was tried, so a file the change *added* is never in the set, and a violation introduced
into a file that already parsed is untouched. What it does forgive is a new violation written
into a file that also stopped parsing before — and the alternative was measured to make the
fix impossible.

### When two limits pull against each other, the file-level one yields

The routine limits ask for extraction — every hint for `MaxNesting`, `CyclomaticStrict` and
`CyclomaticModified` says to move a block into its own named routine. Doing that raises
`file.CountDeclFunction` and `file.CountLineCode` by construction.

Half of that tension is already handled: those counts, and six others, ship with the
**ratchet off**, so a single extraction can never be refused for making its container bigger.
The other half is not. The ceiling still refuses the twentieth extraction, and **a file of
twelve small named helpers is the outcome the routine limits are asking for**.

So when the two disagree, raise the file-level limit rather than undoing the split. Measured
on this repository: every routine and class ceiling contains at least 99% of its population,
while **69 of 210 files** were outside `CountDeclFunction = 25` — the two rules disagreeing,
in numbers.

If your project is mid-cleanup, do not derive limits from it at all yet; see
[the timing precondition](#a-note-on-timing) below.

### A note on timing

`recommend` measures the shape a project *has*. Run it while a routine-level cleanup is in
flight and it bakes in the shape somebody is working to change — most of all
`file.CountDeclFunction`, which rises with every long routine that becomes several named
helpers. Wait until the splits stop.

### Scattered definitions: one value, many files

`structure.duplicate_definition` reports a module-level name bound to the **same value** in
more files than the limit. It is **off by default** — set `duplicate_definitions` to turn it
on, because collecting the bindings costs one extra pass over the database.

```toml
[structure]
duplicate_definitions = 3
duplicate_definitions_severity = "warning"
duplicate_definitions_ignore = ["log", "logger", "pytestmark"]
```

#### What it is for

No limit in the tables above catches this. Every file involved is small, simple and reads
perfectly well on its own; the cost lands on whoever has to *change* the value. Measured on a
770-file project:

| Binding | Files |
| --- | ---: |
| `_HORIZON_DAYS = 20` | 15 — and a sixteenth file binds the same name to `5` |
| `PROJECT = Path(__file__).resolve().parents[2]` | 14 — and six more use `parents[1]` |
| `FloatArray = NDArray[np.float64]` | 12 |
| `_ZERO = Decimal("0")` | 9 |
| `_ONE = Decimal("1")` | 7 — and one file binds it to the float `1.0` |

The first row is the shape worth understanding. Changing the re-select horizon means finding
fifteen files, one of which deliberately disagrees, and **no amount of reading any one of them
reveals either fact**. That is the working-set problem in its purest form: the definition is
distributed, so the change is too.

#### Why it keys on the value, not the name

A name repeated with a *different* value in each file is usually deliberate local vocabulary —
`HELP` in every subcommand module of this project, `__all__` in every package. Reporting those
would bury the real finding under the idiom. A name repeated with the *same* value is a
decision that was copied instead of shared.

The count is over the whole project; the finding is reported against the **affected** files.
So the commit that adds the sixteenth copy is told about the other fifteen, and a commit that
touches none of them is told nothing.

#### Before you collect anything: are these one decision?

The rule sees that N files bind a name to the same text. It cannot see whether those copies
are **supposed to move together**, and that is the question to answer first. Reported from a
real cleanup pass on a 770-file project, where three groups were correctly *not* collapsed:

| Binding | Why it stays | |
| --- | --- | --- |
| `_FACTOR_VERSION = 1` in 7 modules | Each factor's own version. Collapsing them makes a bug the moment one is bumped. |
| `T = TypeVar("T")` in 6 modules | A per-module type variable. Sharing one would be wrong. |
| `AS_OF = ...` in test modules | Test scenarios that happen to pick the same date. |

What separates these from a real finding is not the value, it is intent: a name bound to a
per-module **identity** reads differently from a name bound to a **threshold**, and the rule
cannot tell them apart. So the first move on a finding is not "collapse it" but "decide
whether these are one decision". When they are not, the name goes in
`duplicate_definitions_ignore` — that is what the list is for, alongside the `log` /
`pytestmark` idioms.

The same pass collapsed six groups that *were* one decision — a type alias in 13 files, a
re-select horizon in 15, a numeric floor in 5, a project root under three names in 29 test
modules — so the rule's yield is real; it is the triage that needs a human or an agent.

#### The most valuable finding is the one you leave open

Reported from the same pass: `MIN_ACTIVE` named **two different thresholds** in one project —
3 in four modules, 5 in five others. The rule correctly reports two same-value groups rather
than one, which is exactly the `_HORIZON_DAYS` hazard above seen from the other side: one
name, two meanings, and `grep` answers with whichever it finds first. Unifying them is a
quantitative decision, not a refactor, so leaving it visible is the right call.

#### What it cannot see

- **A binding whose initialiser the lexer cannot recover** — an augmented assignment, a tuple
  unpacking, a bare annotation, a value that does not close within twelve lines — is skipped,
  never grouped. Two unreadable initialisers are not evidence that two definitions agree.
- **Semantic equality.** `Decimal("0")` and `Decimal(0)` are different text and so different
  definitions. The rule under-reports; it does not guess.
- **A value that references another module-level name.** The comparison is on text, so
  `FILES = tuple(sorted(SOURCES))` reads as one definition in every module that writes it —
  even where each `SOURCES` is a different dictionary. This is the rule's one *over*-reporting
  case, found by running it on this project, and it is what `duplicate_definitions_ignore` is
  for. Check the referenced names before you collect anything.
- **The per-module idiom.** `log = logging.getLogger(__name__)` and `pytestmark` are written
  out in every module on purpose. Put them in `duplicate_definitions_ignore`. That list is
  names rather than values deliberately: the similar-looking
  `PROJECT_ROOT = Path(__file__).resolve().parents[2]` — which the same project also writes
  with `parents[1]` in six other files — is a real finding, not an idiom.

### A target with no code in it is not a dependency

`structure.new_dependencies` skips any target whose `CountLineCode` is 0 — a package
initialiser an import merely *traverses*, rather than one it uses.

Measured on this repository: a new test module importing four things scored **six**
dependencies, two of which were `src/scitools_hook/__init__.py` and
`src/scitools_hook/cli/__init__.py`. The second is one line of docstring; Understand reports
`CountLineCode` 0, `CountStmt` 0 and no declaration of any kind for it. Counting it left a new
file in a nested package a real budget of two or three imports against a limit of five, which
made the rule refuse the ordinary act of adding a module with a test.

The test is *no code*, not *named `__init__.py`*: it is language-agnostic, and an initialiser
that re-exports an API has code and goes on counting. A file the analyser could not read is
never treated as empty — its metrics are absent rather than zero, and dropping its edges would
be a coupling the gate quietly stopped measuring.

A cycle that grew a member is reported, because `{a, b, c}` is no subset of `{a, b}` — the
change made it worse. Self-loops are excluded. Each cycle finding names its members and the
closing edges with their reference counts:

```text
error  structure.file_cycle
  2 files form a dependency cycle that did not exist before the change: pricing/catalog.py,
  pricing/rates.py; closed by pricing/catalog.py -> pricing/rates.py (3 refs),
  pricing/rates.py -> pricing/catalog.py (3 refs)
```

In whole-project mode there is no before side, so every cycle is reported as an inventory
and none of them is called new.

## `analysis.parse_error`

A category of its own rather than a metric under `file.`, because it is not a measurement:
there is no number, no limit and nothing for a baseline to hold.

Blocking, by default, for any file **in the selection** that Understand could not read. Files
outside the selection — the interpreter's own standard library, say — are reported and do not
block.

**Its severity is not configurable.** There is no key that turns it into a warning globally,
and that is deliberate: a lever that silences it everywhere is a lever that turns the gate
into one that certifies files it never read. The only escape is per file and requires a
written reason, which the report then quotes on every run. See
[Configuration](../guide/configuration.md#acknowledging-a-file-that-does-not-parse).

## Two limits that were demoted to warnings

Both were shipped as errors, both were measured, and both were demoted because **the number
is not comparable across the entities it ranks**, so neither can carry a refusal. Both keep
their limits and both keep their ratchets. Only the ability to block was removed.

This is documented in detail because it is the most useful thing in this reference: it is a
worked example of what to do when a metric turns out not to mean what you assumed.

### `routine.Essential` ranks style, and contradicts its own hint

Measured on Understand 6.5.1204. One file, one database:

- The same six-way branch written as **six guard clauses** scores `Essential` **7**.
- Written as one `elif` ladder with a single exit, it scores **1**.
- Both score `CyclomaticStrict` **7**.

One guard clause scores 1 and three score 4, so the shipped maximum of 4 fires on the
*fourth* guard clause. Understand counts an early return as unstructured control flow.

And the hint catalogue answers a `routine.Essential` finding with *"extract the block that is
jumped out of into a routine that **returns early**"* — which raises the metric. A default
that blocks the refactoring the same tool recommends is not a default.

On this repository, 25 of 998 routines are over the limit and 824 of them score 1.

The blocking half of the concern is untouched and still ships as an error: `CyclomaticStrict`
10, `CyclomaticModified` 8, `MaxNesting` 3 and `CountPath` 100.

### `class.MaxInheritanceTree` measures where a base class lives

Also measured on 6.5.1204. One fixture, one `und`, varying only which interpreter Understand
analysed with:

- `class Model(BaseModel)` scores **5** when pydantic is on that interpreter's `sys.path`.
- The same unchanged line scores **1** when it is not.

Since the interpreter is now pinned — and it must be, because a metric must not depend on
which libraries happen to sit beside the gate — third-party depth is invisible. What is still
visible is the pure-Python standard library, and it is expensive:

| Base | `MaxInheritanceTree` |
| --- | ---: |
| `class X(Protocol)` | 5 |
| a subclass of that | 6 |
| `enum.Enum`, `abc.ABC` | 4 |
| an `Exception` subclass | 3 |
| its child | 4 |
| `io.StringIO` (a C module) | 0 |

So one level of project inheritance costs anything from 0 to 5 depending on where its base
lives, and the metric is **loudest where there is no hierarchy and silent where there is
one**. All five findings the shipped limit raises on this repository's `src/` are `Protocol`
declarations or one exception subclass. None is a hierarchy this project built.

Raising the number instead was measured and rejected. A limit of 6 clears the standard
library's floor today, stably across CPython 3.11 through 3.14, but it calibrates a shipped
constant against the standard library rather than against the code, and it leaves the
inversion in place: a framework hierarchy four deep still reports 1.

The number stays honest about what Understand saw. The severity stops it deciding a commit.

## What blocks

- Only `error` can block. A `warning` never blocks, in any mode.
- A pre-existing threshold error blocks only under `ratchet.strict = true`.
- A **ratchet** finding on an entity still inside its own limit after the change is demoted
  to a warning by `ratchet.below_limit_severity`, which ships as `"warning"`. Growth that
  crosses a limit, or growth on an entity already over one, blocks.
- A ratchet finding can never be `pre-existing` — it exists precisely because the value just
  got worse.
- Nothing is filtered out of the report. Warnings and pre-existing findings are printed and
  counted; they are simply not counted as blocking.

`below_limit_severity` is a ceiling and never a promotion: a rule you demoted to `warning`
stays a warning even when it is set to `error`.

### Configuring a severity

There is no central severity map, and a top-level `[severity]` table is rejected at load.
Each rule family carries its own key; the table is in
[Configuration](../guide/configuration.md#severities). A threshold table accepts exactly
`max`, `min`, `ratchet` and `severity`, and `Metric = false` works only inside a
`[scope.*]` override.

## Boundary conditions

- Threshold breaches are strict: `value > max` and `value < min`. A value exactly at the
  limit passes.
- Ratchet comparisons are strict too. A value that did not move produces no finding.
- Coupling and fan rules are `>` rules: a file exactly at its limit is allowed.
