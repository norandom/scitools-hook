"""Shared test fixtures: the synthetic before/after project snapshots (task 3.1).

``snapshot_before.json`` and ``snapshot_after.json`` describe one small Python project
(``src/cli/app.py``, ``src/analysis/engine.py``, ``src/analysis/rules.py``,
``src/understand/adapter.py``, ``src/util/text.py``) before and after one change, in the
JSON wire form of :class:`~scitools_hook.models.snapshot.ProjectSnapshot`. They are the
input for every rule evaluator, the change summary and the renderers, so the change was
chosen to exercise each rule at least once:

* ``app.build_parser`` is **modified** and gets worse (CyclomaticStrict 6 -> 12, MaxNesting
  2 -> 4, CountLineCode 40 -> 75): threshold, ratchet and highest-value cases.
* ``app.check_command`` is **added** (``is_new``); ``app.legacy_entry`` is **removed**;
  ``app.main``, ``rules.apply_rules``, ``adapter.extract`` and ``text.wrap_lines`` are
  **unchanged**.
* ``src/analysis/rules.py`` gains an edge back to ``src/analysis/engine.py``, closing a
  **new file cycle** that does not exist in ``before``.
* ``src/cli/app.py`` gains an edge to ``src/understand/adapter.py``: a **layer violation**
  from the ``cli`` architecture node into the ``understand`` node, also visible as a new
  ``cli -> understand`` architecture edge.
* ``src/cli/app.py`` **fan-out grows** from two files to four.
* ``engine.Engine`` is modified without its own file changing (CountClassCoupled 4 -> 6),
  which is the "affected because a dependency changed" case of requirement 4.2.
* Architecture nodes are the four depth-2 ``Directory Structure`` nodes; populations are
  provided per scope, including the ``project`` vectors that back the stats-prefixed
  defaults (``AVG:CyclomaticStrict``, ``AVG:CountLineCode``); ``PercentLackOfCohesion`` is
  reported unavailable for Python and the ``after`` side carries one parse error.

``tests/models/conftest.py`` puts ``tests/`` on ``sys.path`` so this package can be imported
as ``fixtures`` from any test module.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final, Literal

from scitools_hook.models.snapshot import EntityKey, ProjectSnapshot

# --- the synthetic project's vocabulary -------------------------------------------
#
# The five paths, the four architecture nodes and the two entity keys below describe the
# project the snapshots encode. They were written out in up to fourteen test modules each
# until `structure.duplicate_definition` counted them; collecting them here is what that rule
# asks for, and it is why a rename of the fixture project is now one edit rather than thirty.
#
# Only the values that were *identical* everywhere moved. Several modules bind these same
# names to something else on purpose -- `APP` is an `EntityKey` in the threshold tests and
# `TEXT` is a block of source in the contract project -- and those stay where they are.

APP: Final = "src/cli/app.py"
RULES: Final = "src/analysis/rules.py"
ENGINE: Final = "src/analysis/engine.py"
ADAPTER: Final = "src/understand/adapter.py"
TEXT: Final = "src/util/text.py"

CLI_NODE: Final = "Directory Structure/src/cli"
ANALYSIS_NODE: Final = "Directory Structure/src/analysis"
UNDERSTAND_NODE: Final = "Directory Structure/src/understand"
UTIL_NODE: Final = "Directory Structure/src/util"

BUILD_PARSER: Final = EntityKey(
    scope="routine", path=APP, longname="app.build_parser", parameters=""
)
"""The routine the change makes worse: CyclomaticStrict 6 -> 12, MaxNesting 2 -> 4."""

ENGINE_CLASS: Final = EntityKey(scope="class", path=ENGINE, longname="engine.Engine")
"""The class that is affected without its own file changing (CountClassCoupled 4 -> 6)."""


FIXTURES_DIR = Path(__file__).resolve().parent
"""Directory holding every static test fixture."""

Side = Literal["before", "after"]


def snapshot_path(side: Side) -> Path:
    """Path of the snapshot fixture file for ``side``."""
    return FIXTURES_DIR / f"snapshot_{side}.json"


def snapshot_fixture(side: Side) -> ProjectSnapshot:
    """Load and validate the ``before`` or ``after`` snapshot of the synthetic project."""
    raw = snapshot_path(side).read_text(encoding="utf-8")
    return ProjectSnapshot.model_validate(json.loads(raw))
