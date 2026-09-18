# Against CodeQL, Semgrep, and the structure tools

## The conclusion first

Nothing on this page is a replacement for anything else on this page. They answer different
questions, and the useful thing is to know which tool answers which.

| Question | Tool |
| --- | --- |
| Does this code have a defect or a vulnerability? Does tainted input reach a sink? | **CodeQL**, **Semgrep** |
| Is this file correct and consistent in style? Do the types agree? | **ruff**, **mypy**, `clang-tidy`, `tsc` |
| How healthy is this codebase overall, as a score I can watch over time? | **aggregate structure tools** (sentrux, `radon`, `lizard`, SonarQube) |
| Is this routine risky because nothing tests it? | **coverage tools** (`pytest --cov`, `diff-cover`), and composites over them such as [CRAP](#coverage-weighted-complexity-the-crap-metric) |
| Has this problem already been solved somewhere in the tree, in some other shape? | **embedding-based clone finders** ([slopo](#semantic-clone-detection-slopo-and-the-embedding-approach)) |
| Which routine is too complex to change safely, where is it, and **did this commit make it worse than it was in `HEAD`?** | **this gate** |

The last row is the one nothing else in the list does. Not because the others are weak, but
because a per-entity before-and-after comparison inside a single commit is a different
mechanism from a scan, and none of them is built as one.

## CodeQL and Semgrep

These are the two this project is most often mistaken for, and the distinction is worth
stating carefully rather than dismissively.

**They are correctness tools on a security axis.** CodeQL builds a database from a real
compilation or extraction and runs queries with genuine interprocedural data flow and taint
tracking. Semgrep matches syntactic and semantic patterns quickly, with cross-file taint
analysis in the Pro engine. Both find real defects: injection, unsafe deserialisation,
missing authorisation, hardcoded credentials, use-after-free. This gate finds none of those
and does not try to.

**Do not read this as "this replaces CodeQL".** It does not. If you have a CodeQL workflow,
keep it.

There are three real differences, and only the third is a claim about capability:

1. **Different axis.** A routine can be at `CyclomaticStrict` 45 and contain no vulnerability
   at all. A one-line routine can contain a critical one. Neither tool sees the other's
   finding, because they are not looking at the same property.

2. **Different unit of work.** CodeQL and Semgrep are usually run per push or per pull
   request over the whole database or the whole tree, and their output is a set of alerts to
   triage. This gate runs inside the pre-commit hook, over the staged change only, and its
   output is a yes or a no about that commit.

3. **CodeQL can compute complexity metrics; it is not a commit-time ratchet.** This is the
   honest version of the claim. CodeQL's standard libraries expose cyclomatic complexity and
   related metrics, and you can absolutely write a query that reports functions above a
   threshold. What is not available out of the box is the mechanism this gate is built
   around: build a second database from `HEAD`, match entities across the two by a stable
   key, and refuse a commit because one entity's value moved in the wrong direction. That is
   buildable on CodeQL. It is not what CodeQL's default setup does, and doing it per commit
   rather than per pull request is a different performance problem.

Where they overlap usefully: all three emit **SARIF 2.1.0**, so `scitools-hook check --sarif
findings.sarif` lands in the same GitHub code-scanning view as your CodeQL results.

## Aggregate structure tools

Tools in this family — sentrux, `radon`, `lizard`, SonarQube's maintainability rating — score
a codebase and track the score. They are genuinely useful, and this project's own steering
documents credit `srccheck` as the origin of the threshold-per-scope idea.

Two things were measured directly for this page, against the same six-file demo repository
used in the [quickstart](../guide/quickstart.md), with the same dependency cycle and the same
over-complex routine in it. The comparison below is what those runs actually printed.

### A score is a different unit of answer from a finding

sentrux's `scan` returns the aggregate:

```json
{"files": 6, "import_edges": 3, "lines": 148, "quality_signal": 5333}
```

That is the number you put on a dashboard and watch. It is not something you can act on
directly, and it is not meant to be: `quality_signal` going from 5400 to 5333 does not tell
you which commit did it.

Its `health` call does give named diagnostics, and on this repository it found the cycle:

```json
{"acyclicity": {"cycles": [["pricing/catalog.py", "pricing/rates.py"]]}}
```

The gate, on the same tree, reported the same cycle with the closing edges and their
reference counts:

```console
$ scitools-hook check --staged
  error    structure.file_cycle
    2 files form a dependency cycle that did not exist before the change: pricing/catalog.py,
    pricing/rates.py; closed by pricing/catalog.py -> pricing/rates.py (3 refs),
    pricing/rates.py -> pricing/catalog.py (3 refs)
    hint: break the cycle: invert one dependency -- move the shared type into a module both
    files can import, or pass it in instead of importing back
```

The extra content is *which edges close the cycle*, *how many references each carries*, and
**that it did not exist before this change**. The first two tell you where to cut. The third
is what makes it a gate rather than an inventory: a cycle that was already there does not
block the commit that happens to touch one of its files.

### Where a real parse beats name matching

This is where the two approaches diverge at scale, and it is worth being precise about the
mechanism rather than the score.

Most fast structure tools resolve imports by matching module names, because parsing every
language properly is expensive. That works on a small tree — sentrux resolved this six-file
demo correctly. It degrades on a large one, where module basenames collide and dynamic or
relative imports do not match by name.

The figures that motivated this project came from a private 770-file Python repository. They
were reported to the author of this documentation and **have not been re-measured here**, so
they are stated as received:

> On that repository the incumbent tool reported `cycle_count = 0`, because it resolves
> imports by module-name *suffix* and left roughly 2 029 of 4 165 specs unresolved. The gate,
> using Understand's real parse, found three dependency cycles: 9 files across
> `shells/config` &harr; `shells/pods`, 18 files across `shells/dashboard` &harr;
> `shells/reporting`, and one between `scripts` and `tests/unit` — each with its closing edges
> and reference counts named.
>
> The same tool reported `complex_fn_count = 15` and `god_file_count = 16`, and its
> maintainer described having to bisect by untracking files to find which ones they were.

Treat the specific numbers as second-hand. The mechanism behind them is not second-hand and
is checkable in this repository: Understand builds a real database per language, and the
extension map that feeds it is re-measured against the installed build by a contract test in
both directions.

### A calibration difference worth knowing about

On the demo repository, the two tools disagreed about what counts as complex. sentrux's
`complex_functions` list was empty; its `cog_complex_functions` reported `render` at a
cognitive complexity of 29. The gate reported the same routine at `CyclomaticStrict` 12
against a limit of 10, and blocked on it.

Neither is wrong. They are different metrics with different calibrations, and if you run
both you should expect them to disagree at the margin. What matters is that only one of them
is comparing against the previous commit.

### The heuristic this gate deliberately does not use

sentrux's `redundancy` diagnostic on the demo listed six `dead_functions`, including
`pricing.settle.settle` and `pricing.catalog.price_in` — both of which are the module's
public interface, called from outside the tree. On a library, "nothing in this repository
calls it" and "dead" are not the same statement.

This gate does not have a dead-code rule, and the reason is recorded in
`analysis/structure/calls.py` as a measurement on that 770-file project:

> The routine with the highest `CyclomaticStrict` of all — a dataclass's `__post_init__`, 45
> — has a call-graph fan-in of **zero**, because the call that runs it is generated by
> `@dataclass` and appears in no source file. A "nothing calls this" rule would have named it
> dead code, which is the opposite of the truth.

That is not a criticism of the heuristic in general. It is why the gate's primary rules are
per-entity metrics rather than graph statistics, and why the call-graph rules it does have
report every finding as an explicit lower bound with the resolution rate attached.

## Coverage-weighted complexity: the CRAP metric

[CRAP](https://testing.googleblog.com/2011/02/this-code-is-crap.html) — Change Risk
Anti-Patterns — is the composite most often suggested for this gate, so the answer is
recorded here with the measurement behind it. It scores one routine as

```text
CRAP(m) = comp(m)^2 * (1 - cov(m))^3 + comp(m)
```

with a conventional ceiling of 30, where `comp` is cyclomatic complexity and `cov` is the
fraction of that routine covered by tests.

**It is not shipped, and the reason is not taste.** The half of it that is new to this gate is
the half the gate has no input for, and with that half held at zero the metric is a
re-labelling of a ceiling that is already in force.

### Measured: with no coverage term, CRAP is `CyclomaticStrict` under another name

The gate reads two things: git, and an Understand database. Neither carries test coverage;
nothing in the pipeline ingests a `coverage.xml`, an lcov file or a `.coverage`. With
`cov(m) = 0` — the only value this gate could supply for every routine — CRAP collapses to
`comp^2 + comp`, which is strictly increasing in `comp` and therefore ranks a population in
exactly the order `CyclomaticStrict` already ranks it.

That is arithmetic, not a measurement, so it was run as one: every routine of two
repositories, out of the database the gate's own cache held, on Understand 8.0.1262.

| | facdrone, `aa06c5a`, 2026-09-17 | this repository, 0.3.0, 2026-09-13 |
| --- | --- | --- |
| Routines walked | 10 463 | 7 050 |
| `CyclomaticStrict` p50 / p95 / p99 / max | 1 / 7 / 9 / 21 | 1 / 4 / 6 / 14 |
| Order by CRAP identical to order by `CyclomaticStrict` | yes | yes |
| Discordant pairs (400-routine sample, 79 800 pairs) | 0 | 0 |
| Tied pairs, `CyclomaticStrict` / CRAP | 21 190 / 21 190 | 46 811 / 46 811 |
| Reported by the shipped `routine.CyclomaticStrict = 10` | 21 (0.2%) | 3 (0.0%) |
| Reported by `CRAP > 30` at `cov = 0` | 794 (7.6%) | 100 (1.4%) |

Not one pair of routines is ordered differently by the two numbers, and the tie structure is
identical, because squaring a non-negative integer and adding it back preserves both. The
ten worst routines are the same ten in the same order on both repositories.

The only thing the second row adds is a **different cut point on the same axis**: `CRAP > 30`
at zero coverage is `CyclomaticStrict` &ge; 6. It reports a strict superset of what the shipped
ceiling reports — 773 extra routines on facdrone, 97 here — and every extra one sits in
`CyclomaticStrict` 6 to 10. Shipping it would not be a new rule. It would be the existing
ceiling moved from `max = 10` to `max = 5`, on two repositories where nobody measured
that number, stated in a unit (a score of 42) that no hint can answer.

### What the coverage term buys, and which way it points

Suppose the coverage were available. This is what it would be worth, per routine — the
coverage at which CRAP falls back under 30, with facdrone's population at each complexity:

| `comp` | coverage needed for `CRAP <= 30` | facdrone routines at that `comp` |
| ---: | ---: | ---: |
| 6 | 12.6% | 253 |
| 8 | 29.9% | 157 |
| 10 | 41.5% | 72 |
| 11 | 46.1% | 10 |
| 15 | 59.5% | 2 |
| 20 | 70.8% | 1 |
| 25 | 80.0% | 0 |
| 30 | unreachable at any coverage | 0 |

Read the column downwards and the direction of the metric becomes the problem. Coverage does
not tighten CRAP, it **relaxes** it, and it relaxes it fastest exactly where this gate is
least willing to yield:

| Coverage assumed uniform | facdrone routines the gate blocks that `CRAP <= 30` forgives | worst one forgiven |
| --- | --- | --- |
| 50% | 12 of 21 | `CyclomaticStrict` 12 |
| 60% | 18 of 21 | 15 |
| 70% | 19 of 21 | 17 |
| 80% | 21 of 21 | 21 |

At 80% coverage CRAP reports nothing at all on facdrone, including the 21-branch routine the
gate refuses today; on this repository, whose own suite holds `src/` at 98.19% branch
coverage, CRAP is silent at every complexity below 30 while the gate still refuses three
routines. **A tested routine is not a cheaper routine to change.** That is the whole
disagreement: CRAP prices the risk that a defect escapes, and a test suite genuinely lowers
that risk; this gate prices the cost of the next change, and a test suite does not lower
that. The [working-set argument](../argument/working-set.md) is about what an agent or a
reviewer can hold in their head, and 21 branches is 21 branches whether or not they are
covered.

### Where the coverage number does not exist at all

The third measurement is the one that decided it. Coverage is collected for the code under
test, conventionally `--cov=src`, so the gate's population and the coverage tool's population
are not the same population:

| | facdrone | this repository |
| --- | --- | --- |
| Routines under `src/` | 3 890 (37%) | 1 577 (22%) |
| Routines outside it — tests, scripts, tooling | 6 573 (63%) | 5 473 (78%) |
| Of the routines the gate blocks, how many are outside `src/` | 15 of 21 | 3 of 3 |

For the majority of what the gate judges, `cov(m)` is not a number that exists. Taken as 0 it
makes CRAP harshest precisely where the input is missing rather than where the code is worst;
taken from a run that includes the test files themselves it approaches 1, and the metric
forgives a 15-branch test helper for the crime of having executed. Both answers are
arbitrary, and the routines this actually decides are not edge cases — they are 15 of the 21
findings facdrone gets today, and all three of this repository's.

### What to do instead

Keep the two questions with the tools that can answer them.

- **"Is this change untested?"** belongs to the coverage tool, which owns the data:
  `pytest --cov --cov-fail-under`, `diff-cover` for the change rather than the tree, or your
  CI's coverage gate. This repository runs the first of those at 85% branch coverage over all
  of `src/scitools_hook`, and that check is not improved by being folded into a complexity
  score.
- **"Can this routine still be changed safely?"** is what the shipped ceilings answer, per
  entity, against `HEAD`, with a hint naming the refactoring: `CyclomaticStrict` 10,
  `CyclomaticModified` 8, `MaxNesting` 3, `CountPath` 100.

Running both gives you each finding in a unit you can act on. Multiplying them together gives
you a number that cannot tell you which of the two to fix — and as the tables above show,
lets a high value of one buy off the other.

### What would change this

Not the score; the conjunction behind it. A rule of the shape *"this commit touched a routine
that is both complex and has no covering test"* keeps the two facts separate and is
answerable. It is buildable here — `RoutineShape` already carries each routine's first and
last line for the [duplicate-block rule](../reference/rules.md#structureduplicate_block), so
a line-range join against a coverage report is the small part — and it is not built, for
three reasons that are the same reasons the [lean-code rules](../guide/lean-code.md) carry
trust floors:

1. **A pre-commit hook does not run your test suite.** The coverage report arrives as an
   artifact of unknown age, and a stale one errs towards forgiveness — a rule that goes quiet
   when its input rots is worse than no rule.
2. **There is no single coverage format**, and this gate analyses whatever Understand
   analyses. Each language is a separate parser and a separate staleness story.
3. **The population mismatch above would have to be declared**, not averaged away: the rule
   would have to say "not judged" for every routine the coverage run never looked at, the way
   `LinesPerStatement` declines to judge a routine under its floor.

If you want it, say so in an issue with the coverage format you produce. It would ship as a
warning with a freshness check, behind its own floor, and never as a number that trades
complexity against coverage.

## Semantic clone detection: slopo and the embedding approach

[slopo](https://github.com/rafal-qa/slopo) names a failure this gate's own lean-code family
was written for and only half solves, so it is worth being precise about where the line
falls. Its README states the problem better than a paraphrase would:

> agents miss a solution that already exists somewhere and implement it again. This is not
> copy-paste; this is a similar implementation for the same problem, which is hard to detect
> for humans, AI, and other tools.

It embeds code snippets, clusters them by cosine similarity, and re-ranks so that matches
far apart in the tree outrank neighbours — up to a 15% boost across directories and 10% by
line distance within a file. Ten languages, a `slopo.conf.yaml`, and a Markdown report as the
output. It is a CLI you run, not a hook. **Nothing about slopo's own behaviour was measured
for this page** — this machine has no network — so the description above is its README's, the
way the sentrux figures further up are marked as received. What is measured here is the other
side of the line: what this gate's duplication rules do and do not find.

### What the gate's two duplication rules actually compare

Both are lexical, and deliberately so. Neither resolves a reference, which is why neither
takes a [trust floor](../reference/rules.md#two-floors-in-front-of-four-rules):

* [`structure.duplicate_block`](../reference/rules.md#structureduplicate_block) hashes each
  code line — a 64-bit truncated SHA-256 — and reports a window of at least
  `duplicates_min_lines` (12 by default) that stands in more than one place. Exact
  repetition, whitespace and comments already out.
* [`structure.similar_routine`](../reference/rules.md#structuresimilar_routine) takes each
  routine's token shape from `Ent.lexer(False)`, with every identifier collapsed to `ID` and
  every literal to its class, and groups routines whose shapes match at
  `similar_threshold` (0.90) or better over at least `similar_min_statements` (6) statements.

So the second rule is blind to names, blind to literals, and reads the *sequence of token
kinds*. That places it at parameterised clone detection: the twelve `normalize` methods that
are one routine with a provider name in them. It is not a semantic comparison, and the
distance between those two things is measurable.

### Measured: where the token shape stops seeing it

One fixture, three files, one decision written three ways — the same six-way banding of a
score — run through `scitools-hook check --all` on Understand 8.0.1262. The threshold was
then lowered run by run until each pair was reported, which is what puts a number on the gap:

| The same function, rewritten as | Token-shape ratio | Reported at the shipped 0.90? |
| --- | ---: | --- |
| the same branches with every identifier and literal renamed | 1.00 | **yes** — one family of 2 |
| six guard clauses turned into one `elif` ladder with a single exit | 0.64 | no |
| a tuple of `(limit, label)` pairs and a loop over it | 0.34 | no |

The first row is the rule working exactly as intended: nothing textual survives the rename,
and the shape still matches perfectly. The other two are the same six comparisons producing
the same six answers, and the rule does not pair them — which is the honest boundary. **This
gate finds a routine that was copied and renamed. It does not find a routine that was
re-implemented**, and a model asked the same question twice in two sessions re-implements far
more often than it copies.

Note also that the second row is the *easy* miss: same language, same branch order, same
comparison operators, and the ratio is already 0.64.

### Why the answer is not "lower the threshold"

Because the band the restructured twins land in is where unrelated code lives too, and that
was measured on a real repository rather than on the fixture. facdrone's own calibration
records what the two knobs cost:

| `similar_threshold` / `similar_min_statements` | Families reported | Full audit |
| --- | ---: | ---: |
| 0.90 / 6 (shipped) | 128 | 84 s |
| 0.90 / 4 | 292 | — |
| 0.85 / 4 | 394 | 352 s |

Moving the threshold from 0.90 to 0.85 — a fraction of the way down to 0.64 — triples the
report and quadruples the runtime, and the families it adds are not restructured twins:
they are short routines that share a shape by coincidence. A ratio is a statement about
token sequences, and no setting of it turns it into a statement about meaning. That is the
argument for a *different mechanism*, and it is the argument slopo is making.

### Where each one belongs

They do not compete, and the reason is the one this page keeps returning to: they answer in
different units.

| Question | Answered by |
| --- | --- |
| Did this commit add a routine that already exists here under other names? | this gate, at commit time, as a refusal or a warning naming the family |
| Has this problem already been solved somewhere in the tree, in some other shape? | an embedding tool such as slopo, as a ranked report you read |

Run slopo the way its README suggests — periodically, and especially after a stretch of
agent-written code, which is when re-implementation arrives in bulk. Keep it out of the
pre-commit path, and that is not a criticism of the tool; it is the same boundary as
[CRAP](#coverage-weighted-complexity-the-crap-metric) above. A cosine of 0.86 cannot be the
reason a commit was refused: the finding cannot name the evidence a reader checks, the hint
under it cannot be written, and the number is not stable across model versions, which is
fatal for a [ratchet](../argument/ratchet.md) that compares today's value against `HEAD`'s.
A report that a human or an agent triages is exactly the right shape for a fuzzy signal.

One idea in it is worth taking without embeddings: slopo ranks a match higher when its ends
sit far apart in the tree. `structure.similar_routine` weighs every family the same wherever
its members live, and duplication across a package boundary is worse than duplication inside
one file. That is a pure token-shape change, measurable on the 128 families facdrone already
reports, and it is recorded here as a direction rather than a promise.

## The thing none of them do

Per-entity, before and after, within one commit.

```text
routine legacy.report.render CyclomaticStrict rose from 12 to 13;
an affected entity may not get worse than it was
```

That sentence requires four things at once: a stable entity identity across two separate
analysis databases, a before state built from `HEAD`, a per-entity comparison rather than a
per-file or per-project one, and the whole thing fast enough to run before `git commit`
returns. It is the only claim on this page that is not shared with some other tool in the
list.

## Running them together

They compose without conflict, and the ordering only matters for speed:

```yaml
# .pre-commit-config.yaml -- cheapest first
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.16.5   # whatever you already pin
    hooks: [{id: ruff-check}, {id: ruff-format}]

  - repo: https://github.com/norandom/scitools-hook
    rev: v0.3.0   # a tag of this repository
    hooks: [{id: scitools-hook}]
```

CodeQL and Semgrep stay where they are, on the pull request, where their runtime is
affordable and their output is triaged rather than gating.
