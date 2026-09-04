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
