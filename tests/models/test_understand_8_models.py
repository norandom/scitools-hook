"""The model fields this specification adds, and the cache-layout number that guards them.

Every field here is optional and defaults to what 0.1.0a8 recorded, because a state file, a
snapshot document and an analysis result all outlive the run that wrote them: a document
written by the previous release has to keep validating, or upgrading the tool would look like
a corrupt cache. The one field that must *not* default forward is
:attr:`SyncState.schema_version`, whose whole job is to read as stale when it was never
written (requirement 8.6).
"""

from __future__ import annotations

from pathlib import Path

from scitools_hook.models.cache import CACHE_SCHEMA, SyncState
from scitools_hook.models.snapshot import EntityKey, EntityRecord, EntityRef
from scitools_hook.models.understand import (
    AnalyzeResult,
    Availability,
    Feature,
    FeatureReport,
)

# --- the analysis result carries what `und analyze` reported (7.1, 2.1) ------------


def test_an_analysis_result_without_the_new_switches_is_unchanged() -> None:
    """A 6.5 run passes neither switch, so neither figure exists, and that is not an error."""
    result = AnalyzeResult(seconds=1.0)

    assert result.accuracy is None
    assert result.sarif_path is None


def test_an_analysis_result_carries_the_accuracy_and_the_sarif_it_wrote() -> None:
    """Both survive a JSON round trip, because both are recorded in the sync state."""
    result = AnalyzeResult(seconds=1.0, accuracy=0.27, sarif_path=Path("/tmp/parselog.sarif"))
    again = AnalyzeResult.model_validate_json(result.model_dump_json())

    assert again.accuracy == 0.27
    assert again.sarif_path == Path("/tmp/parselog.sarif")


# --- what the installed build offers (1.1) ----------------------------------------


def test_every_feature_of_this_specification_has_a_name() -> None:
    """`doctor` prints one row per member, so the set is the specification's own list."""
    assert {feature.value for feature in Feature} == {
        "understand_sarif",
        "commit_before",
        "generated_archs",
        "plugin_metrics",
        "unused_rule",
        "accuracy",
    }


def test_an_availability_answers_one_of_three_states_and_keeps_the_reason() -> None:
    """`unverified` is not `not on this build`: a probe that could not run said nothing."""
    unverified = Availability(state="unverified", detail="no git on PATH")

    assert unverified.state == "unverified"
    assert unverified.detail == "no git on PATH"
    assert unverified.generated == []


def test_a_feature_report_round_trips_with_the_build_it_was_measured_on() -> None:
    """It is stored between runs, and a report from another build must read as stale."""
    report = FeatureReport(
        build="(Build 1262)",
        features={
            Feature.ACCURACY: Availability(state="available"),
            Feature.GENERATED_ARCHS: Availability(
                state="available", generated=["Git Stability", "Git Owner"]
            ),
        },
    )
    again = FeatureReport.model_validate_json(report.model_dump_json())

    assert again.build == "(Build 1262)"
    assert again.features[Feature.GENERATED_ARCHS].generated == ["Git Stability", "Git Owner"]
    assert again.offers(Feature.ACCURACY) is True
    assert again.offers(Feature.COMMIT_BEFORE) is False


# --- the referenced flag the unused rule reads (6.2) -------------------------------


def a_record(**overrides: object) -> EntityRecord:
    """A routine record, as the worker emits one."""
    key = EntityKey(scope="routine", path="pkg/a.py", longname="a.run", parameters="value")
    fields: dict[str, object] = {
        "ref": EntityRef(key=key, line=3, kind="python function", name="run"),
        "language": "Python",
        "metrics": {},
    }
    return EntityRecord.model_validate(fields | overrides)


def test_a_record_from_a_worker_that_did_not_look_says_nothing_either_way() -> None:
    """`None` is "not measured", which the rule reports as unavailable rather than as unused."""
    assert a_record().referenced is None


def test_a_record_carries_whether_anything_references_the_routine() -> None:
    assert a_record(referenced=False).referenced is False
    assert EntityRecord.model_validate_json(a_record(referenced=True).model_dump_json()).referenced


# --- the sync state, and the layout number that discards an old one (3.5, 4.4, 8.6) -


def test_a_state_written_before_this_feature_reads_as_an_older_layout() -> None:
    """The whole point of the field: absent means stale, so it may not default forward."""
    assert SyncState.model_validate_json("{}").schema_version == 0
    assert CACHE_SCHEMA >= 1


def test_the_new_state_fields_default_to_what_0_1_0a8_recorded() -> None:
    """An old state validates, and every new field reads as "nothing recorded"."""
    state = SyncState.model_validate_json("{}")

    assert state.before_route is None
    assert state.analysis_settings == ""
    assert state.accuracy == {}
    assert state.generated_archs == {}


def test_the_state_carries_the_route_the_fingerprint_the_accuracy_and_the_stamps() -> None:
    """Everything task 4.1's key and task 5.3's skip rule compare, through a round trip."""
    state = SyncState(
        before_commit="3ca0a97",
        before_route="commit",
        analysis_settings="0123456789abcdef",
        accuracy={"after": 0.27, "before": 0.31},
        generated_archs={"Git Stability": "b652812:tree-1"},
        schema_version=CACHE_SCHEMA,
    )
    again = SyncState.model_validate_json(state.model_dump_json())

    assert again.before_route == "commit"
    assert again.before_commit == "3ca0a97"
    assert again.analysis_settings == "0123456789abcdef"
    assert again.accuracy["after"] == 0.27
    assert again.generated_archs["Git Stability"] == "b652812:tree-1"
    assert again.schema_version == CACHE_SCHEMA
