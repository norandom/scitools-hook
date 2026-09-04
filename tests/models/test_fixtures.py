"""The synthetic before/after snapshots load, validate and exercise every rule (task 3.1)."""

from __future__ import annotations

import json
from statistics import mean

import pytest
from fixtures import (
    ADAPTER,
    APP,
    BUILD_PARSER,
    ENGINE,
    ENGINE_CLASS,
    RULES,
    TEXT,
    Side,
    snapshot_fixture,
    snapshot_path,
)

from scitools_hook.models.snapshot import EntityKey, ProjectSnapshot

CHECK_COMMAND = EntityKey(
    scope="routine", path=APP, longname="app.check_command", parameters="args"
)
LEGACY_ENTRY = EntityKey(scope="routine", path=APP, longname="app.legacy_entry", parameters="")
MAIN = EntityKey(scope="routine", path=APP, longname="app.main", parameters="argv")
APP_FILE = EntityKey(scope="file", path=APP, longname=APP)


@pytest.fixture
def before() -> ProjectSnapshot:
    return snapshot_fixture("before")


@pytest.fixture
def after() -> ProjectSnapshot:
    return snapshot_fixture("after")


def _fan_out(snapshot: ProjectSnapshot, path: str) -> set[str]:
    return {edge.dst for edge in snapshot.file_edges if edge.src == path}


# --- loading -------------------------------------------------------------------


@pytest.mark.parametrize("side", ["before", "after"])
def test_fixture_validates_into_a_project_snapshot(side: Side) -> None:
    snapshot = snapshot_fixture(side)
    assert snapshot.side == side
    assert snapshot.languages == ["Python"]
    assert len(snapshot.entities) >= 14


@pytest.mark.parametrize("side", ["before", "after"])
def test_fixture_file_is_the_canonical_wire_form(side: Side) -> None:
    on_disk = json.loads(snapshot_path(side).read_text(encoding="utf-8"))
    assert json.loads(snapshot_fixture(side).model_dump_json()) == on_disk


@pytest.mark.parametrize("side", ["before", "after"])
def test_every_record_is_stored_under_its_own_key(side: Side) -> None:
    snapshot = snapshot_fixture(side)
    assert all(key == record.ref.key for key, record in snapshot.entities.items())


@pytest.mark.parametrize("side", ["before", "after"])
def test_every_entity_carries_the_architecture_path_of_its_file(side: Side) -> None:
    snapshot = snapshot_fixture(side)
    members = {member: node.path for node in snapshot.arch_nodes for member in node.members}
    for record in snapshot.entities.values():
        assert record.archs == [members[record.ref.key.path]]


# --- entity-level change -------------------------------------------------------


def test_added_removed_modified_and_unchanged_entities(
    before: ProjectSnapshot, after: ProjectSnapshot
) -> None:
    assert CHECK_COMMAND in after.entities and CHECK_COMMAND not in before.entities
    assert LEGACY_ENTRY in before.entities and LEGACY_ENTRY not in after.entities
    assert before.entities[MAIN].metrics == after.entities[MAIN].metrics
    assert before.entities[BUILD_PARSER].metrics != after.entities[BUILD_PARSER].metrics


def test_only_the_added_entity_is_marked_new(
    before: ProjectSnapshot, after: ProjectSnapshot
) -> None:
    assert after.entities[CHECK_COMMAND].is_new is True
    assert [key for key, record in after.entities.items() if record.is_new] == [CHECK_COMMAND]
    assert not any(record.is_new for record in before.entities.values())


def test_the_modified_routine_got_worse(before: ProjectSnapshot, after: ProjectSnapshot) -> None:
    was = before.entities[BUILD_PARSER].metrics
    now = after.entities[BUILD_PARSER].metrics
    assert (was["CyclomaticStrict"], now["CyclomaticStrict"]) == (6.0, 12.0)
    assert (was["MaxNesting"], now["MaxNesting"]) == (2.0, 4.0)
    assert (was["CountLineCode"], now["CountLineCode"]) == (40.0, 75.0)


def test_a_class_changes_although_its_own_file_did_not(
    before: ProjectSnapshot, after: ProjectSnapshot
) -> None:
    assert before.entities[ENGINE_CLASS].metrics["CountClassCoupled"] == 4.0
    assert after.entities[ENGINE_CLASS].metrics["CountClassCoupled"] == 6.0


def test_the_changed_file_reports_a_worse_maximum(
    before: ProjectSnapshot, after: ProjectSnapshot
) -> None:
    assert before.entities[APP_FILE].metrics["MaxCyclomaticStrict"] == 6.0
    assert after.entities[APP_FILE].metrics["MaxCyclomaticStrict"] == 12.0


# --- structural change ---------------------------------------------------------


def test_a_file_cycle_exists_only_after_the_change(
    before: ProjectSnapshot, after: ProjectSnapshot
) -> None:
    def pairs(snapshot: ProjectSnapshot) -> set[tuple[str, str]]:
        return {(edge.src, edge.dst) for edge in snapshot.file_edges}

    assert (ENGINE, RULES) in pairs(before)
    assert (RULES, ENGINE) not in pairs(before)
    assert {(ENGINE, RULES), (RULES, ENGINE)} <= pairs(after)


def test_a_layer_violating_edge_appears_only_after_the_change(
    before: ProjectSnapshot, after: ProjectSnapshot
) -> None:
    assert ADAPTER not in _fan_out(before, APP)
    violating = [e for e in after.file_edges if e.src == APP and e.dst == ADAPTER]
    assert len(violating) == 1
    assert violating[0].crosses_arch is True


def test_the_layer_violation_shows_up_between_architecture_nodes(
    before: ProjectSnapshot, after: ProjectSnapshot
) -> None:
    def arch_pairs(snapshot: ProjectSnapshot) -> set[tuple[str, str]]:
        return {(edge.src, edge.dst) for edge in snapshot.arch_edges}

    cli, understand = "Directory Structure/src/cli", "Directory Structure/src/understand"
    assert (cli, understand) not in arch_pairs(before)
    assert (cli, understand) in arch_pairs(after)


def test_fan_out_of_the_changed_file_grows(before: ProjectSnapshot, after: ProjectSnapshot) -> None:
    assert _fan_out(before, APP) == {ENGINE, TEXT}
    assert _fan_out(after, APP) == {ENGINE, TEXT, RULES, ADAPTER}


def test_class_edges_use_the_entity_key_token(after: ProjectSnapshot) -> None:
    edge = after.class_edges[0]
    assert EntityKey.from_token(edge.src) == ENGINE_CLASS
    assert EntityKey.from_token(edge.dst).longname == "adapter.Adapter"


def test_architecture_nodes_sit_at_depth_two(after: ProjectSnapshot) -> None:
    assert {node.path for node in after.arch_nodes} == {
        "Directory Structure/src/analysis",
        "Directory Structure/src/cli",
        "Directory Structure/src/understand",
        "Directory Structure/src/util",
    }
    assert all(node.depth == 2 for node in after.arch_nodes)
    members = {member for node in after.arch_nodes for member in node.members}
    assert members == {APP, ENGINE, RULES, ADAPTER, TEXT}


# --- populations and run-level records -----------------------------------------


@pytest.mark.parametrize("side", ["before", "after"])
def test_populations_back_the_stats_prefixed_defaults(side: Side) -> None:
    snapshot = snapshot_fixture(side)
    routines = [key for key in snapshot.entities if key.scope == "routine"]
    assert len(snapshot.populations["project"]["CyclomaticStrict"]) == len(routines)
    assert (
        snapshot.populations["routine"]["CyclomaticStrict"]
        == (snapshot.populations["project"]["CyclomaticStrict"])
    )
    assert len(snapshot.populations["file"]["CountLineCode"]) == 5


def test_the_average_complexity_rises_with_the_change(
    before: ProjectSnapshot, after: ProjectSnapshot
) -> None:
    was = mean(before.populations["project"]["CyclomaticStrict"])
    now = mean(after.populations["project"]["CyclomaticStrict"])
    assert now > was > 3.0


@pytest.mark.parametrize("side", ["before", "after"])
def test_unavailable_metrics_are_recorded_per_language(side: Side) -> None:
    assert snapshot_fixture(side).unavailable == {"Python": ["PercentLackOfCohesion"]}


def test_only_the_after_side_carries_a_parse_error(
    before: ProjectSnapshot, after: ProjectSnapshot
) -> None:
    assert before.parse_errors == []
    assert [str(error.path) for error in after.parse_errors] == [RULES]
    assert after.parse_errors[0].line == 41
