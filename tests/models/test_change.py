"""Change models: affected set, entity/dependency deltas, impact, graphs, summary (4.2, 9.x)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from scitools_hook.models.change import (
    AffectedSet,
    ChangeSummary,
    DependencyDelta,
    EntityDelta,
    GraphFile,
    GraphTarget,
    ImpactSet,
)
from scitools_hook.models.snapshot import EntityKey, EntityRef

KEY = EntityKey(scope="routine", path="src/cli/app.py", longname="app.build_parser", parameters="")
REF = EntityRef(key=KEY, kind="Python Function", name="build_parser", line=34)
OTHER_KEY = EntityKey(scope="file", path="src/util/text.py", longname="src/util/text.py")
OTHER_REF = EntityRef(key=OTHER_KEY, kind="Python File", name="text.py")


# --- AffectedSet ---------------------------------------------------------------


def test_affected_set_defaults_are_empty() -> None:
    affected = AffectedSet()
    assert affected.files == set()
    assert affected.deleted_files == set()
    assert affected.keys == set()
    assert affected.neighbourhood == set()


def test_affected_set_round_trips_through_json() -> None:
    affected = AffectedSet(
        files={"src/cli/app.py", "src/analysis/rules.py"},
        deleted_files={"src/legacy.py"},
        keys={KEY, OTHER_KEY},
        neighbourhood={"src/util/text.py"},
    )
    assert AffectedSet.model_validate(json.loads(affected.model_dump_json())) == affected


def test_affected_set_serialises_sets_in_a_stable_order() -> None:
    affected = AffectedSet(files={"b.py", "a.py", "c.py"})
    wire = json.loads(affected.model_dump_json())
    assert wire["files"] == ["a.py", "b.py", "c.py"]


def test_affected_keys_serialise_in_a_stable_order() -> None:
    affected = AffectedSet(keys={KEY, OTHER_KEY})
    wire = json.loads(affected.model_dump_json())
    assert [entry["longname"] for entry in wire["keys"]] == [
        "src/util/text.py",
        "app.build_parser",
    ]


# --- deltas --------------------------------------------------------------------


def test_entity_delta_keeps_before_after_and_delta_metrics() -> None:
    delta = EntityDelta(
        ref=REF,
        status="modified",
        before={"CyclomaticStrict": 6.0},
        after={"CyclomaticStrict": 12.0},
        delta={"CyclomaticStrict": 6.0},
    )
    assert delta.status == "modified"
    assert delta.delta["CyclomaticStrict"] == 6.0


def test_entity_delta_status_is_restricted() -> None:
    with pytest.raises(ValidationError):
        EntityDelta(ref=REF, status="renamed")  # type: ignore[arg-type]


def test_entity_delta_metric_maps_default_to_empty() -> None:
    delta = EntityDelta(ref=REF, status="added")
    assert (delta.before, delta.after, delta.delta) == ({}, {}, {})


def test_dependency_delta_marks_architecture_crossings() -> None:
    delta = DependencyDelta(
        src="src/cli/app.py",
        dst="src/understand/adapter.py",
        status="added",
        src_node="Directory Structure/src/cli",
        dst_node="Directory Structure/src/understand",
        crosses_arch=True,
    )
    assert delta.crosses_arch is True
    assert delta.status == "added"


def test_dependency_delta_nodes_are_optional() -> None:
    delta = DependencyDelta(src="a.py", dst="b.py", status="removed")
    assert (delta.src_node, delta.dst_node, delta.crosses_arch) == (None, None, False)


# --- impact and graphs ---------------------------------------------------------


def test_impact_set_keys_depths_by_integer() -> None:
    impact = ImpactSet(by_depth={1: [REF], 2: [OTHER_REF]}, total=2)
    restored = ImpactSet.model_validate(json.loads(impact.model_dump_json()))
    assert restored == impact
    assert list(restored.by_depth) == [1, 2]


def test_graph_target_only_allows_the_verified_graph_names() -> None:
    assert GraphTarget(key=KEY, graph="Butterfly").graph == "Butterfly"
    assert GraphTarget(key=OTHER_KEY, graph="Depends On").graph == "Depends On"
    with pytest.raises(ValidationError):
        GraphTarget(key=KEY, graph="File Dependencies")  # type: ignore[arg-type]


def test_graph_file_points_at_the_exported_svg() -> None:
    graph = GraphFile(key=KEY, graph="Butterfly", path=Path("review/butterfly-1.svg"))
    assert graph.path == Path("review/butterfly-1.svg")


# --- ChangeSummary -------------------------------------------------------------


def _summary() -> ChangeSummary:
    modified = EntityDelta(
        ref=REF,
        status="modified",
        before={"CyclomaticStrict": 6.0},
        after={"CyclomaticStrict": 12.0},
        delta={"CyclomaticStrict": 6.0},
    )
    return ChangeSummary(
        files={"src/cli/app.py": [modified]},
        dependencies=[
            DependencyDelta(
                src="src/cli/app.py",
                dst="src/understand/adapter.py",
                status="added",
                crosses_arch=True,
            )
        ],
        top_by_delta=[modified],
        top_by_value=[modified],
        impact={KEY: ImpactSet(by_depth={1: [OTHER_REF]}, total=1)},
        graphs=[GraphFile(key=KEY, graph="Butterfly", path=Path("review/app-main.svg"))],
        db_path="/home/dev/.cache/scitools-hook/abc/after.und",
        open_command="und -db /home/dev/.cache/scitools-hook/abc/after.und",
    )


def test_change_summary_defaults_are_empty() -> None:
    summary = ChangeSummary(db_path="/tmp/after.und", open_command="und -db /tmp/after.und")
    assert summary.files == {}
    assert summary.dependencies == []
    assert summary.top_by_delta == []
    assert summary.top_by_value == []
    assert summary.impact == {}
    assert summary.graphs == []


def test_change_summary_impact_is_keyed_by_entity_key_over_json() -> None:
    summary = _summary()
    wire = json.loads(summary.model_dump_json())
    assert list(wire["impact"]) == [KEY.token]
    restored = ChangeSummary.model_validate(wire)
    assert restored == summary
    assert restored.impact[KEY].total == 1


def test_change_summary_round_trips_through_python_mode_dump() -> None:
    summary = _summary()
    assert ChangeSummary.model_validate(summary.model_dump()) == summary


def test_change_summary_rejects_an_impact_map_that_is_not_a_mapping() -> None:
    with pytest.raises(ValidationError):
        ChangeSummary.model_validate(
            {"db_path": "/tmp/after.und", "open_command": "und", "impact": []}
        )


def test_change_summary_rejects_an_impact_key_that_is_not_an_entity_key_token() -> None:
    with pytest.raises(ValidationError):
        ChangeSummary.model_validate(
            {
                "db_path": "/tmp/after.und",
                "open_command": "und",
                "impact": {"app.main": {"by_depth": {}, "total": 0}},
            }
        )
