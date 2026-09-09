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

- [ ] 1.7 Plant the C++ contract cases and update the existing count assertions
  - The same eight cases in the contract project's C++ sources where the language allows them; every existing contract test that asserts the fixture's entity, edge or definition counts is updated with the new count and the reason
  - Done when the extended fixture builds on the licensed install and the existing contract suite is green with the updated counts
  - _Requirements: 5.7, 9.1_

- [ ] 1.8 Render the section in the `init` template
  - The template renders the `[lean]` section commented, one line per off rule with its default in the comment, in the style of the unused rule's line
  - Done when the rendered template round-trips through the loader to the same effective settings, and the template test names every lean key
  - _Requirements: 1.5, 10.2_

- [ ] 1.9 Give the entity fake room before the tasks that need it
  - Measured in task 1.5: the entity fake in the Understand API fakes sits at class coupling 12 against a limit of 12, which is an absolute error with no ratchet, so the next member that couples a new class to it blocks the gate rather than warning. Tasks 3.1 and 5.1 both have to extend it
  - Split it, or restructure it so the members those tasks need cost no new coupling. Do not raise the limit and do not widen the scope exemption: the limit is one this project holds its own code to, and a fake that cannot be extended without buying headroom is a fake that has outgrown its shape
  - Done when the entity fake measures at least two collaborators below the limit with every existing fake test still passing, and a check on the worktree exits 0
  - _Requirements: none of its own; it unblocks 3.1 and 5.1_
  - _Boundary: tests/understand/api_fakes.py and its callers_

- [ ] 2. Rules that need no new extraction, and the net delta
- [ ] 2.1 (P) The over-export rule from today's snapshot
  - A file defining exactly one routine or class, holding no other module-level definition, and depended on by exactly one project file is reported against the affected file, naming the dependant; initialisers and ignored paths are excluded; a file nothing depends on is not this rule's finding
  - Done when unit tests cover the finding, the initialiser exclusion, the zero-dependant case and the two-dependant case, and the rule is off by default
  - _Requirements: 4.1, 4.2, 4.3_
  - _Boundary: analysis/lean/layering_

- [ ] 2.2 (P) The net delta of a change and the optional growth finding
  - Over every routine of an affected or deleted file, on either side, paired by key and then by signature family through the ratchet's existing pairing helper, which the design names as this rule's one dependency inside the analysis layer: statements and code lines after minus before, a missing side counting as zero; no before side means no delta
  - When a maximum net growth is configured, a project-scope finding at the configured severity when statements exceed it
  - Done when unit tests show a deleted file counting negative, a renamed signature paired rather than counted twice, a whole-project run answering no delta, and the growth finding raised only past the limit
  - _Requirements: 7.1, 7.2, 7.4, 7.5_
  - _Boundary: analysis/lean/net_

- [ ] 2.3 (P) Tag-form hints and one worked example per rule
  - A hint per lean rule beginning with `delete:`, `yagni:` or `shrink:`, stating what to cut and what replaces it in one line; an example per rule under the rule's example key, in ponytail's format, drawn from its published examples where one fits
  - The similar-routine rule has two entries: the rule key carries `delete:` and its same-file variant carries `shrink:`, selected through the catalogue's existing variant lookup; the net-growth rule carries `shrink:`
  - The catalogue answers an example by rule name, and an operator's hints table overrides the hint and the example at the rule level
  - Done when a test asserts every lean rule's hint begins with one of the three tags and never with `stdlib:` or `native:`, every lean rule has an example, and an override replaces both
  - _Requirements: 8.1, 8.2, 8.3, 8.6_
  - _Boundary: Hints and examples_

- [ ] 2.4 (P) The lean section of the agent-rules snippet, and the four missing structural rules
  - A section listing every enabled lean rule with its severity and tag, the two tags the Gate never emits and that the agent applies itself, the seven rungs one line each, and how to read the net line
  - The structure section names the unused-routine, duplicate-definition, call-cycle and reachable-complexity rules
  - Done when the rendered snippet with every rule on contains all nine lean rules and all ten pre-existing structural rules, and with every rule off contains the ladder and the two-tag note only
  - _Requirements: 8.3, 8.4, 8.5_
  - _Boundary: Reports_

- [ ] 2.5 The net line, the lean-already line, the example in verbose output and the SARIF run property
  - The human summary prints `net: +N lloc (+M lines) over K routines` with either sign whenever a delta exists; when a lean rule is on, no lean finding was raised and the delta is at or below zero, one line says there is nothing to cut; verbose output prints a finding's example under its hint; SARIF carries the delta as a run property; JSON carries it as a field
  - Done when unit tests cover both signs, the absent delta, the lean-already line, the example under the hint, and the run property in the SARIF document
  - _Depends: 2.2, 2.3_
  - _Requirements: 7.1, 7.3, 7.6, 8.2_

- [ ] 2.6 The lean step of the check pipeline
  - One step that runs each lean rule whose severity is set, gathers their unavailable messages, computes the delta, and hands findings, notes and delta to the pipeline; findings go through scopes, ignores, the severity map and hints like any structural finding; notes are printed once per run; the delta lands on the run result; a rule that is off adds nothing
  - The extractor asks for module-level definitions whenever the over-export rule is on, so the rule has them without a second request
  - The pipeline's finishing step attaches the catalogue's example to every lean finding's details, beside the hint it already attaches
  - Done when a pipeline test with a fixture snapshot shows over-export and the delta in the run result, the example in the finding's details, and an all-off configuration producing a run result identical to today's
  - _Depends: 2.1, 2.2, 2.3, 2.5_
  - _Requirements: 1.6, 2.5, 7.1, 9.4, 9.6_
  - _Boundary: runner/lean, CheckPipeline, Snapshot request and feature probes_

- [ ] 3. Reference-based measurement in the worker sibling
- [ ] 3.1 Routine facts: callers, callees, forwarding target, overrides and unused parameters
  - Distinct project routines calling and called by a routine, the callee's long name when there is exactly one, whether the routine overrides, and the names of parameters no project reference uses, sets or modifies
  - Done when unit tests over the API fakes show a parameter used only through a set counted as used, a receiver counted as unused by the worker (the exclusion is the rule's), a caller in an excluded path not counted, and an overriding routine flagged
  - _Requirements: 1.2, 1.3, 1.4, 2.1, 2.3, 9.7_
  - _Boundary: worker_lean_

- [ ] 3.2 Class facts and module-variable use
  - For a class: whether anything in the project references it, the long names of its derived classes across the inheritance kinds of every language, and the count of project entities referencing it other than itself, its members, its derived classes and their members; for a module-level variable: whether any project reference uses it, including a use as a type
  - Done when unit tests show a class used only in an annotation counted as referenced, a base with one derived class and no other referrer, and a variable read from another module counted as used
  - _Requirements: 1.1, 1.3, 3.1, 9.7_
  - _Boundary: worker_lean_

- [ ] 3.3 Load the sibling from the worker and record the facts in the snapshot
  - The plan carries the two request keys; the sibling is loaded by path once per process and only when a key is set; every routine and class record carries its facts when references are asked, module definitions carry their referenced flag, and nothing is loaded or recorded otherwise; the snapshot cache digest covers both files
  - The extractor asks for references when any reference rule is on, extending the request plan task 2.6 introduced
  - Done when a worker test with references off shows no sibling load and no lean key, with references on shows facts on every record and definition, and the cache key changes when the sibling's source changes
  - _Depends: 3.1, 3.2_
  - _Requirements: 1.6, 2.5, 9.4_

- [ ] 4. The reference rules
- [ ] 4.1 (P) Dead parameters, classes and module variables
  - Three rules over the after side: a parameter of an affected routine that the routine never references, located at the routine and naming the parameter, unless the routine overrides or the name matches the ignore list; an affected class nothing references unless ignored; an affected file's module variable nothing references unless ignored; a missing fact on any affected record yields the rule's unavailable message and nothing else; a deleted entity cannot appear
  - Done when unit tests cover each finding, the override exclusion, the receiver exclusion by default ignore, the unavailable message, and an unreferenced class in an unaffected file not reported
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7_
  - _Boundary: analysis/lean/dead_

- [ ] 4.2 (P) Pass-through routines and single-implementation abstractions
  - A routine with exactly one project caller, exactly one callee and a statement count within the budget, not overriding and not ignored, reported naming caller and callee; a class with exactly one derived class and no other referrer, evaluated when it or its derived class is affected, reported naming the derived class; classes with no or several derived classes never reported
  - Done when unit tests show one caller with two callees not reported, a budget of two accepting a call-and-return body, the base reported on the commit that adds the only derived class, and a base with an outside referrer not reported
  - _Depends: 2.1_
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 3.1, 3.2, 3.3, 3.4_
  - _Boundary: analysis/lean/layering_

- [ ] 4.3 Wire the reference rules into the lean step and refuse them where the build cannot answer
  - The lean step runs the five reference rules; the reference feature is recorded as available on every build, the five configuration keys map to it, and the doctor row prints
  - Done when a pipeline test with a fixture snapshot carrying facts shows the five rules' findings and their notes, and the doctor output lists the row
  - _Depends: 2.6, 3.3, 4.1, 4.2_
  - _Requirements: 1.6, 2.5, 9.2, 9.3_

- [ ] 5. Token measurement and the duplication rules
- [ ] 5.1 The token index in the worker sibling
  - Per project file: one hash per code line from lexeme texts with whitespace, comment, newline, indent and dedent tokens dropped, keeping original line numbers; per recorded routine with a start and an end reference in the same file: a shape mapping identifiers and literals to two classes and keeping keyword, operator and punctuation text, encoded through a vocabulary; a file whose lexer raises is listed as unreadable and contributes nothing
  - Done when unit tests over the lexer fake show the five token classes dropped, line numbers preserved, a renamed copy producing the same shape, a routine without an end reference absent, and an unreadable file listed
  - _Requirements: 5.4, 5.8, 9.7_
  - _Boundary: worker_lean_

- [ ] 5.2 Record the index in the snapshot when a token rule is on
  - The extractor asks for tokens when either token rule is on, extending the request plan task 2.6 introduced; the worker runs the token pass after the entity walk and puts the index on the document; nothing runs otherwise
  - Done when a worker test with tokens off shows no index and with tokens on shows every recorded routine with an end reference in it
  - _Depends: 3.3, 5.1_
  - _Requirements: 5.8, 9.4_

- [ ] 5.3 (P) The duplicate-block rule
  - Windows of the configured minimum length over line hashes indexed for the whole project; for each affected file, each maximal run of windows found elsewhere is one finding with the file's line range and up to three other locations; ignored paths contribute neither side; a missing index yields the unavailable message; unreadable files are noted once
  - Done when unit tests show a block in three files reported against the affected one with two locations, a block one line short not reported, an ignored path silent on both sides, and the note for an unreadable file
  - _Requirements: 5.1, 5.3, 5.5, 5.8_
  - _Boundary: analysis/lean/duplicates_

- [ ] 5.4 (P) The similar-routine rule
  - Shingles over routine shapes indexed for the whole project; for each affected routine at or above the statement minimum, candidates from the index and a sequence ratio against each; a ratio at or above the threshold is one finding naming the twin and the ratio, marked with the same-file variant when the twin is in the same file so the catalogue picks the `shrink:` hint; only affected routines are ever queried
  - Done when unit tests show a renamed twin at 0.95 reported, a 0.7 pair not, a routine below the statement minimum skipped, and a project of a thousand fake routines with one affected routine answering in well under a second
  - _Requirements: 5.2, 5.3, 5.4, 5.5_
  - _Boundary: analysis/lean/similar_

- [ ] 5.5 Wire the token rules into the lean step, probe the lexer and the duplicate-lines metric
  - The lean step runs both token rules; a lexer probe on the doctor's scratch database decides the token feature, a lookup of the duplicate-lines id decides the metric feature with a detail naming the Plugin Manager when absent; the two token keys map to the token feature; both rows print in `doctor`
  - Done when a pipeline test with a fixture index shows both rules' findings, the stubbed install can make each probe answer each state, and a configuration enabling a token rule on a build whose probe failed exits with the configuration error naming the key
  - _Depends: 2.6, 5.2, 5.3, 5.4_
  - _Requirements: 5.6, 5.8, 9.2, 9.3_

- [ ] 5.6 Decide what an architecture-scope duplicate-lines threshold means
  - Measured in task 1.3: the file scope is served end to end and the project scope is served through the population path `CorePercentage` already takes, but an architecture-scope threshold on any plugin metric is accepted by validation and then never requested from the worker, because architecture nodes carry no metrics. The hole is pre-existing and metric-agnostic, and requirement 5.6 names architecture, so this family may not inherit it silently
  - Take one of two answers and write the reason down: extract metrics for architecture nodes, or refuse an architecture-scope threshold at configuration time naming the metric and why. A threshold that is accepted and never evaluated is the silent no-op this project refuses everywhere else
  - Done when an architecture-scope duplicate-lines threshold either produces a finding on the contract project or exits 2 with a message naming the scope, and a test pins whichever was chosen
  - _Requirements: 5.6_
  - _Boundary: config validation and, if extraction is chosen, the worker's architecture walk_

- [ ] 5.7 Make the plugin-metric budget measure a signal instead of noise
  - `tests/contract/test_plugin_metrics_contract.py`'s per-entity budget differences two wall-clock subprocess timings where the signal is 1 to 3 per cent of each run. Measured three times on the extended fixture: 0.269, 0.757 and 0.626 milliseconds per entity, from numerators of 6.5, 18.2 and 15.0 milliseconds against runs of about 550 milliseconds. It is measuring variance
  - It began passing during task 1.6 without being fixed, because the fixture grew and the denominator with it, raising the tolerated numerator from about 52 to about 96 milliseconds. A budget that passes because its denominator grew is weaker than it was, and this family's cost tests must not lean on it
  - Make the measurement signal-dominated rather than re-tightening the number, which would only make it flaky: repeat the read enough times to dominate process startup, or time the API call rather than the process
  - Done when the budget fails if the per-entity cost genuinely doubles, and passes repeatably otherwise, demonstrated by running it several times
  - _Requirements: 9.5_
  - _Boundary: tests/contract/test_plugin_metrics_contract.py_

- [ ] 6. Contract measurements on the licensed install
- [ ] 6.1 Reference kinds and counts on the contract project
  - On the extended fixture: the Python and C++ inheritance kinds answer the derived class; overrides are flagged on both; the caller count agrees with the plugin caller metric for every routine of the fixture, and any disagreement is recorded with its cause; each reference rule reports its planted case and nothing else
  - Done when the contract test passes on Build 1262 and prints the per-language kind table into the research log
  - _Depends: 4.3_
  - _Requirements: 1.3, 1.4, 2.1, 3.1, 10.5_

- [ ] 6.2 Token index coverage and the duplication cases
  - Every routine of the fixture with an end reference is in the index; the planted block and the planted twin are the only duplication findings at the shipped numbers; docstring accounting re-confirmed on the fixture's Python
  - Done when the contract test passes on Build 1262
  - _Depends: 5.5_
  - _Requirements: 5.4, 5.7, 6.5_

- [ ] 6.3 Measure every default on this repository and the cost of a warm check with everything on
  - A whole-project run of this repository with every lean rule on, printing the count each rule produces at its shipped numbers and the first ten findings of each so they can be read; a warm check with one changed line, timed with every rule on and with every rule off
  - The counts and timings go into the research log as a dated table
  - Done when the research log carries the table and the cost test asserts the with-everything time within one half of today's warm check on this repository
  - _Depends: 4.3, 5.5_
  - _Requirements: 5.7, 6.5, 9.1, 9.5_

- [ ] 6.4 Adjust the shipped defaults from the measurement
  - Any lean number or threshold whose measured count on this repository is mostly noise is changed, with the measurement written beside it, in its own commit; a default the measurement confirms is left alone and the confirmation recorded
  - Done when every shipped lean default has a recorded count behind it and the unit tests pin the final values
  - _Depends: 6.3_
  - _Requirements: 5.7, 9.1_
  - _Boundary: config defaults_

- [ ] 7. End-to-end behaviour and the shipped skills
- [ ] 7.1 End-to-end checks through the installed command
  - A repository fixture with lean rules on: the human report shows the net line and, with nothing to cut, the lean-already line; JSON carries the delta and an example; SARIF carries the run property; the agent-rules snippet carries the lean section; `doctor` prints the three rows; a check with every rule off produces a report identical to today's
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
