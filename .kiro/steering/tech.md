# Technology Stack

## Architecture

A single Python package with one console entry point. A thin CLI layer delegates to a core that has no knowledge of git or terminals; adapters wrap the two external systems (git, SciTools Understand). Dependency direction is strict:

`config → models (shared pure data) → understand adapter, git adapter → analysis (metrics, structure, diff) → report (human, JSON, SARIF, review aids) → runner (pipelines) → cli`

Nothing imports "upward". The hook entry point is just another CLI invocation. Every call into the `understand` Python API goes through a stdlib-only worker module that runs under Understand's bundled `upython` by default (in-process only when explicitly configured).

## Core Technologies

- **Language**: Python ≥ 3.12 (`requires-python = ">=3.12"`, no upper bound — see decisions).
- **Packaging / runtime**: `uv` project (`pyproject.toml`, `uv.lock`), installable and runnable with `uvx scitools-hook ...`. Build backend: hatchling.
- **External engine**: SciTools Understand ≥ 6.5 — `und` CLI for database create/add/analyze/export/codecheck, and the `understand` Python API for entities, metrics, references, architectures, graphs.
- **VCS**: git (staged-file discovery, worktree export of the pre-change state, hook installation).

## Key Libraries

- `typer` (CLI, typed subcommands) and `rich` (human output). Keep both out of the core.
- `pydantic` for config and report models (validated at boundaries, serialized to JSON).
- `platformdirs` for the per-user cache directory.
- `tomllib` (stdlib) for config files.
- Test: `pytest`, with a fake Understand adapter so the core is testable without a license.

Avoid heavy plotting stacks (the matplotlib/mpld3 pins were what rotted `srccheck`). Graphs come from Understand's own `draw()` (SVG/PNG); tabular output is text/JSON/CSV.

## Development Standards

### Type Safety
- Full type hints; `mypy --strict` on `src/`. Public functions have explicit return types.
- Validate external input (config, `und` output, git output) at the adapter boundary; the core works on typed models only.

### Code Quality
- `ruff` for lint + format (line length 100).
- The tool must pass its own gate: default thresholds apply to this repo (routines ≤ 60 lines, cyclomatic ≤ 10, nesting ≤ 3, ≤ 5 params; no dependency cycles between modules).
- Errors are typed (`UnderstandNotFound`, `LicenseError`, `AnalysisFailed`, ...) and map to distinct exit codes.

### Testing
- Unit tests for analysis/reporting with a fake adapter; contract tests for the Understand adapter that skip unless `SCITOOLS_HOME` resolves and a license is present.
- Hook tests run against a temporary git repo.
- Coverage target 85% on `src/`, excluding the real-Understand adapter.

## Development Environment

### Required Tools
- `uv` ≥ 0.12, Python 3.12 (uv will fetch it), git ≥ 2.40
- SciTools Understand ≥ 6.5 installed; location resolved in this order: `--scitools-home` flag → `SCITOOLS_HOME` env var → config file → `und` on `PATH` → well-known dirs (`~/scitools`, `/opt/scitools`, `/Applications/Understand.app/Contents/MacOS`, `C:\Program Files\SciTools`).

### Common Commands
```bash
# Dev: uv sync --all-extras
# Run: uv run scitools-hook check
# Test: uv run pytest
# Lint: uv run ruff check . && uv run mypy src
# Try as users would: uvx --from . scitools-hook doctor
```

## Key Technical Decisions

- **No interpreter pin; API worker instead**: `uvx <pkg>` ignores `requires-python`, and Understand's `understand` module is bound to one Python minor per Understand build (3.12 for 6.5). So the package requires only `>=3.12`, and all API code lives in `understand/worker.py` (stdlib + `understand` only). The adapter runs `upython worker.py` (Understand's bundled interpreter) as a subprocess with JSON in/out by default; importing the module in-process into system Python (the `srccheck --dllDir` idea) is opt-in only — on Linux 6.5 it aborts the interpreter with a Perl XS symbol error once a license is active. Never `pip install understand` — the PyPI package of that name is unrelated.
- **Understand database lives outside the working tree**: per-repo DB under the user cache dir (or `.git/scitools-hook/`), never committed. Analysis is incremental (`und analyze -changed` / `-files`).
- **Before/after via git**: the pre-change state comes from `git show`/worktree export of `HEAD` into a temp dir with its own DB (or Understand's `-gitcommit` databases), so the gate reports deltas, not just absolutes.
- **Hook lives in the tool, not the target repo**: `install-hook` writes a small shim into `.git/hooks/pre-commit` that calls `uvx scitools-hook check --staged`; a `.pre-commit-hooks.yaml` in this repo lets pre-commit-framework users reference it. Per-repo threshold config is optional; defaults + user-level config suffice.
- **Agent-first output**: every violation carries metric name, value, limit, entity, location, and a remediation hint; `--format json` is stable and documented.

---
_Document standards and patterns, not every dependency_
