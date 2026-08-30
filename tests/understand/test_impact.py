"""The impact expander: entity keys out, blast radius back (task 6.6, requirement 9.5).

The worker answers ``{"impact": {token: ImpactSet}, "warnings": [...]}`` — object keys must
be strings, so the identity of every entity travels as an :attr:`EntityKey.token` and has to
be read back. A key that resolves to nothing is *ordinary*: a routine the change deleted is
asked about against the after database and one it added against the before database, so the
expander keeps those as warnings and answers with an empty set rather than failing the run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

import pytest
from conftest import SampleDatabases
from fakes.api import FakeApiRunner
from test_api_runner import real_env

from scitools_hook.config.metric_names import SCOPE_KINDS
from scitools_hook.errors import AnalysisFailedError
from scitools_hook.models.progress import NullCommandLog
from scitools_hook.models.snapshot import EntityKey
from scitools_hook.understand.api_runner import ApiRunner
from scitools_hook.understand.impact import ImpactExpander

DB: Final = Path("/cache/after.und")
ROOT: Final = Path("/cache/after")

APPLY_RULES: Final = EntityKey(
    scope="routine", path="analysis/rules.py", longname="rules.apply_rules", parameters="names"
)
MISSING: Final = EntityKey(
    scope="routine", path="gone.py", longname="gone.removed", parameters=None
)

ENGINE_RUN: Final[dict[str, Any]] = {
    "key": {
        "scope": "routine",
        "path": "analysis/engine.py",
        "longname": "engine.Engine.run",
        "parameters": "self,options",
    },
    "kind": "python Function Attribute",
    "name": "run",
    "line": 11,
}
"""One ``EntityRef`` exactly as the real worker wrote it for the sample project."""


def an_expander(answer: dict[str, object]) -> ImpactExpander:
    """An expander whose runner answers ``impact`` with ``answer``."""
    return ImpactExpander(FakeApiRunner(answers={"impact": answer}))


def runner_of(expander: ImpactExpander) -> FakeApiRunner:
    """The fake runner behind an expander built by :func:`an_expander`."""
    runner = expander.runner
    assert isinstance(runner, FakeApiRunner)
    return runner


def an_answer(total: int = 1) -> dict[str, object]:
    """The wire shape of one impact set for :data:`APPLY_RULES`, plus no warnings."""
    return {
        "impact": {APPLY_RULES.token: {"by_depth": {"1": [ENGINE_RUN]}, "total": total}},
        "warnings": [],
    }


# --- the request -----------------------------------------------------------------


def test_the_request_names_the_database_the_root_and_the_kind_strings() -> None:
    # Resolving a key back to an entity needs `db.ents(<kind string>)`, and the worker may
    # never invent one: the same SCOPE_KINDS the snapshot travelled with must travel here.
    expander = an_expander(an_answer())

    expander.expand(DB, ROOT, [APPLY_RULES], depth=2)

    request = runner_of(expander).request_for("impact")
    assert request["db"] == str(DB)
    assert request["root"] == str(ROOT)
    assert request["kinds_by_scope"] == SCOPE_KINDS


def test_the_keys_travel_as_the_documents_the_snapshot_answered_with() -> None:
    expander = an_expander(an_answer())

    expander.expand(DB, ROOT, [APPLY_RULES], depth=2)

    assert runner_of(expander).request_for("impact")["keys"] == [
        {
            "scope": "routine",
            "path": "analysis/rules.py",
            "longname": "rules.apply_rules",
            "parameters": "names",
        }
    ]


def test_the_depth_is_passed_through_including_zero() -> None:
    # `ExtractRequest.depth` is `ge=1`, but this operation counts reference hops and 0 is a
    # legal answer ("report nothing"), so the snapshot's floor must not leak into it.
    expander = an_expander({"impact": {APPLY_RULES.token: {"by_depth": {}, "total": 0}}})

    expander.expand(DB, ROOT, [APPLY_RULES], depth=0)

    assert runner_of(expander).request_for("impact")["depth"] == 0


def test_asking_about_no_entities_opens_no_database() -> None:
    expander = an_expander(an_answer())

    assert expander.expand(DB, ROOT, [], depth=3) == {}
    assert runner_of(expander).ops == []


# --- the answer ------------------------------------------------------------------


def test_the_token_keys_become_the_entity_keys_they_encode() -> None:
    expander = an_expander(an_answer(total=5))

    found = expander.expand(DB, ROOT, [APPLY_RULES], depth=2)

    assert list(found) == [APPLY_RULES]
    assert found[APPLY_RULES].total == 5


def test_the_depth_levels_are_numbers_not_the_strings_json_had_to_use() -> None:
    # `ImpactSet.by_depth` is keyed by int; a str key would make every "level 1" lookup miss.
    found = an_expander(an_answer()).expand(DB, ROOT, [APPLY_RULES], depth=2)

    assert list(found[APPLY_RULES].by_depth) == [1]
    assert found[APPLY_RULES].by_depth[1][0].name == "run"


def test_a_key_the_database_does_not_have_is_a_warning_and_an_empty_set() -> None:
    warning = "the routine 'gone.removed' of gone.py is not in this database"
    answer: dict[str, object] = {
        "impact": {MISSING.token: {"by_depth": {}, "total": 0}},
        "warnings": [warning],
    }
    expander = an_expander(answer)

    found = expander.expand(DB, ROOT, [MISSING], depth=2)

    assert found[MISSING].total == 0
    assert expander.warnings == [warning]


def test_warnings_accumulate_across_calls_in_the_order_they_were_reported() -> None:
    answer: dict[str, object] = {"impact": {}, "warnings": ["first"]}
    expander = an_expander(answer)

    expander.expand(DB, ROOT, [MISSING], depth=1)
    expander.expand(DB, ROOT, [MISSING], depth=1)

    assert expander.warnings == ["first", "first"]


def test_a_key_that_is_not_a_key_token_is_a_broken_contract() -> None:
    expander = an_expander({"impact": {"not-a-token": {"by_depth": {}, "total": 0}}})

    with pytest.raises(AnalysisFailedError) as failure:
        expander.expand(DB, ROOT, [APPLY_RULES], depth=1)

    assert "not-a-token" in str(failure.value)


def test_an_impact_set_that_does_not_validate_is_a_broken_contract() -> None:
    expander = an_expander({"impact": {APPLY_RULES.token: {"by_depth": {"1": ["nonsense"]}}}})

    with pytest.raises(AnalysisFailedError):
        expander.expand(DB, ROOT, [APPLY_RULES], depth=1)


def test_an_answer_without_an_impact_object_is_a_broken_contract() -> None:
    expander = an_expander({"warnings": []})

    with pytest.raises(AnalysisFailedError):
        expander.expand(DB, ROOT, [APPLY_RULES], depth=1)


# --- against the real Understand -------------------------------------------------


@pytest.mark.contract
def test_the_real_blast_radius_of_a_routine_the_sample_project_calls(
    sample_databases: SampleDatabases,
) -> None:
    """``rules.apply_rules`` is called by ``Engine.run``, which ``app.main`` calls in turn."""
    expander = ImpactExpander(ApiRunner(real_env("upython"), NullCommandLog()))
    root = sample_databases.root("after")

    found = expander.expand(sample_databases.after_db, root, [APPLY_RULES, MISSING], depth=2)

    impact = found[APPLY_RULES]
    assert impact.total >= 2
    assert {ref.name for ref in impact.by_depth[1]} >= {"run"}
    assert {ref.name for ref in impact.by_depth[2]} >= {"main"}
    assert found[MISSING].total == 0
    assert any("gone.removed" in warning for warning in expander.warnings)
