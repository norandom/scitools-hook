# Every feature, and whether it is on

One page that says what this tool does. Each row names the thing, whether it ships **on** or
**off**, and where the detail is. Nothing here is a summary of a summary: if a row interests
you, the link is the page that argues it.

Two conventions run through the whole list.

- **Off means off.** A feature that ships off changes nothing about a run until you write the
  key. That is not caution for its own sake: a rule whose first run on a real repository is
  mostly false positives should be opted into rather than opted out of, and every "off" below
  has a measurement behind it.
- **A feature the installed Understand cannot do is refused before the run starts.** Not read,
  ignored and quietly measured as something else. `scitools-hook doctor` records what your
  build offers; a check reads that record. See
  [Understand 8.0](understand-8.md#what-80-buys-measured).

## What it measures

| | Ships | Where |
| --- | --- | --- |
| Per-routine, per-class, per-file and per-project thresholds — complexity, nesting, length, parameters, cohesion, comment ratio | **on**, with defaults | [Rules](rules.md#threshold-defaults) |
| Statistical thresholds over a whole scope, `AVG:`, `MAX:`, `P90:` and friends | on where configured | [Rules](rules.md#rule-names) |
| Two metrics the gate computes itself, because Understand has no usable value for them: `CountParams` and `CountDeclMethodNonStub` | on | [Rules](rules.md) |
| The Understand 8.0 plugin metrics: `CountGlobalsModified/Set/Used`, `CountClassCoupledModified`, `CorePercentage`, `BidirectionalDepsPercent`, the `CBRI*` family | **off** — none is a shipped threshold | [Understand 8.0](understand-8.md#what-80-buys-measured) |
| How much of the analysis Understand actually resolved, per side | reported **on**, judged **off** | [Configuration](../guide/configuration.md#analysisaccuracy_floor) |

## What it compares

| | Ships | Where |
| --- | --- | --- |
| The ratchet: every affected entity's before and after value, inside one commit | **on** | [The ratchet](../argument/ratchet.md) |
| Dependency cycles between files and between architecture nodes, reported only when the change creates one | **on** | [Rules](rules.md#structural-rules) |
| Layer rules and coupling limits you declare, over Understand architecture nodes | on where declared | [Configuration](../guide/configuration.md#structural-rules) |
| Fan-in and fan-out, per file and per class | **on**, as warnings | [Rules](rules.md#structural-rules) |
| New dependencies per file | **on** | [Rules](rules.md#structural-rules) |
| Call cycles and reachable complexity | **off** | [Rules](rules.md#structural-rules) |
| A module-level name bound to the same value in many files | **off** | [Rules](rules.md#scattered-definitions-one-value-many-files) |
| Routines nothing in the project calls or uses | **off** | [Configuration](../guide/configuration.md#structureunused_routines) |
| Findings from an Understand CodeCheck configuration | off until you name one | [Rules](rules.md#structural-rules) |
| An adaptive baseline that only ever narrows | **off** | [Configuration](../guide/configuration.md#the-adaptive-baseline) |

## What it produces

| | Ships | Where |
| --- | --- | --- |
| A human report with a remediation hint on every finding | **on** | [CLI](cli.md#check) |
| `--format json`, one document, schema-versioned | on request | [Agents](../guide/agents.md) |
| SARIF 2.1.0, to standard output or to `--sarif PATH` | on request | [CLI](cli.md#-sarif-path-and-understands-own-documents) |
| Understand's own SARIF beside the gate's, re-rooted on the repository, for one code-scanning upload | **off** | [CLI](cli.md#-sarif-path-and-understands-own-documents) |
| Dependency and butterfly graphs as SVG, plus an impact set, for reviewing a large change by shape | on request | [Review](../guide/review.md) |
| The effective limits written into your agent instructions file | on request | [CLI](cli.md#agent-rules) |
| A recommendation: which limits fit this repository, and what each candidate would cost | on request | [CLI](cli.md#recommend) |

## How it runs

| | Ships | Where |
| --- | --- | --- |
| A native `.git/hooks` pre-commit shim, chaining to whatever hook was there | on install | [Hooks and CI](../guide/hooks-and-ci.md) |
| A pre-push shim that checks each pushed range | on install | [Hooks and CI](../guide/hooks-and-ci.md) |
| A `pre-commit` framework hook definition | on install | [Hooks and CI](../guide/hooks-and-ci.md) |
| `--staged`, `--worktree`, `--all`, `--files` and `--range A..B` | on | [CLI](cli.md#check) |
| Nothing written into the working tree: shadows, databases and state live in a cache | **on** | [Operations](operations.md#databases) |
| Per-directory limits, so tests and generated code can be judged differently | on where configured | [Configuration](../guide/configuration.md#different-limits-for-different-directories) |
| `doctor`, which reports the installation, the licence, the features and the cache and always exits 0 | on | [CLI](cli.md#doctor) |

## What it costs

The warm one-line check on this repository is **13.0 s**, down from 27.7 s in `0.1.0a8`. Four
snapshot extractions became one, and the before side is served from a cache.

| | Ships | Where |
| --- | --- | --- |
| One extraction per side, recording two dependency rings in a single walk | **on** | [Understand 8.0](understand-8.md#what-80-buys-measured) |
| A snapshot cache for the before side, keyed on everything that could change the document | **on** | [Configuration](../guide/configuration.md#what-understand-80-adds) |
| A before database built from the base commit instead of an exported tree | **off** | [Configuration](../guide/configuration.md#understandbefore_side) |
| Git-derived architectures, generated from the after side's commit | **off** | [Understand 8.0](understand-8.md#git-architectures-need-a-commit) |

A check with nothing selected analyses nothing, extracts nothing and stores nothing: measured
at 1.0 s, which is the process starting and the configuration being read.

## What it does not do

Worth saying plainly, because each is a thing people reasonably expect.

- **It does not judge correctness, types, security or style.** Those are your test suite,
  mypy, CodeQL or Semgrep, and ruff. See [Against the alternatives](../compare/linters.md).
- **It does not fix anything.** Every finding carries a hint saying what to change; nothing
  changes it for you.
- **It does not report the debt you already have.** A commit is judged against its own before
  side, so existing violations in code you touched are reported as pre-existing and do not
  block. `check --all` is the inventory, and it has no before side at all.
- **It is not on PyPI**, and will not be. See [Install](../guide/install.md).
