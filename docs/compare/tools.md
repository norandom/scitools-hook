# Against CodeQL, Semgrep, and the structure tools

## The conclusion first

Nothing on this page is a replacement for anything else on this page. They answer different
questions, and the useful thing is to know which tool answers which.

| Question | Tool |
| --- | --- |
| Does this code have a defect or a vulnerability? Does tainted input reach a sink? | **CodeQL**, **Semgrep** |
| Is this file correct and consistent in style? Do the types agree? | **ruff**, **mypy**, `clang-tidy`, `tsc` |
| How healthy is this codebase overall, as a score I can watch over time? | **aggregate structure tools** (sentrux, `radon`, `lizard`, SonarQube) |
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
    rev: v0.1.0a2   # a tag of this repository
    hooks: [{id: scitools-hook}]
```

CodeQL and Semgrep stay where they are, on the pull request, where their runtime is
affordable and their output is triaged rather than gating.
