"""The shapes the lean-code family adds to the shared models (task 1.2).

Four questions, and every one of them is about *absence*:

* a fact nobody measured must read as ``None`` and never as zero or as an empty list, which
  is the three-state discipline ``EntityRecord.referenced`` already follows (req 1.6, 2.5);
* a snapshot document written before this specification existed must still validate, because
  the analysis cache holds them and a stale document is read, not rewritten (req 9.6);
* the nine new structural rule names must be legal wherever a rule name is legal -- the
  severity map, the SARIF rule id and the hint catalogue all key on the same grammar (9.6);
* ``RunResult.schema_version`` stays ``2``, because every field this task adds is additive
  and ``report/json_out.py`` bumps only for a field whose *meaning* changed (req 7.1).
"""

from __future__ import annotations

import json
from typing import Final

import pytest
from fixtures import snapshot_fixture
from pydantic import BaseModel, ValidationError

from scitools_hook.models.change import NetDelta
from scitools_hook.models.findings import (
    STRUCTURE_RULES,
    RunResult,
    StructureRuleName,
    is_valid_rule_name,
    parse_rule_name,
    structure_rule,
)
from scitools_hook.models.snapshot import (
    Definition,
    EntityKey,
    EntityRecord,
    EntityRef,
    LeanFacts,
    ProjectSnapshot,
    RoutineShape,
    TokenIndex,
)
from scitools_hook.models.understand import ExtractRequest, Feature

LEAN_RULES: Final[tuple[StructureRuleName, ...]] = (
    "unused_parameter",
    "unused_class",
    "unused_variable",
    "pass_through",
    "single_implementation",
    "over_export",
    "duplicate_block",
    "similar_routine",
    "net_growth",
)
"""The nine names the design's ``StructureRuleName`` block adds, in its order."""

KEY = EntityKey(scope="routine", path="src/cli/app.py", longname="app.main", parameters="argv")
REF = EntityRef(key=KEY, kind="Python Function", name="main", line=12)


def _record(**overrides: object) -> EntityRecord:
    fields: dict[str, object] = {"ref": REF, "language": "Python"}
    fields.update(overrides)
    return EntityRecord.model_validate(fields)


def _run_result(**overrides: object) -> RunResult:
    fields: dict[str, object] = {
        "tool_version": "0.1.0",
        "understand_version": "8.0.1262",
        "repo_root": "/home/dev/project",
        "selection": "staged",
        "started_at": "2026-09-09T10:00:00+00:00",
        "seconds": 4.5,
    }
    fields.update(overrides)
    return RunResult.model_validate(fields)


def _round_trip[T: BaseModel](model: T) -> T:
    """The model as it comes back from its own JSON, which is how every side crosses."""
    return type(model).model_validate_json(model.model_dump_json(warnings="error"))


# --- per-entity lean facts (req 1.6, 2.5) -------------------------------------------


def test_lean_facts_measure_nothing_until_something_measures_them() -> None:
    """Every field is ``None``: "the worker was not asked", never "none found"."""
    facts = LeanFacts()

    assert facts.callers is None
    assert facts.callees is None
    assert facts.forwards_to is None
    assert facts.overrides is None
    assert facts.unused_parameters is None
    assert facts.referenced is None
    assert facts.derived is None
    assert facts.referrers is None


def test_lean_facts_round_trip_with_every_fact_present() -> None:
    facts = LeanFacts(
        callers=1,
        callees=1,
        forwards_to="engine.run",
        overrides=False,
        unused_parameters=["verbose"],
        referenced=True,
        derived=["engine.FastEngine"],
        referrers=0,
    )

    assert _round_trip(facts) == facts


def test_lean_facts_round_trip_while_they_are_absent() -> None:
    assert _round_trip(LeanFacts()) == LeanFacts()


def test_an_entity_record_without_lean_facts_says_nothing_about_them() -> None:
    """The default is the state every record written before this task is in."""
    record = _record()

    assert record.lean is None
    assert _round_trip(record) == record


def test_an_entity_record_carries_its_lean_facts_through_json() -> None:
    record = _record(lean=LeanFacts(callers=1, callees=1, forwards_to="engine.run"))

    again = _round_trip(record)
    assert again == record
    assert again.lean is not None
    assert again.lean.forwards_to == "engine.run"


def test_a_zero_caller_count_is_not_an_unasked_one() -> None:
    """The distinction the whole record exists for: measured nothing vs measured not at all."""
    measured = LeanFacts(callers=0, unused_parameters=[])

    assert measured.callers == 0
    assert measured.unused_parameters == []
    assert LeanFacts().callers is None
    assert LeanFacts().unused_parameters is None


# --- a module-level definition's referenced flag (req 1.6) --------------------------


def test_a_definition_without_a_referenced_flag_was_never_asked() -> None:
    definition = Definition(name="TIMEOUT", path="src/cli/app.py", line=7, value="30")

    assert definition.referenced is None
    assert _round_trip(definition) == definition


def test_a_definition_carries_its_referenced_flag_through_json() -> None:
    unused = Definition(name="TIMEOUT", path="src/cli/app.py", line=7, referenced=False)

    again = _round_trip(unused)
    assert again == unused
    assert again.referenced is False


# --- the token index (req 5.8) ------------------------------------------------------


def test_a_routine_shape_carries_its_range_and_its_encoded_shape() -> None:
    shape = RoutineShape(path="src/cli/app.py", start=12, end=24, shape=[0, 1, 2, 1])

    assert shape.start == 12
    assert shape.end == 24
    assert shape.shape == [0, 1, 2, 1]


def test_a_token_index_defaults_to_no_unreadable_file() -> None:
    """An index with nothing unreadable is the ordinary case and writes nothing extra."""
    index = TokenIndex(vocabulary=[], files={}, routines={})

    assert index.unreadable == []


@pytest.mark.parametrize("missing", ["vocabulary", "files", "routines"])
def test_a_token_index_refuses_a_document_missing_one_of_its_three_halves(missing: str) -> None:
    """The three are required, and this is the test that holds them required.

    An empty default here would read as an index that was built and found nothing -- and both
    duplication rules answer a *present* index by reporting no duplicate, since ``None`` is
    the only state they treat as unavailable (requirement 5.8). So a worker that half-filled
    the document would silence both rules over the whole project with nothing to say it had
    happened, which is the failure that requirement exists to prevent. Only ``unreadable`` may
    be absent: a run with nothing unreadable is the ordinary run.
    """
    document = {"vocabulary": ["def"], "files": {"a.py": [(1, "abc")]}, "routines": {}}
    del document[missing]

    with pytest.raises(ValidationError):
        TokenIndex.model_validate(document)


def test_a_token_index_accepts_a_document_that_only_omits_the_unreadable_list() -> None:
    index = TokenIndex.model_validate(
        {"vocabulary": ["def"], "files": {"a.py": [(1, "abc")]}, "routines": {}}
    )

    assert index.unreadable == []


def test_a_token_index_round_trips_with_line_numbers_shapes_and_unreadable_files() -> None:
    index = TokenIndex(
        vocabulary=["def", "(", ")", "return"],
        files={"src/cli/app.py": [(12, "9f86d081"), (13, "884c7d65")]},
        routines={KEY.token: RoutineShape(path=KEY.path, start=12, end=13, shape=[0, 1, 2, 3])},
        unreadable=["src/util/generated.py"],
    )

    again = _round_trip(index)
    assert again == index
    assert again.files["src/cli/app.py"] == [(12, "9f86d081"), (13, "884c7d65")]
    assert again.routines[KEY.token].end == 13


def test_a_snapshot_without_a_token_index_says_nothing_about_tokens() -> None:
    snapshot = ProjectSnapshot(side="after")

    assert snapshot.tokens is None
    assert _round_trip(snapshot) == snapshot


def test_a_snapshot_carries_its_token_index_through_json() -> None:
    snapshot = ProjectSnapshot(
        side="after",
        tokens=TokenIndex(vocabulary=["def"], files={"a.py": [(1, "abc")]}, routines={}),
    )

    again = _round_trip(snapshot)
    assert again == snapshot
    assert again.tokens is not None
    assert again.tokens.vocabulary == ["def"]


# --- the declaring-class tally, the family's other project-wide fact (req 1.9) ---------


def test_a_snapshot_without_the_declaring_class_tally_says_nothing_about_it() -> None:
    """``None`` is "the worker was not asked", and the dead-code rules read it as unavailable.

    An empty mapping would be a measurement -- "no class in this project declares any method"
    -- and a rule meeting it would take every method for a non-interface and report the
    implementations of every protocol as dead code.
    """
    snapshot = ProjectSnapshot(side="after")

    assert snapshot.method_declarations is None
    assert _round_trip(snapshot) == snapshot


def test_a_snapshot_carries_the_declaring_class_tally_through_json() -> None:
    """A name two classes declare and a name one declares, which is the threshold's shape."""
    snapshot = ProjectSnapshot(side="after", method_declarations={"run": 2, "only": 1})

    again = _round_trip(snapshot)

    assert again == snapshot
    assert again.method_declarations == {"run": 2, "only": 1}


# --- a document written before this task (req 9.6) ----------------------------------


def test_a_snapshot_document_written_before_this_task_still_validates() -> None:
    """The analysis cache holds these; a stale document is read, never rewritten."""
    document = {
        "side": "after",
        "entities": [
            {
                "ref": {
                    "key": {
                        "scope": "routine",
                        "path": "src/cli/app.py",
                        "longname": "app.main",
                        "parameters": "argv",
                    },
                    "kind": "Python Function",
                    "name": "main",
                    "line": 12,
                },
                "language": "Python",
                "metrics": {"CountStmt": 4.0},
            }
        ],
        "definitions": [{"name": "TIMEOUT", "path": "src/cli/app.py", "line": 7, "value": "30"}],
    }

    snapshot = ProjectSnapshot.model_validate(document)

    assert snapshot.tokens is None
    assert snapshot.method_declarations is None
    assert snapshot.entities[KEY].lean is None
    assert snapshot.definitions[0].referenced is None


def test_the_shipped_snapshot_fixtures_still_validate() -> None:
    """Written for the base specification, and every rule's test still reads them."""
    for snapshot in (snapshot_fixture("before"), snapshot_fixture("after")):
        assert snapshot.tokens is None
        assert all(record.lean is None for record in snapshot.entities.values())


# --- the net delta on the run result (req 7.1) --------------------------------------


def test_a_net_delta_carries_statements_lines_and_the_routines_it_summed() -> None:
    delta = NetDelta(statements=-4, lines=-9, routines=3)

    assert delta.statements == -4
    assert delta.lines == -9
    assert delta.routines == 3
    assert _round_trip(delta) == delta


def test_a_net_delta_may_be_positive_or_negative_but_counts_no_negative_routines() -> None:
    """A shorter change is the point of the number; a negative *count* is a bug."""
    assert NetDelta(statements=12, lines=30, routines=7).statements == 12

    with pytest.raises(ValidationError):
        NetDelta(statements=0, lines=0, routines=-1)


def test_a_run_without_a_before_side_reports_no_net_delta() -> None:
    """Requirement 7.4 omits the delta rather than printing a zero it did not measure."""
    result = _run_result()

    assert result.net_delta is None
    assert json.loads(result.model_dump_json())["net_delta"] is None


def test_a_run_result_carries_the_net_delta_through_json() -> None:
    result = _run_result(net_delta=NetDelta(statements=-4, lines=-9, routines=3))

    again = RunResult.model_validate_json(result.model_dump_json(warnings="error"))
    assert again.net_delta == NetDelta(statements=-4, lines=-9, routines=3)


def test_the_json_schema_version_stays_2_with_the_net_delta_present() -> None:
    """An added field is not a bump; ``report/json_out.py`` bumps for a changed meaning."""
    result = _run_result(net_delta=NetDelta(statements=1, lines=2, routines=1))

    assert result.schema_version == 2
    with pytest.raises(ValidationError):
        _run_result(schema_version=3, net_delta=NetDelta(statements=1, lines=2, routines=1))


# --- the nine rule names (req 9.6) --------------------------------------------------


@pytest.mark.parametrize("name", LEAN_RULES)
def test_a_lean_rule_name_is_a_legal_structural_rule(name: StructureRuleName) -> None:
    """Legal here is legal everywhere: severities, SARIF ids and hints share this grammar."""
    rule = structure_rule(name)

    assert rule == f"structure.{name}"
    assert is_valid_rule_name(rule)
    assert parse_rule_name(rule).name == name


def test_the_nine_lean_rules_close_the_structural_list_in_the_designs_order() -> None:
    """The order is read: it is what the grammar's error message lists back to an operator.

    `tests/models/test_findings.py` holds the whole set; this holds the family's place in it,
    so a name inserted among the base specification's eleven is a failure here.
    """
    assert STRUCTURE_RULES[-len(LEAN_RULES) :] == LEAN_RULES


# --- what the extractor is asked for, and what the build must offer (9.2) -----------


def test_an_extract_request_asks_for_no_lean_measurement_by_default() -> None:
    """Requirement 9.4: while every lean rule is off, the worker is asked for nothing."""
    request = ExtractRequest(architecture="Directory Structure", depth=2)

    assert request.lean_references is False
    assert request.lean_tokens is False


def test_an_extract_request_carries_both_lean_keys_through_json() -> None:
    request = ExtractRequest(
        architecture="Directory Structure", depth=2, lean_references=True, lean_tokens=True
    )

    again = ExtractRequest.model_validate_json(request.model_dump_json(warnings="error"))
    assert again.lean_references is True
    assert again.lean_tokens is True


def test_the_three_lean_capabilities_are_features_a_build_is_asked_about() -> None:
    """`doctor` prints one row per member, so the family's capabilities are members."""
    assert Feature.LEAN_REFERENCES.value == "lean_references"
    assert Feature.LEAN_TOKENS.value == "lean_tokens"
    assert Feature.DUPLICATE_METRIC.value == "duplicate_metric"
    assert {feature.value for feature in Feature} == {
        "understand_sarif",
        "commit_before",
        "generated_archs",
        "plugin_metrics",
        "unused_rule",
        "accuracy",
        "lean_references",
        "lean_tokens",
        "duplicate_metric",
    }
