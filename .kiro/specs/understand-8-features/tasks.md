# Implementation Plan

- [ ] 1. Foundation: baseline, configuration, models, fixture history, and the wrapper commands every later group needs
- [x] 1.1 Record the warm-run baseline on both repositories
  - A repeatable timing script under the test tree that runs a warm check with one changed line and a whole-project check with CPU accounting and prints the per-phase times from the verbose output
  - Run it on this repository and on facdrone before any other task changes behaviour; the figures go into the research log beside the ones measured on 2026-09-05
  - Done when the script runs to completion on this repository and the research log carries a dated table for both repositories
  - _Requirements: 8.1_

- [x] 1.2 Add the configuration keys and the 8.0 metric names, every default keeping today's behaviour
  - Keys: companion SARIF (default off); before-side route with values `auto`, `commit`, `shadow` (default `shadow`, so a repository without configuration keeps the 0.1.0a8 route); snapshot cache (default on -- it changes no finding); unused-routine rule (default off; when enabled its findings are warnings) with an ignore list whose defaults cover dunder methods, tests, `main` and the fixture hooks; accuracy floor (default unset); generated-architecture options table
  - The 8.0 metric ids declared with their scopes and languages so validation, `recommend` and the defaults know them; no new blocking default
  - The `init` template renders the new keys commented, and the loader round-trips them
  - Done when a configuration naming every new key validates, a configuration with none of them produces identical effective settings to before, and a test asserts no 8.0 metric ships as a blocking default
  - _Requirements: 1.3, 5.4, 6.3, 7.3_

- [x] 1.3 Extend the shared models, define the analysis-settings hash, and bump the cache schema
  - Feature availability records with the build string; the analysis result carrying an optional accuracy figure and an optional SARIF path; routine records carrying an optional referenced flag; the sync state carrying the before route, the before database's commit, each side's last accuracy figure, the generated-architecture stamps and a schema number
  - One function that hashes the analysis-affecting settings (languages, include and exclude, architecture and depth, the parse acknowledgements), used by the before-database key and by the snapshot-cache key alike
  - An older sync state is discarded with the existing "rebuilt because" note
  - Done when every model round-trips through JSON, existing fixtures still validate, two settings that differ only in a threshold hash the same, and a test shows a pre-feature sync state discarded once with the note
  - _Requirements: 1.1, 3.5, 4.4, 7.1, 8.6_

- [x] 1.4 Give the contract project a git history and a base commit
  - The contract fixture initialises a repository over the sample sources with two commits and exposes the repository path and the base commit; existing contract tests keep passing unchanged
  - Done when a contract test can ask the fixture for the repository and the base commit, and the existing contract suite is green on 8.0
  - _Requirements: 3.2, 4.3_

- [x] 1.5 Analyse with the SARIF and accuracy switches on request
  - The analysis wrapper takes the two switches as parameters, off by default so a 6.5 argv is unchanged; when asked, it records the SARIF path on the result and parses the accuracy line into a fraction; a build that prints no such line yields no figure and no path
  - Done when the stubbed `und` answering the measured line produces the figure and path on the result, an unasked call produces the pre-feature argv, a 6.5-shaped answer produces neither figure nor path, and the contract test parses a real analysis on 8.0
  - _Requirements: 2.1, 7.1_

- [x] 1.6 Create databases from a commit and record a repository on a database
  - The wrapper creates a database with the commit, reference-database and repository options, and records the repository directory on an existing database; argv order follows the measured commands
  - Done when unit tests pin the argv of both commands and the contract test creates a commit-built database of the contract project's base commit with the reference's file set
  - _Depends: 1.4_
  - _Requirements: 3.1_

- [x] 1.7 List and generate architectures through the wrapper
  - Parse the listing into names and statuses; generate by name with an instance name and options, forcing over an existing instance; decide success by exporting and reading the architecture back into the node type every rule consumes, never by the exit status; an export with no members is a typed error naming the likely cause
  - Done when unit tests pin the parser on the measured 21-line listing and the argv of a generation, a generation on the stubbed `und` answers the node read back, and an empty export raises the typed error
  - _Requirements: 4.1, 4.2, 4.3_

- [x] 1.8 Answer plugin metrics by lookup and tags in the worker's catalogue
  - A `lookup` request key answers, per id, the targets and languages read from the metric's tags, and null for an unknown id; the fake API's 8.0 shape gains tags
  - Done when unit tests cover a found id, an unknown id and a 7.x API without lookup, and the contract test finds the four Python plugin metrics with their measured tags on 8.0
  - _Requirements: 5.1_

- [ ] 2. Feature availability by build
- [x] 2.1 Probe each feature on the doctor's scratch project and store the answer
  - Extend the doctor's analysis probe: parse the generated-architecture listing, create a commit-built database from a one-commit repository made of the scratch project, analyse it with the SARIF and accuracy switches, and ask the catalogue for one plugin metric by lookup; the unused rule is always available; no licence switch is ever part of a probe
  - Store the availability with the build string under the cache root
  - Done when the shell-script installation stub can make each probe answer `not on this build` (unit), a probe that cannot run answers `unverified` with its reason, and the stored record on 8.0 shows six `available` entries (contract)
  - _Depends: 1.5, 1.6, 1.7, 1.8_
  - _Requirements: 1.1, 1.4_

- [ ] 2.2 Print one doctor row per feature
  - One `feature` row per feature with `available`, `not on this build`, or `unverified` and the reason, read from the stored record
  - Done when `doctor` on 8.0 prints six `available` rows (contract) and the fixture seam prints six `unverified` rows with the seam as the reason (unit)
  - _Depends: 2.1_
  - _Requirements: 1.1_

- [ ] 2.3 Refuse a configuration that enables a feature the build lacks
  - Configuration validation reads the stored availability; a key enabling a missing feature stops with the configuration-error exit code naming the feature, the build and the key; `before_side = "commit"` enables the commit route, `auto` does not (it falls back), and a generated-architecture name not in the stored listing is refused with the names the build offers
  - A missing or differently-built availability record, with a feature enabled, fails closed and asks for `doctor`; a run with no feature enabled needs no record
  - A test fixture seeds an availability record for the current build so e2e and contract tests that enable a feature can run
  - Done when an e2e run on a stubbed 6.5 with the commit route enabled exits 2 with the three names in its message, `auto` on the same stub runs on the shadow route, a misspelt generated name exits 2 listing the offered ones, and a run with no feature enabled and no record succeeds
  - _Depends: 2.1, 1.2_
  - _Requirements: 1.2, 1.3, 4.2_

- [ ] 3. Understand's SARIF beside the Gate's
- [ ] 3.1 (P) Copy and re-root Understand's SARIF beside the Gate's
  - Takes the analysis document and, when present, the CodeCheck results document; rewrites the project base URI to the repository root; writes each beside the Gate's file under a distinguishable name; a missing or unparsable source becomes a reported problem, never an exception
  - Done when a synthetic document in the measured shape is re-rooted and written, its results still parse as SARIF 2.1.0 with the repository path, and a missing source yields a problem entry
  - _Requirements: 2.1, 2.4_
  - _Boundary: SarifCompanion_

- [ ] 3.2 (P) Read CodeCheck violations from the inspection's SARIF
  - Rule id, message, artifact path joined to its base, line and column, rule name falling back to the id, and the logical location become the same violation records the CSV reader produces; the runner prefers the SARIF file when the output directory holds one and keeps the CSV reader otherwise; the runner reports where the SARIF file is so the companion can copy it
  - Done when synthetic documents cover a full row, a row without a rule name and a document without runs (refused with the file named), and the contract test for a real inspection is an expected failure naming the CodeCheck-licence reason
  - _Requirements: 2.3, 2.6_
  - _Boundary: CodeCheck SARIF reader, CodeCheckRunner_

- [ ] 3.3 Write the companions from the check command and list them in the output
  - `--sarif PATH` also writes the companions when the key is on; the run output names every file written and every companion problem; the JSON output lists them, with the schema version bumped once for this feature's additions and the schema test updated; the Gate's own SARIF content is byte-identical to before; the exit code is decided by findings alone
  - `explain` has no SARIF output format, so the companions are a `check` concern only; a bullet in the docs task records this reading of the requirement
  - Done when an e2e run on 8.0 with `--sarif` writes the Gate's file and the analysis companion, names both, and a run whose companion source was removed still exits by its findings and reports the missing file
  - _Depends: 1.5, 3.1, 3.2_
  - _Requirements: 2.1, 2.2, 2.4_

- [ ] 4. The before side from the base commit
- [ ] 4.1 Build and reuse the commit-built before database
  - A builder that creates the before database from the base commit with the after database as reference (which registers the comparison pair) and analyses it once with the accuracy switch; a key of base commit, languages, the settings hash from 1.3 and the build; reuse without analysis when the recorded key matches, removal and rebuild when it does not; the sync state records the route, the commit and the before accuracy; a failure at any step is a typed error carrying und's words
  - Done when unit tests show reuse on an identical key and a rebuild on each changed component, and the sync state records route, commit and accuracy after a build
  - _Depends: 1.6, 1.3_
  - _Requirements: 3.1, 3.5, 5.5_

- [ ] 4.2 Decide the before route in the database manager, fall back, and skip an unchanged before analysis
  - `auto` takes the commit route when the stored availability offers it and the shadow route otherwise; `commit` and `shadow` force one; a failed commit build is reported in the run output and the shadow route serves that run; a before side whose recorded commit is unchanged runs no analysis on either route and answers the recorded accuracy
  - The manager asks the wrapper for the accuracy switch when the stored availability says it is available and for the SARIF switch only when the companion key is also on, and records each side's figure in the sync state after every analysis; the manager's method count does not grow, the new behaviour lives in the builder
  - Run the 1.1 script and record the figures in the research log as the first lever
  - Done when unit tests with the stubbed `und` cover the three settings, the fallback, the switch selection, and a warm run issuing no before analysis on either route; and the research log has the post-4.2 timing
  - _Depends: 4.1, 2.3_
  - _Requirements: 3.3, 3.4, 3.5_

- [ ] 4.3 Print the before route in doctor
  - A row with the route and, for a commit-built database, the commit it was built from, read from the sync state
  - Done when `doctor` shows `before route: commit (<hash>)` on 8.0 and `before route: shadow` on the fixture seam
  - _Depends: 4.2_
  - _Requirements: 3.6_

- [ ] 4.4 Prove the two before routes interchangeable and record the comparison pair
  - On the contract project, the before snapshot and the findings of a range check through the commit route equal those through the shadow route; the after database reports the commit-built one as its comparison database
  - Done when the contract test asserts equality of both documents and both finding lists on 8.0, a contract test reads the comparison database from the after database, and the research log records that 1262 ships no comparison metric ids
  - _Depends: 4.2, 1.4_
  - _Requirements: 3.2, 5.5_

- [ ] 5. Generated architectures
- [ ] 5.1 Measure whether a shadow-tree database can generate a git architecture, and decide the after-side route
  - Record the repository directory on the contract project's shadow-built after database and generate `Git Stability`; record in the research log whether the export holds the project's files, and the decision: the shadow database is the route if it does, otherwise 5.2 provides the fallback and the modes that cannot generate are named
  - Done when the research log carries the measurement, the exported member count, and the decision
  - _Depends: 1.7, 1.6, 1.4_
  - _Requirements: 4.3_

- [ ] 5.2 Fallback route: generate on a commit-built database of the after side
  - Only if 5.1 measured that the shadow database cannot generate a populated git architecture: build a commit-built database of the after side's commit where one exists, generate there, and report that worktree and staged checks cannot generate git architectures; otherwise mark this task done with a note pointing at 5.1's measurement
  - Done when either the note is in place or a contract test shows a populated `Git Stability` for a range check through the fallback route
  - _Depends: 5.1, 4.1_
  - _Requirements: 4.3_

- [ ] 5.3 Generate the configured architecture before the rules run, and skip when nothing changed
  - When the architecture setting names a generated one: generate it on the after side after the analysis through the route 5.1 decided (5.2 where it applies), hand the node the wrapper read back to the existing declared-architecture step so the rules, `explain` and the review aids see it; record the repository head and after tree id, and skip regeneration while both are unchanged; print the generation time in verbose output
  - An empty generated export is reported in the run output and the run evaluates against the declared architecture when one exists, and fails as an analysis failure when none does
  - Done when unit tests show the generation step running once and then skipped, the empty-export report and both of its outcomes, a contract test on the contract project shows `Git Stability` exported with every project file placed under a bucket, and `explain` lists the generated nodes with their members
  - _Depends: 5.1, 5.2, 2.3_
  - _Requirements: 4.1, 4.3, 4.4, 4.5_

- [ ] 6. The 8.0 metrics
- [ ] 6.1 Read plugin metrics for recorded entities only
  - A `plugin_metrics` request key lists the ids to read through the entity metric call for recorded entities; population vectors never ask a plugin metric; locale-formatted answers are coerced as today
  - Done when a unit test asserts no plugin metric is requested during a population pass, a snapshot on the contract project carries a `CountClassCoupledModified` value for a recorded class, and the measured cost per recorded routine is within the 2 ms budget in the research log
  - _Depends: 1.8_
  - _Requirements: 5.1_

- [ ] 6.2 Offer plugin metrics where their tags say, and let recommend measure them
  - The catalogue unions the built-in list with the lookup answers whose tags name the language or `Any` and the scope's target; a configured plugin metric absent for a language is reported once per run like any other; `recommend` measures every offered metric through the values 6.1 reads
  - The snapshot request builder sends the `plugin_metrics` key with the configured plugin ids the catalogue offered; nothing is sent when none is configured
  - Done when the contract test shows `CountGlobalsModified` offered for Python routines and `CognitiveComplexity` not, unit tests cover the union and the unavailable path, and `recommend` on the contract project proposes a limit for a plugin metric
  - _Depends: 6.1, 1.2_
  - _Requirements: 5.1, 5.2, 5.3_

- [ ] 7. Unused routines as a structural rule
- [ ] 7.1 Record whether anything references each project routine
  - The worker's whole-project call pass marks each routine record with whether any call or use reference originates in a project file; library files count for nothing
  - Done when the fake project shows the flag true for a called routine and false for one nothing reaches, and the contract project shows the fixture's known uncalled routine as unreferenced
  - _Depends: 6.1_
  - _Requirements: 6.2_

- [ ] 7.2 Report unused affected routines as warnings with an ignore list
  - One warning per affected routine whose flag is false and whose long name matches no ignore pattern; a snapshot without the flag reports the rule unavailable once and evaluates nothing; deleted routines cannot appear because they are absent from the after side
  - The snapshot request builder sends the `record_referenced` key only when the rule is enabled
  - Done when unit tests cover the finding, the ignore list, the unavailable case and a deleted routine, and an e2e run with the rule enabled that adds an uncalled routine prints the warning while the exit code stays 0
  - _Depends: 7.1, 1.2_
  - _Requirements: 6.1, 6.3, 6.4, 6.5_

- [ ] 8. The accuracy of an analysis
- [ ] 8.1 Evaluate the accuracy floor and carry the figures into the check outputs
  - Per-side accuracy in verbose output and in the JSON document; a configured floor raises a non-blocking finding when a side falls below it, never changes the exit code, and is absent without a floor or a figure; the before figure comes from the analysis or, on a reused before side, from the sync state
  - Done when unit tests cover the finding and its absence, the JSON schema test sees the new fields, and an e2e run on 8.0 prints both sides' figures in verbose output
  - _Depends: 1.5, 1.2, 3.3, 4.2_
  - _Requirements: 7.1, 7.3_

- [ ] 8.2 Print the accuracy of the after database in doctor
  - A row with the after database's figure from the sync state, or `not measured` when none was recorded
  - Done when `doctor` on 8.0 prints the figure and the fixture seam prints `not measured`
  - _Depends: 8.1_
  - _Requirements: 7.2_

- [ ] 8.3 Measure how accuracy relates to the snapshot's resolution rate
  - On the contract project, record both figures side by side and what each excludes; keep the resolution rate in the output
  - Done when the research log carries the two figures and the stated relation, and no output field was removed
  - _Depends: 8.1_
  - _Requirements: 7.4_

- [ ] 9. The cost of a warm run
- [ ] 9.1 (P) A snapshot cache keyed on everything that could change the document
  - Key over side, base commit, selection, the settings hash from 1.3, worker source hash, build and schema; get, put, prune to the newest eight, and a listing with commit, age and size
  - Done when unit tests show a hit on an identical key, a miss when each component changes, pruning at nine, and a corrupt entry treated as a miss and removed
  - _Depends: 1.3_
  - _Requirements: 8.2, 8.6_
  - _Boundary: SnapshotCache_

- [ ] 9.2 Record two rings of neighbourhood in one worker pass
  - A `neighbourhood_rings` request key; zero keeps today's behaviour; two records entities of the selected files and of the files one and two dependency steps away, with edges scoped as the existing single-ring rule scopes them
  - Done when the fake project shows the second ring recorded at two and absent at zero, and the whole-project walks run once per call as before
  - _Depends: 7.1_
  - _Requirements: 8.3_

- [ ] 9.3 Narrow a snapshot in-process to what the second pass used to extract
  - A narrowing operation keeps entities in the given files and edges within their one ring, mirroring the worker's edge scoping, and leaves populations, call graph, architecture nodes and edges, parse errors and unavailable metrics untouched
  - Done when a unit test on the fake project shows the narrowed two-ring document equal to a document extracted for the narrowed set directly
  - _Depends: 9.2_
  - _Requirements: 8.3_

- [ ] 9.4 Extract once per side, serve the before side from the cache, and time each phase
  - The snapshot request builder sends `neighbourhood_rings` of two for a check and zero for whole-project mode; the pipeline extracts each side once, resolves the affected set as today, narrows both documents, consults the cache before the before extraction and stores after a miss; verbose output prints each phase's time and `served from cache` when it was; the whole-project mode is unchanged
  - Done when the contract test shows the single-pass narrowed documents equal to the two-pass documents on the contract project, and verbose output shows the cache line on the second of two runs
  - _Depends: 9.1, 9.3, 4.2_
  - _Requirements: 8.2, 8.3_

- [ ] 9.5 Prove the cache changes nothing, meet the target under the named configuration, and show the cache in doctor
  - An e2e run of one change reports identical JSON findings cached and uncached; run the 1.1 script after 9.4 as the second lever and once more at the end on both repositories, under the default configuration and again with the commit route; `doctor` prints the cache's entry count and newest age; a check with no selected files still runs no analysis and no extraction
  - Done when the e2e equality test passes, the research log shows the warm run with one changed line under 15 s on this repository under the default configuration with findings identical to the baseline run and the commit-route figure beside it, the no-selection run unchanged, and `doctor` on 8.0 shows the cache row
  - _Depends: 9.4_
  - _Requirements: 8.4, 8.5, 8.6, 8.7_

- [ ] 10. Integration and validation
- [ ] 10.1 Full suites and the tool's own gate over the whole change
  - Unit suite, contract suite on 8.0 with the new contract tests and the one expected failure, e2e suite; the tool's own gate over the commit range with zero blocking findings; mypy and ruff clean
  - Done when every suite is green on 8.0.1262 with the counts recorded in the research log, and the range gate exits 0
  - _Requirements: 1.3, 2.6, 3.2, 8.7_

- [ ] 10.2 Documentation pages for the shipped features and the contributors' note
  - The Understand 8.0 reference page describes each feature with its measurement, including which comparison metrics the base ratchet already covers and why the companions are a `check` concern; the configuration reference carries the new keys and their defaults; the CLI reference carries the doctor rows and the companion files with the GitHub upload example; a contributors' note names `undmcp` and `und ai` with what was measured and the network-boundary constraint
  - Done when the strict docs build passes and each of the four pages carries the named section
  - _Depends: 10.1_
  - _Requirements: 2.5, 5.5, 9.1, 9.2, 9.3_

## Implementation Notes
- 1.1: the harness is `tests/perf/warm_run_timing.py`, run as `uv run python tests/perf/warm_run_timing.py <repo> [--mode in-place|clone] [--target-wall 15]`. It is not collected by pytest (no `test_` prefix); its pure parts are tested in `tests/perf/test_warm_run_timing.py`. It refuses `in-place` on a tree with uncommitted work and clones instead, which is how facdrone must be measured while another session is working there. Measure the same probe file each time or the figures are not comparable -- it picks the median tracked `.py` under `src/` by default.
- 1.1 (measured, affects task 4.2 and 9.x): `analysing the before database` is **0.0 s** on a genuinely warm run on both repositories, so requirement 8.2's "no analysis on an unchanged before side" is worth nothing by itself; the four snapshot extractions are 83% (scitools-hook) and 89% (facdrone) of the warm run. Do not expect task 4.2's timing run to improve the headline figure -- the improvement is 9.1 plus 9.4. Projected warm runs after both levers: 10.2 s here, 14.2 s on facdrone, against a 15 s target that names this repository.
- 1.2: `[analysis]` is a new section for one key, and adding a 20th pydantic model to `config/models.py` was refused by the gate -- `file.CountDeclClass` 19 -> 20 against 6, and `class.CountClassDerived` on `StrictModel` 18 -> 19 against 8. Splitting the file answers neither: `CountClassDerived` counts everything sharing the base wherever it lives. `[scope.schemas]` in `scitools-hook.toml`, which already carried exactly this reasoning for `models/**`, was extended to `src/scitools_hook/config/models.py` with the new measurement in its comment. **This is an operator decision and is flagged as one**; reverse it and no settings model can ever be added again.
- 1.2 (reading recorded, affects 2.3 and 9.x): requirement 1.3 says every feature ships off, and requirement 8.4 states the 15 s target unconditionally. `understand.snapshot_cache` therefore ships **on** -- it changes no finding (8.7) and the target is unreachable without it -- while every key that changes what a run reports ships off. `tests/config/test_understand_8_settings.py` pins both halves so flipping either is a deliberate act with a failing test attached.
- 1.2: the 8.0 metric ids are in `config/metric_names.py` as `PLUGIN_METRICS`, with the `Metric.lookup(id).tags()` targets and languages recorded verbatim -- Understand tags C and C++ separately while the Gate names the pair `C++`, and that mapping belongs in task 6.2 where availability is decided, not in the declaration. None is a shipped threshold (5.4), and two tests hold it that way.
- 1.3: `SyncState.before_commit` already existed and means "the commit the before side represents", so the design's separate `before_db_commit` was not added -- one field, both routes. New: `schema_version` (defaults 0 so a legacy file reads as stale; `_save_state` stamps `CACHE_SCHEMA`), `before_route`, `analysis_settings`, `accuracy` per side, `generated_archs`.
- 1.3: `analysis_fingerprint` is in `config/fingerprint.py`, not `models/`, because it is a function over `Settings`. It hashes what the worker request derives from settings -- languages, include/exclude, architecture, depth, architecture options, the duplicate-definitions flag, the ignore regexes, the acknowledged parse errors -- plus the *metric set* the thresholds name (`scope.metric` with the statistics prefix), never the limits or severities. **Drift risk for task 9.1**: `SnapshotExtractor.request()` builds the real request and this replicates its inputs; if a settings-derived key is added there and not here, a stale snapshot is served. Pin it with a test that walks both.
- 1.3: adding `EntityRecord.referenced` changed the canonical wire form, so `tests/fixtures/snapshot_{before,after}.json` were regenerated -- the diff is exactly 15 records each gaining `"referenced": null`. Any later worker field does the same; `tests/models/test_fixtures.py` is the test that says so.
- 1.4: the history lives in `alpha` only (`ROOTS[0]`), reachable as `sample_project.repo`, `.base_commit` and `.history.head`. `beta` stays a plain directory, which is what makes the two-root test of requirement 4.4 still mean what it meant. The base commit differs from HEAD in `main.py` alone -- one docstring line -- so the working tree is byte-identical to what every pre-existing contract test expects while a before/after comparison still has something to compare. git runs with `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1` and explicit author/committer, so a developer's global hooks or template directory cannot reach the fixture.
- 1.5 (**API change, affects every later task that analyses**): `UndCli.analyze` now takes one `selection` -- `ALL`, `None` for `-changed`, or a list of files -- instead of `files` plus `all`, and two plain switches `accuracy` and `sarif`. The design's `AnalysisReports` value object was tried first and refused by the gate: `UndCli` is 5 over its `CountClassCoupled` limit already (16 against 12, pre-existing), so **any new type it names trips the ratchet**. Merging the two selection parameters kept the count at five with no new class, and removed the expressible-but-meaningless `files=[], all=True`. Expect the same wall in tasks 1.6 and 1.7: `create -gitcommit` needs no new type, but `GeneratedArch` does -- plan to return a plain list or to move the arch commands to module functions as `_list_arches` already is.
- 1.5: the accuracy line is `N of M parsed files had no errors or warnings (P%)`, recorded as `N/M` rather than the rounded percentage. It counts files with **no warning either**, which is why this repository scores 27% with zero errors. A build that prints no such line answers `None`, which is not zero.
- 1.6 (**measured constraint for task 4.1**): `-refdb` requires the new database to be a **sibling** of the reference. In another directory `und` prints a warning about relative paths and exits 1. The Gate's cache satisfies this already; `create_from_commit` guards it with both paths named.
- 1.6: `create_from_commit` and `set_git_repository` are module functions taking the wrapper, following the user's instruction of 2026-09-05 to use module functions rather than growing `UndCli`. `GitSource` is named only in the module, so the class's coupling is untouched. The two remaining *existing* arch methods (`export_arch`, `declare_architecture`) were **not** moved: 18 database tests and both fakes intercept them as methods, and a module function would reach the real subprocess machinery through a fake wrapper. New commands go to module functions; existing seams stay.
- 1.6: the contract fixture's base commit now differs from head in **code**, not a docstring -- `main` returns a literal and imports nothing there. A docstring difference measured identically on both sides, because `CountLineCode` does not count one, so every before/after test over this project would have passed without comparing anything.
- 1.7 (**the module-functions approach paid off**): `GeneratedArch`, `list_generated` and `generate_arch` are module functions in `und_cli.py`, so `UndCli`'s coupling stayed at 16 while three new names entered the module. `und_arch.py` could not host them -- `und_cli` imports it, so the reverse would be circular.
- 1.7 (measured on Build 1262, all four shapes): `arch -generate` prints `<name>: generated` and **the exit status is unreliable** -- 0 on one database, 1 on another, 87 members exported either way. Success is decided by exporting and reading back; failure by the `Error:` line, of which there are three: `architecture not found`, `architecture name already in use` (without `-force`) and `invalid -options: unknown option` (which then lists the options). `und -db X arch -list` works, so the wrapper's own db placement needs no special case.
- 1.7: the design's `GeneratedEmptyError` was implemented and then removed -- `errors.py` holds 9 classes against a limit of 6 and the gate refused a tenth. An empty generated architecture raises `AnalysisFailedError` with the cause in its hint instead. Every caller treats any failure from generation the same way (report, fall back to the declared architecture), so one type loses nothing; task 5.3 does not need to tell them apart.
- 1.8: the `lookup` request key answers `{id: {"targets": [...], "languages": [...]}}` from `Metric.lookup(id).tags()`, and `None` both for an id the build does not know and for a 7.x API with no `lookup` at all -- neither is an error, because requirement 1.3 asks a 6.5 install to behave as it always did. Tags that are neither a target nor a language (`Category:`, `Solution:`, a bare `Dependencies`) are left alone rather than guessed at. Two contract tests prove the four measured metrics on the real build **and** that they are absent from the kind listing, which is the whole reason the second source exists.
- 2.1: `understand/features.py` holds the probe and the store; `doctor.py` gained one field and two calls, no class (its file is already 7 classes against a limit of 6). The probe runs inside the analysis probe's scratch directory, turns *that* into a one-commit repository for the `-gitcommit` question, and ignores `*.und` in it so a database is never committed. Measured on the real build: all six available, 21 generated architectures offered, and the whole of `doctor` still under a second and a half longer than before.
- 2.1: the SARIF answer is **the file**, not the exit status -- a build that ignored an unknown switch would otherwise read as offering the feature while writing nothing. The refusal detail carries `und`'s own sentence first and the wrapper's message only as a fallback: the message leads with the whole command line, which on a temporary directory fills the 300-character detail on its own and pushes the useful line off the end.
- 2.1: `_one_commit` reads the **process** environment for git, not `options.env`, which is why the unit tests find git even though `isolated_env` blanks `PATH`. That is deliberate -- git is ambient, like the interpreter -- but it means a test that wants the `unverified` branch has to make git fail some other way.
