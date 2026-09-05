# Research & Design Decisions

## Summary
- **Feature**: `understand-8-features`
- **Discovery Scope**: Extension of a live system (Complex Integration for items 2, 3 and 8)
- **Key Findings**:
  - Every 8.0 capability in scope was measured on Build 1262 against this repository on 2026-09-05; none is taken from the web. Four of them behave differently from what their names suggest, and the design follows the measurement.
  - The 8.0 metrics are **plugin metrics**: `Metric.list(kind)` does not list them, `Metric.lookup(id)` finds them with target and language tags, and `Ent.metric([...])` computes them at about 2 ms per routine (200x a built-in).
  - Git-derived architectures populate only on a database that knows its repository. On a plain database (the shadow tree, or `src/` of the checkout) `Git Stability` exports **empty**; on the commit-built database (`-gitrepo`) it exports 87 members.
  - The "unused function filters" 8.0 announces have no API surface: `db.ents("... unused ...")` matches nothing and `~used` is ignored. Understand's own definition is reference-based -- no incoming `callby`/`useby` reference -- and that is computable in the worker's existing call-graph pass.
  - `und analyze -accuracy` prints `N of M parsed files had no errors or warnings (P%)`: it counts **warnings**, so on this repository it is 27% (72 "unable to find import module" warnings). It is not the call-resolution rate the snapshot computes.
  - A warm check with one changed line costs 32.6 s; 22.7 s of it is four whole-project snapshot extractions and 3.6 s an analysis of an unchanged before side. Three levers together reach the 15 s target on paper: cache the before snapshot, extract once per side, skip the unchanged before analysis.

## Research Log

### Understand's SARIF (requirement 2)
- **Context**: Can Understand's own SARIF be uploaded beside the Gate's, and what does it contain?
- **Sources Consulted**: `und help analyze`; a run of `und analyze -all -accuracy -sarif parselog.sarif` on a three-file scratch project; `und help codecheck`; `plugins/Solutions/codecheck6Compatability/`.
- **Findings**:
  - `-sarif <file>` writes SARIF 2.1.0 with `tool.driver = {name: "Analysis", version: "1262", organization, product}`, rules `UND_ERROR` and `UND_WARNING`, one result per diagnostic with `fingerprints`, `partialFingerprints`, `contextRegion` snippets and `region.startLine`.
  - Artifacts are `{uri: "src/broken.py", uriBaseId: "UND_PROJECT"}` with the base in `originalUriBaseIds`; on a shadow-tree database the base is the shadow directory, not the repository.
  - `und codecheck` always writes `results.sarif` in its output directory, plus by default `CodeCheckResultsByTable.csv` from the compatibility plugin (columns File, Violation, Line, Column, Entity, Kind, CheckID, Check Name, Check Short Description, Severity; the plugin leaves Check Name and Severity empty). The three 6.5 CSV exports are gone.
  - The measuring machine has no CodeCheck licence: `results.sarif` from a real inspection is unmeasured.
- **Implications**: The Gate copies Understand's SARIF beside its own and rewrites `originalUriBaseIds.UND_PROJECT` to `%SRCROOT%`-style repository-relative form so GitHub places results on repository paths. CodeCheck violations on 8.0 are read from `results.sarif` (the documented, licence-independent format) with the CSV reader kept for 6.5; the SARIF reader is specified from the SARIF 2.1.0 schema and tested with a synthetic document until a licence exists.

### The before side from a commit (requirement 3)
- **Context**: Can the before database be built without exporting a shadow tree, and is it the same database?
- **Sources Consulted**: `und help create`; `help/projects/project-from-git.html`; `und create -db commit.und -gitrepo <repo> -gitcommit 3ca0a97 -refdb repo.und -languages Python` and the same without `-refdb` plus `und add src`.
- **Findings**:
  - With `-refdb`, creation took 0.74 s, copied the reference's settings and file set and rescanned it against the commit: 91 files (the reference had 92; `und_arch.py` did not exist at 3ca0a97). `und analyze -all` took 4.8 s (the reference, over the checkout, 4.1 s). Without `-refdb` the database starts empty; `und add src` against the checkout still yielded the commit's 91 files, because `-gitcommit` decides where contents are read from, not which files exist.
  - `-refdb` registers the new database as a **comparison project** of the reference; the API exposes it as `Db.comparison_db()`. No metric plugin computes comparison metrics (grep over `plugins/Metric` for comparison/refdb finds only plugins that mention the word in prose).
  - `-gitrepo` is a `create` option, not a setting; the database records it as the setting `GitRepositoryDirectory` (`None` on a plain database).
- **Implications**: The commit-built route replaces `git archive`-style export for the before side: `create -gitcommit <base> -refdb after.und -gitrepo <repo>`, then `analyze -all` once per base commit. The database is immutable for a given (base commit, settings, build), which makes it the natural cache key for requirement 8. Requirement 5.5's "comparison metrics" reduce to registering the pair; the design records that no comparison metric ids exist on 1262 and offers none until a build ships them.

**A reference database must be a sibling of the new one** (measured 2026-09-05, while implementing task 1.6). With the two in different directories, `und create -gitcommit ... -refdb ...` answers

```
Warning: The new database is not in the same directory as the old database. Comparison might
not find matching entities when relative paths don't match.
```

and **exits 1** -- a warning that is really a refusal, whose text names neither the switch nor the requirement. The Gate's own cache already puts `before.und` and `after.und` in one directory, so the condition holds in production; `create_from_commit` guards it anyway and names both paths, because a message like that one costs an afternoon.

**Recording the repository on a database works**: `und -db X settings -GitRepositoryDirectory Y` exits 0 and `und -db X list settings` reads the value back (measured on Build 1262). That is what task 5.1 needs for a git-derived architecture on a shadow-tree database.

### `-gitcommit` pins contents only inside `-gitrepo` (task 4.4, requirement 3.2)

**Measured on Build 1262 while implementing task 4.4, and it invalidated the design's
construction.** The design built the before database with

```
und create -db before.und -gitrepo <repo> -gitcommit <base> -refdb after.und
```

because `-refdb` copies the reference's settings *and file set*, which is exactly the parity a
before/after comparison wants. It also copies the reference's **file paths**. The Gate's after
database names its files under a shadow tree in the user's cache
(`<cache>/<repo-id>/after/pkg/core.py`), and those paths are not inside the `-gitrepo`
directory -- so Understand read their contents **from disk**, with no warning and exit 0.

The consequence, measured end to end on a two-commit repository whose `core.add` goes from
one branch to four:

| | before database | after database |
| --- | --- | --- |
| `core.add` `CountLineCode` | 7 | 7 |
| `core.add` `CyclomaticStrict` | 4 | 4 |
| findings of `check --range base..HEAD` | **1** | (8 through the shadow route) |

The before database held the working tree's code, identical to the after database in every
metric, and the ratchet compared a side against itself. Seven of the eight findings vanished
and the run stayed green. That is the exact silent-green shape this tool exists to refuse, and
nothing in `und`'s output said a word about it.

**Where `-gitcommit` does pin**, measured on the same repository:

| database | rooted at | `core.add` `CountLineCode` |
| --- | --- | --- |
| `create -gitrepo <repo> -gitcommit <base>` + `add <repo>` | the repository | **2** (the base commit) |
| `create ... -refdb <repo-rooted reference>` | the repository | **2** |
| `create ... -refdb <cache-rooted reference>` | the cache | 7 (the working tree) |

So the route builds the before database **rooted at the repository**: `create -gitrepo
-gitcommit`, then `add <repo>` under the configured excludes translated by
`und_cli.und_exclusions`, then `settings -GitRepositoryDirectory`, then `analyze -all`. The
snapshot extraction is rooted at the repository for that side, which `AnalyzeResult.
analysis_root` now carries, so an entity has one long name whichever route built its side.

**The two routes then report the same findings.** Measured with the installed console script,
two runs sharing nothing but the repository, each with its own cache: 8 findings each,
identical in rule, path, value, before, limit, severity, blocking, pre-existing and entity.
`tests/contract/test_before_routes_contract.py` is that comparison as a test.

**What giving up `-refdb` costs.** Two things, and both are recorded rather than hidden:

1. **No comparison pair.** Measured: `-refdb` registers the pair on the **reference** --
   `reference.comparison_db()` answers the derived database and the derived one answers
   `None`. Nothing reads it: 108 metric ids across file, function, class and project kinds on
   1262, none of them a comparison metric (`CountClassBase` matches a substring search and
   nothing else does). So the relation is real and no consumer exists, which is what
   requirement 5.5 already concluded.
2. **The before side's file set is the repository's, not the shadow's.** `und add <repo>`
   enrols under `und -exclude`, while the shadow is `project.include`/`project.exclude` applied
   by the synchroniser, and the two pattern languages do not agree everywhere
   (`und -exclude 'build/**'` excludes nothing; `-exclude build` drops the tree). Where they
   differ, the before side sees a different project from the after side, which changes
   project-scope ratchet values and the before side's structural view. **This is an
   operator-visible consequence of turning `understand.before_side` on and belongs in the
   documentation task.** It does not arise on the shipped default, which is `shadow`.

### Generated architectures (requirement 4)
- **Context**: Which architectures can `und arch` generate headlessly, and what do they need?
- **Sources Consulted**: `und help arch`; `help/architecture/architectures-from-git.html`; `und arch -list`, `-generate "Git Stability"` and `"Git Owner"` on a plain database over `src/` and on the commit-built database; `und export -arch` of each.
- **Findings**:
  - `arch -list` on 1262 offers 21: Directory Structure (active), Calendar, Language, Visual Studio, AI Namespace, CMake, Copyright and License, Create From File, Cycles, File Categorizer, File Namespace, Long Name, Visual Studio Structure, POSIX Thread Entry Points, Qt Thread Entry Points, Git Author, Git Date, Git Owner, Git Stability, Visibility Matrix, Visibility Matrix Cores.
  - Generation takes about 1 s here. `-generate` exits 1 while printing `Git Stability: generated` (the exit status cannot be trusted; the export can). A second `-generate` of the same name is refused without `-force`.
  - On the plain database `Git Stability` exported `<arch name="Git Stability"></arch>` -- zero members. On the commit-built database it exported 87 members under `Active`/`Recurrently Active`/`Stable` with the directory tree beneath; `Git Owner` 91 members under the one author.
  - The git plugins run `git log` in the repository the database knows; a shadow tree is not a checkout.
- **Implications**: A generated architecture is declared through the same `structure.architecture` key; the Gate generates it on the after database after analysis, with `GitRepositoryDirectory` pointing at the repository (set through `und settings`, verified by a task, or through the commit-built route for the before side), and refuses an export with zero members as "generated empty", naming the likely cause. Regeneration is skipped while the repository head and the after tree id are unchanged (recorded in the sync state). Nodes flow into the existing architecture plumbing unchanged: a generated architecture is exported and read back like a declared one.

### The 8.0 metrics (requirement 5)
- **Context**: Why does the catalogue not list them, and what do they cost?
- **Sources Consulted**: the worker's `catalogue` op under `upython`; `plugins/Metric/{objects,bidirectional_deps,cbri_metrics,cognitive_complexity}.upy`; `docs/html/python/metric.html`; an API probe on `repo.und` (1254 project routines).
- **Findings**:
  - `Metric.list("python function ~unknown ~unresolved")` answers 18 built-ins; the plugin metrics are absent from every kind list. `Metric.lookup(id)` finds each with tags: `CountGlobalsModified/Set/Used` (Target: Functions; Languages C, C++, Python, Pascal, Web), `CountClassCoupledModified` (Target: Classes; Basic, C#, Java, Pascal, Python), `CorePercentage` (Target: Architectures, Project; Any), `BidirectionalDepsPercent` (Target: Files, Classes; Any), `CognitiveComplexity` (Target: Functions; C, C++ only), `CBRI*` ten ids (project). The API documentation: a plugin metric "must be enabled to be visible in project configuration", but `Ent.metric()` computes it regardless.
  - Cost: three `CountGlobals*` on 300 routines 0.67 s (2.2 ms each) against 0.01 s for three built-ins; `CountClassCoupledModified` on 100 classes 0.00 s; `BidirectionalDepsPercent` answered `None` for every file here; `CorePercentage` answered `'0,19'` -- a locale-formatted string the worker already coerces.
- **Implications**: The catalogue gains a second source: for a configured metric the built-in list does not carry, `Metric.lookup` plus its tags decide availability per language and scope. Plugin routine metrics are requested only for entities in the recorded set, never for populations, or a whole-project walk would pay 2.8 s per side for them. `BidirectionalDepsPercent` needs a measurement on a project where it answers before it is offered as a default.

### Unused routines (requirement 6)
- **Context**: How does 8.0 expose its "unused function filters" to a script?
- **Sources Consulted**: release notes 8.0; `help/risk/find-dead-code.html`; `docs/html/python/kinds.html`; `plugins/Graph/Charts/unused_code_bar.upy`; API probe.
- **Findings**: The filters are entity-locator filters in the GUI; CodeCheck has `RECOMMENDED_13`/`CPP_F003` (needs a licence). The API has no kind keyword: `db.ents("python unused function")` and variants answer 0, and `~used` is ignored (answers the full 11625). The documentation defines unused as "no reference in the analyzed code"; by refs, 9875 of 11625 routines in `repo.und` have no `callby, useby` reference, most of them the injected standard library.
- **Implications**: The rule is reference-based, computed in the worker's whole-project call pass (which already walks every project routine's references) as a `referenced` flag on routine records, restricted to project files. It ships as a warning with an ignore list.

### Accuracy (requirement 7)
- **Findings**: `und analyze -accuracy` appends `25 of 92 parsed files had no errors or warnings (27%)` after the summary line. The numerator excludes files with warnings, and this repository's 72 warnings are unresolved imports of third-party packages under the pinned interpreter. The snapshot's call-resolution rate measures how many call sites bound to a routine -- a different quantity.
- **Implications**: The figure is parsed from the analysis output, carried per side, printed, and compared with the resolution rate on the contract project as a task; it does not replace the rate.

### The cost of a warm run (requirement 8)
- **Context**: Where do 32.6 s go?
- **Sources Consulted**: `/usr/bin/time -v` around `check --worktree` with one changed line (twice), around `check --all`, and around a no-change run; `runner/pipeline.py` (`Engine.observe`); `analysis/affected.py` (`resolve`); `understand/worker.py` (`_neighbourhood`, `_collect_edges`).
- **Findings**:
  | Phase | Time |
  | --- | --- |
  | `und analyze -changed` after | 5.1 s |
  | `und analyze -changed` before (nothing changed there) | 3.6 s |
  | snapshot pass 1 (selected files), after and before | 4.4 s, 4.2 s |
  | snapshot pass 2 (affected files and neighbourhood), after and before | 7.0 s, 7.1 s |
  | whole run | 32.6 s wall, 32 s CPU (single-threaded) |
  | no change at all | 1.0 s, no `und` command |
  - The first pass exists so `resolve()` can build the file dependency graphs of both sides and compute the affected files, the widened files (dependency changed) and the one-ring neighbourhood; the second pass records entities for that set. The worker already bounds edges to the neighbourhood of the requested files inside one call.
  - Every extraction walks the whole project once for populations, call resolution and (since 0.1.0a8) architecture edges; that fixed cost, not the recorded entities, is the 4 s floor.
- **Implications**: (a) cache the before snapshot keyed by base commit, settings, selection, worker version and build -- the before side is immutable per commit, and with requirement 3 its database is too; (b) one extraction per side that records the selected files and two rings around them, narrowed in-process to exactly the set the second pass would have asked for, with a contract test proving the narrowed document equals the two-pass one; (c) no `analyze -changed` on a before side whose commit is unchanged. Paper estimate for the measured run: 5.1 + ~7.5 = 12.6 s, under the 15 s target.

### The harness baseline, 2026-09-05 (task 8.1, requirement 8.1)

Taken with `tests/perf/warm_run_timing.py` (committed in task 1.1) so tasks 4.2, 9.4 and 9.5 can re-run exactly this and compare. Tool 0.1.0a8, Understand Build 1262, 4 cores. `scitools-hook` measured in place on a clean tree; `facdrone` measured in a `git clone --local --no-hardlinks` because its working tree carries another session's uncommitted work, so its warm-up row is a genuinely cold cache and is not comparable with the rest.

| Run | scitools-hook (in-place) | facdrone (clone) |
| --- | --- | --- |
| warm-up whole project | 12.1 s | 26.9 s (cold cache) |
| first check, one changed line | 31.5 s | 52.8 s |
| **warm check, one changed line** | **27.7 s wall, 27.4 s CPU** | **38.7 s wall, 38.3 s CPU** |
| whole project (`--all`) | 14.6 s | 26.4 s |
| no selection (nothing changed) | 1.0 s | 1.1 s |

Phases of the warm one-line check, which is the run requirements 8.2, 8.3 and 8.4 are about:

| Phase | scitools-hook | facdrone |
| --- | --- | --- |
| synchronising the after tree | 0.1 s | 0.2 s |
| analysing the after database | 3.5 s | 2.9 s |
| synchronising the before tree | 0.0 s | 0.1 s |
| analysing the before database | **0.0 s** | **0.0 s** |
| reading the after snapshot, pass 1 | 4.9 s | 6.2 s |
| reading the before snapshot, pass 1 | 5.0 s | 6.1 s |
| reading the after snapshot, pass 2 | 6.6 s | 11.1 s |
| reading the before snapshot, pass 2 | 6.5 s | 11.0 s |
| **the four extractions together** | **23.0 s of 27.7 s (83%)** | **34.4 s of 38.7 s (89%)** |

**Two corrections to what the design assumed, both from these figures.**

1. **The before analysis is already free.** The design's requirement 8.2 pairs "do not extract the before snapshot again" with "run no analysis on an unchanged before side", and the 2026-09-05 hand measurement showed 3.6 s for that analysis. Measured properly warm, on both repositories, it is **0.0 s** -- `und analyze -changed` finds nothing changed in a shadow tree that is still at the same commit. The 3.6 s in the earlier table was a re-sync after an `explain --range` run had moved the after shadow, which is the note `doctor` already prints. So requirement 8.2's analysis half buys nothing on a warm run; **its snapshot half is the whole of it**, and task 4.2's timing run should be expected to show no improvement from the route change alone.
2. **The second pass costs more than the first**, 6.6 s against 4.9 s here and 11.1 s against 6.2 s on facdrone, although both walk the whole project. The extra is the entities the wider set records, so a single pass that records two rings will cost about what the second pass costs today, not the sum.

**What the levers are therefore worth.** One extraction per side (9.2, 9.3, 9.4) plus the before side served from cache (9.1) leaves sync + after analysis + one after extraction:

| | scitools-hook | facdrone |
| --- | --- | --- |
| projected warm run | 0.1 + 3.5 + 6.6 = **10.2 s** | 0.2 + 2.9 + 11.1 = **14.2 s** |
| against the baseline | 27.7 s | 38.7 s |

Both are inside requirement 8.4's 15 s, and facdrone only just -- which is worth knowing before 9.5 claims the target, because 8.4 names this repository and facdrone is the larger case.

### After task 4.2: the before route, measured (requirement 8.2)

Same harness, same repository, same mode, 2026-09-05, tool 0.1.0a8 on Build 1262.

| Run | before 4.2 | after 4.2 |
| --- | --- | --- |
| warm-up whole project | 12.1 s | 10.7 s |
| first check, one changed line | 31.5 s | 33.5 s |
| **warm check, one changed line** | **27.7 s** | **27.8 s** |
| whole project (`--all`) | 14.6 s | 15.4 s |
| no selection (nothing changed) | 1.0 s | 1.0 s |

**No change, which is the result the baseline predicted.** The run above takes the shipped
configuration, and `understand.before_side` ships as `shadow` (requirement 1.3), so the commit
route did not run at all here. That is deliberate: turning it on for this repository is an
operator decision and not one this task may take.

What the figures do settle is **how much there is for the route to win**, which the baseline
had already implied and this run measures directly:

| Phase, warm one-line check | after 4.2 |
| --- | --- |
| synchronising the before tree | **0.0 s** |
| analysing the before database | **0.0 s** |
| the four snapshot extractions | 24.5 s of 27.8 s (**88%**) |

So on a warm run the whole before side -- export *and* analysis -- costs **0.0 s**, and the
commit route can therefore remove nothing from it. On the *first* check after an edit, where
the before side is genuinely built, it costs `0.0 s` of export plus `2.5 s` of analysis, so
the route's ceiling there is 2.5 s against a 33.5 s run.

**The route is a correctness and reproducibility feature, not a speed one.** Requirement 3's
objective says as much -- "reproducible by construction and costs less to keep" -- and the
cost it saves is disk and drift, not wall clock: a commit-built before database needs no
exported tree to keep in step with the repository, and it is reusable across runs by a key
rather than by a synchroniser's bookkeeping. The 15 s target of requirement 8.4 is reachable
only through tasks 9.1 and 9.4, exactly as the baseline concluded.

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|--------|-------------|-----------|---------------------|-------|
| Extend existing adapters in place | Each feature lands in the module that already owns the concern (und_cli, und_arch, database, catalogue, worker, sarif) | No new layer; dependency direction unchanged; the gate's own limits keep modules small | `DatabaseManager` is already over the class limits | Chosen; new concerns get new modules beside the old |
| A "features" facade | One module owns all 8.0 behaviour behind a flag | Easy to switch off | Duplicates the adapters' knowledge; a second code path per command | Rejected |
| Understand-side plugins | Ship `.upy` plugins for the unused rule and metrics | Runs inside Understand | Another artefact to install per machine; the network boundary forbids plugin distribution channels | Rejected; the API suffices |

## Design Decisions

### Decision: Availability is measured, not inferred from the version
- **Context**: 1.4 asks that a later build carrying a feature reports it.
- **Alternatives Considered**: 1. Version table (build number to feature list). 2. Probe each feature on the doctor's scratch project.
- **Selected Approach**: Probe: `und arch -list` parsed for the generated names; `und create -gitcommit` on the scratch project; `analyze -sarif`/`-accuracy` on it; `Metric.lookup` through the catalogue op; the unused rule is reference-based and always available.
- **Rationale**: 6.5 and 8.0 differ in three of these already; a table would be wrong at the next build.
- **Trade-offs**: Doctor's probe grows by two `und` calls (about 1.5 s here). A check does not probe; it trusts configuration and fails with the build's own words.
- **Follow-up**: Measure `arch -list` on 6.5 (`und arch` may not exist there).

### Decision: One extraction per side, narrowed in-process
- **Context**: 8.3.
- **Alternatives Considered**: 1. Keep two passes and cache both. 2. Move the affected-set resolution into the worker. 3. One pass recording two rings, narrowed by the pipeline.
- **Selected Approach**: 3. The worker gains `neighbourhood_rings: int` (0 today, 2 for a check); `ProjectSnapshot.narrow(files)` reproduces the second pass's document; `resolve()` is unchanged.
- **Rationale**: Keeps `analysis/affected.py` pure and testable, keeps the worker free of the change model, and is provable: the contract test compares the narrowed document with the two-pass one.
- **Trade-offs**: Entities of the second ring are extracted and discarded; measured cost negligible against the whole-project walk.
- **Follow-up**: The narrowing rule for edges must mirror `worker._collect_edges` scoping exactly.

### Decision: The before snapshot is cached; the after snapshot is not
- **Context**: 8.2, 8.6, 8.7.
- **Selected Approach**: A JSON document per (side=before, base commit, settings hash, selection hash, worker source hash, Understand build) under the cache root; read before extraction, written after; `doctor` lists entries with age; any key part changing misses the cache.
- **Rationale**: The after side changes on every run by definition; the before side never does between pushes.
- **Trade-offs**: Disk under the cache root grows by one document per base commit; prune to the last N.

### Decision: Generated architectures go through the declared-architecture plumbing
- **Context**: 4.1, 4.5.
- **Selected Approach**: Generation is a step before the existing export-and-read-back; the node source stays `ArchNode`.
- **Rationale**: Every rule and review aid already consumes `ArchNode`; nothing downstream learns a new type.

### Decision: Understand's SARIF is copied and re-rooted, never merged
- **Context**: 2.1, 2.2.
- **Selected Approach**: Separate files, `originalUriBaseIds` rewritten to the repository root, tool names left as Understand wrote them.
- **Rationale**: GitHub distinguishes tools by `tool.driver.name`; merging would mix fingerprints and rules.

### Decision: CodeCheck results read from SARIF on 8.0, CSV on 6.5
- **Context**: 2.3, 2.6.
- **Selected Approach**: A SARIF reader beside the CSV reader, chosen by which file the run wrote; both produce `RawViolation`.
- **Rationale**: 8.0 always writes `results.sarif`; the CSV it writes by default lacks Check Name and Severity.

## Risks & Mitigations
- `GitRepositoryDirectory` set through `und settings` may not be enough for the git plugins on a shadow-tree database (paths differ from the checkout) -- the first task measures it; the fallback is to generate on a commit-built database of the after tree id, which is not a commit. If neither works, generated git architectures are offered for the before side only and documented as such.
- Plugin metrics at 2 ms per routine: restricted to recorded entities; never in populations.
- `arch -generate` exits 1 on success: success is decided by the export, not the status.
- The narrowing rule drifting from the worker's edge scoping: pinned by a contract test on the contract project and a unit test on the fake project.
- Cache entries served after a worker change: the worker source hash is part of the key.
- The single-threaded 32 s becomes about 13 s; facdrone is larger and unmeasured -- task 1 of requirement 8 measures it before any change.

## References
- Understand help (shipped, Build 1262): `help/projects/project-from-git.html`, `help/architecture/architectures-from-git.html`, `help/projects/keep-analysis-current.html`, `help/risk/find-dead-code.html`, `help/troubleshooting/performance-large-codebase.html`, `und help create|arch|analyze|codecheck`, `docs/html/python/metric.html`.
- Plugin sources: `plugins/Metric/objects.upy`, `bidirectional_deps.upy`, `cbri_metrics.upy`, `cognitive_complexity.upy`; `plugins/Solutions/codecheck6Compatability/`.
- SARIF 2.1.0 -- https://docs.oasis-open.org/sarif/sarif/v2.1.0/sarif-v2.1.0.html (`originalUriBaseIds`, `artifactLocation.uriBaseId`).
- Measurements: `/tmp/.../scratchpad/timing-change.txt`, `timing-all.txt`, `u8/measure.txt` (this session, 2026-09-05); reproduced in the log above.
