# Research & Design Decisions

## Summary
- **Feature**: `maintainability-gate`
- **Discovery Scope**: New Feature (greenfield) — full discovery
- **Key Findings**:
  - The Understand Python API (`understand.so`) is a compiled extension tied to CPython 3.12 in Understand 6.5 (build 1204, the version installed at `~/scitools`). It imports from a system `python3.12` once `<SCITOOLS_HOME>/bin/linux64/Python` is on `sys.path`; no `LD_LIBRARY_PATH` tweaks were needed on Linux. This fixes the interpreter pin for the `uvx` package.
  - `und` already provides everything the gate needs without custom C: `create`/`add`/`analyze -changed|-files`, `export -dependencies file|class|arch csv`, `codecheck -files <list> <config> <outdir>`, `metrics -summary -csv`, `create -gitcommit <hash> -refdb`. The Python API adds `Ent.metric()`, `Ent.depends()/dependsby()`, `Arch.depends()`, `Db.archs(ent)`, `Ent.draw(graph, file.svg)`, `Db.comparison_db()`.
  - `srccheck`'s valuable ideas are the threshold model (per-scope JSON, stats prefixes, synthetic metrics, adaptive lowering) and the before/after diff; its liabilities are the plotting stack and whole-project scope. Keep the former, drop the latter.
  - The before/after comparison needs two databases. Building both from **shadow trees** (index → `after/`, `HEAD` → `before/`) kept in the cache directory satisfies "nothing in the working tree", "index not working tree", and incremental analysis in one mechanism.
  - Understand's bundled metric plugins (`plugins/Metric/*.upy`: file fan-in/out, LCOM4, cognitive complexity, bidirectional deps) show the pattern for metrics Understand does not have natively; the gate computes fan-in/out and cycles itself from `depends()` data instead of depending on plugin installation.

## Research Log

### srccheck feature inventory (what to keep)
- **Context**: The user asked to reuse the ideas of `/home/mc/Source/srccheck`.
- **Sources Consulted**: `README.md`, `utilities/srccheck.py`, `utilities/utils.py`, `utilities/srcdiffplot.py`, `utilities/csvkaloi.py`.
- **Findings**:
  - Thresholds: four JSON dicts (`--maxPrjMetrics`, `--maxFileMetrics`, `--maxClassMetrics`, `--maxRoutineMetrics`), literal or file path; violation = `value > max`.
  - Stats prefixes `AVG|MEDIAN|MEDIANHIGH|MEDIANLOW|MEDIANGROUPED|MODE|STDEV|VARIANCE:` computed with `statistics` over the filtered population; results cached per metric (issues #21/#22).
  - Synthetic metrics: `CountParams` = `len(ent.ents("Define", "Parameter ~Catch"))`; `CountDeclMethodNonStub` = `CountDeclMethod - 2*CountDeclPropertyAuto`.
  - Entity streams: kind queries (`file ~Unknown ~Unresolved`, `function ~Unknown ~Unresolved, method ..., procedure ..., classmethod ...`), `skipLibs` via `ent.library()`, regex ignore on `longname()`, container file via `ent.ref("definein, declarein").file()`.
  - Adaptive mode (`-a`): after a run, rewrite the threshold JSON with `min(old, current_max)` per metric — "lower the lid".
  - Exit code = violation count. Diff plots compare two UDBs by entity name (`srcdiffplot`), with a `minChange` filter.
  - `insert_understand_in_path(dllDir)`: `sys.path.insert(0, dllDir)`, `dllDir/Python`, `dllDir/python`, and prepend to `PATH`.
- **Implications**: Reuse the threshold grammar (scope → metric → max, with prefixes) in TOML; keep the synthetic-metric definitions; make adaptive mode write a separate baseline file rather than mutating config; replace "exit code = count" with fixed exit codes (agents and CI need stable meanings) and put counts in the summary/JSON.

### Understand CLI surface relevant to the gate
- **Context**: Which operations can be delegated to `und` vs the Python API.
- **Sources Consulted**: `und help` for `create`, `add`, `analyze`, `export`, `codecheck`, `metrics`, `settings`, `list` (Understand 6.5.1204).
- **Findings**:
  - `und create -db x.und -languages python c++` (+ `-local` to keep analysis data next to the project instead of AppData/`~/.config/SciTools/Db`). `und add [-exclude "pat,pat"] [-filter ...] <dir>`; `und analyze [-all|-changed|-files @list]`; `-errors/-warnings` switches.
  - `und export -dependencies [file|class|arch <name>] [csv|matrix|cytoscape] out.csv` with `-col`, `-format longnoroot`, `-group`. `und export -changes -cmpdb other.und -kinds file -columns ... out.csv` exports changed entities against a comparison database.
  - `und codecheck [-files listfile | -gitfiles [id] | -changedfiles] [-exitstatus] [-html] <config|exported .txt> <outdir>` writes CSV violations to `<outdir>`.
  - `und create -gitcommit <hash> [-refdb ref.und] [-gitrepo path]` builds a database whose file contents come from a commit; `-refdb` also registers it as the comparison project of the reference DB.
  - `und list -metrics settings` lists available metrics; `und metrics -summary -csv` prints project metrics.
- **Implications**: DB lifecycle and CodeCheck go through `und` (subprocess with timeout); metrics, references, architectures and graphs go through the Python API (much faster than parsing CSV exports for thousands of entities). `-gitcommit` is a credible alternative for the *before* DB; not chosen for v1 (see decision below) but the adapter interface leaves room for it.

### Understand Python API contracts used
- **Context**: Exact method names/semantics for the adapter.
- **Sources Consulted**: `~/scitools/doc/manuals/python/api/understand.{Db,Ent,Arch,Metric,open}.html`.
- **Findings**:
  - `understand.open(path)` → `Db`; raises `UnderstandError` with `NoApiLicense`, `DBUnableOpen`, `DBOldVersion`, `DBAlreadyOpen` (only one DB open per process — the before/after DBs must be opened sequentially, or in two subprocesses).
  - `Db.ents(kindstring)`, `Db.files()`, `Db.metrics()`/`Db.metric(list)`, `Db.root_archs()`, `Db.lookup_arch(longname)`, `Db.archs(ent)`, `Db.comparison_db()`, `Db.language()`.
  - `Ent.metric(list) -> dict`, `Ent.metrics()`, `Ent.relname()` (file), `Ent.longname()`, `Ent.uniquename()`, `Ent.kindname()`, `Ent.kind()`, `Ent.parameters()`, `Ent.parent()`, `Ent.library()`, `Ent.ref(refkinds)`, `Ent.refs(refkinds, entkinds, unique)`, `Ent.ents(refkinds, entkinds)`, `Ent.depends()/dependsby() -> dict[Ent, list[Ref]]` (files and classes), `Ent.draw(graph, filename, options, variant)` with graph names as in the GUI ("Butterfly", "Calls", "Called By", "Depends On", ...); SVG/PNG by extension.
  - `Arch.children()`, `Arch.ents(recursive)`, `Arch.depends(recursive, group)`, `Arch.metric(list)`, `Arch.longname()`; built-in "Directory Structure" architecture.
  - `understand.Metric.list(kindstring)` / `.description(name)` — used to validate configured metric names per language (Req 3.8, 5.5).
- **Implications**: One-DB-at-a-time means the analysis layer works on **snapshots** (plain data extracted from a DB) rather than live entities; before and after snapshots are extracted in sequence, then diffed in pure Python. This also makes the core testable without Understand.

### Entity identity across two databases
- **Context**: Ratchet and diff need to match the same routine/class/file in the before and after DBs, which are built from different root directories.
- **Sources Consulted**: API docs (`uniquename`, `relname`, `longname`, `parameters`); **live experiment** (2026-08-28, Understand 6.5.1204): two tiny Python+C projects analyzed from `exp/before/` and `exp/after/`, probed under `upython`.
- **Findings** (verified):
  - `relname` = `pkg/a.py` in both DBs (relative to the added root); `longname` = `a.Widget.size`; `parameters` = `self,scale` — all identical across roots. `uniquename` = `@la.Widget.size@kya.Widget.size:./before/pkg/a.py@f./before/pkg/a.py` — embeds the root directory, so it differs between shadows.
  - `db.archs(ent)` returns `[]` for functions/classes; only files are architecture members (`Directory Structure/pkg` lists `pkg/a.py`, `pkg/b.py`, `pkg/c.c`). Architecture path of a routine = architectures of its container file.
  - Library entities (`ent.library() == "Standard"`, Understand's Python stubs under `conf/understand/python/`) dominate `db.ents()` output and must be filtered.
  - Python routines expose `CyclomaticStrict`, `CyclomaticModified`, `MaxNesting`, `CountPath`, `Essential`, `CountLineCode`; Python classes expose `CountDeclMethod`, `CountDeclInstanceVariable`, `CountClassCoupled`, `MaxInheritanceTree` but **not** `PercentLackOfCohesion` (`None`). Native `CountParams` is `None`; the synthetic `len(ent.ents("Define", "Parameter ~Catch"))` gives 3 for `helper(a, b2, c)`.
  - `f.depends()` / `f.dependsby()` on files show the new `a.py ↔ b.py` cycle in the after DB only.
  - Graph names that render to SVG: routines `Butterfly`, `Calls`, `Called By`; files `Depends On`, `Depended On By`, `Butterfly`. `File Dependencies` is "Unknown Graph".
  - `und create -db x.und -languages python c++ add ./dir analyze` works; `-quiet` must precede the subcommand (`und -quiet create …`), otherwise "unused argument".
- **Implications**: `EntityKey(scope, relpath, longname, parameters)` is correct; `uniquename` is not usable across shadows. Worker derives `archs` from the container file. `PercentLackOfCohesion` must be reported as unavailable for Python (Req 5.5) rather than defaulted; the default class thresholds should prefer `CountClassCoupled`/`CountDeclInstanceVariable` for languages without cohesion metrics.

### In-process API import vs `upython` (live result)
- **Context**: Which execution mode the worker should default to.
- **Findings**: With a valid license, `import understand` from system CPython 3.12 (`PYTHONPATH=<home>/bin/linux64/Python`) aborts the interpreter on first use with `symbol lookup error: <home>/bin/linux64/Perl/auto/Fcntl/Fcntl.so: undefined symbol: Perl_xs_handshake` — the module initializes Understand's embedded Perl, whose XS libraries expect symbols only the bundled interpreters provide. Adding `<home>/bin/linux64` to `LD_LIBRARY_PATH` does not help. Before the license was valid the import "worked" only because nothing was initialized. `upython probe.py` runs the same script successfully (Python 3.12.0 bundled).
- **Implications**: **Default `api_mode` = `upython`** whenever `<home>/bin/<platform>/upython` exists; in-process is opt-in (`api_mode = "inprocess"`) for environments where it is proven to work (e.g. Windows builds, future Understand versions). `doctor` still runs both probes and reports the in-process failure text. The worker-subprocess design was the right call; the "auto prefers in-process" ordering was wrong and is reversed in `design.md`.

### Staged content vs working tree
- **Context**: Req 4.1 requires evaluating the index, not the working tree; Req 2.2 forbids touching the working tree.
- **Sources Consulted**: git documentation for `checkout-index`, `diff --cached`, `archive`; pre-commit framework behaviour (it stashes unstaged changes while hooks run).
- **Findings**: `git checkout-index -a -f --prefix=<dir>/` materializes the full index; `git checkout-index -f --prefix=<dir>/ -- <paths>` materializes selected paths; `git diff --cached --name-status -M -z` lists staged changes with renames; `git archive HEAD <paths> | tar -x -C <dir>` exports committed content; `git rev-parse --git-path hooks` + `core.hooksPath` locate the hooks directory.
- **Implications**: Shadow trees are synced from git plumbing, never from the working tree, except in `--worktree` mode (Req 10.5) where the after-shadow is synced from working-tree files. Under the pre-commit framework the working tree equals the index anyway, but the gate does not rely on that.

### Packaging and runtime
- **Context**: "make a uvx project".
- **Sources Consulted**: uv docs — tools concept, tools guide, Python versions, build backend (uv 0.12.7, 2026-08-27); uv PR #19577 / issue #8206; PyPI JSON for library versions; local `uv 0.12.5`, `/usr/bin/python3.12`; SciTools build notes 6.5 ("Python API updated to use Python 3.12") and the freshdesk "Getting started with the Python API" article; PyPI `understand` project page.
- **Findings**:
  - `uvx <package>` from PyPI **ignores** `requires-python` and `.python-version`; it uses the first Python it finds. Only `uvx --from ./local-tree` (uv ≥ 0.11.17) infers the version from a source tree. `uvx -p 3.12 <package>` forces an interpreter (auto-downloaded if missing).
  - Therefore a `requires-python = ">=3.12,<3.13"` pin would make `uvx scitools-hook` fail to resolve on machines whose default Python is 3.13/3.14 ("no solution found").
  - The shipped `understand` module is tied to the Python minor of the installed Understand build (3.12 for 6.5; newer builds may move — not publicly documented). Understand ships its own interpreter `upython` next to `und`, which always matches the module. Windows needs `os.add_dll_directory`; Linux needs the bin dir reachable (worked here with only `sys.path`).
  - The PyPI project `understand` is an unrelated NLP package — installing it would shadow the real module.
  - Versions (2026-08-28): typer 0.27.2 (vendors Click since 0.26), rich 15.0.0, pydantic 2.13.5, hatchling 1.32.0, pytest 9.1.1, ruff 0.16.5, mypy 2.3.1, pre-commit 4.6.2. uv's own `uv_build` backend is the `uv init` default for pure-Python packages; hatchling remains fully supported.
- **Implications**: `requires-python = ">=3.12"` (no upper bound). All `understand`-API code lives in a **stdlib-only worker module** that the adapter runs in-process when the host interpreter can import the module and otherwise as a subprocess under `upython`. `doctor` reports both probes. `understand` is never a declared dependency. Choose hatchling (steering) — either backend works.

### pre-commit framework contract
- **Context**: Req 11.7/11.8.
- **Sources Consulted**: https://pre-commit.com (hooks yaml, language versions), pre-commit source (`xargs.py`, `lang_base.py`), version 4.6.2.
- **Findings**: required fields `id`, `name`, `entry`, `language`; `language: python` installs the hook repo with `pip install .` and runs the console script; filenames are appended to `entry` + `args` and **sharded xargs-style across parallel invocations** unless `require_serial: true`; `language_version: python3.12` only works if that interpreter is already installed (pre-commit does not download); pre-commit stashes unstaged changes so the working tree equals the index while hooks run.
- **Implications**: `.pre-commit-hooks.yaml` sets `require_serial: true` and `pass_filenames: true`; no `language_version` pin (worker fallback covers interpreter mismatch).

### SARIF and git plumbing details
- **Sources Consulted**: OASIS SARIF 2.1.0 spec §3.27.10 and schema; git docs + empirical checks with git 2.43.
- **Findings**: `result.level ∈ {none, note, warning, error}`; required: `version`, `runs[].tool.driver.name`, `results[].message`; official schema URL `https://docs.oasis-open.org/sarif/sarif/v2.1.0/os/schemas/sarif-schema-2.1.0.json`; relative URIs + `uriBaseId` recommended. `git checkout-index -a -f --prefix=dir/` exports the index (trailing slash required; `-a` cannot be combined with paths — use `-z --stdin`); `git diff --cached --name-status -z -M` emits `R<score>` with two paths; `git rev-parse --git-path hooks` honours `core.hooksPath`.
- **Implications**: captured in `GitRepo` and `render_sarif` contracts in `design.md`.

### Output formats consumed by agents and CI
- **Context**: Req 7 (JSON, SARIF), Req 10 (agent rules).
- **Sources Consulted**: SARIF 2.1.0 specification (OASIS), GitHub code-scanning SARIF requirements; common agent instruction files (`CLAUDE.md`, `AGENTS.md`).
- **Findings**: SARIF 2.1.0 needs `version`, `$schema`, `runs[].tool.driver.{name,version,rules[]}`, `runs[].results[].{ruleId,level(none|note|warning|error),message.text,locations[].physicalLocation.{artifactLocation.uri,region.startLine}}`. Agent instruction files are plain Markdown; begin/end marker comments make idempotent insertion trivial.
- **Implications**: `Finding` carries everything SARIF needs; the JSON schema is versioned (`schema_version: 1`).

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|--------|-------------|-----------|---------------------|-------|
| Layered core with two adapters (chosen) | `config → adapters(understand, git) → analysis → report → cli`; analysis works on extracted snapshots | Core testable without a license; one DB open at a time is natural; strict dependency direction (the gate enforces it on itself) | Snapshot extraction must be complete enough for all rules; two extraction passes per run | Matches steering `tech.md`/`structure.md` |
| Plugin-driven (Understand Metric/CodeCheck plugins do the work) | Install `.upy` plugins into Understand, run `und codecheck`/`und metrics` | Reuses Understand's engine fully | Requires writing into the Understand install/user plugin dir; harder to test; plugin API version coupling | Rejected for v1; CodeCheck configs are still supported as input (Req 6.9) |
| Single-DB with Understand comparison DB (`-gitcommit`, `comparison_db()`) | Let Understand hold before/after and export changes | Less git plumbing; Understand computes "changed entities" | Semantics of `-gitcommit` DBs (paths, incremental behaviour) unverified; both DBs still need separate metric extraction | Keep as documented alternative behind the adapter interface |

## Design Decisions

### Decision: Shadow trees + two databases for before/after
- **Context**: Req 4.1–4.4, 2.2, 2.3, 4.11.
- **Alternatives Considered**:
  1. Analyze the working tree in place (violates 4.1/2.2; unstaged edits leak in).
  2. `und create -gitcommit HEAD -refdb after.und` for the before state (unverified semantics; still needs an index shadow for after).
  3. Shadow trees: `<cache>/<repo-id>/before/` synced from `HEAD`, `<cache>/<repo-id>/after/` synced from the index (or working tree in `--worktree` mode); one `.und` per shadow.
- **Selected Approach**: 3. Sync is incremental: the gate records the commit the before-shadow reflects and the index checksum the after-shadow reflects; on each run it applies only the changed paths (`git diff --name-status` between recorded and current), then runs `und analyze -changed`.
- **Rationale**: One mechanism satisfies isolation, index-accuracy, and incrementality; it is plain files and plain git, easy to test.
- **Trade-offs**: Disk usage ≈ 2× source size in the cache; first run pays a full export + full analysis (Req 2.1 message + progress). `--all` mode uses only the after DB.
- **Follow-up**: Measure first-run and incremental timings on a 100k-line repo during implementation; keep the adapter interface open for the `-gitcommit` variant.

### Decision: Stdlib-only API worker, in-process or under `upython`
- **Context**: `uvx` does not honour `requires-python`; the `understand` module is bound to one Python minor per Understand build.
- **Alternatives Considered**:
  1. Pin `requires-python = ">=3.12,<3.13"` and document `uvx -p 3.12` — brittle (breaks silently on 3.14 defaults; wrong the day Understand moves to 3.13).
  2. Always run under `upython` — robust but slower to develop/test and awkward for in-process debugging.
  3. Worker module with two execution modes (chosen).
- **Selected Approach**: `understand/worker.py` (stdlib + `understand` only) implements every API operation as JSON-in/JSON-out; `ApiRunner` spawns `upython worker.py <op>` by default (verified working) and calls the worker in-process only when `api_mode = "inprocess"` is configured or no `upython` is found (see the live result above: in-process import crashes on Linux 6.5 once licensed).
- **Rationale**: one implementation, no interpreter coupling for `uvx`, `doctor` can explain which mode is active and why.
- **Trade-offs**: JSON serialization cost for large snapshots (bounded by affected-set extraction); worker must stay free of project imports (enforced by a test that imports it under `python -I`).
- **Follow-up**: parity contract test (both modes produce identical snapshots).

### Decision: Snapshot extraction instead of live entity traversal in analysis
- **Context**: Only one DB may be open per process; analysis must be unit-testable.
- **Selected Approach**: The Understand adapter produces a `ProjectSnapshot` (entities with metrics, file/class dependency edges, architecture membership, parse errors) for a given set of files of interest (affected files + their dependents); analysis/report never see `understand` objects.
- **Rationale**: Testability, single-open constraint, clean layer boundary.
- **Trade-offs**: Must decide up front which metrics/entities to extract — driven by effective configuration (metric names) and the affected-file set, so the extraction stays bounded.

### Decision: Pure-Python structural checks (cycles, fan-in/out, layers) over dependency edges
- **Context**: Req 6.
- **Alternatives**: Understand Metric plugins (`file_fan.upy`, `bidirectional_deps.upy`), `und export -dependencies` CSV.
- **Selected Approach**: Extract file→file and class→class edges via `Ent.depends()` (with reference counts) for affected files and their neighbourhood; compute SCCs (Tarjan) and fan metrics in `analysis/structure/`; architecture nodes come from `Db.archs(ent)`/`Arch` at the configured depth.
- **Rationale**: No plugin installation; deterministic; easily unit-tested with synthetic graphs.
- **Trade-offs**: Cycle detection scoped to the neighbourhood of the change plus prior-cycle membership; whole-project cycle inventory is available in `--all` mode.

### Decision: Configuration in TOML, baseline in JSON, hook shim without logic
- **Context**: Req 3, 8, 11; user statement "pre-commit hooks don't live in the codebase usually".
- **Selected Approach**: `scitools-hook.toml` (repo, optional) and `~/.config/scitools-hook/config.toml` (user); `SCITOOLS_HOOK_*` env; `scitools-hook.baseline.json` written only by `baseline`/adaptive runs; `.git/hooks/pre-commit` shim = 10-line `sh` that execs `uvx scitools-hook check --staged` and honours `SCITOOLS_HOOK_SKIP`/`SCITOOLS_HOOK_SOFT_FAIL`.
- **Rationale**: Human-edited vs tool-written files separated; hook never needs reinstalling; repositories can adopt with zero committed files.

### Decision: Fixed exit codes instead of "exit code = violation count"
- **Context**: Req 1.6, 7.9, 12.
- **Selected Approach**: `0` ok, `1` violations, `2` config error, `3` Understand not found, `4` license, `5` analysis failure, `6` not a git repo, `70` unexpected error.
- **Rationale**: Agents and CI branch on meaning; counts live in the summary and JSON.

## Synthesis Outcomes
- **Generalization**: thresholds, ratchets, structural rules and CodeCheck results are all instances of one `Rule → Finding` contract with a shared `Finding` model; the report layer knows only `Finding`s. Threshold evaluation is generalized over scope (routine/class/file/project/arch) with the stats prefixes as population reducers.
- **Build vs adopt**: adopt `typer`, `rich`, `pydantic`, `tomllib`; adopt Understand for parsing/metrics/graphs/CodeCheck; build (small) shadow-sync, snapshot extraction, SCC/fan computation, SARIF writer (schema is small), marker-based Markdown insertion.
- **Simplification**: no plugin system; no plotting library; one `Finding` type; SVGs only via `Ent.draw`; before/after only in staged/worktree/files modes (no arbitrary two-commit diff beyond `explain --range A..B`, which reuses the same shadow mechanism).

## Risks & Mitigations
- Entity identity across DBs verified for functions, methods, classes and files (see live experiment); anonymous/lambda entities and C++ overload edge cases remain to be covered by the contract test; unmatched entities degrade to absolute-threshold-only with a note in the finding.
- Licensing: `und` and the API share the GUI's `~/.config/SciTools/License.conf` (Linux) and need HTTPS to `licensing.scitools.com` for heartbeats; a sandboxed shell without network produced "No valid Und license found" here. CLI activation: `und -setlicensecode <code>` / env `UNDERSTAND_LICENSE_CODE`; `und -isundlicensed` for scripts. `doctor` reports `und license` output verbatim; the hook shim's soft-fail variable prevents lock-out.
- First-run analysis time on large repos — explicit progress messages, `db rebuild` and `db path` commands, cache location documented.
- Metric availability differs per language (e.g. Python lacks some C++ metrics) — validate configured metrics against `Metric.list(kind)` per language; report "unavailable" once per run (Req 5.5).
- Only one DB open per process — snapshot extraction serialized; if a future need arises, run extraction in a subprocess per DB.

## References
- srccheck — https://github.com/sglebs/srccheck (threshold JSON grammar, stats prefixes, adaptive KALOI, diff plots)
- Understand Python API — `~/scitools/doc/manuals/python/index.html`, https://documentation.scitools.com/html/python/index.html
- Understand plugins — https://github.com/stinb/plugins (Metric/`file_fan.upy`, `lcom4.upy`, `cognitive_complexity.upy`; CodeCheck plugin pattern)
- `und` CLI help — `und help create|add|analyze|export|codecheck|metrics|settings|list`
- KALOI — http://structure101.com/2006/10/complexity-debt-dont-fix-it-keep-a-lid-on-it/
- SARIF 2.1.0 — https://docs.oasis-open.org/sarif/sarif/v2.1.0/sarif-v2.1.0.html
- pre-commit hooks — https://pre-commit.com/#new-hooks
- uv — https://docs.astral.sh/uv/ (projects, scripts, `uvx`)
