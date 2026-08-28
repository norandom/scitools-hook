# Project Structure

## Organization Philosophy

`src/` layout, layered by dependency direction (see tech.md). Modules are small on purpose — this tool enforces routine/file limits and must pass them itself. One package, no plugins framework, no speculative abstraction.

## Directory Patterns

### Core package
**Location**: `src/scitools_hook/`
**Purpose**: everything importable. Sub-packages follow the layer order:
- `config/` — typed settings and threshold models, discovery of config files, defaults
- `models/` — shared pure-data models (snapshots, findings, cache state, git change records); every layer above `config` may import it, it imports only `config`
- `understand/` — adapter around `und` and the Python API (path discovery, DB lifecycle, metric/entity queries, graph export); `worker.py` is the only module that imports `understand`, and it imports nothing from `scitools_hook` so it can run under `upython`
- `git/` — staged files, HEAD export, hook installation
- `analysis/` — pure logic: threshold evaluation, before/after diff, structural checks (cycles, layers, fan-in/out), stats prefixes
- `report/` — human text, JSON, SARIF, review-aid bundle (summaries + SVGs), agent rules snippet
- `runner/` — pipelines that order adapter calls, analysis and reporting (`check`, `explain`, `baseline`, `doctor`)
- `cli/` — typer app, one module per subcommand, exit-code mapping

**Example**: a new structural check goes in `analysis/structure/<check>.py`, gets a model in `config/thresholds.py`, and a renderer line in `report/`. No CLI change needed.

### Hook assets
**Location**: repository root (`.pre-commit-hooks.yaml`) and `src/scitools_hook/git/hook_template.sh`
**Purpose**: what gets installed into other repos. Keep the shim minimal; all logic stays in the CLI.

### Tests
**Location**: `tests/` mirroring `src/scitools_hook/` (`tests/analysis/test_thresholds.py`, ...), `tests/fixtures/` for sample Understand exports and tiny sample repos, `tests/contract/` for real-Understand tests (skipped without license).

### Specs and steering
**Location**: `.kiro/specs/`, `.kiro/steering/` — process artefacts, not shipped.

## Naming Conventions

- **Files/modules**: `snake_case.py`; one concern per module.
- **Classes**: `PascalCase`; models end in the noun (`Violation`, `Threshold`, `ChangeSummary`), adapters end in `Adapter`, errors end in `Error`.
- **Functions**: `snake_case`, verb-first (`collect_metrics`, `find_cycles`).
- **CLI subcommands**: kebab-case (`install-hook`, `agent-rules`).
- **Metric names**: use Understand's exact identifiers (`CyclomaticStrict`, `CountClassCoupled`); synthetic ones are documented in `analysis/metrics/synthetic.py` and prefixed only when they collide.

## Import Organization

```python
from __future__ import annotations          # first
import ...                                  # stdlib
import typer                                # third-party
from scitools_hook.config import Thresholds # absolute, package-internal
```

- Absolute imports only; no relative imports across sub-packages.
- `understand` is imported lazily inside `understand/worker.py` only, after the path has been configured.

## Code Organization Principles

- Layer imports point one way: `config → models → understand/git → analysis → report → runner → cli`. Adapters never import each other or anything above them; `analysis` imports only `config`/`models`. An import-direction test enforces this; a violation is a review blocker.
- `analysis/` and `report/` never touch the filesystem, git, or `understand`; they receive typed data.
- Long-running external calls (`und analyze`) are isolated in the adapter with timeouts and clear error mapping.
- Prefer many small modules over one large one; the gate's own limits apply.

---
_Document patterns, not file trees. New files following patterns shouldn't require updates_
