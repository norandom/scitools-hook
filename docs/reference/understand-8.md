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

## New in 8.0, not used yet

Candidates for a follow-up, in the order they seem to pay off for a gate. None is wired.

1. **SARIF from Understand itself.** `und codecheck` always writes `results.sarif`, and
   `und analyze -sarif` exists. GitHub code scanning ingests SARIF directly, so a workflow
   could upload Understand's findings and the gate's own SARIF in the same step.
2. **Databases from a commit.** `und create -gitcommit`, `-refdb` and `-gitrepo` build a
   database from a git revision, which is what the before side of a range check is.
3. **Generated architectures.** `und arch -generate` produces architectures such as Git
   Stability; the layer and coupling rules could run against one.
4. **New metrics.** For Python: `CountGlobalsModified`, `CountGlobalsSet`,
   `CountGlobalsUsed`, `CountClassCoupledModified`. Project-level: `CorePercentage`,
   `BidirectionalDepsPercent`, the `CBRI*` family, and comparison metrics between two
   databases. `CognitiveComplexity` is C/C++ only.
5. **Unused-function filters** for Python, a candidate for a structural rule.
6. **CodeCheck baselines.** `-gitfiles`, `-previous results.sarif` (New and Fixed reports),
   and the `PYTH_02` check; only reachable with a CodeCheck licence.
7. **`undmcp`**, an MCP server over the database. Its probe spawned two `undaiserver --tcp
   56767` processes that had to be killed by hand; it stays outside the network boundary.
8. **`und ai`**, a local model that downloads weights over the network and is off by
   default. Out of scope on a machine kept off the network.
9. **`und analyze -accuracy`**, a report on how much the analysis resolved; the call-graph
   resolution rate the snapshot already computes might be replaced or checked by it.
10. **Rust**, once a Cargo project is in the fixtures.
