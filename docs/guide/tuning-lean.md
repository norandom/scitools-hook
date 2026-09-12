# Tuning duplicate and dead code

## The conclusion first

The lean-code family (`[lean]`) targets code that should not exist or should be shorter:
duplicate blocks, near-identical routines, dead parameters, unused classes and module variables,
forwarding wrappers, and single-implementation abstractions.

If you enabled these rules on your project and saw very few findings or none at all, two
mechanisms explain why:

1. **The duplication rules ship with conservative filters.** Exact duplicate blocks require
   at least 12 consecutive code lines (`duplicates_min_lines = 12`). Similar routines require
   at least 6 statements (`similar_min_statements = 6`) and 90% normalized token similarity
   (`similar_threshold = 0.9`). Furthermore, `scitools-hook check` evaluates findings against
   the **current change**; pre-existing duplicates in untouched files remain silent unless you
   audit with `--all`.
2. **The dead-code rules sit behind two strict safety floors.** `accuracy_floor = 0.75` and
   `resolution_floor = 0.75` require that Understand parsed at least 75% of files with zero
   errors/warnings and resolved at least 75% of call sites. On dynamic languages like Python,
   where call resolution is often 30% to 45%, the Gate prints `was not evaluated` once per run
   and **silences the dead-code rules completely**, because an incomplete call graph or
   parse-errored file would report working code as dead.

This page explains how these detection engines work, why they were designed this way, and how
to calibrate their thresholds and safety gates for your repository with measurement.

---

## Why the engines are engineered this way

### Understanding what SciTools provides

SciTools Understand is an AST indexer, symbol database, and metrics engine. It provides
low-level primitives:
- `ent.refs("callby, useby, setby, modifyby")`: who calls, reads, writes, or modifies an entity.
- `ent.lexer(False).lexemes()`: raw lexeme streams with token classes (`Identifier`, `Literal`,
  `Operator`, `Punctuation`, `Whitespace`, `Comment`).
- `ent.metric(["CountStmt", "CountLineCode"])`: statement and line counts.

Understand does **not** ship a high-level routine clone detection engine, a token sequence
similarity matcher, or a semantic dead-code classifier. CodeCheck is a separately licensed
product that this tool explicitly does not depend on. Therefore, all normalization, sequence
matching, connected-component clustering, and safety gating are implemented in pure Python in
the Gate.

### The quadratic explosion problem ($O(N^2)$)

Scoring similarity between every routine pair in a 2,500-routine project naively requires
evaluating nearly 3,000,000 pairs with sequence matchers. In pure Python, an unoptimized
search takes 15 to 30 minutes, which would freeze git hooks and CI.

[`structure.similar_routine`](../reference/rules.md#structuresimilar_routine) solves this with
two exact mathematical bounds and graph clustering:

1. **Length band rejection ($O(1)$)**: A pair whose lengths differ so much that
   `2 * min(len(a), len(b)) / (len(a) + len(b)) < threshold` can never match, regardless of
   tokens. This rejects ~70% of pairs instantly with two integer comparisons.
2. **Multiset overlap ceiling**: A common subsequence cannot use a token more often than
   either shape holds it. The multiset intersection of distinct token frequencies provides an
   exact ceiling on possible matches. This prunes over 99% of remaining candidates before
   `SequenceMatcher` runs.
3. **Graph connected components (BFS)**: Rather than reporting 66 separate pairwise warnings
   for 12 twin methods, matches are clustered into connected components (families) and reported
   once per family.
4. **Closure / nested span pruning**: Inner functions and closures naturally score ~0.95
   similarity against their enclosing routines. The engine prunes contained spans so parents
   and children are never reported as twins of one another.

### Why dead-code rules require safety floors

When a naive dead-code predicate ("if an entity has no project references, report it as unused")
was tested on real codebases during development, two failures occurred:

- **Structural typing & duck typing**: On a 417-file Python codebase (`facdrone`) resolving at
  26% call resolution, the naive rule flagged **830 routines** as dead. Nearly every one was
  working production code: CLI subcommands, plugin hooks, web framework routes, and duck-typed
  interface implementations that carry no inheritance references.
- **Parse error regions**: On this repository, Understand encountered syntax errors on 19% of
  files due to advanced typing syntax. For every module variable whose use site was inside a
  region Understand errored on, no reference was recorded, yielding a **100% false-positive
  rate** on module constants.

A dead-code rule that cannot tell "nothing uses this" from "the analyzer could not resolve what
uses this" is a machine for deleting working code. The two safety floors
(`accuracy_floor` and `resolution_floor`) exist to prevent that failure.

---

## Auditing your repository before changing limits

Before editing any setting in `scitools-hook.toml`, inspect what the engines see across your
whole repository.

### 1. Check analyzer accuracy and resolution

Run `doctor` to see what Understand measured on your codebase:

```bash
scitools-hook doctor
```

Look at two rows:
- `after accuracy`: the percentage of files parsed without warnings or errors.
- `Python call resolution` (or your project's language): internal call sites resolved into edges.

If either number is below `0.75` (75%), the dead-code rules (`unused_parameters`,
`unused_classes`, `unused_variables`) and `pass_through` will refuse to run on that code by
default.

### 2. Run a full-repository lean audit

To see duplication across the entire project rather than just your current git diff, run:

```bash
scitools-hook check --all --format json > /tmp/lean_audit.json
```

Filter for lean findings using `jq`:

```bash
jq -r '.findings[] | select(.kind=="structural" and (.rule | startswith("structure."))) | [.rule, .path, .line, .message] | @tsv' /tmp/lean_audit.json
```

If this returns nothing, your code is either cleaner than the shipped thresholds require, or
the thresholds are set higher than the patterns in your codebase.

---

## Calibrating duplicate and similar code detection

Both token rules (`duplicates` and `similar_routines`) make **presence claims** ("these lines
stand here, and also there"). They do not depend on call graphs or symbol resolution, and they
take no trust gate. They are the safest and most actionable rules to tune.

### Tuning duplicate blocks (`duplicates`)

The `structure.duplicate_block` rule searches for identical consecutive lines of code. It
strips comments and whitespace and hashes the remaining code lines.

In `scitools-hook.toml`:

```toml
[lean]
duplicates = "warning"
duplicates_min_lines = 8   # Default: 12
```

- **When to lower `duplicates_min_lines`**: If your project has small repeating logic blocks
  (6 to 10 lines) such as repetitive error handling, data mapping, or test setup routines.
- **When to raise `duplicates_min_lines`**: If the rule flags repetitive imports, table headers,
  or short boilerplate constructs.
- **Excluding regions with `duplicates_ignore`**: If certain directories repeat by design (such
  as test fixtures, mock servers, or transcribed protocol stubs), ignore them by glob:

```toml
[lean]
duplicates_ignore = ["tests/fixtures/**", "src/generated/**"]
```

### Tuning similar routines (`similar_routines`)

The `structure.similar_routine` rule normalizes identifiers (`ID`) and literals (`LIT`) and
compares routine token streams.

In `scitools-hook.toml`:

```toml
[lean]
similar_routines = "warning"
similar_threshold = 0.82       # Default: 0.90
similar_min_statements = 4     # Default: 6
similar_min_family = 2         # Default: 2
```

- **`similar_threshold`**:
  - `0.90` (default): Very strict. Finds routines that are essentially renamed twins with identical
    control flow and statements.
  - `0.80 - 0.85`: Surfaces genuine structural clones that differ by a few statements, helper
    calls, or error handlers (e.g. 10 scrapers, 8 API endpoint handlers, or 12 database
    transformers).
  - `< 0.75`: Not recommended. At 0.75, routines with similar structural scaffolding but
    unrelated semantic purposes begin clustering into false families.
- **`similar_min_statements`**:
  - Routines shorter than this threshold are ignored. Setting this to 4 allows catching small
    repeated helpers (e.g., coordinate transformers, date formatters).
- **`similar_name_ignore`**:
  - Ignore idiomatic routines that repeat by architectural design. The default list includes
    `__post_init__`, `__init__`, `setUp`, and `tearDown`. Add project-specific idioms:

```toml
[lean]
similar_name_ignore = ["(^|\\.)validate_.*", "(^|\\.)handle_.*"]
```

---

## Calibrating dead code and safety floors

The dead-code family (`unused_parameters`, `unused_classes`, `unused_variables`) and
`pass_through` sit behind [`resolution_floor` and `accuracy_floor`](configuration.md#the-lean-code-family-lean).

### When rules report "was not evaluated"

If you enable `unused_parameters = "warning"` and run a check, you may see:

```text
structure.unused_parameter was not evaluated for Python: 43% of this run's Python call
sites resolved to a project routine, below the call-resolution floor of 75%, so a finding
would report the analysis, not the code
```

This diagnostic confirms the rule was requested, but was silenced because Understand's call
graph is too sparse to distinguish an unused parameter from an unresolved callback or dynamic
dispatch.

### How to tune dead-code rules safely

If you want to evaluate dead-code rules on a codebase with partial resolution:

1. **Do not lower the reporting floor in `[analysis]`**: `[analysis] accuracy_floor` only
   prints a warning; `[lean] accuracy_floor` and `[lean] resolution_floor` control the gate.
2. **Experiment on a branch with lowered floors**:
   ```toml
   [lean]
   unused_parameters = "warning"
   resolution_floor = 0.40   # Lower from 0.75 to match your measured doctor resolution
   accuracy_floor = 0.20     # Lower from 0.75 only if parse errors are confined to known files
   ```
3. **Audit the first ten findings**:
   Run `scitools-hook check --all --format json` and inspect the findings:
   - Are they genuine dead parameters/variables that nothing uses?
   - Or are they pytest fixtures (parameters used for side-effects), `@overload` signatures, or
     framework callbacks?
4. **Use ignore lists for framework conventions**:
   ```toml
   [lean]
   unused_parameters_ignore = ["^fixture_", "^request$", "^_"]
   unused_variables_ignore = ["pytestmark", "^__all__$"]
   ```

---

## Calibrating layering rules

The layering rules detect structural indirection that adds complexity without adding behavior:

| Rule | Switch | Shipped Default | What it Detects |
| :--- | :--- | :--- | :--- |
| `structure.pass_through` | `pass_through` | `None` (`warning` when on) | A routine with 1 caller and $\le 2$ statements forwarding directly to 1 callee. |
| `structure.single_implementation` | `single_implementation` | `None` (`warning` when on) | An abstract/base class with exactly 1 derived implementation and no other references. |
| `structure.over_export` | `over_export` | `None` (`warning` when on) | A file defining 1 routine/class depended on by only 1 other file. |

### Tuning pass-through routines

Understand's `CountStmt` counts the `def` line as 1 statement, so `return callee(x)` scores 2
statements.
- `pass_through_max_statements = 2` (default): Catches exact one-line forwarding wrappers.
- `pass_through_max_statements = 3`: Catches forwarders that include a single guard clause or
  log call.

---

## Recommended configuration for typical projects

Here is an example configuration tuned for a dynamic Python repository that wants active
duplicate detection and cautious layering checks:

```toml
[lean]
# 1. Duplication rules: active and calibrated
duplicates = "warning"
duplicates_min_lines = 8
duplicates_ignore = ["tests/fixtures/**"]

similar_routines = "warning"
similar_threshold = 0.82
similar_min_statements = 5
similar_min_family = 2
similar_name_ignore = ["(^|\\.)test_.*", "(^|\\.)__post_init__"]

# 2. Layering rules: catch unnecessary wrappers
over_export = "warning"
pass_through = "warning"
single_implementation = "warning"

# 3. Dead-code rules: start with parameters, with fixture ignores
unused_parameters = "warning"
unused_parameters_ignore = ["^fixture_", "^request$", "^_"]

# 4. Net LLOC delta reporting
# net_delta prints automatically on any check with a before side
```

For agent guidance on how to fix findings once reported, see [Lean code](lean-code.md).
For rule descriptions and SARIF identifiers, see the [Rules reference](../reference/rules.md#the-lean-code-rules).
To automate this calibration process with an AI assistant or coding agent, install and run the
[`scitools-tune`](agents.md#scitools-tune) skill (`scitools-hook install-skills`).
