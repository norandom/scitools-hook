# Requirements Document

## Project Description (Input)

**Who has the problem.** Coding agents that write most of a repository's code, and the humans who review it. Agents reliably produce code that is longer than it needs to be: routines nothing calls after a replacement, parameters nothing reads, a routine whose only job is to forward to one other routine, an abstraction with a single implementation, a module that exports one thing to one importer, near-identical routines in several files, and comment volume that outgrows the code it explains. None of that trips a complexity limit, because every piece reads fine on its own.

**Current situation.** scitools-hook already measures complexity, structure and coupling at the commit boundary with the ratchet, and ships two rules in this direction: `structure.unused_routines` (reference-based, whole-project, off by default) and `structure.duplicate_definitions` (one value copied into many files). It has no opinion on single-caller layering, single-implementation abstractions, over-export, unused parameters or classes, code similarity between routines, comment volume beyond a *minimum* `RatioCommentToCode`, or whether a change made the project longer in logical lines of code. Ponytail (github.com/dietrichgebert/ponytail) states the agent-side discipline — the seven-rung ladder, the `delete:` / `stdlib:` / `native:` / `yagni:` / `shrink:` tags, the `net: -N lines possible` score line and worked examples of the shorter form — but it is a prompt plugin with no measurement behind it, and its `stdlib:` and `native:` rungs need semantics a structural database cannot supply.

**What should change.** Add a *lean-code* rule family to the gate that points an agent at code it should delete or shorten, built API-side on Understand's reference database and metrics so that one rule runs on every language Understand parses (CodeCheck is unlicensed on the development machine and is not a dependency):

1. **Dead code**, extending the existing unused machinery from routines to classes, module-level variables and parameters, with the same three-state answer (used / unused / not measured), the same warning default and the same ignore-pattern mechanism.
2. **Single-caller layering**: a routine with one project caller that forwards to one callee in at most two statements (`callby` refs, call edges, `CountStmt`).
3. **Single-implementation abstraction**: a class with exactly one derived class that nothing else references or instantiates (`CountClassDerived`, refs).
4. **Over-export**: a file defining one routine or class with exactly one importer.
5. **Code duplication (similarity)** between routines across files, measured from what Understand exposes (statement/token shape, metrics vectors, call signatures) rather than from a text diff, with a similarity threshold that is itself measured.
6. **Shrink signals** that are metric-only: a *maximum* on `RatioCommentToCode`, per-routine `CountLineComment`, and a verbosity ratio of `CountLineCode` over `CountStmt` (long without being complex).
7. **A net logical-lines-of-code delta per change** over the affected entities, reported in the human, JSON and SARIF output as a number an agent sees before staging — a report line in ponytail's `net:` form, not a blocking rule.
8. **Hints in ponytail's one-line tag form** (`delete:` / `yagni:` / `shrink:` — the measurable tags only), carrying the worked before/after examples ponytail uses so the agent learns the shorter form from the finding itself, and exposed through the existing hint catalogue and `agent-rules`.

Every rule ships **off or as a warning**, and every default limit is measured on this repository's own snapshot (2 799 entities on Understand Build 1262) before it ships, following the project's rule that a limit without a measurement behind it is not a decision. Rules are evaluated over the whole project and reported against the change's affected entities, as the unused rule does today.

## Introduction

`scitools-hook` (below: the Gate) is the maintainability gate specified in `.kiro/specs/maintainability-gate`, extended for Understand 8.0 in `.kiro/specs/understand-8-features`. This specification adds a family of **lean-code rules**: rules and signals whose finding is not "this is too complex" but "this should not exist" or "this should be shorter". They take the measurable half of ponytail's audit list (<https://github.com/dietrichgebert/ponytail>: dead code paths, single-caller layering, single-implementation abstractions, over-export) and answer each from Understand's reference database, so that one rule holds for every language Understand parses. They add duplication and similarity between routines, comment-volume and verbosity signals, and a net logical-lines figure per change. Their hints borrow ponytail's one-line tag vocabulary so that the finding itself teaches the shorter form.

Two things ponytail cannot do and the Gate can: decide "unused" over a whole project rather than a diff, and subtract a real before side. Ponytail's own scoreboard refuses to print a per-repository savings number because "the unbuilt version was never written, so there is no real baseline". The Gate has one.

Terminology used throughout, in addition to the base specification's:
- **Lean-code rule**: any rule in this specification. Each is a structural rule (`structure.<name>`) or a threshold, evaluated over the whole project and reported against the change's affected entities, as `structure.unused_routines` is today.
- **Logical lines (LLOC)**: statements rather than source lines. Understand's `CountStmt`, which is what ponytail's `net: -N lines` measures once formatting is taken out.
- **Project reference**: a reference to an entity from a file inside the repository's included set. References from a stub, a vendored file or an excluded path are not project references.
- **Pass-through routine**: a routine with exactly one project caller whose body does nothing but call one other routine and return, within a small statement budget.
- **Single-implementation abstraction**: a class with exactly one derived class in the project, referenced by nothing in the project except that derived class.
- **Over-exporting file**: a file that defines exactly one routine or class and is depended on by exactly one other file.
- **Exact duplicate**: a run of consecutive lines, whitespace and comments removed, that occurs at more than one location in the project, at least `min_lines` long.
- **Similar routines**: two routines whose token sequences, with identifiers and literals normalised, agree above a similarity threshold, both at least `min_statements` long.
- **Net LLOC delta**: the sum over the change's affected entities, added and deleted entities and deleted files included, of after-side `CountStmt` minus before-side `CountStmt`.
- **Ponytail tags**: `delete:` (remove entirely, nothing replaces it), `yagni:` (an abstraction, layer or option with one user), `shrink:` (same logic, fewer lines). `stdlib:` and `native:` are ponytail's other two tags; the Gate never emits them.

## Boundary Context

- **In scope**: dead-code findings for parameters, classes and module-level variables, alongside the routine rule that exists; pass-through routines; single-implementation abstractions; over-exporting files; exact duplicates and similar routines; a maximum on comment volume, a per-routine comment-line limit and a verbosity ratio; a net LLOC delta per change in every output format; hints in ponytail's tag form with worked examples; the lean-code section of the agent-rules snippet, and the four shipped skills updated to use it; `doctor` rows and feature refusal for what the installed build cannot do; a measured default for every rule, recorded in the design; documentation.
- **Out of scope**: `stdlib:` and `native:` findings (whether a routine re-implements a library is a semantic question and stays with the agent); applying any fix; detection through Understand CodeCheck (unlicensed on the measuring machine and not a dependency of any rule here); semantic clones, i.e. two routines that compute the same thing with different token shapes; duplication across repositories; changing the default severity of any rule that exists today; ponytail itself as a dependency (its vocabulary is borrowed, its files are not read).
- **Adjacent expectations**: Understand provides the entity kinds, references, metrics and token streams the rules read, with the semantics its shipped documentation describes; where the installed build ships a duplicate-lines metric, the Gate may use it and says so in `doctor`, and where it does not the Gate computes exact duplicates itself; git provides the before side as today; the base specification's affected-entity resolution, ratchet, severity map, ignore mechanism, scope overrides and output formats are used unchanged. The Gate owns none of these.

## Requirements

### Requirement 1: Dead Code Beyond Routines
**Objective:** As a reviewer of agent-written code, I want a change that leaves a parameter, a class or a module-level variable that nothing in the project uses to be reported, so that dead code an agent forgot to delete is visible in every shape it takes, not only as a routine.

**Amended 2026-09-10, from measurement.** On a 417-file, ~101 800-line codebase whose analysis resolves at 26%, the predicate as originally specified answers 830 routines and about 6160 lines, and is wrong nearly every time: that codebase uses structural typing, so an implementation holds no reference to the interface it satisfies, and exactly one of the 830 carried an override reference. Criteria 8, 9 and 10 exist because of that measurement. A rule that cannot tell "nothing uses this" from "the analyser could not see what uses this" is a machine for deleting working code.

#### Acceptance Criteria
1. When a lean-code dead-code rule is enabled and a check runs, the Gate shall report each affected class and each affected module-level variable that has no project reference, as a structural finding with the entity's location and a remediation hint.
2. When the unused-parameter rule is enabled and a check runs, the Gate shall report each parameter of an affected routine that the routine's body never references, as a structural finding located at the routine, naming the parameter.
3. The Gate shall decide "unused" for each of these over the whole project, never over the affected neighbourhood alone, so that an entity used from an unchanged file is not reported.
4. The Gate shall not report a parameter that a routine declares because a signature it overrides or implements declares it, and shall not report a language's implicit receiver parameter, so that conformance to an interface is never reported as dead code. The Gate shall apply this whether the interface is declared by inheritance or satisfied structurally, and shall not require an inheritance or override reference to exist, because a structurally typed implementation holds no reference at all to the interface it satisfies.
5. The Gate shall ship each of these rules off, and when enabled as a warning by default, and shall accept an ignore list of name patterns per rule applied the way `structure.unused_ignore` is, with a shipped list covering the shapes a reference cannot see (dunder members, test collection, entry points, decorator-registered handlers, unused-by-convention parameter names such as a leading underscore).
6. If a run cannot measure references for one of these rules, the Gate shall report that rule once per run as unavailable and shall evaluate nothing for it, so that an analysis recorded before the rule was enabled never reports a project full of dead code.
7. When an entity is deleted by the change, the Gate shall not report it under any of these rules.
8. While EITHER the measured call resolution for a language OR the measured accuracy of the analysis is below its configurable floor, the Gate shall report no dead-code finding for entities of that language, and shall say once per run that the rule was not evaluated, which floor stopped it, and the measured value, so that an analysis which resolved too little to know is never reported as a project full of dead code.
   **Amended 2026-09-10, and the amendment is the point.** The original criterion named call resolution alone, and its supporting measurements were quoted as call resolution when they were analysis accuracy: this repository measures 43% call resolution and 19% accuracy, and the two answer different questions. They also bound different failures. Call resolution bounds whether the Gate knows what calls what, which is the failure behind the 830 routines on a structurally typed codebase. Accuracy bounds whether a file was read at all, which is the failure behind every one of the sixteen module bindings this repository reports as unreferenced while they are read, because their use sites sit in regions the analysis errored on. A dead-code claim needs both to hold: that the file was read, and that references from it were resolved. Neither floor alone covers the other's evidence.
9. Where a method name is declared by two or more classes in the project, the Gate shall treat it as an interface method and shall not report it, or any parameter it declares, as unused, so that a structurally typed implementation is excluded without an inheritance edge to detect it by.
10. The Gate shall record, for each shipped dead-code rule, the count it produces on at least two real repositories of different size before that rule ships enabled by default, so that a rule whose findings are dominated by the analyser's resolution rather than by the code is identified before an operator meets it.

### Requirement 2: Pass-Through Routines
**Objective:** As a reviewer, I want a routine whose only job is to forward one call to one caller to be reported, so that layering that adds a name and a file without adding behaviour is visible.

**Amended 2026-09-10, from the snapshot's own contract, and the amendment is the point.** Criterion 1 asked the finding to name **the caller and the callee**. The snapshot records the caller as a *count* (`LeanFacts.callers`, an integer) and the callee as a *name* (`forwards_to`, a long name), so no rule can recover a caller's name that was never kept. Task 3.1's review found it and task 4.2 settled it: **the finding names the callee alone**, and criterion 1 below says so. Three measurements decided that rather than the cost of carrying a sixth fact.

- **The caller is not part of the remedy the finding states.** Requirement 8.1 asks each hint to say what to cut and what replaces it in one line: the routine is what to cut, and its single callee is what replaces it. The caller is the *edit site*, which an agent reaches from the routine's own long name — and reaching it by search is stronger evidence than the field would have been, because a search over the long name finds the call sites the database does not.
- **A named caller would be a claim this rule may not make.** `understand/worker_lean.py::_project_callers` counts distinct project **routines**, and Python records a call made outside any routine against the *file*, which is not a routine. A routine called once from another routine and once from module scope therefore measures one caller and satisfies criterion 1 — the error task 3.1 recorded and accepted — so a finding naming that one routine as "the only caller" would be wrong about the one thing it asserted. A count says "one project routine calls this", which is what was measured.
- **Carrying the name would not have fixed that error**, contrary to the reading that sent this decision here. `callers` and a caller's long name would come from the same filtered list, so the module-scope call site is invisible either way: the undercount lives in *what counts as a caller*, not in count-versus-name. Widening it — counting call sites rather than caller routines — changes criterion 1's own predicate and needs its own measurement, which requirement 2.6's floor work (task 6.4) owns.

#### Acceptance Criteria
1. When the pass-through rule is enabled and a check runs, the Gate shall report each affected routine that has exactly one project caller and whose body consists of a single call to one other project routine, with at most a configurable number of statements (default measured, no more than 2), as a structural finding naming the callee it forwards to. A **project caller** here is a project routine: a call made from module scope is recorded against the file and is not counted, which is the accepted undercount the amendment above records.
2. The Gate shall not report a routine merely for having one caller: a routine with one caller and a body of its own is a decomposition the Gate's own hints ask for, and shall never be reported by this rule.
3. The Gate shall not report a routine whose single caller is outside the project's included set, a routine that is an override or an interface implementation, or a routine matching the rule's ignore list, and the shipped ignore list shall cover entry points and test functions.
4. The Gate shall ship the rule off, and when enabled as a warning by default.
5. If the call references needed to count callers were not recorded in the snapshot, the Gate shall report the rule once per run as unavailable and shall evaluate nothing for it.
6. While the measured call resolution for a language is below the same floor requirement 1.8 names, the Gate shall report no pass-through finding for routines of that language and shall say so once per run, because a caller count taken from a partly resolved call graph understates callers and an understated count is what this rule reports on.

### Requirement 3: Single-Implementation Abstractions
**Objective:** As a reviewer, I want a base class or interface that exists for exactly one implementation to be reported, so that an abstraction introduced for a second implementation that never came is visible.

#### Acceptance Criteria
1. When the single-implementation rule is enabled and a check runs, the Gate shall report each affected class that has exactly one derived class in the project and no project reference other than from that derived class, as a structural finding naming the derived class.
2. The Gate shall evaluate the rule when either the base class or its single derived class is affected by the change, so that the commit that adds the first (and only) implementation is the one told about it.
3. The Gate shall not report a class that has no derived class, a class with two or more derived classes, or a class referenced from anywhere in the project other than its derived class, and shall accept an ignore list of class-name patterns.
4. The Gate shall ship the rule off, and when enabled as a warning by default.

### Requirement 4: Over-Exporting Files
**Objective:** As a reviewer, I want a file that exists to hold one definition for one importer to be reported, so that an agent that answers "fewest files possible" with one file per function is told so.

#### Acceptance Criteria
1. When the over-export rule is enabled and a check runs, the Gate shall report each affected file that defines exactly one routine or class, no other module-level definition, and is depended on by exactly one other file in the project, as a structural finding naming the depending file.
2. The Gate shall not report a file that nothing depends on (that is the dead-code rule's finding), a file that more than one file depends on, a package or module initialiser, or a file matching the rule's ignore list.
3. The Gate shall ship the rule off, and when enabled as a warning by default.

### Requirement 5: Duplication and Similarity
**Objective:** As a reviewer, I want a change that adds a copy of code the project already has, or a routine that is a renamed twin of another, to be reported with the location of the original, so that the agent consolidates instead of copying.

**Amended 2026-09-10, from measurement.** Reporting twins as PAIRS was the wrong shape. Measured on a 417-file, ~101 800-line codebase: at the specified threshold of 0.9 there are 69 similar pairs, and clustering the same data into connected components gives 44 families covering 98 routines. Lowering the threshold to 0.8 gives 81 families over 224 routines, about 2144 lines, more than double what pairs at 0.9 find. The families are recognisable as families, not as coincidences: twelve `normalize` methods, one per data provider, 180 lines between them. Sixty-six pairwise findings about twelve routines is noise; one finding saying "these twelve are one routine with a parameter" is a task. This is also the shape a model actually produces bulk in, one variant at a time without ever seeing the family, so it is the rung of the ladder most worth automating.

#### Acceptance Criteria
1. When the exact-duplicate rule is enabled and a check runs, the Gate shall report each affected file containing an exact duplicate of at least `min_lines` lines, as a structural finding giving the file's line range and the other locations of the same lines, up to a fixed number of locations named.
2. When the similar-routine rule is enabled and a check runs, the Gate shall group every project routine of at least `min_statements` statements into families by transitive similarity above the threshold, and shall report one finding per family that an affected routine belongs to, naming the family's other members, their locations, its size, and the lowest similarity holding it together, so that a family of twelve is one finding rather than sixty-six.
3. The Gate shall decide duplication and similarity over the whole project and report only against affected entities, so that a commit that adds the third copy is told about the other two and a commit touching none of them is told nothing.
4. The Gate shall treat identifiers and literals as interchangeable when measuring similarity and shall treat whitespace and comments as absent for both rules, so that a renamed or re-indented copy is still a copy.
5. The Gate shall make `min_lines`, `min_statements` and the similarity threshold configurable, shall ship each rule off and as a warning when enabled, and shall accept an ignore list of path patterns for each so that fixture and generated directories can be excluded without a scope override.
6. Where the installed build offers a duplicate-lines metric, the Gate shall accept that metric as a file and project threshold like any other metric, shall refuse it at the architecture scope at configuration time with a configuration error naming the scope, and `doctor` shall report whether the metric is offered.
   **Amended 2026-09-11, from measurement, by task 5.6, an approved decision task.** As first written this criterion read: "the Gate shall accept that metric as a file, architecture and project threshold like any other metric, and `doctor` shall report whether it is offered." Task 1.3 found that architecture nodes carry no metrics: `understand.snapshot._element_metrics` keeps a threshold only for the `routine`, `class` and `file` scopes, `_population_metrics` excludes `arch` by name, and the worker walks the same three scopes, so an architecture-scope threshold on any metric was accepted by validation and then never requested. Measured on 2026-09-11 before the guard: a `DuplicateLinesOfCode` ceiling at arch scope produced no finding and one `reducer_failures` note (`the arch population of DuplicateLinesOfCode is empty`), and `scitools-hook config` on that file exited 0. An accepted-but-never-evaluated threshold is the silent no-op this project refuses everywhere, so task 5.6 chose to refuse the scope for every metric rather than to extract architecture metrics: a useful architecture finding must name the node, which needs arch records in the snapshot, the evaluator, the ratchet and the affected set; `understand/worker.py` stands at its 130-function ceiling, so the walk has nowhere to live; and every contract test skips without a licensed install, so the extraction branch's acceptance (a finding on the contract project) was unmeasurable. The refusal is `config/validate.py::_check_threshold`, exit 2, with `NO_ARCH_METRIC_HINT` carrying the reason; the measurement and the decision are recorded in `tasks.md` task 5.6 and its implementation note, and `config.models.PathScope` already refuses an `arch` table, so no accepted-and-ignored architecture threshold remains. The file and project scopes are served as the original criterion asked, the project scope through the population path `CorePercentage` already takes.
7. The Gate shall record in the design, before either rule ships a default, the number of findings each produces on this repository at candidate settings and which of them a reviewer judged genuine, and shall choose the default from that measurement.
8. If a file's token stream cannot be read, the Gate shall treat the file as contributing no duplicates and no similarity, shall say so once per run, and shall not report the file as a duplicate of anything.
9. The Gate shall accept a minimum family size, defaulting to two so that a plain twin is a family of two, and shall make the family threshold configurable independently of the exact-duplicate settings, because the measurement shows the useful threshold for families is lower than for pairs.
10. The Gate shall accept an ignore list of routine-name patterns for the family rule, and shall ship it covering the shapes a family is idiomatic rather than duplicated, so that per-class constructors, validators and framework hooks are not reported. Measured on the same codebase: of the 224 routines in families at 0.8, thirty are `__post_init__` validators across unrelated data shapes, which are one shape per class on purpose and must not be merged.
11. The Gate shall report a family against every affected routine that belongs to it exactly once per run, never once per member, so that a change touching three members of one family produces one finding and not three.

### Requirement 6: Shrink Signals
**Objective:** As a reviewer, I want code that is long without being complex, and comments that outgrow the code they explain, to be reported, so that verbosity is visible as a number and not only as a feeling.

#### Acceptance Criteria
1. The Gate shall accept a maximum on the file comment-to-code ratio alongside the minimum it accepts today, so that both the under-commented and the over-commented file can be reported, and shall ship no maximum on by default until one is measured.
2. The Gate shall offer a per-routine comment-line count as a threshold metric, evaluated like any routine threshold and covered by the ratchet.
3. The Gate shall offer a per-routine verbosity ratio, source lines per statement, as a threshold metric, evaluated like any routine threshold and covered by the ratchet, and shall not evaluate it for a routine below a configurable minimum number of statements so that a three-line routine cannot trip it.
4. The Gate shall report each of these signals as a warning by default, so that none of them blocks a commit until an operator chooses a severity.
5. The Gate shall record in the design how each of these metrics treats a language's documentation comment (a Python docstring counted as comment or as code, for instance), measured on the installed build, before a default is proposed.
6. If a shrink metric is unavailable for a language, the Gate shall report it as unavailable for that language once per run, the way any threshold metric is reported today.

### Requirement 7: The Net LLOC Delta of a Change
**Objective:** As a coding agent checking my own work, I want to see whether my change made the project longer or shorter in logical lines, so that the smallest working diff is something I can measure rather than claim.

#### Acceptance Criteria
1. When a check has a before side, the Gate shall compute the net LLOC delta of the change and shall print it in the human summary, include it as a field in the JSON output, and record it as a run property in the SARIF output.
2. The Gate shall count deleted entities and deleted files as negative contributions and added entities as positive ones, so that a replacement that deletes more than it adds shows as a reduction.
3. The Gate shall print the delta in the form `net: +N lloc` or `net: -N lloc`, with the source-line delta beside it, and shall print the number of affected entities it was summed over.
4. While a check has no before side (`--all`), the Gate shall omit the delta rather than print zero.
5. The Gate shall never block on the delta by default; where an operator configures a maximum net growth per change, the Gate shall report a finding at the configured severity when the delta exceeds it.
6. When lean-code rules are enabled, no lean-code finding is raised and the delta is zero or negative, the Gate shall say so in one line of the human summary.

### Requirement 8: Hints and Agent Guidance in Ponytail's Form
**Objective:** As a coding agent, I want each lean-code finding to tell me what to cut and what replaces it in one line, with a worked example of the shorter form, so that I learn the pattern from the finding rather than from a lecture.

#### Acceptance Criteria
1. The Gate shall give every lean-code finding a hint that begins with one ponytail tag, `delete:`, `yagni:` or `shrink:`, followed by what to cut and what replaces it, in one line.
2. The Gate shall carry, for every lean-code rule, one worked before-and-after example of the shorter form in the hint catalogue, shown in the verbose human output and in the JSON output beside the hint, and drawn from or in the style of ponytail's published examples.
3. The Gate shall never emit the `stdlib:` or `native:` tag, and the agent-rules snippet shall say so and shall tell the agent to apply those two rungs of the ladder itself.
4. When `agent-rules` is generated, the Gate shall include a lean-code section that lists every lean-code rule that is enabled with its severity and the tag its findings carry, the net LLOC delta and what to do with it, and the seven-rung ladder in one line each.
5. When `agent-rules` is generated, the Gate shall list every enabled structural rule, including `unused_routines`, `duplicate_definitions`, `call_cycles` and `reachable_complexity`, which the snippet omits today.
6. The Gate shall let an operator override any lean-code hint or example through the existing hints configuration, at the rule level.
7. The Gate shall update the four shipped skills: `scitools-gate` reads the net LLOC delta and lean-code findings before staging, `scitools-improve` gains a step that works the lean-code findings largest-reduction-first, `scitools-adapt` covers the ignore lists and thresholds of this family, and `scitools-onboard` proposes lean-code rules from measurement without enabling one blindly.

### Requirement 9: Measured Defaults, Availability and Cost
**Objective:** As an operator, I want every lean-code rule to ship off or as a warning with a measurement behind its default, to be refused when the installed build cannot answer it, and to cost nothing when it is off, so that the family follows the same discipline as every rule before it.

#### Acceptance Criteria
1. The Gate shall ship every lean-code structural rule off and every lean-code threshold as a warning, and shall record in the design, for each default value proposed, the count it produces on this repository's whole-project snapshot and the measurement that justifies it.
2. When `doctor` runs, the Gate shall print one row per lean-code capability the installed build must offer (call and use references for the dead-code and pass-through rules, token streams for duplication and similarity, the optional duplicate-lines metric) saying `available`, `not on this build` or `unverified` with the reason.
3. If the configuration enables a lean-code rule the installed build cannot answer, the Gate shall stop with a configuration error naming the rule, the build and the configuration key, as it does for the Understand 8.0 features today.
4. While every lean-code rule is off, the Gate shall extract nothing on their behalf, so that a warm check costs what it costs today.
5. When lean-code rules are enabled, the Gate shall record in the design the measured added cost of a warm check on this repository with every lean-code rule enabled, and that cost shall not exceed one half of today's warm check.
6. The Gate shall apply scope overrides, the severity map, `[ignore]` and the ratchet to lean-code rules exactly as to existing rules, so that an operator learns nothing new to configure them.
7. The Gate shall evaluate every lean-code rule from Understand's references, metrics and token streams alone, and shall not require a CodeCheck licence for any of them.

### Requirement 10: Documentation
**Objective:** As an operator or agent reading the reference, I want the lean-code family documented where the other rules are, with the measurement behind each default, so that a finding is never a surprise.

#### Acceptance Criteria
1. The Gate shall document every lean-code rule in the rules reference with its name, what it reports, what it deliberately does not report, its default and the measurement behind it.
2. The Gate shall document the configuration keys of the family in the configuration guide, with the template excerpt each produces.
3. The Gate shall add one row per lean-code capability to the feature list, saying whether it ships on or off.
4. The Gate shall document, with the reasoning, why `stdlib:` and `native:` are not findings and what an agent is expected to do about them.
5. The Gate shall document the blind spots of reference-based detection for parameters, classes and variables per language, measured on the contract project, alongside the routine rule's.
