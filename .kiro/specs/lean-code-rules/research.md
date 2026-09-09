# Gap analysis: lean-code rules

Written 2026-09-09 by `/kiro-validate-gap`, before design. Everything measured here was measured
on files already on disk: this repository's cached whole-project snapshot (Understand Build 1262,
before side of the last check, 5 689 routines / 273 files / 287 classes), the cached snapshot of
a second, larger repository (6 919 routines / 653 files / 1 121 classes), and a text-level
approximation of the two duplication rules over this repository's Python sources. No `und`
command was run and nothing touched licensing.

## 1. Current state

### What a structural rule touches today (traced from `structure.unused_routines`)

| Layer | Where | Note for this feature |
| --- | --- | --- |
| Config | `config/models.py` `StructureRules` (severity-or-`None` = off; `unused_ignore` compiled by a validator); `DEFAULT_UNUSED_IGNORE` | `config/models.py` is 20 settings classes over one base and already sits in `[scope.schemas]` with `CountDeclClass` off. Eight more rules with severity, ignore list and thresholds each is a **new settings model**, not eight more fields. |
| Template | `config/template.py` `_structure_body` / `_unused` | one commented line per off rule |
| Fingerprint | `config/fingerprint.py` | carries `duplicate_definitions` only; `unused_routines` is absent, which is why the rule needs its "snapshot predates the rule" branch. A rule that changes extraction should add a fingerprint key instead of an unavailable branch. |
| Feature gate | `understand/features.py` `ASKED_BY`, `Feature` enum in `models/understand.py` | one probe per capability, cached in `features.json`, refused at configuration time |
| Extraction request | `understand/snapshot.py` (`record_referenced`), `ExtractRequest` | metrics per entity are **only those the configured thresholds ask for** (`_element_metrics`) |
| Worker | `understand/worker.py` `_referenced` (asks `ent.refs("callby, useby")` and keeps project files only); `PARAMETER_KIND = "Parameter ~Catch"`; `MODULE_VARIABLE_KIND`; `_initialiser` reads `file.lexer().lexemes(line, line+12)` dropping `Comment`/`Whitespace` | the only lexer use in the tree, and the precedent for token shapes |
| Snapshot model | `models/snapshot.py` `EntityRecord.referenced: bool \| None` | three states |
| Rule | `analysis/structure/unused.py` (120 lines) | one module per rule is the convention |
| Rule name | `models/findings.py` `StructureRuleName` Literal | the allowlist for `severity."structure.x"` and SARIF ids |
| Pipeline | `runner/check.py` `_structure` / `_unused` | counts assembled at `check.py:252` into `RunResult` |
| Hint | `report/hints.py` `_STRUCTURE_HINTS`; operator overrides via `[hints]` | no slot for a worked example today |
| agent-rules | `report/agent_rules.py` `_structure_section` | lists cycles, fan, new-deps, layers, coupling, codecheck. **Omits `unused_routines`, `duplicate_definitions`, `call_cycles`, `reachable_complexity`** (requirement 8.5). |
| Docs | `docs/reference/rules.md`, `docs/guide/configuration.md`, `docs/reference/features.md`, `docs/reference/cli.md` (doctor rows) | |
| Tests | `tests/analysis/test_unused_routines.py`, `tests/understand/test_worker_referenced.py`, `tests/contract/test_unused_routines_contract.py`, `tests/e2e/test_unused_routine.py` | the four-layer pattern to copy |

### Constraints that shape every option

- **The worker has no room.** `understand/worker.py` measures `CountLineCode` 1 060 against its scope ceiling of 1 200 and 119 functions against 130. That is about 140 lines and 11 functions for *all* new extraction. The file may import nothing from the package (`tests/test_import_direction.py` holds it to `frozenset()`) because it runs under `upython`, so it cannot be split along package lines.
- **The call graph cannot answer "exactly one caller".** `call_edges` are bounded to the forward closure of the change, and this repository's Python resolution is 9 894 resolved / 5 678 external / 7 402 unresolved call sites. Caller counts must be asked of the entity (`refs("callby")`), as `_referenced` does, and an unresolved call site is a caller the count cannot see.
- **Inheritance is not recorded.** `class_edges` are `depends()` edges. Understand carries `Base`/`Derive` and `Overrides`/`Overriddenby` reference kinds (its help mentions them 216, 227, 138 and 68 times), none of which the worker reads.
- **Parameters and module variables are not entities in the snapshot.** The worker reaches parameters only to count them.
- **Cost ceiling.** Requirement 9.5 allows one half of today's 13.0 s warm check, about 6.5 s, for every lean-code rule on.

### What Understand offers that the Gate does not read yet

| Capability | Evidence | Relevance |
| --- | --- | --- |
| Lexer token classes | `Lexeme.token()` values: Comment, Continuation, EndOfStatement, Identifier, Keyword, Label, Literal, Newline, Operator, Preprocessor, Punctuation, String, Whitespace, Indent, Dedent | identifier/literal normalisation for similarity is a token-class map, language-agnostic by construction |
| Duplicate-lines solution | `~/scitools/plugins/Solutions/duplicates/`: metric ids `DuplicateLinesOfCode`, `DuplicateLinesOfCodePercent`, tags `Language: Any`, targets Files/Architectures/Project; algorithm in `plugins/Shared/und_lib/duplicates.py` (lexer, drop Whitespace/Comment/Newline, N-line windows, default 5) | requirement 5.6; also the reference algorithm for the Gate's own exact-duplicate rule |
| Built-in metrics not in the catalogue today | `CountLineComment`, `CountStmtExe`, `CountStmtDecl`, `CountLineCodeExe`, `CountStmtEmpty`, `CountUnusedVariable`, `CountUnreachableStmt`, `CountEarlyExit`, `MaxEssentialKnots` (from the help index) | `CountLineComment` is requirement 6.2; `CountUnusedVariable` may answer part of requirement 1 for the languages that carry it |
| Plugin metrics, `Language: Any` | `CountCallby`, `CountCalls` (`calls.upy`); `CountLineCommentWithBefore`, `RatioCommentsWithBeforeToCode` (`comments.upy`); `CountExit`, `CountEarlyExit`; `CallCountLineCode`, `CallNodes` | `CountCallby` is a caller count Understand computes itself, through the plugin path the Gate already has |
| `Ent.contents()`, `Ent.control_flow_graph()`, `Ent.parameters()` | API docs | `contents()` gives routine text but not tokens; the file lexer with a line range is the usable route |

Whether Build 1262 loads `plugins/Solutions` without a copy into the user plugin directory (which does not exist on this machine) is unknown: **Research Needed**, answerable by the existing `catalogue` worker op with `lookup=["DuplicateLinesOfCode"]` in a contract test.

## 2. Measurements

### On this repository's snapshot

| Signal | Population | Result |
| --- | --- | --- |
| Verbosity `CountLineCode / CountStmt`, routines with ≥ 5 statements | 2 851 | p50 1.20, p90 2.00, p95 2.29, max 6.6. Over 2.0: 223; over 2.5: 97; over 3.0: 49. Top: `template._structure_body` 33 lines / 5 statements, `calls._cycle_finding` 32 / 5, and test data builders. The tail is **literal-heavy code** (a long f-string, a list literal), not prose. |
| `RatioCommentToCode`, files | 273 | p50 0.37, p90 0.89, p95 1.17, max 5.5. Over 1.0: 20; over 2.0: 4 (`report/json_out.py` 5.5, `models/ports.py` 3.0, two `__init__.py`). These are docstring-heavy modules, so requirement 6.5 (how a docstring counts) decides whether a maximum means anything for Python. |
| Over-export | 273 files | 2 files define exactly one routine or class; 0 of them have exactly one dependant. |
| `CountClassDerived == 1` | 287 classes | 5: `ConfigError`, `CodeCheckRunner`, `FakeApiRunner`, `FakeUnderstand`, `UndStub`. The "nothing else references it" half is unmeasured (needs references). |
| `referenced` | 5 689 routines | `None` on every routine: the unused rule has never been on here, so dead code is unmeasured on this repository. |

### On the second repository's snapshot (653 files)

| Signal | Result |
| --- | --- |
| Verbosity, ≥ 5 statements (3 688) | p50 1.33, p90 2.96, p95 3.80, max 19.6. Over 3.0: 334. Top: UI panel builders at 176 lines / 9 statements. |
| `RatioCommentToCode` | p50 0.20, p90 0.58, p95 0.75, max 11.0; over 1.0: 16. |
| Over-export | 12 one-definition files, **1** with one dependant: `report_publisher_factory.py`, a textbook `yagni:` finding. |
| `CountClassDerived == 1` | 6 classes. |

A verbosity limit therefore needs a floor on statements and a literal-aware reading, or it reports data tables. The over-export rule is quiet by nature (0 and 1 findings on two repositories), which is what a rule that ships on as a warning should look like.

### Text-level approximation of the duplication rules (this repository, Python only)

Exact duplicates over `src/` and `tests/` (fixtures excluded), comments and whitespace stripped:

| Window | Duplicated windows | Files involved | of which `src/` |
| --- | --- | --- | --- |
| 5 lines | 641 | 162 | 43 |
| 8 lines | 227 | 74 | 10 |
| 12 lines | 72 | 24 | 2 (`config/models.py`, `git/shadow.py`) |

Similar routines, ≥ 6 statements (1 943 routines), identifiers and literals normalised, sequence ratio:

| Threshold | Pairs | `src`–`src` pairs |
| --- | --- | --- |
| ≥ 0.9 | 184 | 10 |
| ≥ 0.8 | 733 | 42 |
| ≥ 0.7 | 1 745 | 138 |

The ≥ 0.9 `src` findings are genuine: `config/models._translate_path_pattern` and `git/shadow._translate` are the same 19 statements; `runner/pipeline.phase` and `understand/database._phase` are identical; `und_cli._list_arches` / `list_generated` at 0.96. Tests dominate every table (`time_limit` copied verbatim into three modules, `upython_or_skip` into two). Conclusions for the design: exact duplicates want `min_lines` near 10 to 12 for source; similarity wants a threshold at or above 0.9 with `min_statements` ≥ 6; tests need their own scope or the rule reports the idiom.

## 3. Requirement-to-asset map

| Req | Existing asset | Gap |
| --- | --- | --- |
| 1 dead code: classes, variables | `_referenced` (routines only); `MODULE_VARIABLE_KIND`; `unused.py` | **Missing**: widen `_referenced` to classes and module variables; a rule per shape or one rule with a scope field. **Unknown**: whether `useby` covers a class referenced only as a type annotation, per language. |
| 1 dead code: parameters | `PARAMETER_KIND`, `ent.ents("Define", ...)` | **Missing**: per-parameter reference count (`param.refs("useby, setby, modifyby")`), a per-routine list in the record, a finding located at the routine. **Unknown**: implicit receivers (`self`, `cls`, `this`) as Parameter entities per language; whether `CountUnusedVariable` covers locals rather than parameters. |
| 1.4 / 2.3 override exclusion | none | **Missing**: read `Overrides` / `Overriddenby` refs. **Unknown**: which languages carry them (Python?). |
| 2 pass-through | `_referenced`, `CountStmt`, `CALL_REFS` | **Missing**: per-routine project caller count and distinct callee count. **Constraint**: unresolved call sites undercount callers; `CountCallby` plugin metric is an alternative source to compare against. |
| 3 single-implementation | `CountClassDerived` (shipped threshold) | **Missing**: `Base`/`Derive` refs, "referenced only by its derived class" test. |
| 4 over-export | `file_edges`, `CountDeclFunction`, `CountDeclClass`, `definitions` | **None** in extraction: computable from today's snapshot. Missing only the rule module, the initialiser exclusion and the name literal. |
| 5.1 exact duplicates | `_initialiser` lexer precedent; `und_lib/duplicates.py` algorithm | **Missing**: a whole-project token pass in the worker, an index in the snapshot (or a separate document, since the snapshot is per side and cached), the rule. **Constraint**: worker budget; cost of lexing 273 files (unmeasured). |
| 5.2 similar routines | same | **Missing**: routine line ranges (start is known; end needs the `End` ref or `CountLine`), a normalised token shape per routine, a candidate index, the rule. **Constraint**: O(n²) naïvely; a shingle index keeps it linear-ish, as the approximation above did for 1 943 routines in seconds. |
| 5.6 duplicate-lines metric | `PLUGIN_METRICS`, `catalogue._plugins` | **Missing**: two declarations. **Unknown**: whether the build loads the solution. |
| 6.1 comment maximum | `Limit` accepts `max` and `min` together; default is `{"min": 0.1}` | one-line default change once measured; **Unknown** docstring treatment (6.5). |
| 6.2 `CountLineComment` | catalogue is name-driven | add to the routine request when configured; availability per language via `Metric.list`. |
| 6.3 verbosity ratio | `SYNTHETIC_METRICS` (`CountParams`, `CountDeclMethodNonStub`) | **Missing**: one synthetic metric with `requires=("CountLineCode","CountStmt")` and a statement floor; the ratchet covers synthetic metrics already. |
| 7 net LLOC delta | `analysis/change_summary.py` `EntityDelta.delta` per metric (on the `change` path); `analysis/ratchet.py` per-entity before/after; `AffectedSet.deleted_files`; `RunResult` counts | **Missing**: the sum on the `check` path, a `RunResult` field (additive, schema stays 2 per `json_out.py`'s stated policy), the human line, the SARIF run property. **Unknown**: whether deleted files' entities are in the before snapshot when the selection is staged files (the before side is built from the selection plus neighbourhood). |
| 8.1–8.2 hints with examples | `hints.py` catalogue, four-level lookup, `[hints]` overrides; `Finding.details` free-form | **Missing**: a place for the example (a second catalogue keyed the same way, or a `details["example"]`), verbose rendering. |
| 8.4–8.5 agent-rules | `_structure_section` | **Missing**: lean section; the four omitted rules. |
| 8.7 skills | `src/scitools_hook/skills/` four SKILL.md | edits only. |
| 9.2–9.3 doctor / refusal | `features.py` probes, `Feature` enum, `ASKED_BY` | one probe per capability; the lexer probe can reuse doctor's scratch database. |
| 9.4–9.5 cost | `snapshot_cache`, one extraction per side | **Constraint**: extraction of token shapes must be off unless a rule asks, and cached with a fingerprint key. |
| 10 docs | four reference pages | writing. |

## 4. Implementation options

### Option A: extend in place
Add fields to `StructureRules`, functions to `worker.py`, one rule module each under `analysis/structure/`, hints to `_STRUCTURE_HINTS`.

- Fits the existing pattern exactly; every test layer has a template.
- **Blocked by the worker budget**: 140 lines for six extractions is not credible. The alternative, raising `[scope.worker]` again, is the project adapting its own limits to fit a feature, which its `scitools-adapt` rung 6 allows only after `recommend` and in its own commit.
- `config/models.py` grows by a rule family in a file already exempted from the class-count limit.

### Option B: a second worker and a `lean` package
A second zero-import script (`understand/worker_lean.py` or similar) that `api_runner` dispatches for new ops (references per entity, token shapes), held to the same `frozenset()` import rule; a `config/lean.py` settings model for a `[lean]` section; `analysis/lean/` with one module per rule; a `report/lean.py` for the net line and examples.

- Keeps `worker.py` untouched and under its ceiling; the import-direction test gains one row.
- Both scripts need the same `_import_api`, `_project_path` and envelope helpers, which cannot be imported between them: either duplicated (which `duplicate_definitions` and the new similarity rule would then report on the Gate itself) or moved into a tiny third zero-import module that both scripts load by path. **Research Needed**: whether `upython worker.py` can `sys.path`-load a sibling without violating the import-direction rule's intent.
- A separate op means a separate database open per side; the 8.0 work collapsed four extractions into one for cost reasons, so the second op must run inside the same open or be measured against 9.5.

### Option C: hybrid, phased (recommended for evaluation)
- **Phase 1, no new extraction**: requirements 4 (over-export, from today's snapshot), 6 (thresholds and one synthetic metric), 7 (net delta from the ratchet's per-entity before/after), 8 (hints, examples, agent-rules including the four omitted rules), 9's config/doctor scaffolding, 10. Ships the vocabulary and the number before any rule that needs references.
- **Phase 2, references**: requirements 1, 2, 3 through one new worker op that records, per affected-or-all entity, project caller count, callee count, base/derived, overrides, and per-parameter use. Where the worker lands (A or B) is the design's first decision, taken with the budget measured.
- **Phase 3, tokens**: requirement 5 through a whole-project token pass, cached per side under a fingerprint key, with the exact-duplicate index and per-routine shapes in one document.

## 5. Effort and risk

| Req | Effort | Risk | Why |
| --- | --- | --- | --- |
| 1 dead code beyond routines | M | Medium | `_referenced` generalises; parameters need per-language research on receivers and overrides |
| 2 pass-through | M | Medium | caller count sits on 43 % call resolution for Python; `CountCallby` gives a second opinion to measure against |
| 3 single-implementation | S–M | Low–Medium | two ref kinds and one metric already shipped |
| 4 over-export | S | Low | computable from the snapshot today; quiet by measurement |
| 5 duplication and similarity | L | High | worker budget, whole-project lexing cost, two thresholds to measure, tests dominate the population |
| 6 shrink signals | S | Low | thresholds and one synthetic metric; docstring question is the only unknown |
| 7 net LLOC delta | S–M | Low | deltas exist on the `change` path; deleted-file before-side coverage to confirm |
| 8 hints, examples, agent-rules, skills | M | Low | writing plus one catalogue slot |
| 9 doctor, refusal, cost | M | Medium | the 6.5 s ceiling is the constraint every extraction is measured against |
| 10 docs | S | Low | |

Whole feature: **XL** as one spec, **L** if phase 3 is delivered separately.

## 6. Recommendations for design

1. Decide the worker question first, with the budget written down: `worker.py` at 1 060 / 1 200 lines and 119 / 130 functions. Either a second zero-import script with a measured way to share helpers, or a `recommend`-backed raise of the scope ceiling in its own commit. Do not decide by adding the first 140 lines.
2. Ship phase 1 as its own release: over-export, shrink signals, the net delta and the ponytail-form hints need no extraction and prove the vocabulary.
3. For references (phase 2), record one `LeanRecord` per entity beside `referenced` rather than one boolean per rule, so the three rules share an extraction and a fingerprint key, and drop the "snapshot predates the rule" branch in favour of cache invalidation.
4. For duplication (phase 3), take the shipped `und_lib/duplicates.py` as the reference algorithm for exact duplicates (same windows, same token drops, so the Gate agrees with Understand's own report) and put similarity on top of the same token pass. Start defaults at `min_lines` 10–12, `min_statements` 6, threshold 0.9, and re-measure with Understand's lexer rather than Python's tokenizer before shipping any of them.
5. Give tests a stated policy in the design: ponytail's "a single smoke test is the minimum, not bloat" argues for a shipped `[scope]` proposal from `init --detect` rather than a lower threshold.

### Research Needed (carry into design)

- Does Build 1262 answer `Metric.lookup("DuplicateLinesOfCode")` without the solution copied into a user plugin directory?
- Which languages on 1262 carry `Overrides`/`Overriddenby` refs; does Python?
- Are `self`/`cls`/`this` Parameter entities; do `Useby`/`Setby` refs on a parameter come from the routine body only?
- Does `useby` on a class include a use as a type annotation (Python) or a template argument (C++)? A class used only in annotations must not be "unused".
- The end line of a routine: `End` ref, `CountLine` from the start line, or `contents()` line count. Needed for both token rules.
- Docstring accounting in `CountLineComment` / `RatioCommentToCode` for Python on 1262.
- Whether `CountLineComment`, `CountStmtExe`, `CountUnusedVariable`, `CountUnreachableStmt` are in `Metric.list` for the Python routine kind (the routine kind answered 18 metrics on 1262).
- Cost of `file.lexer(False)` over 273 files inside the existing extraction, against the 6.5 s ceiling.
- Whether the before snapshot for a staged check holds the entities of a deleted file.
- How `upython` can load a shared helper for two zero-import worker scripts, if option B is taken.

---

# Design-phase discovery and decisions

Appended 2026-09-09 by `/kiro-spec-design`. The gap analysis above stands; this part records
what the design phase found on top of it, and the decisions the design took.

## Summary
- **Feature**: `lean-code-rules`
- **Discovery Scope**: Extension (light discovery), escalated for the two extraction questions (worker budget, token pass) which were investigated to the level of the API's documented behaviour.
- **Key Findings**:
  - Understand's per-language reference kinds cover every relation the rules need, under different names per language; a comma-separated, case-insensitive filter string covers them all in one call.
  - A Python docstring **is** a comment line to Understand's metrics (measured: `report/json_out.py`, no `#` comment, 36 docstring lines, about 6 code lines, `RatioCommentToCode` 5.5), even though the lexer tokenises it as `String`. A comment-ratio maximum therefore ranks docstrings on Python.
  - The duplicate-lines solution is discovered from the install tree automatically but is invisible to `Metric.list` unless enabled in the Plugin Manager; `Metric.lookup` finds it either way.
  - A synthetic metric that returns nothing for an entity is recorded as *unavailable* by the worker and by the threshold evaluator; a floor cannot be expressed by omission.
  - There is no lexer fake in the test suite, and the worker's only lexer use has no unit test.

## Research Log

### Reference kinds per language
- **Context**: requirements 1.4, 2.1, 2.3, 3.1 need callers, overrides, inheritance and parameter use across languages.
- **Sources Consulted**: `~/scitools/docs/html/python/kinds.html` (the kind list), `plugins/Metric/analysis.upy`, `plugins/Scripts/Python/funcLexemes.py`.
- **Findings**:
  - Inheritance from the base class's side: `Derive` (C/C++, C#), `Inheritby` (Python), `Extendby` and `Implementby` (Web/JavaScript/TypeScript); Java records it as `Extend Couple` / `Implement Couple` on the derived side. A filter `"derive, inheritby, extendby, implementby, extendby couple, implementby couple"` is the design's candidate and the contract test decides it.
  - `Overrides` / `Overriddenby` exist for Python, C/C++, Java, Web and C#.
  - `Callby`, `Useby`, `Setby`, `Modifyby` exist for all five; `Typedby` exists for all five and is how a class used only in an annotation is referenced.
  - `End` (and `Endby`) exist for all five; Python has no `Begin`. The shipped idiom is `ent.ref("definein")` for the start line and `ent.ref("end")` for the end line, guarded by both being in the same file. `CountLine` on a routine equals end minus start plus one.
  - Parameter kinds: `Python Parameter`, `C Parameter`, `Java Parameter`, `Web Javascript Parameter`, `C# csharp Parameter In/Out/Ref/Value/Params`; the existing `"Parameter ~Catch"` filter covers all of them. `self`/`cls` are ordinary `Python Parameter` entities and must be excluded by name.
  - `CountUnusedVariable` (any language, functions only) counts local variables nothing reads and excludes parameters; `CountUnreachableStmt` likewise. Neither is a substitute for the parameter rule.
- **Implications**: the worker measures per-parameter use as `param.refs("useby, setby, modifyby")` with the referencing file inside the project; the receiver exclusion is a shipped ignore pattern (`^(self|cls|this)$`), not a kind test.

### Docstrings and comment metrics
- **Context**: requirement 6.5.
- **Sources Consulted**: `docs/html/metrics/CountLineComment.html`, `RatioCommentToCode.html`, `CountLineCode.html`; the cached snapshot of this repository.
- **Findings**: the metric pages define a comment line only as "a line containing a comment" and never mention docstrings. The snapshot is decisive: `report/json_out.py` has zero `#` lines, 36 docstring lines and about 6 code lines, and Understand reports `RatioCommentToCode` 5.5. So docstring lines count as comment lines and not as code lines for Python.
- **Implications**: a `RatioCommentToCode` maximum on Python reports docstring-heavy modules first; the design ships the maximum **off** and documents why; the verbosity ratio is not inflated by docstrings, which is the useful half of the finding.

### Plugin discovery and the duplicate-lines metric
- **Context**: requirement 5.6, 9.2.
- **Sources Consulted**: `docs/html/help/apis/write-a-plugin.html`, `plugin-manager.html`, `docs/html/python/api/understand.Metric.html`, `plugins/Solutions/duplicates/*.upy`, `plugins/Shared/und_lib/duplicates.py`.
- **Findings**: Understand scans the install's `plugins/` tree and `~/.config/SciTools/plugin/<Type>` recursively; the duplicates solution is therefore discovered without copying. `Metric.list` defaults to `enabled_only=True`, so a discovered-but-disabled plugin is absent from it; `Metric.lookup(id)` answers regardless, and `tags()` carries `Language: Any`, `Target: Files|Architectures|Project`. The shipped algorithm drops `Whitespace`, `Comment`, `Newline` tokens, joins the remaining lexeme text per line, and matches windows of N lines (default 5) across files.
- **Implications**: the catalogue path the Gate already has (`Metric.lookup` plus tags) offers the metric as a file and project threshold with two `PLUGIN_METRICS` declarations. The Gate's own exact-duplicate rule follows the same line normalisation so the two agree on what a duplicate is; the Gate keeps its own rule because the plugin has no per-routine form and no similarity.

### The worker budget and where extraction lives
- **Context**: `understand/worker.py` measures `CountLineCode` 1 060 of 1 200 and 119 of 130 functions; it imports nothing from the package; `snapshot_cache.worker_digest()` hashes its source.
- **Sources Consulted**: `understand/api_runner.py`, `understand/locator.py` (`WORKER_PATH`), `understand/snapshot_cache.py`, `tests/test_import_direction.py`.
- **Findings**: the extractor walks every entity once (`_read_scope`) and calls `_referenced` per record, so the cheapest place for per-entity lean facts is inside that walk. A second *op* would open the database and walk again. A sibling file loaded by path is not an intra-package import statement, runs under `upython`, and can be held to the same zero-import rule and included in the worker digest.
- **Implications**: decision below: `understand/worker_lean.py`, loaded by path from `worker.py`, called from the walk, given the two helpers it needs as arguments.

### Absent metrics and floors
- **Context**: requirement 6.3.
- **Sources Consulted**: `worker._entity_metrics`, `analysis/thresholds.py` (`record_unavailable`), `analysis/ratchet._compare`.
- **Findings**: a synthetic returning `None` is omitted from the record, counted as missing and reported unavailable per language; the evaluator records unavailable per entity as well; the ratchet skips silently when either side lacks the metric.
- **Implications**: the floor is a declared guard on the synthetic metric (`floor=("CountStmt", 5)`) that the threshold evaluator and the ratchet consult; the worker always answers the ratio.

### Approximating the duplication defaults
- **Context**: requirement 5.7.
- **Sources Consulted**: the text-level measurement in the gap analysis (Python `tokenize`, this repository).
- **Findings**: exact windows fall from 641 at 5 lines to 72 at 12; similar pairs at ratio ≥ 0.9 number 184 with 10 in `src/`, and the `src/` ones are genuine copies. Tests dominate both.
- **Implications**: design defaults `duplicates_min_lines = 12`, `similar_min_statements = 6`, `similar_threshold = 0.9`, all to be re-measured with Understand's lexer in the implementation before the values are recorded in the docs; tests are handled by a proposed `[scope.tests]` in `init --detect` rather than by lower thresholds.

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
| --- | --- | --- | --- | --- |
| A: extend in place | new functions in `worker.py`, new fields in `StructureRules`, rules under `analysis/structure/` | one pattern, every test layer has a template | 140 lines of worker budget for six extractions; raising the ceiling is adapting the tool's own limits to fit a feature | rejected for extraction, kept for config and hints |
| B: second op and `lean` package | `worker_lean.py` as a separate op with its own database open | worker untouched | second open and second walk per side against a 6.5 s ceiling; helpers duplicated across two zero-import scripts | rejected |
| C: sibling loaded by path, called from the walk | `worker_lean.py` holds every lean measurement; `worker.py` loads it by path when the request asks and calls it per entity and once per project for tokens | one open, one walk; `worker.py` stays under its ceiling; sibling held to the same zero-import rule and hashed into the worker digest | a path load is a new shape for the import-direction test to know about | **chosen** |

## Design Decisions

### Decision: one `[lean]` section, rules named `structure.<name>`
- **Context**: nine rules with a severity, an ignore list and up to three numbers each; `StructureRules` already has twenty fields and `config/models.py` is exempt from the class-count limit for being a list of TOML shapes.
- **Alternatives Considered**: fields on `StructureRules`; a `[structure.lean]` sub-table; a `[lean]` section with its own model.
- **Selected Approach**: `Settings.lean: LeanRules`, a model in `config/models.py` following the `unused_routines` convention (`rule: Severity | None = None` is off, `rule_ignore: list[str]`), numbers as sibling keys. Findings keep the `structure.` category so the severity map, SARIF ids and hint lookup work unchanged; `structure.unused_routines` stays where it is and the docs present it as the family's first member.
- **Rationale**: an operator configures one section for one question; the finding names stay in one category.
- **Trade-offs**: `StructureRuleName` grows by nine; the template gains one block.

### Decision: lean facts ride on the snapshot
- **Context**: three rules need per-entity facts (callers, callees, overrides, unused parameters, derived classes, outside referrers), one needs per-definition facts, two need a token index.
- **Selected Approach**: `EntityRecord.lean: LeanFacts | None` (None = not asked), `Definition.referenced: bool | None`, `ProjectSnapshot.tokens: TokenIndex | None`; two request keys `lean_references` and `lean_tokens`; two fingerprint keys so the before-side cache invalidates when either is switched on. No "snapshot predates the rule" branch: a stale cache cannot be served.
- **Rationale**: one document, one cache, the same three-state discipline as `referenced`.
- **Trade-offs**: the token index adds to the cached JSON (estimated 1 to 3 MB on this repository); measured in the cost task.

### Decision: similarity is only ever asked of affected routines
- **Context**: all-pairs similarity over 5 689 routines is quadratic.
- **Selected Approach**: the index (4-gram shingles of the normalised token sequence) is built over the whole project; candidates are generated only for the change's affected routines; the sequence ratio is computed only for candidates sharing at least a fixed number of shingles.
- **Rationale**: the rule reports only affected entities by requirement 5.3, so nothing else needs a ratio.

### Decision: exact duplicates follow Understand's own normalisation
- **Selected Approach**: per file, one hash per code line built from lexeme texts with `Whitespace`, `Comment`, `Newline` dropped (and `Indent`/`Dedent`, which Understand's Python lexer emits), original line numbers kept; windows of `min_lines` matched across the project.
- **Rationale**: the Gate's `structure.duplicate_block` and Understand's `DuplicateLinesOfCode` then agree on what a duplicate is, and an operator can use either.

### Decision: the floor is a declared guard
- **Selected Approach**: `SyntheticMetric.floor: tuple[str, int] | None`; `LinesPerStatement` declares `("CountStmt", 5)`; `evaluate_thresholds` and `evaluate_ratchet` skip an entity below the floor for that metric without recording anything.

### Decision: the net delta is summed over routines of affected files, both sides
- **Selected Approach**: `CountStmt` and `CountLineCode` of every routine record in an affected or deleted file, after minus before, paired through `record_of` so a changed signature is not counted as a delete plus an add. Reported as `NetDelta(statements, lines, routines)`; omitted without a before side.
- **Rationale**: routine scope carries both metrics by default; file scope carries only one; module-level code outside routines is the documented blind spot.

### Decision: examples are catalogue entries under `<rule>/example`
- **Selected Approach**: the hint catalogue's key grammar already has a `/variant` level; examples are stored as `structure.pass_through/example` and so on, in a separate `report/lean_examples.py` dict merged into the catalogue, overridable through `[hints]` at that key. `_finish` copies the example into `Finding.details["example"]` so JSON carries it; verbose human output prints it after the hint.

### Decision: `stdlib:` and `native:` stay with the agent
- **Selected Approach**: the agent-rules snippet states the two tags the Gate never emits and reproduces ponytail's seven rungs in one line each, so the agent runs them on the findings the Gate gives it.

## Risks
- **Caller counts under 43 % call resolution (Python)**: a routine called through an unresolved site looks like it has fewer callers than it has. Mitigation: the pass-through rule requires *exactly one* caller, so an undercount can only turn a real finding into silence, never silence into a finding; the contract test records the measured agreement with `CountCallby`.
- **Inheritance kind strings on languages the contract project does not build (Java, C#)**: unverified; documented as such per language until measured.
- **Token pass cost**: unmeasured until the worker exists; the design gates it behind two rules that ship off and the cost task has the 6.5 s ceiling.
- **Fixture churn**: `tests/contract/contract_project.py` grows by the cases the new rules need; tests that assert its counts move with it.
