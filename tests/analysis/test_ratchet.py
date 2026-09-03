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

from scitools_hook.analysis.ratchet import attach_before, evaluate_ratchet
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
