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


@pytest.mark.parametrize(
    "token",
    ["not json", "{}", "[1, 2]", '["routine", "a.py"]', '["routine","a.py","a.f","",0,1]'],
)
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


def test_unparsed_files_names_the_repository_relative_paths_that_failed() -> None:
    """What the ratchet asks the before side: was this file read at all (tasks 11.9, 11.11)?

    The paths have to be comparable with ``EntityKey.path``, which is repository-relative, and
    ``DatabaseManager`` normalises ``ParseError.path`` to that form for exactly this reason.
    """
    snapshot = _snapshot()
    assert snapshot.unparsed_files == frozenset({"src/cli/app.py"})
    assert MAIN_KEY.path in snapshot.unparsed_files


def test_unparsed_files_leaves_a_path_outside_the_repository_matching_nothing() -> None:
    """A standard-library error keeps its absolute name, so no entity of this project matches it.

    The other half of the same decision, with a different input: task 10.4 measured four parse
    errors in the interpreter's own files on a clean run, and a ratchet that treated them as
    repository paths would exempt whatever happened to share the basename.
    """
    stdlib = ProjectSnapshot(
        side="before",
        parse_errors=[ParseError(path=Path("/usr/lib/python3.12/inspect.py"), message="boom")],
    )
    assert stdlib.unparsed_files == frozenset({"/usr/lib/python3.12/inspect.py"})
    assert MAIN_KEY.path not in stdlib.unparsed_files


def test_a_snapshot_that_parsed_cleanly_names_nothing() -> None:
    """The empty answer is an answer, and it must not be a truthy set."""
    assert ProjectSnapshot(side="after").unparsed_files == frozenset()


# --- keys the first four fields cannot separate (task 11.6) ----------------------

DUP_KEY = EntityKey(scope="routine", path="pkg/dup.py", longname="dup.same", parameters="x")
"""``def same(x)`` written twice: two entities Understand reports and one key names.

Measured against the licensed Understand in ``tests/contract/test_entity_key_contract.py``;
``@typing.overload`` produces the same collision three deep.
"""


def _wire(key: EntityKey, line: int, cyclomatic: float) -> dict[str, object]:
    """One entity record in the wire form the worker answers with: no ordinal, ever."""
    return {
        "ref": {"key": key.model_dump(), "kind": "Python Function", "name": "same", "line": line},
        "language": "Python",
        "metrics": {"CyclomaticStrict": cyclomatic},
        "archs": [],
    }


def _duplicated(*lines: int) -> ProjectSnapshot:
    """A snapshot whose wire form holds one record per line, all under :data:`DUP_KEY`."""
    return ProjectSnapshot.model_validate(
        {"side": "after", "entities": [_wire(DUP_KEY, line, float(line)) for line in lines]}
    )


def test_family_is_the_identity_a_signature_change_leaves_alone() -> None:
    assert MAIN_KEY.family == ("routine", "src/cli/app.py", "app.main")
    assert MAIN_KEY.family == OVERLOAD_KEY.family
    assert MAIN_KEY != OVERLOAD_KEY


def test_two_records_under_one_key_are_both_kept() -> None:
    """The silent drop this task removed: a mapping that discards a record hides an entity.

    ``{record.key: record}`` kept the last of the two and the first was never measured
    against a threshold, never ratcheted and never counted -- on both sides, so no rule could
    notice. Both arrive now, and the metrics say the two are really the two.
    """
    snapshot = _duplicated(4, 9)

    assert len(snapshot.entities) == 2
    assert {record.metrics["CyclomaticStrict"] for record in snapshot.entities.values()} == {
        4.0,
        9.0,
    }


def test_the_records_under_one_key_are_numbered_in_file_order() -> None:
    """The ordinal has to mean the same on both sides, so it follows the line, not the walk.

    The two records are handed over *last line first*; the numbering still puts line 4 at
    ordinal 0, which is what lets a before snapshot and an after snapshot that walked the
    database in different orders still compare the same two routines.
    """
    snapshot = _duplicated(9, 4)

    numbered = {key.ordinal: snapshot.entities[key].ref.line for key in snapshot.entities}
    assert numbered == {0: 4, 1: 9}


def test_a_numbered_record_carries_the_key_it_is_stored_under() -> None:
    """The mapping's invariant survives the numbering, ref included."""
    snapshot = _duplicated(4, 9)

    assert all(key == record.ref.key for key, record in snapshot.entities.items())
    assert {key.ordinal for key in snapshot.entities} == {0, 1}


def test_a_single_record_keeps_the_ordinal_zero_the_worker_sent() -> None:
    """The sibling of the two cases above, differing only in how many records arrive."""
    snapshot = _duplicated(4)

    (key,) = snapshot.entities
    assert key == DUP_KEY
    assert key.ordinal == 0


def test_numbered_records_survive_a_round_trip_through_the_wire_form() -> None:
    """Serialising a numbered snapshot and reading it back must not renumber or merge it."""
    snapshot = _duplicated(4, 9)

    restored = ProjectSnapshot.model_validate(json.loads(snapshot.model_dump_json()))

    assert restored == snapshot
    assert len(restored.entities) == 2


def test_a_zero_ordinal_is_left_out_of_the_wire_form() -> None:
    """``understand.worker`` may not import this package and writes four fields; so does this.

    A ``0`` no worker can produce would break the agreement the worker tests hold the two
    forms to, and would rewrite every cached snapshot and stored baseline for a field that
    says "never ambiguous".
    """
    assert MAIN_KEY.model_dump() == {
        "scope": "routine",
        "path": "src/cli/app.py",
        "longname": "app.main",
        "parameters": "argv",
    }


def test_a_numbered_key_writes_its_ordinal() -> None:
    """The sibling: the same key with an ordinal that carries information keeps it."""
    numbered = MAIN_KEY.model_copy(update={"ordinal": 2})

    assert numbered.model_dump()["ordinal"] == 2
    assert EntityKey.model_validate(numbered.model_dump()) == numbered


def test_the_token_of_a_zero_ordinal_key_is_the_four_element_form() -> None:
    """Class edge endpoints and stored baselines hold four element tokens; they still match."""
    assert json.loads(MAIN_KEY.token) == ["routine", "src/cli/app.py", "app.main", "argv"]
    assert EntityKey.from_token(MAIN_KEY.token) == MAIN_KEY


def test_the_token_of_a_numbered_key_carries_the_ordinal_and_round_trips() -> None:
    numbered = MAIN_KEY.model_copy(update={"ordinal": 2})

    assert json.loads(numbered.token) == ["routine", "src/cli/app.py", "app.main", "argv", 2]
    assert EntityKey.from_token(numbered.token) == numbered
    assert numbered.token != MAIN_KEY.token


def test_a_negative_ordinal_is_rejected() -> None:
    with pytest.raises(ValidationError):
        EntityKey(scope="routine", path="a.py", longname="a.f", parameters="", ordinal=-1)
