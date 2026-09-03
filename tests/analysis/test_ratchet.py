"""Ratchet comparison and before-value attachment (task 4.2; req 4.4, 4.5).

Every case runs against the synthetic ``before``/``after`` snapshots, so the numbers below
are the ones ``tests/fixtures/__init__.py`` documents: ``app.build_parser`` is the modified
routine that got worse (CyclomaticStrict 6 -> 12, MaxNesting 2 -> 4, CountLineCode
40 -> 75), ``app.check_command`` is added, ``app.legacy_entry`` is removed, every other
routine is unchanged, ``src/cli/app.py`` grew (CountLineCode 120 -> 160) while its comment
ratio fell (0.18 -> 0.12) and ``src/analysis/rules.py`` grew a little (90 -> 96) while its
ratio fell from 0.2 to 0.19.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal

import pytest
from fixtures import snapshot_fixture

from scitools_hook.analysis.ratchet import (
    attach_before,
    evaluate_ratchet,
    pair_changed_signatures,
    within_limit,
)
from scitools_hook.analysis.thresholds import evaluate_thresholds
from scitools_hook.config.metric_names import Scope, parse_metric_name
from scitools_hook.config.models import Limit, Severity, ThresholdSpec
from scitools_hook.models.findings import EffectiveThreshold, Finding
from scitools_hook.models.snapshot import (
    EntityKey,
    EntityRecord,
    EntityRef,
    ParseError,
    ProjectSnapshot,
    Side,
)

APP = "src/cli/app.py"
RULES = "src/analysis/rules.py"
ENGINE = "src/analysis/engine.py"
BUILD_PARSER = EntityKey(scope="routine", path=APP, longname="app.build_parser", parameters="")
CHECK_COMMAND = EntityKey(
    scope="routine", path=APP, longname="app.check_command", parameters="args"
)
LEGACY_ENTRY = EntityKey(scope="routine", path=APP, longname="app.legacy_entry", parameters="")
ENGINE_CLASS = EntityKey(scope="class", path=ENGINE, longname="engine.Engine")


@pytest.fixture
def after() -> ProjectSnapshot:
    """The ``after`` side of the synthetic project."""
    return snapshot_fixture("after")


@pytest.fixture
def before() -> ProjectSnapshot:
    """The ``before`` side of the synthetic project."""
    return snapshot_fixture("before")


def threshold(
    scope: Scope,
    metric: str,
    limit: Limit,
    severity: Severity = "error",
    source: Literal["config", "baseline"] = "config",
) -> EffectiveThreshold:
    """One configured threshold whose effective limit equals the configured one.

    ``ratchet`` is written out rather than left to the model, because eight rules now default
    it *off* (``config.models.DECOMPOSITION_COUNTS``, task 11.9) and a helper that inherited
    that would silently stop comparing anything the moment a test named one of them -- which
    is how ``test_a_limit_with_both_bounds_ratchets_in_both_directions`` first passed for the
    wrong reason. What the default is belongs to ``tests/config``; what the comparison does
    once it is on belongs here, so this file says so on every threshold it builds.
    """
    spec = ThresholdSpec(scope=scope, metric=metric, limit=limit, severity=severity, ratchet=True)
    return EffectiveThreshold(
        spec=spec, metric=parse_metric_name(metric), limit=limit, source=source
    )


def ratchet(
    after: ProjectSnapshot,
    before: ProjectSnapshot,
    *specs: EffectiveThreshold,
) -> list[Finding]:
    """Compare every entity of the ``after`` snapshot against its ``before`` counterpart."""
    return evaluate_ratchet(after, before, set(after.entities) | set(before.entities), specs)


def longnames(findings: list[Finding]) -> set[str]:
    """The qualified names the findings point at."""
    return {finding.entity.key.longname for finding in findings if finding.entity is not None}


def test_a_value_that_rose_is_reported_even_though_it_stays_under_the_limit(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """A worsening metric is a finding whatever the absolute limit says (req 4.4).

    ``app.build_parser`` went from 6 to 12, well under the limit of 20 used here, so the
    only reason this finding can exist is the before/after comparison.
    """
    findings = ratchet(after, before, threshold("routine", "CyclomaticStrict", Limit(max=20)))

    (finding,) = findings
    assert finding.kind == "ratchet"
    assert finding.rule == "routine.CyclomaticStrict"
    assert finding.metric == "CyclomaticStrict"
    assert finding.scope == "routine"
    assert finding.entity is not None and finding.entity.key == BUILD_PARSER
    assert finding.path == APP
    assert finding.line == 34
    assert finding.value == pytest.approx(12.0)
    assert finding.before == pytest.approx(6.0)
    assert finding.limit == pytest.approx(20.0)
    assert finding.limit_source == "config"
    assert finding.severity == "error"
    assert finding.blocking is True
    assert finding.preexisting is False
    assert finding.hint == ""
    assert "rose" in finding.message
    assert "6" in finding.message and "12" in finding.message


def test_an_unchanged_or_improved_value_is_silent(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """Under a ``max`` limit only a rise is worse; equal and falling values report nothing.

    Every file's comment ratio either stayed the same (engine 0.15, adapter 0.16, text 0.22)
    or fell (rules 0.2 -> 0.19, app 0.18 -> 0.12), which is an improvement against a maximum.
    """
    findings = ratchet(after, before, threshold("file", "RatioCommentToCode", Limit(max=0.5)))

    assert findings == []


def test_a_min_limit_reports_a_value_that_fell(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """Worse means lower when the limit is a minimum (req 4.4)."""
    findings = ratchet(after, before, threshold("file", "RatioCommentToCode", Limit(min=0.2)))

    assert {finding.path for finding in findings} == {RULES, APP}
    fell = next(finding for finding in findings if finding.path == RULES)
    assert fell.value == pytest.approx(0.19)
    assert fell.before == pytest.approx(0.2)
    assert fell.limit == pytest.approx(0.2)
    assert "fell" in fell.message


def test_a_new_entity_produces_no_ratchet_finding(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """An added routine has no pre-change value, so it is judged by thresholds only (4.5).

    ``app.check_command`` has CyclomaticStrict 4, over the limit of 3 used here; a ratchet
    that treated a missing before side as a zero, or as anything else, would report it.
    """
    assert CHECK_COMMAND in after.entities
    assert CHECK_COMMAND not in before.entities

    findings = evaluate_ratchet(
        after, before, {CHECK_COMMAND}, [threshold("routine", "CyclomaticStrict", Limit(max=3))]
    )

    assert findings == []


def test_a_deleted_entity_produces_no_ratchet_finding(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """A routine the change removed is in the affected set but has no after value (4.10)."""
    assert LEGACY_ENTRY in before.entities
    assert LEGACY_ENTRY not in after.entities

    findings = evaluate_ratchet(
        after, before, {LEGACY_ENTRY}, [threshold("routine", "CyclomaticStrict", Limit(max=1))]
    )

    assert findings == []


def test_a_metric_no_entity_carries_is_skipped(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """Neither class record carries ``CountLineCode``; the ratchet cannot compare it."""
    findings = ratchet(after, before, threshold("class", "CountLineCode", Limit(max=10)))

    assert findings == []


def test_only_the_requested_keys_are_compared(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """Staged mode ratchets the affected entities, not the whole database (req 4.2)."""
    findings = evaluate_ratchet(
        after, before, {ENGINE_CLASS}, [threshold("routine", "CyclomaticStrict", Limit(max=20))]
    )

    assert findings == []


def test_a_metric_with_the_ratchet_disabled_is_not_compared(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """The ratchet is per metric: ``ratchet = false`` silences the comparison (req 4.4)."""
    spec = ThresholdSpec(
        scope="routine", metric="CyclomaticStrict", limit=Limit(max=20), ratchet=False
    )
    disabled = EffectiveThreshold(
        spec=spec, metric=parse_metric_name("CyclomaticStrict"), limit=Limit(max=20)
    )

    assert ratchet(after, before, disabled) == []


def test_a_population_threshold_is_not_ratcheted(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """A stats-prefixed or project-scope threshold has no entity to compare (req 4.8)."""
    findings = ratchet(
        after,
        before,
        threshold("routine", "AVG:CyclomaticStrict", Limit(max=3)),
        threshold("project", "MaxCyclomaticStrict", Limit(max=3)),
    )

    assert findings == []


def test_a_limit_with_both_bounds_ratchets_in_both_directions(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """A two-sided limit is a maximum and a minimum, so either movement is worse.

    ``CountLineCode`` only rose (rules 90 -> 96, app 120 -> 160) and ``RatioCommentToCode``
    only fell (rules 0.2 -> 0.19, app 0.18 -> 0.12); each finding carries the bound its
    direction belongs to.
    """
    findings = ratchet(
        after,
        before,
        threshold("file", "CountLineCode", Limit(max=500, min=10)),
        threshold("file", "RatioCommentToCode", Limit(max=1.0, min=0.05)),
    )

    assert {(f.path, f.metric, f.limit) for f in findings} == {
        (RULES, "CountLineCode", 500.0),
        (APP, "CountLineCode", 500.0),
        (RULES, "RatioCommentToCode", 0.05),
        (APP, "RatioCommentToCode", 0.05),
    }


def test_the_severity_and_the_limit_source_of_the_spec_are_carried(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """A warning ratchet never blocks, and an adaptive limit says where it came from (8.5)."""
    spec = ThresholdSpec(
        scope="routine", metric="CyclomaticStrict", limit=Limit(max=30), severity="warning"
    )
    lowered = EffectiveThreshold(
        spec=spec,
        metric=parse_metric_name("CyclomaticStrict"),
        limit=Limit(max=20),
        source="baseline",
    )

    (finding,) = ratchet(after, before, lowered)

    assert finding.severity == "warning"
    assert finding.blocking is False
    assert finding.limit == pytest.approx(20.0)
    assert finding.limit_source == "baseline"


# --- the two before values that are not measurements of the change (task 11.9) -----

DEEP = "pkg/deep.py"
WALK = EntityKey(scope="routine", path=DEEP, longname="deep.walk", parameters="rows")
STDLIB = "/usr/lib/python3.14/typing.py"
"""A parse error outside the repository, in the absolute form ``ParseError`` keeps it in."""


def one_routine(
    side: Side, metrics: Mapping[str, float], unparsed: Sequence[str] = ()
) -> ProjectSnapshot:
    """A snapshot holding only ``deep.walk``, with exactly the metrics and parse errors given."""
    record = EntityRecord(
        ref=EntityRef(key=WALK, kind="Function", name="walk", line=1),
        language="Python",
        metrics=dict(metrics),
    )
    return ProjectSnapshot(
        side=side,
        entities={WALK: record},
        parse_errors=[
            ParseError(path=Path(path), line=4, message="expected token '(' at token [")
            for path in unparsed
        ],
    )


def walked(
    was: Mapping[str, float],
    now: Mapping[str, float],
    *specs: EffectiveThreshold,
    unparsed_before: Sequence[str] = (),
    unparsed_after: Sequence[str] = (),
) -> list[Finding]:
    """Ratchet ``deep.walk`` from ``was`` to ``now`` under ``specs``."""
    return evaluate_ratchet(
        one_routine("after", now, unparsed_after),
        one_routine("before", was, unparsed_before),
        {WALK},
        specs,
    )


# The four metric sets below are the measurement behind the exemption, taken through the
# installed CLI against Understand 6.5.1204 on a real repository: rewriting a five-deep
# routine as guard clauses ("invert the condition and return early", which is the second half
# of MaxNesting's own hint) moved MaxNesting 5 -> 2 and left CyclomaticStrict at 5, while
# CountLineCode and CountStmt both moved 8 -> 11.
FLATTENED_BEFORE = {"CountLineCode": 8, "CountStmt": 8, "MaxNesting": 5, "CyclomaticStrict": 5}
FLATTENED_AFTER = {"CountLineCode": 11, "CountStmt": 11, "MaxNesting": 2, "CyclomaticStrict": 5}
GREW_BEFORE = {"CountLineCode": 8, "CountStmt": 8, "MaxNesting": 5, "CyclomaticStrict": 5}
GREW_AFTER = {"CountLineCode": 11, "CountStmt": 11, "MaxNesting": 5, "CyclomaticStrict": 5}

LINES = threshold("routine", "CountLineCode", Limit(max=60))
STATEMENTS = threshold("routine", "CountStmt", Limit(max=40))


def test_a_count_a_measured_simplification_raised_is_not_reported() -> None:
    """The gate must not refuse the second remedy its own MaxNesting hint offers (11.9).

    ``routine.CountLineCode`` and ``routine.CountStmt`` both rose, and both are dropped --
    because the same routine's MaxNesting fell 5 -> 2 in the same change and nothing in
    ``COMPLEXITY_EVIDENCE`` rose.
    """
    assert walked(FLATTENED_BEFORE, FLATTENED_AFTER, LINES, STATEMENTS) == []


def test_a_count_that_rose_with_the_complexity_standing_still_is_still_reported() -> None:
    """The sibling of the case above, differing only in MaxNesting: 5 -> 5 instead of 5 -> 2.

    An entity that got longer and no simpler is exactly what the ratchet is for, so both
    counts are reported with the values they moved between.
    """
    findings = walked(GREW_BEFORE, GREW_AFTER, LINES, STATEMENTS)

    assert {(f.rule, f.before, f.value) for f in findings} == {
        ("routine.CountLineCode", 8.0, 11.0),
        ("routine.CountStmt", 8.0, 11.0),
    }


def test_a_count_that_rose_while_the_complexity_rose_too_is_still_reported() -> None:
    """One rising evidence metric vetoes the exemption even when another one fell.

    MaxNesting falls 5 -> 2 exactly as in the exempted case; CyclomaticStrict rises 5 -> 9,
    which is the only difference and is enough.
    """
    findings = walked(
        {"CountLineCode": 8, "MaxNesting": 5, "CyclomaticStrict": 5},
        {"CountLineCode": 11, "MaxNesting": 2, "CyclomaticStrict": 9},
        LINES,
    )

    assert [(f.rule, f.before, f.value) for f in findings] == [("routine.CountLineCode", 8.0, 11.0)]


def test_a_count_that_rose_with_no_evidence_present_at_all_is_still_reported() -> None:
    """No complexity metric in the snapshot is not the same as one that improved.

    A configuration that thresholds only ``CountLineCode`` never asks for CyclomaticStrict or
    MaxNesting, so neither side carries them. The exemption needs a measurement to point at,
    and having none is not one.
    """
    findings = walked({"CountLineCode": 8}, {"CountLineCode": 11}, LINES)

    assert [(f.rule, f.before, f.value) for f in findings] == [("routine.CountLineCode", 8.0, 11.0)]


def test_a_rule_outside_the_decomposition_counts_is_never_forgiven() -> None:
    """``routine.CountParams`` rises for no reason a decomposition explains, so it still fires.

    The evidence here is as strong as the exempted case's -- MaxNesting 5 -> 2, nothing rising
    -- and the only difference is which rule went up. Nothing good makes a parameter list
    longer, so nothing forgives it.
    """
    findings = walked(
        {"CountParams": 3, "MaxNesting": 5},
        {"CountParams": 5, "MaxNesting": 2},
        threshold("routine", "CountParams", Limit(max=5)),
    )

    assert [(f.rule, f.before, f.value) for f in findings] == [("routine.CountParams", 3.0, 5.0)]


def test_an_entity_whose_file_did_not_parse_before_is_not_ratcheted() -> None:
    """A before value read out of a file ``und`` could not parse is not a before value (11.9).

    The numbers are a real regression -- MaxNesting 2 -> 6 -- and would fire on their own; the
    parse error on the before side is what stops them, because the analysis getting better is
    not the code getting worse.
    """
    nesting = threshold("routine", "MaxNesting", Limit(max=3))

    assert walked({"MaxNesting": 2}, {"MaxNesting": 6}, nesting, unparsed_before=[DEEP]) == []


def test_the_same_regression_still_fires_when_only_the_after_side_failed_to_parse() -> None:
    """Only the *before* side's coverage can make a before value fictional.

    Identical metrics to the case above, with the parse error moved to the after side: the
    finding comes back, so the skip is about which side lost coverage and not about the
    presence of a parse error anywhere in the run.
    """
    nesting = threshold("routine", "MaxNesting", Limit(max=3))

    findings = walked({"MaxNesting": 2}, {"MaxNesting": 6}, nesting, unparsed_after=[DEEP])

    assert [(f.rule, f.before, f.value) for f in findings] == [("routine.MaxNesting", 2.0, 6.0)]


def test_a_parse_error_in_a_file_no_commit_owns_silences_nothing() -> None:
    """The interpreter's own standard library fails to parse on both sides of every run.

    Task 10.4 measured four such errors in a clean analysis. They arrive with absolute paths
    while an entity's path is repository-relative, so they match no entity -- and this is the
    case that says the matching is on the path and not on "were there any parse errors".
    """
    nesting = threshold("routine", "MaxNesting", Limit(max=3))

    findings = walked({"MaxNesting": 2}, {"MaxNesting": 6}, nesting, unparsed_before=[STDLIB])

    assert [(f.rule, f.before, f.value) for f in findings] == [("routine.MaxNesting", 2.0, 6.0)]


# --- attach_before ---------------------------------------------------------------


def test_attach_before_leaves_a_file_the_before_side_could_not_parse_unset() -> None:
    """An inflated before value would excuse the violation it invented (task 11.9).

    ``analysis.classify`` reads ``Finding.before`` to decide whether a violation was already
    there, and a value read out of a file that failed to parse is not a reading of that file.
    The finding therefore comes back with ``before`` still ``None``, which classify treats as
    "not known" and leaves blocking, rather than as "was already worse".
    """
    before = one_routine("before", {"CountStmt": 66}, ["pkg/deep.py"])
    finding = Finding(
        kind="threshold",
        rule="routine.CountStmt",
        metric="CountStmt",
        scope="routine",
        entity=EntityRef(key=WALK, kind="Function", name="walk", line=1),
        path=DEEP,
        value=45.0,
        severity="error",
        blocking=True,
        message="routine deep.walk CountStmt is 45, which exceeds the maximum 40",
    )

    (attached,) = attach_before([finding], before)

    assert attached.before is None


def test_attach_before_fills_the_same_finding_when_the_before_side_parsed() -> None:
    """The sibling, differing only in whether the before snapshot names the file.

    The metrics are identical, so a change that stopped filling ``before`` at all would pass
    the case above and fail here.
    """
    before = one_routine("before", {"CountStmt": 66})
    finding = Finding(
        kind="threshold",
        rule="routine.CountStmt",
        metric="CountStmt",
        scope="routine",
        entity=EntityRef(key=WALK, kind="Function", name="walk", line=1),
        path=DEEP,
        value=45.0,
        severity="error",
        blocking=True,
        message="routine deep.walk CountStmt is 45, which exceeds the maximum 40",
    )

    (attached,) = attach_before([finding], before)

    assert attached.before == pytest.approx(66.0)


def threshold_findings(after: ProjectSnapshot, *specs: EffectiveThreshold) -> list[Finding]:
    """The threshold findings of the after side, exactly as step 4.1 produces them."""
    findings = evaluate_thresholds(after, set(after.entities), specs).findings
    assert all(finding.before is None for finding in findings)
    return findings


def test_attach_before_keeps_a_before_value_the_pre_change_record_cannot_supply(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """A metric the pre-change record never carried must not erase a value already set.

    ``attach_before`` is idempotent: re-running it over findings that already carry a
    before value leaves them alone rather than overwriting it with nothing (req 4.6).
    """
    findings = threshold_findings(after, threshold("routine", "CountLineCode", Limit(max=1)))
    (finding,) = [f for f in findings if f.entity is not None and f.entity.key == BUILD_PARSER]
    carried = finding.model_copy(update={"before": 6.0})
    stripped = before.model_copy(
        update={
            "entities": {
                key: (
                    record.model_copy(
                        update={
                            "metrics": {
                                name: value
                                for name, value in record.metrics.items()
                                if name != "CountLineCode"
                            }
                        }
                    )
                    if key == BUILD_PARSER
                    else record
                )
                for key, record in before.entities.items()
            }
        }
    )
    assert "CountLineCode" not in stripped.entities[BUILD_PARSER].metrics

    (attached,) = attach_before([carried], stripped)

    assert attached.before == pytest.approx(6.0)


def test_attach_before_fills_the_before_value_of_an_entity_on_both_sides(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """A threshold finding gains the pre-change value classify needs (req 4.3, 4.6)."""
    findings = threshold_findings(after, threshold("routine", "CyclomaticStrict", Limit(max=10)))

    (attached,) = attach_before(findings, before)

    assert attached.entity is not None and attached.entity.key == BUILD_PARSER
    assert attached.before == pytest.approx(6.0)
    assert attached.value == pytest.approx(12.0)
    assert attached.limit == pytest.approx(10.0)
    assert attached.kind == "threshold"


def test_attach_before_leaves_a_new_entity_without_a_before_value(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """Only a key present on both sides gets a before value (req 4.5)."""
    findings = threshold_findings(after, threshold("routine", "CyclomaticStrict", Limit(max=3)))

    attached = attach_before(findings, before)

    by_name = {f.entity.key.longname: f for f in attached if f.entity is not None}
    assert by_name["app.check_command"].before is None
    assert by_name["app.build_parser"].before == pytest.approx(6.0)


def test_attach_before_leaves_a_population_finding_alone(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """A project-level finding belongs to no entity, so it has no before value to fill."""
    findings = threshold_findings(after, threshold("project", "AVG:CyclomaticStrict", Limit(max=3)))

    (attached,) = attach_before(findings, before)

    assert attached.entity is None
    assert attached.before is None


def test_attach_before_touches_only_threshold_findings(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """A ratchet finding already carries its own before value and keeps it untouched."""
    (own,) = ratchet(after, before, threshold("routine", "CyclomaticStrict", Limit(max=20)))
    marked = own.model_copy(update={"before": 99.0})

    (attached,) = attach_before([marked], before)

    assert attached.before == pytest.approx(99.0)


def test_attach_before_does_not_modify_the_findings_it_is_given(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """The findings are frozen contracts; the step returns new objects (req 7.1)."""
    findings = threshold_findings(after, threshold("routine", "CyclomaticStrict", Limit(max=10)))
    original = findings[0]

    attached = attach_before(findings, before)

    assert original.before is None
    assert attached[0] is not original
    assert attached[0].before == pytest.approx(6.0)


# --- the same routine under a new signature (task 11.6) ---------------------------

AREA = "Shape::area"
NATIVE = "native/shape.cpp"
LINE_LIMIT = threshold("routine", "CountLineCode", Limit(max=60))
"""One ratcheted rule, well above every value below, so only the comparison can report."""


def routine(longname: str, parameters: str, path: str = DEEP) -> EntityKey:
    """One routine key; ``parameters`` is what a signature change moves and nothing else."""
    return EntityKey(scope="routine", path=path, longname=longname, parameters=parameters)


def side(name: Side, entities: Mapping[EntityKey, float]) -> ProjectSnapshot:
    """A snapshot holding exactly the given routines at exactly the given line counts."""
    return ProjectSnapshot(
        side=name,
        entities={
            key: EntityRecord(
                ref=EntityRef(
                    key=key, kind="Function", name=key.longname.rsplit(".", 1)[-1], line=1
                ),
                language="Python",
                metrics={"CountLineCode": lines},
            )
            for key, lines in entities.items()
        },
    )


def affected(was: Mapping[EntityKey, float], now: Mapping[EntityKey, float]) -> list[Finding]:
    """Ratchet the after side's own keys, exactly the set ``analysis.affected`` hands over."""
    after, before = side("after", now), side("before", was)
    return evaluate_ratchet(after, before, set(after.entities), [LINE_LIMIT])


WALK_ONE = routine("deep.walk", "rows")
WALK_FOUR = routine("deep.walk", "rows,skip_none,skip_str,limit")
"""The measured defect, as two keys: one routine, before and after three parameters arrived."""


def test_a_routine_that_grew_and_gained_parameters_is_compared_with_its_old_signature() -> None:
    """The defect this task exists for, at its smallest (task 11.6, req 4.4).

    Nothing but ``parameters`` differs between the two keys, so before the pairing the after
    key found no before record, the comparison returned "added" and the run went green on a
    routine that grew by four lines.
    """
    findings = affected({WALK_ONE: 6.0}, {WALK_FOUR: 10.0})

    (finding,) = findings
    assert finding.kind == "ratchet"
    assert finding.entity is not None and finding.entity.key == WALK_FOUR
    assert finding.before == pytest.approx(6.0)
    assert finding.value == pytest.approx(10.0)
    assert "rose from 6 to 10" in finding.message


def test_the_same_growth_with_the_signature_untouched_reports_the_same_thing() -> None:
    """The control: identical numbers, one difference -- the after key's parameter list.

    Without it "the pairing works" could be read off a rule that reports every routine that
    grew whatever its identity, and this pair is what says the two paths agree.
    """
    paired = affected({WALK_ONE: 6.0}, {WALK_FOUR: 10.0})
    direct = affected({WALK_ONE: 6.0}, {WALK_ONE: 10.0})

    assert [finding.before for finding in paired] == [pytest.approx(6.0)]
    assert [finding.before for finding in direct] == [pytest.approx(6.0)]
    assert [finding.value for finding in paired] == [finding.value for finding in direct]


def test_a_key_that_is_on_both_sides_of_the_affected_set_is_still_reported_once() -> None:
    """``keys`` may hold the removed key too; the pairing must not report the routine twice."""
    after, before = side("after", {WALK_FOUR: 10.0}), side("before", {WALK_ONE: 6.0})

    findings = evaluate_ratchet(after, before, {WALK_ONE, WALK_FOUR}, [LINE_LIMIT])

    assert len(findings) == 1


def test_pairing_answers_nothing_when_every_signature_stayed_the_same() -> None:
    """The common case: no key is missing from either side, so there is nothing to pair."""
    after, before = side("after", {WALK_ONE: 10.0}), side("before", {WALK_ONE: 6.0})

    assert pair_changed_signatures(after, before) == {}


# --- what must keep working: a real C++ overload pair (task 10.1's measurement) ----

AREA_ONE = routine(AREA, "int width", NATIVE)
AREA_TWO = routine(AREA, "int width,int height", NATIVE)
"""``Shape::area(int) const`` and ``Shape::area(int, int) const``: same scope, path, long name,
kind and short name; only ``parameters`` tells them apart."""


def test_an_overload_pair_that_both_survive_is_compared_member_by_member() -> None:
    """Two entities in, two entities out: the pairing may not merge a real overload pair.

    Both overloads exist on both sides, so neither is missing and neither is paired. The one
    that grew is reported against **its own** before value of 20, not against its sibling's 6,
    which is what a merged pair would have produced.
    """
    findings = affected({AREA_ONE: 6.0, AREA_TWO: 20.0}, {AREA_ONE: 6.0, AREA_TWO: 30.0})

    (finding,) = findings
    assert finding.entity is not None and finding.entity.key == AREA_TWO
    assert finding.before == pytest.approx(20.0)
    assert finding.value == pytest.approx(30.0)


def test_a_new_overload_beside_an_unchanged_one_is_new_rather_than_paired() -> None:
    """One added, none removed: requirement 4.5 still applies, so the addition is not judged."""
    after = side("after", {AREA_ONE: 6.0, AREA_TWO: 40.0})
    before = side("before", {AREA_ONE: 6.0})

    assert pair_changed_signatures(after, before) == {}
    assert evaluate_ratchet(after, before, set(after.entities), [LINE_LIMIT]) == []


def test_one_overload_changes_its_signature_while_its_sibling_stays_untouched() -> None:
    """The C++ case this task is really about: an overload pair, one of them re-signed.

    ``Shape::area(int)`` is unchanged and matches itself; ``Shape::area(int, int)`` gained a
    third parameter, so it is the one key missing from each side of the family and it pairs.
    The before value proves the pairing picked the right partner: 20 is what
    ``area(int, int)`` measured, 6 is what its untouched sibling measured, and a pairing that
    took whichever family member came first would have reported 6.
    """
    area_three = routine(AREA, "int width,int height,int depth", NATIVE)
    after = side("after", {AREA_ONE: 6.0, area_three: 30.0})
    before = side("before", {AREA_ONE: 6.0, AREA_TWO: 20.0})

    assert pair_changed_signatures(after, before) == {area_three: AREA_TWO}
    (finding,) = evaluate_ratchet(after, before, set(after.entities), [LINE_LIMIT])
    assert finding.entity is not None and finding.entity.key == area_three
    assert finding.before == pytest.approx(20.0)


def test_two_signatures_changing_at_once_in_one_family_pair_nothing() -> None:
    """The ambiguous case, answered with silence rather than with a guess.

    Both overloads changed their parameter lists, so two keys are missing from each side and
    nothing in the database says which became which. Both read as new, which is the behaviour
    this module already had -- stated here so a later change that starts guessing has to
    change a test that says why it must not.
    """
    after = side(
        "after",
        {
            routine(AREA, "long width", NATIVE): 40.0,
            routine(AREA, "long width,long height", NATIVE): 40.0,
        },
    )
    before = side("before", {AREA_ONE: 6.0, AREA_TWO: 6.0})

    assert pair_changed_signatures(after, before) == {}
    assert evaluate_ratchet(after, before, set(after.entities), [LINE_LIMIT]) == []


def test_a_routine_moved_to_another_file_is_not_paired_with_the_one_it_left() -> None:
    """The family carries the path, so a move is not a signature change."""
    moved = routine("deep.walk", "rows", "pkg/other.py")
    after, before = side("after", {moved: 10.0}), side("before", {WALK_ONE: 6.0})

    assert pair_changed_signatures(after, before) == {}
    assert evaluate_ratchet(after, before, set(after.entities), [LINE_LIMIT]) == []


def test_attach_before_does_not_follow_the_signature_pairing() -> None:
    """The deliberate asymmetry (task 11.6): a guess may add a finding, not excuse one.

    ``analysis.classify`` calls a violation **pre-existing** -- and therefore non-blocking --
    when ``Finding.before`` already broke the limit. Filling that from a paired entity would
    let a signature change excuse a violation on the strength of a pairing, so the before
    value stays unset, which classify reads as "not known" and leaves blocking. The ratchet
    still reports the growth; the two steps disagree on purpose.
    """
    before = side("before", {WALK_ONE: 66.0})
    finding = Finding(
        kind="threshold",
        rule="routine.CountLineCode",
        metric="CountLineCode",
        scope="routine",
        entity=EntityRef(key=WALK_FOUR, kind="Function", name="walk", line=1),
        path=DEEP,
        value=70.0,
        severity="error",
        blocking=True,
        message="routine deep.walk CountLineCode is 70, which exceeds the maximum 60",
    )

    (attached,) = attach_before([finding], before)

    assert attached.before is None


# --- inside the limit, and the sentence that says so (task 11.15) -------------------


def test_within_limit_reads_the_boundary_off_the_finding(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """``app.build_parser`` at 12, judged against three limits: 11, 12 and 13.

    One movement, three thresholds, so the only thing that differs between the answers is the
    number the limit stands at -- and the three are written as literals, one on each side of
    the after value and one exactly on it. The middle one is the case a comparison written
    ``<`` instead of ``<=`` gets wrong, and it is the one that decides whether growth up to a
    limit is allowed to spend the last unit of headroom.
    """
    answers = {}
    for maximum in (11.0, 12.0, 13.0):
        (finding,) = ratchet(
            after, before, threshold("routine", "CyclomaticStrict", Limit(max=maximum))
        )
        assert (finding.before, finding.value) == (pytest.approx(6.0), pytest.approx(12.0))
        answers[maximum] = within_limit(finding)

    assert answers == {11.0: False, 12.0: True, 13.0: True}


def test_within_limit_reads_a_minimum_from_the_direction_the_value_moved(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """The ``min`` side: ``src/analysis/rules.py``'s comment ratio fell 0.2 -> 0.19.

    A minimum is broken by being *below* it, so the same three-way check runs the other way:
    a floor of 0.2 is broken by 0.19, a floor of 0.19 is met exactly, and a floor of 0.18
    leaves headroom. Without this the predicate could compare every value against a maximum
    and still pass every test above.
    """
    answers = {}
    for minimum in (0.2, 0.19, 0.18):
        findings = ratchet(
            after, before, threshold("file", "RatioCommentToCode", Limit(min=minimum))
        )
        (finding,) = [item for item in findings if item.path == RULES]
        assert (finding.before, finding.value) == (pytest.approx(0.2), pytest.approx(0.19))
        answers[minimum] = within_limit(finding)

    assert answers == {0.2: False, 0.19: True, 0.18: True}


def test_within_limit_is_false_for_a_finding_that_is_not_a_ratchet_one() -> None:
    """Only a ratchet finding carries a before value the predicate can read.

    A threshold finding exists *because* its value broke the limit, so answering "inside"
    for one would demote the absolute rules as well; and "not known" is the answer that
    keeps a refusal rather than inventing an exemption.
    """
    threshold_finding = Finding(
        kind="threshold",
        rule="routine.CountLineCode",
        metric="CountLineCode",
        scope="routine",
        value=70.0,
        before=40.0,
        limit=60.0,
        severity="error",
        blocking=True,
        message="routine deep.walk CountLineCode is 70, which exceeds the maximum 60",
    )
    no_limit = threshold_finding.model_copy(update={"kind": "ratchet", "limit": None})

    assert within_limit(threshold_finding) is False
    assert within_limit(no_limit) is False


def test_the_message_inside_the_limit_names_the_bound_that_is_still_holding(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """What a below-limit growth prints, since it is no longer a refusal (req 7.1).

    The old sentence -- "an affected entity may not get worse than it was" -- is kept for the
    findings that still block and dropped from the ones that do not, because a warning that
    states a rule the run will not enforce is worse than no warning at all.
    """
    (inside,) = ratchet(after, before, threshold("routine", "CyclomaticStrict", Limit(max=20)))
    (outside,) = ratchet(after, before, threshold("routine", "CyclomaticStrict", Limit(max=10)))

    assert inside.message == (
        "routine app.build_parser CyclomaticStrict rose from 6 to 12, still within the maximum 20"
    )
    assert outside.message == (
        "routine app.build_parser CyclomaticStrict rose from 6 to 12; "
        "an affected entity may not get worse than it was"
    )


def test_the_message_on_a_minimum_names_the_minimum(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> None:
    """The ``min`` wording, so the sentence does not say "maximum" about a floor."""
    findings = ratchet(after, before, threshold("file", "RatioCommentToCode", Limit(min=0.1)))

    (fell,) = [finding for finding in findings if finding.path == RULES]

    assert fell.message == (
        "file src/analysis/rules.py RatioCommentToCode fell from 0.2 to 0.19, "
        "still within the minimum 0.1"
    )
