# Requirements Document

## Project Description (Input)
**Who has the problem.** The operator of scitools-hook -- an engineering lead running the gate as a pre-push hook on facdrone and on this repository, on a machine kept off the network with an offline Understand licence -- and the coding agents that run the gate on their behalf.

**Current situation.** 0.1.0a8 works on Understand 8.0 (Build 1262) at feature parity with what 6.5 offered: it builds two shadow databases, analyses them with `und analyze` (`-all` cold, `-changed` warm), extracts a snapshot per side through the Python API, and evaluates thresholds, ratchets and structural rules. It uses none of what 8.0 added. A check costs more CPU than the operator would like: measured on this repository (210 files, 8.0.1262), a warm check with one changed line takes 32.6 s of wall clock and 32 s of CPU at 105%, of which the two `und analyze -changed` passes are 8.7 s and the four snapshot extractions (two per side: the selected files first, then their neighbourhood) are 22.7 s; a whole-project check of one side takes 14.9 s. Findings reach GitHub only through the gate's own SARIF writer; Understand's own SARIF (`und codecheck` writes `results.sarif`, `und analyze -sarif` exists) is unused. Architecture rules run only against `Directory Structure` or a hand-declared architecture. The metrics 8.0 added for Python and for the project are not in the catalogue the gate offers.

**What should change.** In the order the operator chose on 2026-09-05: (1) Understand's SARIF beside the gate's own; (2) the before side built from a commit; (3) generated architectures as rule input; (4) the new metrics; (5) unused-function filters as a structural rule; (6) `und analyze -accuracy` as a resolution report; (7) the warm-run cost, measured first and then lowered.

**Out of scope, recorded as contribution candidates.** `undmcp` (an MCP server over the database; its probe spawned `undaiserver` processes on a TCP port, which the network boundary forbids) and `und ai` (a local model that downloads weights over the network). Someone else may take them up; nothing here depends on them.

**Constraints that carry over.** The machine stays off the network; licensing is the user's and never an agent's (`.kiro/steering/licensing.md`); the tool is released only on GitHub; the CodeCheck licence is absent on the measuring machine, so anything needing `und codecheck` output is specified against the plugin sources and documented as unmeasured until a licence exists.

## Introduction

`scitools-hook` (below: the Gate) is the maintainability gate specified in `.kiro/specs/maintainability-gate`. This specification adds what Understand 8.0 (Build 1262) offers that the Gate does not yet use, and lowers the cost of a warm run. Everything below is conditional on the installed build: a repository gated on Understand 6.5 keeps today's behaviour, and `doctor` says which of these features the installed build offers.

Terminology used throughout, in addition to the base specification's:
- **The Gate's SARIF**: the findings document the Gate already writes with `--format sarif`. **Understand's SARIF**: the documents Understand itself writes -- the parse errors and warnings of an analysis (`und analyze -sarif`), and the inspection results of a CodeCheck run (`results.sarif`).
- **Base commit**: the commit the before side of a check represents (`HEAD` for a staged or worktree check, the range start for a range check).
- **Commit-built database**: an Understand database whose file contents come from a git commit rather than from a checkout on disk.
- **Generated architecture**: an architecture Understand derives by itself from the repository's git history (Git Author, Git Owner, Git Stability) or from another plugin, as opposed to `Directory Structure` and a hand-declared one.
- **Accuracy**: the figure Understand reports for how much of an analysis it could resolve (its Project Overview "accuracy" metric, printed by `und analyze -accuracy`).
- **Warm run**: a check on a repository whose databases exist from an earlier run, with a change of ordinary size (one to a few files).
- **Snapshot**: the document the Gate extracts from one database side, as in the base specification.

## Boundary Context

- **In scope**: writing Understand's SARIF beside the Gate's and reading CodeCheck results from it; building the before side from the base commit; generated architectures as the node source for architecture rules and review aids; the 8.0 metrics in the catalogue, the defaults and `recommend`; an unused-routine structural rule for Python; the accuracy figure in output and diagnosis; the measured cost of a warm run and its reduction; a `doctor` row per feature saying whether the installed build offers it; documentation, including a contributors' note on `undmcp` and `und ai`.
- **Out of scope**: `undmcp` and `und ai` (recorded, not built); obtaining or configuring a CodeCheck licence; Rust support; uploading SARIF to GitHub (the operator's workflow does that; the Gate produces the files); any change to what the Gate's own SARIF contains; multi-threading Understand's analysis (the build decides that); analysis engines other than Understand.
- **Adjacent expectations**: Understand 8.0 provides `-gitcommit`/`-refdb`/`-gitrepo`, `und arch -generate`, `und analyze -sarif` and `-accuracy`, the new metrics and the unused-function filters, with the semantics its shipped documentation describes; git provides the base commit and the history the generated architectures read; GitHub code scanning accepts SARIF 2.1.0 documents from more than one tool in one upload. The Gate owns none of these and reports plainly when one is missing. Behaviour that can only be verified with a CodeCheck licence is specified from the plugin sources shipped with 8.0 and is documented as unmeasured until a licence exists.

## Requirements

### Requirement 1: Feature Availability by Build
**Objective:** As an operator, I want the Gate to say which of these features my installed Understand offers, so that a configuration asking for one the build lacks fails at configuration time and never as a silent no-op.

#### Acceptance Criteria
1. When `doctor` runs against an installed Understand, the Gate shall print one row per feature in this specification (Understand SARIF, commit-built before side, generated architectures, 8.0 metrics, unused-routine rule, accuracy) saying `available`, `not on this build`, or `unverified` with the reason.
2. If the configuration enables a feature the installed build does not offer, the Gate shall stop with a configuration error (exit 2) naming the feature, the build, and the configuration key that asked for it.
3. While the installed build is 6.5, the Gate shall behave exactly as 0.1.0a8 does, and every feature in this specification shall be off by default.
4. The Gate shall measure the availability of each feature on the installed build rather than infer it from the version number, so that a build that carries a feature under a later version reports it.

### Requirement 2: Understand's SARIF Beside the Gate's
**Objective:** As an operator publishing findings to GitHub code scanning, I want Understand's own SARIF documents produced with the Gate's, so that one upload step carries the Gate's findings, Understand's parse diagnostics and, where licensed, CodeCheck's inspection results.

#### Acceptance Criteria
1. When a check or explain run is invoked with SARIF output and the installed build can write the analysis diagnostics as SARIF, the Gate shall write Understand's analysis SARIF for the after side beside the Gate's SARIF, as a separate file named so the two cannot be confused, and shall name both files in the run output.
2. The Gate shall leave the content of its own SARIF unchanged: no finding shall move from the Gate's SARIF into Understand's or be duplicated across the two.
3. Where CodeCheck is configured and the installed build writes inspection results as SARIF, the Gate shall keep that document beside the other two and shall read the CodeCheck violations the Gate reports from it, so that CodeCheck findings work on a build that no longer writes the CSV exports the Gate reads today.
4. If an Understand SARIF file is missing or unreadable after the analysis, the Gate shall report which one and why in the run output, shall still write its own SARIF, and shall not change the exit code on that account.
5. The Gate shall document, with a working example, how the files are uploaded together to GitHub code scanning.
6. While no CodeCheck licence is available on the measuring machine, the Gate shall document acceptance criterion 3 as specified from the shipped plugin sources and unverified, and its contract test shall be an expected failure naming that reason.

### Requirement 3: The Before Side Built from the Base Commit
**Objective:** As an operator, I want the before side of a check built directly from the base commit rather than from an exported shadow tree, so that the before side is reproducible by construction and costs less to keep.

#### Acceptance Criteria
1. When a check needs a before side and the installed build can create a database from a git commit, the Gate shall build the before database from the base commit in the repository, with the same file set the after side has, and shall not export a shadow tree for it.
2. The Gate shall produce, for the contract project, the same before snapshot and the same findings through the commit-built route as through the shadow-tree route, so that the two routes are interchangeable.
3. While the installed build cannot create a database from a commit, the Gate shall keep the shadow-tree route unchanged.
4. If the commit-built route fails -- the repository cannot be read, the base commit does not exist, the build refuses -- the Gate shall report the failure in the run output, fall back to the shadow-tree route for that run, and say so.
5. The Gate shall reuse a commit-built before database across runs while the base commit, the enabled languages, the configuration that affects analysis and the Understand build are unchanged, and shall rebuild it when any of them changes.
6. When `doctor` runs, the Gate shall report which route the before side uses and, for a commit-built database, the commit it was built from.

### Requirement 4: Generated Architectures as Rule Input
**Objective:** As a reviewer, I want the layer and coupling rules and the change summary to work on an architecture Understand generates from the repository's history, so that a change can be judged by who owns the code it touches and by how often that code churns, not only by its directory.

#### Acceptance Criteria
1. Where the configuration names a generated architecture, the Gate shall generate it in the after database before the architecture rules run and shall use its nodes as the node source for the arch-cycle, layer and coupling rules and for the change summary, as `Directory Structure` is used today.
2. If the configuration names a generated architecture the installed build does not offer, the Gate shall stop with a configuration error listing the generated architectures the build does offer.
3. While the after side is analysed from a shadow tree that is not a git checkout, the Gate shall still generate git-based architectures from the repository's own history, and if it cannot, shall report why rather than evaluate the rules against an empty architecture.
4. When a generated architecture is regenerated, the Gate shall report the time it took in the verbose output, and the Gate shall not regenerate it when the repository's history and the after database are unchanged since the last run.
5. The Gate shall list the nodes of a generated architecture in the explain output and the review aids exactly as it lists directory nodes, so that a reader sees which node a changed file falls under.

### Requirement 5: The 8.0 Metrics
**Objective:** As an operator, I want the metrics 8.0 added offered wherever Understand computes them, so that limits and ratchets can be set on them with the same configuration, catalogue and recommendation the existing metrics have.

#### Acceptance Criteria
1. The Gate shall offer, in its metric catalogue and in threshold configuration, every metric the installed build computes for a configured language and scope, including `CountGlobalsModified`, `CountGlobalsSet`, `CountGlobalsUsed` and `CountClassCoupledModified` for Python, `CorePercentage`, `BidirectionalDepsPercent` and the `CBRI` family for the project, and `CognitiveComplexity` for the languages that have it.
2. When a configured metric is not computed by the installed build for a language, the Gate shall report it once per run as unavailable, exactly as the base specification's requirement 5.5 does today.
3. When `recommend` runs, the Gate shall measure the 8.0 metrics the build offers and propose limits for them with the same evidence it gives for the existing metrics.
4. The Gate shall ship no new blocking default for a metric that was not measured on this repository; a new metric enters the defaults as a warning or disabled until a measurement is recorded in the configuration.
5. Where the before and after databases are registered with each other as a comparison pair, the Gate shall offer the comparison metrics Understand computes between them as project-scope metrics, named as Understand names them, and shall document which comparison metrics the base specification's own ratchet already covers.

### Requirement 6: Unused Routines as a Structural Rule
**Objective:** As a reviewer of agent-written code, I want a change that leaves a Python routine nothing calls to be reported, so that dead code an agent forgot to delete is visible before it lands.

#### Acceptance Criteria
1. Where the installed build offers its unused-function filter for Python, the Gate shall report each affected routine that the filter classifies as unused as a structural finding, named as a structural rule, with the routine's location and a remediation hint.
2. The Gate shall decide "unused" over the whole project, never over the affected neighbourhood alone, so that a routine called from an unchanged file is not reported.
3. The Gate shall ship the rule as a warning by default, and shall accept an ignore list of routine-name patterns for entry points, tests, dunder methods and decorated handlers, applied the way the existing ignore rules are.
4. If the installed build does not offer the filter, the Gate shall report the rule once per run as unavailable and shall evaluate nothing for it.
5. When a routine is deleted by the change, the Gate shall not report it as unused.

### Requirement 7: The Accuracy of an Analysis
**Objective:** As an operator, I want to know how much of an analysis Understand could resolve, so that a run whose findings rest on a poorly resolved analysis is recognisable as such.

#### Acceptance Criteria
1. When a check analyses a side on a build that reports accuracy, the Gate shall record the figure per side and print it in the verbose output and in the JSON output.
2. When `doctor` runs, the Gate shall report the accuracy of the after database when one exists.
3. Where a configured accuracy floor exists, if a side's accuracy falls below it, the Gate shall raise a non-blocking finding saying so, and shall never block on accuracy alone.
4. The Gate shall measure, on the contract project, how the accuracy figure relates to the call-resolution rate the snapshot computes today, shall record the relation in the design's research log, and shall keep its own rate in the output until that relation is understood.

### Requirement 8: The Cost of a Warm Run
**Objective:** As an operator who runs the Gate on every push, I want a warm check to cost a fraction of what it costs today, measured, so that the hook stays something one runs rather than switches off.

#### Acceptance Criteria
1. The Gate shall record, before any change under this specification, the wall-clock and CPU time of a warm check with one changed line and of a whole-project check on this repository and on facdrone, per phase, in the design's research log; the figures above for this repository are the first such record.
2. When a warm check runs and the base commit, the configuration and the Understand build are unchanged since the last run, the Gate shall not extract the before snapshot again, and shall say in the verbose output that the before side was served from the previous run.
3. When a warm check runs, the Gate shall extract each side at most once, or shall bound the first extraction to the selected files so that its cost is proportional to the change rather than to the repository; the verbose output shall show the time of each extraction.
4. The Gate shall complete a warm check with one changed line on this repository within 15 s of wall clock on the measuring machine, against the 32.6 s measured today, without changing the findings it reports.
5. When no file is selected for a check, the Gate shall run no analysis and no extraction, as today (measured: 1.0 s).
6. The Gate shall invalidate every cached snapshot or database when the Understand build, the enabled languages, the configuration that affects analysis or the base commit changes, and `doctor` shall report the cache's contents and their age.
7. The Gate shall not change the findings, the exit code or the output format of a check on account of any caching; a cached run and an uncached run of the same change shall report the same findings.

### Requirement 9: Documentation and the Contributors' Note
**Objective:** As an operator or a contributor, I want each feature documented where the existing ones are, and the two features deliberately left out named with their constraints, so that nobody re-derives what was decided.

#### Acceptance Criteria
1. When a feature under this specification is released, the Gate's documentation shall describe it on the Understand 8.0 reference page and in the configuration reference, with the measurement that justified it.
2. The Gate's documentation shall carry a contributors' note naming `undmcp` and `und ai`, what was measured about each on 2026-09-05, and the network-boundary constraint any contribution must respect, so that someone else can take them up.
3. The Gate's `doctor` documentation shall describe the per-feature availability rows of requirement 1.
