"""The change-summary builder over the synthetic snapshots (task 4.8; req 9.1-9.3, 9.5, 9.7, 9.8).

Every expectation below is a number the fixtures really carry (``tests/fixtures/__init__.py``):
``app.build_parser`` is modified and worse (CyclomaticStrict 6 -> 12, MaxNesting 2 -> 4,
CountLineCode 40 -> 75), ``app.check_command`` is added, ``app.legacy_entry`` is removed,
``app.main`` and ``rules.apply_rules`` are unchanged, ``src/cli/app.py`` grew (120 -> 160)
while its comment ratio fell, ``src/analysis/rules.py`` grew a little (90 -> 96), and
``engine.Engine`` moved without its own file changing (CountClassCoupled 4 -> 6). The change
adds three file dependencies -- ``rules.py -> engine.py`` inside the ``analysis`` node,
``app.py -> rules.py`` and ``app.py -> adapter.py`` across an architecture boundary -- and
removes none.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

import pytest
from fixtures import (
    ADAPTER,
    APP,
    BUILD_PARSER,
    CLI_NODE,
    ENGINE,
    ENGINE_CLASS,
    RULES,
    TEXT,
    snapshot_fixture,
)

from scitools_hook.analysis.change_summary import (
    DEFAULT_TOP_N,
    GUI_EXECUTABLE,
    ReviewAids,
    architecture_index,
    build_summary,
    open_command,
)
from scitools_hook.models.cache import CachePaths
from scitools_hook.models.change import (
    AffectedSet,
    ChangeSummary,
    DependencyDelta,
    EntityDelta,
    GraphFile,
    ImpactSet,
)
from scitools_hook.models.snapshot import DepEdge, EntityKey, EntityRef, ProjectSnapshot

ANALYSIS_NODE = "Directory Structure/src/analysis"
UNDERSTAND_NODE = "Directory Structure/src/understand"

AFFECTED_FILES = frozenset({APP, RULES, ENGINE})
"""The change's files: the two it edited plus the one whose class moved with them."""

CHECK_COMMAND = EntityKey(
    scope="routine", path=APP, longname="app.check_command", parameters="args"
)
LEGACY_ENTRY = EntityKey(scope="routine", path=APP, longname="app.legacy_entry", parameters="")


@pytest.fixture
def after() -> ProjectSnapshot:
    """The ``after`` side of the synthetic project."""
    return snapshot_fixture("after")


@pytest.fixture
def before() -> ProjectSnapshot:
    """The ``before`` side of the synthetic project."""
    return snapshot_fixture("before")


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


def build(
    after: ProjectSnapshot,
    before: ProjectSnapshot | None,
    files: Iterable[str] = AFFECTED_FILES,
    aids: ReviewAids | None = None,
) -> ChangeSummary:
    """The summary of the change over ``files``, with the default cache layout."""
    return build_summary(before, after, AffectedSet(files=set(files)), cache_paths(), aids)


def delta_of(summary: ChangeSummary, key: EntityKey) -> EntityDelta:
    """The one delta the summary carries for ``key``; fails when it carries none."""
    found = [delta for group in summary.files.values() for delta in group if delta.ref.key == key]
    assert len(found) == 1, f"expected exactly one delta for {key.longname}, got {len(found)}"
    return found[0]


def longnames(deltas: Iterable[EntityDelta]) -> list[str]:
    """The qualified names of the entities these deltas point at, in the order given."""
    return [delta.ref.key.longname for delta in deltas]


def ranking(deltas: Iterable[EntityDelta]) -> list[tuple[str, str, float]]:
    """Each ranked entry as ``(longname, metric, delta)``; every entry carries one metric."""
    rows = []
    for delta in deltas:
        (metric,) = delta.delta if delta.delta else delta.after
        rows.append((delta.ref.key.longname, metric, delta.delta.get(metric, 0.0)))
    return rows


def values(deltas: Iterable[EntityDelta]) -> list[tuple[str, str, float]]:
    """Each ranked entry as ``(longname, metric, current value)``."""
    return [
        (delta.ref.key.longname, metric, value)
        for delta in deltas
        for metric, value in delta.after.items()
    ]


# --- entity deltas per file (req 9.1) -------------------------------------------


def test_every_affected_file_is_a_group_of_its_own(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """The summary lists the change file by file, in a stable order (req 9.1)."""
    summary = build(after, before)

    assert list(summary.files) == [ENGINE, RULES, APP]


def test_a_modified_routine_carries_its_before_after_and_delta(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """``app.build_parser`` got worse; the summary shows both sides and the movement (9.1)."""
    delta = delta_of(build(after, before), BUILD_PARSER)

    assert delta.status == "modified"
    assert delta.before["CyclomaticStrict"] == 6.0
    assert delta.after["CyclomaticStrict"] == 12.0
    assert delta.delta["CyclomaticStrict"] == 6.0
    assert delta.delta["MaxNesting"] == 2.0
    assert delta.delta["CountLineCode"] == 35.0


def test_a_metric_that_did_not_move_is_absent_from_the_delta_map(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """``CountParams`` is 0 on both sides of ``build_parser``: reported, but not as movement."""
    delta = delta_of(build(after, before), BUILD_PARSER)

    assert delta.before["CountParams"] == 0.0
    assert delta.after["CountParams"] == 0.0
    assert "CountParams" not in delta.delta


def test_an_added_routine_has_no_before_values_and_a_delta_of_its_own_size(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """``app.check_command`` is new: everything it counts arrived with the change (9.1)."""
    delta = delta_of(build(after, before), CHECK_COMMAND)

    assert delta.status == "added"
    assert delta.before == {}
    assert delta.after["CountLineCode"] == 26.0
    assert delta.delta["CountLineCode"] == 26.0
    assert delta.delta["CyclomaticStrict"] == 4.0


def test_a_removed_routine_has_no_after_values_and_negative_deltas(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """``app.legacy_entry`` is gone: its metrics left the codebase (9.1)."""
    delta = delta_of(build(after, before), LEGACY_ENTRY)

    assert delta.status == "removed"
    assert delta.after == {}
    assert delta.before["CyclomaticStrict"] == 4.0
    assert delta.delta["CyclomaticStrict"] == -4.0
    assert delta.delta["CountLineCode"] == -22.0


def test_an_unchanged_entity_is_omitted(after: ProjectSnapshot, before: ProjectSnapshot) -> None:
    """``app.main`` and ``rules.apply_rules`` did not move, so they are not in the summary."""
    summary = build(after, before)

    assert longnames(summary.files[APP]) == [
        APP,
        "app.build_parser",
        "app.check_command",
        "app.legacy_entry",
    ]
    assert longnames(summary.files[RULES]) == [RULES]


def test_a_class_is_reported_when_only_its_coupling_moved(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """``engine.Engine`` changed without its file being edited: still an affected entity."""
    delta = delta_of(build(after, before), ENGINE_CLASS)

    assert delta.status == "modified"
    assert delta.delta == {"CountClassCoupled": 2.0}
    assert longnames(build(after, before).files[ENGINE]) == ["engine.Engine"]


def test_an_affected_file_whose_entities_all_stand_still_is_an_empty_group(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """The file inventory of the change is complete even where no metric moved (9.1)."""
    summary = build(after, before, files=[ADAPTER])

    assert summary.files == {ADAPTER: []}


def test_a_deleted_file_reports_every_entity_it_took_with_it(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """A file the change removed is grouped like any other, with removed entities only."""
    summary = build_summary(
        before,
        without_file(after, TEXT),
        AffectedSet(files={APP}, deleted_files={TEXT}),
        cache_paths(),
    )

    assert longnames(summary.files[TEXT]) == [TEXT, "text.wrap_lines"]
    assert {delta.status for delta in summary.files[TEXT]} == {"removed"}
    assert {delta.arch_path for delta in summary.files[TEXT]} == {"Directory Structure/src/util"}


def test_without_a_before_side_every_entity_is_new(after: ProjectSnapshot) -> None:
    """A repository with no ``HEAD`` has nothing to compare against: everything is added."""
    summary = build(after, None)

    assert {delta.status for group in summary.files.values() for delta in group} == {"added"}
    assert {delta.status for delta in summary.dependencies} == {"added"}


# --- dependency deltas (req 9.2, 9.7) -------------------------------------------


def test_added_dependencies_are_grouped_by_node_and_marked_across_boundaries(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """The three new edges, in architecture-node order, with the boundary crossings (9.2)."""
    summary = build(after, before)

    assert summary.dependencies == [
        DependencyDelta(
            src=RULES,
            dst=ENGINE,
            status="added",
            src_node=ANALYSIS_NODE,
            dst_node=ANALYSIS_NODE,
            crosses_arch=False,
        ),
        DependencyDelta(
            src=APP,
            dst=RULES,
            status="added",
            src_node=CLI_NODE,
            dst_node=ANALYSIS_NODE,
            crosses_arch=True,
        ),
        DependencyDelta(
            src=APP,
            dst=ADAPTER,
            status="added",
            src_node=CLI_NODE,
            dst_node=UNDERSTAND_NODE,
            crosses_arch=True,
        ),
    ]


def test_a_dependency_the_change_removed_is_reported_as_removed(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """Losing an edge is a dependency delta too, with the two nodes it used to join (9.2)."""
    summary = build_summary(
        before, without_edge(after, APP, TEXT), AffectedSet(files={APP}), cache_paths()
    )

    removed = [delta for delta in summary.dependencies if delta.status == "removed"]
    assert removed == [
        DependencyDelta(
            src=APP,
            dst=TEXT,
            status="removed",
            src_node=CLI_NODE,
            dst_node="Directory Structure/src/util",
            crosses_arch=True,
        )
    ]


def test_only_dependencies_touching_an_affected_file_are_listed(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """Requirement 9.2 lists edges between affected files and other files, and no others."""
    summary = build(after, before, files=[RULES])

    assert [(delta.src, delta.dst) for delta in summary.dependencies] == [
        (RULES, ENGINE),
        (APP, RULES),
    ]


def test_the_dependencies_of_one_architecture_node_are_listed_together(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """Requirement 9.2 groups the dependency diff by node, not by file path.

    ``src/util/text.py`` is moved into the ``analysis`` node and gains an edge, so node
    order and path order disagree: grouped by node its edge follows ``rules.py``'s, while
    plain path order would put it last, behind both ``cli`` edges.
    """
    moved = only_in_node(with_edge(after, TEXT, ADAPTER), TEXT, ANALYSIS_NODE)

    summary = build_summary(before, moved, AffectedSet(files={APP, RULES, TEXT}), cache_paths())

    assert [(delta.src_node, delta.src, delta.dst) for delta in summary.dependencies] == [
        (ANALYSIS_NODE, RULES, ENGINE),
        (ANALYSIS_NODE, TEXT, ADAPTER),
        (CLI_NODE, APP, RULES),
        (CLI_NODE, APP, ADAPTER),
    ]


def test_an_end_outside_every_architecture_is_not_marked_as_crossing(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """No node means no known boundary: the edge is reported, the flag stays off (9.2)."""
    vendored = "vendor/thing.py"
    summary = build_summary(
        before, with_edge(after, APP, vendored), AffectedSet(files={APP}), cache_paths()
    )

    (delta,) = [entry for entry in summary.dependencies if entry.dst == vendored]
    assert delta.src_node == CLI_NODE
    assert delta.dst_node is None
    assert delta.crosses_arch is False


def test_the_architecture_path_of_every_affected_file_is_the_one_the_summary_shows(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """The nodes on the dependency deltas are the files' own architecture paths (req 9.7)."""
    index = architecture_index(after, before)
    summary = build(after, before)

    assert index[APP] == CLI_NODE
    assert index[RULES] == ANALYSIS_NODE
    assert index[ADAPTER] == UNDERSTAND_NODE
    assert all(delta.src_node == index[delta.src] for delta in summary.dependencies)
    assert all(delta.dst_node == index[delta.dst] for delta in summary.dependencies)


def test_an_entity_delta_carries_the_architecture_path_of_its_container_file(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """Every entity inherits its file's node, so the reviewer can open it in the GUI (9.7)."""
    summary = build(after, before)

    assert delta_of(summary, BUILD_PARSER).arch_path == CLI_NODE
    assert delta_of(summary, CHECK_COMMAND).arch_path == CLI_NODE
    assert delta_of(summary, LEGACY_ENTRY).arch_path == CLI_NODE
    assert delta_of(summary, ENGINE_CLASS).arch_path == ANALYSIS_NODE
    assert [delta.arch_path for delta in summary.files[RULES]] == [ANALYSIS_NODE]


def test_an_entity_in_no_architecture_node_has_no_architecture_path(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """Requirement 9.7 is conditional: no membership, no path -- and nothing invented."""
    summary = build(outside_every_node(after, APP), outside_every_node(before, APP))

    assert delta_of(summary, BUILD_PARSER).arch_path is None
    assert delta_of(summary, ENGINE_CLASS).arch_path == ANALYSIS_NODE


def test_a_ranking_row_carries_the_architecture_path_too(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """A reviewer starting at the top of the ranking is told where to look (req 9.3, 9.7)."""
    summary = build(after, before, aids=ReviewAids(top_n=5))

    assert [row.arch_path for row in summary.top_by_delta] == [CLI_NODE] * 5
    assert {row.arch_path for row in summary.top_by_value} == {CLI_NODE, ANALYSIS_NODE}


def test_the_architecture_path_survives_the_json_round_trip(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """``explain --format json`` must carry the path, not only the in-process object (9.6)."""
    summary = build(after, before)

    wire = json.loads(summary.model_dump_json())
    restored = ChangeSummary.model_validate(wire)

    assert wire["files"][APP][0]["arch_path"] == CLI_NODE
    assert delta_of(restored, BUILD_PARSER).arch_path == CLI_NODE


def test_the_architecture_index_answers_for_a_file_the_change_deleted(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """A file only the before side knows still has an architecture path (req 9.7)."""
    index = architecture_index(without_file(after, TEXT), before)

    assert index[TEXT] == "Directory Structure/src/util"


def test_a_file_the_change_added_to_an_architecture_is_located_by_the_after_side(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """A file the pre-change architecture never held still has a path to show (req 9.7)."""
    wizard = "src/cli/wizard.py"

    index = architecture_index(also_in_node(after, wizard, CLI_NODE), before)

    assert index[wizard] == CLI_NODE


def test_the_after_side_wins_where_a_file_moved_between_nodes(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """Where a file sits *now* is where the reviewer opens the GUI to find it (req 9.7)."""
    index = architecture_index(only_in_node(after, TEXT, CLI_NODE), before)

    assert index[TEXT] == CLI_NODE


def test_a_file_in_several_nodes_takes_the_first_node_in_sorted_order(
    after: ProjectSnapshot,
) -> None:
    """One path per file, picked the same way on every run (req 9.7)."""
    index = architecture_index(also_in_node(after, TEXT, ANALYSIS_NODE))

    assert index[TEXT] == ANALYSIS_NODE


def test_a_file_in_no_architecture_node_has_no_architecture_path(
    after: ProjectSnapshot,
) -> None:
    """Membership is not invented for a file the architecture does not contain (req 9.7)."""
    assert architecture_index(after).get("vendor/thing.py") is None


# --- rankings (req 9.3) ---------------------------------------------------------


def test_top_by_delta_ranks_the_largest_metric_movements_first(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """The riskiest movements first, one entry per entity and metric (req 9.3).

    Ties (``build_parser`` moved 28 on two metrics) break on the metric name, so the order
    is the same on every run.
    """
    summary = build(after, before, aids=ReviewAids(top_n=5))

    assert ranking(summary.top_by_delta) == [
        (APP, "CountLineCode", 40.0),
        ("app.build_parser", "CountLineCode", 35.0),
        ("app.build_parser", "CountPath", 28.0),
        ("app.build_parser", "CountStmt", 28.0),
        ("app.check_command", "CountLineCode", 26.0),
    ]


def test_a_large_removal_outranks_a_smaller_addition(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """Ranking is by the MAGNITUDE of the movement, so a big drop outranks a small rise.

    Requirement 9.3 ranks by how far a metric moved so a reviewer starts with the riskiest
    parts; deleting 22 lines is a bigger change than adding 6, and a signed ordering would
    bury every removal below every addition.
    """
    summary = build(after, before, aids=ReviewAids(top_n=DEFAULT_TOP_N))
    ranked = ranking(summary.top_by_delta)
    positions = {(longname, metric): index for index, (longname, metric, _) in enumerate(ranked)}

    assert ("app.legacy_entry", "CountLineCode") in positions
    assert ("src/analysis/rules.py", "CountLineCode") in positions
    assert (
        positions[("app.legacy_entry", "CountLineCode")]
        < positions[("src/analysis/rules.py", "CountLineCode")]
    )
    assert (
        positions[("app.legacy_entry", "CountStmt")]
        < positions[("src/analysis/rules.py", "CountLineCode")]
    )


def test_ranked_entries_of_equal_size_are_ordered_by_metric_name(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """Two metrics of one entity at the same value sort alphabetically (req 9.3).

    ``top_by_value`` walks ``delta.after``, whose order comes from the worker's metric
    emission rather than a sorted map, so without the tie-break the ranking would silently
    follow whatever order the upstream snapshot happened to use.
    """
    summary = build(after, before, aids=ReviewAids(top_n=100))
    engine = [row for row in values(summary.top_by_value) if row[0] == "engine.Engine"]

    tied = [(metric, value) for _, metric, value in engine if value == 6.0]
    assert tied == sorted(tied)
    assert [metric for metric, _ in tied] == ["CountClassCoupled", "CountDeclMethod"]


def test_a_ranked_entry_carries_only_the_metric_it_is_ranked_on(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """Each ranking row is self-contained: entity, status, that metric's before/after/delta."""
    summary = build(after, before, aids=ReviewAids(top_n=1))

    (top,) = summary.top_by_delta
    assert top.ref.key.path == APP
    assert top.status == "modified"
    assert top.before == {"CountLineCode": 120.0}
    assert top.after == {"CountLineCode": 160.0}
    assert top.delta == {"CountLineCode": 40.0}


def test_top_by_value_ranks_the_largest_values_the_change_leaves_behind(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """Largest absolute values, which is a different order from the largest deltas (9.3)."""
    summary = build(after, before, aids=ReviewAids(top_n=5))

    assert values(summary.top_by_value) == [
        (APP, "CountLineCode", 160.0),
        (RULES, "CountLineCode", 96.0),
        ("app.build_parser", "CountLineCode", 75.0),
        ("app.build_parser", "CountStmt", 58.0),
        ("app.build_parser", "CountPath", 40.0),
    ]


def test_a_removed_entity_has_no_value_left_to_rank(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """``app.legacy_entry`` ranks by delta but not by value: it is gone (req 9.3)."""
    summary = build(after, before, aids=ReviewAids(top_n=100))

    assert "app.legacy_entry" in longnames(summary.top_by_delta)
    assert "app.legacy_entry" not in longnames(summary.top_by_value)


def test_a_metric_that_did_not_move_is_no_candidate_for_the_delta_ranking(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """A ranking of movements contains movements only, however wide it is (req 9.3)."""
    summary = build(after, before, aids=ReviewAids(top_n=100))

    assert all(entry.delta and all(entry.delta.values()) for entry in summary.top_by_delta)
    assert ("app.build_parser", "CountParams") not in [
        (entry.ref.key.longname, metric) for entry in summary.top_by_delta for metric in entry.delta
    ]


def test_both_rankings_stop_at_the_requested_width(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """``top_n`` is a cap on both lists (req 9.3)."""
    summary = build(after, before, aids=ReviewAids(top_n=3))

    assert len(summary.top_by_delta) == 3
    assert len(summary.top_by_value) == 3


def test_a_width_of_zero_ranks_nothing(after: ProjectSnapshot, before: ProjectSnapshot) -> None:
    """A reviewer who asks for no ranking gets none, not the whole list."""
    summary = build(after, before, aids=ReviewAids(top_n=0))

    assert summary.top_by_delta == []
    assert summary.top_by_value == []


def test_the_default_width_applies_when_no_aids_are_given(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """The change moves far more than ten metrics, so the default cap is visible."""
    summary = build(after, before)

    assert DEFAULT_TOP_N == 10
    assert len(summary.top_by_delta) == DEFAULT_TOP_N
    assert len(summary.top_by_value) == DEFAULT_TOP_N


# --- impact, graphs, database (req 9.5, 9.8) ------------------------------------


def test_impact_and_graphs_are_attached_as_given(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """The builder computes neither; it carries what the pipeline hands it (req 9.4, 9.5)."""
    caller = EntityRef(key=CHECK_COMMAND, kind="Python Function", name="check_command")
    impact = {BUILD_PARSER: ImpactSet(by_depth={1: [caller]}, total=1)}
    graph = GraphFile(key=BUILD_PARSER, graph="Butterfly", path=Path("review/build_parser.svg"))

    summary = build(after, before, aids=ReviewAids(impact=impact, graphs=[graph]))

    assert summary.impact == impact
    assert summary.graphs == [graph]


def test_graphs_are_listed_in_a_stable_order(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """Two runs of ``explain`` must produce the same document (req 9.4)."""
    butterfly = GraphFile(key=BUILD_PARSER, graph="Butterfly", path=Path("review/b.svg"))
    depends = GraphFile(
        key=EntityKey(scope="file", path=APP, longname=APP),
        graph="Depends On",
        path=Path("review/d.svg"),
    )

    summary = build(after, before, aids=ReviewAids(graphs=[butterfly, depends]))

    assert summary.graphs == [depends, butterfly]


def test_the_database_path_and_the_open_command_come_from_the_cache_layout(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """The reviewer is told where the database is and how to open it (req 9.8)."""
    summary = build(after, before)

    assert summary.db_path == "/cache/repo/after.und"
    assert summary.open_command == f"{GUI_EXECUTABLE} /cache/repo/after.und"


def test_the_open_command_quotes_a_path_that_needs_it() -> None:
    """A cache under a directory with a space is still one pasteable command (req 9.8)."""
    assert open_command(cache_paths(Path("/my cache/repo"))) == (
        "understand '/my cache/repo/after.und'"
    )


# --- the whole document ---------------------------------------------------------


def test_the_summary_round_trips_through_json(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """``explain --format json`` must be able to write and read this document back (9.6)."""
    caller = EntityRef(key=CHECK_COMMAND, kind="Python Function", name="check_command")
    graph = GraphFile(key=BUILD_PARSER, graph="Butterfly", path=Path("review/b.svg"))
    summary = build(
        after,
        before,
        aids=ReviewAids(
            impact={BUILD_PARSER: ImpactSet(by_depth={1: [caller]}, total=1)}, graphs=[graph]
        ),
    )

    restored = ChangeSummary.model_validate(json.loads(summary.model_dump_json()))

    assert restored == summary


def test_two_runs_over_the_same_change_produce_the_same_document(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """Determinism is what makes a summary reviewable in a merge request (req 9.6)."""
    assert build(after, before).model_dump_json() == build(after, before).model_dump_json()


# --- snapshot surgery for the cases the fixtures do not contain -----------------


def without_file(snapshot: ProjectSnapshot, path: str) -> ProjectSnapshot:
    """``snapshot`` with ``path`` deleted: no entities, no edges, no architecture member."""
    return snapshot.model_copy(
        update={
            "entities": {
                key: record for key, record in snapshot.entities.items() if key.path != path
            },
            "file_edges": [
                edge for edge in snapshot.file_edges if path not in (edge.src, edge.dst)
            ],
            "arch_nodes": [
                node.model_copy(update={"members": [m for m in node.members if m != path]})
                for node in snapshot.arch_nodes
            ],
        }
    )


def without_edge(snapshot: ProjectSnapshot, src: str, dst: str) -> ProjectSnapshot:
    """``snapshot`` with the dependency from ``src`` to ``dst`` gone."""
    kept = [edge for edge in snapshot.file_edges if (edge.src, edge.dst) != (src, dst)]
    return snapshot.model_copy(update={"file_edges": kept})


def outside_every_node(snapshot: ProjectSnapshot, path: str) -> ProjectSnapshot:
    """``snapshot`` with ``path`` a member of no architecture node at all."""
    nodes = [
        node.model_copy(update={"members": [m for m in node.members if m != path]})
        for node in snapshot.arch_nodes
    ]
    return snapshot.model_copy(update={"arch_nodes": nodes})


def only_in_node(snapshot: ProjectSnapshot, path: str, node_path: str) -> ProjectSnapshot:
    """``snapshot`` with ``path`` a member of ``node_path`` and of no other node."""
    nodes = []
    for node in snapshot.arch_nodes:
        members = [member for member in node.members if member != path]
        kept = sorted([*members, path]) if node.path == node_path else members
        nodes.append(node.model_copy(update={"members": kept}))
    return snapshot.model_copy(update={"arch_nodes": nodes})


def also_in_node(snapshot: ProjectSnapshot, path: str, node_path: str) -> ProjectSnapshot:
    """``snapshot`` with ``path`` a member of ``node_path`` as well as wherever it already is."""
    nodes = [
        node.model_copy(update={"members": sorted([*node.members, path])})
        if node.path == node_path
        else node
        for node in snapshot.arch_nodes
    ]
    return snapshot.model_copy(update={"arch_nodes": nodes})


def with_edge(snapshot: ProjectSnapshot, src: str, dst: str) -> ProjectSnapshot:
    """``snapshot`` with one more dependency from ``src`` to ``dst``."""
    added = DepEdge(src=src, dst=dst, refs=1)
    return snapshot.model_copy(update={"file_edges": [*snapshot.file_edges, added]})
