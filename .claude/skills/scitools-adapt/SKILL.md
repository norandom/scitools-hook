---
name: scitools-adapt
description: Adapt a repository's scitools-hook rules to what the repository actually is, with the measurement behind every change. Use when the gate reports findings that are wrong for this project rather than wrong in the code, when a first run is unusably noisy, when another tool already owns a question, or when asked to tune, configure or rescue the configuration.
allowed-tools: Read, Edit, Write, Bash, Grep, Glob
argument-hint: [measure | propose | apply | why] [path-or-rule]
---

# scitools-adapt

## Overview

`scitools-gate` and `scitools-improve` both refuse to touch the configuration. They are right
to: an agent that can silence its own findings has no gate. This skill is the other half —
the operator's decision, made **with evidence and written down**, so that a year from now
somebody can tell which overrides are still true.

The question it answers is not "how do I make this finding go away". It is:

> Is this finding wrong about **the code**, or wrong about **what this repository is**?

Only the second one is a configuration change. The first is work for `scitools-improve`.

## When to Use

- A first run on a real repository reports hundreds of findings and someone asks what to do.
- A rule is measuring something another tool already owns and blocks on.
- A directory is generated, vendored or otherwise not source, and is being judged as source.
- Files the analyser cannot read to the end are blocking commits.
- Someone asks to tune, relax, tighten or rescue the configuration.

Do not use it to unblock a specific commit. A change made to get today's commit through is
the one change that will still be there, unexplained, in a year.

## The rule you cannot buy your way past

**Measure before you change anything, and re-measure after.** A configuration edit whose
effect nobody counted is indistinguishable from turning off whatever was inconvenient.

```bash
scitools-hook check --all --format json > /tmp/before.json
jq -r '.findings | group_by(.rule) | map({rule: .[0].rule, n: length})
       | sort_by(-.n) | .[] | "\(.n)\t\(.rule)"' /tmp/before.json
```

Then, for the rule you are about to change, look at *where* it fires:

```bash
jq -r '[.findings[] | select(.rule=="file.CountDeclFunction")]
       | .[] | .path' /tmp/before.json | sed 's:/[^/]*$::' | sort | uniq -c | sort -rn
```

If the answer is "almost all in `tests/`" or "almost all in `generated/`", the rule is not
wrong — its *scope* is. That distinction decides which rung of the ladder you use.

## The first number is not a quality score

Read this before reacting to the count. A large first run is a long tail plus file-scope
counts, not a codebase that is N kinds of bad. Check the shape before you touch a limit:

```bash
jq -r '[.findings[] | select(.rule=="routine.CyclomaticStrict") | .value] | sort
       | {n: length, median: .[length/2|floor], max: .[-1]}' /tmp/before.json
```

A median of 1 against a maximum of 40 means a handful of routines, not a systemic limit
problem. Fix those with `scitools-improve`; do not raise the limit for everyone.

---

## The ladder, cheapest and most honest first

Work down it. Stop at the first rung that fits. **Never skip to a lower rung because it is
quicker.**

### 1. The analyser could not read the file

An `analysis.parse_error` is not a limit and must never be silenced as one. Its hint names
the construct. Two honest answers:

- **Rewrite the construct.** On Python the usual cause is PEP 695 syntax (`def f[T](...)`,
  `type X = ...`), which Understand 6.5 cannot parse — the parse aborts and the rest of the
  file leaves the database.
- **Acknowledge it, with a reason:**

  ```toml
  [[parse.acknowledged]]
  paths = ["src/pkg/generic.py"]
  reason = "PEP 695 type parameters; Understand 6.5 stops at the declaration."
  ```

  `reason` is required and is what the report quotes. An acknowledged file is **still
  analysed, still reported and still named** — it stops *blocking*, and nothing else. It is
  not "checked and clean": it is checked up to the construct that stopped the parse, and
  anything below that line was never measured. Never write a reason that implies otherwise.

### 2. It is not source

```bash
scitools-hook init --detect --print
```

`--detect` classifies the repository from what it *declares about itself* and prints the
evidence beside each line. Generated code, vendored trees, migrations and lock files belong
in `[project] exclude`, and this is the one place a blanket exclusion is right — the files
are not the project's work.

Read the evidence. A directory proposed for exclusion that you know is hand-written source is
a detection bug worth reporting, not a line to paste.

### 3. Another tool already owns this question

Demote, do not delete:

```toml
[structure]
# import-linter enforces the twelve layer contracts on the real import graph.
# Measured 2026-09: 9 of 9 findings here duplicated one of its contracts.
arch_cycles = "warning"
```

**One blocking voice per question.** Two gates blocking on the same thing is worse than one,
because the second one's failures teach people to bypass both. `severity = "warning"` keeps
the finding visible and counted and stops it deciding a commit.

### 4. The region is different in kind

Test code, examples and fixtures are organised by *subject*, not by size. A path scope says so
without hiding anything:

```toml
[scope.tests]
paths = ["tests/**"]
[scope.tests.thresholds.routine]
CyclomaticStrict = 15
[scope.tests.thresholds.file]
CountDeclFunction = false
```

Four things to get right here:

- **A scope never removes a file from the analysis.** It changes the numbers the file is
  judged by. That is the whole reason to prefer it over `[ignore]`.
- **`Metric = false` switches a rule off for the region — and only inside a scope.** Writing
  `CyclomaticStrict = false` in a global `[thresholds.*]` table is a configuration error
  (exit 2: *expected a number or a table with max/min, got bool*).
- **A scope carries element thresholds only**: `routine`, `class`, `file`. A
  `[scope.tests.thresholds.project]` table is refused, because the project population is
  reduced once over the whole repository and cannot be narrowed to a path.
- **Structure rules cannot be scoped at all.** `file_cycles`, `arch_cycles`,
  `new_dependencies_severity`, `fan_severity` and `max_new_dependencies_per_file` are global.
  If one is wrong only for tests, say so and change it globally, or leave it.

Two scopes matching one file both apply, in the order they appear, and the later one wins per
rule. Check what a file actually gets:

```bash
scitools-hook config --why tests/cli/test_app.py
```

### 4b. A rule that is off by default and worth turning on

Not every adaptation is a relaxation. `structure.duplicate_definition` ships **off**, and a
project that has grown by copying is exactly where it earns its keep — a module-level name
bound to the same value in many files:

```toml
[structure]
duplicate_definitions = 3
duplicate_definitions_ignore = ["log", "logger", "pytestmark"]
```

Turn it on, measure, and read the top of the list before deciding anything.

**The first question on a finding is not "collapse it" but "are these one decision?"** The
rule sees that N files bind a name to the same text; it cannot see whether the copies are
*supposed* to move together. From a real cleanup pass: `_FACTOR_VERSION = 1` in seven factor
modules is each factor's own version and collapsing it makes a bug the moment one is bumped;
`T = TypeVar("T")` in six modules is a per-module type variable; `AS_OF` in test modules is
scenarios that happen to share a date. A name bound to a per-module **identity** reads
differently from one bound to a **threshold**, and only a reader can tell them apart. Those
go in the ignore list, which is what it is for.

The list is names rather than values because the similar-looking
`PROJECT_ROOT = Path(__file__).resolve().parents[2]` is a real finding when six other files
write it with `parents[1]`.

**The most valuable finding is often the one to leave open.** One name bound to two different
values across a project -- `MIN_ACTIVE = 3` in four modules and `= 5` in five others -- is
reported as two groups, which is correct: one name, two meanings, and `grep` answers with
whichever it meets first. Unifying them is a quantitative decision, not a refactor. Leave it
visible and say so.

### 5. A project-scope rule a commit cannot act on

`[thresholds.project]` reduces over the whole repository — `AVG:CyclomaticStrict`,
`MaxCyclomaticStrict`, `MaxNesting`. A commit touching one file cannot move a project mean,
so blocking on it tells the author to fix something they did not do and cannot reach. Demote
these to `severity = "warning"` and read them as a trend.

### 6. The limit is genuinely wrong for this repository

Last rung, and the only one that needs its own measurement:

```bash
scitools-hook recommend            # the evidence, per ceiling
scitools-hook recommend --toml     # just the lines to paste
```

For every ceiling in force it reports how much of the repository is already inside it, what
each candidate limit would cost in entities reported, and who the worst offenders are. A limit
that already fits is reported `keep`. It writes nothing and applies nothing.

Two rules on what you take from it:

- **A recommendation is not a baseline.** `baseline` records *where you are* — today's worst
  value, so debt reports as `pre-existing`. `recommend` says *where to aim*. Pasting one
  believing it is the other fails silently in both directions: a recommendation read as a
  baseline blocks the first commit that touches the tail; a baseline read as a recommendation
  freezes the worst routine in the repository as the limit.
- **Prefer tightening.** A limit the repository is already inside costs nothing today and
  refuses the next regression. `recommend` never lowers a limit you already hold, so a
  tightening is always yours to propose.

---

## What is never a rung

| Move | Why not |
| --- | --- |
| `[ignore] files/classes/routines` | Regexes that make an entity skip **every** rule. It is not a quieter scope, it is a hole; a 2598-line module once disappeared into one. Use a `[scope]` so the file is still measured. |
| Raising a limit to pass today's commit | The change outlives the commit and nobody remembers why. If the limit is wrong, prove it with `recommend` in its own change. |
| `max_new_dependencies_per_file = 0` "to switch it off" | `0` is the **strictest** possible setting: every new dependency blocks. Removing the key restores the default; only `None` disables the rule. |
| Re-running `baseline` to clear findings | It **replaces** the file with today's values, worse ones included. During refinement, tighten with `check --all` and `adaptive = true`, which can only narrow. |
| Acknowledging a parse error without a reason, or with a reason that says "clean" | The file was measured only up to the construct that stopped the parse. |
| Deleting a rule you have not counted | See the first section. Every line is a decision; a decision without a number is a preference. |

## Write the reason down, every time

A configuration file full of undocumented overrides is indistinguishable from one where
somebody turned off whatever was inconvenient. Keep **only** the keys that deviate from the
defaults, each with the measurement behind it:

```toml
[thresholds.file]
# 19 of 19 CountLineCode findings are test modules organised by subject rather than by size;
# splitting them by line count made them harder to navigate, not easier. Measured 2026-09-03.
CountLineCode = { max = 800, ratchet = false }
```

Delete an override when re-measurement shows its reason has gone. That is as much a part of
this skill as adding one.

## Prove the change did what you said

```bash
scitools-hook config                      # the effective settings, and where each came from
scitools-hook check --all --format json > /tmp/after.json
jq -s '{before: (.[0].findings|length), after: (.[1].findings|length),
        blocking_before: .[0].blocking_count, blocking_after: .[1].blocking_count}' \
   /tmp/before.json /tmp/after.json
```

Then check the thing that matters most: **the findings you demoted are still there.**

```bash
jq -r '[.findings[] | select(.rule=="structure.arch_cycles")] | length' /tmp/after.json
```

A demotion that made the count go to zero did not demote anything — it hid it, and you
reached for the wrong rung.

## Escalate rather than guess

Stop and ask a human when:

- the change would raise a limit and `recommend` does not support it;
- a detection result contradicts what you know about the repository;
- the only way to make a rule fit is to exclude source code;
- two rules disagree and you cannot tell which one this project actually wants.

Bring the measurement. "It reports 230 errors" is not a case; "230 errors, 143 of them in
`tests/`, 99 of them one rule, median `CyclomaticStrict` 1 against a max of 14" is.

## Output Format

Per decision:

```md
## Decision
- FINDING: <rule>, <n> occurrences, <where they cluster>
- QUESTION: wrong about the code, or wrong about the repository?
- RUNG: <1 parse | 2 not source | 3 owned elsewhere | 4 region | 5 project-scope | 6 limit>
- CHANGE: <the exact TOML lines, with the reason comment>
- COST: <findings before -> after; blocking before -> after>
- STILL VISIBLE: <yes/no -- a demoted finding must still be reported>
```

At the end:

```md
## Configuration
- CHANGED: <n decisions, one line each>
- MEASURED: <total findings and blocking, before -> after>
- REMOVED: <any override whose reason no longer holds>
- FOR A HUMAN: <anything that needed a judgement you did not make>
```

Report `NOT_MEASURED` rather than a result for any run that exited above 1 — those did not
look at the code, and a configuration decision made on one is unfounded.
