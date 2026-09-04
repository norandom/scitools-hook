---
name: scitools-gate
description: Explore a repository's maintainability and check a change through the scitools-hook CLI. Use before committing, when a commit was blocked, when asked which code is too complex to change safely, or when a reviewer needs the structural picture of a large change.
allowed-tools: Read, Bash, Grep, Glob
argument-hint: [check | explain | worst | config | doctor] [path-or-range]
---

# scitools-gate

## Overview

`scitools-hook` measures the routines, classes and files a change touches with SciTools
Understand, compares each one against what it was before the change, and refuses a commit
that makes any of them measurably worse.

This skill drives that CLI to answer four kinds of question:

1. **Will this change be allowed?** — the staged or working-tree check.
2. **What did this change do to the shape of the code?** — the structural summary of a range.
3. **Which code here is too complex to change safely?** — the worst values per metric.
4. **Which rules apply, and why does this path get those numbers?** — the effective
   configuration.

It never edits the configuration to make a finding disappear.

## When to Use

- Before staging or committing a change you wrote.
- After a commit was blocked, to find out what to fix.
- When asked to assess, survey or explore an unfamiliar repository's structure.
- When asked which routines are the risky ones, or where the dependency cycles are.
- Before reviewing a large change somebody else (or another agent) produced.

Do not use this skill to decide whether code is *correct*. It has no opinion on correctness,
types, security or style. Those are `mypy`, `ruff`, CodeQL and Semgrep.

## Preconditions

Check these first. Do not report a clean run that was actually an infrastructure failure.

```bash
scitools-hook doctor
```

Read three rows of that output before going further:

- `license: ok` — anything else means the run cannot happen (exit 4).
- `api mode:` — `not verified` means no usable API; the run will exit 3.
- `analysis python:` — must be a 3.x interpreter. If it is not, Understand analyses **Python
  2**, every routine after the first Python 3 construct silently leaves the database, and the
  run reports success over code it never read.

`doctor` always exits 0. It reports; it does not judge. Read the `Problems` section.

If Understand is not installed at all, say so and stop. Do not guess at the numbers.

## Protocol

### 1. Check a change

```bash
scitools-hook check --worktree           # while still editing; nothing staged yet
scitools-hook check --staged             # what the commit hook will run
scitools-hook check --files path/a path/b
```

**`--files` and `--staged` measure the index, not your working tree.** Only `--worktree` reads
the files as they are on disk. Editing a file and then running `check --files that/file.py`
without staging it reports the problem you just fixed; this has cost real sessions several
iterations. Stage it, or use `--worktree`.

Exit 0 means nothing blocks. Exit 1 means the change is blocked. Anything else is an
infrastructure failure, not a verdict about the code — see the exit-code table below.

For machine reading:

```bash
scitools-hook check --staged --format json
```

Then work from `blocking_count` and the `findings` array. **Do not stop until
`blocking_count` is `0`.**

### 2. Fix what blocks

For each finding whose `blocking` is `true`:

- read `message` for the measurement,
- read `hint` for what to change,
- change the code.

The hints are specific and are worth following literally. `routine.MaxNesting` says *extract
the inner block into its own routine, or invert the condition and return early*.
`routine.CyclomaticModified` says *collapse the case arms into a lookup table or a
polymorphic call*.

Extracting a helper is safe, twice over: the counts a decomposition raises by construction
(`file.CountDeclFunction`, `file.CountLineCode`, `class.CountDeclMethod` and five others)
ship with the ratchet off, and a routine that grows while staying inside its limit produces a
warning rather than a refusal. Splitting a routine does not trade one blocking finding for
another.

Re-run `scitools-hook check --worktree` after each edit.

### 3. Understand what a change did

```bash
scitools-hook explain --staged
scitools-hook explain --range "origin/main...HEAD"
scitools-hook explain --range "origin/main...HEAD" --format markdown --output review.md
scitools-hook explain --range "origin/main...HEAD" --graphs --impact --out review/
```

`explain` never blocks anything. Read `largest deltas` first: it is what moved most,
ordered, whether or not it broke a rule. `--graphs` writes one butterfly graph per changed
routine or class and one depends-on graph per changed file, as SVG. `--impact` lists what
references each changed entity, by depth.

### 4. Find the worst code in the repository

```bash
scitools-hook check --all --show-highest
```

The `highest values` section names the largest value found per metric, with the entity and
line, whether or not it breaks a limit:

```text
highest values: the largest value per metric, whether or not it breaks a limit
  routine.CountPath  4  pricing.settle.line_total  pricing/settle.py  line 24
  routine.CyclomaticStrict  4  pricing.settle.line_total  pricing/settle.py  line 24
```

`--all` has no before side, so every finding is absolute and nothing is `pre-existing`. Use it
to survey; use `--staged` to gate.

For a ranked list rather than one row per metric:

```bash
scitools-hook check --all --format json \
  | jq -r '[.findings[] | select(.scope=="routine")]
           | sort_by(-(.value // 0))[:15]
           | .[] | "\(.value)\t\(.rule)\t\(.entity.key.longname)\t\(.path):\(.line)"'
```

### 5. Read the effective configuration

```bash
scitools-hook config                       # every setting, with where it came from
scitools-hook config --why path/to/file.py # which scopes apply to one path, and why
scitools-hook agent-rules                  # the limits as a block written for an agent
```

`config --why` is the command for "why did this file get those numbers". It names the path
scope that matched, the rules it changed, and whether an unreadable file there would block.

### 6. Write the rules where the next agent will read them

```bash
scitools-hook agent-rules --write AGENTS.md
```

Inserts the effective limits between markers in that file, so an agent knows the numbers
before it writes the code rather than after the commit is refused. Re-run it whenever the
configuration changes.

## Reading a finding

Every finding carries: `rule`, `scope`, `path`, `line`, `value`, `before`, `limit`,
`severity`, `blocking`, `preexisting`, `message`, `hint`.

| What you see | What it means |
| --- | --- |
| `1.4x limit` | An absolute threshold. The value is 1.4 times the maximum. |
| `worse than before, was 12` | A ratchet finding. The value moved in the worse direction. It **blocks** only if the entity is outside its limit after the change. |
| `rose from 23 to 24, still within the maximum 60` | The same, while inside the limit. Reported as a **warning**; does not block. |
| `pre-existing` | The violation was already there before this change and did not get worse. **Does not block.** |
| `warning` | Reported and counted. **Never blocks, in any mode.** |
| `analysis.parse_error` | A file in the selection could not be read. Nothing after the named line was measured and no rule ran on it. This blocks. |

An `analysis.parse_error` is not a limit at all and must not be silenced. Its hint names the
construct to rewrite. On Python, the most common cause is PEP 695 syntax (`def f[T](...)`,
`type X = ...`), which Understand 6.5 cannot parse — the parse aborts and the rest of the
file leaves the database.

## Exit codes

| Code | Meaning | What to do |
| ---: | --- | --- |
| 0 | No blocking violations | Proceed. |
| 1 | Blocking violations found | Fix the code. |
| 2 | Configuration error | The config names an unknown key, metric, scope, regex or architecture. Report it; do not "fix" it by deleting rules. |
| 3 | No usable Understand installation | Infrastructure. Report it. The code was **not checked**. |
| 4 | No valid license | Infrastructure. Report it. The code was **not checked**. |
| 5 | Analysis failed | Try `scitools-hook db rebuild`, then report. |
| 6 | Not inside a git repository | Change directory. |
| 7 | Report could not be delivered | Check `--output` / `--sarif` paths. |
| 70 | Unexpected internal error | Report it with the command and the output. |

**Only 0 and 1 are statements about the code.** Everything above 1 means nothing was
measured, and reporting such a run as clean is a false completion claim.

## The rule this skill will not break

When the gate blocks a change:

1. Read the `message` and the `hint` of every finding whose `blocking` is `true`.
2. **Fix the code.**
3. Re-run until `blocking_count` is `0`.

Do not raise a limit. Do not add an ignore pattern. Do not add a path scope. Do not
re-capture the baseline. Do not acknowledge a parse error. Every one of those changes the
rules rather than the code, and all of them are the operator's decision, not the agent's.

If you believe a limit is genuinely wrong for this repository, say so, with the measurement,
and let a human decide. Do not make the change and report success.

## Common Rationalizations

| Rationalization | Reality |
| --- | --- |
| "It only exceeds the limit slightly." | The limit is the limit. Growth *inside* a limit is only a warning; a blocking finding means the value crossed the limit or worsened something already over it. |
| "I'll add it to `[ignore]` and fix it later." | That is changing the rules. Fix the code or escalate. |
| "The check exited 3, so there is nothing to fix." | Exit 3 means nothing was checked. It is not a pass. |
| "Splitting the routine will trip `file.CountDeclFunction`." | It will not. Eight decomposition counts ship with the ratchet off, and the absolute limits are far away. |
| "The file has no findings, so it is clean." | Not if it is named in `parse_errors`. A file the analyser could not read has no findings *because it was never read*. |
| "`check --all` reported 231 errors, so this repository fails the gate." | `--all` is an inventory with no before side. A commit is gated by `--staged`, where those are `pre-existing` and do not block. |
| "I ran the check before my last edit and it passed." | Re-run it. Evidence must be fresh. |

## Output Format

```md
## Gate Result
- STATUS: PASS | BLOCKED | NOT_CHECKED
- COMMAND: <the exact command run>
- EXIT: <code>
- BLOCKING: <count, and the rule + entity of each>
- PRE_EXISTING: <count -- reported, not blocking>
- WARNINGS: <count>
- PARSE_ERRORS: <files in the selection that were not read, or "none">
- ACTION: <what was changed in the code, or what a human must decide>
```

Use `NOT_CHECKED` for any exit code above 1. Never report `PASS` from a run that did not
measure the code.
