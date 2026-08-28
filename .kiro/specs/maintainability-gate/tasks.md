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

- [ ] 4.7 (P) Implement the CodeCheck violation mapper
  - `RawViolation` rows → `Finding(kind="codecheck", rule="codecheck.<id>")` with configured severity, repo-relative path/line and message; `hint` left empty
  - Done when a fixture of raw violations maps to findings with the configured severity and repo-relative paths
  - _Requirements: 6.9_
  - _Boundary: analysis/codecheck_

- [ ] 4.8 (P) Implement the change-summary builder
  - Per-file entity deltas (added/removed/modified with before/after/delta metrics), dependency deltas grouped by architecture node with cross-boundary marking, rankings by delta and by value, impact sets attached, architecture paths shown, database path and open-in-GUI command included
  - Done when tests over the fixture snapshots produce the expected deltas, rankings and cross-boundary flags
  - _Requirements: 9.1, 9.2, 9.3, 9.5, 9.7, 9.8_
  - _Boundary: analysis/change_summary_

- [ ] 5. Report layer: renderers and agent guidance
- [ ] 5.1 (P) Implement the hint catalogue and the human renderer
  - Hint lookup order rule → metric → generic with configuration overrides; findings grouped by file, ordered by severity then overshoot ratio; summary line with counts and exit-code meaning; quiet mode; colour disabled when not a TTY or `NO_COLOR`, forced by `--color`; agent instruction block appended when blocking
  - Done when snapshot tests of the rendered text match for a blocking run, a warnings-only run, quiet mode and a non-TTY run (no ANSI sequences)
  - _Requirements: 7.2, 7.3, 7.6, 7.8, 10.4_
  - _Boundary: report/hints, report/human_

- [ ] 5.2 (P) Implement JSON and SARIF renderers
  - JSON: single document from `RunResult` (`schema_version` 1) and nothing else; SARIF 2.1.0 with one rule per distinct `Finding.rule`, levels error/warning/note (pre-existing), repo-relative URIs with `%SRCROOT%`, `startLine ≥ 1`
  - Done when JSON round-trips to `RunResult` and the SARIF output validates with `jsonschema` against the 2.1.0 schema stored in `tests/fixtures/`
  - _Requirements: 7.4, 7.5, 7.7_
  - _Boundary: report/json_out, report/sarif_

- [ ] 5.3 (P) Implement change-summary renderers
  - Text, Markdown (merge-request friendly tables) and JSON views of `ChangeSummary`, graph file references, final open-in-GUI command line
  - Done when snapshot tests match for all three formats over the fixture summary
  - _Requirements: 9.4, 9.6, 9.8_
  - _Boundary: report/markdown_

- [ ] 5.4 (P) Implement the agent-rules renderer and marker insertion
  - Deterministic Markdown snippet from effective thresholds and settings (sorted, no timestamps) covering limits, structural rules, the command to run, JSON reading guidance and the blocked-commit workflow; insert/replace between `<!-- scitools-hook:begin/end -->` markers preserving surrounding content
  - Done when rendering twice yields identical text and inserting twice into a file yields one snippet with the rest of the file byte-identical
  - _Requirements: 10.1, 10.2, 10.3_
  - _Boundary: report/agent_rules_

- [ ] 6. Understand adapter: API worker, location, `und` wrapper, database lifecycle
- [ ] 6.1 Implement the stdlib-only API worker skeleton with `ping`, `catalogue` and `archs`
  - `worker.py` importing only the standard library and `understand`; JSON request on stdin / result on stdout when run as a script; `dispatch(op, request)` for in-process use; error envelope mapping `UnderstandError` texts (`NoApiLicense`, `DBUnableOpen`, `DBOldVersion`) to typed error names; `catalogue` returns available metrics per language and scope kind; `archs` returns root architecture names and nodes at a depth
  - Done when `python -I worker.py ping` (no package on `sys.path`) answers with the API version or a license error envelope, and a test asserts the module has no `scitools_hook` imports
  - _Requirements: 1.2, 1.4, 6.7, 6.8_
  - _Boundary: understand/worker_

- [ ] 6.2 Implement the worker `snapshot` operation
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
