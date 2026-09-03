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
- **Findings (CORRECTED 2026-08-30 — the original claim was too broad).** The in-process import is NOT globally broken. Measured on both `/usr/bin/python3.12` and the project venv's CPython 3.14.4, with a valid license: `import understand`, `understand.open()`, entity iteration, `ent.metric()` and `db.close()` all succeed, rc 0. **Only `Ent.draw` aborts**, on both interpreters, with `symbol lookup error: <home>/bin/linux64/Perl/auto/Fcntl/Fcntl.so: undefined symbol: Perl_xs_handshake` (rc 127) — drawing loads Understand's bundled Perl/Qt stack, which resolves only under `upython`. The original probe happened to draw graphs, which is why the failure looked general — the module initializes Understand's embedded Perl, whose XS libraries expect symbols only the bundled interpreters provide. Adding `<home>/bin/linux64` to `LD_LIBRARY_PATH` does not help. Before the license was valid the import "worked" only because nothing was initialized. `upython probe.py` runs the same script successfully (Python 3.12.0 bundled).
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

### Contract measurements against the licensed installation (task 10.1)
- **Context**: Requirements 3.5, 4.4, 5.5, 6.7, 6.9 and 9.4 all describe behaviour only a real Understand can produce. Task 10.1 built a mixed Python/C++ sample repository, analysed it from **two differently named roots over byte-identical sources**, and measured what the installed build actually answers. Everything below is measured on Understand 6.5.1204 (build 1204, `~/scitools`), Linux x86-64, 2026-09-03, and is pinned by `tests/contract/test_*_contract.py`.

#### Entity identity across two roots (req 4.4)
- **22 entities, 22 matched, 0 unmatched, and the entity *records* are equal, not merely the keys.** Two databases built from `alpha/` and `beta/` over identical sources produce identical `metrics`, `archs`, `kind`, `name` and `line` for every routine, class and file. The file-longname defect (a FILE entity's `longname()` is the absolute path) would show here as 6 unmatched file keys; it does not, because the worker derives a file key from `longname()` made relative to the analysis root.
- **C++ overloads are separated by `parameters` and by nothing else.** `Shape::area(int) const` and `Shape::area(int,int) const` agree on scope, path, long name, Understand kind (`C Public Member Const Function`) and short name (`area`); only `parameters` and the definition line differ, and a line number moves whenever code above it changes, so it cannot be identity. Same for the free-function pair `scale(int)` / `scale(int,int)`.
- **`ent.parameters(False)` is the type-only form** (`int` / `int,int` for C++) while `ent.parameters()` is the full declaration (`int width,int height`). For **Python both forms are the parameter *names*** (`self,value`), so a "types only" key would not stop a Python signature edit from changing the key.
- **Census for task 11.6.** Dropping `parameters` from the key collides on exactly the two C++ overload pairs in the sample project and on **nothing else**: all 13 Python entities stay unique on `(scope, path, longname)`. Measured on a larger corpus -- this repository's own `src/` tree, **941 routines** -- dropping `parameters` produces **0 collisions**. So the field is load-bearing for C++ and, on real Python, buys nothing while costing requirement 4.4 its join whenever a parameter list changes.

#### Unmatched and unseparable entity kinds (the task-10.1 done condition)
- **A Python lambda is not an entity.** `step = lambda item: item + 1` yields only a `python LambdaParameter` for `item`; there is no routine entity, so a lambda has no key, no metrics of its own, and its complexity is counted into the enclosing routine. Nothing is "unmatched" because nothing arrives.
- **A C++ function template is ONE entity.** `largest` instantiated with `int`, `double` and `long` is a single `C Function Template` carrying the generic signature `T left,T right`. Correct for a join (one edit, one entity) but a real coverage limit: the metrics are measured once on the template body, so a template that is pathological in one instantiation shows a single set of numbers.
- **Several Understand entities can share one `EntityKey`, and the extra ones are dropped silently.** Two measured Python constructs do it:
  - `@typing.overload` -- two stubs plus the implementation are **three** `python Function` entities, all `typed.widen` with `parameters == 'x'`. The worker emits three records; `ProjectSnapshot.entities` is a mapping, so **one survives** (measured: the last in walk order, which on this build is the implementation).
  - A plain redefinition -- `def same(x)` written twice in one module -- gives two entities with an identical key.
  Consequence: for such a routine the ratchet compares whichever record survived on each side. Recorded for task 11.6 together with the census above; not fixed here (the models are outside task 10.1's boundary).
- **Understand cannot tell a `classmethod` from a `staticmethod`**: both are `python Function Attribute Static`, and only the leading `cls` parameter separates them. A rule that branched on the kind string would treat them alike.
- **A Python property getter/setter pair does NOT collide**: Understand names them `Gauge.value-getter` and `Gauge.value-setter`, so their long names differ.
- **`__init__.py` is kind `python File`, not `python Module File`** -- both are inside the `file ~unknown ~unresolved` kind string, so nothing is lost, but a rule that matched the kind name would miss every package initialiser.
- **Kinds no scope ever sees** (measured on this repository's own database): `python Package` (324), `python Variable Global/Local/Attribute`, `python Parameter`, `python LambdaParameter`, `python Unknown *`, `python Unresolved Attribute`, plus `C Macro`, `C Parameter`, `C Namespace`. None of them is an entity a threshold can be written for, and **no entity of a scope Understand does report was dropped for lack of a container file** (0 of 941 routines, 0 of 135 classes, 0 of 79 files).

#### Metric availability, language by language (req 5.5, 3.5)
Read from `Metric.list` for all twelve languages the extension map can configure:

| metric | languages that have it |
|---|---|
| `CountParams` (routine) | **none** |
| `CountDeclPropertyAuto` (class) | **C# only** |
| `PercentLackOfCohesion` (class) | Basic, C#, C++, Java, Pascal |
| any class metric at all | all except Ada, Assembly, Fortran, Jovial, VHDL (which answer an empty list) |
| any routine metric at all | all except Assembly |

- **`CountParams` is unavailable for every language, C++ included.** The design said "unset for Python"; measured, the synthetic is the *only* source of a parameter count on this build, so a request that omitted it would silently stop evaluating parameter thresholds in every language, not just Python.
- **`CountDeclMethodNonStub` therefore equals `CountDeclMethod` outside C#**, because the metric it subtracts (`CountDeclPropertyAuto`) exists only there. The "excluding trivial accessors" half of requirement 3.5 never fires for Python or C++.
- The snapshot reports this as **language -> metrics** (`unavailable == {"Python": ["PercentLackOfCohesion"]}`) while the C++ class in the same snapshot carries a real value for it -- which is what distinguishes the correct orientation from an inverted map carrying the same two strings.

#### Architecture nodes and dependency edges (req 6.7)
- At **depth 2** on a tree with a root-level file and a package that holds a file beside a subdirectory, the node set is `Directory Structure` (holding `main.py` and `pkg/core.py`), `.../app`, `.../native` and `.../pkg/inner`; at depth 1 it is `.../app`, `.../native`, `.../pkg` and the architecture itself. A branch shallower than the requested depth contributes its own leaf, and a file no node holds is attributed to the architecture, so no file leaves the structural rules.
- **DEFECT, on the shipped default depth.** `Arch.depends()` reports exactly one edge for this project (`.../app -> .../pkg`, 4 refs). It is published at depth 1 and **dropped at depth 2**: `worker._arch_edges` trims the target to `Directory Structure/pkg` and then requires that path to be a published node, which it is not, because `pkg/core.py` fell back to the architecture itself. The document then contradicts itself -- three file edges marked `crosses_arch` and an empty `arch_edges` -- so the arch-cycle rule (6.2) and the coupling rule (6.6) evaluate an empty edge set on an ordinary layout. Reproduced on a second tree with no root-level file at all. Recorded as `xfail(strict=True)` in `tests/contract/test_structure_contract.py`; the fix belongs to whoever owns `understand/worker.py`.
- Understand does not report a **parent -> descendant** architecture dependency, so a dependency starting at a file in the walk-root node is visible only as a file edge with `crosses_arch` set. That is Understand's behaviour, not the worker's, and is separate from the defect above.
- **A Python method called through an instance attribute has an empty blast radius.** `self.leaf.widen(value)` does not resolve back to `Leaf.widen`: the `impact` operation answers `total: 0` for that method, while the *class* `leaf.Leaf` answers 7 and `core.Engine.run` answers 3. Requirement 9.5's coverage for Python is therefore class- and file-shaped rather than method-shaped.

#### Worker parity across the two execution modes
- `catalogue`, `archs` and `impact` produce **byte-identical answer documents** under `upython` and in-process (whole documents compared, not sampled fields); task 6.6 already showed the same for `snapshot`.
- `ping` differs by design and only in one field: the Understand version agrees, the reported `python` is the interpreter that answered (3.12.0 under `upython`, the host's version in-process).
- **`understand.Metric.description(<unknown metric>)` never returns in an ordinary CPython process.** Under `upython` it answers `''` in ~0.1 s; in-process it was still running after 500 s, on both `/usr/bin/python3.12` and the venv's 3.14.4. A *known* metric answers instantly in both. `MetricCatalogue.describe` asks exactly this question about the two synthetic metrics, and the in-process path has no timeout -- so an in-process Gate would stop for good. It is a hazard rather than a live defect only because **no production code path calls `describe` today**.

#### Enrolment, selection and parse errors
- **`und add <dir>` never enrols a symlink** -- not a symlinked file whose target is inside the tree, not one whose target is outside it, and it does not follow a symlinked directory.
- **`und analyze -files @list` resolves symlinks before matching.** A link whose target *is* in the project is accepted (exit 0); a link whose target is not is refused -- and the refusal prints `Analyze Completed (Errors:0 Warnings:0)` **on stdout** while exiting **1**. The status is the only signal; the banner and the error count are both clean.
- **CodeCheck is not licensed on this machine**: `und -db X codecheck Sandbox <out>` answers `Licensing Error: No license for CodeCheck.` at rc 1 and leaves the output directory **empty**, which is why an empty directory must be a failure rather than "no violations". The contract test asserts both admissible outcomes (a parsed CSV, or a `LicenseError` over an empty directory) and refuses the third.

#### CORRECTION: Python parse coverage depends on `PATH`
- The recorded product-level finding "Understand's Python parser fails on `["k", *xs]` and loses every routine after it" is **conditional, not absolute**. Measured on one database analysed repeatedly, alternating only the environment:
  - `PATH` holding a bare **`python`** executable -> `Analyze Completed (Errors:0 Warnings:0)`, both routines present, `before_marker` at 2 code lines.
  - `PATH` holding only **`python3`** (or nothing) -> `Errors:8`, `after_marker` **absent from the database**, `before_marker` at 4 code lines.
  - Flipped back and forth three times on the same database, deterministic in both directions. The `python` may be any real interpreter (3.12.3 and 3.14.4 both work); a stub that exits 1 or merely prints a version string does **not** work, so Understand executes it.
- Consequences: the gate's analysis coverage depends on the `PATH` of whoever invoked it -- `uv run`/`uvx` put a venv `python` on `PATH` and hide the problem, a git hook run from a login shell on a distribution that ships only `python3` does not. Two machines, one commit, two entity sets, and nothing in the output naming `PATH`. Whoever owns `understand/und_cli` / `understand/database` should decide whether the gate guarantees a bare `python` in the environment it gives `und`, and `doctor` should report which one `und` will find.

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
- Entity identity across DBs verified for functions, methods, classes and files (see live experiment); **closed by task 10.1's contract suite** -- C++ overloads separate on `parameters` alone, a function template is one entity, a Python lambda is no entity at all, and the residual hole is the opposite of the one feared: several entities can share one key (`@typing.overload`, a plain redefinition) and the extra records are dropped silently. Unmatched entities degrade to absolute-threshold-only with a note in the finding.
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
