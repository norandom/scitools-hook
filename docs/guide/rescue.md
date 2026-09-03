# Rescuing a problematic project

## The conclusion first

You point this at your real repository, and the first run says something like:

```text
summary: 1286 errors, 600 warnings, 0 pre-existing, 1286 blocking
         | 23 files failed to parse, not fully checked
         | exit 1: blocking violations found
```

1286 blocking findings in 37.7 seconds, on 770 files. The reasonable reaction is that the
tool is unusable here.

It is a starting position, not a verdict. The worked example on this page took that same
repository to **0 blocking on a well-shaped commit**, with the existing debt still visible as
`pre-existing` and warnings, and a genuinely complex addition still refused by name and line.
It took five configuration decisions, each of which is recorded with its reason.

None of them was "turn the rule off".

## The first number is not a quality score

Read this before you read the findings.

On that 770-file repository, of 12 629 routines measured, **99.3% were already inside the
default `CyclomaticStrict` of 10**, and the p95 was **5**. The `CountPath` median was **1**
against a maximum of **955 514 880**.

The same shape is checkable on this repository, whose numbers you can reproduce:

```console
$ scitools-hook check --all
summary: 230 errors, 131 warnings, 0 pre-existing, 230 blocking
         | 1 file failed to parse, not fully checked
         | 1 metric unavailable, those limits were not evaluated
         | exit 1: blocking violations found
```

4232 routines. Median `CyclomaticStrict` **1**, p95 **4**, maximum **14**, and **99.9%**
inside the default limit. Where do 230 errors come from, then?

| Count | Rule |
| ---: | --- |
| 99 | `structure.new_dependencies` |
| 54 | `file.CountDeclFunction` |
| 19 | `file.CountLineCode` |
| 19 | `file.CountDeclClass` |
| 13 | `routine.CountParams` |
| 11 | `class.CountClassCoupled` |
| 3 | `routine.CyclomaticStrict` |

Three. Out of 230. And 143 of the 230 are in `tests/`, not in `src/`.

So the large first-run count comes from a long tail plus file-scope counts, not from a
codebase that is 230 kinds of bad. A reader who concludes otherwise has misread the output,
and **that is the tool's fault, not theirs** — a gate that presents an inventory in the same
shape as a verdict is inviting the mistake.

The five steps below are about making the output mean what it looks like it means.

## Step 1: run it, and expect a large number

```bash
scitools-hook check --all --show-highest
```

`--all` has no before side, so nothing is `pre-existing` and every finding is absolute. This
is the inventory. Do not treat it as a to-do list.

`--show-highest` is the useful half: it names the worst value found per metric with its
entity and line, which tells you where the tail actually is.

## Step 2: separate what belongs to another tool

That repository already ran a structural grader and an import-linter. Both answered questions
the gate also answers: module coupling, import direction, dependency cycles.

Everything those two already answered was **demoted to a warning**, not answered twice. One
blocking voice per question, and no second baseline to disagree with the first.

```toml
[structure]
# Answered by the existing structural grader; kept visible, not kept blocking.
new_dependencies_severity = "warning"
# Answered by the import-linter, which owns import direction in this repository.
file_cycles = "warning"
arch_cycles = "warning"

[thresholds.class]
# Same question, already graded elsewhere. Keep the limit; drop the refusal.
CountClassCoupled = { max = 12, severity = "warning" }
```

!!! note "There is no `[severity]` table"

    A severity is set where the rule is declared, not in a central map. Measured — a
    top-level `[severity]` table is rejected at load:

    ```console
    $ scitools-hook config
    error: severity: Extra inputs are not permitted
      file: /path/to/scitools-hook.toml
    ```

    | Rule family | Where its severity lives |
    | --- | --- |
    | thresholds | inline in the table: `Metric = { max = 25, severity = "warning" }` |
    | file and architecture cycles | `structure.file_cycles`, `structure.arch_cycles` |
    | new dependencies | `structure.new_dependencies_severity` |
    | fan-in and fan-out | `structure.fan_severity` |
    | layer and coupling rules | `severity` inside each `[[structure.layers]]` / `[[structure.coupling]]` |
    | CodeCheck | `codecheck.severity` |
    | `analysis.parse_error` | **not configurable.** Use `[[parse.acknowledged]]`, which needs a written reason. |

    A threshold table accepts exactly four keys — `max`, `min`, `ratchet`, `severity` — and
    rejects anything else by name:

    ```console
    error: thresholds.file.CountDeclFunction: unknown keys enabled;
           allowed: max, min, ratchet, severity
    ```

**Demoted, not excluded.** The finding stays in the report with its entity, its line and its
hint. What it loses is the ability to refuse a commit, because another tool already refuses
that commit and two gates on one question is how a team ends up with two disagreeing
baselines.

That step alone took the repository from **1286 blocking to 591**.

!!! warning "A blanket `tests/**` ignore was refused, for the same reason"

    It was the obvious next move and it was the wrong one. `[ignore] files = ['^tests/']`
    would have taken the number down further, and it would also have hidden a **2598-line
    test module** that genuinely needs splitting.

    An ignore says "do not look". A severity says "look, and let somebody else decide". On a
    directory you have not read, the second is the honest one.

    If tests genuinely need different numbers rather than no numbers, that is a
    [path scope](configuration.md#different-limits-for-different-directories), which changes
    the limits and keeps the file in the analysis.

## Step 3: exclude what is not source

This is the one place exclusion is right, because the files are not code you wrote and never
were.

```toml
[project]
exclude = [
    # keep the shipped defaults you want
    ".git/**", "node_modules/**", ".venv/**", "build/**", "dist/**", "__pycache__/**",
    "*.min.js", "*.generated.*", "*.lock",
    # a vendored SDK, declared linguist-generated in .gitattributes
    "vendor/**",
    # generated migrations
    "**/migrations/*.py",
    # rendering assets the analyser reads as Web source
    "**/*.css", "**/*.html", "static/**",
]
```

Three categories, each with evidence rather than judgement:

- **Vendored SDKs**, identified from the repository's own `.gitattributes`
  `linguist-generated` declaration. If the repository already says a directory is not
  hand-written, that is the evidence.
- **Generated migrations**, which nobody edits and whose shape is the generator's.
- **Rendering assets**, which Understand enrols as `Web` *source* and then judges by
  file-scope rules. See
  [Languages](languages.md#web-is-one-language-and-it-will-enrol-your-assets-as-source).

`scitools-hook init --detect` proposes these, with the evidence for each, rather than asking
you to guess:

```bash
scitools-hook init --detect --print
```

Lists replace rather than merge, so repeat the shipped defaults you want to keep.

## Step 4: acknowledge what the analyser cannot read

On that repository, **18 files used Python 3.12 syntax that Understand 6.5 cannot parse** —
PEP 695 type parameters — and the repository's own linter mandates that syntax. So those
files would fail to parse forever, and every commit touching one would be blocked forever.

```toml
[[parse.acknowledged]]
paths = ["src/api/generic.py", "src/api/registry.py"]
reason = "PEP 695 type parameters; Understand 6.5 stops at the declaration. ruff UP047 mandates this syntax here."
```

The reason is required, and it is what the report quotes.

!!! danger "An acknowledged file is not 'checked and clean'"

    It is checked **up to the construct that stopped the parse**, and the report says exactly
    that on every run:

    ```console
    $ scitools-hook check --staged
    pricing/generic.py
      error    analysis.parse_error  line 4
        Understand could not read pricing/generic.py: 6 parse errors, the first at line 4:
        expected token '(' at token [. ... -- acknowledged: PEP 695 type parameters;
        Understand 6.5 stops at the declaration.; the file is measured only up to the
        construct that stopped the parse; nothing after it was read

    summary: 1 error, 0 warnings, 0 pre-existing, 0 blocking | 1 file failed to parse, not fully checked | exit 0: no blocking violations
    ```

    `1 error ... 0 blocking`, and the run still reports `1 file failed to parse, not fully
    checked`. The finding keeps its `error` severity and its place in the report. All the
    acknowledgement removes is the ability to refuse the commit.

    The alternative, where the syntax is yours to change, is to write the older spelling. This
    repository does exactly that, and puts `UP040`, `UP046` and `UP047` in its `ruff` ignore
    list with the measurement in a comment. See
    [What this adds beyond ruff and mypy](../compare/linters.md#the-one-that-matters-ruff-fix-can-blind-the-analyser).

## Step 5: fix what is wrong for a gate rather than wrong for the code

One rule on that repository blocked every commit, and it was not about the commit.

Adding a **three-line function** was refused by `project.MaxCyclomaticStrict` at 3.0x the
limit. That number describes the worst routine anywhere in the project. The commit neither
caused it nor could fix it, and no edit to those three lines would ever clear it.

```toml
# Project-scope rules describe the codebase, not the change. A commit cannot answer them,
# so they report and do not refuse. The limits stay; only the refusal goes.
[thresholds.project]
MaxCyclomaticStrict     = { max = 15, severity = "warning" }
MaxNesting              = { max = 5,  severity = "warning" }
"AVG:CyclomaticStrict"  = { max = 3,  severity = "warning" }
"AVG:CountLineCode"     = { max = 30, severity = "warning" }
```

!!! warning "`AVG:CountLineCode` is doing a job before you demote it"

    It is the backstop for
    [growth inside a limit](../argument/ratchet.md#what-that-trade-gives-up-stated-rather-than-hidden):
    a routine may creep from 29 lines towards 60 one commit at a time without a refusal, and
    the project mean at 30 is what catches that drift. Demote it only if something else in
    your stack watches routine length, and say so in the comment.

This is the general rule, and it is worth stating as one: **a rule that a single commit cannot
act on should not be able to refuse a single commit.** Project-scope thresholds are reduced
over the whole population; they are a dashboard number wearing a gate's clothes. Keep them,
watch them, and let per-entity rules decide commits.

The effect is visible at any scale. A three-file demo repository with one 12-branch routine
in it reports `project AVG:CyclomaticStrict is 4.66667, which exceeds the maximum 3` and
exits 1 on every commit, until enough ordinary routines exist to move the mean — which is a
statement about the demo's size, not about the commit being made.

## Step 6: capture a baseline and let the ratchet carry the rest

Everything left is real, per-entity, and in code somebody wrote. You do not fix it now.

```bash
scitools-hook baseline          # record the worst current value per ratcheted rule
scitools-hook install-hook
```

From here the ratchet does the work. Debt reports as `pre-existing` on every commit that
touches its file, with its hint, and does not block. What blocks is a change that makes a
touched entity worse.

Optionally, with `baseline.adaptive = true`, the effective limit becomes
`min(configured, baseline)` so the numbers descend as the code improves rather than sitting
at the shipped default. A baseline can only ever narrow a limit, never widen it.

## The end state

On that 770-file repository, after those five decisions:

- **A well-shaped commit: 0 blocking.**
- Existing debt: still reported, as `pre-existing` and as warnings, on every run that touches
  it.
- A genuinely complex addition: still refused, by rule, entity and line.

That is the shape you are aiming for. Not zero findings. Zero *blocking* findings on work
that did not make anything worse.

## Two things to check before you start

### The ratchet already handles growth inside a limit

A rescue means editing exactly the files that carry the debt, so the obvious worry is that
every such edit is refused for moving a number. It is not. A ratchet finding on an entity
that is still inside its own limit after the change is reported as a **warning**:

```text
warning  routine.CountLineCode  legacy.report.render  line 4  worse than before, was 30
  routine legacy.report.render CountLineCode rose from 30 to 33, still within the maximum 60
```

What blocks is growth that crosses a limit, or growth on an entity already over one. If you
want the stricter behaviour on a repository that is already clean, `[ratchet]
below_limit_severity = "error"` buys it back exactly. See
[growth inside the limit](../argument/ratchet.md#growth-inside-the-limit-is-reported-not-refused).

If a specific size count should not be compared against `HEAD` at all, drop its ratchet and
keep its absolute limit:

```toml
[thresholds.routine]
CountLineCode = { max = 60, ratchet = false }
```

### Write the reason down, every time

Every configuration key above is a decision, and a configuration file full of undocumented
overrides is indistinguishable from one where somebody turned off whatever was inconvenient.

This repository's own `scitools-hook.toml` carries **only** the keys that deviate from the
defaults, each with the measurement behind it in a comment, so the file reads as a list of
decisions. Two deviations were later removed when re-measurement showed the reason for them
had gone.

Do the same, and a year from now somebody can tell which of your overrides are still true.

## The order, as a checklist

```text
1. scitools-hook check --all --show-highest      # the inventory; expect a large number
2. severity = "warning"  on what another tool owns  # not [ignore] -- demote, keep visible
3. [project] exclude   what is not source        # init --detect proposes these with evidence
4. [[parse.acknowledged]]  what cannot be read   # with a written reason; never "clean"
5. severity = "warning"  on project-scope rules     # a commit cannot act on them
6. scitools-hook baseline && install-hook        # the ratchet carries the rest
```
