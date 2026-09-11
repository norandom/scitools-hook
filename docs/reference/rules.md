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
| `CountLineComment` | 20 | **warning** | yes | Comment lines; on Python a docstring counts. [A shrink signal](#the-shrink-metrics-length-without-complexity) |
| `LinesPerStatement` | 4 | **warning** | yes | `CountLineCode / CountStmt`, judged from `verbosity_min_statements = 5` statements. **Synthetic**. [A shrink signal](#the-shrink-metrics-length-without-complexity) |

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
| `structure.call_cycle` | off by default | A cycle in the call graph among the routines the change reaches; set `call_cycles` to turn it on |
| `structure.reachable_complexity` | warning, off by default | A routine whose transitively reached `CyclomaticStrict` exceeds `reachable_complexity` |
| `structure.unused_routine` | warning, off by default | An affected routine nothing in the project calls or uses; set `unused_routines` to turn it on |
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

## The lean-code rules

Every limit above asks whether a piece of code is too complex. The nine rules of the
lean-code family ask whether it should exist at all. All nine carry the `structure.`
category, all nine **ship off**, and each is enabled by naming a severity on its switch in
`[lean]`. The keys and the excerpt `init` writes are in
[Configuration](../guide/configuration.md#the-lean-code-family-lean); how to read a finding,
the three tags and the two the Gate never emits are on [Lean code](../guide/lean-code.md).
This section is one entry per rule: what it reports, what it deliberately does not, its
shipped default and the measurement behind it.

The switch and the rule are not spelled the same. The switch is named after the table it
configures (`unused_parameters`); the finding after what it reports, in the singular
(`structure.unused_parameter`).

Every measurement below names the task in `.kiro/specs/lean-code-rules/research.md` that took
it. Two repositories were measured on Understand 8.0 Build 1262 on 2026-09-11: **this one**,
319 analysed files, and **facdrone**, 945. Where an entry gives two numbers, they are in that
order. "First ten" means the first ten findings of the rule's output, read in the source and
sorted into genuine, same shape (twins an author keeps apart on purpose) and noise (nothing
an author would change).

### Two floors in front of four rules

`structure.unused_parameter`, `structure.unused_class`, `structure.unused_variable` and
`structure.pass_through` make absence claims: nothing reads this, nothing else calls that.
Each sits behind two floors that ship **set** while every rule ships off:

- `resolution_floor = 0.75`: the share of a language's call sites that resolved to a project
  routine. Below it the rule prints, once per run and per language,
  `<rule> was not evaluated for <language>: 32% of this run's Python call sites resolved to a
  project routine, below the call-resolution floor of 75%, so a finding would report the
  analysis, not the code`, and judges nothing in that language.
- `accuracy_floor = 0.75`: the share of files `und analyze -accuracy` parsed with neither an
  error nor a warning, per side. Below it the rule prints, once per run,
  `<rule> was not evaluated: Understand parsed 19% of this analysis without an error, below
  the accuracy floor of 75%; a name whose use sites sit in a region the analysis errored on
  reads as unused while it is read`, and judges nothing.

A run that measured neither figure is refused the same way: an absent figure is not a good
one. Two floors because two failures were measured, and neither number bounds the other's
(task 3.3's review, task 6.4): on facdrone a naive "no reference, therefore dead" predicate
answered 830 routines at 26% accuracy, wrong nearly every time, because that codebase
satisfies its interfaces structurally and exactly one of the 830 carried an override
reference; on this repository at 19% accuracy every one of the sixteen `src/` module bindings
the snapshot answered unreferenced was in fact read, its use sites sitting in regions the
analysis had errored on.

**Both measured repositories are below both floors**: accuracy 19.1% and 25.9%, call
resolution 45.9% and 32.0% (6.4). On both, at the shipped floors, the four rules produce **no
finding by design**, and the line they print instead is the whole of what the run says about
dead code. It does not say there is none.

What the rules say below the floors was measured too, by forcing both to zero, so that an
operator lowering a floor knows what they will get (6.3 first tens, 6.4 whole lists):

| Rule | Findings | What they are |
| --- | --- | --- |
| `structure.unused_parameter` | 68 and 58 | 56 and 39 are pytest fixture parameters of `test_` routines, 4 and 16 are `@overload` stubs; first tens 0 and 2 genuine |
| `structure.pass_through` | 47 and 59 | first tens 1 and 2 genuine forwarders, the rest one-line comprehensions and expressions holding a single project call |
| `structure.unused_variable` | 17 and 55 | first tens 5 and 3 certain; facdrone's other seven need the file open, three of them named in other files the analysis did not resolve |
| `structure.unused_class` | 0 and 18 | 8 of facdrone's first ten named in no other tracked source file |

Two of the four are mostly noise at any floor, and the noise is a routine *shape* no list of
names can spell: a `test_` routine declaring a fixture it uses for its effect, an overload
stub whose body is `...`, a comprehension that holds one call and forwards nothing. Those
exclusions belong to the rules and are recorded for their owners; until they land, enable
`unused_parameters` and `pass_through` last and read their first ten before trusting the
count. No repository above either floor has been measured, so nothing says at what figure the
variable rule's "named in other files" third vanishes. The floors move when one is measured,
not to license a run.

`[lean] accuracy_floor` is not `[analysis] accuracy_floor`. Both read the same figure; the
`[analysis]` one **reports** a poorly resolved run as a warning that never blocks and silences
nothing, the `[lean]` one **refuses** to judge. Lowering the reporting floor unlocks nothing
here.

### `structure.unused_parameter`

**Reports.** A parameter of an affected routine that the routine never reads, sets or
modifies from a project file (Understand's `Useby`, `Setby` and `Modifyby` references), the
routine carrying no override reference. A defaulted parameter's own default is not a use:
Understand records no `Set Init` against it, so `def advance(verbose=False)` with `verbose`
unread is reported (6.1).

**Does not report.** A parameter of a routine that overrides another, because the signature
is the base's (req 1.4). A parameter of a method whose short name two or more project classes
declare, the interface-method tally of requirement 1.9: under structural typing an
implementation holds no reference to the interface it satisfies, so the tally stands in for
the inheritance edge. It is applied by short name, so a free function sharing a name with two
classes' methods is excused too; that false negative was measured at 3 and 2 routines against
the 33 and 157 interface methods the tally rightly excuses (6.4). Names on
`unused_parameters_ignore`, shipped as the receiver (`self`, `cls`, `this`), a leading
underscore and `args`/`kwargs`. Anything the change deleted: the after side is read, where a
deleted entity is absent (req 1.7). Anything at all below either floor, or when any affected
record was extracted before the rule was switched on (a stale warm cache), which is reported
as unavailable rather than judged in part.

**Default.** `unused_parameters` off; the ignore list as shipped.

**Measurement.** The whole lists at floors zero (6.4): 68 and 58 findings, of which 56 and 39
are pytest fixture parameters of `test_` routines and 4 and 16 are `@overload` stubs; the 8
and 3 that remain include a dispatch-table signature and two parameters already carrying
`# noqa: ARG001`, the author and the rule agreeing. First tens 0 and 2 genuine (6.3). The
list is over parameter names and none of those shapes has one, so it was kept as shipped and
the routine-shape exclusion is recorded for the rule's owner.

### `structure.unused_class`

**Reports.** An affected class that nothing in the project references: no use, set, modify,
type, call or inheritance reference from a project file. Inheritance counts as use, so a base
class whose only inbound reference is its one subclass's is used, not dead.

**Does not report.** Any class with a subclass, including an abstract base whose whole subtree
is dead; that is the cheaper error than reporting one class as both dead and worth folding
into its single implementation (task 1.6). `Test*`, `*Error` and `*Exception`
(`unused_classes_ignore`): pytest collects test classes by name and an exception is raised and
caught, neither of which is a reference the rule can see. Anything below either floor, or
deleted by the change.

**Default.** `unused_classes` off; the ignore list as shipped.

**Measurement.** Floors at zero: 0 of this repository's 338 classes, because every class in
`src/` is named from a test; 18 of facdrone's 1 809, eight of the first ten named in no
other tracked source file and one re-exported from a package initialiser and used nowhere
(6.3, 6.4). Nothing in either list was an exception or a test class, so the list is neither
short nor long by measurement.

### `structure.unused_variable`

**Reports.** An affected module-level binding that nothing in the project reads: no `Useby`,
`Callby` or `Typedby` reference from a project file. **A write is not a use**: a binding
something assigns and nothing reads is dead, which is also how Understand's own
`CountUnusedVariable` counts.

**Does not report.** Dunders, `log`, `logger`, `pytestmark` and `_` (`unused_variables_ignore`):
read by the interpreter, the packaging tools, the logging machinery or pytest, or the
deliberate discard. Local variables, which are `CountUnusedVariable`'s subject and not this
rule's. Anything below either floor.

**Default.** `unused_variables` off; the ignore list as shipped, `^_$` added in 6.4.

**Measurement.** This is the rule whose failure set the accuracy floor: at 19.1% every one of
the sixteen `src/` bindings the snapshot answered unreferenced was in fact read (task 3.3's
review). Floors at zero: 17 here, first ten 5 genuine and 5 the `_` discard, which is why
`^_$` joined the list and not `^_`, which would have excused facdrone's `_SAMPLE_EVERY` and
`_MAX_LEVERAGE_STEPS`, both bound and never mentioned again; 55 on facdrone, first ten 3
named nowhere else, 4 with a same-file mention and 3 named in other files the analysis did not
resolve, which is the accuracy floor's case exactly (6.3, 6.4).

### `structure.pass_through`

**Reports.** An affected routine with exactly one project caller, exactly one project callee
and at most `pass_through_max_statements = 2` statements, naming the callee. Understand's
`CountStmt` counts the `def` line, so 2 means a body of one statement, `return other(x)`; a
guard clause would need 3 (6.4).

**Does not report.** One caller alone: a routine with one caller and a body of its own is
the decomposition this tool's own hints ask for (req 2.2). The caller's name: `callers` is a
count, and Python records a call made at module scope against the file rather than a
routine, so a finding naming "the only caller" could be wrong about the one thing it
asserted (task 4.2). Dunders, `test_` routines, `setup`/`teardown` and `main`
(`pass_through_ignore`, the same four shapes as `structure.unused_ignore`). A routine whose
statement count was not taken, which is not judged rather than judged as empty; an operator
who deletes `routine.CountStmt` from the thresholds silences this rule without a note (6.1).
Anything below either floor. This rule's direction makes a partly resolved call graph worse
rather than quieter: an unresolved call site *removes* a caller, so a routine with three
callers of which two did not resolve measures one and qualifies (req 2.6).

**Default.** `pass_through` off; `pass_through_max_statements = 2`.

**Measurement.** Over the snapshots, 274 routines here and 459 on facdrone have one project
caller, one project callee and no override; 56 and 86 are at 2 statements and none at 1, so
2 is requirement 2.2's line and the value was kept (6.4). Floors at zero: 47 and 59 findings,
first tens 1 and 2 genuine forwarders, the rest one-line comprehensions, expressions around a
call and constructor calls taking a call's result, each holding one project call edge in two
statements exactly as a forwarder does (6.3). The number is not what separates those; the
body-shape predicate is the rule's to tighten.

### `structure.single_implementation`

**Reports.** A class with exactly one derived class and no project referrer other than that
derived class, reported when the base **or** its one derived class is in the change, because
the commit worth telling is usually the one that adds the second (req 3.2).

**Does not report.** `*Error` and `*Exception` (`single_implementation_ignore`): an exception
base with one subclass is what `except Base` catches, and collapsing it changes what callers
can catch. A base with two or more derived classes, or with any other referrer. It takes
**no floor**: `referrers` counts use, type and inheritance references rather than call edges,
so the resolution floor is not the bounding quantity; whether the accuracy floor should bound
it is an open question, because no corpus has paired an accuracy figure with a false-positive
count for this rule (task 4.2, 6.4).

**Default.** `single_implementation` off; the ignore list as shipped.

**Measurement.** 0 findings on both repositories, and both zeros are answers rather than
refusals: 5 classes here and 12 on facdrone have exactly one derived class, and every one
has between 3 and 144 other referrers (6.4). A rule that has produced no finding on two
repositories cannot be paired with a false-positive rate, so no floor was added.

### `structure.over_export`

**Reports.** An affected file that defines exactly one routine or class, holds nothing else at
module level and is depended on by exactly one other file, naming the dependant, because the
fix is to move the definition there and neither half is actionable without both names
(req 4.1).

**Does not report.** A file nothing depends on: that is dead code and the dead-code rules'
subject. A file with two or more dependants: one definition with two users is the boundary
working. A file with a second module-level definition: a routine beside a module constant is
a module with state of its own. `__init__.py`, `index.*`, `mod.rs` and `__main__.py`
(`over_export_ignore`, path globs): four languages' idiom for holding one name for one
importer. A file whose declaration counts are missing, which is unmeasured rather than empty.
It takes no floor and has no unavailable state: it reads file metrics, file edges and the
definitions walk the snapshot already carries and asks for no reference on any entity. What it
does switch on is the definitions walk, so a snapshot cached without it is not reused.

**Default.** `over_export` off; the ignore list as shipped.

**Measurement.** On this repository two files define exactly one routine or class and neither
has exactly one dependant; on a 653-file snapshot twelve files qualified on the count and
exactly one, a factory module, was genuine (task 4.1). 0 and 1 on the 6.4 run, the one
genuine; a rule firing on all twelve would be a rule about file size, which
`file.CountDeclFunction` already is.

### `structure.duplicate_block`

**Reports.** A run of at least `duplicates_min_lines = 12` consecutive code lines in an
affected file that the project holds at another location, naming up to three other
locations. Whitespace, comment and newline tokens are dropped and the remaining lexemes of
each line joined and hashed, the normalisation Understand's own duplicate-lines plugin uses,
so the two agree on what a duplicate is. Both copies are reported when both are affected, and
two copies in one file count.

**Does not report.** A window shorter than the minimum, or a single repeated line: a match is
a 64-bit hash agreeing at `min_lines` aligned positions, and that margin is what lets a hash
comparison be reported as a fact about code. A run that repeats only itself, such as a table
of identical rows with a period below the minimum, because a reader has that range open
already and there is no separable copy to delete. Paths under `duplicates_ignore`. Lines of a
file the lexer refused, which is named in a note rather than read as a file with nothing in
it (req 5.8). It takes **no floor**: it makes a presence claim carried by the locations it
names, so low accuracy can make it quieter than the code deserves and cannot make it say
something false.

**Default.** `duplicates` off; `duplicates_min_lines = 12`; `duplicates_ignore` empty.

**Measurement.** 48 findings here, about 24 places since every block is reported from both
ends, and 152 on facdrone; first tens 8 and 5 genuine (6.3). The whole lists: 42 and 122 are
code, 6 and 30 are name lists, an `__all__` or an import block of twelve or more names, which
the lexer sees as twelve code lines. A window of 15 would remove 4 and 17 of the lists at the
cost of 26 of the 42 and 63 of the 122 code findings, and a path pattern cannot name an
`__all__`, so 12 stays and the token-shape exclusion is recorded for the rule's owner (6.4).
Known and pinned rather than fixed: the three locations a finding names are three windows of
the one other copy, `:352`, `:353`, `:354`, so one copy reads as several places (6.2).

### `structure.similar_routine`

**Reports.** One finding per **family** of routines whose token shapes match at
`similar_threshold = 0.9` or above, identifiers and literals interchangeable, each member of
at least `similar_min_statements = 6` statements, the family of at least
`similar_min_family = 2`, whenever an affected routine is a member. It names the other
members with their locations, the family's size and the weakest similarity holding it
together. A family within one file carries the `shrink:` hint (the `/same_file` variant);
across files, `delete:`.

**Does not report.** Pairs: on facdrone the 69 similar pairs at 0.9 are 44 families, and
sixty-six pairwise findings about twelve `normalize` methods is noise where one finding is a
task (req 5.9). A routine nested inside another as that routine's twin: a closure scores 0.95
against its parent and neither can be deleted in favour of the other, so the edge is refused
and a contained member is dropped from any family it reaches. A routine whose statement count
was not taken. Names on `similar_name_ignore` (dunders, `setUp`/`tearDown` and their
lower-case forms), which are neither reported, nor named as members, nor a link between two
families. Paths under `similar_ignore`. Cross-language pairs, which score 0.63 on the
contract fixture and form no family. No floor, on the same grounds as the block rule.

**Default.** `similar_routines` off; `similar_threshold = 0.9`, `similar_min_statements = 6`,
`similar_min_family = 2`; `similar_ignore` empty and `similar_name_ignore` as shipped.

**Measurement.** 163 families here and 129 on facdrone at the shipped numbers; first tens 6
and 7 genuine merges, the rest twins with the same skeleton over different facts kept apart
on purpose, and none noise, so every number was kept (6.3, 6.4). The pass is lossless: two
exact ceilings on how much two shapes can match refuse a pair before it is scored, and a
whole-project `check --all` with the rule on scores or refuses all 2 890 810 pairs of this
repository's 2 405 considered routines in 24.4 s (task 5.9).

### `structure.net_growth`

**Reports.** A change whose net statement delta, the figure of the net line, exceeds
`max_net_growth`, at `net_growth_severity = "warning"`.

**Does not report.** Anything unless an operator sets `max_net_growth`: the net line never
blocks on its own (req 7.5). `0` is a legal maximum and means the change may not make the
project longer. Nothing on `--all`, which has no before side and no delta.

**Default.** `max_net_growth` unset, so off; `net_growth_severity = "warning"`.

**Measurement.** The figure itself: `net: +12 lloc (+30 lines) over 7 routines` is
`CountStmt` added minus removed, summed over the routines of the change's files on both
sides, with `CountLineCode` beside it; a routine that exists only before counts its whole
size negatively and one only after positively (req 7.2). Because the sum is over
**routines**, a deleted file that held no routines, a constants module or an `__init__.py` of
imports, reads as `net: +0 lloc (+0 lines) over 0 routines`: a genuine zero over nothing,
not a missing measurement, and the design's reading of requirement 7.2 rather than a defect
in the number. A routine whose signature changed is paired across the two sides by the
ratchet's own join, which one commit in five on this repository needs (task 2.2). The line and
its `lean already: nothing to cut` companion are on the [CLI page](cli.md#the-net-line).

### The shrink metrics: length without complexity

`routine.LinesPerStatement` and `routine.CountLineComment` are the family's two rung-six
signals and are ordinary threshold rows in the routine table above, on by default as
warnings, ratcheted like any routine metric, and configurable under `[thresholds.routine]`
and `[scope.*]`. The one `[lean]` key they read is `verbosity_min_statements = 5`: below that
many statements the ratio is not judged, because a two-statement routine spread over six
lines scores 3.0 by arithmetic and would outrank a genuinely long one.

`LinesPerStatement` shipped at 3.0 and was raised to 4.0 in 6.4: everything read in the band
between them on both repositories was a single statement the formatter had wrapped one item
per line, and a ceiling whose findings are the formatter's is one an operator deletes. At 4.0
the tail is 13 of 3 467 judged routines here (0.4%) and 204 of 5 085 on facdrone (4.0%).
`CountLineComment` at 20 leaves 23 of 6 969 routines here and 11 of 9 450 there outside
(6.3). On Python a docstring counts as comment lines and not as code, which is why
`file.RatioCommentToCode` ships as a minimum only and no comment maximum on files ships at
all.

### What reference-based detection cannot see

`structure.unused_routine` is documented as a warning because a reference database cannot
see an entry point named in packaging metadata, a handler a decorator registers, a dunder the
interpreter calls or a test pytest collects; `unused_ignore` excuses those four shapes out of
the box and the remedy is a pattern, never an added caller. The three dead-code rules above
inherit every one of those blind spots, since a parameter of an uncalled routine and a class
only a decorator names are unreferenced for the same reason, and add their own, which are per
language.

What the family reads was measured on the contract project (task 6.1, Build 1262): the two
languages it builds, Python and C++, with every reference rule on, and a scratch tree in the
six it does not, each with one base, one derived type and, where the language has one, an
interface and its implementer. Inheritance is read **on the base class**, naming the derived
one, in every language measured; the pair order in Understand's kind documentation varies per
language and the direction does not.

| Language | Inheritance kind on the base | Override on the overriding method | Measured |
| --- | --- | --- | --- |
| Python | `Python Inheritby` | `Python Overrides` | contract project, every rule |
| C++ | `C Public Derive` | `C Overrides`, with or without the `override` keyword | contract project, every rule; `Shape shape(side)` is a `C Callby Implicit` and counts as a call |
| Ada | `Ada Derive` | `Ada Overrides` | scratch tree, kinds only |
| C# | `c# csharp Derive`; an interface `c# csharp Implementby` | `c# csharp Overrides` | scratch tree, kinds only |
| Fortran | `Fortran Extendby` | **none**: Fortran records no override reference | scratch tree, kinds only |
| Java | `Java Extendby Coupleby`; an interface `Java Implementby Coupleby` | `Java Overrides` | scratch tree, kinds only |
| Pascal | `Pascal Derive` | `Pascal Overrides` | scratch tree, kinds only |
| TypeScript | `web Javascript Extendby`; an interface `web Javascript Implementby` | `web Javascript Overrides` on `extends`; **none** on `implements` | scratch tree, kinds only |

What that leaves unseen, per language:

- **Python.** A call made outside any routine is recorded against the file, so a routine
  called once from a routine and once from module scope measures one caller and can satisfy
  `structure.pass_through`. `self` and `cls` are ordinary parameter entities and are excused
  by name, not by kind. Every parameter kind the five families declare is matched by the one
  `Parameter ~Catch` filter.
- **C++.** Each translation unit carries clang's libstdc++ note, which `-accuracy` counts
  against it: the contract project measures 61.9% on 21 files and is itself refused by the
  accuracy floor, which a test asserts. A pure virtual, an interface method or a prototype
  whose definition is outside the analysis has no body, so it is absent from the token index
  and neither duplication rule can see it.
- **TypeScript.** An `implements` carries no override reference on the implementing method,
  so for that shape requirement 1.4's exclusion rests on the interface-method tally alone. A
  plain `.js` file holding an ES6 `class ... extends` produced no entities at all in a scratch
  probe: observed, not asserted.
- **Fortran.** No override reference at all, so an overriding procedure's unread parameters
  are reported like any other's, and the tally is the only guard. The extractor's class kinds
  (`class`, `interface`, `struct`) record no Fortran derived type, so `structure.unused_class`
  and `structure.single_implementation` never reach one.
- **Ada.** The same: a tagged type is not a class kind the extractor records, so the two class
  rules do not run on it. A primitive operation declares a `Self` parameter of the type, and
  its `Typedby` reference sits outside the class's own members, so an Ada base with one
  operation would count a referrer and never be a single implementation.
- **Java, C#, Pascal.** Inheritance and overrides are recorded as the table says and matched
  by the shipped kind set; nothing beyond one base, one derived type and one override was
  planted, so multiple inheritance, generics and nested types are unmeasured in every
  language.
- **Not measured at all.** Basic, Rust, VHDL and Objective-C: their `Implement`, `Extend` and
  `ObjC Extend` kinds are read off the installed kind documentation, not off a database, and
  are unverified per language until one is built. The two shapes where the worker's caller
  count and Understand's `CountCallbyUnique` would part, a module-scope call and a caller
  outside the analysis root, did not occur in the fixture, so on it the two agree on all 36
  routines and nothing says by how much they disagree elsewhere.

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

## `analysis.accuracy`

The other rule about the analysis rather than about the code. `und analyze -accuracy` reports
the share of files that parsed with neither an error nor a warning, and
`analysis.accuracy_floor` turns a figure below the floor into a finding.

**It is a warning and it never blocks**, whatever the severity map says, and that is a
decision rather than a default. A poor figure is usually a third-party package Understand has
no source for, an interpreter version it does not model, or a language feature it has not
caught up with. None of those is fixable by the commit in front of you, and a gate that
refused the commit over them is a gate that gets switched off.

Off by default: without a floor there is no finding, only the figure in the report and in the
JSON. A build that reports no accuracy at all — 6.5, or 8.0 never asked — reports nothing
rather than zero, because the absence of a measurement is not a measurement.

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
