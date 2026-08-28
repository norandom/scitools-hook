"""Fan-in/fan-out thresholds for files and classes, and the fan-out ratchet (task 4.4; req 6.4).

Fan-in is how many entities depend on an entity, fan-out how many it depends on. Both are
counted over the after snapshot's edge lists — ``file_edges`` for files, ``class_edges`` for
classes, whose endpoints are :attr:`~scitools_hook.models.snapshot.EntityKey.token` strings —
and only for the entities the change affected, which is what ``keys_files`` and
``keys_classes`` carry.

Beside the absolute limits, requirement 6.4 asks for a ratchet: an affected entity whose
fan-out *grew* is reported even when it stays under the limit. The tests below pin that this
ratchet is fan-out only, that it needs a before side, and that growth and excess are two
separate findings, so an implementation that reports one instead of the other fails.

The headline case is the synthetic project of ``tests/fixtures``, where ``src/cli/app.py``
gains edges to ``rules.py`` and ``adapter.py`` and its fan-out goes from two to four.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Final

from fixtures import snapshot_fixture

from scitools_hook.analysis.structure.fan import evaluate_fan
from scitools_hook.config.models import FanKey, Limit
from scitools_hook.models.findings import Finding
from scitools_hook.models.snapshot import (
    DepEdge,
    EntityKey,
    EntityRecord,
    EntityRef,
    ProjectSnapshot,
    Side,
)

APP: Final = "src/cli/app.py"
ENGINE: Final = "src/analysis/engine.py"
RULES: Final = "src/analysis/rules.py"
ADAPTER: Final = "src/understand/adapter.py"
TEXT: Final = "src/util/text.py"

ALPHA: Final = EntityKey(scope="class", path="src/a.py", longname="a.Alpha")
BETA: Final = EntityKey(scope="class", path="src/b.py", longname="b.Beta")
GAMMA: Final = EntityKey(scope="class", path="src/c.py", longname="c.Gamma")
DELTA: Final = EntityKey(scope="class", path="src/d.py", longname="d.Delta")


def edge(src: str, dst: str, refs: int = 1) -> DepEdge:
    """One dependency edge with a reference count."""
    return DepEdge(src=src, dst=dst, refs=refs)


def class_edge(src: EntityKey, dst: EntityKey, refs: int = 1) -> DepEdge:
    """One class dependency edge, whose endpoints are entity key tokens."""
    return DepEdge(src=src.token, dst=dst.token, refs=refs)


def file_record(path: str, line: int | None = 1) -> EntityRecord:
    """The record a snapshot holds for a file entity."""
    key = EntityKey(scope="file", path=path, longname=path)
    return EntityRecord(
        ref=EntityRef(key=key, kind="File", name=path.rpartition("/")[2], line=line),
        language="Python",
    )


def class_record(key: EntityKey, line: int | None = 7) -> EntityRecord:
    """The record a snapshot holds for a class entity."""
    return EntityRecord(
        ref=EntityRef(key=key, kind="Class", name=key.longname.rpartition(".")[2], line=line),
        language="Python",
    )


def snap(
    side: Side,
    files: Sequence[DepEdge] = (),
    classes: Sequence[DepEdge] = (),
    records: Sequence[EntityRecord] = (),
) -> ProjectSnapshot:
    """A snapshot carrying nothing but the edges and records a fan test needs."""
    return ProjectSnapshot(
        side=side,
        file_edges=list(files),
        class_edges=list(classes),
        entities={record.key: record for record in records},
    )


def fan_limit(key: FanKey, maximum: float) -> dict[FanKey, Limit]:
    """One configured fan limit, in the shape ``structure.fan`` has."""
    return {key: Limit(max=maximum)}


def test_a_file_over_its_fan_out_limit_is_reported() -> None:
    """Fan-out is the number of distinct files an entity depends on (req 6.4)."""
    after = snap("after", files=[edge(APP, ENGINE), edge(APP, RULES, refs=4), edge(APP, TEXT)])

    (finding,) = evaluate_fan(after, None, {APP}, set(), fan_limit("file_fan_out", 2))

    assert finding.kind == "structural"
    assert finding.rule == "structure.fan_out"
    assert finding.scope == "file"
    assert finding.metric is None
    assert finding.path == APP
    assert finding.value == 3
    assert finding.before is None
    assert finding.limit == 2
    assert finding.limit_source == "rule"
    assert finding.blocking is False
    assert finding.preexisting is False
    assert finding.hint == ""
    assert finding.details == {"depends_on": [ENGINE, RULES, TEXT]}
    assert APP in finding.message
    assert "fan-out" in finding.message


def test_a_file_exactly_at_its_fan_out_limit_is_silent() -> None:
    """The limit is a maximum: three dependencies under a limit of three are allowed."""
    after = snap("after", files=[edge(APP, ENGINE), edge(APP, RULES), edge(APP, TEXT)])

    assert evaluate_fan(after, None, {APP}, set(), fan_limit("file_fan_out", 3)) == []


def test_a_file_over_its_fan_in_limit_is_reported() -> None:
    """Fan-in is the number of distinct files that depend on an entity (req 6.4)."""
    after = snap("after", files=[edge(APP, TEXT), edge(ENGINE, TEXT), edge(ADAPTER, TEXT)])

    (finding,) = evaluate_fan(after, None, {TEXT}, set(), fan_limit("file_fan_in", 2))

    assert finding.rule == "structure.fan_in"
    assert finding.path == TEXT
    assert finding.value == 3
    assert finding.limit == 2
    assert finding.details == {"depended_on_by": [ENGINE, APP, ADAPTER]}
    assert "fan-in" in finding.message


def test_a_file_exactly_at_its_fan_in_limit_is_silent() -> None:
    """``>`` and not ``>=``: being at the maximum is not a violation."""
    after = snap("after", files=[edge(APP, TEXT), edge(ENGINE, TEXT)])

    assert evaluate_fan(after, None, {TEXT}, set(), fan_limit("file_fan_in", 2)) == []


def test_a_class_over_its_fan_out_limit_is_reported() -> None:
    """Class edges carry entity key tokens; the finding names the class, not the token."""
    after = snap(
        "after",
        classes=[class_edge(ALPHA, BETA), class_edge(ALPHA, GAMMA), class_edge(ALPHA, DELTA)],
        records=[class_record(ALPHA)],
    )

    (finding,) = evaluate_fan(after, None, set(), {ALPHA}, fan_limit("class_fan_out", 2))

    assert finding.rule == "structure.fan_out"
    assert finding.scope == "class"
    assert finding.path == "src/a.py"
    assert finding.line == 7
    assert finding.entity is not None
    assert finding.entity.key == ALPHA
    assert finding.value == 3
    assert finding.details == {"depends_on": ["b.Beta", "c.Gamma", "d.Delta"]}
    assert "a.Alpha" in finding.message
    assert ALPHA.token not in finding.message


def test_a_class_over_its_fan_in_limit_is_reported() -> None:
    """The same count in the other direction, under the fan-in rule name (req 6.4)."""
    after = snap(
        "after",
        classes=[class_edge(BETA, ALPHA), class_edge(GAMMA, ALPHA), class_edge(DELTA, ALPHA)],
    )

    (finding,) = evaluate_fan(after, None, set(), {ALPHA}, fan_limit("class_fan_in", 2))

    assert finding.rule == "structure.fan_in"
    assert finding.scope == "class"
    assert finding.entity is None
    assert finding.path == "src/a.py"
    assert finding.details == {"depended_on_by": ["b.Beta", "c.Gamma", "d.Delta"]}


def test_only_the_affected_entities_are_evaluated() -> None:
    """``keys_files`` is the affected set; an untouched file over the limit is not this change."""
    after = snap(
        "after",
        files=[
            edge(APP, ENGINE),
            edge(APP, RULES),
            edge(APP, TEXT),
            edge(ADAPTER, ENGINE),
            edge(ADAPTER, RULES),
            edge(ADAPTER, TEXT),
        ],
    )

    findings = evaluate_fan(after, None, {APP}, set(), fan_limit("file_fan_out", 2))

    assert [finding.path for finding in findings] == [APP]


def test_a_growing_fan_out_is_a_ratchet_finding_even_under_the_limit() -> None:
    """An affected entity that depends on more than it did is reported (req 6.4)."""
    before = snap("before", files=[edge(APP, ENGINE)])
    after = snap("after", files=[edge(APP, ENGINE), edge(APP, ADAPTER)])

    (finding,) = evaluate_fan(after, before, {APP}, set(), fan_limit("file_fan_out", 10))

    assert finding.kind == "ratchet"
    assert finding.rule == "structure.fan_out"
    assert finding.scope == "file"
    assert finding.path == APP
    assert finding.value == 2
    assert finding.before == 1
    assert finding.limit == 10
    assert finding.limit_source == "rule"
    assert finding.details == {"depends_on": [ENGINE, ADAPTER]}
    assert APP in finding.message
    assert "fan-out" in finding.message


def test_a_fan_out_that_stayed_the_same_is_not_a_ratchet() -> None:
    """Swapping one dependency for another keeps the fan-out, so nothing got worse."""
    before = snap("before", files=[edge(APP, ENGINE)])
    after = snap("after", files=[edge(APP, ADAPTER)])

    assert evaluate_fan(after, before, {APP}, set(), fan_limit("file_fan_out", 10)) == []


def test_a_shrinking_fan_out_is_not_a_ratchet() -> None:
    """Depending on less is an improvement, never a finding."""
    before = snap("before", files=[edge(APP, ENGINE), edge(APP, ADAPTER)])
    after = snap("after", files=[edge(APP, ENGINE)])

    assert evaluate_fan(after, before, {APP}, set(), fan_limit("file_fan_out", 10)) == []


def test_a_growing_fan_in_is_not_a_ratchet() -> None:
    """Requirement 6.4 ratchets fan-out only: being used more is not a regression."""
    before = snap("before", files=[edge(APP, TEXT)])
    after = snap("after", files=[edge(APP, TEXT), edge(ENGINE, TEXT)])
    limits = {**fan_limit("file_fan_in", 10), **fan_limit("file_fan_out", 10)}

    assert evaluate_fan(after, before, {TEXT}, set(), limits) == []


def test_growth_past_the_limit_yields_both_a_threshold_and_a_ratchet_finding() -> None:
    """The absolute limit and the ratchet are two rules; a change can break both (req 6.4)."""
    before = snap("before", files=[edge(APP, ENGINE)])
    after = snap("after", files=[edge(APP, ENGINE), edge(APP, RULES), edge(APP, ADAPTER)])

    findings = evaluate_fan(after, before, {APP}, set(), fan_limit("file_fan_out", 2))

    assert [finding.kind for finding in findings] == ["structural", "ratchet"]
    assert [finding.value for finding in findings] == [3, 3]
    assert [finding.before for finding in findings] == [None, 1]


def test_without_a_before_snapshot_no_ratchet_is_reported() -> None:
    """Whole-project mode compares nothing, so it produces absolute findings only (req 4.8)."""
    after = snap("after", files=[edge(APP, ENGINE), edge(APP, ADAPTER)])

    assert evaluate_fan(after, None, {APP}, set(), fan_limit("file_fan_out", 10)) == []


def test_an_unconfigured_fan_key_switches_that_rule_off() -> None:
    """No ``file_fan_out`` limit means no file fan-out rule at all, ratchet included."""
    before = snap("before", files=[edge(APP, ENGINE)])
    after = snap("after", files=[edge(APP, ENGINE), edge(APP, ADAPTER)])

    assert evaluate_fan(after, before, {APP}, set(), fan_limit("class_fan_out", 0)) == []


def test_a_self_dependency_does_not_count_towards_fan() -> None:
    """A file referencing its own contents is a parse artefact, as it is for cycles (req 6.1)."""
    after = snap("after", files=[edge(APP, APP, refs=5), edge(APP, ENGINE)])
    limits = {**fan_limit("file_fan_in", 0), **fan_limit("file_fan_out", 1)}

    assert evaluate_fan(after, None, {APP}, set(), limits) == []


def test_a_file_with_no_dependency_edge_at_all_has_no_fan() -> None:
    """An affected file that neither uses nor is used by anything cannot break a fan limit."""
    after = snap("after", files=[edge(ENGINE, RULES)])
    limits = {**fan_limit("file_fan_in", 0), **fan_limit("file_fan_out", 0)}

    assert evaluate_fan(after, None, {APP}, set(), limits) == []


def test_the_entity_reference_is_attached_when_the_snapshot_knows_the_file() -> None:
    """Requirement 7.1 wants the entity and its line whenever they are known."""
    after = snap(
        "after",
        files=[edge(APP, ENGINE), edge(APP, RULES)],
        records=[file_record(APP, line=1)],
    )

    (finding,) = evaluate_fan(after, None, {APP}, set(), fan_limit("file_fan_out", 1))

    assert finding.entity is not None
    assert finding.entity.key.path == APP
    assert finding.line == 1


def test_files_are_reported_before_classes_and_both_in_a_stable_order() -> None:
    """Deterministic output whatever order the affected sets iterate in."""
    after = snap(
        "after",
        files=[edge(APP, ENGINE), edge(ADAPTER, ENGINE)],
        classes=[class_edge(ALPHA, BETA), class_edge(GAMMA, BETA)],
    )
    limits = {**fan_limit("file_fan_out", 0), **fan_limit("class_fan_out", 0)}

    findings = evaluate_fan(after, None, {APP, ADAPTER}, {ALPHA, GAMMA}, limits)

    assert [finding.path for finding in findings] == [APP, ADAPTER, "src/a.py", "src/c.py"]


def test_fan_findings_are_warnings_by_default() -> None:
    """``structure.fan_severity`` defaults to ``warning``; ``classify`` applies any override."""
    after = snap("after", files=[edge(APP, ENGINE)])

    (finding,) = evaluate_fan(after, None, {APP}, set(), fan_limit("file_fan_out", 0))

    assert finding.severity == "warning"
    assert finding.blocking is False


def test_the_details_of_a_finding_survive_a_json_round_trip() -> None:
    """``details`` is part of the JSON output contract, so it must reload unchanged (req 7.4)."""
    before = snap("before", files=[edge(APP, ENGINE)])
    after = snap("after", files=[edge(APP, ENGINE), edge(APP, ADAPTER)])

    (finding,) = evaluate_fan(after, before, {APP}, set(), fan_limit("file_fan_out", 10))
    reloaded = Finding.model_validate(json.loads(finding.model_dump_json()))

    assert reloaded == finding
    assert reloaded.details == {"depends_on": [ENGINE, ADAPTER]}


def test_the_fixture_change_grows_the_fan_out_of_the_cli_entry_point() -> None:
    """``src/cli/app.py`` goes from two dependencies to four: over the limit and worse (6.4)."""
    before = snapshot_fixture("before")
    after = snapshot_fixture("after")

    findings = evaluate_fan(after, before, {APP}, set(), fan_limit("file_fan_out", 3))

    threshold, ratchet = findings
    assert threshold.kind == "structural"
    assert threshold.value == 4
    assert threshold.limit == 3
    assert threshold.details == {"depends_on": [ENGINE, RULES, ADAPTER, TEXT]}
    assert ratchet.kind == "ratchet"
    assert ratchet.before == 2
    assert ratchet.value == 4
    assert ratchet.entity is not None
    assert ratchet.entity.key.path == APP


def test_the_fixture_class_fan_out_did_not_grow() -> None:
    """``engine.Engine`` gained references to ``adapter.Adapter``, but no new class."""
    before = snapshot_fixture("before")
    after = snapshot_fixture("after")
    engine_class = EntityKey(scope="class", path=ENGINE, longname="engine.Engine")

    findings = evaluate_fan(after, before, set(), {engine_class}, fan_limit("class_fan_out", 1))

    assert findings == []
