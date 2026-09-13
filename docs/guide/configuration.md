# Configuration

## You may not need any

The built-in defaults are a complete configuration. `scitools-hook check --staged` works on a
repository with no configuration file at all.

Write one when you have a decision to record. This project's own configuration file carries
only the keys that deviate from the defaults, with the measurement behind each deviation in a
comment, so the file reads as a list of decisions rather than a copy of the defaults.

## Where settings come from

Lowest to highest precedence:

```text
built-in defaults
  < ~/.config/scitools-hook/config.toml     (user)
  < ./scitools-hook.toml                    (repository)
  < SCITOOLS_HOOK_* environment variables
  < command-line options
  < [scope.*] path scopes
```

Each layer overrides only the keys it defines. `config` shows the result and where each
setting came from:

```console
$ scitools-hook config
  baseline.adaptive = false  # default
  baseline.file = "scitools-hook.baseline.json"  # default
  project.exclude = [".git/**", "node_modules/**", ...]  # repo:/path/to/scitools-hook.toml
  project.include = ["**"]  # default
  ratchet.strict = false  # default
  structure.architecture = "Directory Structure"  # default
  ...
```

## Write a starting file

```bash
scitools-hook init
```

That writes `scitools-hook.toml` containing every value at its default, with a comment on
each section. Delete a key to go back to its default; change it to override.
`scitools-hook init --print` writes it to standard output instead.

```toml
# Thresholds: `Metric = 10` means at most 10; `Metric = { min = 0.1 }` means at least 0.1;
# add `severity = "warning"` (does not block) or `ratchet = false` (no worse-than-before
# check) inside the table.

[thresholds.routine]
CyclomaticStrict = 10
CyclomaticModified = 8
Essential = { max = 4, severity = "warning" }
MaxNesting = 3
CountLineCode = 60
CountStmt = 40
CountParams = 5
CountPath = 100
CountLineComment = { max = 20, severity = "warning" }
LinesPerStatement = { max = 4, severity = "warning" }

[thresholds.class]
CountDeclMethod = { max = 20, ratchet = false }
CountDeclMethodNonStub = { max = 15, ratchet = false }
CountDeclInstanceVariable = { max = 10, ratchet = false }
MaxInheritanceTree = { max = 4, severity = "warning" }
CountClassDerived = { max = 8, ratchet = false }
CountClassCoupled = { max = 12, ratchet = false }
PercentLackOfCohesion = { max = 70, severity = "warning" }

[thresholds.file]
CountLineCode = { max = 500, ratchet = false }
CountDeclFunction = { max = 25, ratchet = false }
CountDeclClass = { max = 3, ratchet = false }
MaxCyclomaticStrict = 10
RatioCommentToCode = { min = 0.1, severity = "warning" }

[thresholds.project]
"AVG:CyclomaticStrict" = 3
MaxCyclomaticStrict = 15
"AVG:CountLineCode" = 30
MaxNesting = 5
```

Metric names are Understand identifiers, plus the two the gate computes itself
(`CountParams`, `CountDeclMethodNonStub`). A metric that exists for none of the enabled
languages is a configuration error and exits 2.

A stats prefix at project scope reduces over the population of the scope:
`AVG`, `MEDIAN`, `MEDIANHIGH`, `MEDIANLOW`, `MEDIANGROUPED`, `MODE`, `STDEV`, `VARIANCE`.

## Choose what is analysed

```toml
[project]
include = ["**"]
exclude = [
    ".git/**", "node_modules/**", ".venv/**", "venv/**",
    "build/**", "dist/**", "target/**", "__pycache__/**",
    "*.min.js", "*.generated.*", "*.lock", "uv.lock", "package-lock.json",
]
```

Globs are relative to the repository root. **Lists replace rather than merge**, so if you set
`exclude` you are replacing the whole default list and should repeat the entries you want to
keep.

Two exclusions worth adding deliberately:

- **Test fixtures that are analysis input rather than source.** This repository excludes
  `tests/fixtures/**` because one fixture was built to *contain* a dependency cycle, a layer
  violation and a fan-out increase, and the test suite asserts the gate reports them. Gating
  on it would be gating on the test data.
- **Web assets that are not code.** Understand treats `.css`, `.html` and `.xml` as `Web`
  source and will judge them by file-scope rules. See
  [Languages](languages.md#web-is-one-language-and-it-will-enrol-your-assets-as-source).

## Different limits for different directories

A path scope changes the numbers a file is judged by. It never removes a file from the
analysis.

```toml
[scope.legacy]
paths = ["legacy/**"]

[scope.legacy.thresholds.routine]
CyclomaticStrict = 20
MaxNesting = false          # switch the rule off for this region
```

Measured on a real tree with that file in place — a routine at `CyclomaticStrict` 11 and
`MaxNesting` 4, which would normally raise both:

```console
$ scitools-hook config --why legacy/report.py
path: legacy/report.py
role: product
  no region covers this path; it is product code by default
scopes: legacy
  [scope.legacy] matched by 'legacy/**'
    routine.CyclomaticStrict = max=20
    routine.MaxNesting = false (the rule does not apply here)
parse: not acknowledged; an unreadable file here blocks the commit

$ scitools-hook check --staged
  error    routine.CyclomaticModified  report.render  line 4  1.1x limit
  warning  file.RatioCommentToCode  0.4x limit
summary: 1 error, 1 warning, 0 pre-existing, 1 blocking | exit 1: blocking violations found
```

`CyclomaticStrict` and `MaxNesting` are gone. `CyclomaticModified`, which the scope did not
mention, still applies at its default of 8.

Rules:

- A rule the scope names takes the scope's limit, severity and ratchet setting, falling back
  to the global spec for anything it does not say.
- `Metric = false` removes the rule for this path. It is not evaluated, and no finding can
  come from it.
- A rule no global threshold defines is *added* if the scope gives it a limit.
- A rule the scope does not mention keeps its global value.
- Two scopes matching one file both apply, in declaration order, and the later one wins per
  rule.

`config --why PATH` is the command to run when you are not sure which of those happened.

## Ignore entities entirely

```toml
[ignore]
files = ['^vendor/']
classes = ['Migration$']
routines = ['^test_']
```

These are regular expressions matched against the entity's qualified long name, plus the
repository-relative path for the file scope. A matching entity skips every rule and is
counted in the run's ignore total, so the report still says how much was skipped.

Prefer a path scope over an ignore where you can. A scope says "these numbers are different
here"; an ignore says "do not look".

## Structural rules

```toml
[structure]
architecture = "Directory Structure"
depth = 2
file_cycles = "error"
arch_cycles = "error"
max_new_dependencies_per_file = 5
new_dependencies_severity = "error"
fan_severity = "warning"

[structure.fan]
file_fan_in = { max = 50 }
file_fan_out = { max = 20 }
class_fan_in = { max = 30 }
class_fan_out = { max = 12 }
```

Architecture nodes come from Understand. `Directory Structure` at `depth = 2` means the
first two levels of your directory tree become the nodes.

Layer rules and coupling limits are declared as arrays of tables:

```toml
[[structure.layers]]
name = "cli must not reach the adapters"
node = "Directory Structure/src/cli"
may_depend_on = ["Directory Structure/src/runner"]
severity = "error"

[[structure.coupling]]
from_node = "Directory Structure/src"
to_node = "Directory Structure/lib"
max_refs = 50
severity = "error"
```

A layer rule reports a *new* edge that the rule does not allow. An edge that was already
there before the change is not a new edge, and a growing reference count on an existing edge
is not one either — newness is a property of the pair.

### Scattered definitions

Off by default. It reports a module-level name bound to the same value in more files than the
limit — a constant that was copied instead of shared.

```toml
[structure]
duplicate_definitions = 3
duplicate_definitions_ignore = ["log", "logger", "pytestmark"]
```

Turn it on when a project has grown by copying: type aliases, tolerance constants, test
fixtures and project-root computations are where it earns its keep. The ignore list is for the
per-module idiom, which is written out in every file on purpose. See
[Scattered definitions](../reference/rules.md#scattered-definitions-one-value-many-files).

## What Understand 8.0 adds

Every key here is **off by default**. A repository that changes none of them behaves on 8.0
exactly as it did on 6.5, which is the point: none of these is a feature you get by upgrading
your analyser.

```toml
[understand]
sarif = false           # also write Understand's own SARIF beside --sarif PATH
before_side = "shadow"  # "shadow", "commit", or "auto" (commit where the build offers it)
snapshot_cache = true   # reuse the before side's extraction between runs

[analysis]
accuracy_floor = 0.0    # unset by default; a floor raises a warning, never blocks

[structure]
unused_routines = "warning"   # unset by default
unused_ignore = ['\.__\w+__$', '(^|\.)test_', '(^|\.)(setup|teardown)(_\w+)?$', '(^|\.)main$']
architecture_options = {}     # passed to `und arch -generate -options`
```

**Every one of them is refused unless `doctor` has measured that your build offers it.** Run
`scitools-hook doctor` once after an upgrade; it records what the installation can do beside
the analysis databases, and a check reads that record rather than probing. A key the build
cannot honour stops the run with the key, the feature and the build named, rather than being
read, ignored and silently measuring something else.

### `understand.sarif`

Writes Understand's own SARIF documents beside the one `check --sarif PATH` asks for, so one
upload carries the gate's findings, Understand's parse diagnostics and, where CodeCheck is
licensed, its inspection results. The three are never merged.

The analysis companion comes from a **whole-project** analysis pass only. A warm run analyses
little or nothing, and Understand's SARIF describes the pass rather than the database, so a
warm run writes no companion and says so. See [Understand
8.0](../reference/understand-8.md#understands-sarif-a-check-concern-and-a-whole-project-one).

### `understand.before_side`

`"shadow"` exports the base commit into a tree and analyses it, which is what every release
before this one did. `"commit"` builds the before database from the commit directly.
`"auto"` asks for the commit route where the build offers it and falls back silently
otherwise, which is what makes it safe to set on a mixed fleet.

It buys reproducibility rather than speed — measured, a warm run's before side already costs
0.0 s — and it **changes the before side's file set** to the repository under `und -exclude`
rather than the shadow under `project.include` and `project.exclude`. Where those disagree,
the two sides see different projects.

### `analysis.accuracy_floor`

`und analyze -accuracy` reports the share of files that parsed with neither an error nor a
warning. Below the floor, the run says so as a **warning that never blocks**: a poor figure is
usually a third-party package or a language feature Understand has not caught up with, and
neither is fixable by the commit in front of you.

### `structure.unused_routines`

Reports each affected routine that nothing in the project calls or uses. It is a warning by
default because reference-based dead-code detection cannot see an entry point named in
packaging metadata, a handler a decorator registers, a dunder the interpreter calls or a test
pytest collects — the four shapes `unused_ignore` excuses out of the box. Add your own
patterns rather than adding a caller.

## The lean-code family: `[lean]`

Every rule in this table is **off by default**, and the table is the one place the family is
configured: code an agent left behind, layers that forward and nothing else, abstractions
with one implementation, files that export one name, copies and near-copies, and the length
of the change itself. What each rule reports and how to read a finding is on
[Lean code](lean-code.md); empirical calibration of duplication thresholds and safety floors is on
[Tuning duplicate and dead code](tuning-lean.md); the measurement behind each default is in the
[rules reference](../reference/rules.md#the-lean-code-rules). This section is the keys.

This is the excerpt `scitools-hook init` writes. Each commented line is both the switch and
the documentation of one rule: uncomment it to enable the rule, with `"warning"` as the value
to start from. Every set line belongs to one rule and does nothing until that rule is on, with
three exceptions the comment names: the two floors, which are read on every run, and
`verbosity_min_statements`, which `routine.LinesPerStatement` reads and which ships on.

```toml
# The lean-code family: code an agent left behind, layers that forward and nothing else,
# abstractions with one implementation, files that export one name, copies and near-copies.
# Every rule below SHIPS OFF, and each commented line is both the switch and the
# documentation: uncomment it to enable the rule, and "warning" -- report it, do not refuse
# the commit -- is the value to start from. Findings carry the `structure.` category, so
# [ignore], the scope overrides, the severity map and the ratchet reach them like any other
# structural rule. The *_ignore lists are regular expressions over entity names, except
# over_export_ignore, duplicates_ignore and similar_ignore, which are path globs.
# The two *_floor keys are the only set lines here that no rule owns, which is why they ship
# SET while every rule in this block ships off: runner.lean.evaluate reads them on every run,
# whatever these switches say. Every other key that ships set here belongs to one rule -- it
# is that rule's limit, its exception list or its severity -- and does nothing until that rule
# is on, with one exception: verbosity_min_statements is read by [thresholds.routine]
# LinesPerStatement, which does ship on. The floors say how much of the analysis has to have
# worked before a rule may claim a name is unused: below either one the dead-code and
# pass_through rules evaluate nothing and say which floor stopped them at what measured
# value. [lean] accuracy_floor REFUSES to judge below it; the separate [analysis]
# accuracy_floor only REPORTS a poorly resolved run and silences nothing.
# Measured on two repositories (research.md, task 6.4): similar_routines and duplicates
# report copies at these numbers; unused_parameters and pass_through mostly report test
# fixtures, overload stubs and one-line comprehensions -- enable those two last, and read
# their first ten findings before trusting their count.
[lean]
# unused_parameters = "warning"  # unset: off. Reports parameters a routine never reads
unused_parameters_ignore = ["^(self|cls|this)$", "^_", "^(args|kwargs)$"]
# unused_classes = "warning"  # unset: off. Reports classes nothing in the project references
unused_classes_ignore = ["(^|\\.)Test", "Error$", "Exception$"]
# unused_variables = "warning"  # unset: off. Reports module-level names nothing references
unused_variables_ignore = ["^__\\w+__$", "^(log|logger|pytestmark)$", "^_$"]
resolution_floor = 0.75  # below this share of resolved calls the rules above say nothing
accuracy_floor = 0.75  # the parse floor; NOT [analysis] accuracy_floor, which only reports
# pass_through = "warning"  # unset: off. Reports a routine with one caller that only forwards
pass_through_max_statements = 2
pass_through_ignore = ["\\.__\\w+__$", "(^|\\.)test_", "(^|\\.)(setup|teardown)(_\\w+)?$", "(^|\\.)main$"]
# single_implementation = "warning"  # unset: off. Reports a base class only one subclass uses
single_implementation_ignore = ["Error$", "Exception$"]
# over_export = "warning"  # unset: off. Reports files defining one name for one importer
over_export_ignore = ["**/__init__.py", "**/index.*", "**/mod.rs", "**/__main__.py"]
# duplicates = "warning"  # unset: off. Reports copies of duplicates_min_lines lines or more
duplicates_min_lines = 12
duplicates_ignore = []
# similar_routines = "warning"  # unset: off. Reports routines whose tokens twin another's
similar_min_statements = 6
similar_threshold = 0.9  # 1.0 is an identical token run
similar_min_family = 2  # 2 keeps a plain twin
similar_ignore = []
similar_name_ignore = ["(^|[.:])__\\w+__$", "(^|[.:])(setUp|tearDown|setup|teardown)(_\\w+)?$"]
verbosity_min_statements = 5  # below this a routine is not judged by LinesPerStatement
# max_net_growth = 50  # unset: off. Reports a change adding more than N net lloc
net_growth_severity = "warning"  # how a max_net_growth finding is reported
```

The keys, in the five groups the excerpt is written in. A switch takes `"warning"` or
`"error"`; leaving it unset is off. **The switch and the rule it enables are not spelled the
same**: the finding is named after what it reports, in the singular, and the switch after the
table it configures.

| Key | Enables or bounds | Notes |
| --- | --- | --- |
| `unused_parameters` | `structure.unused_parameter` | A parameter the routine never reads, sets or modifies, and that no signature it overrides asks for. |
| `unused_parameters_ignore` | its exceptions | Regular expressions over parameter names. Ships excusing `self`, `cls`, `this`, a leading underscore, `args` and `kwargs`. |
| `unused_classes` | `structure.unused_class` | A class nothing in the project references. |
| `unused_classes_ignore` | its exceptions | Regular expressions over class names. Ships excusing `Test*`, `*Error` and `*Exception`. |
| `unused_variables` | `structure.unused_variable` | A module-level name nothing in the project reads. |
| `unused_variables_ignore` | its exceptions | Regular expressions over binding names. Ships excusing dunders, `log`, `logger`, `pytestmark` and `_`. |
| `resolution_floor` | the four rules above and `pass_through` | The share of a language's call sites that resolved to a project routine, below which those rules evaluate nothing and say so once per run. Read on every run, whatever the switches say. |
| `accuracy_floor` | the same five rules | The share of the analysis Understand parsed without an error, below which those rules refuse. **Not** `analysis.accuracy_floor`, which reports and silences nothing. |
| `pass_through` | `structure.pass_through` | A routine with one project caller and one project callee that only forwards. |
| `pass_through_max_statements` | its bound | Understand's `CountStmt` counts the `def` line, so the shipped `2` means a body of one statement; a guard clause would need `3` (measured, task 6.4). |
| `pass_through_ignore` | its exceptions | Regular expressions over routine long names. Ships as `structure.unused_ignore` does: dunders, `test_*`, `setup`/`teardown`, `main`. |
| `single_implementation` | `structure.single_implementation` | A base class exactly one subclass derives from and nothing else names. |
| `single_implementation_ignore` | its exceptions | Regular expressions over class names. Ships excusing `*Error` and `*Exception`. |
| `over_export` | `structure.over_export` | A file defining one name that one other file imports. |
| `over_export_ignore` | its exceptions | **Path globs**, relative to the repository root. Ships excusing `__init__.py`, `index.*`, `mod.rs` and `__main__.py`. |
| `duplicates` | `structure.duplicate_block` | A run of lines the project holds elsewhere, whitespace and comments absent. |
| `duplicates_min_lines` | its window | The shortest run reported, in code lines. Measured on two repositories (task 6.4): a window of 15 would have cost 26 of 42 and 63 of 122 code findings. |
| `duplicates_ignore` | its exceptions | Path globs, for a fixture or generated tree that is copies by design. |
| `similar_routines` | `structure.similar_routine` | A family of routines whose token streams match above the threshold, identifiers and literals interchangeable; one finding per family, never one per member. |
| `similar_min_statements` | its population | A routine below this many statements is in no family. |
| `similar_threshold` | its bound | Token similarity holding a family together; `1.0` is an identical token run. |
| `similar_min_family` | its size | `2` keeps a plain twin; a family of one is every routine in the project. |
| `similar_ignore` | its exceptions | Path globs, as `duplicates_ignore`. |
| `similar_name_ignore` | its exceptions | Regular expressions over routine long names, for the shapes a family is idiomatic rather than duplicated. Ships excusing dunders and `setUp`/`tearDown`/`setup`/`teardown`. |
| `verbosity_min_statements` | `routine.LinesPerStatement` | Below this many statements the ratio is not judged: a two-statement routine over six lines scores 3.0 by arithmetic. The metric itself is a threshold under `[thresholds.routine]` and ships on. |
| `max_net_growth` | `structure.net_growth` | Unset, the net line never blocks. Set, a change adding more net logical lines than this is a finding; `0` is legal and means "may not make the project longer". |
| `net_growth_severity` | its severity | `"warning"` by default. |

Four things that are easy to get wrong:

- **Lists replace, they do not merge.** Setting `unused_parameters_ignore` replaces the
  shipped three patterns; repeat the ones you want to keep, as with `[project] exclude`.
- **Six lists are regular expressions and three are globs.** `over_export_ignore`,
  `duplicates_ignore` and `similar_ignore` name files, in the glob language `[project]`
  speaks; writing `.*\.py$` there is a literal that matches nothing, and the loader says so.
  The other six match entity names.
- **A path scope does not reach this table.** `[scope.*]` holds thresholds, so it can raise,
  demote or switch off `LinesPerStatement` and `CountLineComment` for a region and nothing
  under `[lean]`; the structural rules and their lists are repository-wide. The tests policy
  that follows from that is on [Lean code](lean-code.md#tests).
- **The floors are not a rung of adaptation.** Both measured repositories sit below them
  (19.1% and 25.9% accuracy, task 6.4), and with the floors forced to zero the rules they
  guard were right in 0 to 8 of their first ten. A floor moves when a repository above it is
  measured, not to make a rule speak.

The family reads Understand's references and its lexer, and `doctor` prints a row for each:
the reference walk the dead-code and layering rules need, the lexer the two duplication rules
need, and the optional duplicate-lines metric. As with the Understand 8.0 keys above, a switch
the installed build cannot honour stops the run rather than measuring something else, with
the key, the capability and the build named:
`lean.duplicates needs lean tokens, which <build> does not offer`.

What enabling the family costs, measured with everything on (task 6.3): a warm check gained
4.2 s on a 14.7 s run, 3.8 s of it the after-side snapshot, and a whole-project `check --all`
with `similar_routines` on compares every routine with every other, 43.7 s on a 319-file
repository and 85.7 s on a 945-file one. Off, the family extracts nothing and a warm check
costs what it costs today.

## Severities

Every rule has a severity. `error` can block; `warning` never does, in any mode.

**There is no central severity map.** A severity is set where the rule is declared. A
top-level `[severity]` table is rejected at load:

```console
$ scitools-hook config
error: severity: Extra inputs are not permitted
  file: /path/to/scitools-hook.toml
```

| Rule family | Where its severity lives |
| --- | --- |
| thresholds | inline in the table: `CountPath = { max = 100, severity = "warning" }` |
| file and architecture cycles | `structure.file_cycles`, `structure.arch_cycles` |
| new dependencies | `structure.new_dependencies_severity` |
| fan-in and fan-out | `structure.fan_severity` |
| layer and coupling rules | `severity` inside each `[[structure.layers]]` / `[[structure.coupling]]` |
| CodeCheck | `codecheck.severity` |
| ratchet findings inside a limit | `ratchet.below_limit_severity` |
| `analysis.parse_error` | **not configurable** — use `[[parse.acknowledged]]`, below |

```toml
[thresholds.routine]
CountPath = { max = 100, severity = "warning" }

[structure]
fan_severity = "error"
new_dependencies_severity = "warning"
```

A threshold table accepts exactly four keys and rejects anything else by name:

```console
error: thresholds.file.CountDeclFunction: unknown keys enabled;
       allowed: max, min, ratchet, severity
```

**A global rule cannot be switched off.** `Metric = false` is a `[scope.*]` construct only;
at the top level it is a type error:

```console
error: thresholds.file.CountDeclFunction: expected a number or a table with max/min, got bool
```

To stop a rule blocking, demote it. That keeps the finding, its entity, its line and its hint
in the report and removes only its ability to refuse a commit.

### `ratchet.below_limit_severity`

```toml
[ratchet]
below_limit_severity = "warning"   # the default
```

The severity ceiling a ratchet finding gets while the entity is **still inside its own limit
after the change**. Shipping it as `warning` is what stops the gate freezing every file it is
pointed at:

```text
warning  routine.CountLineCode  pkg.big.grow  line 4  worse than before, was 23
  routine pkg.big.grow CountLineCode rose from 23 to 24, still within the maximum 60
```

Set it to `error` to refuse any movement at all. It is a **ceiling, never a promotion**: a
rule you demoted to `warning` yourself stays a warning under either value. Full detail on
[the ratchet](../argument/ratchet.md#growth-inside-the-limit-is-reported-not-refused).

## Acknowledging a file that does not parse

A file in the selection that Understand could not read is a blocking `analysis.parse_error`,
because a file the analyser could not read must never report as clean. If you genuinely have
to ship past one, the acknowledgement requires a written reason:

```toml
[[parse.acknowledged]]
paths = ["pricing/generic.py"]
reason = "PEP 695 type parameters; Understand 6.5 stops at the declaration."
```

What that does, and only what it does: it clears `blocking` on that finding. The finding
keeps its `error` severity, keeps its place in the report, and **gains a sentence** saying
the file is measured only up to the construct that stopped the parse. Measured, with and
without that entry, on the same file:

```console
$ scitools-hook check --staged
pricing/generic.py
  error    analysis.parse_error  line 4
    Understand could not read pricing/generic.py: 6 parse errors, the first at line 4:
    expected token '(' at token [. The analysis stops where the parse stops, so the code
    after it is absent from the database and no rule ran on it -- this file cannot be
    reported as checked. -- acknowledged: PEP 695 type parameters; Understand 6.5 stops
    at the declaration.; the file is measured only up to the construct that stopped the
    parse; nothing after it was read
    hint: PEP 695 type parameters: Understand 6.5 cannot parse a type-parameter list, and
    one of them costs the rest of the file. Declare the variable explicitly instead --
    `T = TypeVar("T")` at module level, then `def generic(x: T) -> T:` and
    `class Box(Generic[T]):` -- which is the same type with a spelling the analysis reads

summary: 1 error, 0 warnings, 0 pre-existing, 0 blocking | 1 file failed to parse, not fully checked | exit 0: no blocking violations
```

`1 error ... 0 blocking`, and the run still says `1 file failed to parse, not fully checked`.

An acknowledged file never reads as a clean one. Strict mode does not override it, because an
acknowledgement is a statement about the *analyser*, not about whether a violation is old.
An acknowledgement covers `analysis.parse_error` and nothing else — one that reached a
threshold finding would be an ignore list wearing another name.

`scitools-hook init --detect` will propose acknowledgement entries for the files it finds,
**commented out**, because uncommenting one is the operator's decision.

## The adaptive baseline

```toml
[baseline]
file = "scitools-hook.baseline.json"
adaptive = true
```

```bash
scitools-hook baseline      # capture the worst current value per ratcheted rule
```

With `adaptive = true` the effective limit becomes `min(configured, baseline)` for a maximum
and `max(configured, baseline)` for a minimum. A baseline can only narrow the configured
limit, never widen it. Tightening only ever lowers a recorded value, and only runs on a
whole-project analysis.

Do not commit a baseline you captured by accident. This repository gitignores the file
precisely because two were once captured from whatever the tree happened to be at the time,
which is not a decision.

## Remediation hints

Every finding carries a hint. Override one:

```toml
[hints]
"routine.MaxNesting" = "Use a guard clause. See docs/style.md."
```

## Environment variables

| Variable | Effect |
| --- | --- |
| `SCITOOLS_HOME` | The Understand installation to use. |
| `SCITOOLS_HOOK_*` | Overrides for configuration keys, above the repository file. |
| `SCITOOLS_HOOK_SKIP` | Set non-empty to skip the gate for one commit. A chained hook still runs. |
| `SCITOOLS_HOOK_SOFT_FAIL` | Set non-empty to warn instead of blocking when the gate **could not run** (exit 2 and above). Findings, exit 1, block regardless. |

`git commit --no-verify` skips every hook, not just this one.
