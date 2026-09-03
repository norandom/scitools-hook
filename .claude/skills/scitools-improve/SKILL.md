---
name: scitools-improve
description: Iteratively lower a repository's complexity through scitools-hook, one commit at a time, so coding agents stay effective as the project grows. Use when a project has become hard to change safely, when the gate reports a large first-run count, when asked to work through the findings, or when asked to improve or tighten the maintainability baseline.
allowed-tools: Read, Edit, Write, Bash, Grep, Glob
argument-hint: [setup | survey | loop | tighten] [path-or-limit]
---

# scitools-improve

## Overview

`scitools-gate` answers **"may this commit land?"**. This skill answers the other question:
**"how does this repository get measurably easier to change, one commit at a time?"**

It is not a code clean-up task and the goal is not a tidy codebase. A coding agent reads a
routine and the neighbourhood that routine reaches. Once that working set grows past what
fits in a model's head, the agent stops reasoning about a change and starts guessing. Every
commit in this loop exists to shrink one working set.

Nothing here assumes a particular repository, language, layout or limit. Everything is read
from the project in front of you.

## When to Use

- The gate reports hundreds of findings and someone asks what to do about them.
- A project has reached the point where changes to it keep going wrong.
- You are asked to work through the maintainability findings, or to tighten the baseline.
- A limit was raised in the past to get moving, and you are asked to earn it back.

Do not use this skill to gate a single change you just wrote — that is `scitools-gate`.
Do not use it to judge correctness, types, security or style. It has no opinion on any of
them; those are the test suite, `mypy`, `ruff`, CodeQL and Semgrep.

## The number you must not chase

**The goal is not zero findings.** The first run on a real repository reports hundreds. That
is an inventory, not a verdict:

```console
$ scitools-hook check --all
summary: 1286 errors, 600 warnings, 0 pre-existing, 1286 blocking | exit 1
```

`--all` has no before side, so every violation is absolute and nothing is `pre-existing`.
The same repository gated by `check --staged` reports those as `pre-existing`, and they do
not block. Exit 1 from `--all` is normal and is not a statement about any commit.

> You are never asked to fix the 1286 findings.
> You are forbidden from adding the 1287th.

Work in that order: stop the bleeding (the gate already does that), then lower the ceiling
deliberately, entity by entity.

## Preconditions

```bash
scitools-hook doctor
```

Read three rows before trusting any number: `license`, `api mode`, `analysis python`. Only
exit codes 0 and 1 are statements about the code — anything above 1 means nothing was
measured, and reporting such a run as progress is a false completion claim.

Also read `git status --porcelain`. Start from a clean tree. This loop commits.

---

## Phase 1 — Record where you are

Skip whatever already exists; do not regenerate a configuration somebody has tuned.

**No `scitools-hook.toml` yet?** Look at it before writing it:

```bash
scitools-hook init --detect --print     # review
scitools-hook init --detect             # then write
```

`--detect` classifies the repository from the evidence in it — which directories are tests,
which are generated or vendored, which files the analyser cannot read — instead of guessing
from names. Read the reason beside each line. Anything you disagree with is an operator
decision, so raise it rather than editing it away.

**No baseline yet?**

```bash
scitools-hook baseline
```

That records today's worst value for every configured limit, so existing debt reports as
`pre-existing`. Then check `[baseline]` in the configuration:

```toml
[baseline]
adaptive = true
```

**`adaptive` is off by default and this loop does nothing useful without it.** Off, the
baseline is a fixed floor. On, the effective limit is `min(configured, recorded)` and every
whole-project run lowers a recorded value the code has beaten — which is the mechanism that
turns each improvement into the new ceiling. Turning it on is the operator's call; propose
it, with this sentence, if it is off.

Commit the configuration and the baseline **on their own**, in a commit that changes no
code, so the first behavioural commit has a clean before side.

## Phase 2 — Decide where to aim

```bash
scitools-hook recommend            # the evidence
scitools-hook recommend --toml     # only the lines you would paste
```

`baseline` says where you are. `recommend` says where to aim: for every ceiling in force, how
much of the repository is already inside it, what each candidate limit would cost in entities
reported, and who the worst offenders are. A limit that already fits is reported `keep`.

It writes nothing and applies nothing, deliberately. **Propose the diff and let a human take
it.** Two rules on what you may propose:

- Never propose a line that *raises* a limit. That is not aiming, it is surrender with a
  measurement attached.
- Prefer tightening a limit the repository is already inside — it costs nothing today and
  refuses the next regression.

## Phase 3 — Survey, once per batch

```bash
scitools-hook check --all --show-highest
```

`highest values` names the largest value found per metric with its entity and line, whether
or not it breaks a limit. For a ranked list of the worst routines:

```bash
scitools-hook check --all --format json \
  | jq -r '[.findings[] | select(.scope=="routine" and .blocking)]
           | sort_by(-((.value // 0) / (.limit // 1)))[:15]
           | .[] | "\(.value)/\(.limit)\t\(.rule)\t\(.entity.key.longname)\t\(.path):\(.line)"'
```

This run is the expensive one. Do it **once per batch of commits**, not after every edit.

Pick the next entity by this order:

1. **Routine-scope metrics first** — `CyclomaticStrict`, `CyclomaticModified`, `MaxNesting`,
   `CountPath`, `CountStmt`. These measure the working set of a single change, which is the
   thing this whole exercise is about. File-scope counts (`CountDeclFunction`,
   `CountLineCode`) mostly measure how much lives in one place, which costs an agent far
   less.
2. **Worst relative to its limit**, not worst absolute. `3x limit` on a small routine is a
   better first commit than `1.1x` on a large one.
3. **Code that actually changes.** `git log --format= --name-only -n 400 -- <path> | sort |
   uniq -c | sort -rn` ranks churn. Complexity in a file nobody has touched in two years
   costs a model nothing, because no model ever reads it.

Do not pick:

- generated, vendored or migration code — propose a `[scope]` exclusion to the operator
  instead of refactoring it;
- anything named under `parse_errors`. Those files were measured only up to the construct
  that stopped the parse, so a clean check on one is not evidence its code is fine. Fixing
  the construct itself is a good separate commit; its hint names it.

## Phase 4 — The loop: one entity, one commit

For each chosen entity:

1. **Start clean.** `git status --porcelain` empty.
2. **Read the finding's `hint:`.** It says what to change, not what is wrong, and it is worth
   following literally. `MaxNesting` → *extract the inner block, or invert the condition and
   return early*. `CyclomaticModified` → *collapse the case arms into a lookup table or a
   polymorphic call*.
3. **Change the code.** Extraction is expected and safe: the counts a decomposition raises by
   construction ship with the ratchet off, and a routine that grows while staying inside its
   limit produces a warning, not a refusal. Splitting a routine does not trade one blocking
   finding for another. If an extraction *is* refused, that is a bug — report it, do not work
   around it.
4. **Run the repository's own tests.** This skill measures shape and has nothing to say about
   whether the code still works; a green gate on broken code is the worst outcome available
   here. Find the command in the repo — `Makefile`, `justfile`, `pyproject.toml`,
   `package.json`, `scripts/`, the CI workflow — and run it. If you cannot find one, say so
   before committing rather than after.
5. **Re-check the change only**, which is fast:
   ```bash
   scitools-hook check --worktree
   ```
6. **Stage and confirm**, until `blocking_count` is `0`:
   ```bash
   git add <paths> && scitools-hook check --staged --format json
   ```
7. **Commit one entity**, with the movement in the message:
   ```
   refactor(pricing): settle CyclomaticStrict 14 -> 6

   Extracted goods_line, service_line and line_total; settle now dispatches.
   ```

Small commits are the deliverable, not a side effect. A reviewer must be able to read one
commit and see one routine get simpler.

## Phase 5 — Lock the gain in

Two commands write the baseline file and they are **not** interchangeable:

| Command | What it does to the baseline |
| --- | --- |
| `scitools-hook check --all` (with `adaptive = true`) | **Narrows only.** Lowers every recorded value this run beat; never raises one. |
| `scitools-hook baseline` | **Replaces the file.** Whatever the repository measures today becomes the baseline — including values that got *worse*. |

So during refinement, tighten by running the whole-project check:

```bash
scitools-hook check --all
git diff -- <the baseline file>
```

Every value that went **down** is a ceiling you just earned and can never lose by accident.

Run `baseline` again only deliberately, from a clean tree, and read the diff before staging
it. **A value that went up in that diff is a limit you just relaxed** — which is the one
thing this loop exists not to do. If you see one, throw the file away and investigate the
regression instead.

Commit the tightened baseline with the change that earned it, or on its own directly after.

---

## Rules this skill will not break

1. **Never relax a limit to make a finding go away.** Not in `scitools-hook.toml`, not in the
   baseline file, not by re-capturing.
2. **Never add an `[ignore]` pattern or a `[scope]` block to pass.** Both are legitimate
   operator decisions about what the tool should look at, and neither is yours to make while
   working through findings.
3. **Never acknowledge a parse error to clear it.** The hint names the construct to rewrite.
4. **Never touch another gate's state.** Most repositories that need this skill already carry
   other baselines and ratchets — a size baseline, an import-linter contract, a security
   scanner's accepted findings. Leave them alone: one blocking voice per question, and none of
   them is yours.
5. **Never report progress from a run that did not measure the code.** Exit 3, 4, 5 and 7 are
   infrastructure. They are not passes.

If you believe a limit is genuinely wrong for this repository, say so with the measurement and
stop. That is a human's decision, and making it silently is worse than leaving the finding.

## When you are stuck

Some routines cannot be simplified without a design change that is out of scope. Do not force
those. Record what you found — the entity, the metric, why the obvious extraction does not
work, what it would take — and move to the next one.

**A short, honest list of what you did not fix is worth more than a contorted change that
satisfies a number.** A refactor that hits the limit by shuffling complexity into three
badly-named helpers has made the repository worse while making the gate happier, and that is
the specific failure this skill exists to avoid.

## Common Rationalizations

| Rationalization | Reality |
| --- | --- |
| "`check --all` reports 1286 blocking, so the repository fails." | `--all` is an inventory with no before side. Commits are gated by `--staged`, where those are `pre-existing`. |
| "I'll re-run `baseline` to get a clean starting point." | `baseline` replaces the file and will happily record a value that got worse. During refinement, tighten with `check --all`. |
| "This limit is unrealistic for this codebase." | Maybe. Say so with the measurement and let a human decide. Do not edit it and report success. |
| "The whole file needs rewriting, so I'll do it in one commit." | Then nobody can review it, which is the problem you were sent to fix. One entity per commit. |
| "Extracting helpers will trip the file-level counts." | It will not. The counts a decomposition raises ship with the ratchet off. |
| "This file has no findings, so it is clean." | Not if it is named under `parse_errors`. It has no findings because it was never fully read. |
| "The gate passes, so the change is good." | The gate measures shape, not behaviour. Run the repository's tests. |
| "I improved the metric by moving the branches into a helper called `_helper2`." | The metric moved; the working set did not. Name what you extract after what it does, or the commit is a no-op with a green tick. |

## Output Format

Per commit:

```md
## Improvement
- ENTITY: <longname>  <path>:<line>
- RULE: <rule>
- BEFORE -> AFTER: <value> -> <value>  (limit <limit>)
- TESTS: <command run, and its result>
- COMMIT: <hash>
```

At the end of a session:

```md
## Session
- COMMITS: <n>, one entity each
- TIGHTENED: <rule: previous -> current, per baseline value that dropped>
- NOT FIXED: <entity, metric, and why -- one line each>
- FOR A HUMAN: <any limit you believe is wrong, with its measurement>
```

Report `NOT_MEASURED` rather than a result for any run that exited above 1.
