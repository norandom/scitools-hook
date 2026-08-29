# Implementation Plan

- [ ] 1. Foundation: project scaffold and error contract
- [x] 1.1 Create the uv project and package skeleton
  - `pyproject.toml` with package name `scitools-hook`, `requires-python = ">=3.12"` (no upper bound), hatchling backend, console script `scitools-hook`, runtime deps (`typer`, `rich`, `pydantic`, `platformdirs`) and dev deps (`pytest`, `pytest-cov`, `ruff`, `mypy`, `jsonschema`); tool sections for ruff (line length 100), mypy strict on `src/`, pytest
  - `src/scitools_hook/` with empty layer packages (`config`, `models`, `understand`, `git`, `analysis`, `analysis/structure`, `report`, `runner`, `cli`), `__version__`, and a placeholder typer app that prints help
  - `README.md` stub with the install line and the note that the PyPI `understand` package is unrelated
  - Done when `uv sync` succeeds and `uv run scitools-hook --help` prints usage; `uvx --from . scitools-hook --help` also works
  - _Requirements: 12.1, 12.2_

- [x] 1.2 Define exit codes and the typed error hierarchy
  - `ExitCode` enum (0 ok, 1 violations, 2 config, 3 understand-not-found, 4 license, 5 analysis, 6 not-a-git-repo, 70 unexpected) and `GateError` subclasses carrying exit code, hint and context fields (tried locations, file/key, command/stderr, available architectures)
  - Unit test asserting every subclass maps to a distinct documented code
  - Done when `tests/test_exit_codes.py` passes and the enum is the single source imported by later tasks
  - _Requirements: 1.3, 1.4, 1.6, 12.5, 12.7_

- [ ] 2. Configuration: metric grammar, models, defaults, loading
- [x] 2.1 Implement metric-name grammar, reducer registry, synthetic-metric registry and scope kinds
  - `Scope` type; parse `PREFIX:Metric` (exactly one colon; unknown prefix is a configuration error); name → callable registry for AVG/MEDIAN/MEDIANHIGH/MEDIANLOW/MEDIANGROUPED/MODE/STDEV/VARIANCE; synthetic metric registry (`CountParams`, `CountDeclMethodNonStub`) with scope binding; scope → Understand kind strings
  - Done when unit tests cover valid and invalid names and every registry entry resolves to the intended `statistics` function
  - _Requirements: 3.4, 3.5_
  - _Boundary: config/metric_names_

- [x] 2.2 Implement configuration models, built-in defaults and the `init` template
  - Pydantic models for settings, thresholds (scalar = max, table = `{max}`/`{min}`), ignore regexes, structure rules (architecture, depth, cycles severities, fan limits, layer rules, coupling rules, max new deps), codecheck, baseline, hints, output, understand (`home`, `db_location`, `api_mode`)
  - Built-in defaults covering the routine, class, file and project metrics named in the requirements, default exclude patterns, default per-rule severities
  - Commented TOML template renderer for `init` and an overwrite guard
  - Done when defaults validate into `Settings` without a config file and the rendered template parses with `tomllib` and validates into `Settings`
  - _Requirements: 2.5, 3.1, 3.3, 3.7, 3.9, 5.1, 5.2, 5.3, 5.4_
  - _Boundary: config/models, config/defaults, config/template_
  - _Depends: 2.1_

- [x] 2.3 Implement the settings loader with precedence, provenance and validation
  - Merge order defaults < user file (`~/.config/scitools-hook/config.toml`) < repo file (`scitools-hook.toml`) < `SCITOOLS_HOOK_*` env < CLI overrides; deep-merge tables, replace lists; provenance map per leaf
  - Validation: unknown keys/scopes, wrong value types, invalid regexes, unknown metric names when a `MetricAvailability` protocol (declared in `config`, satisfied later by the Understand catalogue) is supplied — each error names file and key
  - Done when tests show a repo value overriding a user value while untouched keys keep their default provenance, the `init` template round-trips through the loader unchanged, and each invalid-config case raises `ConfigError` with the expected file/key
  - _Requirements: 3.2, 3.6, 3.8, 3.10_
  - _Boundary: config/loader, config/validate_

- [ ] 3. Shared models and test infrastructure
- [x] 3.1 Define the shared data models layer
  - `models/`: snapshot types (`EntityKey`, `EntityRef`, `EntityRecord`, `DepEdge`, `ArchNode`, `ProjectSnapshot`, `ParseError`), findings types (`Finding`, `RunResult` with `schema_version = 1`, `EffectiveThreshold`, `TightenedLimit`, `HighestValue`, rule-name grammar helper), change types (`AffectedSet`, `EntityDelta`, `DependencyDelta`, `ImpactSet`, `GraphFile`, `GraphTarget`, `ChangeSummary`), understand records (`UnderstandEnv`, `AnalyzeResult`, `LicenseStatus`, `RawViolation`, `ExtractRequest` carrying files, kind strings per scope, metrics, synthetic ids, population metrics, ignore regexes, architecture and depth so the worker needs nothing from config), git records (`StagedChange`, sync targets, `SyncDelta`), cache state (`CachePaths`, `SyncState` with index/worktree/commit after-targets, `repo_id()` and cache-root rule), `Baseline`/`BaselineIssue`, and the `Progress`/`CommandLog` protocols with no-op implementations
  - Two fixture snapshots (`before`/`after`) for a small synthetic project in `tests/fixtures/` exercising added, removed, modified and unchanged entities, a new cycle, a layer violation and a fan-out increase, plus a `snapshot_fixture` loader
  - Done when fixtures validate into `ProjectSnapshot`, every model round-trips through JSON losslessly, and `repo_id()` is stable for the same git common dir
  - _Requirements: 4.2, 7.1, 9.7_
  - _Boundary: models_
  - _Depends: 2.1, 2.2_

- [x] 3.2 Build the test infrastructure
  - `tests/conftest.py` with a temporary-git-repo builder (init, commit, stage, unstaged edits, renames), a `FakeCommandLog` implementing the `CommandLog` protocol, a `contract` marker that skips unless `SCITOOLS_HOME` is set and `und license` (run as a plain subprocess) reports a valid license, and a session-scoped contract fixture that builds `before`/`after` databases from `tests/fixtures/sample_project/` (Python + C++ files, two roots) using plain `und create/add/analyze` subprocess calls
  - Done when `uv run pytest` shows contract tests skipped on a machine without a license, one smoke test using the repo builder passes, and on a licensed machine the fixture yields two openable databases
  - _Requirements: 4.1, 11.1_
  - _Depends: 3.1_

- [ ] 4. Analysis core: rule evaluators (pure, fixture-driven)
- [x] 4.1 (P) Implement threshold evaluation over elements and populations
  - Absolute max/min checks per entity in scope; stats-prefixed thresholds reduced over the population vectors, with reducer failures (e.g. `MODE` on a multimodal vector) recorded once per metric instead of raising; ignore-regex filtering with per-scope ignored counts; unavailable-metric bookkeeping per language; highest-value tracking per metric; threshold findings leave `before = None` and `hint` empty (filled by the ratchet step and the pipeline respectively)
  - Done when tests confirm a value over max, a ratio under min, an `AVG:` violation, an ignored entity excluded and counted, a reducer failure and an unavailable metric each reported once, and highest values ranked
  - _Requirements: 3.4, 3.6, 5.1, 5.2, 5.3, 5.4, 5.5, 5.6_
  - _Boundary: analysis/thresholds, analysis/population_

- [x] 4.2 (P) Implement the ratchet evaluator and finding classification
  - Before/after comparison per `EntityKey` (worse = higher for max limits, lower for min limits); new entities skip ratchet; the ratchet step also populates `before` on every threshold finding whose key exists on both sides; `classify` then derives pre-existing status, applies strict mode and sets `blocking = error ∧ ¬preexisting`
  - Done when the test matrix (worse / same / better × new / existing × under / over limit × strict on / off) produces the expected `blocking` and `preexisting` flags
  - _Requirements: 4.4, 4.5, 4.6, 4.7, 7.9_
  - _Boundary: analysis/ratchet, analysis/classify_

- [x] 4.3 (P) Implement dependency-graph utilities and new-cycle detection
  - Directed graph with reference counts, Tarjan SCC, cycle identification for file and architecture levels, "new" = SCC not contained in any before-SCC; findings list members and closing references
  - Done when tests show a new 3-file cycle reported once, a pre-existing cycle not reported, and an architecture-level cycle reported with node names
  - _Requirements: 6.1, 6.2_
  - _Boundary: analysis/structure/graph, analysis/structure/cycles_

- [x] 4.4 (P) Implement layer, fan and coupling rules
  - Layer rules over new edges between architecture nodes; fan-in/out thresholds for files and classes plus fan-out ratchet for affected entities; new-dependencies-per-file limit; node-pair reference limits
  - Done when tests show a forbidden new edge reported with rule name and nodes, an allowed edge silent, fan-out growth reported as ratchet, a file gaining 6 new deps flagged at limit 5, and a node pair over its reference limit flagged
  - _Requirements: 6.3, 6.4, 6.5, 6.6_
  - _Boundary: analysis/structure/layers, analysis/structure/fan, analysis/structure/coupling_

- [x] 4.5 (P) Implement baseline parsing, application, tightening and capture (no file I/O)
  - Tolerant parse from a raw dict reporting per-entry issues; effective limit = min(config, baseline) for max limits and max for min limits with `limit_source` recorded; tightening that never raises a value; capture of current maxima from a snapshot
  - Done when tests show a baseline lowering an effective limit, a run tightening one value and leaving another, a corrupt entry yielding an issue while other limits still apply, and capture producing one entry per configured threshold
  - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6_
  - _Boundary: analysis/baseline_

- [x] 4.6 (P) Implement the affected-set resolver
  - Affected files = staged non-deleted paths plus files whose dependency set differs between before and after; affected keys = entities defined in those files; neighbourhood = direct dependents and dependencies; deletions-only handling
  - Done when tests show a dependent file pulled in because its dependency set changed and a deletions-only change yielding an empty file set with a non-empty neighbourhood
  - _Requirements: 4.2, 4.10_
  - _Boundary: analysis/affected_

- [x] 4.7 (P) Implement the CodeCheck violation mapper
  - `RawViolation` rows → `Finding(kind="codecheck", rule="codecheck.<id>")` with configured severity, repo-relative path/line and message; `hint` left empty
  - Done when a fixture of raw violations maps to findings with the configured severity and repo-relative paths
  - _Requirements: 6.9_
  - _Boundary: analysis/codecheck_

- [x] 4.8 (P) Implement the change-summary builder
  - Per-file entity deltas (added/removed/modified with before/after/delta metrics), dependency deltas grouped by architecture node with cross-boundary marking, rankings by delta and by value, impact sets attached, architecture paths shown, database path and open-in-GUI command included
  - Done when tests over the fixture snapshots produce the expected deltas, rankings and cross-boundary flags
  - _Requirements: 9.1, 9.2, 9.3, 9.5, 9.7, 9.8_
  - _Boundary: analysis/change_summary_

- [ ] 5. Report layer: renderers and agent guidance
- [x] 5.1 (P) Implement the hint catalogue and the human renderer
  - Hint lookup order rule → metric → generic with configuration overrides; findings grouped by file, ordered by severity then overshoot ratio; summary line with counts and exit-code meaning; quiet mode; colour disabled when not a TTY or `NO_COLOR`, forced by `--color`; agent instruction block appended when blocking
  - Done when snapshot tests of the rendered text match for a blocking run, a warnings-only run, quiet mode and a non-TTY run (no ANSI sequences)
  - _Requirements: 7.2, 7.3, 7.6, 7.8, 10.4_
  - _Boundary: report/hints, report/human_

- [x] 5.2 (P) Implement JSON and SARIF renderers
  - JSON: single document from `RunResult` (`schema_version` 1) and nothing else; SARIF 2.1.0 with one rule per distinct `Finding.rule`, levels error/warning/note (pre-existing), repo-relative URIs with `%SRCROOT%`, `startLine ≥ 1`
  - Done when JSON round-trips to `RunResult` and the SARIF output validates with `jsonschema` against the 2.1.0 schema stored in `tests/fixtures/`
  - _Requirements: 7.4, 7.5, 7.7_
  - _Boundary: report/json_out, report/sarif_

- [x] 5.3 (P) Implement change-summary renderers
  - Text, Markdown (merge-request friendly tables) and JSON views of `ChangeSummary`, graph file references, final open-in-GUI command line
  - Done when snapshot tests match for all three formats over the fixture summary
  - _Requirements: 9.4, 9.6, 9.8_
  - _Boundary: report/markdown_

- [x] 5.4 (P) Implement the agent-rules renderer and marker insertion
  - Deterministic Markdown snippet from effective thresholds and settings (sorted, no timestamps) covering limits, structural rules, the command to run, JSON reading guidance and the blocked-commit workflow; insert/replace between `<!-- scitools-hook:begin/end -->` markers preserving surrounding content
  - Done when rendering twice yields identical text and inserting twice into a file yields one snippet with the rest of the file byte-identical
  - _Requirements: 10.1, 10.2, 10.3_
  - _Boundary: report/agent_rules_

- [ ] 6. Understand adapter: API worker, location, `und` wrapper, database lifecycle
- [x] 6.1 Implement the stdlib-only API worker skeleton with `ping`, `catalogue` and `archs`
  - `worker.py` importing only the standard library and `understand`; JSON request on stdin / result on stdout when run as a script; `dispatch(op, request)` for in-process use; error envelope mapping `UnderstandError` texts (`NoApiLicense`, `DBUnableOpen`, `DBOldVersion`) to typed error names; `catalogue` returns available metrics per language and scope kind; `archs` returns root architecture names and nodes at a depth
  - Done when `python -I worker.py ping` (no package on `sys.path`) answers with the API version or a license error envelope, and a test asserts the module has no `scitools_hook` imports
  - _Requirements: 1.2, 1.4, 6.7, 6.8_
  - _Boundary: understand/worker_

- [x] 6.2 Implement the worker `snapshot` operation
  - Entities defined in requested files via the kind strings carried in the request and container-file references; requested metrics plus the synthetic ids listed in the request (`CountParams`, `CountDeclMethodNonStub`); ignore regexes from the request applied to entity lists and populations; library and unresolved entities excluded; file and class dependency edges with reference counts for requested files and direct neighbours; architecture membership trimmed to depth; population vectors for prefixed metrics; unavailable metrics per language; parse-error passthrough
  - Done when, run against the contract-fixture database, the returned document validates into `ProjectSnapshot` with expected entities, edges and metrics for the sample project
  - _Requirements: 3.5, 5.5, 6.7, 9.7_
  - _Boundary: understand/worker_
  - _Depends: 6.1, 3.2_

- [ ] 6.3 Implement the worker `impact` and `graphs` operations
  - Reverse-reference expansion to a depth with per-depth lists and totals; SVG export of butterfly graphs for routines/classes and depends-on graphs for files into an output directory, recording per-target failures as warnings
  - Done when a contract test against the fixture database produces at least one SVG per graph type and an impact set with depth-1 callers for a sample function
  - _Requirements: 9.4, 9.5_
  - _Boundary: understand/worker_
  - _Depends: 6.1, 3.2_

- [ ] 6.4 (P) Implement installation discovery and probe-driven verification
  - Candidate list in precedence order (CLI, env, config, `und` on PATH, per-OS well-known directories) with the source recorded; `verify` takes injected probes (und version, in-process import run in a child process, upython ping), chooses `api_mode` per preference (auto = upython when present), and raises `UnderstandNotFoundError` listing every location tried and both probe reasons
  - Done when tests with a fake directory layout and stub probes resolve each precedence step, choose in-process vs upython correctly, and the not-found message lists all candidates
  - _Requirements: 1.1, 1.2, 1.3_
  - _Boundary: understand/locator_

- [ ] 6.5 (P) Implement the `und` command wrapper and its fake
  - Subprocess calls with timeouts and `-quiet` for `version`, `license`, `create` (`-local`, languages), `add` (root with excludes), `remove`, `analyze` (`-files @list` / `-all`) parsing errors and warnings, `list -metrics settings`, `codecheck -files`; non-zero exit → `AnalysisFailedError(command, stderr)`; license text → `LicenseError`; every call recorded in `CommandLog` with timing; `tests/fakes/FakeUndCli` recording calls and returning configured `AnalyzeResult`s
  - Done when tests with a stubbed executable verify argument construction, timeout handling and error mapping for each command
  - _Requirements: 1.2, 1.4, 2.6, 12.8_
  - _Boundary: understand/und_cli_

- [ ] 6.6 Implement `ApiRunner`, the typed adapter wrappers and their fakes
  - `ApiRunner.run(op, request)` executing in-process (`worker.dispatch`) or via `upython worker.py <op>` subprocess with timeout and `CommandLog`; error envelopes → `LicenseError` / `AnalysisFailedError`; wrappers `SnapshotExtractor` (builds the self-describing `ExtractRequest` from settings: kind strings, synthetic ids, ignore regexes), `ImpactExpander`, `GraphExporter`, `MetricCatalogue` (satisfies the config `MetricAvailability` protocol) validating worker output into models and raising `ArchitectureNotFoundError` with the available list; `tests/fakes/FakeApiRunner` (fixture dicts per op) and `FakeSnapshotExtractor` (fixture snapshots)
  - Done when unit tests with `FakeApiRunner` validate a fixture document into `ProjectSnapshot`, and a contract test shows both modes returning identical snapshots for the same database
  - _Requirements: 1.2, 1.4, 3.8, 5.5, 6.8_
  - _Boundary: understand/api_runner, understand/snapshot, understand/impact, understand/graphs, understand/catalogue_
  - _Depends: 6.2, 6.3_

- [ ] 6.7 (P) Implement the CodeCheck runner
  - Run a named or exported CodeCheck configuration over a file list into a temp output directory and parse the violations CSV into `RawViolation` records
  - Done when a fixture CSV parses into records with check id, path, line and message, and a contract test runs a bundled configuration on the sample project
  - _Requirements: 6.9_
  - _Boundary: understand/codecheck_
  - _Depends: 6.5_

- [ ] 7. Git adapter: plumbing, shadow synchronisation, hook installation
- [ ] 7.1 Implement the git repository wrapper
  - Discovery of root/git-dir/common-dir (raising `NotAGitRepositoryError`), `HEAD` (unborn-safe), staged changes via `diff --cached --name-status -z -M` with rename pairs, index tree id via `write-tree`, name-status between refs, index export via `checkout-index` (`-a` or `-z --stdin` with paths, trailing-slash prefix), commit export via `archive | tar`, tracked files, hooks directory honouring `core.hooksPath` and the global hooks path; all calls logged with timing
  - Done when tests in a temporary repo verify staged-vs-unstaged content export, rename detection and hooks-path resolution with and without `core.hooksPath`
  - _Requirements: 4.1, 4.3, 12.5, 12.8_
  - _Boundary: git/repo_

- [ ] 7.2 (P) Implement incremental shadow synchronisation
  - Targets index, worktree and commit into the `CachePaths` shadows; full export on first use, otherwise apply add/modify/delete/rename deltas from the recorded ref or tree id in `SyncState`; include/exclude pattern filtering; target-kind change forces full re-sync; returns the `SyncDelta` for database updates
  - Done when tests show an unstaged edit absent from the after shadow, a second run touching only changed paths, a rename handled, and nothing written inside the repository working tree
  - _Requirements: 2.2, 2.5, 4.1, 4.3, 10.5_
  - _Boundary: git/shadow_
  - _Depends: 7.1_

- [ ] 7.3 (P) Implement the hook shim, installer and pre-commit framework definition
  - POSIX shim template with identifying header, `SCITOOLS_HOOK_SKIP` notice/exit 0, `scitools-hook` on PATH else `uvx scitools-hook` else message + exit 3, soft-fail only for exit codes ≥ 2 under `SCITOOLS_HOOK_SOFT_FAIL`, chaining to a preserved previous hook; installer with refuse/force+chain/uninstall+restore/global path; `.pre-commit-hooks.yaml` with `require_serial: true`, `pass_filenames: true`, entry `scitools-hook check --files`
  - Done when tests install into a temp repo, refuse a second install, force-install over an existing hook and chain to it, uninstall restoring the original, and a shell test of the shim with a stub `scitools-hook` returning 1 blocks while returning 3 with soft-fail passes
  - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7, 11.9_
  - _Boundary: git/hooks, git/hook_template, .pre-commit-hooks.yaml_
  - _Depends: 7.1_

- [ ] 8. Database lifecycle and runner pipelines
- [ ] 8.1 Implement the database manager
  - Cache root selection (user cache dir or `.git/scitools-hook/`), `ensure_side` (sync shadow → create database with configured or detected languages, `-local` → add root with excludes / remove deleted → analyze changed files or all on first run → parse errors), `rebuild`, `paths`, extension-based language detection, progress messages for phases over 5 s
  - Done when tests with `FakeUndCli` and a temp-repo `ShadowSync` show first run create + full analyze, second run analyzing only changed files, rebuild discarding databases, and cache placement under gitdir vs user cache
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.6, 2.7, 2.8, 4.11_
  - _Boundary: understand/database_
  - _Depends: 6.5, 7.2_

- [ ] 8.2 Implement the run context, baseline store and doctor pipeline
  - `RunContext` assembling settings with provenance, repository (optional), Understand environment via locator + real probes, adapters and command log; documented test seam `SCITOOLS_HOOK_FAKE_UNDERSTAND=<dir>` substituting the fixture-backed `FixtureUndCli`/`FixtureApiRunner` shipped in `understand/fake.py` (reading `analyze.json` and `<op>.<side>.json` from the directory; `tests/fakes` reuse them); `BaselineStore` reading/writing the baseline file at the configured path (missing file → none, unreadable → issue); `DoctorReport` with install directory, versions, both API probes and chosen mode, license status, git status, cache paths and sync state, effective configuration with sources, problems list
  - Done when doctor runs with fakes inside and outside a repository and with Understand missing, producing problems entries instead of raising, and `BaselineStore` round-trips a baseline file
  - _Requirements: 1.5, 8.1, 8.6, 12.5_
  - _Boundary: runner/context, runner/baseline_store, runner/doctor, understand/fake_
  - _Depends: 6.4, 6.6_

- [ ] 8.3 Implement the check pipeline (integration)
  - `CheckPipeline.run(selection)`: staged/worktree/files/all handling, empty-selection early exit, before side only when `HEAD` exists and the mode needs it, first-pass extraction for selected files, affected-set resolution, second-pass extraction for affected ∪ neighbourhood with configured metrics and populations, baseline load/apply, evaluators in order thresholds → ratchet → structure → codecheck → classify, hint attachment via the catalogue for every finding, adaptive tightening and save, `RunResult` assembly with counts, ignored counts, unavailable metrics, parse errors, tightened limits and highest values
  - Done when pipeline tests with fake adapters and fixture snapshots yield the expected findings (all carrying hints) for a staged run, an all-project run without ratchet findings, an empty staged set exiting cleanly, and a deletions-only change
  - _Requirements: 4.1, 4.2, 4.3, 4.8, 4.9, 4.10, 4.11, 6.9, 7.2, 8.2, 8.3, 8.5_
  - _Boundary: runner/check_
  - _Depends: 8.1, 8.2_

- [ ] 8.4 Implement the explain and baseline pipelines
  - `ExplainPipeline` over a selection or a commit range (both shadows synced to commit targets, recorded as the `commit` after-target in `SyncState`), building the change summary, optional graph export into a chosen directory and impact expansion to the configured depth; `BaselineCmd` capturing current maxima for every configured threshold and saving through `BaselineStore` at a chosen or default path
  - Done when tests with fakes produce a summary referencing exported graph files and an impact set, and a baseline file with one entry per configured threshold
  - _Requirements: 8.1, 9.1, 9.2, 9.3, 9.4, 9.5, 9.8_
  - _Boundary: runner/explain, runner/baseline_cmd_
  - _Depends: 8.3_

- [ ] 9. Command-line interface
- [ ] 9.1 Implement the typer application, shared options, command registration and error handling
  - Global options (`--scitools-home`, `--config`, `--api-mode`, `--verbose`, `--color/--no-color`, `--quiet`), selection option group (`--staged | --worktree | --all | --files`, mutually exclusive, hook-aware default), `--format`/`--output`, exit-code mapping for every `GateError`, unexpected-error one-liner with traceback under verbose, findings to stdout and diagnostics/progress/command log to stderr, no prompts anywhere; all ten subcommand modules registered as stubs so later tasks touch only their own module
  - Done when CLI tests confirm each error class yields its exit code, conflicting selection flags are rejected on a stub command, and `--help` lists all subcommands with exit codes documented
  - _Requirements: 1.6, 7.6, 7.7, 12.1, 12.3, 12.4, 12.6, 12.7, 12.8_
  - _Boundary: cli/app, cli/common_

- [ ] 9.2 (P) Implement `check`, `explain` and `baseline` commands
  - `check` with `--strict`, `--adaptive/--no-adaptive`, `--show-highest`, `--sarif PATH`; `explain` with `--range A..B`, `--graphs`, `--impact`, `--out DIR`; `baseline` with `--file`; each wired to its pipeline and renderer
  - Done when help documents every option, and CLI tests with fake pipelines render human, JSON, SARIF and Markdown outputs to stdout or `--output`, `--format json` writes nothing but the document to stdout, and `check` outside a repository exits with the not-a-git-repository code
  - _Requirements: 4.7, 4.8, 5.6, 7.4, 7.5, 8.1, 9.6, 11.8, 12.1, 12.4, 12.5_
  - _Boundary: cli/check, cli/explain, cli/baseline_
  - _Depends: 8.4_

- [ ] 9.3 (P) Implement `init`, `config`, `db`, `doctor`, `install-hook`, `uninstall-hook` and `agent-rules` commands
  - `init` (refuses overwrite without `--force`), `config` (effective values with sources), `db path|rebuild|analyze`, `doctor` (works outside a repository), `install-hook --force --global`, `uninstall-hook`, `agent-rules [--write FILE]`
  - Done when CLI tests exercise each command against fakes and a temp repo, including `doctor` and `config` succeeding outside a repository
  - _Requirements: 1.5, 2.7, 2.8, 3.9, 3.10, 10.1, 10.3, 10.5, 11.1, 11.6, 11.9, 12.1, 12.5_
  - _Boundary: cli/config_cmd, cli/db, cli/doctor, cli/hooks, cli/agent_rules_
  - _Depends: 8.2, 8.4_

- [ ] 10. Validation: contract, end-to-end and self-gate
- [ ] 10.1 Contract tests against a real Understand installation
  - Sample repository with Python and C++ files; two databases from different roots; `EntityKey` matching for functions, methods, overloads, classes and files; worker parity across modes; metric availability per language incl. synthetic metrics; `Directory Structure` nodes at depth 2; dependency edges; graph SVG export; CodeCheck CSV parsing
  - Done when the contract suite passes on a licensed machine and is skipped elsewhere, and any unmatched entity kinds are documented in `research.md`
  - _Requirements: 3.5, 4.4, 5.5, 6.7, 6.9, 9.4_
  - _Depends: 6.6, 6.7_

- [ ] 10.2 End-to-end hook and workflow tests in temporary repositories
  - Using the `SCITOOLS_HOOK_FAKE_UNDERSTAND` seam with the installed CLI on PATH, pointing the variable at a "violating" fixture directory and then at a "fixed" one — developer path: install hook → stage a routine over the nesting limit → real `git commit` blocked with hint → fix (re-point fixtures) → commit passes; skip variable bypasses with notice; soft-fail variable turns an infrastructure failure into a warning but not a violation; pre-commit style `--files` invocation evaluates only the given files with `HEAD` as before state; agent path: `agent-rules --write` then `check --worktree --format json` before staging
  - Done when the end-to-end suite passes with the fake seam and, when licensed, with the real adapters
  - _Requirements: 4.9, 10.4, 10.5, 11.4, 11.5, 11.8_
  - _Depends: 9.2, 9.3_

- [ ] 10.3 (P) License-free quality gates
  - Import-direction test enforcing the allowed-import matrix (`config → models → understand | git → analysis → report → runner → cli`; adapters import neither each other nor anything above; `worker.py` imports nothing from the package, checked under `python -I`); `ruff` and `mypy --strict` clean; coverage report ≥ 85% on `src/` excluding the real-Understand adapter modules
  - Done when the import-direction test, lint, type check and coverage threshold all pass in one `uv run` invocation documented in the README
  - _Requirements: 12.2_
  - _Depends: 9.3_

- [ ] 10.4 Self-gate and timing check (license-gated)
  - Run `scitools-hook check --all` on this repository with default thresholds; remediate up to five findings by extraction/simplification and document any remainder as intentional with a rationale; measure a warm-cache staged run on this repository and record the timing in `research.md`
  - Done when the tool reports zero blocking findings on itself (or documented exceptions) and the recorded warm staged run is under 30 seconds
  - _Requirements: 4.11_
  - _Depends: 10.1, 10.3_

## Implementation Notes
- 1.1: RED-phase runs must use an isolated env (`uv run --isolated` or a scratch venv) — after `uv sync`, `--no-project --with` layers onto `.venv` and no longer fails. `[tool.ruff] extend-exclude = [".kiro", ".claude"]` keeps ruff format out of spec Markdown code fences. Dev deps are a `[dependency-groups] dev` group: plain `uv sync` installs them. Typer `no_args_is_help` exits 2 on bare invocation — CLI tasks must reconcile with ExitCode.CONFIG_ERROR=2.
- 1.2: Requirements 1.6/12.7 reconciled to the design: unexpected errors exit 70 (distinct from analysis failure 5). Keep every `GateError` subclass inside `errors.py` (the distinct-code test only sees imported subclasses). `ConfigError` family takes context via `**context: Unpack[TypedDict]` to respect the 5-parameter limit; unknown kwargs are dropped at runtime — add `tests` to mypy `files` in 10.3. Add a `py.typed` marker in 10.3.
- 2.1: `config/metric_names.py` exports `Scope`, `SCOPES`, `ELEMENT_SCOPES`, `is_valid_scope`, `STATS_REDUCERS`, `MetricRef`, `parse_metric_name`/`format_metric_name`, `SyntheticMetric`/`SYNTHETIC_METRICS`, `SCOPE_KINDS`. `SCOPE_KINDS` deliberately omits `project`/`arch` — build `ExtractRequest.kinds_by_scope` from `SCOPE_KINDS.items()`, never iterate `SCOPES` (the worker must never call `db.ents("")`). Metric ids must be identifiers.
- 2.2: `Settings.ratchet.strict` (not `ratchet_strict`) — design aligned. Structure severities live on `fan_severity`, `new_dependencies_severity`, `LayerRule.severity`, `CouplingRule.severity`; `structure.fan_in`/`fan_out` share one model field so their `DEFAULT_SEVERITIES` entries must stay equal. Every model is `extra="forbid"`. Carry into 2.3: errors raised inside `ThresholdSpec` surface with pydantic loc `('thresholds', <index>, ...)`, not a dotted `thresholds.<scope>.<metric>` key — the loader must map index→key (order is preserved by `threshold_entries`) to satisfy 3.8, or fix the `thresholds_from_tables` docstring at models.py:111.
- 2.3: `load_settings(repo_root, cli_overrides, env)` merges defaults < user (`$XDG_CONFIG_HOME`) < repo < env < CLI; tables deep-merge, lists replace, and a *limit* is the merge unit (a bare number becomes `{max=n}` and keeps the default severity/ratchet). Env convention: `SCITOOLS_HOOK_<PATH>__<SEGMENTS>` (`__` separators, lower-cased except the metric under `thresholds` and the rule under `hints`); names without `__` (SKIP, SOFT_FAIL, FAKE_UNDERSTAND) are not settings. Pydantic errors are relocated to dotted `thresholds.<scope>.<metric>` keys with the source file. `MetricAvailability` lives in `config/validate.py` so config never imports `understand` — the catalogue (6.6) satisfies it; use `attach_source(error, provenance)` for that later pass.
- 3.1: `ProjectSnapshot.entities` is a Python `dict[EntityKey, EntityRecord]` but serializes as a sorted record list (pydantic's model-keyed-dict default is lossy `str(key)`); `EntityKey.token`/`from_token` is the canonical reversible string used for JSON object keys and class-edge endpoints; set fields serialize sorted so JSON is byte-deterministic. Model-level invariant is only `blocking ⇒ severity == "error"` — whether a pre-existing finding blocks is `analysis/classify`'s decision under strict mode (design reconciled). `config/models.ThresholdSpec.rule` re-implements the rule grammar because `config` may not import `models`; keep the two in sync. Open: mypy `comparison-overlap` error at tests/test_exit_codes.py:62 (from 1.2) must be fixed when 10.3 adds `tests` to mypy.
- 3.2: `tests/conftest.py` provides `git_repo` (hermetic builder; strips GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE/GIT_AUTHOR_*/GIT_COMMITTER_* so it works inside a hook environment — without that, collection breaks), `FakeCommandLog`/`FakeProgress`, the `contract` skip gate (`understand_probe`, `SCITOOLS_HOME` then `und -isundlicensed`) and `sample_databases` (real `und` build of `tests/fixtures/sample_project/{before,after}`). Verified licensed: 2 databases build and open.
- 3.2 Understand facts for the adapter tasks: a `.und` database is a DIRECTORY, not a file — never use `is_file()`. Understand injects `<SCITOOLS_HOME>/conf/understand/python/python3/builtins.py` (and ~600 `builtins.*` routine entities) into the Python file set; `und list files` hides it but the Python API does NOT, so `SnapshotExtractor` MUST filter entities to the project root. Later contract tests belong in `tests/contract/`. Annotate fakes as `log: CommandLog = FakeCommandLog()` so mypy checks the signature (runtime_checkable only checks attribute presence).
- 4.1: `evaluate_thresholds(snapshot, keys, specs, catalogue_unavailable=None, ignore=None) -> ThresholdOutcome` (frozen dataclass: findings, highest, ignored_counts, unavailable, reducer_failures keyed by RULE not metric). Two review rounds were needed: element-scope stats prefixes, entity-level unavailable discovery and the snapshot-seed path each survived deletion untested until pinned by mutation-proven tests. `analysis` never sees `ConfigError` — `reduce` raises `ValueError` on an unknown prefix.
- 4.1 constraints on the extractor (6.2): population vectors must arrive ALREADY ignore-filtered (`ExtractRequest.ignore`), and plain `project`-scope metrics are read from single-element population vectors — if the extractor omits them, `project.MaxCyclomaticStrict`/`MaxNesting` never fire. `reducer_failures` has no `RunResult` field: 8.3 surfaces it on stderr.
- 4.1 self-gate debt for 10.4: `_seed_unavailable` in analysis/thresholds.py nests to depth 4, over the tool's own default `MaxNesting: 3`.
- 4.2: pipeline order is `evaluate_thresholds` -> `attach_before` -> `evaluate_ratchet` -> `classify`; without `attach_before` no threshold finding can ever be pre-existing (8.3 must honour this). `classify` infers the broken bound from the finding, valid only for `kind="threshold"`, so ratchet findings are never pre-existing; `preexisting` is additive, so a structural evaluator may declare its own. `evaluate_ratchet(keys: Collection[...])` — narrowed from `Iterable` because it re-iterates per spec and a generator silently truncated the results.
- 4.2 open design question for operators: a limit carrying BOTH max and min freezes the metric (any movement is "worse") and the message wording reads oddly for a move toward the band. No default threshold is two-sided; revisit if operators start setting both.
- 4.3: Tarjan is ITERATIVE on purpose (verified at recursionlimit 60 against 5000-node chain and ring); SCC correctness cross-checked against a reachability partition over 120 random graphs. "New" is per-component (`not any(cycle <= before ...)`), NOT against the union of before-cycles — merging two known cycles is a new, larger cycle, and the union rule is a silent false negative now pinned by test. Findings are deterministic byte-for-byte (members and closing_refs sorted).
- 4.3 renderer obligation for 5.1/5.2/5.3: `Finding.path` on an arch-level structural finding is an architecture node path, not a repo-relative file.
- 4.4: `evaluate_fan` keeps the design's 5-param signature so it carries no severity — findings default to `structure.fan_severity` ("warning") and an override must reach them through the SeverityMap `classify` applies. **8.3 MUST project `settings.structure.fan_severity` onto BOTH `structure.fan_in` and `structure.fan_out`**, or an operator's override never lands. Self-references are excluded from fan and new-dependency counts; edges inside one node or touching no node are silent.
- 4.4 for the config task: a fan limit written as `{ min = N }` validates but silently switches that direction off (max AND ratchet). `config/validate.py` should reject a fan limit with no `max`.
- METHOD (all future mutation testing): clear `__pycache__` or set `PYTHONDONTWRITEBYTECODE=1` before a mutated run — two same-size mutations of one file can be masked by a stale `.pyc`, which produced a false "survivor" in 4.4.
- 4.5: `capture` records the WORST value for the bound direction (max for a `max` limit, MIN for a `min`-only one) — the max would set the floor to the best file and fail all the others. `apply` narrows a two-sided limit on its upper bound only and records `source="baseline"` only when the baseline actually won. `tighten` only ever lowers, so a `min` entry re-tightens only via an operator `capture`.
- 4.5 HAZARD for 8.3: do NOT feed `ThresholdOutcome.highest` to `tighten` — it is a max over the AFFECTED element subset only, so a run touching one simple file would tighten a project-wide baseline to that file's value and fail everything next run. Use a full-snapshot `capture`. Also pass the run's `started_at` as `captured_at` so no production path reads the clock inside analysis.
- 4.5 for 10.3: add `addopts = "--import-mode=importlib"` to `[tool.pytest.ini_options]` — pytest's default prepend mode rejects duplicate test basenames across directories (that is why `tests/analysis/test_baseline_rules.py` is not `test_baseline.py`), and the collision will recur.
- 4.6: dependency comparison ignores targets in `deleted_files` on BOTH sides, reconciling 4.2 with 4.10 — losing an edge because the target was deleted is not a dependency change, but an edge gained or lost against a SURVIVING file still is (an equal-cardinality swap counts: that is the shape that closes a new cycle). Sets are compared by identity, never by size. Empty `staged` short-circuits to an empty AffectedSet (4.9).
- 4.6 HAZARD for 8.3: req 4.10 says structural rules run on the "remaining affected files", and under this reading those land in `neighbourhood`, not `files`. The pipeline MUST pass `files | neighbourhood` as `keys_files` to `evaluate_fan`, or a deletions-only change evaluates nothing. `find_new_cycles`/`evaluate_layers` take whole edge lists and are unaffected.
- 4.7: `map_violations(violations, severity, repo_root=None)` maps rows only — running `und` is 6.7's job. Paths are normalised treating BOTH `/` and `\` as separators (without it every path on Windows stays absolute and breaks req 7.1 and the SARIF contract); the accepted cost is that a POSIX name containing a backslash is split, now a decision on record with its own test. A path outside `repo_root` stays ABSOLUTE. CodeCheck's line 0 maps to `line=None` (SARIF needs `startLine >= 1`).
- 4.7 HAZARD for 5.1/5.2: `Finding.entity` is always `None` for codecheck findings — the qualified name req 7.1 asks for lives in `details["entity"]`, so the renderers must read it there. For a path outside the repo root, SARIF cannot use `uriBaseId: "%SRCROOT%"`; omit it or emit a `file://` URI. `line=None` (also produced by `structure/fan.py`) means OMIT `region`, never emit `startLine: 0`.
- 4.7 HAZARD for 8.3: `repo_root` defaults to `None`, so a pipeline that forgets to pass it silently emits absolute paths with no type error. Also decide dedup: identical CodeCheck rows are NOT deduplicated by the mapper, and `RunResult.blocking_count` is a validated invariant.
- 4.7 for 6.7: the CSV parser must strip whitespace/newlines from check ids — `codecheck_rule` only rejects a blank id, and a newline inside a rule name corrupts human output and severity-map keys. Name its test file distinctly (`tests/understand/test_codecheck.py` would collide until 10.3 adds `--import-mode=importlib`).
- 4.8: `build_summary(before, after, affected, paths, aids=None)` — `impact`/`graphs`/`top_n` are grouped into a frozen `ReviewAids` because the design's 7 params exceed the 5-param limit. Rankings are per (entity, metric) pair by MAGNITUDE (a removal of 22 lines outranks an addition of 6) with ties on entity key then metric name; each row is narrowed to the metric it ranks on. Known cost: one entity can take several slots and crowd others out of `DEFAULT_TOP_N` — revisit against configured limits when real change sizes are seen.
- 4.8: `EntityDelta.arch_path` was added to `models/change.py` (the one authorised field) because `render_summary(summary, fmt)` sees only the `ChangeSummary` — a helper the renderer must call cannot satisfy req 9.7, and `--format json` would have omitted it. `architecture_index(after, before)` stays public for 8.4's graph targets; the after side wins so a deleted file still resolves.
- 4.8 for 5.3/8.4: `GUI_EXECUTABLE = "understand"` and `open_command` are defined here (shlex-quoted, pointing at the AFTER database). Do not define a second version of that command.
- 5.1 HAZARD for 9.1/9.2 (CLI): `render_human` returns a plain string containing its own SGR escapes and a fixed-width layout. The CLI must print it RAW — never through a `rich` Console, which would parse `[...]` in a `Finding.message` as markup and re-wrap the layout. Colour is decided by the pure `report.human.resolve_color(force, is_tty=, no_color=)`; the CLI supplies `sys.stdout.isatty()` and `"NO_COLOR" in os.environ`, because the renderer reads neither.
- 5.1 for 8.3: `render_human` derives its summary counts from `result.findings`, so the pipeline must fill `RunResult.warning_count`/`preexisting_count` from that same list or the JSON and human summaries will disagree. It also renders no hint line when `hint == ""` — the pipeline must attach `HintCatalogue.hint(finding.rule, finding)` or req 7.2 is lost in every format.
- 5.1: quiet mode deliberately suppresses the 10.4 agent block (7.8's "only the summary line and blocking findings" is exhaustive and its condition is narrower); a caller wanting the block must not request quiet.
- Self-gate debt for 10.4 (with the 4.1 nesting item): several modules exceed the tool's own `file.CountDeclClass: 3` default — `config/models.py` (15), `errors.py` (8), `models/snapshot.py` (8), `report/human.py` (5).
- 5.1: two review rounds. `overshoot_ratio` (display) and `_limit_distance` (ordering) are deliberately DIFFERENT: a min-bound breach is measured `limit/value` so 0.002 against a 0.1 minimum ranks 50x out, and a ratchet finding has NO limit distance at all — it is reported for getting worse, not for leaving a limit, so measuring it ranked a healthy metric above a real breach (the defect this task shipped and the review caught). Ratchet findings are tagged `worse than before`, never `Nx limit`. A file with no comments scores 0 against the `min: 0.1` default, so the `value <= 0` guard is on the shipped path and is pinned against a ZeroDivisionError.
- 5.2: `tests/fixtures/sarif-schema-2.1.0.json` is the official OASIS schema, byte-identical to the published file (sha256 ad6db498…, 115632 bytes, CRLF preserved) — validation is real `jsonschema` and was proven live by rejecting 12 deliberately broken documents. `render_json` is `model_dump_json(indent=2)` verbatim: no re-sorting, no recomputed counts, because round-trip equality is the done criterion — filling `warning_count`/`preexisting_count` consistently is 8.3's job. SARIF: arch findings get a `logicalLocations` entry and NO `physicalLocation`; out-of-repo paths get an absolute `file://` URI with no `uriBaseId`; a line below 1 omits the region.
- 5.2 for 10.3: `jsonschema` ships no stubs, so `tests/report/test_sarif.py` carries a `# type: ignore[import-untyped]`. Adding `types-jsonschema` to the dev group removes it — but `warn_unused_ignores` will then flag that ignore.
- 5.2 for feature validation: a non-finite `Finding.value`/`limit`/`seconds` would serialize to `null` in JSON (breaking round-trip) and the invalid token `Infinity` in SARIF. Unreachable today — `analysis/baseline.py` guards `isfinite` at the one operator-editable boundary — but re-check if metric provenance widens.
- 5.3: `render_summary(summary, fmt)` for text/markdown/json. Paths print VERBATIM here (deliberately unlike 4.7/5.2, which build machine-consumed URIs) — a reviewer opens these on this machine. Order is the producer's everywhere except `impact`, ordered by `EntityKey.token`. The open-in-GUI line is read from `summary.open_command`, never rebuilt. A metric row falls back to the union of BOTH sides, so a removed entity whose metrics never moved still shows its numbers.
- 5.3 DECISION for 9.2 (CLI `explain --impact`) and feature validation: req 9.5's "list ... with counts" is satisfied in the JSON view only — the text and markdown views count and never name, because the blast radius is unbounded. Reviewed and accepted (design assigns 9.5 to understand/impact + change_summary, and 9.6 makes JSON a first-class view); record the decision rather than rediscovering it.
- 5.4: `render_rules` is deterministic by construction — thresholds sorted by `SCOPES` order then metric name, fan by `FAN_KEYS`, layer/coupling by name; no clock, path, version or environment string is ever printed, so the block can be committed to CLAUDE.md/AGENTS.md and regenerated without churn. It prints the EFFECTIVE limit (a baseline only ever narrows) and attributes it when the baseline won.
- 5.4 for 9.3: `insert_between_markers` RAISES `ConfigError` on unbalanced, duplicated, nested or out-of-order markers, and on a snippet containing a marker — repairing would either leave a stale block an agent reads as authoritative or delete operator content. The function takes a string, so it cannot set `ConfigError.file`: the CLI must attach the target path (precedent: `config/loader.attach_source`). Marker-like text inside a fenced code block IS treated as a real block; a mixed file raises rather than corrupting.
- 6.1: PROVEN END-TO-END on the licensed machine — `upython worker.py ping` returns 6.5.1204, `archs` reads real Directory Structure nodes. `understand` is imported LAZILY inside `_import_api`, never at module level, so the module unit-tests on an unlicensed machine. `dispatch(op, request)` is the single implementation both modes use; `main` always exits 0 when the answer is parseable — the envelope IS the answer.
- 6.1 envelope contract for 6.6: `NoApiLicense`, `DBAlreadyOpen`, `DBUnableOpen`, `DBOldVersion`, `DBUnknownVersion`, `DBCorrupt`, `UnderstandError`, `ApiUnavailable` (interpreter cannot import the module → `UnderstandNotFoundError`, NOT `LicenseError`), `ArchitectureNotFound` (carries `available` → `ArchitectureNotFoundError`), `BadRequest`, `UnknownOperation`.
- 6.1 LIVE FINDING for 6.6/8.1: a real seventh error text exists — `DBEmpty: database is empty`, raised by `understand.open` on a partially built `.und` directory. It currently falls through to the generic `UnderstandError`, which reads as an analysis failure rather than "rebuild the database". Map it explicitly when wiring `ApiRunner`/`DatabaseManager`.
- 6.1 `archs` depth semantics: depth 0 is the architecture itself, and a branch ending ABOVE the requested depth contributes its own leaf — otherwise a shallow directory's files vanish from every node-level structural rule.
- 6.1 for 10.3: `tests/understand/test_worker.py` and `tests/test_infra.py` both use `from conftest import ...`, which BREAKS under `--import-mode=importlib`. Convert both to fixtures before enabling that option.
- LIVE FINDING (verified against the licensed install, affects 6.2/4.1/5.1/8.3): **not every Understand metric returns a number.** `RatioCommentToCode`, `CCViolDensityLine` and `CCViolDensityCode` come back as locale-formatted STRINGS — on this machine `'0,00'`, with a comma decimal separator — at entity scope AND from `db.metric(...)` at project scope. `RatioCommentToCode` is in the shipped defaults (`file`, `{min: 0.1}`), so this is on the default path. The adapter MUST coerce: accept both `,` and `.` as the decimal separator, treat an unparseable value as *unavailable* (never zero, never a string), and coerce project metrics and population vectors too — everything above the adapter is typed `dict[str, float]` and would otherwise fail validation, silently skip the threshold, or raise `TypeError` on `value < limit.min`.
- RATCHET FOUNDATIONS VALIDATED against the real before/after databases (probe under `upython`, both sides): (1) `Db.archs()` returns an EMPTY list for every routine and class (0 of 5 before, 0 of 7 after) — architecture membership must come from the container file; (2) `ent.parameters()` is IDENTICAL across two databases built from different roots for every shared entity (`self,x`, `self,scale`, `a,b2,c`, `v`; `None` for a class) — the `EntityKey` overload discriminator is stable, which is what makes the before/after join work at all; (3) `depends()` reference counts are stable on an unchanged edge (`a.py -> b.py` stays at 3) while the genuinely new edge appears only in `after` — so a ref-count-only change is distinguishable from a topology change, which `analysis/affected.py` depends on. Caveat: a 5-7 entity sample proves the mechanism, not behaviour under overloads, templates, anonymous entities or multi-TU C++ headers — those stay on 10.1's contract list.
- CRITICAL LIVE FINDING (verified; binds 6.2 and every consumer of `EntityKey`): **`ent.longname()` on a FILE entity returns the ABSOLUTE path, including the shadow root.** Two databases built from different directories over token-identical sources produced 16 unmatched keys, every one a file entity differing only by its embedded root. Routine and class longnames are stable. Left unfixed, every file-scope threshold silently loses its ratchet, `attach_before` never fills `before` for a file finding, and no file violation can ever be classified pre-existing — the same failure `research.md` records for `uniquename`, reaching file entities by another door. **For `scope == "file"` the key's `longname` MUST be the root-relative path (`ent.relname()`, POSIX separators), never `ent.longname()`.** The committed models and fixtures already assume this (`tests/fixtures/snapshot_*.json`, `tests/analysis/test_affected.py::file_key`). Contract test required: extract from both sample databases (different roots) and assert the file-scope keys compare EQUAL across sides.
- PRODUCT PROPERTY VALIDATED (whitespace-only reformat, token-identical sources, 21 entities): **0 differing (entity, metric) pairs and an identical edge set with identical reference counts.** The ratchet does not fire on reformatting — a change that only touches whitespace cannot produce a false positive. In the gated metric set only `RatioCommentToCode` returns a string.
- ENTITYKEY VALIDATED FOR C++ (overloads, templates, multi-TU headers — the false-match risk deferred from 10.1). Two databases built from different roots over a header with an overloaded method, a function template instantiated three ways, and an inline helper included from two translation units:
  - **Overloads discriminate correctly**: `Shape::area` appears twice with `parameters()` = `int w` and `int w,int h`. Zero duplicate keys within either database.
  - **Templates are ONE entity, not one per instantiation**: `largest` appears once as kind `Function Template` with the generic signature `T left,T right`, despite being instantiated with int, double and long. Metrics live on the template. No key collision, and the ratchet sees a single entity.
  - **A header included from two TUs yields ONE entity**, attributed to its defining file (`src/shape.h`), not duplicated per including translation unit.
  - **Cross-root join: 6 keys, 6 matched, 0 unmatched, 0 metric differences.**
  - Routine longnames use C++ scoping (`Shape::area`) and are NOT absolute paths — reinforcing that the absolute-path problem is specific to FILE entities.
  - CAVEAT on the template result: because a template is ONE entity carrying the generic signature, its metrics are measured once on the template body. A template that is cheap in one instantiation and pathological in another shows a single set of numbers, so the gate cannot see per-instantiation complexity. This is the right behaviour for the ratchet (one edit, one entity, no double-counting) but it is a real limit on what the gate detects in heavily templated C++ — state it in the README rather than letting a user infer coverage the tool does not have.
- CRITICAL LIVE FINDING #2 (verified independently twice; binds 6.2, 6.6, 8.1): **`ent.relname()` is NOT root-relative when any analysed file sits directly in the analysis root** — it is then prefixed with the root directory's own name.
  ```
  root with only subdirectories:       relname='pkg/core.py'      arch child 'Directory Structure/pkg'
  root with a top-level file + subdir: relname='mixed/main.py'    arch child 'Directory Structure/mixed'
  ```
  On the shadow layout the design mandates (`<cache>/before`, `<cache>/after`) applied to an ordinary repository with a top-level `main.py`, the real worker returns **0 entity records on BOTH sides**, architecture nodes named `Directory Structure/before` vs `Directory Structure/after`, and a document that still validates into `ProjectSnapshot` — a fully green run that gates nothing. Affects essentially every real repo (`setup.py`, `conftest.py`, `main.go`, `index.js`). Every fixture we own has only subdirectories, which is exactly why 1254 tests passed.
  **Rule: derive a file's key path from `ent.longname()` (absolute, reliable) made relative to the analysis root, which the caller must pass in the request; use `relname()` only as a fallback.** Apply the same normalisation to architecture members, and resolve the node corresponding to the analysis root BEFORE applying `depth` so node paths never embed the shadow basename.
- 6.2 handoffs recorded during review: (a) req 3.6's "report how many entities were ignored" is currently UNREACHABLE — the worker drops ignored entities from the snapshot, so `analysis/population.filter_keys` can only ever count zero; 6.6/8.3 need a spec decision (count in the worker and return it, or stop promising the count). (b) `ExtractRequest` has neither `side` nor `parse_errors` and forbids extras — 6.6 must add both; its `depth` is `ge=1` while the worker accepts 0 for `archs`. (c) `project` population vectors are keyed by the STRIPPED metric name, so a config carrying both `project.CountLineCode` and `project.AVG:CountLineCode` collides (not on the default path). (d) `worker.py` exceeds the tool's own FILE-level defaults (67 functions vs 25, 4 classes vs 3, 569 lines vs 500) with no routine-level violation — this is FORCED by the single-file import-purity rule and should be recorded in 10.4 as an accepted deviation, not repaid by splitting.
- 6.2 SPEC DECISION (controller, made during review round 2): **a file with no architecture node at depth >= 1 is attributed to the WALK-ROOT node itself** (`archs: ["Directory Structure"]`), never `[]`. Rationale: it removes an internal inconsistency (a root holding only files already behaved this way, while a root holding a file beside subdirectories returned `[]`); it keeps `setup.py`/`conftest.py`/`main.go` inside the node-level rules instead of silently exempt — that is a COVERAGE question, not a ratchet question, so "identical on both sides" does not justify silence; and req 9.7 asks for the architecture path of an affected entity that belongs to an architecture, which these do.
- 6.2 SECOND REGRESSION FOUND IN REVIEW (round 2): inferring the inserted shadow level from "the node's own name is not the first component of its members' paths" is NOT unique to that level. Understand roots `Directory Structure` at the parent of the deepest common ancestor of the ANALYSED files (non-source files never enter it), so any repo whose sources all sit under one nested path presents the same signature and has a REAL directory stripped. Measured: sources under `src/app/` made `src/app/entry.py` return `archs: []` from `snapshot` while 6.1's `archs` op placed it in `Directory Structure/app`; and adding one file a level up flipped the node set from `Directory Structure/core` to `Directory Structure/src`, so an arch ratchet compares nothing across that change. **Rule: identify the inserted level by IDENTITY — descend only when the single child's basename equals `basename(root)` — never by inference.**
- 6.2 handoff: requiring `root` only removes the forgot-it case. A WRONG root (`/completely/wrong`) still yields 0 entities, shadow-named nodes, exit 0 and a valid document — indistinguishable from an empty change. The worker must make that diagnosable (typed envelope, or a resolved-file count in the document) so 6.6/8.1 can fail loudly.
- 6.2 THIRD REGRESSION, found in the final review and fixed by the controller: `_arch_edges` dropped every architecture dependency whose TARGET is the walk-root node. The deletion had been justified by measuring only the OUTGOING direction — `Arch.depends()` on the walk root is genuinely empty (it holds every analysed file, and Understand does not report parent->descendant). The INCOMING direction is real: measured `Directory Structure/pkg -> Directory Structure` at 3 refs when `pkg/core.py` imports a root-level `main.py`. Dropping it produced a self-contradicting document — the node published, the file edge marked `crosses_arch`, and `arch_edges` empty — so the coupling rule summed 0 and the arch-cycle rule saw an isolated node. Affects every repo with an imported top-level module (`main.py`, `settings.py`, `conftest.py`, `manage.py`). Fix: the walk root is a valid edge endpoint exactly when the answer publishes it as a node.
- 6.2 envelope addition for 6.6/8.1: **`AnalysisRootMismatch`** — raised when a non-empty `root` matches no file of the database; carries `found`, up to three real long names. Map it as a CONFIGURATION error (the caller passed the wrong directory), not an analysis failure. Add it to the 6.1 envelope list alongside `NoApiLicense`, `DBEmpty` and the rest. This resolves the earlier "make a wrong root diagnosable" handoff.
- 6.2 REMAINING HOLE (accepted, recorded rather than fixed): `_check_root` returns early once ANY file resolves, so a root pointing at a real SUBDIRECTORY of the shadow (e.g. `<shadow>/before/pkg`) is not refused — it yields zero entity records and `arch_nodes` members carrying the shadow name via the `relname` fallback, exit 0. Tightening `any` to `all` was measured safe (non-library file entities outside the analysis root = 0 on every database tried, including C with a local `#include`), so 6.6 or 8.1 should either tighten it or validate the root against the shadow it just built.
- 6.2 handoffs for 6.6: `ExtractRequest` must gain `root` (REQUIRED), `side` and `parse_errors`; it forbids extras, so the op cannot be called until they exist. Its `depth` is `ge=1` while the worker accepts 0 for `archs`. The `archs` op still reports `nodes[].files` from `relname()`, which on a nested tree is relative to the architecture root rather than the analysis root — treat those paths as display-only; node PATHS now agree between the two ops.
- 6.3 LIVE FINDING (binds 6.6/8.4): **`upython` ABORTS AT SHUTDOWN after `Ent.draw` renders a dependency graph.** Measured on 6.5.1204: `Depends On`/`Depended On By` (file or class) → `Fatal Python error: PyInterpreterState_Delete: remaining subinterpreters`, SIGABRT, rc 134 — AFTER the complete JSON answer and an intact SVG have been written. `Butterfly`/`Calls`/`Called By` are clean (rc 0). No call ordering avoids it. Reproduced by the controller: Butterfly rc=0 with a 1284-byte SVG; Depends On rc=134 with a 516-byte SVG still written. Without a fix, `ApiRunner` reports a correct run as a broken worker. The script entry point therefore flushes both streams and calls `os._exit`, never running interpreter finalization; `main()` is unchanged so in-process callers and unit tests are unaffected. Verified: envelopes still reach stdout with documented exit statuses, a genuine handler error still gives rc 1 with a traceback, 3 MB of buffered output survives, and `db.close()` runs in `finally` before the exit.
- 6.3 handoffs for 6.6: both `impact` and `graphs` additionally require `kinds_by_scope` — resolving an `EntityKey` back to an entity needs `db.ents(<kindstring>)` and note 2.1 forbids the worker inventing kind strings, so `ImpactExpander`/`GraphExporter` must build it from `SCOPE_KINDS` as `SnapshotExtractor` does. Answer shapes are `{"impact": {token: ImpactSet}, "warnings": [...]}` and `{"graphs": [...], "warnings": [...]}` — the warnings the task requires need somewhere to live. `out_dir` is created BEFORE the database is opened, so a request that later fails leaves an empty directory behind; pass an absolute path. `depth` here is INCLUSIVE and accepts 0, while `ExtractRequest.depth` is `ge=1`.
- 6.3 REJECTED IN REVIEW for two measured req-9.5 failures, both the 6.2 shape: (a) dropping a non-keyable referencer WITHOUT walking through it empties the C++ blast radius — a class used as a member, a local or a parameter answers `total: 0`, because the only reverse ref is `C Typedby` from the variable that types it; (b) `REVERSE_REFS` omits `throwby`, `catchby` and `assignby`, so a thrown/caught exception class answers `total: 0`. **`Kind.list_reference(lang)` returns FORWARD names only** (164 for `c`, 56 for `python`) — it can confirm a token exists but never that a reverse kind is covered, so coverage must be derived from reverse names observed on real databases. A set-equality test on the token list pins the gap rather than the requirement; use a subset assertion.
- 6.3 SVG locale hazard (same family as the metric-string finding): exported SVGs carry `stroke-opacity="0,000000"` under a comma-decimal locale. Well-formed XML, but an invalid presentation-attribute value that renderers ignore, so an intended-transparent stroke draws opaque. Force `LC_NUMERIC=C` around `draw` or in the child environment.
- FIXTURE EXTENSION AUTHORISED (controller, during 6.3 review): `tests/fixtures/sample_project/*/native/**` may gain a class/struct used as a member, a local and a parameter, plus a thrown/caught exception class — the C++ half of req 9.5 is untestable without them, since the fixture currently declares only free functions. The existing entities and the before/after story must stay intact; other tasks' contract tests read these fixtures.
