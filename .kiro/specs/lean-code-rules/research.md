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

---

# A second repository, measured 2026-09-10

Run read-only against `facdrone` (945 files, 9449 routines, 1809 classes, Python and Web,
its own `scitools-hook.toml`, no hook installed) with the working-tree tool, to ask whether
the defaults tasks 1.3 and 1.4 shipped from this repository's numbers generalise.

| Metric | scitools-hook | facdrone | Verdict |
| --- | --- | --- | --- |
| `routine.LinesPerStatement = 3.0` | 2963 judged, p50 1.20, p95 2.33, **53 outside (1.8%)**, `keep 3` | 5084 judged, p50 1.33, p95 3.78, max 19.6, **442 outside (8.7%)**, `raise 3 -> 4` (204 outside, 4.0%) | **does not generalise** |
| `routine.CountLineComment = 20` | 5916 routines, p95 8, 20 outside (0.3%), `keep 20` | absent from the deviation list, i.e. `keep` | **generalises** |

The tool's own bar is that a ceiling fits when it contains 95% of its population, so 91.3%
inside misses it. The floor is working on both -- facdrone judges 5084 of 9449 routines, so
4365 are below five statements and correctly not judged.

**Not acted on here.** Task 6.4 owns adjusting shipped defaults from measurement and now has
two repositories rather than one. The options it should weigh, with these numbers:

- **4.0 fits both** (facdrone 4.0% outside; this repository would be further inside still),
  at the cost of only reporting routines that are more than four lines per statement.
- **3.0 stays** and a verbose repository uses `recommend`, which is what `recommend` is for.
  The rule ships as a warning, so 8.7% is noise rather than a blocked commit -- but this
  project's own guidance is that a ceiling most of a repository fails is not a limit, and 8.7%
  is a lot of warnings to teach an operator to ignore.

Two other things this run established. The `NO_PROBE` row task 1.2 added renders correctly on
a repository that is not this one ("feature lean references: not measured (this build was
probed before the feature existed)"). And facdrone's analysis resolves at 26% against this
repository's 19%, which matters for the false structural finding recorded in `tasks.md`: that
artefact was attributed to an under-resolved before side, and a second repository at a
different resolution is where to reproduce it deliberately.


---

# Reference kinds and counts on the contract project, measured 2026-09-11 (task 6.1)

Understand 8.0 Build 1262. Everything between the two rules below is the output of
`tests/contract/test_lean_references_contract.py::test_contract_the_kind_table_is_printed_for_the_record`
on this machine, pasted rather than typed, so the log and the run cannot disagree; the module
is 33 tests and runs in 17.9 s serially (`-n 0`). Three databases are read: the contract
project's `alpha` side with every reference rule on, a scratch tree with three shapes of a
parameter in Python and C++, and a scratch tree in the six languages the contract project does
not build (Ada, C#, Fortran, Java, Pascal, TypeScript), each with one base, one derived type
and, where the language has one, an interface and its implementer.

---

und: /home/mc/scitools/bin/linux64/und

| figure | value |
| --- | --- |
| accuracy (`und analyze -accuracy`, alpha) | 61.9% |
| call resolution, C++ | 7 of 9 call sites resolved (77.8%) |
| call resolution, Python | 6 of 13 call sites resolved (46.2%) |

| language | on the base | on the derived class | override | callby kinds seen |
| --- | --- | --- | --- | --- |
| C++ | C Public Derive | C Public Base | C Overrides | C Callby, C Callby Implicit |
| Python | Python Inheritby | Python Inherit | Python Overrides | Python Callby |
| Ada (scratch) | Ada Derive | Ada Derivefrom | Ada Overrides | n/a |
| C# (scratch) | c# csharp Derive; interface: c# csharp Implementby / implementer: c# csharp Implement, c# csharp Base, c# csharp Couple | c# csharp Base, c# csharp Couple | c# csharp Overrides | n/a |
| Fortran (scratch) | Fortran Extendby | Fortran Extend | (none) | n/a |
| Java (scratch) | Java Extendby Coupleby; interface: Java Implementby Coupleby / implementer: Java Implement Couple | Java Extend Couple | Java Overrides | n/a |
| Pascal (scratch) | Pascal Derive | Pascal Derivefrom | Pascal Overrides | n/a |
| Web (scratch) | web Javascript Extendby; interface: web Javascript Implementby / implementer: web Javascript Implement | web Javascript Extend | web Javascript Overrides | n/a |

| language | parameter | every reference on its declaration | reported unused |
| --- | --- | --- | --- |
| Python | `defaulted(verbose)` | Python Definein | yes |
| Python | `defaulted_read(verbose)` | Python Definein, Python Useby | no |
| Python | `reassigned(verbose)` | Python Definein, Python Setby | no |
| C++ | `defaulted(verbose)` | C Definein | yes |
| C++ | `defaulted_read(verbose)` | C Definein, C Useby | no |
| C++ | `reassigned(verbose)` | C Definein, C Setby | no |

callers: 36 routines probed; `LeanFacts.callers` == `CountCallbyUnique` on every one, and `CountCallby` == `CountCallbyUnique` on 36 of 36

---

## What the run established

- **Inheritance (req 3.1).** `Python Inheritby` and `C Public Derive` fire on the base naming
  the derived class; `LeanFacts.derived` is `[layers.OnlyChannel]` and `[OnlyNativeChannel]`.
  The derived class carries `Python Inherit` / `C Public Base` back, which neither set matches,
  so a subclass is not a user of its base.
- **Overrides (req 1.4).** `Python Overrides` and `C Overrides` on the two overriding `send`
  methods, `overrides=True`; both base methods `False`. The C++ half was the one the fixture
  could not assume (a virtual member with no `override` keyword); this build records it.
- **Callers (req 2.1).** `LeanFacts.callers` equals `CountCallbyUnique` on all 36 routines the
  fixture records, and `CountCallby` equals it too, so there is no disagreement to record with
  a cause. The two shapes where the worker and the plugin would part -- a call from module
  scope, which the plugin counts and the worker does not, and a caller outside the analysis
  root -- do not occur in the fixture. The C++ constructor call `Shape shape(side)` is recorded
  as `C Callby Implicit`, which the `callby` filter matches; it is the one caller kind beyond
  plain `Callby` this fixture produces.
- **The floors (req 1.8) on this database.** Accuracy 61.9% (13 of 21 files; each of the eight
  C++ translation units carries clang's libstdc++ note, which `-accuracy` counts against it),
  Python call resolution 46.2% (6 of 13), C++ 77.8% (7 of 9). At the shipped floors of 75%
  the contract project itself is refused on accuracy, and a test asserts exactly that. The
  rule tests pass the measured accuracy with both floors at zero, and say so.
- **Each reference rule reports its planted case and nothing else**, with the shipped ignore
  lists: `unused_parameters` -> `dead.advance(verbose)`, `native_advance(verbose)`;
  `unused_classes` -> `dead.ForgottenReport`, `ForgottenNativeReport`; `unused_variables` ->
  `RETRY_LIMIT`, `kRetryLimit`; `pass_through` -> `layers.display_name` forwarding to
  `layers.canonical_name`, `display_native_name` to `canonical_native_name`;
  `single_implementation` -> `layers.BaseChannel` with `layers.OnlyChannel`,
  `BaseNativeChannel` with `OnlyNativeChannel`. No unavailable note from any of the five.
- **`setby` in `PARAMETER_USE` (task 3.1's review).** Understand records **no** `Set Init`
  against a defaulted parameter's own declaration, in either language: `defaulted(verbose)`
  carries `Definein` and nothing else and is reported unused. `reassigned(verbose)` carries
  `Setby` and is not reported, which is what the member is for. The set is unchanged, and the
  claim now has a test behind it rather than reasoning.
- **`derive` direction (task 3.2's review, the design's open item).** The reading that
  `Derive (Derivefrom)` means `Derive` is the forward reference for Ada and Pascal is **false
  on this build**: `Ada Derive` and `Pascal Derive` sit on the base naming the derived type,
  as `C Public Derive` and `c# csharp Derive` do, and the derived type carries `Derivefrom`,
  which the word `derive` does not match. The kind list's pair order varies per language
  section; the direction does not. Java, filed under `Couple`, answers `Java Extendby Coupleby`
  on the base and `Java Implementby Coupleby` on the interface, both matched by members already
  in the set. Fortran answers `Extendby`, TypeScript `Extendby` and `Implementby`.
  `DERIVED_KINDS` needs no per-language spelling and is unchanged; the design and the worker
  docstring are corrected.
- **Overrides per language.** `Overrides` fires on the overriding method in Ada, C#, Java,
  Pascal and a TypeScript `extends`. It does **not** fire on a TypeScript `implements`
  (`TsSquare.area` carries no override reference), so for that shape requirement 1.4's
  exclusion rests on the method-declaration tally (1.9) alone.

## Two dependencies the run found, one fixed

- **`Settings.wants_definitions` did not know `lean.unused_variables`** (fixed here). The
  property switched the definitions walk on for `structure.duplicate_definitions` and
  `lean.over_export` only. A configuration with `unused_variables` on and neither of those got
  a snapshot whose `definitions` list was empty; the rule walked nothing, reported nothing and
  raised no unavailable note, because an empty list is a project without module bindings as
  far as it can tell. On the contract project that is both planted cases lost in silence --
  the failure this task was sent to look for in `setby`, found one rule over. The RED run of
  the new module shows it (`set() == {RETRY_LIMIT, kRetryLimit}` with `unavailable == ()`);
  the fix adds the third rule to the property and a unit test beside the two existing ones
  (`tests/understand/test_snapshot_extractor.py`).
- **The pass-through rule reads `CountStmt` off the record and asks for nothing** (not
  changed). Under the contract project's fixed threshold list, which carries no
  `routine.CountStmt`, the rule judged nothing and said nothing, by task 4.2's decision that a
  routine without a statement count is a record the rule does not judge. The shipped defaults
  carry `routine.CountStmt = 40`, so a real run has it; an operator who deletes that threshold
  silences the rule without a note. The contract test adds the threshold as the defaults do
  and records why. Whether the extractor should ask for `CountStmt` whenever `pass_through`
  is on is a follow-up for the parent, not decided here.

## Limits of the measurement

- The extractor's class kinds (`class`, `interface`, `struct`) record neither an Ada tagged
  type nor a Fortran derived type, so `class_facts` does not run on either language today;
  the direction result above is about the reference kinds, not about a rule reaching them.
- An Ada primitive operation declares a `Self` parameter of the tagged type, and its `Typedby`
  reference is outside `_own_ids`, so an Ada base with an operation would count one referrer
  and never be a single implementation. Recorded, not addressed.
- A plain `.js` file holding an ES6 `class ... extends` produced no entities at all on this
  build in a scratch probe; only the `.ts` file is measured and asserted. Observed, not
  asserted.
- The per-language table is what this build records for one base, one derived type and one
  override; multiple inheritance, generics and nested types were not planted.

## Mutation evidence (`__pycache__` cleared before each trial and after the copy-restore)

- `derive` removed from `DERIVED_KINDS`: 4 failed, 9 passed of the inheritance tests --
  the C++ fixture base, and Ada, C# and Pascal in the scratch tree.
- `setby` removed from `PARAMETER_USE`: 2 failed, 2 passed -- `reassigned(verbose)` reported
  unused in both languages.
- `wants_definitions` without the third rule (the RED run): the variable-rule test fails with
  an empty finding set and no unavailable note; the other ten tests of that run pass.

---

# Task 6.2: the token index on the contract project, measured 2026-09-11

Build 1262, `tests/contract/test_token_index_contract.py`. Every table below is printed by the
test that asserts it, so this section and a run of the module cannot disagree; the module runs
in about 17 s and the whole contract suite is the gate.

## Coverage (requirement 5.4)

`routines: 36 recorded, 36 spanned by the API, 36 indexed; 741 routine entities in the
database`. "Spanned" is read off the raw API under `upython` with the kind strings spelled in
the test rather than imported: `ent.ref("definein")` and `ent.ref("end")` both present and in
one file under the analysis root. The index equals that set in both directions, and on this
fixture that set is every recorded routine; the other 705 routine entities are the library
routines Understand injects outside the root, which the extractor never records.

## The duplication findings at the shipped numbers (requirement 5.7)

The whole fixture as the affected set, `duplicates_min_lines = 12`, `similar_threshold = 0.9`,
`similar_min_statements = 6`, both rules called with their shipped defaults (which the test
asserts are the `LeanRules()` values). The **exact** finding set, asserted as a list:

```
duplicate_block at min_lines 12:
  ('lean/table_left.py', 11, 24, 14.0, ('lean/table_right.py:10', 'lean/table_right.py:11', 'lean/table_right.py:12'))
  ('lean/table_right.py', 10, 23, 14.0, ('lean/table_left.py:11', 'lean/table_left.py:12', 'lean/table_left.py:13'))
  ('native/lean_table_left.cpp', 13, 28, 16.0, ('native/lean_table_right.cpp:7', 'native/lean_table_right.cpp:8', 'native/lean_table_right.cpp:9'))
  ('native/lean_table_right.cpp', 7, 22, 16.0, ('native/lean_table_left.cpp:13', 'native/lean_table_left.cpp:14', 'native/lean_table_left.cpp:15'))
similar_routine at threshold 0.9, min_statements 6:
  ('lean/twin_left.py', 9, 'twin_left.summarise_orders', 2.0, 1.0, ('twin_right.summarise_invoices (lean/twin_right.py:9)',), None)
  ('native/lean_twin_left.cpp', 13, 'summarise_native_orders', 2.0, 1.0, ('summarise_native_invoices (native/lean_twin_right.cpp:4)',), None)
```

Columns: path, start line, end line, code lines / family size, other locations; and for a
family: path, line, anchor longname, size, weakest similarity, other members, construct.

- Four blocks and two families, nothing else: the planted block per language reported from
  both ends, the planted twin per language as one family of two at exactly 1.0. The
  cross-language pairs (fixture: 0.63) form no family and no window crosses languages.
- **Each block names three locations and all three are windows of the one other copy**
  (`:10`, `:11`, `:12` of a fourteen-line block at twelve). That is item 7 of the
  "found while reviewing 4.2" list above, now measured on the real build: the message says
  `also holds at lean/table_right.py:10, lean/table_right.py:11, lean/table_right.py:12` for
  one copy. The test pins the shape as measured so that the fix, when it comes, is shown to
  change the output on the real build. Not fixed here: it is owned by no task and is not this
  task's subject.
- Mutation evidence for the pin, each trial with `__pycache__` cleared before and after and
  the file restored by copy and verified by SHA-256: scaling `similar._score`'s ratio by 0.95
  (families still form, similarity 0.95), keeping only the first location in
  `duplicates._block`, and replacing the shipped `similar_min_statements` with 20 -- all three
  fail the exact-set test. `worker_lean.py` was deliberately not mutated: task 6.1 was editing
  it concurrently.

## Docstring accounting (requirement 6.5), re-confirmed on the fixture's Python

Per routine, `CountLine` / `CountLineCode` / `CountLineComment` / `CountStmt` from Understand
against the index (span, indexed lines in the span, `LIT` tokens in the shape,
`RoutineShape.statements`). The first four carry a one-line docstring; the last two none.

| routine | CountLine | CountLineCode | CountLineComment | CountStmt | span | indexed lines | LIT tokens | shape.statements |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| layers.canonical_name | 4.0 | 3.0 | 1.0 | 3.0 | 26-29 | 4 | 1 | 3 |
| layers.display_name | 3.0 | 2.0 | 1.0 | 2.0 | 32-34 | 3 | 1 | 2 |
| layers.open_channel | 4.0 | 3.0 | 1.0 | 3.0 | 37-40 | 4 | 1 | 3 |
| dead.advance | 4.0 | 3.0 | 1.0 | 3.0 | 26-29 | 4 | 1 | 3 |
| layers.BaseChannel.send | 2.0 | 2.0 | 0.0 | 2.0 | 14-15 | 2 | 0 | 2 |
| twin_left.summarise_orders | 9.0 | 9.0 | 0.0 | 9.0 | 9-17 | 9 | 6 | 9 |

Per file, against a line budget read off the fixture text with Python's own `tokenize`
(blank lines outside docstrings; docstring count; non-blank docstring lines; blank lines inside
docstrings; plain code lines), and the index's line count and first indexed line:

| file | CountLine | CountLineCode | CountLineComment | CountStmt | lines | blank | docstrings | docstring prose | docstring blank | plain code | indexed lines | first indexed |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| app/entry.py | 10.0 | 5.0 | 1.0 | 5.0 | 10 | 4 | 1 | 1 | 0 | 5 | 6 | 1 |
| lean/dead.py | 29.0 | 7.0 | 13.0 | 7.0 | 29 | 7 | 3 | 13 | 2 | 7 | 10 | 1 |
| lean/exported.py | 10.0 | 2.0 | 5.0 | 2.0 | 10 | 2 | 1 | 5 | 1 | 2 | 3 | 1 |
| lean/layers.py | 40.0 | 15.0 | 12.0 | 15.0 | 40 | 12 | 6 | 12 | 1 | 15 | 21 | 1 |
| lean/table_left.py | 24.0 | 15.0 | 6.0 | 2.0 | 24 | 2 | 1 | 6 | 1 | 15 | 16 | 1 |
| lean/table_right.py | 23.0 | 15.0 | 5.0 | 2.0 | 23 | 2 | 1 | 5 | 1 | 15 | 16 | 1 |
| lean/twin_left.py | 17.0 | 9.0 | 5.0 | 9.0 | 17 | 2 | 1 | 5 | 1 | 9 | 10 | 1 |
| lean/twin_right.py | 17.0 | 9.0 | 5.0 | 9.0 | 17 | 2 | 1 | 5 | 1 | 9 | 10 | 1 |
| main.py | 7.0 | 3.0 | 1.0 | 3.0 | 7 | 3 | 1 | 1 | 0 | 3 | 4 | 1 |
| pkg/core.py | 20.0 | 13.0 | 1.0 | 11.0 | 20 | 6 | 1 | 1 | 0 | 13 | 14 | 1 |
| pkg/inner/leaf.py | 6.0 | 3.0 | 1.0 | 3.0 | 6 | 2 | 1 | 1 | 0 | 3 | 4 | 1 |

| class | CountLine | CountLineCode | CountLineComment | CountStmt |
| --- | --- | --- | --- | --- |
| dead.ForgottenReport | 2.0 | 1.0 | 1.0 | 1.0 |
| layers.OnlyChannel | 6.0 | 4.0 | 1.0 | 4.0 |

And three docstring-only initialisers beside a module with code, in a project of their own
built once per Python grammar with `und settings -PythonSetVersion` pinned and read back --
`old/__init__.py` is `analysis/lean/__init__.py` at commit `acd741c`, byte for byte:

| grammar | file | CountLine | CountLineCode | CountLineComment | CountStmt | docstring prose | plain code | indexed lines |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Python2 | line/__init__.py | 1.0 | 0.0 | 1.0 | 0.0 | 1 | 0 | 1 |
| Python2 | old/__init__.py | 15.0 | 0.0 | 13.0 | 0.0 | 13 | 0 | 1 |
| Python2 | prose/__init__.py | 4.0 | 0.0 | 3.0 | 0.0 | 3 | 0 | 1 |
| Python2 | prose/module.py | 5.0 | 2.0 | 1.0 | 2.0 | 1 | 2 | 3 |
| Python3 | line/__init__.py | 1.0 | 0.0 | 1.0 | 0.0 | 1 | 0 | 1 |
| Python3 | old/__init__.py | 15.0 | 0.0 | 13.0 | 0.0 | 13 | 0 | 1 |
| Python3 | prose/__init__.py | 4.0 | 0.0 | 3.0 | 0.0 | 3 | 0 | 1 |
| Python3 | prose/module.py | 5.0 | 2.0 | 1.0 | 2.0 | 1 | 2 | 3 |

What the tables say, each line asserted by the test:

1. **To Understand, a non-blank docstring line is a comment line, never a code line and never
   a statement**, at routine, class and file level, for one-line and multi-line docstrings,
   for a file that is nothing but a docstring, and under both Python grammars. `CountLineCode`
   equals the plain code lines and `CountLineComment` the non-blank docstring lines on all 11
   fixture files and all 8 initialiser rows. A blank line inside a docstring is neither: it is
   in `CountLine` and in nothing else (`lean/dead.py`: 15 docstring lines, 13 comment lines).
   This confirms the design-phase entry above ("docstrings are comment lines") with the
   sample it lacked.
2. **`CountStmt` counts the `def` line**: a routine of `def` plus one `return` measures 2, and
   a docstring adds nothing to it (`display_name`: `def`, docstring, `return` measure 2). So
   `similar_min_statements = 6` is a body of five statements, and the fixture's twins clear it
   at 9. `RoutineShape.statements` carries the same number.
3. **To the index a docstring is one line and one token.** The lexer yields one `String`
   lexeme for the whole docstring, so `tokens.files` holds one code line at the line it opens
   on -- every Python file is indexed from line 1, and a file's indexed lines are its plain
   code lines plus one per docstring -- and the shape holds one `LIT` for it whatever its
   length. A C++ `//` comment is `Comment` and dropped: `native/lean_twin_left.cpp` is indexed
   from line 13 and its twin from line 4, past their comment headers. For the rules: a copied
   docstring contributes at most **one** line to a duplicate window, so twelve identical lines
   of prose are never a block, and two twins with different docstrings still match at 1.0
   because both docstrings are `LIT`.
4. **The `layering.py` claim did not reproduce.** `analysis/lean/layering.py` records that
   Understand charged this repository's multi-line docstring-only `analysis/lean/__init__.py`
   as code lines, inferred from a per-file dependency count that fell from eight to seven when
   the docstring was shortened to one line. Measured on that initialiser's own text under both
   grammars: `CountLineCode` 0, `CountLineComment` 13. So `coupling.namespace_targets` reads
   that file as empty either way, and the eight-to-seven change had some other cause. The
   paragraph in `layering.py` is wrong on Build 1262 as far as this measurement reaches, and the
   test is written to fail if either grammar ever answers the paragraph's way. **Owned by
   whoever next touches `layering.py`**; not edited by this task, whose boundary is the test
   and this record.
5. **Which grammar the contract databases use is now read rather than assumed.** A scratch
   database built exactly as `build_database` builds the fixture read back `PythonSetVersion
   Python2` on this machine even with `uv run`'s `.venv/bin/python` on `PATH`, which agrees
   with the fixture's own note that Understand falls back to Python 2 here. Nothing in this
   section depends on the grammar -- every row is the same under both -- but the next
   measurement that does should pin it the way `grammar_database` does.

---

# Task 6.3: every lean default on two repositories, and the cost of everything on, measured 2026-09-11

Understand 8.0 Build 1262, the working tree at `e951c0a` (`0.1.0a9`), this 4-core machine
with other work on it (the one-minute load average is printed beside every timed run). Every
table and every `- \`path:line\`` bullet below is the output of `tests/perf/lean_defaults.py`,
the harness this task adds beside `warm_run_timing.py`, or of
`tests/contract/test_lean_cost_contract.py`, the cost test it adds -- pasted, not typed, so
the log and a re-run cannot disagree. The prose between them is the implementer's reading of
the sources the first ten findings name, which is the judgement task 6.4 acts on.

**Method.** The harness copies the repository's own `scitools-hook.toml`, appends a `[lean]`
section switching all eight rules on as warnings and nothing else -- so every number, ignore
list and threshold is the shipped default -- and runs `check --all --format json --verbose`
under `/usr/bin/time -v`. Counts are per rule name over the JSON's `findings`; the note beside
a count is the once-per-run "was not evaluated" line requirement 1.8 has a refused rule print,
because a zero with that reason and a zero without it are different answers. Both
repositories sit below the shipped accuracy floor of 0.75 (19.1% and 25.9%), so the three
dead-code rules and the pass-through rule were refused at the shipped floors, as designed, and
the harness ran a second whole-project pass with `resolution_floor = 0.0` and
`accuracy_floor = 0.0` so that what they *would* say is on record. The three shrink thresholds
(`routine.LinesPerStatement = 3.0`, `routine.CountLineComment = 20`, the shipped minimum on
`file.RatioCommentToCode`) ship on as warnings and are counted from the same run; the family
therefore has eleven measured rows, not nine. `max_net_growth` ships off with no number to
measure and has no row. The `first N` lists are in the order the check printed them, which
puts `src/` before `tests/`.

Commands, from this repository:

```
uv run python tests/perf/lean_defaults.py /home/mc/Source/scitools-hook --lower-floors
uv run python tests/perf/lean_defaults.py /home/mc/Source/facdrone --lower-floors --no-timing \
    --config <scratch>/fac-base.toml
uv run pytest tests/contract/test_lean_cost_contract.py -n 0 -s
```

`<scratch>/fac-base.toml` is facdrone's own configuration with one line changed: `[baseline]
file` pointed at a copy of its baseline in the scratch directory (see the facdrone notes for
why). The manual runs that preceded the harness -- the same commands typed out, on
2026-09-11 between 13:24 and 13:40 -- produced the same counts on both repositories to the
finding (48 / 163 / 57 / 23 / 24 and 68 / 0 / 17 / 47 here; 152 / 129 / 1 / 442 / 11 / 227
and 58 / 18 / 55 / 59 on facdrone), which is the check that the harness measures what the
hand did.

## This repository: 319 analysed files, accuracy 19.1%

### Every lean rule on at the shipped numbers and floors


- **accuracy** (after side, from the JSON): {'after': 0.1910828025477707}
- **analysed files**: 319

| rule (lean-on) | count | note |
| --- | --- | --- |
| `structure.unused_parameter` | 0 | Understand parsed 19% of this analysis without an error, below the accuracy floor of 75%; a name whose use sites sit in a region the analysis errored on reads as unused while it is read |
| `structure.unused_class` | 0 | Understand parsed 19% of this analysis without an error, below the accuracy floor of 75%; a name whose use sites sit in a region the analysis errored on reads as unused while it is read |
| `structure.unused_variable` | 0 | Understand parsed 19% of this analysis without an error, below the accuracy floor of 75%; a name whose use sites sit in a region the analysis errored on reads as unused while it is read |
| `structure.pass_through` | 0 | Understand parsed 19% of this analysis without an error, below the accuracy floor of 75%; a name whose use sites sit in a region the analysis errored on reads as unused while it is read |
| `structure.single_implementation` | 0 |  |
| `structure.over_export` | 0 |  |
| `structure.duplicate_block` | 48 |  |
| `structure.similar_routine` | 163 |  |
| `routine.LinesPerStatement` | 57 |  |
| `routine.CountLineComment` | 23 |  |
| `file.RatioCommentToCode` | 24 |  |

**`structure.unused_parameter`**: 0 findings, first 0:

**`structure.unused_class`**: 0 findings, first 0:

**`structure.unused_variable`**: 0 findings, first 0:

**`structure.pass_through`**: 0 findings, first 0:

**`structure.single_implementation`**: 0 findings, first 0:

**`structure.over_export`**: 0 findings, first 0:

**`structure.duplicate_block`**: 48 findings, first 10:
- `src/scitools_hook/config/models.py:474` src/scitools_hook/config/models.py:474-492 repeats 19 code lines this project also holds at src/scitools_hook/git/shadow.py:352, src/scitools_hook/git/shadow.py:353, src/scitools_hook/git/shadow.py:354, and 5 more
- `src/scitools_hook/git/shadow.py:352` src/scitools_hook/git/shadow.py:352-370 repeats 19 code lines this project also holds at src/scitools_hook/config/models.py:474, src/scitools_hook/config/models.py:475, src/scitools_hook/config/models.py:476, and 5 more
- `src/scitools_hook/models/findings.py:47` src/scitools_hook/models/findings.py:47-66 repeats 20 code lines this project also holds at tests/models/test_findings.py:78, tests/models/test_findings.py:79, tests/models/test_findings.py:80, and 6 more
- `tests/analysis/test_change_summary.py:58` tests/analysis/test_change_summary.py:58-73 repeats 12 code lines this project also holds at tests/analysis/test_ratchet.py:40
- `tests/analysis/test_ratchet.py:40` tests/analysis/test_ratchet.py:40-55 repeats 12 code lines this project also holds at tests/analysis/test_change_summary.py:58
- `tests/analysis/test_ratchet.py:453` tests/analysis/test_ratchet.py:453-466 repeats 13 code lines this project also holds at tests/analysis/test_ratchet.py:478, tests/analysis/test_ratchet.py:479
- `tests/analysis/test_ratchet.py:478` tests/analysis/test_ratchet.py:478-491 repeats 13 code lines this project also holds at tests/analysis/test_ratchet.py:453, tests/analysis/test_ratchet.py:454
- `tests/cli/test_cli_app.py:595` tests/cli/test_cli_app.py:595-609 repeats 13 code lines this project also holds at tests/cli/test_cli_app.py:709, tests/cli/test_cli_app.py:710, tests/cli/test_cli_app.py:849, and 2 more
- `tests/cli/test_cli_app.py:709` tests/cli/test_cli_app.py:709-723 repeats 13 code lines this project also holds at tests/cli/test_cli_app.py:595, tests/cli/test_cli_app.py:596, tests/cli/test_cli_app.py:849, and 2 more
- `tests/cli/test_cli_app.py:849` tests/cli/test_cli_app.py:849-863 repeats 13 code lines this project also holds at tests/cli/test_cli_app.py:595, tests/cli/test_cli_app.py:596, tests/cli/test_cli_app.py:709, and 2 more

**`structure.similar_routine`**: 163 findings, first 10:
- `src/scitools_hook/analysis/lean/dead.py:305` scitools_hook.analysis.lean.dead.find_unused_classes (src/scitools_hook/analysis/lean/dead.py:305) is 1 of 2 routines this project holds in near-identical form, the weakest pair matching at 0.93; the others are scitools_hook.analysis.lean.dead.find_unused_variables (src/scitools_hook/analysis/lean/dead.py:329)
- `src/scitools_hook/analysis/lean/dead.py:375` scitools_hook.analysis.lean.dead._routine_facts (src/scitools_hook/analysis/lean/dead.py:375) is 1 of 3 routines this project holds in near-identical form, the weakest pair matching at 0.99; the others are scitools_hook.analysis.lean.dead._class_facts (src/scitools_hook/analysis/lean/dead.py:404), scitools_hook.analysis.lean.layering._forwarder_facts (src/scitools_hook/analysis/lean/layering.py:492)
- `src/scitools_hook/analysis/lean/dead.py:386` scitools_hook.analysis.lean.dead._routine_fact (src/scitools_hook/analysis/lean/dead.py:386) is 1 of 2 routines this project holds in near-identical form, the weakest pair matching at 0.94; the others are scitools_hook.analysis.lean.layering._base_fact (src/scitools_hook/analysis/lean/layering.py:629)
- `src/scitools_hook/analysis/lean/dead.py:505` scitools_hook.analysis.lean.dead._reported_class (src/scitools_hook/analysis/lean/dead.py:505) is 1 of 2 routines this project holds in near-identical form, the weakest pair matching at 0.93; the others are scitools_hook.analysis.lean.dead._reported_binding (src/scitools_hook/analysis/lean/dead.py:528)
- `src/scitools_hook/analysis/lean/duplicates.py:416` scitools_hook.analysis.lean.duplicates._unreadable_note (src/scitools_hook/analysis/lean/duplicates.py:416) is 1 of 2 routines this project holds in near-identical form, the weakest pair matching at 1.00; the others are scitools_hook.analysis.lean.similar._unreadable_note (src/scitools_hook/analysis/lean/similar.py:979)
- `src/scitools_hook/analysis/lean/layering.py:333` scitools_hook.analysis.lean.layering._dependants (src/scitools_hook/analysis/lean/layering.py:333) is 1 of 2 routines this project holds in near-identical form, the weakest pair matching at 1.00; the others are scitools_hook.analysis.structure.coupling._targets_by_file (src/scitools_hook/analysis/structure/coupling.py:146)
- `src/scitools_hook/cli/common.py:782` scitools_hook.cli.common._list_lines (src/scitools_hook/cli/common.py:782) is 1 of 2 routines this project holds in near-identical form, the weakest pair matching at 0.93; the others are scitools_hook.cli.common._block_lines (src/scitools_hook/cli/common.py:792)
- `src/scitools_hook/cli/config_cmd.py:347` scitools_hook.cli.config_cmd._reject_unusable (src/scitools_hook/cli/config_cmd.py:347) is 1 of 2 routines this project holds in near-identical form, the weakest pair matching at 0.96; the others are scitools_hook.understand.cache_files.present (src/scitools_hook/understand/cache_files.py:59)
- `src/scitools_hook/config/loader.py:235` scitools_hook.config.loader._read_toml (src/scitools_hook/config/loader.py:235) is 1 of 2 routines this project holds in near-identical form, the weakest pair matching at 0.91; the others are scitools_hook.config.loader._env_value (src/scitools_hook/config/loader.py:351)
- `src/scitools_hook/config/models.py:472` scitools_hook.config.models._translate_path_pattern (src/scitools_hook/config/models.py:472) is 1 of 2 routines this project holds in near-identical form, the weakest pair matching at 1.00; the others are scitools_hook.git.shadow._translate (src/scitools_hook/git/shadow.py:345)

**`routine.LinesPerStatement`**: 57 findings, first 10:
- `src/scitools_hook/analysis/change_summary.py:105` routine scitools_hook.analysis.change_summary.build_summary LinesPerStatement is 3.125, which exceeds the maximum 3
- `src/scitools_hook/analysis/structure/calls.py:319` routine scitools_hook.analysis.structure.calls._cycle_finding LinesPerStatement is 6.4, which exceeds the maximum 3
- `src/scitools_hook/analysis/structure/unused.py:60` routine scitools_hook.analysis.structure.unused.find_unused_routines LinesPerStatement is 4, which exceeds the maximum 3
- `src/scitools_hook/analysis/thresholds.py:305` routine scitools_hook.analysis.thresholds.evaluate_thresholds LinesPerStatement is 3.14286, which exceeds the maximum 3
- `src/scitools_hook/cli/explain.py:111` routine scitools_hook.cli.explain.explain LinesPerStatement is 3.5, which exceeds the maximum 3
- `src/scitools_hook/config/template.py:236` routine scitools_hook.config.template._structure_body LinesPerStatement is 7.2, which exceeds the maximum 3
- `src/scitools_hook/config/template.py:665` routine scitools_hook.config.template._subproject_suggestions LinesPerStatement is 3.33333, which exceeds the maximum 3
- `src/scitools_hook/config/template.py:839` routine scitools_hook.config.template.render_template LinesPerStatement is 4.2, which exceeds the maximum 3
- `src/scitools_hook/git/repo.py:215` routine scitools_hook.git.repo.GitRepo.discover LinesPerStatement is 3.42857, which exceeds the maximum 3
- `src/scitools_hook/runner/baseline_cmd.py:109` routine scitools_hook.runner.baseline_cmd.BaselineCmd._captured LinesPerStatement is 3.2, which exceeds the maximum 3

**`routine.CountLineComment`**: 23 findings, first 10:
- `src/scitools_hook/analysis/ratchet.py:246` routine scitools_hook.analysis.ratchet.attach_before CountLineComment is 22, which exceeds the maximum 20
- `src/scitools_hook/analysis/thresholds.py:116` routine scitools_hook.analysis.thresholds.resolve_for_path CountLineComment is 22, which exceeds the maximum 20
- `src/scitools_hook/cli/common.py:452` routine scitools_hook.cli.common._deliver CountLineComment is 24, which exceeds the maximum 20
- `src/scitools_hook/cli/common.py:497` routine scitools_hook.cli.common._replace_atomically CountLineComment is 21, which exceeds the maximum 20
- `src/scitools_hook/cli/common.py:579` routine scitools_hook.cli.common._write_stdout CountLineComment is 51, which exceeds the maximum 20
- `src/scitools_hook/config/models.py:1098` routine scitools_hook.config.models.Settings.wants_definitions-getter CountLineComment is 28, which exceeds the maximum 20
- `src/scitools_hook/git/repo.py:403` routine scitools_hook.git.repo.GitRepo._reject_empty_hooks_path CountLineComment is 24, which exceeds the maximum 20
- `src/scitools_hook/models/cache.py:176` routine scitools_hook.models.cache.SyncState.record_parse_errors CountLineComment is 21, which exceeds the maximum 20
- `src/scitools_hook/models/cache.py:236` routine scitools_hook.models.cache._inside CountLineComment is 26, which exceeds the maximum 20
- `src/scitools_hook/models/snapshot.py:252` routine scitools_hook.models.snapshot._index_by_key CountLineComment is 21, which exceeds the maximum 20

**`file.RatioCommentToCode`**: 24 findings, first 10:
- `docs/stylesheets/extra.css:None` file docs/stylesheets/extra.css RatioCommentToCode is 0, which is below the minimum 0.1
- `src/scitools_hook/analysis/__init__.py:None` file src/scitools_hook/analysis/__init__.py RatioCommentToCode is 0, which is below the minimum 0.1
- `src/scitools_hook/analysis/lean/__init__.py:None` file src/scitools_hook/analysis/lean/__init__.py RatioCommentToCode is 0, which is below the minimum 0.1
- `src/scitools_hook/analysis/structure/__init__.py:None` file src/scitools_hook/analysis/structure/__init__.py RatioCommentToCode is 0, which is below the minimum 0.1
- `src/scitools_hook/cli/__init__.py:None` file src/scitools_hook/cli/__init__.py RatioCommentToCode is 0, which is below the minimum 0.1
- `src/scitools_hook/config/__init__.py:None` file src/scitools_hook/config/__init__.py RatioCommentToCode is 0, which is below the minimum 0.1
- `src/scitools_hook/git/__init__.py:None` file src/scitools_hook/git/__init__.py RatioCommentToCode is 0, which is below the minimum 0.1
- `src/scitools_hook/models/__init__.py:None` file src/scitools_hook/models/__init__.py RatioCommentToCode is 0.07, which is below the minimum 0.1
- `src/scitools_hook/report/__init__.py:None` file src/scitools_hook/report/__init__.py RatioCommentToCode is 0, which is below the minimum 0.1
- `src/scitools_hook/runner/__init__.py:None` file src/scitools_hook/runner/__init__.py RatioCommentToCode is 0, which is below the minimum 0.1

| Run | Wall | User CPU | Sys CPU | CPU% | Peak RSS | load 1m before -> after | Exit |
| --- | --- | --- | --- | --- | --- | --- | --- |
| whole project (lean-on) | 43.7 s | 42.5 s | 1.0 s | 99% | 575 MB | 1.38 -> 1.55 | 1 |

| Run | # | Phase | Time |
| --- | --- | --- | --- |
| whole project (lean-on) | 1 | `synchronising the after tree` | 0.0 s |
| whole project (lean-on) | 2 | `analysing the after database` | 0.0 s |
| whole project (lean-on) | 3 | `reading the after snapshot` | 15.8 s |


### The same run with both floors at zero (`resolution_floor = 0.0`, `accuracy_floor = 0.0`)

The seven rules the floors do not gate answered identically (same counts, same first ten; not repeated); the four gated rules are listed.


- **accuracy** (after side, from the JSON): {'after': 0.1910828025477707}
- **analysed files**: 319

| rule (lean-on-floors-zero) | count | note |
| --- | --- | --- |
| `structure.unused_parameter` | 68 |  |
| `structure.unused_class` | 0 |  |
| `structure.unused_variable` | 17 |  |
| `structure.pass_through` | 47 |  |
| `structure.single_implementation` | 0 |  |
| `structure.over_export` | 0 |  |
| `structure.duplicate_block` | 48 |  |
| `structure.similar_routine` | 163 |  |
| `routine.LinesPerStatement` | 57 |  |
| `routine.CountLineComment` | 23 |  |
| `file.RatioCommentToCode` | 24 |  |

**`structure.unused_parameter`**: 68 findings, first 10:
- `src/scitools_hook/analysis/structure/cycles.py:104` scitools_hook.analysis.structure.cycles._closing declares the parameter edges and never reads, sets or modifies it; no signature it overrides asks for it either
- `src/scitools_hook/analysis/structure/cycles.py:104` scitools_hook.analysis.structure.cycles._closing declares the parameter level and never reads, sets or modifies it; no signature it overrides asks for it either
- `src/scitools_hook/analysis/structure/cycles.py:100` scitools_hook.analysis.structure.cycles._closing declares the parameter edges and never reads, sets or modifies it; no signature it overrides asks for it either
- `src/scitools_hook/analysis/structure/cycles.py:100` scitools_hook.analysis.structure.cycles._closing declares the parameter level and never reads, sets or modifies it; no signature it overrides asks for it either
- `src/scitools_hook/understand/worker.py:166` scitools_hook.understand.worker._op_ping declares the parameter request and never reads, sets or modifies it; no signature it overrides asks for it either
- `tests/cli/test_baseline_command.py:78` test_baseline_command.test_the_answer_names_the_file_and_how_much_it_holds declares the parameter assembler and never reads, sets or modifies it; no signature it overrides asks for it either
- `tests/cli/test_baseline_command.py:102` test_baseline_command.test_the_wording_counts_more_than_one_limit_correctly.two declares the parameter path and never reads, sets or modifies it; no signature it overrides asks for it either
- `tests/cli/test_check_command.py:262` test_check_command.test_a_blocking_destination_still_names_output_when_that_is_what_was_given declares the parameter assembler and never reads, sets or modifies it; no signature it overrides asks for it either
- `tests/cli/test_check_command.py:407` test_check_command.test_a_clean_run_exits_zero declares the parameter assembler and never reads, sets or modifies it; no signature it overrides asks for it either
- `tests/cli/test_check_command.py:185` test_check_command.test_a_report_that_cannot_be_delivered_names_the_option_that_asked_for_it declares the parameter assembler and never reads, sets or modifies it; no signature it overrides asks for it either

**`structure.unused_class`**: 0 findings, first 0:

**`structure.unused_variable`**: 17 findings, first 10:
- `src/scitools_hook/cli/common.py:922` nothing in this project reads the module-level FormatOption that src/scitools_hook/cli/common.py binds
- `tests/cli/test_cli_app.py:97` nothing in this project reads the module-level CLI_SOURCE_DIR that tests/cli/test_cli_app.py binds
- `tests/cli/test_cli_app.py:99` nothing in this project reads the module-level PROMPT_CALLS that tests/cli/test_cli_app.py binds
- `tests/cli/test_cli_app.py:115` nothing in this project reads the module-level _ that tests/cli/test_cli_app.py binds
- `tests/config/test_loader.py:356` nothing in this project reads the module-level _ that tests/config/test_loader.py binds
- `tests/config/test_metric_names.py:66` nothing in this project reads the module-level _ that tests/config/test_metric_names.py binds
- `tests/contract/test_call_graph_contract.py:113` nothing in this project reads the module-level HOLDER_METHODS that tests/contract/test_call_graph_contract.py binds
- `tests/report/test_hints.py:285` nothing in this project reads the module-level _ that tests/report/test_hints.py binds
- `tests/report/test_markdown.py:47` nothing in this project reads the module-level ANALYSIS_NODE that tests/report/test_markdown.py binds
- `tests/runner/test_check_lean.py:1140` nothing in this project reads the module-level _ that tests/runner/test_check_lean.py binds

**`structure.pass_through`**: 47 findings, first 10:
- `.dagger/module/src/scitools_hook_ci/main.py:246` exactly one project routine calls scitools_hook_ci.main.ScitoolsHookCi.licensed_inventory, and its body does nothing but call scitools_hook_ci.main._app_env; the hop adds a name and no behaviour
- `src/scitools_hook/analysis/baseline.py:256` exactly one project routine calls scitools_hook.analysis.baseline._unknown_key_issues, and its body does nothing but call scitools_hook.analysis.baseline._unknown_key_message; the hop adds a name and no behaviour
- `src/scitools_hook/analysis/change_summary.py:262` exactly one project routine calls scitools_hook.analysis.change_summary._deltas_for, and its body does nothing but call scitools_hook.analysis.change_summary._dependency; the hop adds a name and no behaviour
- `src/scitools_hook/analysis/change_summary.py:316` exactly one project routine calls scitools_hook.analysis.change_summary._narrow, and its body does nothing but call scitools_hook.analysis.change_summary._only; the hop adds a name and no behaviour
- `src/scitools_hook/analysis/lean/duplicates.py:208` exactly one project routine calls scitools_hook.analysis.lean.duplicates._considered, and its body does nothing but call scitools_hook.config.models.matching_pattern; the hop adds a name and no behaviour
- `src/scitools_hook/analysis/lean/net.py:186` exactly one project routine calls scitools_hook.analysis.lean.net._movement, and its body does nothing but call scitools_hook.analysis.lean.net._count; the hop adds a name and no behaviour
- `src/scitools_hook/analysis/lean/similar.py:823` exactly one project routine calls scitools_hook.analysis.lean.similar._nested, and its body does nothing but call scitools_hook.analysis.lean.similar._inside; the hop adds a name and no behaviour
- `src/scitools_hook/analysis/lean/similar.py:734` exactly one project routine calls scitools_hook.analysis.lean.similar._unnested, and its body does nothing but call scitools_hook.analysis.lean.similar._supersedes; the hop adds a name and no behaviour
- `src/scitools_hook/analysis/population.py:110` exactly one project routine calls scitools_hook.analysis.population.filter_snapshot_keys, and its body does nothing but call scitools_hook.analysis.population.filter_keys; the hop adds a name and no behaviour
- `src/scitools_hook/analysis/ratchet.py:392` exactly one project routine calls scitools_hook.analysis.ratchet._a_decomposition_raised_it, and its body does nothing but call scitools_hook.analysis.ratchet._got_simpler; the hop adds a name and no behaviour

| Run | Wall | User CPU | Sys CPU | CPU% | Peak RSS | load 1m before -> after | Exit |
| --- | --- | --- | --- | --- | --- | --- | --- |
| whole project (lean-on-floors-zero) | 42.7 s | 41.6 s | 0.9 s | 99% | 576 MB | 1.55 -> 1.49 | 1 |

| Run | # | Phase | Time |
| --- | --- | --- | --- |
| whole project (lean-on-floors-zero) | 1 | `synchronising the after tree` | 0.0 s |
| whole project (lean-on-floors-zero) | 2 | `analysing the after database` | 0.0 s |
| whole project (lean-on-floors-zero) | 3 | `reading the after snapshot` | 15.1 s |

### What the first ten of each rule look like on this repository (read by the implementer)

Each verdict below names the finding by the line the table above gives it, so a reader can
disagree with the source open. "Genuine" means a reviewer would act on it as the hint says;
"same shape" means two routines the rule is right to call twins but which an author keeps
apart on purpose; "noise" means nothing an author would change.

- **`structure.similar_routine`, 163 at 0.9 / 6 statements / family 2 -- first ten, all in
  `src/`: 6 genuine, 4 same shape, 0 noise.** Genuine: `dead._routine_facts` /
  `_class_facts` / `layering._forwarder_facts` (0.99, three identical loops over a different
  item function), `dead._reported_class` / `_reported_binding` (0.93),
  `duplicates._unreadable_note` / `similar._unreadable_note` (1.00, a verbatim copy),
  `layering._dependants` / `coupling._targets_by_file` (1.00, the same loop with `src` and
  `dst` swapped), `config_cmd._reject_unusable` / `cache_files.present` (0.96, the same
  classify-absent-unusable ladder raising a different error) and
  `models._translate_path_pattern` / `shadow._translate` (1.00, a copy the block rule also
  reports). Same shape: `dead.find_unused_classes` / `find_unused_variables` (0.93, two
  rules that read different facts through one records-facts-gate skeleton),
  `dead._routine_fact` / `layering._base_fact` (0.94, three refusals over different fields),
  `common._list_lines` / `_block_lines` (0.93) and `loader._read_toml` / `_env_value` (0.91,
  the "outcome is the contract" wrapper written twice on purpose, as its comment says).
  The count is 163 findings over the whole project, of which the first ten are the
  `src/` ones the ordering puts first; the test tree, where the bulk sits, was not read here.
- **`structure.duplicate_block`, 48 at 12 lines -- first ten: 8 genuine, 2 noise.** Every
  block is reported from both ends, so 48 findings are about 24 places. Genuine:
  `config/models.py:474-492` / `git/shadow.py:352-370` (the `_translate` copy, 19 lines),
  `tests/analysis/test_change_summary.py:58-73` / `test_ratchet.py:40-55` (the same two
  fixtures and two keys in two modules), `test_ratchet.py:453` / `:478` (13 lines twice in
  one file) and `tests/cli/test_cli_app.py:595` / `:709` / `:849` (13 lines three times in
  one file). Noise: `models/findings.py:47-66` against `tests/models/test_findings.py:78`,
  which is the rule-name list and the test that enumerates it on purpose so the two must be
  edited together. The `also_at` shape is the one task 6.2 pinned: the three locations named
  are three windows of the one other copy (`:352`, `:353`, `:354`) and the "and 5 more" are
  more windows of it, so a reader sees one copy reported as eight places.
- **`structure.pass_through`, 47 with the floors at zero -- first ten: 1 genuine, 9 noise.**
  Genuine: `population.filter_snapshot_keys`, whose body is
  `return filter_keys(snapshot.entities, ignore)`. The other nine are bodies that hold one
  *project* call and something else the rule does not count: a comprehension
  (`baseline._unknown_key_issues`, `change_summary._deltas_for`, `duplicates._considered`,
  `similar._unnested`), an expression around the call (`net._movement` is a subtraction of
  two calls, `ratchet._a_decomposition_raised_it` an `and`, `similar._nested` a boolean),
  a constructor call taking the result (`change_summary._narrow` builds an `EntityDelta`
  around two `_only` calls), or a chain of non-project calls (`scitools_hook_ci.main
  .licensed_inventory` is `_app_env(source).with_exec(...).stdout()`, a dagger entry point).
  So the rule's "body consists of a single call to one other project routine" is being met by
  "one project call edge and at most two statements", which a one-line comprehension or
  expression satisfies while forwarding nothing. That is the predicate task 6.4 has to look
  at; the number of statements is not what separates these ten.
- **`structure.unused_parameter`, 68 with the floors at zero -- first ten: 0 genuine, 10
  noise.** Four are the two `@overload` stubs of `analysis/structure/cycles.py::_closing`
  (lines 100 and 104, `edges` and `level` each), whose bodies are `...` and read nothing by
  construction. One is `understand/worker.py::_op_ping(request)`, declared for the
  dispatch-table signature every `_op_*` shares. Five are test functions and one nested
  helper declaring a pytest fixture they use only for its effect (`assembler`, `path`). The
  shipped ignore list covers `self`/`cls`, a leading underscore and `*args`/`**kwargs`; it
  does not cover a routine matching the `test_` pattern, an `@overload` stub, or a
  `Protocol` method, and those three shapes are what the first ten are made of.
- **`structure.unused_variable`, 17 with the floors at zero -- first ten: 5 genuine, 5
  noise.** Genuine: `cli/common.py::FormatOption` (a second `FormatOption` is defined in
  `cli/check.py` and `cli/explain.py` and used there; this one nothing reads),
  `tests/cli/test_cli_app.py::CLI_SOURCE_DIR` and `PROMPT_CALLS` (copies of
  `test_cli_inventory.py`'s, which are used there and not here),
  `tests/contract/test_call_graph_contract.py::HOLDER_METHODS` and
  `tests/report/test_markdown.py::ANALYSIS_NODE` (named nowhere else). Noise: five
  module-level `_` bindings, the deliberate-discard spelling, which the variable ignore list
  (`__dunder__`, `log`, `logger`, `pytestmark`) does not cover though the parameter list's
  `^_` would.
- **`structure.unused_class`: 0 with the floors at zero** over 268 classes; every class of
  `src/` is named from a test, and `Test*` is excused. **`structure.single_implementation`:
  0. `structure.over_export`: 0** over 319 files. None of the three printed an unavailable
  note, so these are answers, not refusals; whether 0 over-exports is right was not
  independently checked here.
- **`routine.LinesPerStatement` 57, `routine.CountLineComment` 23, `file.RatioCommentToCode`
  24** (the last is the shipped *minimum* of 0.1; no maximum ships). The verbosity count is
  the 53 outside at 3.0 that `recommend` measured on 2026-09-10 plus four, on a tree that has
  grown since; the comment-line count is 23 against that day's 20.

## facdrone: 945 analysed files, accuracy 25.9%, measured read-only

### Every lean rule on at the shipped numbers and floors


- **accuracy** (after side, from the JSON): {'after': 0.25925925925925924}
- **analysed files**: 945

| rule (lean-on) | count | note |
| --- | --- | --- |
| `structure.unused_parameter` | 0 | Understand parsed 26% of this analysis without an error, below the accuracy floor of 75%; a name whose use sites sit in a region the analysis errored on reads as unused while it is read |
| `structure.unused_class` | 0 | Understand parsed 26% of this analysis without an error, below the accuracy floor of 75%; a name whose use sites sit in a region the analysis errored on reads as unused while it is read |
| `structure.unused_variable` | 0 | Understand parsed 26% of this analysis without an error, below the accuracy floor of 75%; a name whose use sites sit in a region the analysis errored on reads as unused while it is read |
| `structure.pass_through` | 0 | Understand parsed 26% of this analysis without an error, below the accuracy floor of 75%; a name whose use sites sit in a region the analysis errored on reads as unused while it is read |
| `structure.single_implementation` | 0 |  |
| `structure.over_export` | 1 |  |
| `structure.duplicate_block` | 152 |  |
| `structure.similar_routine` | 129 |  |
| `routine.LinesPerStatement` | 442 |  |
| `routine.CountLineComment` | 11 |  |
| `file.RatioCommentToCode` | 227 |  |

**`structure.unused_parameter`**: 0 findings, first 0:

**`structure.unused_class`**: 0 findings, first 0:

**`structure.unused_variable`**: 0 findings, first 0:

**`structure.pass_through`**: 0 findings, first 0:

**`structure.single_implementation`**: 0 findings, first 0:

**`structure.over_export`**: 1 findings, first 1:
- `src/facdrone/shells/cli/panel_coverage.py:None` src/facdrone/shells/cli/panel_coverage.py defines one routine or class and nothing else, and src/facdrone/shells/cli/download_root.py is the only file in this project that depends on it

**`structure.duplicate_block`**: 152 findings, first 10:
- `packages/facdrone-client/facdrone_client/ctrader/__init__.py:19` packages/facdrone-client/facdrone_client/ctrader/__init__.py:19-30 repeats 12 code lines this project also holds at packages/facdrone-client/facdrone_client/ctrader/session.py:132
- `packages/facdrone-client/facdrone_client/ctrader/root.py:166` packages/facdrone-client/facdrone_client/ctrader/root.py:166-181 repeats 14 code lines this project also holds at packages/facdrone-client/facdrone_client/ctrader/root.py:216, packages/facdrone-client/facdrone_client/ctrader/root.py:217, packages/facdrone-client/facdrone_client/ctrader/root.py:218
- `packages/facdrone-client/facdrone_client/ctrader/root.py:216` packages/facdrone-client/facdrone_client/ctrader/root.py:216-229 repeats 14 code lines this project also holds at packages/facdrone-client/facdrone_client/ctrader/root.py:166, packages/facdrone-client/facdrone_client/ctrader/root.py:167, packages/facdrone-client/facdrone_client/ctrader/root.py:168
- `packages/facdrone-client/facdrone_client/ctrader/session.py:132` packages/facdrone-client/facdrone_client/ctrader/session.py:132-143 repeats 12 code lines this project also holds at packages/facdrone-client/facdrone_client/ctrader/__init__.py:19
- `packages/facdrone-client/facdrone_client/instruments/__init__.py:28` packages/facdrone-client/facdrone_client/instruments/__init__.py:28-39 repeats 12 code lines this project also holds at tests/unit/test_volume_arithmetic.py:12
- `packages/facdrone-wire/facdrone_wire/__init__.py:30` packages/facdrone-wire/facdrone_wire/__init__.py:30-45 repeats 16 code lines this project also holds at packages/facdrone-wire/facdrone_wire/contract.py:293, packages/facdrone-wire/facdrone_wire/contract.py:294, packages/facdrone-wire/facdrone_wire/contract.py:295, and 2 more
- `packages/facdrone-wire/facdrone_wire/contract.py:293` packages/facdrone-wire/facdrone_wire/contract.py:293-308 repeats 16 code lines this project also holds at packages/facdrone-wire/facdrone_wire/__init__.py:30, packages/facdrone-wire/facdrone_wire/__init__.py:31, packages/facdrone-wire/facdrone_wire/__init__.py:32, and 2 more
- `scripts/diag_q1.py:31` scripts/diag_q1.py:31-45 repeats 15 code lines this project also holds at scripts/measure_fresh_year.py:72, scripts/measure_fresh_year.py:73, scripts/measure_fresh_year.py:74, and 1 more
- `scripts/equity_census.py:49` scripts/equity_census.py:49-62 repeats 12 code lines this project also holds at scripts/equity_pod_census.py:71
- `scripts/equity_census.py:108` scripts/equity_census.py:108-119 repeats 12 code lines this project also holds at scripts/equity_pod_census.py:109

**`structure.similar_routine`**: 129 findings, first 10:
- `packages/facdrone-client/facdrone_client/instruments/map.py:128` facdrone_client.instruments.map._row_from_dict (packages/facdrone-client/facdrone_client/instruments/map.py:128) is 1 of 2 routines this project holds in near-identical form, the weakest pair matching at 0.90; the others are facdrone_client.instruments.universe._row_from_dict (packages/facdrone-client/facdrone_client/instruments/universe.py:55)
- `packages/facdrone-client/facdrone_client/transport.py:172` facdrone_client.transport._validate (packages/facdrone-client/facdrone_client/transport.py:172) is 1 of 3 routines this project holds in near-identical form, the weakest pair matching at 0.92; the others are facdrone.library.reporting.serialization._parse_enum (src/facdrone/library/reporting/serialization.py:285), facdrone.library.reporting.serialization._parse_day (src/facdrone/library/reporting/serialization.py:424)
- `packages/facdrone-observe/facdrone_observe/collector.py:66` facdrone_observe.collector.from_env (packages/facdrone-observe/facdrone_observe/collector.py:66) is 1 of 2 routines this project holds in near-identical form, the weakest pair matching at 0.94; the others are facdrone_observe.openobserve.openobserve_from_env (packages/facdrone-observe/facdrone_observe/openobserve.py:52)
- `research/h_and_failure_decomposition.py:59` h_and_failure_decomposition._replay_module (research/h_and_failure_decomposition.py:59) is 1 of 4 routines this project holds in near-identical form, the weakest pair matching at 0.92; the others are h_and_replay._reference (research/h_and_replay.py:161), equity_census._replay_module (scripts/equity_census.py:53), equity_pod_census._replay_module (scripts/equity_pod_census.py:75)
- `research/h_and_replay.py:73` h_and_replay._api_key (research/h_and_replay.py:73) is 1 of 2 routines this project holds in near-identical form, the weakest pair matching at 1.00; the others are equity_coverage_probe._api_key (scripts/equity_coverage_probe.py:49)
- `scripts/equity_census.py:82` equity_census.main (scripts/equity_census.py:82) is 1 of 2 routines this project holds in near-identical form, the weakest pair matching at 0.94; the others are equity_pod_census.main (scripts/equity_pod_census.py:87)
- `scripts/measure_trade_parity.py:92` measure_trade_parity.load_targets (scripts/measure_trade_parity.py:92) is 1 of 2 routines this project holds in near-identical form, the weakest pair matching at 1.00; the others are measure_trade_parity.load_intended (scripts/measure_trade_parity.py:109)
- `src/facdrone/excel/functions.py:42` facdrone.excel.functions.fd_trace (src/facdrone/excel/functions.py:42) is 1 of 2 routines this project holds in near-identical form, the weakest pair matching at 1.00; the others are facdrone.excel.functions.fd_factors (src/facdrone/excel/functions.py:59)
- `src/facdrone/factors/vix_credit.py:85` facdrone.factors.vix_credit._asset_vix_score (src/facdrone/factors/vix_credit.py:85) is 1 of 2 routines this project holds in near-identical form, the weakest pair matching at 1.00; the others are facdrone.library.models.jpy_carry.asset_carry_score (src/facdrone/library/models/jpy_carry.py:129)
- `src/facdrone/library/models/vqvae_features.py:69` facdrone.library.models.vqvae_features.ridge_forward_score (src/facdrone/library/models/vqvae_features.py:69) is 1 of 2 routines this project holds in near-identical form, the weakest pair matching at 0.96; the others are tests.unit.test_vqvae._v1_inline (tests/unit/test_vqvae.py:30)

**`routine.LinesPerStatement`**: 442 findings, first 10:
- `packages/facdrone-client/facdrone_client/ctrader/broker.py:428` routine facdrone_client.ctrader.broker.CtraderBroker._execution_result LinesPerStatement is 3.14286, which exceeds the maximum 3
- `packages/facdrone-client/facdrone_client/ctrader/broker.py:478` routine facdrone_client.ctrader.broker.CtraderBroker._narrate LinesPerStatement is 3.5, which exceeds the maximum 3
- `packages/facdrone-client/facdrone_client/ctrader/broker.py:119` routine facdrone_client.ctrader.broker._narrate_stops LinesPerStatement is 3.83333, which exceeds the maximum 3
- `packages/facdrone-client/facdrone_client/ctrader/cycle_stage.py:57` routine facdrone_client.ctrader.cycle_stage.run_heartbeat_stage LinesPerStatement is 4.6, which exceeds the maximum 3
- `packages/facdrone-client/facdrone_client/ctrader/fake.py:99` routine facdrone_client.ctrader.fake.FakeSession.place_pending LinesPerStatement is 3.71429, which exceeds the maximum 3
- `packages/facdrone-client/facdrone_client/ctrader/fake.py:186` routine facdrone_client.ctrader.fake.ctrader_from_scenario LinesPerStatement is 4.81818, which exceeds the maximum 3
- `packages/facdrone-client/facdrone_client/ctrader/mcp.py:166` routine facdrone_client.ctrader.mcp.McpReadOnlySession._position LinesPerStatement is 3.8, which exceeds the maximum 3
- `packages/facdrone-client/facdrone_client/ctrader/mcp.py:109` routine facdrone_client.ctrader.mcp.McpReadOnlySession.deals LinesPerStatement is 3.5, which exceeds the maximum 3
- `packages/facdrone-client/facdrone_client/cycle.py:285` routine facdrone_client.cycle.ClientCycle._narrate LinesPerStatement is 4.5, which exceeds the maximum 3
- `packages/facdrone-client/facdrone_client/cycle.py:388` routine facdrone_client.cycle.ClientCycle._price_gate LinesPerStatement is 3.07143, which exceeds the maximum 3

**`routine.CountLineComment`**: 11 findings, first 10:
- `scripts/capture_decision_goldens.py:74` routine capture_decision_goldens._install_capture CountLineComment is 22, which exceeds the maximum 20
- `src/facdrone/engine/stages/select.py:375` routine facdrone.engine.stages.select.clayton_select_with_evidence CountLineComment is 21, which exceeds the maximum 20
- `src/facdrone/library/market_state/transitions.py:323` routine facdrone.library.market_state.transitions.step_transition CountLineComment is 28, which exceeds the maximum 20
- `src/facdrone/library/models/identity.py:128` routine facdrone.library.models.identity.single_identity_posterior CountLineComment is 27, which exceeds the maximum 20
- `src/facdrone/library/models/score_filter.py:90` routine facdrone.library.models.score_filter.assess_risk CountLineComment is 22, which exceeds the maximum 20
- `src/facdrone/library/models/vqvae_features.py:147` routine facdrone.library.models.vqvae_features.compute_features_from_df CountLineComment is 43, which exceeds the maximum 20
- `src/facdrone/shells/cli/config_overview.py:20` routine facdrone.shells.cli.config_overview.turbulence_declaration CountLineComment is 26, which exceeds the maximum 20
- `src/facdrone/shells/decision_core.py:919` routine facdrone.shells.decision_core.finalize_decision CountLineComment is 25, which exceeds the maximum 20
- `src/facdrone/shells/sim/alpha_policy.py:896` routine facdrone.shells.sim.alpha_policy.AlphaDecisionPolicy._size CountLineComment is 22, which exceeds the maximum 20
- `src/facdrone/shells/sim/alpha_policy.py:445` routine facdrone.shells.sim.alpha_policy.AlphaDecisionPolicy.decide CountLineComment is 43, which exceeds the maximum 20

**`file.RatioCommentToCode`**: 227 findings, first 10:
- `packages/facdrone-client/facdrone_client/__init__.py:None` file packages/facdrone-client/facdrone_client/__init__.py RatioCommentToCode is 0.07, which is below the minimum 0.1
- `packages/facdrone-client/facdrone_client/ctrader/check_stage.py:None` file packages/facdrone-client/facdrone_client/ctrader/check_stage.py RatioCommentToCode is 0.06, which is below the minimum 0.1
- `packages/facdrone-client/facdrone_client/ctrader/fake.py:None` file packages/facdrone-client/facdrone_client/ctrader/fake.py RatioCommentToCode is 0.06, which is below the minimum 0.1
- `packages/facdrone-client/facdrone_client/fake.py:None` file packages/facdrone-client/facdrone_client/fake.py RatioCommentToCode is 0.09, which is below the minimum 0.1
- `packages/facdrone-client/facdrone_client/instruments/__init__.py:None` file packages/facdrone-client/facdrone_client/instruments/__init__.py RatioCommentToCode is 0.05, which is below the minimum 0.1
- `packages/facdrone-wire/facdrone_wire/__init__.py:None` file packages/facdrone-wire/facdrone_wire/__init__.py RatioCommentToCode is 0.02, which is below the minimum 0.1
- `scripts/seed_factor_definitions.py:None` file scripts/seed_factor_definitions.py RatioCommentToCode is 0.07, which is below the minimum 0.1
- `src/facdrone/datasets/__init__.py:None` file src/facdrone/datasets/__init__.py RatioCommentToCode is 0, which is below the minimum 0.1
- `src/facdrone/domain/__init__.py:None` file src/facdrone/domain/__init__.py RatioCommentToCode is 0, which is below the minimum 0.1
- `src/facdrone/engine/__init__.py:None` file src/facdrone/engine/__init__.py RatioCommentToCode is 0, which is below the minimum 0.1

| Run | Wall | User CPU | Sys CPU | CPU% | Peak RSS | load 1m before -> after | Exit |
| --- | --- | --- | --- | --- | --- | --- | --- |
| whole project (lean-on) | 85.7 s | 83.7 s | 1.8 s | 99% | 1069 MB | 1.26 -> 1.49 | 1 |

| Run | # | Phase | Time |
| --- | --- | --- | --- |
| whole project (lean-on) | 1 | `synchronising the after tree` | 0.1 s |
| whole project (lean-on) | 2 | `analysing the after database` | 0.0 s |
| whole project (lean-on) | 3 | `reading the after snapshot` | 29.2 s |


### The same run with both floors at zero (`resolution_floor = 0.0`, `accuracy_floor = 0.0`)

The seven rules the floors do not gate answered identically (same counts, same first ten; not repeated); the four gated rules are listed.


- **accuracy** (after side, from the JSON): {'after': 0.25925925925925924}
- **analysed files**: 945

| rule (lean-on-floors-zero) | count | note |
| --- | --- | --- |
| `structure.unused_parameter` | 58 |  |
| `structure.unused_class` | 18 |  |
| `structure.unused_variable` | 55 |  |
| `structure.pass_through` | 59 |  |
| `structure.single_implementation` | 0 |  |
| `structure.over_export` | 1 |  |
| `structure.duplicate_block` | 152 |  |
| `structure.similar_routine` | 129 |  |
| `routine.LinesPerStatement` | 442 |  |
| `routine.CountLineComment` | 11 |  |
| `file.RatioCommentToCode` | 227 |  |

**`structure.unused_parameter`**: 58 findings, first 10:
- `src/facdrone/library/models/ssr.py:99` facdrone.library.models.ssr.compute_ssr declares the parameter benchmark_sr and never reads, sets or modifies it; no signature it overrides asks for it either
- `src/facdrone/library/reporting/live_release.py:50` facdrone.library.reporting.live_release.LiveSnapshotStore.snapshots_for declares the parameter limit and never reads, sets or modifies it; no signature it overrides asks for it either
- `src/facdrone/pipelines/factors/combine.py:301` facdrone.pipelines.factors.combine.legacy_inverse_corr declares the parameter config and never reads, sets or modifies it; no signature it overrides asks for it either
- `src/facdrone/shells/providers/ecb_reporting_source.py:438` facdrone.shells.providers.ecb_reporting_source._parse_optional_aware_datetime declares the parameter value and never reads, sets or modifies it; no signature it overrides asks for it either
- `src/facdrone/shells/providers/ecb_reporting_source.py:438` facdrone.shells.providers.ecb_reporting_source._parse_optional_aware_datetime declares the parameter fallback and never reads, sets or modifies it; no signature it overrides asks for it either
- `src/facdrone/shells/providers/ecb_reporting_source.py:438` facdrone.shells.providers.ecb_reporting_source._parse_optional_aware_datetime declares the parameter evidence_id and never reads, sets or modifies it; no signature it overrides asks for it either
- `src/facdrone/shells/providers/ecb_reporting_source.py:438` facdrone.shells.providers.ecb_reporting_source._parse_optional_aware_datetime declares the parameter row_number and never reads, sets or modifies it; no signature it overrides asks for it either
- `src/facdrone/shells/providers/ecb_reporting_source.py:428` facdrone.shells.providers.ecb_reporting_source._parse_optional_aware_datetime declares the parameter value and never reads, sets or modifies it; no signature it overrides asks for it either
- `src/facdrone/shells/providers/ecb_reporting_source.py:428` facdrone.shells.providers.ecb_reporting_source._parse_optional_aware_datetime declares the parameter fallback and never reads, sets or modifies it; no signature it overrides asks for it either
- `src/facdrone/shells/providers/ecb_reporting_source.py:428` facdrone.shells.providers.ecb_reporting_source._parse_optional_aware_datetime declares the parameter evidence_id and never reads, sets or modifies it; no signature it overrides asks for it either

**`structure.unused_class`**: 18 findings, first 10:
- `packages/facdrone-client/facdrone_client/fmp_quote.py:36` nothing in this project names the class facdrone_client.fmp_quote.FmpQuoteReader; every reference to it is either absent or outside the analysis root
- `packages/facdrone-client/facdrone_client/waiter.py:26` nothing in this project names the class facdrone_client.waiter.Waiter; every reference to it is either absent or outside the analysis root
- `src/facdrone/engine/stages/select.py:56` nothing in this project names the class facdrone.engine.stages.select.SelectionModel; every reference to it is either absent or outside the analysis root
- `src/facdrone/library/control_stack.py:116` nothing in this project names the class facdrone.library.control_stack.PortfolioControlInputs; every reference to it is either absent or outside the analysis root
- `src/facdrone/library/control_stack.py:135` nothing in this project names the class facdrone.library.control_stack.SymbolControlInputs; every reference to it is either absent or outside the analysis root
- `src/facdrone/library/models/kalman.py:46` nothing in this project names the class facdrone.library.models.kalman.KalmanTrendFilter; every reference to it is either absent or outside the analysis root
- `src/facdrone/library/reporting/calculations/portfolio_lab_feasibility.py:135` nothing in this project names the class facdrone.library.reporting.calculations.portfolio_lab_feasibility.PortfolioRuleValidator; every reference to it is either absent or outside the analysis root
- `src/facdrone/library/reporting/live_release.py:42` nothing in this project names the class facdrone.library.reporting.live_release.LiveSnapshotStore; every reference to it is either absent or outside the analysis root
- `src/facdrone/pipelines/factors/identity_collapse.py:154` nothing in this project names the class facdrone.pipelines.factors.identity_collapse.SignalHistoryMomentsSource; every reference to it is either absent or outside the analysis root
- `src/facdrone/pipelines/reporting/run_report.py:54` nothing in this project names the class facdrone.pipelines.reporting.run_report.RunReportBuilder; every reference to it is either absent or outside the analysis root

**`structure.unused_variable`**: 55 findings, first 10:
- `packages/facdrone-observe/facdrone_observe/openobserve.py:25` nothing in this project reads the module-level KEYS that packages/facdrone-observe/facdrone_observe/openobserve.py binds
- `packages/facdrone-observe/facdrone_observe/vocabulary.py:33` nothing in this project reads the module-level SEVERITIES that packages/facdrone-observe/facdrone_observe/vocabulary.py binds
- `research/h_and_replay.py:64` nothing in this project reads the module-level _SAMPLE_EVERY that research/h_and_replay.py binds
- `src/facdrone/engine/journal.py:25` nothing in this project reads the module-level CRISIS_ONSET that src/facdrone/engine/journal.py binds
- `src/facdrone/engine/journal.py:26` nothing in this project reads the module-level CRISIS_END that src/facdrone/engine/journal.py binds
- `src/facdrone/library/control_stack.py:40` nothing in this project reads the module-level _MAX_LEVERAGE_STEPS that src/facdrone/library/control_stack.py binds
- `src/facdrone/library/freshness/publication_rules.py:229` nothing in this project reads the module-level StateName that src/facdrone/library/freshness/publication_rules.py binds
- `src/facdrone/library/numerics.py:13` nothing in this project reads the module-level STD_FLOOR that src/facdrone/library/numerics.py binds
- `src/facdrone/library/reporting/artifact_schemas.py:964` nothing in this project reads the module-level EXTENSION_CSV_HEADERS that src/facdrone/library/reporting/artifact_schemas.py binds
- `src/facdrone/library/reporting/calculations/chaos.py:31` nothing in this project reads the module-level SIGMA_BUCKETS that src/facdrone/library/reporting/calculations/chaos.py binds

**`structure.pass_through`**: 59 findings, first 10:
- `.dagger/module/src/facdrone_ci/main.py:185` exactly one project routine calls facdrone_ci.main.FacdroneCi.build_image, and its body does nothing but call facdrone_ci.main._app_env; the hop adds a name and no behaviour
- `packages/facdrone-client/facdrone_client/instruments/map.py:64` exactly one project routine calls facdrone_client.instruments.map.InstrumentMap.by_broker_name, and its body does nothing but call facdrone_client.instruments.map.InstrumentMap.mapped; the hop adds a name and no behaviour
- `packages/facdrone-client/facdrone_client/mt5/__main__.py:62` exactly one project routine calls facdrone_client.mt5.__main__.Turn.exit_code-getter, and its body does nothing but call facdrone_client.mt5.__main__.Turn.exit_code-getter; the hop adds a name and no behaviour
- `packages/facdrone-client/facdrone_client/resting_ledger.py:37` exactly one project routine calls facdrone_client.resting_ledger.RestingLedger.cancelled, and its body does nothing but call facdrone_client.resting_ledger.RestingLedger._append; the hop adds a name and no behaviour
- `packages/facdrone-client/facdrone_client/resting_ledger.py:21` exactly one project routine calls facdrone_client.resting_ledger.RestingLedger.placed, and its body does nothing but call facdrone_client.resting_ledger.RestingLedger._append; the hop adds a name and no behaviour
- `src/facdrone/factors/composition.py:127` exactly one project routine calls facdrone.factors.composition.ComposedFactor.compute, and its body does nothing but call facdrone.factors.composition.ComposedFactor.evaluate; the hop adds a name and no behaviour
- `src/facdrone/library/models/jpy_carry.py:49` exactly one project routine calls facdrone.library.models.jpy_carry.simple_returns, and its body does nothing but call facdrone.library.models.jpy_carry.return_series; the hop adds a name and no behaviour
- `src/facdrone/library/reporting/calculations/chaos.py:181` exactly one project routine calls facdrone.library.reporting.calculations.chaos.chaos_metric_results._emit, and its body does nothing but call facdrone.library.reporting.model.Availability.available; the hop adds a name and no behaviour
- `src/facdrone/library/reporting/calculations/factor_comparison.py:176` exactly one project routine calls facdrone.library.reporting.calculations.factor_comparison.compare_market_and_basket.metric, and its body does nothing but call facdrone.library.reporting.model.Availability.available; the hop adds a name and no behaviour
- `src/facdrone/library/reporting/calculations/factor_contributions.py:114` exactly one project routine calls facdrone.library.reporting.calculations.factor_contributions.attach_factor_contributions.metric, and its body does nothing but call facdrone.library.reporting.model.Availability.available; the hop adds a name and no behaviour

| Run | Wall | User CPU | Sys CPU | CPU% | Peak RSS | load 1m before -> after | Exit |
| --- | --- | --- | --- | --- | --- | --- | --- |
| whole project (lean-on-floors-zero) | 87.4 s | 85.6 s | 1.8 s | 99% | 1070 MB | 1.49 -> 1.18 | 1 |

| Run | # | Phase | Time |
| --- | --- | --- | --- |
| whole project (lean-on-floors-zero) | 1 | `synchronising the after tree` | 0.1 s |
| whole project (lean-on-floors-zero) | 2 | `analysing the after database` | 0.0 s |
| whole project (lean-on-floors-zero) | 3 | `reading the after snapshot` | 30.0 s |

### What the first ten of each rule look like on facdrone (read by the implementer, read-only)

Same vocabulary as above. facdrone's sources were read and nothing in that repository was
written; the "other files naming it" checks below are `git grep -w` over its tracked `*.py`.

- **`structure.similar_routine`, 129 -- first ten: 7 genuine, 2 same shape, 1 by design.**
  Genuine: `facdrone_observe.collector.from_env` / `openobserve.openobserve_from_env` (0.94,
  the same function in two packages with the key names changed), the `_replay_module` family
  of four across `research/` and `scripts/` (0.92, a copied loader), `h_and_replay._api_key`
  / `equity_coverage_probe._api_key` (1.00), `equity_census.main` / `equity_pod_census.main`
  (0.94, a copied script), `measure_trade_parity.load_targets` / `load_intended` (1.00, one
  line differs), `excel.functions.fd_trace` / `fd_factors` (1.00, the route and the key
  differ) and `vix_credit._asset_vix_score` / `jpy_carry.asset_carry_score` (1.00, a sign
  differs). Same shape: `instruments.map._row_from_dict` / `universe._row_from_dict` (0.90,
  two row types built field by field) and `transport._validate` / `serialization._parse_enum`
  (0.92, an isinstance guard and a re-raise). By design: `vqvae_features.ridge_forward_score`
  against `tests/unit/test_vqvae.py::_v1_inline` (0.96), whose docstring calls itself an
  "independent transcription" pinning the port -- a twin the author wants kept.
- **`structure.duplicate_block`, 152 -- first ten: 5 genuine, 5 noise.** Genuine:
  `ctrader/root.py:166-181` / `:216-229` (the same fourteen-line `except` ladder twice in one
  file), `scripts/diag_q1.py:31-45` / `measure_fresh_year.py:72` (a `SimRunSpec` built the
  same way), `scripts/equity_census.py:49-62` and `:108-119` against `equity_pod_census.py`
  (a copied script, twice). Noise: `ctrader/__init__.py:19-30` / `session.py:132` and
  `facdrone_wire/__init__.py:30-45` / `contract.py:293` (an `__all__` list and the package
  initialiser that repeats it, from both ends) and `instruments/__init__.py:28-39` /
  `tests/unit/test_volume_arithmetic.py:12` (the same twelve-name import block). To the
  lexer a name list is twelve code lines like any other; an `__all__` or an import block of
  twelve or more names is a block finding wherever a package re-exports what a module
  declares. That shape did not occur on this repository's first ten and is what the second
  repository was for.
- **`structure.over_export`, 1: `src/facdrone/shells/cli/panel_coverage.py`**, one routine,
  imported by `download_root.py` alone -- exactly the predicate. The module's docstring argues
  for its separation ("this is the only thing here that touches the database, and it decides
  nothing"), so it is the rule's finding and an author's decision at once; a reader can judge
  which wins. **`structure.single_implementation`: 0** over 1809 classes, as on this
  repository; two repositories at 0 is worth a look in 6.4 before the rule ships enabled.
- **`structure.unused_class`, 18 with the floors at zero -- first ten: 8 named in no other
  source file, 2 named in one.** `FmpQuoteReader`, `Waiter`, `SelectionModel`,
  `SymbolControlInputs`, `KalmanTrendFilter`, `PortfolioRuleValidator`, `LiveSnapshotStore`
  and `SignalHistoryMomentsSource` appear in their own module and, for some, a `.kiro`
  design document, and nowhere else in the tracked Python. `RunReportBuilder` is re-exported
  by `pipelines/reporting/__init__.py` and used nowhere; `PortfolioControlInputs` is named in
  `library/reporting/live_limits.py`, which is a use the 26% analysis did not resolve or an
  annotation, and was not read further. So the class rule at 26% is mostly right on this
  repository, against 0 findings on this one at 19%.
- **`structure.unused_variable`, 55 with the floors at zero -- first ten: 3 named nowhere
  else, 4 with a same-file mention, 3 named in other files.** `_SAMPLE_EVERY`,
  `_MAX_LEVERAGE_STEPS` and `EXTENSION_CSV_HEADERS` are bound and never mentioned again.
  `KEYS`, `SEVERITIES`, `StateName` and `SIGMA_BUCKETS` are mentioned once more in their own
  file. `CRISIS_ONSET`, `CRISIS_END` and `STD_FLOOR` are named in four to six other files --
  which, on a repository whose own configuration records `_HORIZON_DAYS` copied into fifteen
  factor modules, may be copies of the name rather than reads of this binding; not resolved
  here. The honest summary is 3 certain of 10 and 7 that need the file open, which is the
  rule the accuracy floor exists for.
- **`structure.unused_parameter`, 58 with the floors at zero -- first ten: 2 genuine, 8
  noise.** Genuine: `ssr.compute_ssr(benchmark_sr)` and
  `combine.legacy_inverse_corr(config)`, both already carrying `# noqa: ARG001` -- the author
  knows, and the rule agrees with ruff. Noise: seven parameters of the two `@overload` stubs
  of `ecb_reporting_source._parse_optional_aware_datetime` (lines 428 and 438) and
  `LiveSnapshotStore.snapshots_for(limit)`, a `Protocol` method whose body is `...`. The same
  two shapes as on this repository, from a different codebase: a stub body reads nothing.
- **`structure.pass_through`, 59 with the floors at zero -- first ten: 2 genuine, 8 noise.**
  Genuine: `ComposedFactor.compute`, which is `return self.evaluate(view).cross_section`,
  and `mt5.__main__.Turn.exit_code`, a property forwarding to the module-level
  `exit_code(self.report)` -- though the finding names the callee as the property itself
  (`Turn.exit_code-getter ... does nothing but call Turn.exit_code-getter`), a resolution
  artefact worth knowing about. Noise, for the reason given for this repository:
  `InstrumentMap.by_broker_name` is a `next(...)` over a generator; `RestingLedger.placed` and
  `cancelled` build an event dict and append it; `jpy_carry.simple_returns` converts the
  result; the nested `_emit` and two `metric` helpers construct a `MetricResult`; the dagger
  `build_image` chains non-project calls. Two repositories, twenty findings, three
  forwarders: the predicate, not the number, is what 6.4 has to change.
- **`routine.LinesPerStatement` 442** -- the same 442 the 2026-09-10 `recommend` run found
  outside 3.0 (8.7% of 5084 judged), so the two methods agree. **`routine.CountLineComment`
  11**, consistent with that run's `keep 20`. **`file.RatioCommentToCode` 227** at the
  shipped minimum. **Accuracy 25.9%**, the 26% recorded on 2026-09-10.

### Where facdrone agrees with 2026-09-10, and what the run found on the way

- **Agrees.** `routine.LinesPerStatement` outside 3.0: 442 on both days (the 8.7% of 5084
  judged). `routine.CountLineComment` 11 outside 20, consistent with that day's `keep`.
  Accuracy 25.9% against the 26% recorded. Files 945 (that day's count; task 6.3's brief says
  417 source files, which is the Python subset). So the shrink defaults' picture for 6.4 is
  unchanged: 3.0 does not fit facdrone, 20 fits both.
- **New: the two token rules at their shipped numbers on a second repository.** 152 block
  findings (about 76 places, both ends) and 129 families on 945 files, against 48 and 163 on
  319 files here. Families do not scale with size -- facdrone's routines are more varied and
  its test tree smaller -- while blocks do, and half of facdrone's first ten blocks are name
  lists (`__all__`, import blocks), a shape this repository's first ten did not show.
- **New: the dead-code rules at 26% look different from the same rules at 19%.** Here, with
  the floors at zero, `unused_class` found nothing and `unused_variable`'s first ten were
  half genuine, half `_`; on facdrone `unused_class` found 18 of which eight are named in no
  other source file, and `unused_variable` found 55 of which the first ten split 3 / 4 / 3
  (nowhere, same file, other files). That is requirement 1.10's case in the direction it
  worried about least: the higher-resolution repository produces *more* findings, most of
  them defensible, and the lower-resolution one produces few. What 1.10 was written for -- a
  rule whose count is the analyser's rather than the code's -- shows in the variable rule's
  "named in other files" third and in `PortfolioControlInputs`, and nowhere else in these
  first tens. Neither repository is above the floor, so neither says what the rules do where
  they are allowed to run.
- **The feature probe refused the first facdrone run (requirement 9.3, as designed).** The
  cache facdrone was measured into on 2026-09-10 carried a `features.json` written before the
  token feature existed, and `check --all` with `lean.duplicates` on stopped in one second
  with `error: lean.duplicates needs lean tokens, which (Build 1262) does not offer
  (unverified)` and the hint to run `doctor` once. `doctor` under the same configuration
  printed `feature lean references: available`, `feature lean tokens: available`, `feature
  duplicate metric: available` and `after accuracy: 26%`, rewrote the probe beside the
  databases (in the cache, not the repository), and the check then ran. The cost test met the
  same refusal in its fresh cache on its first version and now runs `doctor` once in its
  fixture; a `check` on a fresh cache with a lean rule on always needs that step, and the
  error's hint says so.
- **`check --all` wrote facdrone's `scitools-hook.baseline.json`.** facdrone's configuration
  has `[baseline] adaptive = true`, and the whole-project run tightened two project averages
  in the file (`project.AVG:CountLineCode` 12.758 -> 12.053, `project.AVG:CyclomaticStrict`
  2.151 -> 2.102; `git diff` saved, 2 lines). The file was restored with `git checkout --`
  after each of the two manual runs, and the harness runs used a configuration whose
  `[baseline] file` points at a copy in the scratch directory, after which `git status` in
  facdrone was byte-for-byte what it was before (`?? task.md`, which predates this task).
  Nothing else in that repository was touched. Worth knowing for anyone measuring a
  repository they do not own: with adaptive baselines on, `check` is not read-only.
- **The two token rules at 26%.** Neither takes the floors, and the counts above are the
  same in both facdrone runs, as they should be.

## The cost of everything on: a warm check with one changed line, this repository (requirement 9.5)

Two instruments, one shape. The harness measures in place, on this working tree, under
`/usr/bin/time -v`, with the load average read before and after each run; the cost test
measures on a `git clone --local --no-hardlinks` of `HEAD` under a private `XDG_CACHE_HOME`
so that it can run in the gate without touching the developer's cache or tree. Both run the
pair "first check, then warm check" per configuration, each with a *different* appended
comment line and the file restored between them, so the quoted run is warm on the before side
and still analyses a real edit (the reason is in `warm_run_timing.py`: the same line twice
gives `und analyze -changed` nothing to do). Switching the reference and token rules on
changes the extraction fingerprint, so the all-on pair's first check re-extracts both sides
before the quoted run.

### The harness's four timed runs (`tests/perf/lean_defaults.py`, in place, probe file `src/scitools_hook/git/hooks.py`)


| Run | Wall | User CPU | Sys CPU | CPU% | Peak RSS | load 1m before -> after | Exit |
| --- | --- | --- | --- | --- | --- | --- | --- |
| first check, one changed line (lean-off) | 34.5 s | 34.3 s | 2.6 s | 107% | 297 MB | 1.49 -> 1.54 | 0 |
| warm check, one changed line (lean-off) | 14.7 s | 14.7 s | 1.0 s | 106% | 238 MB | 1.54 -> 1.71 | 0 |
| first check, one changed line (lean-on) | 34.0 s | 33.4 s | 1.6 s | 102% | 269 MB | 1.71 -> 1.58 | 0 |
| warm check, one changed line (lean-on) | 18.9 s | 18.8 s | 1.1 s | 105% | 311 MB | 1.58 -> 1.69 | 0 |

| Run | # | Phase | Time |
| --- | --- | --- | --- |
| first check, one changed line (lean-off) | 1 | `synchronising the after tree` | 0.1 s |
| first check, one changed line (lean-off) | 2 | `analysing the after database` | 5.9 s |
| first check, one changed line (lean-off) | 3 | `synchronising the before tree` | 0.1 s |
| first check, one changed line (lean-off) | 4 | `analysing the before database` | 4.9 s |
| first check, one changed line (lean-off) | 5 | `reading the after snapshot` | 10.9 s |
| first check, one changed line (lean-off) | 6 | `reading the before snapshot` | 10.9 s |
| warm check, one changed line (lean-off) | 1 | `synchronising the after tree` | 0.1 s |
| warm check, one changed line (lean-off) | 2 | `analysing the after database` | 1.9 s |
| warm check, one changed line (lean-off) | 3 | `synchronising the before tree` | 0.0 s |
| warm check, one changed line (lean-off) | 4 | `analysing the before database` | 0.0 s |
| warm check, one changed line (lean-off) | 5 | `reading the after snapshot` | 10.8 s |
| first check, one changed line (lean-on) | 1 | `synchronising the after tree` | 0.1 s |
| first check, one changed line (lean-on) | 2 | `analysing the after database` | 1.9 s |
| first check, one changed line (lean-on) | 3 | `synchronising the before tree` | 0.1 s |
| first check, one changed line (lean-on) | 4 | `analysing the before database` | 0.0 s |
| first check, one changed line (lean-on) | 5 | `reading the after snapshot` | 14.8 s |
| first check, one changed line (lean-on) | 6 | `reading the before snapshot` | 14.8 s |
| warm check, one changed line (lean-on) | 1 | `synchronising the after tree` | 0.1 s |
| warm check, one changed line (lean-on) | 2 | `analysing the after database` | 1.9 s |
| warm check, one changed line (lean-on) | 3 | `synchronising the before tree` | 0.0 s |
| warm check, one changed line (lean-on) | 4 | `analysing the before database` | 0.0 s |
| warm check, one changed line (lean-on) | 5 | `reading the after snapshot` | 14.6 s |


### The cost test's runs (`tests/contract/test_lean_cost_contract.py`, on a clone under a private cache, serial `-n 0 -s`)

| Run | Wall | Exit |
| --- | --- | --- |
| whole project, cold cache, lean off | 18.7 s | 1 |
| first check, one changed line, lean off | 35.5 s | 0 |
| warm check, one changed line, lean off | 15.3 s | 0 |
| warm check, one changed line, lean off | 15.1 s | 0 |
| doctor (feature probe), lean on | 8.3 s | 0 |
| first check, one changed line, lean on | 33.5 s | 0 |
| warm check, one changed line, lean on | 19.3 s | 0 |
| warm check, one changed line, lean on | 18.7 s | 0 |

Verdict line printed by the test: `warm check, lean off: 15.1 s; lean on: 18.7 s; added 3.6 s against 7.6 s allowed (50% of the all-off run): margin +4.0 s`
Result: `2 passed in 166.67s (0:02:46)`; one-minute load average 0.88 before the module, 1.42 after.

### What the numbers say

- **The bound holds, with margin.** Requirement 9.5 allows the added cost of everything on to
  be one half of today's warm check. Harness, in place: 14.7 s off, 18.9 s on, **4.2 s added
  against 7.35 s allowed** (28.6% of the all-off run; margin 3.1 s). Cost test, on the clone,
  serial: the table above, quoting the better of two warm runs per configuration. The earlier
  serial run of the cost test before it quoted the better of two (one warm run each) measured
  15.3 s off and 18.9 s on, 3.5 s added against 7.7 s allowed, margin +4.1 s. The design's
  figure of "6.5 s over today's 13.0 s" was written against a 13.0 s warm check; the tree has
  grown since (`reading the after snapshot` is 10.8 s alone now) and the bound is relative,
  so the number to hold onto is the share, not the seconds.
- **What the added cost is.** All of it is the after-side snapshot: `reading the after
  snapshot` goes from 10.8 s to 14.6 s in the harness (+3.8 s of the +4.2 s total), which is
  the worker's per-entity `refs` walk for the five reference rules plus the `lexer(False)`
  pass over every file for the two token rules, on one side (the before side is cached under
  the new fingerprint by the first check). The in-process rules, hint lookup and report
  account for the rest, under half a second: the family rule's pass over a one-line change
  considers the change's routines against the whole index, not the whole index against
  itself.
- **Why the whole-project figure is not the warm one.** `check --all` with everything on
  costs 43.7 s here (15.8 s of it the snapshot) and 85.7 s on facdrone (29.2 s the snapshot)
  -- the difference is the in-process pass with *every* routine affected, about 27 s and 56 s,
  which is the family rule's whole-project comparison that task 5.9 re-timed at 25.8 s here.
  That cost is real and is what an operator's first `check --all` with `similar_routines` on
  will see; it is not the warm check requirement 9.5 bounds, and it is recorded here so 6.4
  can weigh it when deciding whether that rule ships enabled.
- **The test can fail, and did.** Its first run measured a one-second "warm check" with
  everything on: a fresh cache holds no feature probe, the all-on configuration was refused
  with exit 2 (requirement 9.3), and the bound test alone would have passed on an added cost
  of -13.8 s. The companion test -- both quoted runs exited 0 and the all-on run printed the
  four floor refusals, which only happens with the switches on -- failed instead, and the
  bound test now asserts the exit codes itself before reading a figure. The fixture runs
  `doctor` once, untimed, before the all-on pair.
- **Load.** The machine carried a one-minute load average between 1.0 and 1.9 across every
  timed run (other work on it; four cores), printed per run in the harness table. Under the
  gate's `-n auto` the cost test shares the machine with three other workers driving `und`,
  which is why it quotes the better of two warm runs per configuration; that narrows the
  noise and leaves the bound where the requirement put it. A run the load pushes past the
  bound is a failure to look at, not a bound to widen.
- **Time the test costs the gate.** 2 min 47 s serial for eight runs (cold `--all` 18.7 s,
  two first checks of 35.5 s and 33.5 s, four warm checks, one `doctor` 8.3 s); one worker of
  four under `-n auto`.
