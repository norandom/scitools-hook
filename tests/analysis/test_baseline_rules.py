"""Adaptive baseline parsing, application, tightening and capture (task 4.5; req 8.1-8.6).

The numbers below come from the synthetic ``after`` snapshot documented in
`tests/fixtures/__init__.py`: the worst routine ``app.build_parser`` has CyclomaticStrict 12,
the worst class ``engine.Engine`` has CountClassCoupled 6, the largest file
``src/analysis/engine.py`` has CountLineCode 210, the project population of
``CyclomaticStrict`` averages 6.125, the lowest ``RatioCommentToCode`` is 0.12 and
``PercentLackOfCohesion`` is unavailable for Python, so no entity carries it.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from fixtures import snapshot_fixture

from scitools_hook.analysis.baseline import apply, capture, parse_baseline, tighten
from scitools_hook.analysis.thresholds import evaluate_thresholds
from scitools_hook.config.metric_names import Scope
from scitools_hook.config.models import Limit, ThresholdSpec
from scitools_hook.models.baseline import Baseline
from scitools_hook.models.findings import EffectiveThreshold
from scitools_hook.models.snapshot import ProjectSnapshot

STAMP = "2026-08-28T10:00:00+00:00"
"""Fixed capture timestamp, so every expected baseline is deterministic."""

CYCLOMATIC = "routine.CyclomaticStrict"
FILE_LINES = "file.CountLineCode"
RATIO = "file.RatioCommentToCode"


@pytest.fixture
def after() -> ProjectSnapshot:
    """The ``after`` side of the synthetic project; the only input ``capture`` reads."""
    return snapshot_fixture("after")


def spec(scope: Scope, metric: str, limit: Limit) -> ThresholdSpec:
    """One configured threshold."""
    return ThresholdSpec(scope=scope, metric=metric, limit=limit)


def baseline(values: dict[str, float]) -> Baseline:
    """A stored baseline holding ``values``, keyed by rule name."""
    return Baseline(captured_at=STAMP, values=values)


def raw_document(values: object) -> dict[str, object]:
    """A well-formed baseline document whose ``values`` member is ``values``."""
    return {"version": 1, "captured_at": STAMP, "values": values}


def by_rule(thresholds: list[EffectiveThreshold]) -> dict[str, EffectiveThreshold]:
    """The effective thresholds keyed by their rule name."""
    return {threshold.rule: threshold for threshold in thresholds}


# --- apply: effective limit and its source (req 8.2, 8.5) -----------------------


def test_a_baseline_below_the_configured_maximum_lowers_the_effective_limit() -> None:
    """The effective maximum is the lower of configuration and baseline (req 8.2, 8.5)."""
    specs = [spec("routine", "CyclomaticStrict", Limit(max=10))]

    thresholds, issues = apply(specs, baseline({CYCLOMATIC: 8}))

    (threshold,) = thresholds
    assert issues == []
    assert threshold.rule == CYCLOMATIC
    assert threshold.limit.max == pytest.approx(8.0)
    assert threshold.source == "baseline"
    assert threshold.spec == specs[0]
    assert threshold.metric.metric == "CyclomaticStrict"


def test_a_baseline_above_the_configured_maximum_is_ignored() -> None:
    """A baseline can only ever tighten, so a higher value leaves the configured limit (8.4)."""
    specs = [spec("routine", "CyclomaticStrict", Limit(max=10))]

    thresholds, issues = apply(specs, baseline({CYCLOMATIC: 15}))

    (threshold,) = thresholds
    assert issues == []
    assert threshold.limit.max == pytest.approx(10.0)
    assert threshold.source == "config"


def test_a_minimum_limit_is_tightened_upward_by_the_baseline() -> None:
    """For a ``min`` limit the effective bound is the higher of the two (req 8.2)."""
    specs = [spec("file", "RatioCommentToCode", Limit(min=0.1))]

    tightened, _ = apply(specs, baseline({RATIO: 0.15}))
    loosened, _ = apply(specs, baseline({RATIO: 0.05}))

    assert tightened[0].limit.min == pytest.approx(0.15)
    assert tightened[0].limit.max is None
    assert tightened[0].source == "baseline"
    assert loosened[0].limit.min == pytest.approx(0.1)
    assert loosened[0].source == "config"


def test_a_two_sided_limit_tightens_only_its_maximum() -> None:
    """A baseline records observed maxima, so it narrows the upper bound of a two-sided limit."""
    specs = [spec("file", "RatioCommentToCode", Limit(max=0.2, min=0.15))]

    thresholds, issues = apply(specs, baseline({RATIO: 0.18}))

    assert issues == []
    assert thresholds[0].limit.max == pytest.approx(0.18)
    assert thresholds[0].limit.min == pytest.approx(0.15)
    assert thresholds[0].source == "baseline"


def test_a_baseline_below_the_configured_minimum_is_reported_and_ignored() -> None:
    """A value that would invert a two-sided limit is an issue, not a crash (req 8.6)."""
    specs = [spec("file", "RatioCommentToCode", Limit(max=0.2, min=0.15))]

    thresholds, issues = apply(specs, baseline({RATIO: 0.1}))

    (issue,) = issues
    assert issue.key == RATIO
    assert "minimum" in issue.message
    assert thresholds[0].limit.max == pytest.approx(0.2)
    assert thresholds[0].source == "config"


def test_thresholds_without_a_baseline_keep_their_configured_limits() -> None:
    """No baseline at all is the ordinary case: every limit comes from configuration (8.5)."""
    specs = [
        spec("routine", "CyclomaticStrict", Limit(max=10)),
        spec("file", "CountLineCode", Limit(max=500)),
    ]

    thresholds, issues = apply(specs, None)

    assert issues == []
    assert [threshold.source for threshold in thresholds] == ["config", "config"]
    assert [threshold.limit.max for threshold in thresholds] == [10.0, 500.0]


def test_a_baseline_key_without_a_configured_threshold_is_reported_and_the_rest_applies() -> None:
    """An unknown key is reported and the configured limits keep working (req 8.6)."""
    specs = [spec("routine", "CyclomaticStrict", Limit(max=10))]
    stored = baseline({CYCLOMATIC: 8, "file.Nonsense": 40})

    thresholds, issues = apply(specs, stored)

    (issue,) = issues
    assert issue.key == "file.Nonsense"
    assert "not configured" in issue.message
    assert thresholds[0].limit.max == pytest.approx(8.0)
    assert thresholds[0].source == "baseline"


# --- parse_baseline: tolerant reading (req 8.6) ---------------------------------


def test_a_corrupt_entry_is_reported_while_the_other_limits_still_apply() -> None:
    """One bad value never costs the run its other baseline limits (req 8.6)."""
    specs = [
        spec("routine", "CyclomaticStrict", Limit(max=10)),
        spec("file", "CountLineCode", Limit(max=500)),
    ]

    parsed, issues = parse_baseline(raw_document({CYCLOMATIC: "eight", FILE_LINES: 200}), specs)

    (issue,) = issues
    assert issue.key == CYCLOMATIC
    assert "eight" in issue.message
    assert parsed is not None
    assert parsed.values == {FILE_LINES: 200.0}

    thresholds = by_rule(apply(specs, parsed)[0])
    assert thresholds[FILE_LINES].limit.max == pytest.approx(200.0)
    assert thresholds[FILE_LINES].source == "baseline"
    assert thresholds[CYCLOMATIC].limit.max == pytest.approx(10.0)
    assert thresholds[CYCLOMATIC].source == "config"


def test_an_unknown_key_is_reported_and_skipped() -> None:
    """A baseline naming a threshold that is not configured is an issue (req 8.6)."""
    specs = [spec("routine", "CyclomaticStrict", Limit(max=10))]

    parsed, issues = parse_baseline(raw_document({CYCLOMATIC: 8, "file.Nonsense": 3}), specs)

    (issue,) = issues
    assert issue.key == "file.Nonsense"
    assert parsed is not None and parsed.values == {CYCLOMATIC: 8.0}


@pytest.mark.parametrize("value", [True, None, "8", [8], {"max": 8}, float("nan")])
def test_a_value_that_is_not_a_finite_number_is_reported(value: object) -> None:
    """Only real numbers become limits; a boolean and a NaN are values, not numbers."""
    specs = [spec("routine", "CyclomaticStrict", Limit(max=10))]

    parsed, issues = parse_baseline(raw_document({CYCLOMATIC: value}), specs)

    assert [issue.key for issue in issues] == [CYCLOMATIC]
    assert parsed is not None and parsed.values == {}


def test_a_wrong_version_is_reported_and_the_values_are_still_read() -> None:
    """A version the Gate does not know is reported, not fatal (req 8.6)."""
    specs = [spec("routine", "CyclomaticStrict", Limit(max=10))]

    parsed, issues = parse_baseline(
        {"version": "one", "captured_at": STAMP, "values": {CYCLOMATIC: 8}}, specs
    )

    (issue,) = issues
    assert issue.key is None
    assert "version" in issue.message
    assert parsed is not None and parsed.values == {CYCLOMATIC: 8.0}
    assert parsed.version == 1


def test_a_wrong_typed_timestamp_and_an_unknown_member_are_reported() -> None:
    """File-level problems carry no key and leave the values alone (req 8.6)."""
    specs = [spec("routine", "CyclomaticStrict", Limit(max=10))]

    parsed, issues = parse_baseline(
        {"version": 1, "captured_at": 17, "extra": True, "values": {CYCLOMATIC: 8}}, specs
    )

    assert {issue.key for issue in issues} == {None}
    assert any("captured_at" in issue.message for issue in issues)
    assert any("extra" in issue.message for issue in issues)
    assert parsed is not None and parsed.captured_at == ""
    assert parsed.values == {CYCLOMATIC: 8.0}


@pytest.mark.parametrize(
    "document",
    [
        [1, 2],
        "not a baseline",
        None,
        {"version": 1, "captured_at": STAMP},
        {"version": 1, "captured_at": STAMP, "values": [CYCLOMATIC]},
    ],
)
def test_a_document_without_usable_values_yields_no_baseline(document: object) -> None:
    """``None`` means there is no baseline to apply, and the issue says why (req 8.6)."""
    specs = [spec("routine", "CyclomaticStrict", Limit(max=10))]

    parsed, issues = parse_baseline(document, specs)

    assert parsed is None
    assert issues != [] and all(issue.key is None for issue in issues)


def test_an_empty_values_member_parses_to_an_empty_baseline() -> None:
    """A baseline that constrains nothing is well-formed, not a problem."""
    specs = [spec("routine", "CyclomaticStrict", Limit(max=10))]

    parsed, issues = parse_baseline(raw_document({}), specs)

    assert issues == []
    assert parsed is not None and parsed.values == {}
    assert apply(specs, parsed)[0][0].source == "config"


# --- tighten: lower, never raise (req 8.3, 8.4) ---------------------------------


def test_a_run_tightens_one_value_and_leaves_another() -> None:
    """Only the metric that improved moves, and the run reports it (req 8.3)."""
    stored = baseline({CYCLOMATIC: 12, FILE_LINES: 210})

    updated, tightened = tighten(stored, {CYCLOMATIC: 9, FILE_LINES: 210})

    (limit,) = tightened
    assert limit.rule == CYCLOMATIC
    assert limit.previous == pytest.approx(12.0)
    assert limit.current == pytest.approx(9.0)
    assert updated.values == {CYCLOMATIC: 9.0, FILE_LINES: 210.0}
    assert updated.captured_at == STAMP
    assert stored.values[CYCLOMATIC] == pytest.approx(12.0)


def test_tighten_never_raises_a_value() -> None:
    """A metric that got worse leaves the baseline untouched (req 8.4)."""
    stored = baseline({CYCLOMATIC: 9})

    updated, tightened = tighten(stored, {CYCLOMATIC: 20})

    assert tightened == []
    assert updated.values == {CYCLOMATIC: 9.0}


def test_tighten_ignores_a_metric_the_baseline_does_not_hold() -> None:
    """A newly configured threshold is added by a capture, never by a tightening run."""
    stored = baseline({CYCLOMATIC: 9})

    updated, tightened = tighten(stored, {FILE_LINES: 100})

    assert tightened == []
    assert updated.values == {CYCLOMATIC: 9.0}


def test_tighten_reports_every_lowered_limit_in_rule_order() -> None:
    """Two improvements are two reported tightenings, deterministically ordered (req 8.3)."""
    stored = baseline({CYCLOMATIC: 12, FILE_LINES: 210})

    updated, tightened = tighten(stored, {CYCLOMATIC: 9, FILE_LINES: 180})

    assert [limit.rule for limit in tightened] == [FILE_LINES, CYCLOMATIC]
    assert updated.values == {CYCLOMATIC: 9.0, FILE_LINES: 180.0}


# --- capture: the current worst value per threshold (req 8.1) -------------------


def test_capture_records_one_entry_per_configured_threshold_with_data(
    after: ProjectSnapshot,
) -> None:
    """Element maxima, a reduced population and a project metric, all keyed by rule (8.1)."""
    specs = [
        spec("routine", "CyclomaticStrict", Limit(max=10)),
        spec("class", "CountClassCoupled", Limit(max=12)),
        spec("file", "CountLineCode", Limit(max=500)),
        spec("project", "AVG:CyclomaticStrict", Limit(max=3)),
        spec("project", "MaxCyclomaticStrict", Limit(max=20)),
    ]

    captured = capture(after, specs, captured_at=STAMP)

    assert captured.captured_at == STAMP
    assert captured.version == 1
    assert captured.values == pytest.approx(
        {
            CYCLOMATIC: 12.0,
            "class.CountClassCoupled": 6.0,
            FILE_LINES: 210.0,
            "project.AVG:CyclomaticStrict": 6.125,
            "project.MaxCyclomaticStrict": 12.0,
        }
    )


def test_capture_skips_a_threshold_with_no_data(after: ProjectSnapshot) -> None:
    """A metric no entity carries yields no entry rather than a wrong one (req 5.5)."""
    specs = [
        spec("class", "PercentLackOfCohesion", Limit(max=70)),
        spec("project", "MEDIAN:CountParams", Limit(max=4)),
        spec("class", "CountClassCoupled", Limit(max=12)),
    ]

    captured = capture(after, specs, captured_at=STAMP)

    assert set(captured.values) == {"class.CountClassCoupled"}


def test_capture_records_the_minimum_of_a_minimum_only_threshold(after: ProjectSnapshot) -> None:
    """The worst value of a ``min`` limit is the lowest one, so that is what is recorded."""
    specs = [spec("file", "RatioCommentToCode", Limit(min=0.1))]

    captured = capture(after, specs, captured_at=STAMP)

    assert captured.values[RATIO] == pytest.approx(0.12)


def test_capture_stamps_the_current_time_when_none_is_given(after: ProjectSnapshot) -> None:
    """The default timestamp is a real ISO-8601 instant; tests pass their own."""
    captured = capture(after, [spec("file", "CountLineCode", Limit(max=500))])

    assert datetime.fromisoformat(captured.captured_at).tzinfo is not None


def test_capture_then_apply_flags_nothing_on_the_snapshot_it_captured(
    after: ProjectSnapshot,
) -> None:
    """A fresh capture narrows every limit to what the project already achieves (8.1, 8.2)."""
    specs = [
        spec("routine", "CyclomaticStrict", Limit(max=100)),
        spec("class", "CountClassCoupled", Limit(max=50)),
        spec("file", "CountLineCode", Limit(max=1000)),
        spec("file", "RatioCommentToCode", Limit(min=0.0)),
        spec("project", "AVG:CyclomaticStrict", Limit(max=50)),
    ]

    captured = capture(after, specs, captured_at=STAMP)
    thresholds, issues = apply(specs, captured)
    outcome = evaluate_thresholds(after, set(after.entities), thresholds)

    assert issues == []
    assert {threshold.source for threshold in thresholds} == {"baseline"}
    assert outcome.findings == []
