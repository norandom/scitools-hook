# The ratchet

## The conclusion first

A finding that was already there before your change is reported as `pre-existing` and does
not block. A finding your change created, or a value your change made **worse**, blocks. That
is the whole rule, and it is what makes a complexity gate adoptable on a repository that has
never had one: you are not asked to pay down the debt you already have.

"Worse" is measured per entity against `HEAD`, not against the limit: a value that moved in
the wrong direction is a finding even when it is well inside its limit. Whether that finding
*blocks* is a second question, and the answer is no while the entity is still inside its
limit after the change. Both halves matter, and
[the section on `below_limit_severity`](#growth-inside-the-limit-is-reported-not-refused) is
the one to read before you turn this on anywhere.

## Watch it work

Three commits in one small repository. Everything below is real terminal output from
`scitools-hook 0.1.0a1` against Understand 6.5.1204. The demo repository is built in the
[quickstart](../guide/quickstart.md), if you want to reproduce it.

### Act 1: debt arrives

`legacy/report.py` is imported from an old repository. It has a 12-branch routine in it.
Nobody is going to rewrite it today.

```console
$ scitools-hook check --all
legacy/report.py
  error    routine.CyclomaticModified  legacy.report.render  line 4  1.5x limit
    routine legacy.report.render CyclomaticModified is 12, which exceeds the maximum 8
  error    routine.MaxNesting  legacy.report.render  line 4  1.3x limit
    routine legacy.report.render MaxNesting is 4, which exceeds the maximum 3
  error    file.MaxCyclomaticStrict  1.2x limit
    file legacy/report.py MaxCyclomaticStrict is 12, which exceeds the maximum 10
  error    routine.CyclomaticStrict  legacy.report.render  line 4  1.2x limit
    routine legacy.report.render CyclomaticStrict is 12, which exceeds the maximum 10

summary: 4 errors, 3 warnings, 0 pre-existing, 4 blocking | exit 1: blocking violations found
```

`check --all` is an inventory of the whole project, so everything is a finding and nothing is
`pre-existing` — there is no before state to compare against. This is the number you would
have to "pay down" if the gate worked the way a linter works.

It does not. A commit is not gated by `--all`.

### Act 2: touch the debt, change nothing about its shape

One line inside `render`, improving a warning message:

```diff
-            sink.warn("unknown row")
+            sink.warn(f"unknown row kind: {row.kind}")
```

```console
$ scitools-hook check --staged
legacy/report.py
  error    routine.CyclomaticModified  legacy.report.render  line 4  1.5x limit, was 12, pre-existing
    routine legacy.report.render CyclomaticModified is 12, which exceeds the maximum 8
    hint: collapse the case arms into a lookup table or a polymorphic call and leave only the dispatch in this routine
  error    routine.MaxNesting  legacy.report.render  line 4  1.3x limit, was 4, pre-existing
    routine legacy.report.render MaxNesting is 4, which exceeds the maximum 3
    hint: extract the inner block into its own routine, or invert the condition and return early so the body stops nesting
  error    file.MaxCyclomaticStrict  1.2x limit, was 12, pre-existing
    file legacy/report.py MaxCyclomaticStrict is 12, which exceeds the maximum 10
    hint: the most complex routine in this file is over the limit: simplify that routine first by extracting its branches into named routines
  error    routine.CyclomaticStrict  legacy.report.render  line 4  1.2x limit, was 12, pre-existing
    routine legacy.report.render CyclomaticStrict is 12, which exceeds the maximum 10
    hint: too many decision points in one routine: extract each group of related decisions into its own named routine, and replace boolean flag parameters with separate routines
  warning  file.RatioCommentToCode  0.3x limit, was 0.03, pre-existing
    file legacy/report.py RatioCommentToCode is 0.03, which is below the minimum 0.1
    hint: too little explanation: state at the top of the module, and on each exported routine, why it exists -- not what the code already says

summary: 4 errors, 1 warning, 5 pre-existing, 0 blocking | exit 0: no blocking violations
```

Exit 0. The commit goes through.

Four errors are still printed, each with the value it had before (`was 12`) and each labelled
`pre-existing`. Nothing is hidden and nothing is suppressed. The debt is visible on every run
that touches the file, with a hint saying what to do about it, and it stops there.

### Act 3: make it worse by one branch

An agent is asked to add fee waivers, and does the obvious thing:

```diff
         elif row.kind == "fee":
-            total += row.value
+            if flags.waive_fees:
+                total += 0
+            else:
+                total += row.value
```

```console
$ scitools-hook check --staged
legacy/report.py
  error    routine.CyclomaticModified  legacy.report.render  line 4  1.6x limit, was 12
    routine legacy.report.render CyclomaticModified is 13, which exceeds the maximum 8
  error    routine.MaxNesting  legacy.report.render  line 4  1.3x limit, was 4, pre-existing
    routine legacy.report.render MaxNesting is 4, which exceeds the maximum 3
  error    file.MaxCyclomaticStrict  1.3x limit, was 12
    file legacy/report.py MaxCyclomaticStrict is 13, which exceeds the maximum 10
  error    routine.CyclomaticStrict  legacy.report.render  line 4  1.3x limit, was 12
    routine legacy.report.render CyclomaticStrict is 13, which exceeds the maximum 10
  error    file.MaxCyclomaticStrict  worse than before, was 12
    file legacy/report.py MaxCyclomaticStrict rose from 12 to 13; an affected entity may not get worse than it was
  error    routine.CyclomaticModified  legacy.report.render  line 4  worse than before, was 12
    routine legacy.report.render CyclomaticModified rose from 12 to 13; an affected entity may not get worse than it was
  error    routine.CyclomaticStrict  legacy.report.render  line 4  worse than before, was 12
    routine legacy.report.render CyclomaticStrict rose from 12 to 13; an affected entity may not get worse than it was
  warning  file.RatioCommentToCode  0.3x limit, was 0.03, pre-existing
    file legacy/report.py RatioCommentToCode is 0.03, which is below the minimum 0.1
  warning  routine.CountLineCode  legacy.report.render  line 4  worse than before, was 30
    routine legacy.report.render CountLineCode rose from 30 to 33, still within the maximum 60
  warning  routine.CountPath  legacy.report.render  line 4  worse than before, was 30
    routine legacy.report.render CountPath rose from 30 to 33, still within the maximum 100
  warning  routine.CountStmt  legacy.report.render  line 4  worse than before, was 22
    routine legacy.report.render CountStmt rose from 22 to 24, still within the maximum 40

summary: 7 errors, 4 warnings, 2 pre-existing, 6 blocking | exit 1: blocking violations found
```

Exit 1. Read the difference between Act 2 and Act 3 carefully, because all three of the
mechanism's behaviours are visible in that one output:

- **`CyclomaticStrict` lost its `pre-existing` label** the moment it moved from 12 to 13, and
  produced a *second* finding that names the movement rather than the limit. It was already
  over its limit and it got worse, so it blocks.
- **`MaxNesting` is still `pre-existing`**, because the new branch did not deepen the nesting.
  It does not block.
- **The three size counts are warnings**, not errors: `CountLineCode` 30 → 33 against a
  maximum of 60, `CountPath` 30 → 33 of 100, `CountStmt` 22 → 24 of 40. They grew, they are
  reported with the limit they are still inside, and they do not block. That is
  [`below_limit_severity`](#growth-inside-the-limit-is-reported-not-refused), and it is the
  difference between a gate a team keeps and one they turn off.

The same file. The same untouched debt. One branch is the difference between exit 0 and
exit 1, and the report says exactly which numbers caused it.

## The mechanics

### Two databases, one commit

A staged run builds two Understand databases: `before`, from `HEAD`, and `after`, from the
index. Both live outside the working tree in a per-repository cache directory and are
analysed incrementally. Nothing is written into your repository.

The comparison is per entity, not per file and not per project. The entity identity is an
`EntityKey`: scope, repository-relative path, Understand's qualified long name, the parameter
list, and an ordinal tie-break. That is the only identity that survives across two separate
Understand databases.

### Four statuses

| Status | Meaning | Blocks? |
| --- | --- | --- |
| new | The entity did not exist before the change. Judged by the absolute limits alone; there is nothing to ratchet against. | Yes, if it breaks a limit. |
| regression | The value moved in the worse direction, whether or not it is inside the limit. | Only if the entity is **outside** its limit after the change. Inside it, the finding is demoted to a warning by [`below_limit_severity`](#growth-inside-the-limit-is-reported-not-refused). |
| pre-existing | The before value already broke the same limit, and the value did not get worse. | No, unless `ratchet.strict = true`. |
| deleted | The entity is gone. Nothing to compare. | No. |

"Worse" means higher for a `max` limit and lower for a `min` one. The comparison is strict:
a value that did not move produces no ratchet finding.

Only a rule whose severity is `error` can ever block. Warnings are printed and counted and
never block, in any mode — and a ratchet finding on an entity still inside its limit is one
of those warnings.

### Rename and move

A rename changes the entity's path, so its key changes. The entity reads as one removed plus
one added, which makes it *new*: judged by the absolute limits, never ratcheted. Moving a
routine to a different file is the same case.

This is a deliberate choice rather than an oversight. Pairing an added entity with a removed
one across files would be a guess, and the project's rule for guesses is stated in the
ratchet's own source: **a guess may add a finding; it may not excuse one.**

### Signature changes are paired, carefully

Within one file and one long name, a key that exists only in `after` is paired with a key
that exists only in `before` when there is exactly one of each. That is what stops a routine
escaping the ratchet by gaining a parameter.

The measurement that produced this rule, from `analysis/ratchet.py`:

> Measured through the installed CLI, one repository, two runs whose sources differ in
> nothing but the parameter list: the routine that grew with its signature untouched drew
> the whole set of routine-scope ratchet findings, `routine deep.walk CountLineCode rose from
> 6 to 10` among them; the same growth with three parameters added drew **none at routine
> scope**.

Where the evidence does not settle it, nothing is paired: two signatures changing at once in
the same family is two added and two removed, which no evidence can resolve, so both read as
new.

### The ratchet does not refuse the refactoring it just asked for

This is the failure mode a naive ratchet has, and it is worth understanding before you turn
one on anywhere.

The gate's hint for `routine.MaxNesting` says *"extract the inner block into its own
routine"*. Doing that raises `file.CountDeclFunction` and `file.CountLineCode` on the
container the routine came out of. A ratchet on those counts would refuse the fix, and the
cheapest way past the refusal is to undo the extraction.

Eight rules therefore ship with the ratchet **off** while keeping their absolute limits:

```text
file.CountDeclFunction     class.CountDeclMethod
file.CountDeclClass        class.CountDeclMethodNonStub
file.CountLineCode         class.CountDeclInstanceVariable
                           class.CountClassCoupled
                           class.CountClassDerived
```

Measured, from `config/models.py`: extracting two helpers out of a six-deep routine moved
`file.CountDeclFunction` 1 &rarr; 3 and `file.CountLineCode` 10 &rarr; 18, while every routine
metric of the routine that was split fell. Extracting two methods inside a class moved
`class.CountDeclMethod` and `class.CountDeclMethodNonStub` 2 &rarr; 4.

The dividing line is whether the entity being judged can show the improvement. An extracted
routine did not exist before, so it has no pre-change value and the absolute limits judge it
alone. The container it came out of has nothing left to show but the extra declaration.
Where the improvement *is* visible on the same entity — a routine flattened in place — the
ratchet stays on.

The absolute limits are untouched by this. A file with 40 functions still fails
`file.CountDeclFunction` at 25.

There is a second exemption for the same reason, applied per finding rather than per rule:
if a routine's `CountLineCode` or `CountStmt` rose while its complexity evidence
(`CyclomaticStrict`, `CyclomaticModified`, `MaxNesting`, `CountPath`, `MaxCyclomaticStrict`)
fell and none of it rose, the before value is dropped. Flattening a five-deep routine into
guard clauses was measured moving `MaxNesting` 5 &rarr; 2 while `CountLineCode` and
`CountStmt` both moved 8 &rarr; 11.

### Growth inside the limit is reported, not refused

This is the setting that stops the ratchet freezing every file it is pointed at, and it is
worth understanding because it is the difference between a gate a team keeps and one they
switch off.

A ratchet finding on an entity that is **still inside its own limit after the change** is
demoted to a warning. Measured, on a 23-line registrar with the shipped limits
(`routine.CountLineCode` 60, `routine.CountStmt` 40), adding one line:

```console
$ scitools-hook check --staged
pkg/big.py
  warning  routine.CountLineCode  pkg.big.grow  line 4  worse than before, was 23
    routine pkg.big.grow CountLineCode rose from 23 to 24, still within the maximum 60
  warning  routine.CountStmt  pkg.big.grow  line 4  worse than before, was 23
    routine pkg.big.grow CountStmt rose from 23 to 24, still within the maximum 40

summary: 0 errors, 4 warnings, 0 pre-existing, 0 blocking | exit 0: no blocking violations
```

The growth is printed, with the value it came from and the limit it is still inside. It does
not block.

**The rule, in three cases.** The configured limit is the team's own statement of what is
acceptable, so that is where the refusal belongs:

| The change | Result |
| --- | --- |
| grows an entity, still inside its limit | reported, **warning**, does not block |
| grows an entity **across** its limit | blocks |
| grows an entity already over its limit | blocks |

The second and third need no special case: a value outside its limit is a threshold violation
that the classifier refuses to call pre-existing once it has worsened. Measured, the same
routine taken from 24 lines to 64:

```console
  error  routine.CountLineCode  pkg.big.grow  line 4  1.1x limit, was 24
  error  routine.CountLineCode  pkg.big.grow  line 4  worse than before, was 24
  error  project.AVG:CountLineCode  2.1x limit
summary: 5 errors, 2 warnings, 0 pre-existing, 5 blocking | exit 1: blocking violations found
```

### What that trade gives up, stated rather than hidden

A routine may now creep from 29 lines to 60 one commit at a time without a single refusal.

Two things are left watching it, and neither is the ratchet. Every one of those commits still
*prints* the growth, because the finding is demoted rather than dropped. And
`project.AVG:CountLineCode` — shipped at 30, half the routine limit — blocks the commit that
pushes the mean routine length past it. That backstop was measured rather than assumed: it is
the `project.AVG:CountLineCode  2.1x limit` line in the output above.

What is genuinely given up is the case where a few routines fatten towards their limit while
the project mean stays under 30. That is the price of not freezing every file an agent
touches.

### Buying the strict behaviour back

```toml
[ratchet]
below_limit_severity = "error"
```

Measured, on the same one-line change that produced four warnings above:

```console
  error    routine.CountLineCode  pkg.big.grow  line 4  worse than before, was 23
  error    routine.CountStmt  pkg.big.grow  line 4  worse than before, was 23
summary: 2 errors, 2 warnings, 0 pre-existing, 2 blocking | exit 1: blocking violations found
```

The value is a **ceiling, never a promotion**: a rule you demoted to `warning` yourself stays
a warning even under `below_limit_severity = "error"`. The setting exists to soften a refusal
and must not manufacture one.

The defect this fixed is worth knowing about, because it is what the setting is calibrated
against. Every value in the field report it came from had the same shape — `CountLineCode`
29 → 30 of 60, `CountStmt` 2 → 3 of 40, `CyclomaticStrict` 5 → 6 of 10 — and every one was
produced *while splitting the routines the same gate had asked the reporter to split*. A gate
whose own remedy it refuses gets turned off, and it was: that day's commits went in under
`SCITOOLS_HOOK_SKIP=1`.

### A before value the analyser could not read is not used

If the *before* side of a file failed to parse, entities in it are not ratcheted and are not
given a before value at all.

The reason is a measured inversion. When a parse error truncated `config/models.py`,
Understand reported 3 classes for a file that has 15, and a 30-line function reported
`CountStmt` 66. Comparing against those numbers produced `file.CountDeclClass rose from 3 to
15` for a commit that *fixed* a syntax error, and would have forgiven any statement-count
violation the change introduced.

Leaving `before` unset says "not known", which blocks. Setting it from a truncated parse
would say "was worse", which does not.

## Strict mode

`ratchet.strict = true` makes pre-existing violations block too. It is off by default, and
it is the switch you turn on when a repository is clean and you want it to stay clean, not
the switch you start with.

```toml
[ratchet]
strict = true
```

## The adaptive baseline

There is a second mechanism for teams that want the limits to descend over time rather than
stay fixed.

`scitools-hook baseline` records the worst value currently present for each ratcheted rule.
With `baseline.adaptive = true`, the effective limit becomes `min(configured, baseline)` for
a maximum and `max(configured, baseline)` for a minimum. A baseline can only ever narrow the
configured limit, never widen it, and tightening only ever lowers a recorded value.

Adaptive tightening is confined to whole-project runs. A staged run's snapshot holds the
affected entities only, and feeding that to the baseline would lower a project-wide limit to
whatever the smallest commit of the day happened to touch.
