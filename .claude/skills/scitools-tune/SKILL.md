---
name: scitools-tune
description: Tune and calibrate scitools-hook's lean-code rules ([lean]) for a repository, including duplicate blocks, similar routines, dead code, and layering. Use when lean detection reports too few or too many findings, when duplicate code was missed, when dead-code rules were silenced by safety floors, or when calibrating duplication thresholds and ignore lists.
allowed-tools: Read, Edit, Write, Bash, Grep, Glob
argument-hint: [diagnose | audit | calibrate | apply]
---

# scitools-tune

## Overview

The lean-code family (`[lean]`) targets code that should not exist or should be shorter:
`structure.duplicate_block`, `structure.similar_routine`, `structure.unused_parameter`,
`structure.unused_class`, `structure.unused_variable`, `structure.pass_through`,
`structure.single_implementation`, and `structure.over_export`.

The rules ship disabled (`None`) and with conservative defaults. If a repository has
duplicated code or dead parameters that were not reported, it is usually because:
1. `check` was run on a git diff rather than the whole project (`--all`).
2. Duplicate blocks were under 12 code lines (`duplicates_min_lines = 12`).
3. Similar routines had under 90% token similarity (`similar_threshold = 0.90`) or fewer than 6 statements (`similar_min_statements = 6`).
4. Dead-code rules were silenced by the safety floors (`resolution_floor = 0.75`, `accuracy_floor = 0.75`), which printed `was not evaluated` to avoid false positives.

This skill guides you through diagnosing your project's substrate, auditing whole-repository duplication, and calibrating the thresholds with measurement.

## When to Use

- A project has duplicate code or boilerplate twins that `scitools-hook` did not detect.
- A dead-code rule (`unused_parameters`, `unused_variables`) output `was not evaluated`.
- You want to turn on lean-code rules for the first time and tune their sensitivity.
- Duplication rules report too much noise (e.g. framework boilerplate or generated files).

---

## Step 1 — Diagnose the substrate

Run `doctor` to see what SciTools Understand can measure on this repository:

```bash
scitools-hook doctor
```

Read two specific rows:
- `after accuracy`: the percentage of files parsed without syntax errors or warnings.
- `<Language> call resolution`: the share of call sites resolved to project routines.

**The safety rule for dead code**:
If either number is below `0.75` (75%), the dead-code rules (`unused_parameters`, `unused_classes`,
`unused_variables`, `pass_through`) refuse to evaluate by design. Do not lower floors blindly:
dynamic typing and parse errors make active code appear unreferenced.

The two duplication rules (`structure.duplicate_block` and `structure.similar_routine`) do **not**
read the call graph and take no trust gate. They can always be calibrated safely.

---

## Step 2 — Audit whole-repository duplication

A regular `scitools-hook check` only inspects files touched in the current commit or staging area.
To see what duplication exists across the entire codebase, run a whole-project audit:

```bash
scitools-hook check --all --format json > /tmp/lean_audit.json
```

Count the findings by rule:

```bash
jq -r '.findings[] | select(.kind=="structural" and (.rule | startswith("structure."))) | .rule' /tmp/lean_audit.json | sort | uniq -c
```

If `structure.duplicate_block` and `structure.similar_routine` report zero findings, the defaults
are tighter than the duplicates in your codebase.

---

## Step 3 — Calibrate duplicate blocks (`duplicates`)

`structure.duplicate_block` compares exact hashes of consecutive code lines (ignoring whitespace
and comments).

In `scitools-hook.toml`:

```toml
[lean]
duplicates = "warning"
duplicates_min_lines = 8   # Default: 12
```

1. **Lower `duplicates_min_lines`** to `8` or `6` if your code has smaller copied blocks
   (e.g., repeating validation logic, dispatch blocks, error handling).
2. Re-run `check --all` and sample the first ten findings:
   - Are they genuine repeated blocks that should be extracted into a shared helper?
   - Or are they boilerplate import lists or repetitive table definitions?
3. **Add path ignores for intentional copies**:
   If test fixtures or generated files repeat by design, add them to `duplicates_ignore`:
   ```toml
   duplicates_ignore = ["tests/fixtures/**", "src/generated/**"]
   ```

---

## Step 4 — Calibrate similar routines (`similar_routines`)

`structure.similar_routine` normalizes identifiers (`ID`) and literals (`LIT`) and clusters
routines into connected families.

In `scitools-hook.toml`:

```toml
[lean]
similar_routines = "warning"
similar_threshold = 0.82       # Default: 0.90
similar_min_statements = 4     # Default: 6
similar_min_family = 2         # Default: 2
```

1. **Lower `similar_threshold`** from `0.90` to `0.80`–`0.85`:
   - `0.90` requires almost verbatim structure.
   - `0.82` surfaces twin routines with minor variations (e.g. data transformers, endpoint handlers).
2. **Lower `similar_min_statements`** from `6` to `4` or `5` if you have short duplicated helpers.
3. **Exclude framework idioms** with `similar_name_ignore`:
   Routines that repeat intentionally because of framework conventions (e.g., dataclass
   `__post_init__`, Pydantic validators, pytest hooks) should not be merged:
   ```toml
   similar_name_ignore = ["(^|\\.)validate_.*", "(^|\\.)__post_init__"]
   ```

---

## Step 5 — Calibrate dead code and safety floors

If you want to detect unused parameters or variables in a dynamic language (e.g. Python):

1. Inspect your measured `doctor` resolution (e.g. 43% resolution, 20% accuracy).
2. If you decide to test dead code detection on a development branch:
   ```toml
   [lean]
   unused_parameters = "warning"
   resolution_floor = 0.40   # Set slightly below your measured call resolution
   accuracy_floor = 0.15     # Set slightly below your measured accuracy
   ```
3. Run `scitools-hook check --all --format json > /tmp/dead_audit.json` and inspect the findings.
4. Filter out expected noise (e.g., pytest fixture parameters):
   ```toml
   unused_parameters_ignore = ["^fixture_", "^request$", "^_"]
   unused_variables_ignore = ["pytestmark", "^__all__$"]
   ```

---

## Step 6 — Write the measurements down

Every setting you add to `scitools-hook.toml` must carry a comment recording what it measured
before and after:

```toml
[lean]
# 270 files: 0 findings at min_lines 12; 18 findings (14 genuine) at min_lines 8
duplicates = "warning"
duplicates_min_lines = 8
duplicates_ignore = ["tests/fixtures/**"]

# 1940 routines: 10 families at 0.90; 42 families at 0.82 (first ten all genuine twins)
similar_routines = "warning"
similar_threshold = 0.82
similar_min_statements = 4
similar_name_ignore = ["(^|\\.)test_.*", "(^|\\.)__post_init__"]
```

Verify with a clean check:

```bash
scitools-hook check --all
```

Watch the net logical-lines line (`net: ±N lloc`) when simplifying and merging duplicate
routines.
