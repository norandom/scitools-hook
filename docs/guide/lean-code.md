# Lean code

## The conclusion first

Every limit in this tool asks whether a piece of code is too complex. None of them asks
whether it should exist at all: a routine nothing calls once its replacement landed, a
parameter nothing reads, a wrapper that only forwards, an abstraction with one
implementation, a file that exports one name, the same twelve lines in three places. Each of
those is inside every limit and costs a reader anyway. The lean-code family is the set of
rules that asks the second question, and this page is how to read what it says.

Three things to hold on to before the detail:

- **Every rule in the family ships off.** A repository that never writes a `[lean]` table gets
  no lean finding. The one thing the family prints on the shipped defaults is the net line at
  the end of a check with a before side.
- **The tool sees three rungs of a seven-rung ladder and says so.** Its findings carry one of
  three tags, `delete:`, `yagni:` and `shrink:`. It never emits `stdlib:` or `native:`,
  because the reference database cannot see whether a standard-library or native call would
  replace the code. Those two rungs are the agent's own.
- **Half of the family is reliable today and half is refused by design.** The two duplication
  rules measured well on two repositories; the four rules that need to know what calls what
  sit behind two floors, and both measured repositories are below them. That is not a defect
  to configure away.

## The ladder

The family takes its shape from ponytail's ladder, which `scitools-hook agent-rules` prints
into the file your agent reads. Walk these in order and stop at the first that answers; only
the last one writes code.

1. Does this need to exist at all?
2. Does it already exist in this codebase?
3. Does the standard library do it?
4. Does a native platform feature cover it?
5. Does an already-installed dependency solve it?
6. Can it be one line?
7. Then write the minimum that works.

What a reference database can see of that:

| Rung | What the Gate can see of it | Rules |
| ---: | --- | --- |
| 1 | After the fact: code that exists and is not used, or a layer that exists for nothing | `structure.unused_parameter`, `structure.unused_class`, `structure.unused_variable`, `structure.pass_through`, `structure.single_implementation`, `structure.over_export` |
| 2 | Copies and near-copies of what the project already holds | `structure.duplicate_block`, `structure.similar_routine` |
| 3, 4, 5 | **Nothing.** Whether the standard library, the platform or an installed dependency would have done the job is a semantic question about code that was *not* written | none, by design |
| 6, 7 | Length without complexity, and whether the change made the project longer | `routine.LinesPerStatement`, `routine.CountLineComment`, the net line, `structure.net_growth` |

The rules on rungs 1 and 2 are structural rules: they carry the `structure.` category, so
`[ignore]`, the severity map and the ratchet reach them as they reach `unused_routines` or
the cycle rules. The two on rung 6 are ordinary threshold metrics under
`[thresholds.routine]`, on by default as warnings. Every rule is documented one by one, with
what it deliberately does not report, in the
[rules reference](../reference/rules.md#the-lean-code-rules); the
keys are in the [configuration guide](configuration.md#the-lean-code-family-lean).

## The three tags the Gate emits, and the two it does not

Every lean-code finding's hint opens with one tag naming the edit being asked for, followed
by what to cut and what replaces it, in one line. Under `scitools-hook --verbose check` (the
flag goes before the subcommand) each also carries a worked before-and-after example of the
shorter form beneath the hint, and the JSON output carries the same example beside the hint.

| Tag | Meaning | Rules whose hint opens with it |
| --- | --- | --- |
| `delete:` | It is not used, or it is a copy: remove it, and keep the one that is | `structure.unused_parameter`, `structure.unused_class`, `structure.unused_variable`, `structure.duplicate_block`, `structure.similar_routine` |
| `yagni:` | It should not have been written: a layer, an abstraction or a file that does nothing its one user could not do directly | `structure.pass_through`, `structure.single_implementation`, `structure.over_export` |
| `shrink:` | The same work fits in less code | `structure.similar_routine` when the whole family is in one file (the `/same_file` variant), `structure.net_growth` |

An operator may override any of these hints under `[hints]` at the rule level, and its tag
with it. The `agent-rules` snippet reads the tag off the hint in force rather than off the
shipped catalogue, so the tag it lists beside a rule is the tag that rule's findings carry.

**`stdlib:` and `native:` are never emitted.** Ponytail's ladder has them, and an agent
reading a report with no `stdlib:` finding in it could conclude that the standard library was
checked and had nothing to offer. It was not. The Gate works from Understand's reference
database: what calls what, what reads what, which token runs repeat. That database can say a
routine is called by nothing. It cannot say that the routine's forty lines are
`itertools.groupby`, that the hand-rolled path join is `os.path.join`, or that the retry loop
is something the HTTP client this project already installs does on its own. Each of those is
a statement about code that was not written, compared against a library the database never
indexed, and a rule that guessed at it would be right by coincidence. So the Gate says the
opposite, in the snippet it writes into your agent's rules file:

> Rungs 3, 4 and 5 are yours alone. No finding will ever carry either `stdlib:` or `native:`:
> whether the standard library, the platform or a dependency this project already installs
> would have done the job is a semantic question a reference database cannot ask. Their
> absence from a report is not a clearance -- nobody checked them but you.

What an agent is expected to do about them: walk rungs 3, 4 and 5 before writing, every time,
and treat a clean lean report as silence on those three rungs rather than as an answer.

## How to read the net line

A check with a before side ends with one line for the whole change:

```text
net: +12 lloc (+30 lines) over 7 routines
```

Read it left to right:

- **`+12 lloc`** is logical lines added minus logical lines removed: Understand's `CountStmt`,
  summed over the routines of the change's files on both sides. It leads because formatting
  cannot move it. The sign is always written, in both directions and on a zero, because the
  figure is a movement: `net: 0 lloc` would read as a measurement that was not taken.
- **`(+30 lines)`** is `CountLineCode` beside it. The two disagreeing is information: a change
  that removes statements while adding source lines has spread the same logic wider.
- **`over 7 routines`** is how many routines the sum ran over. A deleted routine counts as a
  negative contribution and an added one as positive, so a replacement that removes more than
  it adds shows as a reduction.

Three shapes of that line are worth knowing before you meet them:

- **`--all` prints no net line.** It has no before side, and the delta is omitted rather than
  printed as zero.
- **A deleted file that held no routines reads as net zero.** A change that only deletes a
  constants module, a data file or an `__init__.py` of imports prints
  `net: +0 lloc (+0 lines) over 0 routines`. The delta counts routines, and those lines were
  never counted as logic on either side. Task 2.2's review asked for this to be written down
  so that an agent meeting it does not conclude the number is broken: it is a genuine zero
  over nothing, not a missing measurement.
- **`lean already: nothing to cut, net -4 lloc`** beneath the net line means three things held
  at once: a lean rule is on, the change is no longer than what it replaced, and no lean rule
  found anything. It is printed only when all three hold, so do not report "nothing to cut"
  from a run that did not print it.

A positive number is not a violation. A feature costs code. It is the figure to argue with,
and the usual reason it is positive is that the path the change replaced is still there:
delete that, and the tests that only covered it, before you accept the number. The line
never blocks unless the operator sets `max_net_growth`; then a change above it is one more
finding, `structure.net_growth`, at `net_growth_severity`, with a `shrink:` hint.

The same figure is `net_delta` in the JSON output, an object with `statements`, `lines` and
`routines`, and `runs[0].properties.net_delta` in the SARIF output, where it is a property of
the run rather than a result.

## Which rules to trust

The family was measured on two repositories before any default shipped: this one and a
second, larger one, with every rule on at the shipped numbers, and again with the floors
forced to zero so that what the refused rules *would* say is on record (research.md, tasks
6.3 and 6.4). Every figure below names the task that took it.

### The duplication rules: the reliable half

`structure.similar_routine` at `similar_threshold = 0.9`, `similar_min_statements = 6` and
`similar_min_family = 2` reported 163 families here and 129 on the second repository; the
first ten of each, read in the source, were 6 and 7 genuine merges, the rest twins with the
same skeleton over different facts kept apart on purpose, and none noise (6.3).
`structure.duplicate_block` at `duplicates_min_lines = 12` reported 48 and 152; 8 and 5 of
the first tens were genuine, and the noise was `__all__` lists and import blocks of twelve or
more names, which repeat by design (6.3). Task 6.4 read the whole lists behind those first
tens and kept every one of the four numbers: a window of 15 would have removed 4 and 17
name-list findings at the cost of 26 of 42 and 63 of 122 code findings. Every block is
reported from both of its ends, so 48 findings are about 24 places.

These are the two rules the shipped skills propose on a first day, from a first-ten reading
of your own repository and never as a paste.

### The dead-code rules and the two floors

`structure.unused_parameter`, `structure.unused_class`, `structure.unused_variable` and
`structure.pass_through` need to know what calls what. They sit behind two floors that ship
set while every rule ships off: `resolution_floor = 0.75`, the share of a language's call
sites that resolved to a project routine, and `accuracy_floor = 0.75`, the share of the
analysis Understand parsed without an error. Below either, the rule evaluates nothing and
says so once per run:

```text
structure.unused_parameter was not evaluated: Understand parsed 19% of this analysis
without an error, below the accuracy floor of 75%; a name whose use sites sit in a
region the analysis errored on reads as unused while it is read
```

The resolution floor's line is printed per language: `<rule> was not evaluated for
<language>`, the share of that language's call sites that resolved to a project routine, the
floor, and `so a finding would report the analysis, not the code`. Either line means
**nothing was judged**. A run that printed it has no dead-code findings by design, and "no
unused code" is not something it can be read to say.

Both measured repositories are below both floors: 19.1% and 25.9% accuracy, 45.9% and 32.0%
call resolution (6.4). With the floors forced to zero, what the rules would have said is
why the floors stay where they are (6.4, whole lists):

- the parameter rule's 68 and 58 findings were 56 and 39 pytest fixtures declared by `test_`
  routines and 4 and 16 `@overload` stubs, shapes a list of parameter names cannot reach;
- the pass-through rule was right once and twice in its first tens, the rest one-line
  comprehensions and expressions holding a single project call, which score exactly as a
  forwarder does;
- the variable rule's first tens were 5 and 3 certain, and the second repository's noise was
  names read in other files the analysis did not resolve, which is the accuracy floor's case
  exactly;
- the class rule found 0 here and 18 there, 8 of its first ten named in no other source
  file;
  `structure.single_implementation` found nothing on either repository and
  `structure.over_export` one genuine file.

No repository above either floor has been measured, so nothing says at what figure that
noise vanishes. The floors move when one is, not to license a run. Do not confuse
`[lean] accuracy_floor` with `[analysis] accuracy_floor`: they read the same measurement, and
the `[lean]` one **refuses** where the `[analysis]` one only **reports**. Lowering the
reporting floor unlocks nothing here, and lowering the refusing one to make a rule speak
licenses the pass-through rule at one right answer in ten.

### The shrink metrics

`routine.LinesPerStatement` ships at most 4 as a warning and is judged only on routines of
`verbosity_min_statements = 5` statements or more, because below that the ratio is
arithmetic rather than verbosity. Task 6.4 raised the ceiling from 3.0: everything read
between 3.0 and 4.0 on both repositories was a single statement the formatter wrapped one
item per line, and at 4.0 the tail is 0.4% and 4.0% of the routines judged.
`routine.CountLineComment` ships at most 20 as a warning; 23 of 6 969 routines here and 11
of 9 450 there are outside it (6.3). Both carry the ratchet like any routine metric.

## Tests

No lean default was lowered for tests and no test tree is excluded. The measurement that
produced the duplication defaults found that tests dominate both duplication counts, and the
design's answer was a scope for the test tree rather than lower thresholds for everyone:
"tests are handled by a proposed `[scope.tests]` rather than by lower thresholds"
(research.md, the duplication defaults). Of the block rule's first ten here, every genuine
finding but one pair, the `_translate` copy between `config/models.py` and `git/shadow.py`,
was a block copied between or within test modules: two fixtures shared by two modules,
thirteen lines twice in one test file and three times in another (6.3). A test tree that
copies is a test tree that costs, and the rule is right to say so.

What the scope proposal is, and where it stops:

- **A path scope holds thresholds only.** `[scope.tests.thresholds.routine]` can raise,
  demote or switch off `LinesPerStatement` and `CountLineComment` for the test tree,
  exactly as it does `CyclomaticStrict`. It cannot touch anything under `[lean]`: the
  family's structural rules and their lists are repository-wide, because a copy in `tests/`
  of a block in `src/` is one duplicate with two ends.
- **`init --detect` already proposes a `[scope.<name>]` for every detected test tree**, with
  `CyclomaticStrict = 15`, `CountLineCode = 120` and `CountDeclFunction = false` as starting
  numbers to review. The lean-code extension of that proposal, a line for each of the two
  shrink metrics, is documented here and not built: nothing in `init --detect` writes a lean
  line for a tests region. If your test tree needs one, write it with the measurement beside
  it, as for any scope.

```toml
[scope.tests]
paths = ["tests/**"]

[scope.tests.thresholds.routine]
LinesPerStatement = { max = 6, severity = "warning" }   # measured: <your count> outside 4
CountLineComment = false                                # fixtures explain themselves in prose
```

- **For the structural rules, the shipped lists are the instrument**, and they already know
  what a test looks like by name: `(^|\.)test_` in `pass_through_ignore`, `(^|\.)Test` in
  `unused_classes_ignore`, `pytestmark` in `unused_variables_ignore`. `similar_ignore` and
  `duplicates_ignore` are path globs for a *region* that is copies by design, a fixture tree
  or a transcribed port; never `tests/**` wholesale, and never the file the finding is in.
- **What no list reaches is left with the rule.** The parameter rule's noise is a routine
  *shape*, a `test_` routine declaring a fixture it uses only for its effect, and 6.4 found
  that shape in 56 of 68 findings here and 39 of 58 there. A list of parameter names cannot
  spell it, and a `tests/**` ignore would hide the 12 and 19 that are not that shape. The
  exclusion belongs to the rule and is recorded for its owner; until it lands, enable the
  parameter rule last and read its first ten before trusting its count.

The shipped skills carry the same policy: `scitools-onboard` proposes the two duplication
rules from a first-ten reading and never enables a dead-code rule without reading
`after accuracy` in `doctor` against the floor; `scitools-adapt` has the family's lists and
numbers as its rung 4c and refuses the floors as a rung at all.
