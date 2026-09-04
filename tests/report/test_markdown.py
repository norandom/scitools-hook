"""The three views of one change summary: text, Markdown and JSON (req 9.4, 9.6, 9.8).

The snapshots below are the contract of requirement 9.6 -- the same document rendered for a
terminal, for a merge request and for a machine -- over the summary the real builder produces
from ``tests/fixtures/snapshot_{before,after}.json``. Every number in them is a number the
fixtures carry: ``app.build_parser`` modified and worse, ``app.check_command`` added,
``app.legacy_entry`` removed, ``engine.Engine`` moved without its own file changing, three
added dependencies of which two cross an architecture boundary, and the two rankings the
builder derives from all of that.

The graph files (req 9.4) and the impact sets (req 9.5) are attached to the fixture summary
through ``ReviewAids``, exactly as ``explain`` will attach them, because the builder carries
them through untouched and the renderer is where a reviewer finally sees them.

Beyond the snapshots the tests pin the decisions a mutation could quietly undo: the renderer
preserves the producer's order instead of re-sorting it, the impact map is ordered by entity
key token (the order the model itself writes), Markdown escapes pipe characters in every cell
while the text view does not, paths are printed exactly as they arrive (no separator
normalisation), numbers lose the float tail and deltas keep their sign, and the last line of
both human views is the command ``analysis.change_summary.open_command`` built -- shell
quoting included -- never a second version of it derived from ``db_path``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

import pytest
from fixtures import APP, BUILD_PARSER, ENGINE, ENGINE_CLASS, RULES, snapshot_fixture

from scitools_hook.analysis.change_summary import ReviewAids, build_summary, open_command
from scitools_hook.models.cache import CachePaths
from scitools_hook.models.change import (
    AffectedSet,
    ChangeSummary,
    DependencyDelta,
    EntityDelta,
    GraphFile,
    ImpactSet,
)
from scitools_hook.models.snapshot import EntityKey, EntityRef, ProjectSnapshot
from scitools_hook.report.json_out import INDENT
from scitools_hook.report.markdown import Format, render_summary

ANALYSIS_NODE: Final = "Directory Structure/src/analysis"

AFFECTED: Final = frozenset({APP, RULES, ENGINE})

APP_FILE: Final = EntityKey(scope="file", path=APP, longname=APP)


def cache_paths(root: Path = Path("/cache/repo")) -> CachePaths:
    """A cache layout with a fixed root, so the expected strings do not depend on the host."""
    return CachePaths(
        root=root,
        before_tree=root / "before",
        after_tree=root / "after",
        before_db=root / "before.und",
        after_db=root / "after.und",
        state=root / "state.json",
        graphs=root / "graphs",
    )


def routine_ref(longname: str, path: str = APP, line: int = 10) -> EntityRef:
    """One routine as the impact sets and the entity deltas carry it."""
    key = EntityKey(scope="routine", path=path, longname=longname, parameters="")
    return EntityRef(key=key, kind="Python Function", name=longname.rpartition(".")[2], line=line)


def routine_refs(count: int, prefix: str) -> list[EntityRef]:
    """``count`` distinct routines, for an impact set that is only ever counted."""
    return [routine_ref(f"app.{prefix}{index}") for index in range(count)]


def review_aids() -> ReviewAids:
    """The graphs (req 9.4) and impact sets (req 9.5) ``explain`` attaches to the summary."""
    return ReviewAids(
        impact={
            BUILD_PARSER: ImpactSet(
                by_depth={1: routine_refs(2, "caller"), 2: routine_refs(3, "deep")}, total=5
            ),
            ENGINE_CLASS: ImpactSet(by_depth={1: [routine_ref("app.main")]}, total=1),
        },
        graphs=[
            GraphFile(key=BUILD_PARSER, graph="Butterfly", path=Path("/cache/repo/graphs/bp.svg")),
            GraphFile(key=APP_FILE, graph="Depends On", path=Path("/cache/repo/graphs/app.svg")),
        ],
    )


def fixture_summary() -> ChangeSummary:
    """The change summary of the synthetic change, with graphs and impact attached."""
    return build_summary(
        snapshot_fixture("before"),
        snapshot_fixture("after"),
        AffectedSet(files=set(AFFECTED)),
        cache_paths(),
        review_aids(),
    )


def empty_summary(root: Path = Path("/cache/repo")) -> ChangeSummary:
    """The summary of a change that touched nothing: every section is empty."""
    return build_summary(None, ProjectSnapshot(side="after"), AffectedSet(), cache_paths(root))


def summary_with(**fields: object) -> ChangeSummary:
    """An otherwise empty summary carrying exactly the fields a test wants to render."""
    return empty_summary().model_copy(update=fields)


# --- the three required snapshots (req 9.6) -------------------------------------

TEXT: Final = """\
change summary
  database: /cache/repo/after.und

files (3)
  src/analysis/engine.py  [Directory Structure/src/analysis]
    modified  class    engine.Engine  line 20
      CountClassCoupled  4 -> 6  (+2)
  src/analysis/rules.py  [Directory Structure/src/analysis]
    modified  file     src/analysis/rules.py
      CountLineCode  90 -> 96  (+6)
      RatioCommentToCode  0.2 -> 0.19  (-0.01)
  src/cli/app.py  [Directory Structure/src/cli]
    modified  file     src/cli/app.py
      CountLineCode  120 -> 160  (+40)
      MaxCyclomaticStrict  6 -> 12  (+6)
      RatioCommentToCode  0.18 -> 0.12  (-0.06)
    modified  routine  app.build_parser  line 34
      CountLineCode  40 -> 75  (+35)
      CountPath  12 -> 40  (+28)
      CountStmt  30 -> 58  (+28)
      CyclomaticModified  6 -> 11  (+5)
      CyclomaticStrict  6 -> 12  (+6)
      Essential  2 -> 4  (+2)
      MaxNesting  2 -> 4  (+2)
    added     routine  app.check_command  line 96
      CountLineCode  - -> 26  (+26)
      CountParams  - -> 1  (+1)
      CountPath  - -> 6  (+6)
      CountStmt  - -> 18  (+18)
      CyclomaticModified  - -> 4  (+4)
      CyclomaticStrict  - -> 4  (+4)
      Essential  - -> 1  (+1)
      MaxNesting  - -> 2  (+2)
    removed   routine  app.legacy_entry  line 80
      CountLineCode  22 -> -  (-22)
      CountPath  6 -> -  (-6)
      CountStmt  15 -> -  (-15)
      CyclomaticModified  4 -> -  (-4)
      CyclomaticStrict  4 -> -  (-4)
      Essential  1 -> -  (-1)
      MaxNesting  2 -> -  (-2)

dependencies (3)
  Directory Structure/src/analysis
    added    src/analysis/rules.py -> src/analysis/engine.py
  Directory Structure/src/cli
    added    src/cli/app.py -> src/analysis/rules.py
      crosses into Directory Structure/src/analysis
    added    src/cli/app.py -> src/understand/adapter.py
      crosses into Directory Structure/src/understand

largest deltas (10)
   1. src/cli/app.py  CountLineCode  120 -> 160  (+40)
   2. app.build_parser (src/cli/app.py)  CountLineCode  40 -> 75  (+35)
   3. app.build_parser (src/cli/app.py)  CountPath  12 -> 40  (+28)
   4. app.build_parser (src/cli/app.py)  CountStmt  30 -> 58  (+28)
   5. app.check_command (src/cli/app.py)  CountLineCode  - -> 26  (+26)
   6. app.legacy_entry (src/cli/app.py)  CountLineCode  22 -> -  (-22)
   7. app.check_command (src/cli/app.py)  CountStmt  - -> 18  (+18)
   8. app.legacy_entry (src/cli/app.py)  CountStmt  15 -> -  (-15)
   9. src/analysis/rules.py  CountLineCode  90 -> 96  (+6)
  10. src/cli/app.py  MaxCyclomaticStrict  6 -> 12  (+6)

largest values (10)
   1. src/cli/app.py  CountLineCode  160
   2. src/analysis/rules.py  CountLineCode  96
   3. app.build_parser (src/cli/app.py)  CountLineCode  75
   4. app.build_parser (src/cli/app.py)  CountStmt  58
   5. app.build_parser (src/cli/app.py)  CountPath  40
   6. app.check_command (src/cli/app.py)  CountLineCode  26
   7. app.check_command (src/cli/app.py)  CountStmt  18
   8. src/cli/app.py  MaxCyclomaticStrict  12
   9. app.build_parser (src/cli/app.py)  CyclomaticStrict  12
  10. app.build_parser (src/cli/app.py)  CyclomaticModified  11

impact (2)
  engine.Engine (src/analysis/engine.py)  1 total; depth 1: 1
  app.build_parser (src/cli/app.py)  5 total; depth 1: 2, depth 2: 3

graphs (2)
  Depends On  src/cli/app.py  /cache/repo/graphs/app.svg
  Butterfly   app.build_parser (src/cli/app.py)  /cache/repo/graphs/bp.svg

open in the Understand GUI: understand /cache/repo/after.und"""

MARKDOWN: Final = """\
# Change summary

Database: `/cache/repo/after.und`

## Files (3)

### `src/analysis/engine.py`

Architecture: `Directory Structure/src/analysis`

| Entity | Scope | Line | Status | Metric | Before | After | Δ |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `engine.Engine` | class | 20 | modified | `CountClassCoupled` | 4 | 6 | +2 |

### `src/analysis/rules.py`

Architecture: `Directory Structure/src/analysis`

| Entity | Scope | Line | Status | Metric | Before | After | Δ |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `src/analysis/rules.py` | file | - | modified | `CountLineCode` | 90 | 96 | +6 |
| `src/analysis/rules.py` | file | - | modified | `RatioCommentToCode` | 0.2 | 0.19 | -0.01 |

### `src/cli/app.py`

Architecture: `Directory Structure/src/cli`

| Entity | Scope | Line | Status | Metric | Before | After | Δ |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `src/cli/app.py` | file | - | modified | `CountLineCode` | 120 | 160 | +40 |
| `src/cli/app.py` | file | - | modified | `MaxCyclomaticStrict` | 6 | 12 | +6 |
| `src/cli/app.py` | file | - | modified | `RatioCommentToCode` | 0.18 | 0.12 | -0.06 |
| `app.build_parser` | routine | 34 | modified | `CountLineCode` | 40 | 75 | +35 |
| `app.build_parser` | routine | 34 | modified | `CountPath` | 12 | 40 | +28 |
| `app.build_parser` | routine | 34 | modified | `CountStmt` | 30 | 58 | +28 |
| `app.build_parser` | routine | 34 | modified | `CyclomaticModified` | 6 | 11 | +5 |
| `app.build_parser` | routine | 34 | modified | `CyclomaticStrict` | 6 | 12 | +6 |
| `app.build_parser` | routine | 34 | modified | `Essential` | 2 | 4 | +2 |
| `app.build_parser` | routine | 34 | modified | `MaxNesting` | 2 | 4 | +2 |
| `app.check_command` | routine | 96 | added | `CountLineCode` | - | 26 | +26 |
| `app.check_command` | routine | 96 | added | `CountParams` | - | 1 | +1 |
| `app.check_command` | routine | 96 | added | `CountPath` | - | 6 | +6 |
| `app.check_command` | routine | 96 | added | `CountStmt` | - | 18 | +18 |
| `app.check_command` | routine | 96 | added | `CyclomaticModified` | - | 4 | +4 |
| `app.check_command` | routine | 96 | added | `CyclomaticStrict` | - | 4 | +4 |
| `app.check_command` | routine | 96 | added | `Essential` | - | 1 | +1 |
| `app.check_command` | routine | 96 | added | `MaxNesting` | - | 2 | +2 |
| `app.legacy_entry` | routine | 80 | removed | `CountLineCode` | 22 | - | -22 |
| `app.legacy_entry` | routine | 80 | removed | `CountPath` | 6 | - | -6 |
| `app.legacy_entry` | routine | 80 | removed | `CountStmt` | 15 | - | -15 |
| `app.legacy_entry` | routine | 80 | removed | `CyclomaticModified` | 4 | - | -4 |
| `app.legacy_entry` | routine | 80 | removed | `CyclomaticStrict` | 4 | - | -4 |
| `app.legacy_entry` | routine | 80 | removed | `Essential` | 1 | - | -1 |
| `app.legacy_entry` | routine | 80 | removed | `MaxNesting` | 2 | - | -2 |

## Dependencies (3)

| Architecture node | Change | From | To | Crosses |
| --- | --- | --- | --- | --- |
{DEP_ROW_1}
{DEP_ROW_2}
{DEP_ROW_3}

## Largest deltas (10)

| # | Entity | Metric | Before | After | Δ |
| --- | --- | --- | --- | --- | --- |
| 1 | `src/cli/app.py` | `CountLineCode` | 120 | 160 | +40 |
| 2 | `app.build_parser (src/cli/app.py)` | `CountLineCode` | 40 | 75 | +35 |
| 3 | `app.build_parser (src/cli/app.py)` | `CountPath` | 12 | 40 | +28 |
| 4 | `app.build_parser (src/cli/app.py)` | `CountStmt` | 30 | 58 | +28 |
| 5 | `app.check_command (src/cli/app.py)` | `CountLineCode` | - | 26 | +26 |
| 6 | `app.legacy_entry (src/cli/app.py)` | `CountLineCode` | 22 | - | -22 |
| 7 | `app.check_command (src/cli/app.py)` | `CountStmt` | - | 18 | +18 |
| 8 | `app.legacy_entry (src/cli/app.py)` | `CountStmt` | 15 | - | -15 |
| 9 | `src/analysis/rules.py` | `CountLineCode` | 90 | 96 | +6 |
| 10 | `src/cli/app.py` | `MaxCyclomaticStrict` | 6 | 12 | +6 |

## Largest values (10)

| # | Entity | Metric | Value |
| --- | --- | --- | --- |
| 1 | `src/cli/app.py` | `CountLineCode` | 160 |
| 2 | `src/analysis/rules.py` | `CountLineCode` | 96 |
| 3 | `app.build_parser (src/cli/app.py)` | `CountLineCode` | 75 |
| 4 | `app.build_parser (src/cli/app.py)` | `CountStmt` | 58 |
| 5 | `app.build_parser (src/cli/app.py)` | `CountPath` | 40 |
| 6 | `app.check_command (src/cli/app.py)` | `CountLineCode` | 26 |
| 7 | `app.check_command (src/cli/app.py)` | `CountStmt` | 18 |
| 8 | `src/cli/app.py` | `MaxCyclomaticStrict` | 12 |
| 9 | `app.build_parser (src/cli/app.py)` | `CyclomaticStrict` | 12 |
| 10 | `app.build_parser (src/cli/app.py)` | `CyclomaticModified` | 11 |

## Impact (2)

| Entity | Total | By depth |
| --- | --- | --- |
| `engine.Engine (src/analysis/engine.py)` | 1 | depth 1: 1 |
| `app.build_parser (src/cli/app.py)` | 5 | depth 1: 2, depth 2: 3 |

## Graphs (2)

| Entity | Graph | File |
| --- | --- | --- |
| `src/cli/app.py` | Depends On | `/cache/repo/graphs/app.svg` |
| `app.build_parser (src/cli/app.py)` | Butterfly | `/cache/repo/graphs/bp.svg` |

Open in the Understand GUI: `understand /cache/repo/after.und`"""

DEP_ROW_1: Final = (
    "| `Directory Structure/src/analysis` | added | `src/analysis/rules.py` "
    "| `src/analysis/engine.py` | no |"
)
DEP_ROW_2: Final = (
    "| `Directory Structure/src/cli` | added | `src/cli/app.py` | `src/analysis/rules.py` | yes |"
)
DEP_ROW_3: Final = (
    "| `Directory Structure/src/cli` | added | `src/cli/app.py` "
    "| `src/understand/adapter.py` | yes |"
)

MARKDOWN_TEXT: Final = MARKDOWN.format(
    DEP_ROW_1=DEP_ROW_1, DEP_ROW_2=DEP_ROW_2, DEP_ROW_3=DEP_ROW_3
)

EMPTY_TEXT: Final = """\
change summary
  database: /cache/repo/after.und

files (0)
  none

dependencies (0)
  none

largest deltas (0)
  none

largest values (0)
  none

impact (0)
  none

graphs (0)
  none

open in the Understand GUI: understand /cache/repo/after.und"""

EMPTY_MARKDOWN: Final = """\
# Change summary

Database: `/cache/repo/after.und`

## Files (0)

_None._

## Dependencies (0)

_None._

## Largest deltas (0)

_None._

## Largest values (0)

_None._

## Impact (0)

_None._

## Graphs (0)

_None._

Open in the Understand GUI: `understand /cache/repo/after.und`"""


def test_text_snapshot() -> None:
    """The terminal view of the fixture change (req 9.6)."""
    assert render_summary(fixture_summary(), "text") == TEXT


def test_markdown_snapshot() -> None:
    """The merge-request view of the same change: the same facts as tables (req 9.6)."""
    assert render_summary(fixture_summary(), "markdown") == MARKDOWN_TEXT


def test_json_snapshot_is_the_change_summary_document() -> None:
    """The machine view is the document itself, and it round-trips (req 9.6)."""
    summary = fixture_summary()

    got = render_summary(summary, "json")

    assert ChangeSummary.model_validate(json.loads(got)) == summary


def test_json_uses_the_same_indentation_as_the_run_document() -> None:
    """Both JSON outputs of the tool look the same (``report.json_out``)."""
    got = render_summary(fixture_summary(), "json")

    assert f'\n{" " * INDENT}"files"' in got
    assert got == got.rstrip("\n")


def test_json_carries_the_graph_files_and_the_impact_entities() -> None:
    """What the human views only count, the machine view names in full (req 9.4, 9.5)."""
    document = json.loads(render_summary(fixture_summary(), "json"))

    assert [graph["path"] for graph in document["graphs"]] == [
        "/cache/repo/graphs/app.svg",
        "/cache/repo/graphs/bp.svg",
    ]
    assert len(document["impact"][BUILD_PARSER.token]["by_depth"]["2"]) == 3


# --- the empty change -----------------------------------------------------------


def test_empty_summary_renders_as_text() -> None:
    assert render_summary(empty_summary(), "text") == EMPTY_TEXT


def test_empty_summary_renders_as_markdown() -> None:
    assert render_summary(empty_summary(), "markdown") == EMPTY_MARKDOWN


def test_empty_summary_renders_as_json() -> None:
    empty = empty_summary()

    assert ChangeSummary.model_validate(json.loads(render_summary(empty, "json"))) == empty


# --- the open-in-GUI command is the last line, and is the builder's (req 9.8) ----


def quoted_summary() -> ChangeSummary:
    """A summary whose database path needs shell quoting, so a re-derivation is visible."""
    return empty_summary(Path("/cache/my repo"))


def test_the_open_command_is_the_last_line_of_the_text_view() -> None:
    got = render_summary(quoted_summary(), "text")

    assert (
        got.splitlines()[-1]
        == f"open in the Understand GUI: {open_command(cache_paths(Path('/cache/my repo')))}"
    )


def test_the_open_command_is_the_last_line_of_the_markdown_view() -> None:
    command = open_command(cache_paths(Path("/cache/my repo")))

    got = render_summary(quoted_summary(), "markdown")

    assert got.splitlines()[-1] == f"Open in the Understand GUI: `{command}`"


def test_the_open_command_is_taken_from_the_summary_not_rebuilt() -> None:
    """A renderer that rebuilt the command from ``db_path`` would ignore this field."""
    summary = summary_with(open_command="understand-6.5 '/elsewhere/other.und'")

    for fmt in ("text", "markdown"):
        assert "understand-6.5 '/elsewhere/other.und'" in render_summary(summary, fmt)


def test_the_open_command_keeps_the_builders_shell_quoting() -> None:
    """``open_command`` shlex-quotes the path; printing ``db_path`` raw would drop the quotes."""
    got = render_summary(quoted_summary(), "text")

    assert "'/cache/my repo/after.und'" in got.splitlines()[-1]


# --- order is the producer's ----------------------------------------------------


def delta_of(key: EntityKey, metric: str, before: float, after: float) -> EntityDelta:
    """One modified entity carrying exactly one moved metric."""
    ref = EntityRef(key=key, kind="Python File", name=key.longname.rpartition("/")[2])
    return EntityDelta(
        ref=ref,
        status="modified",
        before={metric: before},
        after={metric: after},
        delta={metric: after - before},
    )


def file_key(path: str) -> EntityKey:
    """The key of a file entity, whose qualified name is its own path."""
    return EntityKey(scope="file", path=path, longname=path)


def unsorted_summary() -> ChangeSummary:
    """A summary whose lists are deliberately not in alphabetical order."""
    zeta, alpha = file_key("src/zeta.py"), file_key("src/alpha.py")
    return summary_with(
        files={
            "src/zeta.py": [delta_of(zeta, "CountLineCode", 1, 2)],
            "src/alpha.py": [delta_of(alpha, "CountLineCode", 1, 3)],
        },
        top_by_delta=[
            delta_of(zeta, "CountLineCode", 1, 2),
            delta_of(alpha, "CountLineCode", 1, 3),
        ],
        top_by_value=[
            delta_of(zeta, "CountLineCode", 1, 2),
            delta_of(alpha, "CountLineCode", 1, 3),
        ],
        graphs=[
            GraphFile(key=zeta, graph="Depends On", path=Path("/g/zeta.svg")),
            GraphFile(key=alpha, graph="Depends On", path=Path("/g/alpha.svg")),
        ],
    )


@pytest.mark.parametrize("fmt", ["text", "markdown"])
def test_files_keep_the_order_the_builder_wrote_them_in(fmt: Format) -> None:
    got = render_summary(unsorted_summary(), fmt)

    assert got.index("src/zeta.py") < got.index("src/alpha.py")


@pytest.mark.parametrize("fmt", ["text", "markdown"])
def test_rankings_keep_the_order_the_builder_ranked_them_in(fmt: Format) -> None:
    got = render_summary(unsorted_summary(), fmt)
    ranked = [line for line in got.splitlines() if "zeta" in line or "alpha" in line]

    assert ranked.index([line for line in ranked if "zeta" in line][0]) == 0


@pytest.mark.parametrize("fmt", ["text", "markdown"])
def test_graphs_keep_the_order_they_were_exported_in(fmt: Format) -> None:
    got = render_summary(unsorted_summary(), fmt)

    assert got.index("/g/zeta.svg") < got.index("/g/alpha.svg")


def test_metrics_keep_the_order_the_builder_moved_them_in() -> None:
    """The builder sorts a delta's metrics; the renderer prints them, it does not re-sort."""
    ref = EntityRef(key=file_key("a.py"), kind="Python File", name="a.py")
    delta = EntityDelta(
        ref=ref,
        status="modified",
        before={"Zeta": 1, "Alpha": 1},
        after={"Zeta": 2, "Alpha": 2},
        delta={"Zeta": 1, "Alpha": 1},
    )
    summary = summary_with(files={"a.py": [delta]})

    for fmt in ("text", "markdown"):
        got = render_summary(summary, fmt)
        assert got.index("Zeta") < got.index("Alpha")


def test_a_removed_entity_whose_metrics_never_moved_still_shows_its_numbers() -> None:
    """A row falls back to the union of BOTH sides, so a removed entity is not left blank.

    ``change_summary._movement`` omits a metric whose change is zero, so an entity the
    change deleted whose metrics were all zero arrives with an empty ``delta`` and an empty
    ``after``: its numbers exist only on the ``before`` side (req 9.1).
    """
    key = EntityKey(scope="routine", path="src/a.py", longname="a.noop", parameters="")
    removed = EntityDelta(
        ref=EntityRef(key=key, kind="Python Function", name="noop", line=3),
        status="removed",
        before={"CountParams": 0.0},
        after={},
        delta={},
    )
    summary = summary_with(files={"src/a.py": [removed]})

    assert "CountParams  0 -> -  (0)" in render_summary(summary, "text")
    assert "| `CountParams` | 0 | - | 0 |" in render_summary(summary, "markdown")


def test_a_removed_dependency_is_not_reported_as_an_added_one() -> None:
    summary = summary_with(
        dependencies=[DependencyDelta(src="a.py", dst="b.py", status="removed", src_node="Core")]
    )

    assert "removed  a.py -> b.py" in render_summary(summary, "text")
    assert "| `Core` | removed | `a.py` | `b.py` | no |" in render_summary(summary, "markdown")


def test_dependencies_keep_the_producers_grouping() -> None:
    """The builder orders by source node; the renderer groups the runs it finds."""
    summary = summary_with(
        dependencies=[
            DependencyDelta(src="b.py", dst="c.py", status="added", src_node="Ui"),
            DependencyDelta(src="a.py", dst="c.py", status="removed", src_node="Core"),
        ]
    )

    lines = render_summary(summary, "text").splitlines()
    nodes = [line.strip() for line in lines if line.strip() in {"Ui", "Core"}]

    assert nodes == ["Ui", "Core"]


def test_a_dependency_outside_every_architecture_node_is_still_listed() -> None:
    summary = summary_with(dependencies=[DependencyDelta(src="a.py", dst="b.py", status="added")])

    got = render_summary(summary, "text")

    assert "(no architecture node)" in got
    assert "a.py -> b.py" in got


# --- crossing an architecture boundary (req 9.2) --------------------------------


def crossing_summary() -> ChangeSummary:
    """One dependency that crosses a boundary and one that stays inside a node."""
    return summary_with(
        dependencies=[
            DependencyDelta(
                src="core/a.py",
                dst="core/b.py",
                status="added",
                src_node="Core",
                dst_node="Core",
            ),
            DependencyDelta(
                src="core/a.py",
                dst="ui/c.py",
                status="added",
                src_node="Core",
                dst_node="Ui",
                crosses_arch=True,
            ),
        ]
    )


def test_only_the_crossing_dependency_is_marked_in_text() -> None:
    lines = render_summary(crossing_summary(), "text").splitlines()
    crossings = [line.strip() for line in lines if line.strip().startswith("crosses")]

    assert crossings == ["crosses into Ui"]


def test_only_the_crossing_dependency_is_marked_in_markdown() -> None:
    got = render_summary(crossing_summary(), "markdown")
    rows = [line for line in got.splitlines() if line.startswith("| `Core`")]

    assert [row.endswith("| no |") for row in rows] == [True, False]
    assert rows[1].endswith("| yes |")


def test_a_crossing_without_a_named_target_node_is_still_marked() -> None:
    summary = summary_with(
        dependencies=[DependencyDelta(src="a.py", dst="b.py", status="added", crosses_arch=True)]
    )

    assert "crosses an architecture boundary" in render_summary(summary, "text")


# --- Markdown escaping ----------------------------------------------------------


def piped_summary() -> ChangeSummary:
    """A change whose file, entity and metric names all contain a pipe character."""
    key = EntityKey(scope="file", path="src/a|b.py", longname="src/a|b.py")
    return summary_with(
        files={"src/a|b.py": [delta_of(key, "Count|Line", 1, 2)]},
        graphs=[GraphFile(key=key, graph="Depends On", path=Path("/g/a|b.svg"))],
    )


def test_markdown_escapes_a_pipe_in_every_cell() -> None:
    got = render_summary(piped_summary(), "markdown").splitlines()

    assert "| `src/a\\|b.py` | file | - | modified | `Count\\|Line` | 1 | 2 | +1 |" in got
    assert "| `src/a\\|b.py` | Depends On | `/g/a\\|b.svg` |" in got
    assert "### `src/a\\|b.py`" in got


def test_the_text_view_leaves_a_pipe_alone() -> None:
    """Escaping is a Markdown table rule, not a fact about the data."""
    got = render_summary(piped_summary(), "text")

    assert "src/a|b.py" in got
    assert "\\" not in got


def test_a_windows_style_file_path_is_not_rewritten() -> None:
    """The summary shows the paths the snapshot carries; nothing is re-separated here."""
    key = EntityKey(scope="file", path="src\\cli\\app.py", longname="src\\cli\\app.py")
    summary = summary_with(files={"src\\cli\\app.py": [delta_of(key, "CountLineCode", 1, 2)]})

    for fmt in ("text", "markdown"):
        got = render_summary(summary, fmt)
        assert "src\\cli\\app.py" in got
        assert "src/cli/app.py" not in got


def test_a_path_is_printed_exactly_as_it_arrives() -> None:
    """No separator normalisation: the reviewer opens the path the exporter wrote."""
    key = file_key("src/a.py")
    summary = summary_with(
        graphs=[GraphFile(key=key, graph="Butterfly", path=Path("C:\\out\\a.svg"))]
    )

    for fmt in ("text", "markdown"):
        assert "C:\\out\\a.svg" in render_summary(summary, fmt)


# --- numbers --------------------------------------------------------------------


def test_metric_values_drop_the_float_tail() -> None:
    summary = summary_with(files={"a.py": [delta_of(file_key("a.py"), "CountLineCode", 40, 75)]})

    got = render_summary(summary, "text")

    assert "CountLineCode  40 -> 75  (+35)" in got
    assert "40.0" not in got


def test_a_growing_metric_keeps_its_plus_sign() -> None:
    summary = summary_with(files={"a.py": [delta_of(file_key("a.py"), "CountLineCode", 1, 3)]})

    assert "(+2)" in render_summary(summary, "text")


def test_a_shrinking_metric_keeps_its_minus_sign() -> None:
    summary = summary_with(files={"a.py": [delta_of(file_key("a.py"), "CountLineCode", 3, 1)]})

    assert "(-2)" in render_summary(summary, "text")


def test_a_ratio_is_not_rendered_with_its_floating_point_tail() -> None:
    """``0.19 - 0.2`` is not exactly ``-0.01`` in binary, and a reviewer must not see that."""
    summary = summary_with(
        files={"a.py": [delta_of(file_key("a.py"), "RatioCommentToCode", 0.2, 0.19)]}
    )

    assert "RatioCommentToCode  0.2 -> 0.19  (-0.01)" in render_summary(summary, "text")


# --- the shapes a renderer must survive -----------------------------------------


def test_an_entity_without_an_architecture_path_renders_without_one() -> None:
    """``arch_path`` is ``None`` for a file no architecture contains (req 9.7)."""
    summary = summary_with(files={"a.py": [delta_of(file_key("a.py"), "CountLineCode", 1, 2)]})

    assert "  a.py\n" in render_summary(summary, "text")
    assert "[" not in render_summary(summary, "text")
    assert "Architecture" not in render_summary(summary, "markdown")


def test_an_entity_with_an_architecture_path_shows_it() -> None:
    delta = delta_of(file_key("a.py"), "CountLineCode", 1, 2).model_copy(
        update={"arch_path": "Directory Structure/src"}
    )
    summary = summary_with(files={"a.py": [delta]})

    assert "[Directory Structure/src]" in render_summary(summary, "text")
    assert "Architecture: `Directory Structure/src`" in render_summary(summary, "markdown")


def test_a_file_whose_entities_did_not_move_says_so() -> None:
    summary = summary_with(files={"a.py": []})

    assert "no entity changed" in render_summary(summary, "text")
    assert "_No entity changed._" in render_summary(summary, "markdown")


def test_an_entity_without_a_line_number_prints_no_line() -> None:
    summary = summary_with(files={"a.py": [delta_of(file_key("a.py"), "CountLineCode", 1, 2)]})

    assert "line" not in render_summary(summary, "text")


def test_a_ranking_row_whose_metric_did_not_move_still_shows_its_value() -> None:
    """``top_by_value`` may rank a metric that stood still: the row must not come out empty."""
    ref = EntityRef(key=file_key("a.py"), kind="Python File", name="a.py")
    row = EntityDelta(
        ref=ref, status="modified", before={"CountParams": 3}, after={"CountParams": 3}
    )
    summary = summary_with(top_by_value=[row])

    assert "   1. a.py  CountParams  3" in render_summary(summary, "text")


# --- impact (req 9.5) -----------------------------------------------------------


def test_impact_entities_are_ordered_by_entity_key_token() -> None:
    """The order the model itself writes, so the three views agree."""
    summary = summary_with(
        impact={
            BUILD_PARSER: ImpactSet(by_depth={1: [routine_ref("app.x")]}, total=1),
            ENGINE_CLASS: ImpactSet(by_depth={1: [routine_ref("app.y")]}, total=1),
        }
    )

    got = render_summary(summary, "text")

    assert got.index("engine.Engine") < got.index("app.build_parser")


def test_impact_depths_are_counted_in_ascending_depth_order() -> None:
    summary = summary_with(
        impact={
            ENGINE_CLASS: ImpactSet(
                by_depth={2: routine_refs(3, "deep"), 1: routine_refs(2, "near")}, total=5
            )
        }
    )

    assert "5 total; depth 1: 2, depth 2: 3" in render_summary(summary, "text")


def test_impact_reports_the_producers_total_not_a_recount() -> None:
    """A truncated impact set says how big it really was; the renderer never recounts."""
    summary = summary_with(
        impact={ENGINE_CLASS: ImpactSet(by_depth={1: [routine_ref("app.x")]}, total=97)}
    )

    assert "97 total; depth 1: 1" in render_summary(summary, "text")


def test_impact_does_not_list_the_entities_it_counts() -> None:
    """The blast radius is a shape in the human views; the JSON view carries the names."""
    summary = summary_with(
        impact={ENGINE_CLASS: ImpactSet(by_depth={1: [routine_ref("app.secret_caller")]}, total=1)}
    )

    for fmt in ("text", "markdown"):
        assert "secret_caller" not in render_summary(summary, fmt)


def test_an_impact_set_with_no_depths_still_reports_its_total() -> None:
    summary = summary_with(impact={ENGINE_CLASS: ImpactSet(total=0)})

    assert "0 total" in render_summary(summary, "text")


# --- graphs (req 9.4) -----------------------------------------------------------


def test_a_graph_is_listed_with_its_entity_and_its_kind() -> None:
    summary = summary_with(
        graphs=[GraphFile(key=BUILD_PARSER, graph="Butterfly", path=Path("/g/bp.svg"))]
    )

    assert "Butterfly   app.build_parser (src/cli/app.py)  /g/bp.svg" in render_summary(
        summary, "text"
    )


# --- section counts and the format switch ---------------------------------------


def test_each_section_header_counts_what_it_lists() -> None:
    summary = summary_with(
        files={"a.py": [], "b.py": []},
        graphs=[GraphFile(key=file_key("a.py"), graph="Depends On", path=Path("/g/a.svg"))],
    )

    got = render_summary(summary, "text")

    assert "files (2)" in got
    assert "graphs (1)" in got
    assert "dependencies (0)" in got


def test_an_unknown_format_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown change-summary format"):
        render_summary(empty_summary(), "html")  # type: ignore[arg-type]


def test_the_text_view_is_ascii_so_it_survives_any_console() -> None:
    render_summary(fixture_summary(), "text").encode("ascii")


def test_rendering_is_deterministic() -> None:
    for fmt in ("text", "markdown", "json"):
        assert render_summary(fixture_summary(), fmt) == render_summary(fixture_summary(), fmt)
