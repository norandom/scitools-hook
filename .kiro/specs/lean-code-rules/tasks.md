# Implementation Plan

- [ ] 1. Foundation: configuration, models, metric declarations, the floor guard, and the test seams every later group needs
- [x] 1.1 Add the `[lean]` configuration section with every rule off
  - One settings section holding the nine rule switches as severity-or-off, their ignore lists with the shipped defaults from the design (receivers and underscore-prefixed parameters, error and exception classes, module idioms, initialiser paths), the three numbers of the duplication rules, the pass-through statement budget and the optional net-growth maximum, with the same validation the existing ignore lists get
  - Two derived answers on the section saying whether any reference rule and whether any token rule is on
  - The analysis fingerprint changes when either derived answer changes, and its definitions key also turns on when over-export is on
  - Done when a configuration naming every new key validates, a configuration without the section produces effective settings identical to today, and the fingerprint test shows a change when one reference rule is switched on
  - _Requirements: 1.5, 2.4, 3.4, 4.3, 5.5, 7.5, 9.4_

- [x] 1.2 Extend the shared models for lean facts, the token index, the net delta and the nine rule names
  - Per-entity lean facts as an optional record (callers, callees, forwards-to, overrides, unused parameters, referenced, derived, referrers), an optional referenced flag on a module-level definition, an optional token index on the snapshot (vocabulary, per-file line hashes with line numbers, per-routine shapes with ranges, unreadable files), a net delta value (statements, lines, routines) on the run result, two request keys for references and tokens, and three feature members
  - The nine structural rule names become legal for severities, SARIF ids and hints; the JSON schema version stays at 2
  - Done when every model round-trips through JSON with the new fields absent and present, an old snapshot document still validates, and a test asserts the schema version is unchanged
  - _Requirements: 1.6, 2.5, 5.8, 7.1, 9.2, 9.6_

- [x] 1.3 Declare the shrink metrics, the floor, and the duplicate-lines plugin metrics
  - A synthetic verbosity metric on routines requiring code lines and statements, declared with a statement floor of five; the per-routine comment-line metric offered through the ordinary catalogue path; the two duplicate-lines metric ids declared as plugin metrics for file, architecture and project scope
  - Shipped defaults: verbosity 3.0 and comment lines 20 on routines, both as warnings; the file comment ratio keeps its minimum and accepts a maximum, with none shipped
  - The worker's synthetic answers the ratio whenever statements are present and non-zero, beside the existing parameter-count synthetic
  - Done when the defaults table lists the two new routine thresholds as warnings, a configuration with a comment-ratio maximum validates, and a threshold on a duplicate-lines metric is accepted by validation when the catalogue offers it and refused when it does not
  - _Requirements: 5.6, 6.1, 6.2, 6.3, 6.4, 6.6_
  - _Boundary: config metric declarations, worker synthetics_

- [x] 1.4 Apply the floor in the threshold evaluator and the ratchet, and make it configurable
  - An entity below a metric's declared floor is judged on nothing for that metric: no finding, no unavailable record, no ratchet comparison
  - The minimum is configurable, because requirement 6.3 says it is: one key in the `[lean]` section beside the family's other numbers, defaulting to the declaration's five. Task 1.3's review caught that the design had fixed it as a constant, which would have shipped the requirement unsatisfied
  - Add the sentence task 1.3 could not: with the floor applied, `recommend` on this repository should return `keep` for the verbosity default rather than the `raise 3 -> 4` an unfloored run reports, and the recorded measurement should say which population it describes
  - Done when a routine with four statements and a ratio of 8 raises no finding and no unavailable entry, one with five statements does, a ratchet comparison between a below-floor before and an above-floor after is skipped, a configured minimum of two makes the four-statement routine judged again, and `recommend` on this repository no longer proposes raising the verbosity default
  - _Requirements: 6.3_

- [x] 1.5 Give the test suite a lexer fake and the second worker file a place in the import-direction rules
  - A lexer and lexeme fake beside the existing API fakes, with token class, text and line, and a lexer method on the entity fake that raises for a file marked unreadable
  - An empty measurement module at the sibling path with the zero-import allowance, the parse test and an isolated-interpreter load test copied from the worker's
  - Serves the design's allowed-dependency rule for the worker sibling; no acceptance criterion of its own
  - Done when the import-direction suite passes with the new entry and the lexer fake serves a first unit test

- [x] 1.6 Plant the Python contract cases
  - In the contract project's Python sources: an unused parameter, an unused class, an unused module variable, a pass-through routine with one caller, a base class with one derived class and no other user, a file holding one definition for one importer, a twelve-line block copied into two files, and a routine that is a renamed twin of another
  - Done when the extended fixture builds on the licensed install and the counts it changes are listed in the task's commit message for task 1.7 to apply
  - _Requirements: 5.7, 9.1_

- [x] 1.7 Plant the C++ contract cases and update the existing count assertions
  - The same eight cases in the contract project's C++ sources where the language allows them; every existing contract test that asserts the fixture's entity, edge or definition counts is updated with the new count and the reason
  - Done when the extended fixture builds on the licensed install and the existing contract suite is green with the updated counts
  - _Requirements: 5.7, 9.1_

- [x] 1.8 Render the section in the `init` template
  - The template renders the `[lean]` section commented, one line per off rule with its default in the comment, in the style of the unused rule's line
  - Done when the rendered template round-trips through the loader to the same effective settings, and the template test names every lean key
  - _Requirements: 1.5, 10.2_

- [x] 1.9 Give the entity fake room before the tasks that need it
  - **Amended after the first attempt's review, and the amendment is the point.** The original wording forbade both raising the limit and recording a deviation, which forced an accounting move: splitting the fake into six bases took its coupling from 12 to 0 while every one of the twelve edges survived, merely reattributed to bases whose inheritance edges the metric does not charge. The tool's own fan-out rule said the same class got worse in the same run, which is what a metric being satisfied rather than answered looks like
  - The measurement that changes the decision: eight of the twelve couplings are `dict`, `int`, `list`, `object`, `range`, `str`, `tuple` and `pathlib.Path`. `CountClassCoupled` charges annotation types on Python, so a wide shallow dataclass reaches the limit on its field annotations. The fake has **four** real collaborators. This is the same finding `[scope.schemas]` already records for another region, where a metric counts schemas rather than coupling
  - So: record the honest deviation. A `[scope.api_fakes.thresholds.class]` ceiling on `CountClassCoupled`, carrying the enumerated twelve, the eight that are annotation types, and the note that the figure is not a count of design collaborators. Set a **ceiling, not `false`**, so growth past it still blocks, which is what every other scope block in this file does. Restore whatever method-count deviation the single class needs, with its own measurement
  - Keep the `bool` annotations on the lexer switches. Task 1.5 spent them to buy a coupling and they are correct against the documented API; they must not be traded back
  - Decide the six-class shape on readability alone or revert it, and either way remove every claim that it reduced coupling, in the fake's docstrings and in the configuration comment. If it is kept, its seven new ratchet warnings belong in the record
  - Done when the fake carries a recorded deviation with its measurement rather than an accounting shape, tasks 3.1 and 5.1 have room to extend it, every existing fake test passes unchanged, and a check on the worktree exits 0
  - _Requirements: none of its own; it unblocks 3.1 and 5.1_
  - _Boundary: tests/understand/api_fakes.py, scitools-hook.toml_

- [ ] 2. Rules that need no new extraction, and the net delta
- [x] 2.1 (P) The over-export rule from today's snapshot
  - A file defining exactly one routine or class, holding no other module-level definition, and depended on by exactly one project file is reported against the affected file, naming the dependant; initialisers and ignored paths are excluded; a file nothing depends on is not this rule's finding
  - Done when unit tests cover the finding, the initialiser exclusion, the zero-dependant case and the two-dependant case, and the rule is off by default
  - _Requirements: 4.1, 4.2, 4.3_
  - _Boundary: analysis/lean/layering_

- [x] 2.2 (P) The net delta of a change and the optional growth finding
  - Over every routine of an affected or deleted file, on either side, paired by key and then by signature family through the ratchet's existing pairing helper, which the design names as this rule's one dependency inside the analysis layer: statements and code lines after minus before, a missing side counting as zero; no before side means no delta
  - When a maximum net growth is configured, a project-scope finding at the configured severity when statements exceed it
  - Done when unit tests show a deleted file counting negative, a renamed signature paired rather than counted twice, a whole-project run answering no delta, and the growth finding raised only past the limit
  - _Requirements: 7.1, 7.2, 7.4, 7.5_
  - _Boundary: analysis/lean/net_

- [x] 2.3 (P) Tag-form hints and one worked example per rule
  - A hint per lean rule beginning with `delete:`, `yagni:` or `shrink:`, stating what to cut and what replaces it in one line; an example per rule under the rule's example key, in ponytail's format, drawn from its published examples where one fits
  - The similar-routine rule has two entries: the rule key carries `delete:` and its same-file variant carries `shrink:`, selected through the catalogue's existing variant lookup; the net-growth rule carries `shrink:`
  - The catalogue answers an example by rule name, and an operator's hints table overrides the hint and the example at the rule level
  - Done when a test asserts every lean rule's hint begins with one of the three tags and never with `stdlib:` or `native:`, every lean rule has an example, and an override replaces both
  - _Requirements: 8.1, 8.2, 8.3, 8.6_
  - _Boundary: Hints and examples_

- [x] 2.4 (P) The lean section of the agent-rules snippet, and the four missing structural rules
  - A section listing every enabled lean rule with its severity and tag, the two tags the Gate never emits and that the agent applies itself, the seven rungs one line each, and how to read the net line
  - The structure section names the unused-routine, duplicate-definition, call-cycle and reachable-complexity rules
  - Done when the rendered snippet with every rule on contains all nine lean rules and all ten pre-existing structural rules, and with every rule off contains the ladder and the two-tag note only
  - _Requirements: 8.3, 8.4, 8.5_
  - _Boundary: Reports_

- [x] 2.5 The net line, the lean-already line, the example in verbose output and the SARIF run property
  - The human summary prints `net: +N lloc (+M lines) over K routines` with either sign whenever a delta exists; when a lean rule is on, no lean finding was raised and the delta is at or below zero, one line says there is nothing to cut; verbose output prints a finding's example under its hint; SARIF carries the delta as a run property; JSON carries it as a field
  - Done when unit tests cover both signs, the absent delta, the lean-already line, the example under the hint, and the run property in the SARIF document
  - _Depends: 2.2, 2.3_
  - _Requirements: 7.1, 7.3, 7.6, 8.2_

- [x] 2.6 The lean step of the check pipeline
  - One step that runs each lean rule whose severity is set, gathers their unavailable messages, computes the delta, and hands findings, notes and delta to the pipeline; findings go through scopes, ignores, the severity map and hints like any structural finding; notes are printed once per run; the delta lands on the run result; a rule that is off adds nothing
  - The extractor asks for module-level definitions whenever the over-export rule is on, so the rule has them without a second request. **Found in task 2.1's review and it is a live gap, not tidiness:** the fingerprint already keys on the disjunction, so a stale snapshot cannot be served, but the extractor still sets `include_definitions` from the duplicate-definitions setting alone. With over-export on and duplicate definitions off, a FRESH snapshot carries no definitions, the rule's module-level-binding guard sees nothing, and every candidate file with a constant beside its one routine becomes a false positive. Land it with a test that an over-export-only configuration produces a request with definitions on
  - The pipeline's finishing step attaches the catalogue's example to every lean finding's details, beside the hint it already attaches
  - **From task 2.2's review:** the net-growth finding carries a project scope with an EMPTY path, matching how the accuracy finding is built. Confirm with a test that an empty path survives the `[ignore]` patterns and the scope overrides in the finishing step: a path matcher meeting an empty string is exactly where a rule silently disappears or matches everything
  - Done when a pipeline test with a fixture snapshot shows over-export and the delta in the run result, the example in the finding's details, and an all-off configuration producing a run result identical to today's **apart from the net delta**. Task 2.4's review settled that conflict: requirement 7.1 prints the delta whenever a check has a before side and attaches no condition about the lean rules, while requirement 7.6 scopes itself explicitly with "when lean-code rules are enabled", which shows the author scoping by enablement where they meant it. The delta needs no extraction of its own, so requirement 9.4's cost rule does not bar it either
  - _Depends: 2.1, 2.2, 2.3, 2.5_
  - _Requirements: 1.6, 2.5, 7.1, 9.4, 9.6_
  - _Boundary: runner/lean, CheckPipeline, Snapshot request and feature probes_

- [ ] 3. Reference-based measurement in the worker sibling
- [x] 3.1 Routine facts: callers, callees, forwarding target, overrides and unused parameters
  - Distinct project routines calling and called by a routine, the callee's long name when there is exactly one, whether the routine overrides, and the names of parameters no project reference uses, sets or modifies
  - Done when unit tests over the API fakes show a parameter used only through a set counted as used, a receiver counted as unused by the worker (the exclusion is the rule's), a caller in an excluded path not counted, and an overriding routine flagged
  - _Requirements: 1.2, 1.3, 1.4, 2.1, 2.3, 9.7_
  - _Boundary: worker_lean_

- [x] 3.2 Class facts and module-variable use
  - For a class: whether anything in the project references it, the long names of its derived classes across the inheritance kinds of every language, and the count of project entities referencing it other than itself, its members, its derived classes and their members; for a module-level variable: whether any project reference uses it, including a use as a type
  - Done when unit tests show a class used only in an annotation counted as referenced, a base with one derived class and no other referrer, and a variable read from another module counted as used
  - Also record, per method name, how many project classes declare it. A name declared on two or more classes is an interface method under structural typing, which is the only way to see one when no inheritance edge exists (requirement 1.9). Measured on a 417-file codebase: of 830 naive dead-code candidates exactly one carried an override reference, so inheritance alone detects nothing there
  - _Requirements: 1.1, 1.3, 1.9, 3.1, 9.7_
  - _Boundary: worker_lean_

- [x] 3.3 Load the sibling from the worker and record the facts in the snapshot
  - The plan carries the two request keys; the sibling is loaded by path once per process and only when a key is set; every routine and class record carries its facts when references are asked, module definitions carry their referenced flag, and nothing is loaded or recorded otherwise; the snapshot cache digest covers both files
  - The extractor asks for references when any reference rule is on, extending the request plan task 2.6 introduced
  - **From task 3.2, and it needs a name nothing has given it yet:** the declaring-class tally that requirement 1.9 depends on is a project-wide fact, not a per-entity one — two classes declaring one method name is a fact about the pair, and neither class can see it. It does not fit the per-entity lean facts and needs ONE project-wide snapshot field of its own, beside the token index. Name it here, record it in the design's model block, and task 4.1 reads it to apply the interface-method exclusion
  - Done when a worker test with references off shows no sibling load and no lean key, with references on shows facts on every record and definition, and the cache key changes when the sibling's source changes
  - _Depends: 3.1, 3.2_
  - _Requirements: 1.6, 2.5, 9.4_

- [ ] 4. The reference rules
- [x] 4.1 (P) Dead parameters, classes and module variables
  - Three rules over the after side: a parameter of an affected routine that the routine never references, located at the routine and naming the parameter, unless the routine overrides or the name matches the ignore list; an affected class nothing references unless ignored; an affected file's module variable nothing references unless ignored; a missing fact on any affected record yields the rule's unavailable message and nothing else; a deleted entity cannot appear
  - Done when unit tests cover each finding, the override exclusion, the receiver exclusion by default ignore, the unavailable message, and an unreferenced class in an unaffected file not reported
  - **Measured on this repository during task 3.3's review, and it is the strongest evidence the floor exists for:** every one of the 16 module bindings in `src/` that the snapshot answers `referenced: false` for is in fact read. Understand recorded no use reference for them because the use sites sit inside regions its analysis errored on, and a controlled two-file probe shows the same constants resolve correctly in a clean parse. That is a **100 per cent false-positive rate** for the unused-variable rule on this repository at this resolution. Requirement 10.5's blind-spot documentation needs it too
  - Apply the resolution floor before reporting anything: below it the rules report nothing and say why once, because a caller count from a partly resolved graph cannot tell "nothing uses this" from "the analyser could not see what uses this" (requirement 1.8). Apply the interface-method exclusion from task 3.2's declaring-class count, which is what catches structural typing where the override exclusion catches nothing (requirement 1.9)
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9_
  - _Boundary: analysis/lean/dead_

- [x] 4.2 (P) Pass-through routines and single-implementation abstractions
  - A routine with exactly one project caller, exactly one callee and a statement count within the budget, not overriding and not ignored, reported naming the callee it forwards to; a class with exactly one derived class and no other referrer, evaluated when it or its derived class is affected, reported naming the derived class; classes with no or several derived classes never reported
  - Done when unit tests show one caller with two callees not reported, a budget of two accepting a call-and-return body, the base reported on the commit that adds the only derived class, and a base with an outside referrer not reported
  - _Depends: 2.1_
  - The pass-through rule takes the same resolution floor: an understated caller count is exactly what it reports on (requirement 2.6)
  - **Settled 2026-09-10, and requirement 2.1 is amended in place to match.** The gap task 3.1's review handed here was that requirement 2.1 asked the finding to name the caller while the snapshot records a caller COUNT. The finding names the callee alone. Verified against `worker_lean.py::_project_callers`: the count is the length of a list already filtered to project routines, so a caller's long name would come off that same filtered list and would NOT have recovered the module-scope call site. Implementation Note 3.1's claim that carrying the callers themselves fixes the undercount is therefore wrong, and is corrected below. Widening what counts as a caller changes requirement 2.1's own predicate and belongs to task 6.4
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 3.1, 3.2, 3.3, 3.4_
  - _Boundary: analysis/lean/layering_

- [x] 4.3 Wire the reference rules into the lean step and refuse them where the build cannot answer
  - The lean step runs the five reference rules; the reference feature is recorded as available on every build, the five severity keys map to it, and the doctor row prints
  - **Requirement 1.8's floors are configurable and nothing owns them yet.** Add `lean.resolution_floor` and `lean.accuracy_floor` to the settings, each with its template line, since the template test is driven off the model's own field list. Task 4.1 could only take them as call-site arguments, so without this the requirement ships configurable in Python and nowhere else
  - Done when a pipeline test with a fixture snapshot carrying facts shows the five rules' findings and their notes, and the doctor output lists the row
  - _Depends: 2.6, 3.3, 4.1, 4.2_
  - _Requirements: 1.6, 2.5, 9.2, 9.3_

- [ ] 5. Token measurement and the duplication rules
- [x] 5.1 The token index in the worker sibling
  - Per project file: one hash per code line from lexeme texts with whitespace, comment, newline, indent and dedent tokens dropped, keeping original line numbers; per recorded routine with a start and an end reference in the same file: a shape mapping identifiers and literals to two classes and keeping keyword, operator and punctuation text, encoded through a vocabulary; a file whose lexer raises is listed as unreadable and contributes nothing
  - Done when unit tests over the lexer fake show the five token classes dropped, line numbers preserved, a renamed copy producing the same shape, a routine without an end reference absent, and an unreadable file listed
  - _Requirements: 5.4, 5.8, 9.7_
  - _Boundary: worker_lean_

- [x] 5.2 Record the index in the snapshot when a token rule is on
  - **Hard constraint, measured twice in task 3.3 and confirmed by its review:** `understand/worker.py` now stands at `CountDeclFunction` **130 against its ceiling of 130**. This task may add NO function to that file. Putting the index on the document is a statement inside the existing build routine and fits; anything needing a new function belongs in the sibling. Raising the ceiling is not available: it would be this tool adapting its own limits to a feature that measures limits
  - The extractor asks for tokens when either token rule is on, extending the request plan task 2.6 introduced; the worker runs the token pass after the entity walk and puts the index on the document; nothing runs otherwise
  - Done when a worker test with tokens off shows no index and with tokens on shows every recorded routine with an end reference in it
  - _Depends: 3.3, 5.1_
  - _Requirements: 5.8, 9.4_

- [x] 5.3 (P) The duplicate-block rule
  - Windows of the configured minimum length over line hashes indexed for the whole project; for each affected file, each maximal run of windows found elsewhere is one finding with the file's line range and up to three other locations; ignored paths contribute neither side; a missing index yields the unavailable message; unreadable files are noted once
  - Done when unit tests show a block in three files reported against the affected one with two locations, a block one line short not reported, an ignored path silent on both sides, and the note for an unreadable file
  - _Requirements: 5.1, 5.3, 5.5, 5.8_
  - _Boundary: analysis/lean/duplicates_

- [x] 5.4 (P) The similar-routine rule, reporting families rather than pairs
  - **Amended 2026-09-10 from measurement, see requirement 5's note.** Pairs were the wrong shape: 69 pairs at 0.9 on a real codebase are 44 families over 98 routines, and at 0.8 they are 81 families over 224 routines and about 2144 lines. One finding naming twelve members is a task; sixty-six pairwise findings about the same twelve are noise
  - **From task 2.3's review, and it fails silently if missed:** this rule must set `details["construct"]` to the literal `"same_file"` when every member shares a file, because the report layer sits above analysis and the constant cannot be imported. A misspelling raises nothing: the lookup falls through and prints the cross-file wording, telling an agent to delete one member of a family that should be merged. The report side now pins its half; this rule's own tests must assert the literal it writes
  - **Also reserved:** `example` is a variant name the hint catalogue uses for worked examples. No rule may set `details["construct"] = "example"`, or its hint lookup answers with the example instead
  - Union every scored pair at or above the threshold and report the connected components. One finding per family an affected routine belongs to, once per run and never once per member. Name the other members, their locations, the family size, and the lowest similarity holding the family together
  - A family size minimum defaulting to 2, so a plain twin still reports and nothing is lost. A name-pattern ignore list beside the path one, shipped covering idiom families: of those 224 routines, thirty are `__post_init__` validators across unrelated shapes, one per class on purpose
  - Done when a family of twelve produces one finding naming eleven others, a change touching three members of one family produces one finding rather than three, a family of two still reports, an idiom family is silent, and the whole-project scan over a thousand fake routines with one affected routine answers in well under a second
  - _Requirements: 5.2, 5.3, 5.4, 5.5, 5.9, 5.10, 5.11_
  - _Boundary: analysis/lean/similar_

- [x] 5.5 Wire the token rules into the lean step, probe the lexer and the duplicate-lines metric
  - The lean step runs both token rules; a lexer probe on the doctor's scratch database decides the token feature, a lookup of the duplicate-lines id decides the metric feature with a detail naming the Plugin Manager when absent; the two token keys map to the token feature; both rows print in `doctor`
  - Done when a pipeline test with a fixture index shows both rules' findings, the stubbed install can make each probe answer each state, and a configuration enabling a token rule on a build whose probe failed exits with the configuration error naming the key
  - _Depends: 2.6, 5.2, 5.3, 5.4_
  - _Requirements: 5.6, 5.8, 9.2, 9.3_

- [x] 5.8 Give the family rule the reach the requirement promises
  - **Found by task 5.5's review, with the measurement that makes it not a documentation fix.** The pipeline hands the lean step `narrow(wide_after, affected.files | affected.neighbourhood)`, which cuts `entities` to the change's files plus ONE dependency ring. The token index survives narrowing whole, but `similar._routine` reads each member's statement count and `EntityRef` off `entities` and drops any routine with no record, so the family rule runs half-blind while the block rule beside it reads the index alone and is correct
  - **Measured twice on this repository against a real whole-project index**, 6803 indexed routines, 2344 considered, at the **shipped** threshold of 0.9 and not the aggressive 0.8 the amendment discusses. The reproducible figure is a random sample of 60 single-file commits: a whole-project vertex set reports **41** families, the narrowed one-ring set still reports **26**, one of those shrunk, and **15 are lost outright -- 37%**. A second method over one real change agreed at 8 of 22, so 36% and 37% from two different family populations. The losses concentrate in cross-file families, which are the ones worth merging; `test_single_implementation.py` alone loses four. The sharpest single case: `analysis/lean/similar.py`'s own family of two shows one visible member, so **the similar-routine rule cannot see its own twin through this wiring**
  - Carry what the rule needs on `RoutineShape` so `similar._considered` needs no entity record, or widen what the step is handed. Either changes a snapshot contract tasks 5.1 and 5.2 shipped, which is why it is its own task and not a patch
  - **Also settle the cost, because it may be the same question.** Task 5.5's review measured `check --all` with `similar_routines` on and it did not finish in ten minutes on this 312-file project, against the rule's own 1000-routine benchmark of under a second. A rule that cannot complete a whole-project run cannot be measured by task 6.3 or shipped enabled
  - Done when a twin in a file the change neither touched nor depends on is named in the family, the count above is re-measured and recorded, `analysis/lean/similar.py`'s "decided over the whole project" claim is true, and a whole-project run completes in a time task 6.3 can use
  - _Depends: 5.5_
  - _Requirements: 5.2, 5.3, 9.5_

- [x] 5.9 (P) Delete the shingle index, which filters nothing and costs half the pass
  - **Measured twice in task 5.8 and once by its review, and the numbers are not close.** On this repository's whole-project index the candidate index offers the median routine 2341 of the other 2343, admitting 99.22% of pairs. It costs about 46% of the whole-project pass to remove 0.78% of them: 48.2s with it against 25.8s with `_candidates` scanning the considered map and no postings built, the SAME 161 findings both ways
  - The reason it was written no longer holds. Its docstring claimed candidate sets are small because routines do not all begin with the same eight tokens; on real Python they effectively do, because the shape vocabulary collapses every identifier and literal to one of two symbols, leaving an alphabet of about 61 in which a four-token run is the language's own punctuation. Every C-family language collapses the same way
  - It is also the rule's ONLY approximation, and the one the module spends forty lines bounding. Deleting it makes the rule lossless outright, deletes that reasoning and its exhaustive-search paragraph, and makes the pass faster. Nothing downstream loses: the guarantee gets stronger
  - Done when `_candidates`, `_postings`, `_shingles` and the bounded-approximation reasoning are gone, the whole-project run is re-timed and recorded, the findings are shown identical before and after on this repository, and the two exact bounds are the only filters left
  - _Depends: 5.8_
  - _Requirements: 5.2, 9.5_

- [x] 5.6 Decide what an architecture-scope duplicate-lines threshold means
  - Measured in task 1.3: the file scope is served end to end and the project scope is served through the population path `CorePercentage` already takes, but an architecture-scope threshold on any plugin metric is accepted by validation and then never requested from the worker, because architecture nodes carry no metrics. The hole is pre-existing and metric-agnostic, and requirement 5.6 names architecture, so this family may not inherit it silently
  - Take one of two answers and write the reason down: extract metrics for architecture nodes, or refuse an architecture-scope threshold at configuration time naming the metric and why. A threshold that is accepted and never evaluated is the silent no-op this project refuses everywhere else
  - Done when an architecture-scope duplicate-lines threshold either produces a finding on the contract project or exits 2 with a message naming the scope, and a test pins whichever was chosen
  - _Requirements: 5.6_
  - _Boundary: config validation and, if extraction is chosen, the worker's architecture walk_

- [x] 5.7 Make the plugin-metric budget measure a signal instead of noise
  - `tests/contract/test_plugin_metrics_contract.py`'s per-entity budget differences two wall-clock subprocess timings where the signal is 1 to 3 per cent of each run. Measured three times on the extended fixture: 0.269, 0.757 and 0.626 milliseconds per entity, from numerators of 6.5, 18.2 and 15.0 milliseconds against runs of about 550 milliseconds. It is measuring variance
  - It began passing during task 1.6 without being fixed, because the fixture grew and the denominator with it, raising the tolerated numerator from about 52 to about 96 milliseconds. A budget that passes because its denominator grew is weaker than it was, and this family's cost tests must not lean on it
  - Make the measurement signal-dominated rather than re-tightening the number, which would only make it flaky: repeat the read enough times to dominate process startup, or time the API call rather than the process
  - Done when the budget fails if the per-entity cost genuinely doubles, and passes repeatably otherwise, demonstrated by running it several times
  - _Requirements: 9.5_
  - _Boundary: tests/contract/test_plugin_metrics_contract.py_

- [ ] 6. Contract measurements on the licensed install
- [x] 6.1 Reference kinds and counts on the contract project
  - On the extended fixture: the Python and C++ inheritance kinds answer the derived class; overrides are flagged on both; the caller count agrees with the plugin caller metric for every routine of the fixture, and any disagreement is recorded with its cause; each reference rule reports its planted case and nothing else
  - **From task 3.1's review:** `setby`'s place in the parameter-use set is reasoned rather than measured, unlike `callby`, which the fixture forced. Measure whether Understand records a `Set Init` against a defaulted parameter's own declaration. If it does, every defaulted parameter reads as used and requirement 1.2 under-reports in silence, which is the quietest failure this family can have
  - Done when the contract test passes on Build 1262 and prints the per-language kind table into the research log
  - _Depends: 4.3_
  - _Requirements: 1.3, 1.4, 2.1, 3.1, 10.5_

- [x] 6.2 Token index coverage and the duplication cases
  - Every routine of the fixture with an end reference is in the index; the planted block and the planted twin are the only duplication findings at the shipped numbers; docstring accounting re-confirmed on the fixture's Python
  - Done when the contract test passes on Build 1262
  - _Depends: 5.5_
  - _Requirements: 5.4, 5.7, 6.5_

- [x] 6.3 Measure every default on this repository and the cost of a warm check with everything on
  - A whole-project run of this repository with every lean rule on, printing the count each rule produces at its shipped numbers and the first ten findings of each so they can be read; a warm check with one changed line, timed with every rule on and with every rule off
  - The counts and timings go into the research log as a dated table
  - Done when the research log carries the table and the cost test asserts the with-everything time within one half of today's warm check on this repository
  - Run the same measurement on a second, larger repository as well as this one. Requirement 1.10 asks for two of different size before any dead-code rule ships enabled, because a rule dominated by the analyser's resolution rather than by the code only shows up when the resolutions differ. facdrone resolves at 26% against this repository's 19% and is already measured read-only, so the comparison exists to build on
  - _Depends: 4.3, 5.5_
  - _Requirements: 5.7, 6.5, 9.1, 9.5, 1.10_

- [ ] 6.4 Adjust the shipped defaults from the measurement
  - **Evidence already gathered, 2026-09-10, see research.md:** `routine.LinesPerStatement = 3.0` fits this repository (1.8% outside, `keep`) and does NOT fit facdrone (8.7% outside, `recommend` proposes 4, where 4.0% would be outside). `routine.CountLineComment = 20` fits both. Decide the verbosity default with both repositories in hand rather than one
  - **From task 4.1's review:** report how many dead-code candidates the interface-method tally excuses and how many of those are free functions rather than methods. The exclusion matches by short name, so a free function sharing a name with two classes' methods is excused too; that is the safe direction but its size is asserted rather than measured, and names like `run`, `close` and `write` are declared by two or more classes in almost any project
  - Any lean number or threshold whose measured count on this repository is mostly noise is changed, with the measurement written beside it, in its own commit; a default the measurement confirms is left alone and the confirmation recorded
  - Done when every shipped lean default has a recorded count behind it and the unit tests pin the final values
  - _Depends: 6.3_
  - _Requirements: 5.7, 9.1_
  - _Boundary: config defaults_

- [ ] 7. End-to-end behaviour and the shipped skills
- [ ] 7.1 End-to-end checks through the installed command
  - A repository fixture with lean rules on: the human report shows the net line and, with nothing to cut, the lean-already line; JSON carries the delta and an example; SARIF carries the run property; the agent-rules snippet carries the lean section; `doctor` prints the three rows; a check with every rule off produces a report identical to today's apart from the net delta
  - **Make the verbose human output reachable, because today it is not, and task 2.5's review found nothing owns it.** Requirement 8.2 asks for the worked example in the verbose human output; task 2.5 renders it correctly at that verbosity and no invocation can produce that verbosity, because the option resolver answers only quiet or normal. Half of 8.2 therefore ships unmet with every check green. The shape that keeps the documented precedence is quiet first, then verbose, then normal. Note that `--verbose` currently means stderr command logs, timings and tracebacks for every subcommand, so this changes what an existing option does to standard output and needs saying in the CLI documentation
  - Done when `--verbose` on a check prints a lean finding's worked example under its hint, `--quiet --verbose` still resolves to quiet, and a test asserts both
  - Done when the e2e suite passes through the CLI on the fixture
  - _Depends: 6.4_
  - _Requirements: 7.1, 7.3, 7.6, 8.2, 8.4, 9.2, 9.4_

- [ ] 7.2 (P) Update the four shipped skills
  - The gate skill reads the net line and lean findings before staging; the improve skill gains a step working lean findings largest-reduction-first; the adapt skill covers the family's ignore lists and numbers as rungs of its ladder; the onboard skill proposes lean rules from measurement and never enables one blindly
  - Done when the packaged skills install with the new text and a test asserts each skill names the net line or the lean rules
  - _Depends: 1.1, 2.5_
  - _Requirements: 8.7_
  - _Boundary: skills_

- [ ] 8. Documentation (Requirement 10 mandates it; the code-only rule yields to the requirement, as the previous specification's plan did)
- [ ] 8.1 (P) The rules reference, the feature list and the CLI reference
  - Every lean rule with what it reports, what it does not, its default and the measurement behind it; the per-language blind spots of reference-based detection beside the routine rule's; the feature-list rows; the doctor rows and the net line in the CLI page
  - **From task 2.2's review:** requirement 7.2 says deleted files count as negative contributions, and the delta measures routines only, so a deleted file that held no routines reads as a net of zero. That follows the design exactly and is broader than the requirement's wording, so the documentation must say it rather than let an agent meet it and conclude the number is broken
  - Done when the docs build and every lean rule name in the code appears in the rules reference
  - _Depends: 6.4_
  - _Requirements: 10.1, 10.3, 10.5_

- [ ] 8.2 (P) The configuration guide and the lean-code guide
  - The `[lean]` keys with the template excerpt each produces; the agents guide gains the lean section of the snippet; a guide page with the ladder, the three tags the Gate emits and the two it does not with the reasoning, how to read the net line, and the tests policy with the scope proposal
  - Done when the docs build with the new page in the navigation
  - _Depends: 6.4_
  - _Requirements: 8.3, 8.4, 10.2, 10.4_

## Implementation Notes

Cross-cutting findings recorded as they were learned, so a later task does not rediscover them.

- **1.1** The design said `wants_references` covered six rules in one sentence and five in another. Resolved to five: `over_export` is answered from file metrics, file edges and the definitions walk, so putting it in the reference set would make an over-export-only configuration pay for a `refs` call on every recorded entity and read none of the answers (req 9.4, 9.5). Both design sentences now say five, and two named tests pin it.
- **1.2** Adding a name to `StructureRuleName` is never local: `tests/report/test_hints.py` walks the list and fails any rule whose hint falls through to the generic text, so nine names forced nine hints in `report/hints.py` one task before task 2.3 owns them. Task 2.3 still owns the final hint text, the worked examples, the `structure.similar_routine/same_file` variant and the tag-form assertion test. The same edit also forces `tests/fixtures/snapshot_{before,after}.json` to be regenerated, because `test_fixture_file_is_the_canonical_wire_form` compares against the canonical dump.
- **1.2** `cli/doctor.py` gained a `NO_PROBE` constant, out of boundary and not forced by any test, because the three new `Feature` members would otherwise make a real build print "not checked (the fixture seam starts no processes)" for features that simply have no probe yet. `tests/runner/test_doctor_features.py::test_a_feature_with_no_probe_yet_says_so_rather_than_blaming_the_test_seam` asserts an interim state: **tasks 4.3 and 5.5 must retire or rewrite it** when the probes land.
- **1.2** A required field with no refusal test is not an invariant. The reviewer defaulted `TokenIndex`'s three halves and the whole 4340-test suite stayed green; the guard that stops a half-built index reading as "no duplicates anywhere" (req 5.8) now has a parametrized `ValidationError` test, verified to fail under that exact mutation.
- **1.3** `CountLineComment = 20` was shipped against my instinct and on the strength of a measurement: 5916 routines, p50 1, p95 8, 20 routines (0.3%) outside, and `recommend` returns `keep 20`. Docstrings do count as comment lines here, but per routine that is a thin tail rather than the population. The measurement is recorded where the default is declared, per requirement 9.1.
- **1.3** Requirement 6.3 asks for a *configurable* statement floor and the design had fixed it as a constant. Corrected in the design and folded into task 1.4. A requirement is not satisfied by a design that is merely self-consistent.
- **1.3** An architecture-scope threshold on a plugin metric validates and is then never requested, because architecture nodes carry no metrics. Pre-existing and metric-agnostic, not introduced here, but requirement 5.6 names architecture, so new task 5.6 owns the decision rather than letting this family inherit a silent no-op.
- **1.4** A declared floor has **four** consumers, not two, and the two the design named were the easy ones. Beyond `analysis.thresholds` and `analysis.ratchet`, a below-floor value also reaches `analysis.recommend` (which prices a ceiling over the population it measures) and `analysis.baseline.capture` (which records the worst value per rule). Each was answering a different question from the gate: `recommend` proposed `raise 3 -> 4` over 5 940 routines where the floored answer is `keep 3` over 2 963, and an unfloored capture records `routine.LinesPerStatement = 22.5` against a floored maximum of 7.2 -- not inert, because `apply` narrows a configured limit down to a recorded value below it, so on a repository configured at 30 the unfloored figure would loosen the rule threefold. Any later task that adds a floor to a second metric must check all four.
- **1.4** `analysis.ratchet.attach_before` is a judgement input, and it was missed. It fills `Finding.before`, which `analysis.classify` reads to call a violation **pre-existing** and therefore non-blocking -- so a before value taken below the floor excused the very violation the floor says it cannot speak about: measured, a routine of four statements at ratio 8.0 growing to six at 5.0 against a maximum of 3.0 reported `preexisting=True, blocking=False` for a violation the change introduced. Leaving `before` unset says "not known", which blocks, and is exactly what that function already does for a before side that did not parse. The rule to carry forward: **any place a value decides severity is a place the floor applies**, not only the places that raise findings.
- **1.4** The configured minimum could not be passed as an argument to either evaluator, and the reason is this project's own gate. `evaluate_thresholds` already declares six parameters and `evaluate_ratchet` five, against a `routine.CountParams` maximum of five; a seventh would have been reported and blocked, and `pair_changed_signatures` re-pairs a routine whose signature changed, so the ratchet would have caught it too. It travels instead on `EffectiveThreshold.floor`, stamped once by `analysis.thresholds.with_floor`, which is the one object both evaluators already receive. A later task needing run-wide state in `analysis/` should reach for the same shape rather than a parameter.
- **1.4** A path scope can **add** a threshold no global one defines -- including one an earlier scope switched off -- and such a threshold has no base to inherit a stamped floor from. `_overlay` therefore re-stamps the run's minimum, read back off the global thresholds by `_run_minimum`. The residue is honest and pinned by a test: with no floored metric configured globally at all, a scope-added one keeps the declaration's default, because there is then no operator number in the analysis layer to find.
- **1.5** The entity fake in `tests/understand/api_fakes.py` is at class coupling 12 of 12, an absolute error with no ratchet. Scalar and tuple-shaped members cost nothing and `object`-annotated parameters cost nothing, but that escape is already spent on `lexer()`. Task 1.9 gives it room before 3.1 and 5.1 need it.
- **1.5** `snapshot_cache.worker_digest()` still hashes only `worker.py`. That is correct while the sibling is empty, and task 3.3 must extend it before `worker_lean.py` gains content, or a cached before-side snapshot will survive a change to the measurements that produced it.
- **1.5** Understand 8.0's `Ent.lexer` documentation contradicts itself: the signature says `show_inactive=False` while the prose says True by default, and it documents a `tabstop` parameter the signature does not have. The fake follows the signature, which is right. Do not "fix" it to match the prose.
- **1.5** Measured on commit: a file that is a docstring and nothing else reports `RatioCommentToCode` **0**, and the gate calls it under-documented against the 0.1 minimum. The ratio is comment lines over code lines, so a file with no code has an undefined ratio that arrives as zero. Harmless here and non-blocking, but requirement 6.1 adds a *maximum* to this same metric, and whoever writes that documentation should say what the metric does at both ends rather than let an operator meet this on their own.
- **1.6** The reference sets had a second omission of the same kind as the inheritance one: `PARAMETER_USE` lacked `callby`, so a parameter used only as `fn()` read as unused. Masked in the fixture by the `self|cls|this` ignore, but `def apply(fn): return fn()` has no ignore to hide behind. Corrected in the design.
- **1.6** Of the four inheritance kinds now in the reference set, only `inheritby` fires on Build 1262 for Python and C++, and `derive` is an outbound kind in an otherwise inbound set. Recorded in the design so it is not read as four measured facts.
- **1.6** Uniqueness in the contract project is a per-language property until task 1.7 lands. `Shape` is a second unused class and `native/shape.h` a second over-export, both pre-existing on the C++ side, and 1.7 owns resolving them.
- **1.7** C++ inheritance fires `derive` where Python fires `inheritby`, both on the base class naming the derived one. A reference set holding either alone calls the other language's base class dead. Both were already in the set; the design's claim that only one fired was falsified by the C++ case and corrected.
- **1.7** `variable_referenced` as designed would answer True for every module variable in every project, because a binding's own defining assignment is a `Set Init` reference to it. The design now reads only `useby, callby, typedby`, and a write-only variable is dead, which is also how Understand defines its own unused-variable metric. Task 3.2 implements it that way.
- **1.7** Clipping a file's lexeme stream to a routine's line range picks up a trailing empty-text lexeme, which adds a free matching token to both sides of every similarity comparison: 0.637 with it against 0.631 without, on the contract project's cross-language pair. The design now drops lexemes by empty text rather than by class name, because the documented token classes do not include one for it.
- **1.7** Two contract failures predate this feature and are owned by nobody: `test_doctor_features_contract` expects six feature rows where task 1.2 made nine, and `test_metrics_contract::test_the_catalogue_answers_a_metric_list_for_every_configurable_language` answers an empty set. Both need scheduling.

## Found while dogfooding, and NOT part of this feature

**A package initialiser holding only a docstring is charged as a real dependency.** Found
during task 2.6 and confirmed by its review, measured twice on Understand 8.0 Build 1262.

`analysis/structure/coupling.namespace_targets` excuses a dependency on a package initialiser
that holds no code, and it decides "holds no code" by testing `CountLineCode == 0`. Understand
counts a multi-line module docstring as **code lines**, so an initialiser carrying only prose
is charged as a genuine dependency. Measured: restoring a long docstring to
`analysis/lean/__init__.py` took `runner/lean.py` from 7 new dependencies to 8 against a limit
of 7, blocking the commit, and shortening it to one line dropped that file out of the list with
no other change.

The same underlying fact was recorded earlier from the other direction, in task 1.5: a file that
is a docstring and nothing else reports `RatioCommentToCode` 0 and is called under-documented,
because the ratio is comment lines over code lines and the docstring lands on the wrong side of
that division.

The consequence is that a project is penalised for documenting a package, which is the opposite
of what every other rule here asks for. The emptiness test needs a measure that is not
`CountLineCode`, or the rule needs to know that a docstring is not code. Like the finding below,
this belongs to the base maintainability gate rather than to the lean-code family, and it
should have its own specification rather than being absorbed here.

**The structural ratchet can report that an untouched file's coupling grew.** Found during
task 1.8 and characterised by its review, on Understand 8.0 Build 1262 with this repository's
own configuration.

Appending two comment lines to `config/template.py` makes `check --worktree` report
`analysis/recommend.py fan-out rose from 6 to 7 files`. That file is byte-identical on both
sides and none of its dependencies was deleted, so it cannot legitimately have gained
fan-out. Measured:

- deterministic across repeated runs, so not a race;
- survives moving the snapshot cache aside and forcing a cold before-side extraction, so not
  a stale cache;
- absent when an unrelated file is touched, and absent when a different dependency of the
  same file is touched, so not universal;
- the after side's recorded dependencies are the correct seven (four imports plus the three
  package initialisers Python executes on the way), all present at HEAD too, so **the before
  side's six is the under-resolved figure**, under an analysis reporting 19% accuracy.

A structural "worse than before" finding on a file that is unchanged between the two sides is
an artefact by construction. The cheap and defensible guard is to refuse to raise one for a
file whose content is identical on both sides, or to require the two sides' resolution to
agree before comparing structural counts.

This is a defect in the base maintainability gate, not in the lean-code family, and it should
have its own specification rather than being absorbed here. Recorded so it is not lost.

**Update, same task, later run:** the artefact did NOT reproduce on a subsequent
`check --worktree` over the same two files with four small edits added. So it is not purely a
function of which files changed, and whatever resolves the before side's sixth edge varies
between runs that ought to be equivalent. That widens the question rather than narrowing it,
and it is worth reproducing deliberately before any fix is designed.
- **1.9** The first attempt satisfied a coupling limit by splitting the fake into six bases, taking its count from 12 to 0 while every edge survived, reattributed to bases whose inheritance edges the metric does not charge. The tool contradicted itself in that run: fan-out on the same class rose 4 to 6. My task text caused it, by forbidding both raising the limit and recording a deviation, which left no honest route. A constraint with no legitimate solution inside it produces an illegitimate one.
- **1.9** `CountClassCoupled` charges annotation types on Python, so a wide shallow dataclass reaches the limit on its fields rather than on collaborators: of the fake's 13 edges, 7 are builtins named only in annotations and 2 are single expressions, leaving 4 real collaborators. Recorded as a scoped ceiling with the enumeration, the way `[scope.schemas]` already records a metric counting the wrong thing. Other wide dataclasses in this repository are subject to the same effect and nobody has looked.
- **1.9** `CountDeclMethodNonStub` is this project's own synthetic, `CountDeclMethod - 2 * CountDeclPropertyAuto`, and Understand leaves `CountDeclPropertyAuto` unset for Python. The two keys therefore carry the same number on Python and differ only in their limits, 15 against 20, so only the non-stub key can ever fire there. A deviation on the other one was inert and is gone.

## Refinements the facdrone measurement demands (2026-09-10)

Measured read-only on facdrone, 417 source files, ~101 800 lines, analysis at 26% resolution.
Recorded here because they change what tasks 4.1, 4.2 and 4.3 must build, and the design now
carries them under "The resolution gate".

1. **The reference-based rules need a resolution gate before they may report.** The dead-code
   predicate answers 830 routines and ~6160 lines on that codebase and is wrong nearly every
   time. Below a resolution floor the rules must report nothing and say why once, the way the
   gate already refuses a metric it cannot measure. **Tasks 4.1 and 4.3 own this.**
2. **The override exclusion misses structural typing.** One of the 830 carried an `overrides`
   reference; the rest implement protocols with no inheritance edge at all. A method name
   declared on two or more project classes is an interface method regardless. **Task 3.2 must
   record the declaring-class count per method name; task 4.1 must apply it.**
3. **Duplication is the reliable half and should lead.** Token-based detection needs no
   reference resolution: 76 exact 12-line windows and 51 mergeable twins, about 1220 lines
   defensibly reducible, roughly 1.4% of that source tree. Dead code's 6160 is the larger
   number and the untrustworthy one. If the family ever ships in stages, group 5 goes first.
4. **Requirements need amending, not just the design.** Requirement 1 has no acceptance
   criterion for a resolution floor and requirement 1.4 names only overrides. Both should be
   amended before group 4 is implemented, rather than the design silently carrying a rule the
   requirements do not ask for.
- **2.1** Branch coverage does not protect a guard fused into a boolean expression: coverage records no arc for an `and` short-circuit, so `None not in counts and ...` could be deleted with the module still reporting 100%. All 21 tests stayed green under that mutation. Every rule in this family should assume the coverage number says nothing about its guards, and mutate them.
- **2.1** `details` keys are a shared namespace across the whole `structure.` category, not per rule. The first draft published `depended_on_by` as a string where `structure.fan_in` already publishes it as a list, in the same JSON object and the same SARIF properties. The convention the five remaining rules follow is recorded in the layering module's docstring.
- **2.2** A review packet must include the implementer's status report. Task 2.2's did not, so the reviewer had no RED-phase evidence to inspect and substituted a 23-mutant run. That produced stronger evidence than the report would have, but by accident rather than design, and the omission was the controller's.
- **2.3** Two hints shipped in the first draft told an agent to remove a parameter an interface requires and a constant an outside importer reads. Both were green everywhere: the tests asserted the tag and the shape of the text, never its meaning. The text of a hint is a product, and the only thing that catches a wrong one is somebody reading it as the agent will.
- **2.3** Two of nine worked examples did not compile in the language they claimed. An example is code and should be checked as code; a wrong one teaches a wrong habit to every repository that installs the tool.
- **2.4** A defect fixed in one task returned in the next through a different mechanism. Task 2.3's review added behavioural exclusions to three hints so an agent would not delete an interface-required parameter or an externally-read constant; task 2.4 then quoted only each hint's first sentence into the agent-rules snippet and dropped all three. Both tasks were green everywhere. A fix recorded in one artefact is not a fix in the artefact that quotes it, and the only test that catches this reads the rendered text for meaning.
- **2.4** Requirement 7.1 prints the net delta whenever a check has a before side, with no condition on the lean rules being enabled. Task 2.6's acceptance line said an all-off configuration must produce a run result identical to today's, which contradicts it. Requirement wins; 2.6's line is amended.
- **2.5** Requirement 8.2 had a half nobody owned: the worked example renders at a verbosity the command line cannot produce, and task 7.1 listed the requirement while its acceptance text covered only the JSON half. A requirement id in a task's `_Requirements:` line is not coverage; the acceptance text is what gets built.
- **2.6** Two artefacts that must agree, with nothing binding them, is now this feature's most frequent defect: the hints and the snippet quoting them, the promised net line and the printed one, and the definitions predicate written out in the fingerprint and the extractor. The third instance was the recurrence guard for the defect the task was closing. Where two places must answer the same question, one of them should ask the other.
- **3.1** An accepted error, recorded so task 4.2 inherits it rather than rediscovering it: a routine called once from another routine and once from module scope measures ONE caller, because a file is not a routine, and so satisfies the pass-through predicate exactly. The rule will report a routine that two places call. ~~Carrying the callers themselves instead of a count fixes it~~ **— corrected by task 4.2, and the correction matters more than the note did.** It does not fix it. `worker_lean.py::_project_callers` returns the length of a list already filtered to project routines, so the names and the count come off the SAME list and the module-scope call site is invisible to both. The undercount lives in what counts as a caller, not in count-versus-name. Widening it changes requirement 2.1's predicate and is task 6.4's to measure. A note that proposes a fix is a claim like any other, and this one was never checked against the code it named.
- **3.1** The worker sibling may not contain a dataclass. Loaded by path without being registered in `sys.modules`, the decorator raises inside the standard library. Nothing in the type checker or the ordinary suite sees it; the isolated-interpreter test written in task 1.5 to prove a rule fires is what catches it. Tasks 3.3 and 5.1 both add to that file.
- **3.2** The guard task 3.1's review added, pinning which file decides a caller, did not survive being refactored: task 3.2 generalised that helper, gave it two new consumers, and the reading became swappable again with the whole suite green. Where a helper is generalised, its guards have to be generalised with it, and the discriminating case has to differ per consumer or every reading agrees by construction.
- **3.2** `derive` is not one direction across languages. It is the inverse kind on the base for Basic, C and C#, and the forward kind on the derived type for Ada and Pascal, so a set that treats it as inverse makes those two languages list a class's own base as its derived class. Recorded because the same trap applies to any kind name reused across language parsers.
- **3.3** The digest binding was verified by replacing the path with a second spelling that resolves to the same file today; both tests still failed. That is the first time this feature's most frequent defect, two artefacts that must agree, was closed by binding rather than by the two happening to agree.
- **3.3** On this repository 4448 of 6211 routines answer zero callers, but 4106 of those are test functions that genuinely have no project caller because pytest collects them by reflection. The `src/` figure is 342 of 1399. A rule reading these facts without the resolution floor would report most of a working codebase.
- **4.1** I conflated two Understand figures from task 1.6 onward: 19% and 26% are analysis ACCURACY, the share of files parsed cleanly, while this repository's CALL RESOLUTION measures 43%. The conflation reached the requirement, the design, three task briefings and then the code's own justification. They bound different failures, so requirement 1.8 now names both floors: accuracy bounds whether a file was read, resolution bounds whether its references were understood, and a dead-code claim needs both.
- **4.2** A mutation run is worthless unless `__pycache__` is cleared before every trial. Restoring a same-size reordering with `cp` leaves the byte count and the modification time close enough that Python serves the stale bytecode, so every guard-ORDER swap reads as killed. Task 4.2's implementer reported 35 of 35 killed on that method; the reviewer re-ran the same battery with the caches cleared and four mutants survived, two of them in the routine whose deviation record cited the figure as its justification. Every mutation claim in this feature made before this note should be read as unproven for reorderings.
- **4.2** An argument that refutes one of two floors does not refute both. The single-implementation rule was shipped with no trust gate, justified by `referrers` counting use, type and inheritance edges rather than call edges. That answers the RESOLUTION floor only. `referrers == 0` is an absence-of-references claim, which is exactly what the ACCURACY floor bounds, and the prose never engaged it while presenting the conclusion as settled. Since requirement 1.8 was split into two floors, every no-floor justification in this family has to say which floor it is refusing and why, one at a time.
- **4.2** A guard-ORDER property needs one test per position, and the number of positions must be counted from the code, not from the prose describing it. Two review rounds were spent on the same finding one position further down each time: round 1 pinned the gate above one condition of four, round 2 above three of four. Both rounds asserted the property was fully covered, and the miscount came from the prose itself -- a docstring saying "three guards" where the routine had four, and a test comment calling the third guard "the bottom of the chain" with a fourth below it. Group 5 has three rules of the same multi-guard shape.
- **4.2** `check --worktree` does not see untracked files, so a task that adds new test modules dogfoods clean and is then blocked by its own gate at commit time. Task 4.2's new test helper took six parameters against a maximum of five and nothing reported it until the files were staged. Every task adding a file should `git add -N` it before dogfooding. The sixth parameter turned out to be a line number no test ever passed, which is what requirement 1.2 reports, so the gate caught the feature's own subject in the feature's own tests.
- **4.3** A test written to bind a claim was built to accept the claim's counterexamples. Its docstring carried an exemption ("or a routine threshold") that the operator-facing sentence did not have, and its else branch asserted the very fact that made the sentence false. So the binding artefact and the claim disagreed, and the test passed BECAUSE of the disagreement. A guard whose exemption clause is exactly the hole it was written to cover is worse than no guard, because it certifies the hole.
- **4.3** Two rounds were rejected for the same operator-facing sentence, both times because the sentence was reasoned about rather than read against the file the tool writes. What closed it was rendering the block and ruling on all sixteen set keys one at a time in writing. Any claim generated into a user's configuration should be checked that way, key by key, not by sampling.
- **4.3** Two settings can read one measurement and must NOT agree: `analysis.accuracy_floor` reports, `lean.accuracy_floor` refuses. Sharing one key would let an operator silence a warning and thereby unlock the dead-code rules at an accuracy where this repository misreports sixteen bindings as unreferenced while they are read. Where two artefacts must DIFFER, the binding is a pair of tests that fail if either starts reading the other, not a shared constant.
- **5.1** Never restore a mutation trial with `git checkout --`. On an UNCOMMITTED task that reverts the file to HEAD, silently discarding the work under test rather than the mutation. Task 5.1's implementer lost its own module mid-loop and rebuilt it from a captured diff; the suite stayed green throughout, because a guard and the test written for it disappear together and nothing is left to notice. Use a file copy taken before the first trial, and verify the restore by checksum, not by running the suite.
- **5.1** A constant naming an Understand reference kind is invisible to every unit test that uses the shared fake, because the fake answers any filter the same way. `START_REFS` could be set to the literal `"zzz"` with 1307 tests green, which against the real API makes the similar-routine rule report nothing on every project, silently. The repository had already solved this once for `CONTAINER_KINDS` by binding the two spellings with a test, and the solution did not generalise on its own: the next constant of the same kind arrived unbound. Every new reference-kind constant needs a binding, and the honest one is behavioural -- split the fake so it can answer the filter -- not string equality.
- **5.1** Four ordering dependencies reached the token index document and each was bound only after somebody found it, three of them by a reviewer. What finally worked was enumerating every iteration order that reaches the document and stating for each whether it is bound DIRECTLY or only transitively. That table found the third instance itself. Any task that builds a document consumed by a later rule should produce the same enumeration, because a document whose order depends on the walk is not comparable to the other side of a change.
- **5.1** "Bound transitively" is not bound. The within-line join order was reported as covered through the shape assertions; it was not. Reordering inside `_line_hashes` alone left all 1255 tests green, and a commutative line hash makes `a = b` collide with `b = a` and `f(x, y)` with `f(y, x)`, so task 5.3 would report runs of non-duplicate lines as exact duplicate blocks. Closed with one anagram line in the fixture. When a site's only cover is another site's test, mutate THAT site, not the one upstream of it.
- **5.2** The absence of a field does not prove the work that fills it did not run. Both tokens-off tests asserted no index was recorded, and a worker that measured the whole project on every load and discarded the answer passed all 4967 of them. Requirement 9.4 is about the COST, not the field. Closed with a spy on the sibling's own `token_index` that calls through. Any "nothing runs otherwise" clause needs a call spy; an assertion about the output cannot express it.
- **5.2** A pair of request keys guarded asymmetrically is invisible from either end alone. `lean_references` could be written as `wants_references or wants_tokens` with the whole suite green, making a duplicates-only run pay a reference call on every recorded entity and read none of it. The tokens twin spanned both configuration tables and the references one did not. Where two keys are two costs, each test must span BOTH tables, not its own.
- **5.3** A finding may not name a location inside the range it is reporting. Twenty-four identical lines produced `a.py:1-24 ... also holds at a.py:1, a.py:2, a.py:3`, and in the case that mattered -- the same block genuinely in a second file -- all three named positions were inside the reported range and the one actionable location was buried in "and 23 more". The trigger is any run of `2 * min_lines - 1` code lines periodic below `min_lines`: identical table rows, repeated `pass`. Requirement 5.1 says OTHER locations, and a position inside the range is not one.
- **5.3** Whether to delete an unbindable line or test it is decided by tracing, not by taste. One `sorted()` in this module was deleted because its order provably reached no output, and that was upheld; a second was equally unbound but its order reaches the finding order, which `report/json_out.py` documents as the contract making a run render byte-identically in any process. Same standard, opposite answers. Trace whether the ordering reaches an output: if not the line is decoration, if so its absence is a gap.
- **5.4** A guard that refuses a RELATION does not survive being merged into a GROUP. The nesting check correctly refused the edge between a closure and its parent, and grouping by connected components put them back together through any third member, producing a finding anchored at lines 10-40 that named line 12 as a separate member. Any rule that filters pairs and then merges them into groups has this hole; the filter must be re-applied to the group.
- **5.4** Four review rounds, and every one fixed its reported defect and then shipped ITS OWN new guards unbound. Round 2 added three guards and all three were deletable or reversible with the suite green, each producing a different wrong answer. The rule that ended it: every guard a remediation ADDS must be mutation-tested before the round is reported, listed with the mutation that kills it. A guard written during a fix is exactly as unbound as one written in the first draft.
- **5.4** An answer that depends on which unrelated routine the same commit touched is worse than no answer. `taken` was filled from the pre-prune component, so a routine's genuine family reported when it was touched alone vanished when an unrelated routine was touched with it. Fixed by keying once-per-run on the anchor. Requirement 5.11 asks for a family against every affected routine belonging to it, and this tool exists to give a stable signal about what to delete.
- **5.4** Record a bound, do not assert past one, and do not quote a count nobody can reproduce. "No reader would accept these as twins" was replaced by a proof: the shingle filter is lossless above 12/13, about 0.923, attained at a six-token run against a seven-token one. That is ABOVE the shipped threshold of 0.9, so the default is not lossless -- the opposite of what the first, looser bound of 6/7 implied. Three runs of the same exhaustive search reported pair counts differing threefold because each applied a different symmetry reduction, while agreeing on the bands, the maximum and the witness. The count is gone; those three stayed.
- **5.5** Deleting a test is the only edit that reduces coverage while every check stays green. One test was retired on instruction and three more went with it, all covering a DIFFERENT module's rendering, leaving four mutations alive in `doctor` across the whole 5133-test suite. An uninstructed deletion deserves the same scrutiny as a new guard, and the question to ask is which mutation the deleted test was the only killer of.
- **5.5** The worker module's docstring predicted its own ceiling and the prediction held: it stood at 130 of 130 permitted functions, two new functions took it to 132, the gate blocked the commit, and the probe went into the sibling exactly where that docstring said the next thing would have to go. A limit that is measured, recorded and then enforces itself against the next change is this tool working on its own codebase.
- **5.8** A performance fixture can validate an algorithm and tell you nothing about the data. The family rule benchmarked under a second on a thousand synthetic routines and did not finish in ten minutes on a real 312-file project, because the synthetic shapes were varied and real ones are not: the vocabulary collapses every identifier and literal to one of two symbols, so the candidate index filters 0.78% of pairs instead of the "handful" its docstring claimed. The fix was an exact ceiling from token-multiset overlap, 900s to 49s with byte-identical findings.
- **5.8** Correcting a false measurement claim is the easiest place to write another one. The sentence replacing "candidates are a handful" asserted the index is kept "because it is cheap"; measured, it costs 46% of the pass. A docstring that says a previous draft got the cost wrong has to carry its own measurement, or it is the same defect with the sign flipped.
- **5.8** A number must name the sample it came from. A pinning test quoted a post-fix figure over a sample nobody ran, because task 5.5's measurement used 60 single-file commits and task 5.8 could not retake it -- this repository has 90 single-file commits and one touches a single `.py`. The substitution was honest and disclosed in one record and not in the other two. Where two measurements answer one question by different methods, every record of either has to say which it is.
- **5.7** A contract test asserted a literal count of the tool's OWN features -- six rows -- while its docstring explained that it deliberately avoided asserting the build's feature list "because the list is the build's and will grow". It had the direction backwards: the build's list grows on its own, and the COUNT grows only when this tool adds a feature. Task 5.5 added two and the test failed on a correct change. It now counts `Feature` itself. Where a test asserts how many of something this project has, derive it from the thing that decides it.
- **5.7** The budget this task replaced was not merely weak, it was already FLAKY, and nobody knew because it usually passed. Six trials of the statistic it computed gave two NEGATIVE per-entity costs -- the run with the plugin finished sooner than the run without it -- and one trial failed the ceiling on unchanged code. Differencing two wall-clock subprocess timings to find a signal worth one to three per cent of each run cannot do better. The replacement times the API call under the worker interpreter and charges processor time, which barely moves with machine load.
- **5.6** The hole was metric-agnostic, so the refusal is of the SCOPE and not of one metric. An architecture-scope threshold on any metric was accepted by validation, produced no finding, emitted one note about an empty population, and exited 0. Extraction was the other honest answer and was refused on evidence rather than taste: a useful architecture finding has to name the node, which needs arch records in the snapshot, a branch in the evaluator and the ratchet, and an affected set mapping a change to nodes -- and the worker is at its function ceiling. It was also unverifiable here, since every contract test skips without Understand on the path, so shipping it would have been a claim with no sample.
- **5.9** The proof that a deletion lost nothing was the witness the deleted proof had named. Forty lines argued that the shingle index could only drop pairs below a similarity of 12/13; the pair attaining exactly that -- six identical tokens against seven with one changed, sharing no four-token run -- is now a reported family and a test. Where an approximation is removed, its own worst case is the cheapest evidence that the removal was safe.
- **5.9** Deleting a test needs the same evidence as adding a guard: name the mutation it was the only killer of, and show that mutation is now IMPOSSIBLE rather than merely uncaught. Both tests deleted here pinned behaviour of functions that no longer exist -- a dedupe over a candidate stream, and a window shorter than a shape -- so neither mutation can be written any more.
- **6.1** A rule can report nothing with no unavailable note and every test green: `wants_definitions` did not know `unused_variables`, so enabling that rule alone handed it an empty definitions list. The contract test caught it because it asserts the planted case IS reported, not merely that nothing false is. A rule's contract test needs a positive case for the same reason a budget needs a doubling it can detect.
- **6.1** Measure the kind set, do not reason it. `setby` in the parameter-use set was defended as reasoning since task 3.1; on Build 1262 a defaulted parameter carries `Definein` only and a reassigned one carries `Setby`, so the set is right and now has a test. The design's claim that Ada and Pascal invert `Derive` was reasoning too, and it was false: both put `Derive` on the base naming the derived type. Two claims, one confirmed and one refuted, both by the same ten-line fixture.
- **6.2** A docstring in `layering.py` states that Understand reports a multi-line module docstring as code lines, inferred from a dependency count moving eight to seven. Measured on the exact text under both Python grammars: `CountLineCode` 0, `CountLineComment` 13. The count moved for some other reason. The test is written to fail if a build ever answers the paragraph's way; the paragraph itself is follow-up 12.
- **6.1/6.2** The parent made the mistake its own note 4.2 records: two new test modules were dogfooded while untracked, passed, and were blocked at commit for nine and ten new dependencies against seven and two routines of 79 and 85 lines against 60. Both were split by subject rather than exempted, every assertion intact, 33 and 5 tests before and after. Two further rules for parallel agents: the full gate is the parent's to run once after they land, since two concurrent suites each take ten minutes instead of one; and `git stash` is forbidden while another agent has intent-to-add files, which the index refused on its own but which would otherwise have swept a sibling's work.
- **6.3** A cost measurement on a repository you do not own can WRITE to it. `check --all` on facdrone rewrote its adaptive baseline file, twice, and had to be restored by checkout after each manual run. The harness now points `[baseline] file` into the scratchpad. Anyone measuring a foreign repository read-only should assume the tool's own state files are in scope of "read-only" and route them elsewhere first.
- **6.3** The first ten findings per rule, read by a person, are worth more than the counts. Two rules whose counts looked healthy are mostly noise on inspection: `pass_through` is met by comprehensions and expressions holding one project call rather than by the statement count, and `unused_parameter` is dominated by overload stubs, protocol methods and pytest fixture parameters. Both counts would have passed a threshold review; neither survives being read.

## Found while reviewing 4.2, owned by no task yet

1. **`dead.py`'s no-tally call site can be reworded in silence.** Making `verdict` required stopped the argument being DELETED, not the word being CHANGED: rewording `_UNUSED` to `"judged"` at the `_NO_TALLY` site alone survives all 4884 tests, because the test that pins the sentence exercises three of the four `dead.py` sites. The docstring does not overclaim, so this is a gap rather than a false statement. Task 4.3 or 6.4 should close it when next in that file.
2. **`TrustGate.messages` promises an order nothing tests.** Replacing `sorted(self._refused)` with `self._refused.values()` survives the whole suite; no test refuses two languages in one run, so the docstring's "a fixed order so a run reads the same twice" stands on nothing. One two-language refusal test closes it.
3. **The layering flowchart in `design.md` is a stale simplification.** It shows no `TrustGate` node and puts the ignore check second from the top, while the same file's signature block and its 2.6 traceability row both record the gate-first order correctly. Not a contradiction of record, but the picture teaches the order the rule spent three rounds proving wrong. Fix it whenever that diagram is next touched.
4. **The `[lean]` help pins words, not what they attach to.** Swapping REFUSES and REPORTS between the two accuracy-floor keys in `_LEAN_HELP`, so the paragraph contradicts the note two lines below it on the same rendered screen, leaves all 866 tests green. The behaviour is bound by two tests; only the prose is loose. Assert a phrase carrying the claim rather than the individual words.
5. **`_reads_in` records the LAST guard a key is read under, not that it is read under exactly one.** A second read under a wrong guard survives when the correct read comes later in the same function body. The reader errs toward red, so it is not urgent, but tasks 5.1-5.5 wire the token keys through that path.
6. **The five token keys are bound to their rule by shared name stem**, which is uniquely determining only because no other `[lean]` switch begins `duplicates` or `similar`. Three switches share the stem `unused`. The exemption list must shrink when 5.1-5.5 wire those rules, and a test asserts it.
7. **A duplicate-block finding can still suppress a distinct file.** With `a.py` and `b.py` holding thirty identical lines and `z_real.py` holding twelve of them, nineteen window offsets of the `b.py` copy consume all three named slots and `z_real.py` never appears in the message or in `details`. Structurally it is finding 1 one file over, but the reader is still carried to a true location and the "and N more" tail claims no completeness, so it was not blocked. The cheap mitigation needs no general rule about what "the same copy" means across files: collapse a path's locations to one per contiguous window run before the cap is applied.
8. **`cli/doctor.py`'s comment says six unknown rows where the enum has nine**, and the restored test mirroring it now says nine. Stale at HEAD rather than introduced, so task 5.5 left it to keep the byte-identity its review was asked to confirm. Fix it alongside task 5.8.
9. **`worker_lean.py`'s header says worker.py measures 119 of its 130 functions**; task 5.2 measured 130 of 130. Pre-existing, and the paragraph's argument only strengthens with the true number.
10. **Two contract tests fail against the installed build for reasons outside this feature.** `test_metrics_contract`'s list of languages without class metrics no longer matches Build 1262, which now offers them for Ada, Fortran, VHDL, Jovial and Assembly. Found by task 5.7; pre-existing and not this family's to fix.
11. **The pass-through rule silently depends on a `routine.CountStmt` threshold being configured.** It reads the statement count off the entity record, and that metric is requested only because a threshold asks for it. The shipped default carries one at 40, so today it works; a configuration that drops that threshold makes the rule judge nothing with no unavailable note, which is the silent no-op this project refuses. Found by task 6.1's contract test, which adds the threshold and records why. Task 7.1 should either make the lean step request `CountStmt` when `pass_through` is on, the way `wants_tokens` requests the index, or refuse the rule at configuration time without it.
12. **`analysis/lean/layering.py` claims a multi-line module docstring is reported as code lines.** Task 6.2 measured the exact text of `analysis/lean/__init__.py` under both Python grammars on Build 1262: `CountLineCode` 0, `CountLineComment` 13. The paragraph was inferred from a dependency count moving and does not reproduce. Correct or delete it, and re-examine the "docstring-only package initialiser charged as a dependency" base-gate defect recorded from dogfooding, which may share the misreading.
