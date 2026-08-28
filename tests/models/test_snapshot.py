"""Snapshot models: entity identity, records, edges, the entities wire form (task 3.1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from scitools_hook.models.snapshot import (
    ArchNode,
    DepEdge,
    EntityKey,
    EntityRecord,
    EntityRef,
    ParseError,
    ProjectSnapshot,
)

MAIN_KEY = EntityKey(scope="routine", path="src/cli/app.py", longname="app.main", parameters="argv")
OVERLOAD_KEY = EntityKey(
    scope="routine", path="src/cli/app.py", longname="app.main", parameters="argv, env"
)
FILE_KEY = EntityKey(scope="file", path="src/cli/app.py", longname="src/cli/app.py")


def _record(key: EntityKey, cyclomatic: float = 3.0) -> EntityRecord:
    return EntityRecord(
        ref=EntityRef(
            key=key, kind="Python Function", name=key.longname.rsplit(".", 1)[-1], line=12
        ),
        language="Python",
        metrics={"CyclomaticStrict": cyclomatic},
        archs=["Directory Structure/src/cli"],
    )


# --- EntityKey identity --------------------------------------------------------


def test_entity_key_is_frozen() -> None:
    with pytest.raises(ValidationError):
        MAIN_KEY.path = "other.py"


def test_entity_key_is_hashable_and_usable_in_dicts_and_sets() -> None:
    same = EntityKey(scope="routine", path="src/cli/app.py", longname="app.main", parameters="argv")
    assert hash(same) == hash(MAIN_KEY)
    assert {MAIN_KEY: 1}[same] == 1
    assert {MAIN_KEY, same, OVERLOAD_KEY} == {MAIN_KEY, OVERLOAD_KEY}


def test_parameters_distinguish_overloads() -> None:
    assert MAIN_KEY != OVERLOAD_KEY


def test_parameters_default_to_none() -> None:
    assert FILE_KEY.parameters is None


def test_token_round_trips_through_from_token() -> None:
    for key in (MAIN_KEY, OVERLOAD_KEY, FILE_KEY):
        assert EntityKey.from_token(key.token) == key


def test_token_survives_separator_characters_in_names() -> None:
    awkward = EntityKey(
        scope="class",
        path='src/odd,name/"weird".py',
        longname="pkg.Cls|inner:thing",
        parameters="a, b",
    )
    assert EntityKey.from_token(awkward.token) == awkward


def test_token_is_deterministic() -> None:
    assert MAIN_KEY.token == MAIN_KEY.token
    assert MAIN_KEY.token != OVERLOAD_KEY.token


@pytest.mark.parametrize("token", ["not json", "{}", "[1, 2]", '["routine", "a.py"]'])
def test_from_token_rejects_malformed_tokens(token: str) -> None:
    with pytest.raises(ValueError):
        EntityKey.from_token(token)


def test_unknown_scope_is_rejected() -> None:
    with pytest.raises(ValidationError):
        EntityKey(scope="module", path="a.py", longname="a")  # type: ignore[arg-type]


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        EntityKey.model_validate(
            {"scope": "file", "path": "a.py", "longname": "a.py", "uniquename": "@la"}
        )


# --- records, edges, architecture ----------------------------------------------


def test_entity_record_exposes_its_key() -> None:
    assert _record(MAIN_KEY).key == MAIN_KEY


def test_entity_record_defaults() -> None:
    record = EntityRecord(
        ref=EntityRef(key=FILE_KEY, kind="Python File", name="app.py"), language="Python"
    )
    assert record.metrics == {}
    assert record.archs == []
    assert record.is_new is False
    assert record.ref.line is None


def test_dep_edge_defaults_to_not_crossing_an_architecture_boundary() -> None:
    edge = DepEdge(src="src/cli/app.py", dst="src/util/text.py", refs=2)
    assert edge.crosses_arch is False


def test_dep_edge_rejects_negative_reference_counts() -> None:
    with pytest.raises(ValidationError):
        DepEdge(src="a.py", dst="b.py", refs=-1)


def test_arch_node_depth_counts_components_below_the_architecture_root() -> None:
    assert ArchNode(path="Directory Structure/src/cli", members=["src/cli/app.py"]).depth == 2
    assert ArchNode(path="Directory Structure").depth == 0


def test_parse_error_keeps_path_and_optional_line() -> None:
    error = ParseError(path=Path("src/cli/app.py"), line=41, message="unexpected token")
    assert error.path == Path("src/cli/app.py")
    assert ParseError(path=Path("a.py"), message="boom").line is None


# --- ProjectSnapshot -----------------------------------------------------------


def _snapshot() -> ProjectSnapshot:
    return ProjectSnapshot(
        side="after",
        languages=["Python"],
        entities={MAIN_KEY: _record(MAIN_KEY), FILE_KEY: _record(FILE_KEY, 6.0)},
        file_edges=[
            DepEdge(src="src/cli/app.py", dst="src/util/text.py", refs=2, crosses_arch=True)
        ],
        arch_nodes=[ArchNode(path="Directory Structure/src/cli", members=["src/cli/app.py"])],
        populations={"routine": {"CyclomaticStrict": [3.0, 6.0]}},
        unavailable={"Python": ["PercentLackOfCohesion"]},
        parse_errors=[ParseError(path=Path("src/cli/app.py"), line=41, message="unexpected token")],
    )


def test_snapshot_defaults_are_empty() -> None:
    empty = ProjectSnapshot(side="before")
    assert empty.entities == {}
    assert empty.file_edges == []
    assert empty.class_edges == []
    assert empty.arch_nodes == []
    assert empty.arch_edges == []
    assert empty.populations == {}
    assert empty.unavailable == {}
    assert empty.parse_errors == []
    assert empty.languages == []


def test_entities_serialize_as_a_list_of_records_keyed_by_the_record_key() -> None:
    wire = json.loads(_snapshot().model_dump_json())
    assert isinstance(wire["entities"], list)
    assert [item["ref"]["key"]["longname"] for item in wire["entities"]] == [
        "src/cli/app.py",
        "app.main",
    ]


def test_entities_round_trip_through_json() -> None:
    snapshot = _snapshot()
    restored = ProjectSnapshot.model_validate(json.loads(snapshot.model_dump_json()))
    assert restored == snapshot
    assert restored.entities[MAIN_KEY].metrics == {"CyclomaticStrict": 3.0}


def test_entities_round_trip_through_python_mode_dump() -> None:
    snapshot = _snapshot()
    assert ProjectSnapshot.model_validate(snapshot.model_dump()) == snapshot


def test_entities_accept_the_mapping_form_used_in_python() -> None:
    snapshot = _snapshot()
    assert set(snapshot.entities) == {MAIN_KEY, FILE_KEY}


def test_entities_reject_a_key_that_disagrees_with_its_record() -> None:
    with pytest.raises(ValidationError):
        ProjectSnapshot(side="after", entities={OVERLOAD_KEY: _record(MAIN_KEY)})


def test_snapshot_side_is_restricted() -> None:
    with pytest.raises(ValidationError):
        ProjectSnapshot(side="middle")  # type: ignore[arg-type]
