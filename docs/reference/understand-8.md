# Understand 8.0

The install this project is measured on became Understand 8.0 (Build 1262) on 2026-09-05,
replacing 6.5 (Build 1204). Everything below is measured on that build unless it says
otherwise. `0.1.0a7` is the first release that works on it; `0.1.0a6` did not, in one
specific way described first.

## What broke in 0.1.0a6, and how 0.1.0a7 fixed it

**The metric catalogue.** 8.0 changed `understand.Metric.list()` to return `Metric` objects
instead of id strings, made `description` an instance method, and added `Metric.lookup(id)`
(its "Breaking API changes in 8.0" page, item 1). The a6 worker sorted the objects without
complaint and died serialising the answer:

```text
TypeError: Object of type Metric is not JSON serializable
```

Every catalogue request exited 1 with that traceback and the wrapper reported an analysis
failure, exit 5. The catalogue is asked only when `project.languages` is configured, so a6
on a repository without that key ran on 8.0 as before, and on one with it never got past
configuration validation. The worker now takes ids off the objects and describes through
`Metric.lookup(id).description()`, with the 7.x spellings kept as the fallback.

**The licence probe.** `und -isundlicensed` prints `0` with exit status 2 on 8.0, where 6.5
exited 0. The wrapper required exit status 0, fell through to `und license`, and `doctor`
printed `license: ok` on a machine where every `und analyze` failed with `No Server
Response`. The digit is the answer now, whatever the exit status, and `-isundlicensed` is
the only licence switch the tool runs: the licence commands that list a licence's options
rewrote the licence file that morning, so an answer that is not `1` is quoted as the
problem, early, and nothing else is asked. See [licensing is done from the command
line](../guide/install.md#licensing-is-done-from-the-command-line).

**`No Server Response`, `license is Invalid` and CodeCheck's `No checks in this configuration
are licensed to run`** are licensing texts now, mapped to exit 4 rather than to a broken
analysis.

**A licence without the API option** -- `1` from `-isundlicensed`, a clean `und analyze`,
`NoApiLicense` from `understand.open` -- is caught by `doctor`'s analysis probe, which
analyses a one-file scratch project and then opens it through the API in the mode a check
would use, and by a check at its first metric read.

## Three more differences, each with a test

| 8.0 does | 6.5 did | What the tool does |
| --- | --- | --- |
| `understand.open` on a bare `.und` directory raises `DBUnableOpen` | raised `DBEmpty` | the `DBUnableOpen` hint goes on to `scitools-hook db rebuild` |
| ships `CountParams` as a HIS plugin metric, with a description | answered `""` | the gate still computes its own count and describes it itself, because the plugin metric is unset for Python |
| `upython` describes `CountLineCode` in 2892 characters; an ordinary CPython loading the same module answers 2006 | the same | only the bundled interpreter finds the documentation resources and adds a `<br>`, an image and a "Targets By Language" list; the cross-mode contract compares descriptions with that block removed, and the metric lists to the character |

## What did not change

- `und create`, `und add`, `und analyze`, the shadow trees and the analysis cache.
- The Python dialect pin. `und` still runs a bare `python` from `PATH`; none there still
  means the Python 2 model, where `[first, *rest] = xs` still fails. The tool's `PATH`
  directory holding a `python` link still works. `-PythonVersion` is not a switch on 8.0;
  its settings are `PythonSetVersion Python2|Python3` and `PythonExe`.
- PEP 695 and PEP 654. 7.2 taught the parser type parameters, `type` aliases and `except*`.
  The thirteen constructs from `report/hints.py` analyse with `Errors:0` under the pin. The
  6.5 measurement stays in [What this adds beyond ruff and mypy](../compare/linters.md),
  dated, because the acknowledgement machinery was built against it; the ruff ignores and
  the acknowledgement suggestions are deliberately not lifted until an API-licensed run
  confirms that every routine after such a declaration is in the database, not only that
  the parse finishes.
- A file no build reads still costs the rest of the file. `def generic(x:` answers
  `expected identifier at token return` and then `expected token ':' at token EOF`, and the
  routine after it is gone from the database. That is the e2e fixture now, since the PEP 695
  one parses.

## Languages

`und create -languages` accepts the classic set plus **Rust**, which needs a Cargo project:
a bare `.rs` file analyses to nothing. Go, Dart, COBOL, Tcl, Perl, PL/M, Verilog and Delphi
are "not a valid language" on 8.0 as on 6.5. (`-quiet` hides that message; the first
measurement missed it for that reason.) The tool's language table is the one in
[Languages](../guide/languages.md); Rust is not in it yet.

## Open on 8.0: CodeCheck

CodeCheck configurations are plugins under `plugins/CodeCheck/{Configs,Published Standards}`;
the `Sandbox` configuration the 6.5 contract tests used does not exist. `und codecheck`
writes `results.sarif` and, by default, one CSV report, `CodeCheckResultsByTable.csv`, from
`plugins/Solutions/codecheck6Compatability`, with the columns

```text
File, Violation, Line, Column, Entity, Kind, CheckID, Check Name, Check Short Description, Severity
```

The three 6.5 CSV exports this tool reads by name are gone from `und`. The licence on the
measuring machine excludes CodeCheck, so the 8.0 output is unmeasured and the integration is
not adapted to it. The three CodeCheck contract tests expected-fail on such a build with
that reason, rather than skip, so the suite keeps saying the contract is open.

## What 8.0 buys, measured

Everything below shipped in this release. Each row is a measurement on Build 1262, and each
feature is **off by default**: a repository that changes no configuration behaves exactly as
it did on 6.5.

| Feature | Key | What it is worth, measured |
| --- | --- | --- |
| Understand's own SARIF beside the gate's | `understand.sarif` | one upload carries three tools; GitHub tells them apart by `tool.driver.name` |
| The before side built from the base commit | `understand.before_side` | reproducibility, **not** speed: on a warm run the before side already costs 0.0 s |
| Generated architectures as rule input | `structure.architecture` | `Git Stability` groups files by how the code has behaved rather than by where it was filed |
| The 8.0 plugin metrics | any threshold naming one | `CountGlobalsUsed` and friends for Python routines, `CountClassCoupledModified` for classes |
| Unused routines | `structure.unused_routines` | dead code an agent forgot to delete, as a warning |
| The accuracy of an analysis | `analysis.accuracy_floor` | how much of the run to trust; never blocks |
| One extraction per side, cached | `understand.snapshot_cache` | the warm one-line check went from **27.7 s to 13.0 s** on this repository |

### Understand's SARIF: a `check` concern, and a whole-project one

`check --sarif PATH` writes the gate's findings there and, with `understand.sarif = true`,
Understand's own documents beside it as `PATH.understand-analysis.sarif` and
`PATH.understand-codecheck.sarif`. They are never merged: GitHub code scanning accepts several
tools in one upload, and merging would mix fingerprints and rule ids from tools that know
nothing about each other.

`explain` has no SARIF output format, so the companions are a `check` concern only. Requirement
2.1 names "a check or explain run"; the reading taken is that the clause applies where SARIF
exists.

**The analysis companion comes from a whole-project pass only.** Measured: `und analyze -sarif`
reports *the pass*, not the database. A selective pass over one clean file writes a document
whose `results` array is empty while the database still holds three parse errors, and nothing
in the document says it is partial. Published, that is a clean bill of health for a repository
that has none. So a warm run writes no analysis companion and says why; a run over a cold
cache, or one after `scitools-hook db rebuild`, writes one.

### The before side from a commit: what it changes about the file set

`understand.before_side = "commit"` builds the before database with `und create -gitcommit`
rather than by exporting a shadow tree. Two consequences an operator should know:

- **The file set is the repository's, not the shadow's.** Without `-refdb` there is no file set
  to copy, so `und add` decides it under `und -exclude`, while the shadow is
  `project.include`/`project.exclude` applied by the synchroniser. The two pattern languages do
  not agree everywhere: measured, `und -exclude 'build/**'` excludes nothing while
  `-exclude build` drops the tree. Where they differ, the before side sees a different project.
- **`-refdb` cannot be used at all.** It copies the reference's file *paths*, the gate's after
  database names its files under a shadow tree in your cache, and `-gitcommit` pins the contents
  only of files inside `-gitrepo`. A file outside it is read from disk, silently. The before
  database then held the working tree's code and a range check reporting eight ratchet findings
  reported one.

### Comparison metrics: none exist yet

`-refdb` registers the two databases as a comparison pair, readable as `Db.comparison_db()`.
Measured: **no metric on 1262 reads it** -- 108 ids across the file, function, class and project
kinds, none of them a comparison metric. The gate's own ratchet already answers the question
those metrics would: it compares each entity's value on the two sides and reports the ones that
got worse. Nothing is lost by not registering the pair.

### Git architectures need a commit

Measured: the gate's own database, built over an exported shadow tree with
`GitRepositoryDirectory` set, exports `Git Stability` with **zero** members while exporting
`Directory Structure` with 260. The plugin runs `git log` and matches its output to the
database's file paths, and a shadow tree's paths are not paths git has heard of. So the gate
generates on a third database, rooted at the repository and pinned to the after side's commit,
and a `--staged` or `--worktree` check has no commit to pin: it says so rather than evaluating
against an empty architecture.

## Still open on 8.0

**CodeCheck.** The licence on the measuring machine excludes it, so the 8.0 output is
specified from the shipped plugin sources and unverified. The gate reads violations from
`results.sarif` where a run wrote one and from the 6.5 CSV exports otherwise; the contract test
for a real inspection is an expected failure naming the licence, so the suite keeps saying the
contract is open. `-gitfiles`, `-previous results.sarif` and the `PYTH_02` check are unreached
for the same reason.

**Rust**, once a Cargo project is in the fixtures. `und create -languages` accepts it; a bare
`.rs` file analyses to nothing.

## For a contributor: two things deliberately left out

Both are outside the boundary this project works in rather than beyond its interest, and both
are good first contributions for somebody whose machine is on the network.

**`undmcp`**, an MCP server over an Understand database. Probing it spawned two
`undaiserver --tcp 56767` processes that had to be killed by hand. A gate that starts a
listening server as a side effect of a commit hook is not a gate anyone should install, so
whatever this becomes needs a lifecycle somebody has designed on purpose.

**`und ai`**, a local model that downloads weights over the network and is off by default. The
machine this project is measured on is kept off the network deliberately, for licensing
reasons, so nothing here can measure it. Neither feature is refused on its merits; both are
simply unmeasurable here, and an unmeasured feature is not one this project ships.
