---
name: scitools-onboard
description: Enable scitools-hook on a repository for the first time, deriving its limits from what the repository measures rather than from assumptions. Use when setting the gate up on a project that has never had it, when asked to configure it from scratch, or when an existing configuration was assembled by guesswork and should be re-derived from evidence.
allowed-tools: Read, Edit, Write, Bash, Grep, Glob
argument-hint: [check | detect | measure | apply]
---

# scitools-onboard

## Overview

Enabling this gate is eight commands, and the order matters because each one answers a
question the next one needs. Done wrong it produces a configuration full of exemptions
inherited from somebody else's codebase; done right it produces one where **every line that
deviates from a default carries the measurement that justifies it**.

The shipped defaults are a starting position, not a verdict. Some of them will fit your
repository and some will not, and the difference is measurable — so measure it, once, at the
start, instead of discovering it one blocked commit at a time.

## When to Use

- A repository that has never had the gate.
- A configuration somebody assembled by reacting to findings, which should be re-derived.
- After a large restructuring, when the shape the limits were derived from has changed.

Do **not** use it to fix a single blocked commit — that is `/scitools-adapt`, and it works
one decision at a time. This is the one-time act of deciding what this repository is.

---

## Stop here if the codebase is still moving

**Do not derive limits from a repository you are in the middle of reshaping.** `recommend`
measures the shape a project *has*, so running it mid-cleanup bakes in the shape somebody is
working to change. The metric most affected is `file.CountDeclFunction`, which rises every
time a long routine becomes several named helpers — exactly what a routine-level cleanup
does.

If a complexity cleanup is in flight, do steps 1 and 2 and stop. Come back for the
measurement when the splits stop. A limit derived from a transitional state is one the
project will argue with later.

## Step 0 — can this run at all?

```bash
scitools-hook doctor
```

Read `license`, `api mode` and `analysis python`. Only exit codes 0 and 1 are statements
about code; anything above means nothing was measured. If Understand is not installed, stop
and say so — everything below produces confident nonsense without it.

Also check the tree is clean. This writes files and captures a baseline.

## Step 1 — find out what this repository is

```bash
scitools-hook init --detect --print
```

`--detect` classifies the tree from what it *declares about itself* — which directories are
tests, which are generated or vendored, which files the analyser cannot read — and prints the
evidence beside each line. **Read the evidence.** A directory proposed for exclusion that you
know is hand-written source is a detection bug worth reporting, not a line to accept.

When it looks right:

```bash
scitools-hook init --detect
```

## Step 2 — the hooks and the agent's copy of the rules

```bash
scitools-hook install-hook --pre-push         # the push boundary: one range check per push
scitools-hook install-hook                    # the commit boundary, optional: every commit
scitools-hook agent-rules --write AGENTS.md   # the limits where your agent already reads
scitools-hook install-skills                  # gate, improve, adapt, and this one
```

Pre-push is the lighter gate and the one this project's own repositories run: commits stay
cheap and the range is judged once, when it leaves the machine. Pre-commit judges every commit
and is the right choice when commits are what gets reviewed.

Do this **before** the measurement, not after: the gate is useful from the first commit even
on shipped defaults, and a repository is not "enabled" until a commit actually meets it.

## Step 3 — measure, and change only what the measurement says

```bash
scitools-hook recommend            # the evidence, per ceiling
scitools-hook recommend --toml     # only the lines to paste
```

For every configured ceiling it reports how much of the population is already inside it, what
each candidate limit would cost, and who the worst offenders are. It writes nothing.

**Paste a line only when the report proposes it.** A ceiling reported `keep` fits this
repository; changing it because a finding annoyed you is the failure this whole step exists
to prevent. Copy the measurement into a comment above each line you take:

```toml
[thresholds.file]
# 210 files: p50 15, p95 80, max 321; 69 outside (32.9%) at 25, 10 outside (4.8%) at 80
CountDeclFunction = { max = 80, ratchet = false }
```

Two readings that decide what you do next:

- **A ceiling most of the repository fails is not a limit, it is noise.** A third of files
  outside `CountDeclFunction = 25` means the default is wrong here, not that the codebase is.
  Raise it to what the report proposes.
- **A ceiling the repository fits, with a handful of entities outside it, is working.** Those
  are outliers. Leave the limit alone; the ratchet will hold them as `pre-existing` and block
  only when one gets worse. **Do not draw a path scope around them** — scattered outliers do
  not cluster, and a scope drawn round the three that happened to block you is a scope with a
  worse reason than none.

If the routine limits and the file limits disagree — and they will, because every routine
hint asks for extraction and extraction raises the file counts — **the file-level one
yields**. A file of twelve small named helpers is the outcome the routine limits are asking
for.

## Step 4 — record where you are

```bash
scitools-hook baseline
```

Then turn the ratchet's memory on:

```toml
[baseline]
adaptive = true
```

Off, the baseline is a fixed floor. On, the effective limit is `min(configured, recorded)`,
and every whole-project run lowers a recorded value the code has beaten. **That is the
invariant that makes onboarding safe to do once:** limits are derived from evidence at the
start, and from then on they can only narrow. Nothing in this procedure ever loosens a limit
again on its own.

## Step 5 — prove it

```bash
scitools-hook check --all --show-highest    # the inventory: expect a large number
scitools-hook check --staged                # what a commit will meet: expect 0 blocking
```

Those two numbers are different on purpose and confusing them is the most common first-day
mistake. `--all` has no before side, so every violation is absolute and nothing is
`pre-existing`; a large count there is a starting position. `--staged` is the gate, and on a
freshly onboarded repository with a baseline it should be **0 blocking**.

If `--staged` blocks on a trivial change, something above is wrong — stop and find it rather
than adding exemptions.

## Step 6 — commit the decisions on their own

```bash
git add scitools-hook.toml scitools-hook.baseline.json AGENTS.md .agents/
git commit -m "chore(gate): enable scitools-hook, limits derived from measurement"
```

Configuration and baseline in one commit that changes no code, so the first behavioural commit
has a clean before side.

---

## What onboarding must never do

| Move | Why not |
| --- | --- |
| Exempt before measuring | Every exemption written on the first day is a guess. Measure, then exempt only what the measurement cannot explain. |
| Paste a line the report says `keep` | The report is the evidence. Overruling it silently makes the configuration a preference again. |
| Derive limits mid-cleanup | You bake in the shape somebody is working to change. |
| Add `[ignore]` | It removes entities from judgement entirely. A `[scope]` keeps them measured against different numbers; that is almost always what was meant. |
| Acknowledge a parse error to make it quiet | The file is measured only up to the construct that stopped the parse. `reason` is required and is quoted in the report. |
| Skip the baseline | Without it every pre-existing violation is a blocking one, and the first commit meets all of them at once. |

## Output Format

```md
## Onboarding
- DOCTOR: <license / api mode / analysis python, or the reason to stop>
- DETECTED: <scopes, exclusions and parse acknowledgements, with what the evidence was>
- MEASURED: <routines, classes, files; which ceilings the report proposed moving>
- CHANGED: <one line per deviation, each with its measurement>
- KEPT: <ceilings reported `keep` that were left alone -- name them, it is the point>
- BASELINE: <limits recorded, and whether adaptive is on>
- VERIFIED: <check --all inventory; check --staged blocking count>
- LEFT FOR A HUMAN: <anything the evidence did not settle>
```

Report `NOT_MEASURED` rather than a result for any run that exited above 1.
