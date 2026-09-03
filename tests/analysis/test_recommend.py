"""Threshold recommendation: percentiles, the priced trade, the verdict and the tail.

Every population below is written out as an explicit list of values and every expected limit,
count and share is asserted as a **literal**. That is deliberate and it is this project's
recorded hazard: a boundary test whose expectation is computed from the constant under test
passes whatever that constant is, and this whole feature is about a constant. So
``TARGET_COVERAGE`` appears in exactly one test -- the one asserting what its shipped value is
-- and nowhere else; the rest name 0.95 by hand or pass their own target.

The other hazard the suite is built against is a test whose failure mode is unreachable. The
central claim here is a *negative* one -- "a distribution far below its limit is reported
``keep``, never tightened" -- and a negative claim is exactly the kind that passes because
nothing happened. Each of those is therefore paired with a population that does produce a
proposal, so the machinery is shown to be capable of the answer it is declining to give.
"""

from __future__ import annotations

from typing import Any

import pytest

from scitools_hook.analysis.recommend import (
    OFFENDERS_SHOWN,
    TAIL_RATIO,
    TARGET_COVERAGE,
    MetricAdvice,
    Recommendation,
    deviations,
    distribution,
    percentile,
    plural,
    readable_at_least,
    recommend,
    share_within,
)
from scitools_hook.config.metric_names import Scope
from scitools_hook.config.models import Limit, ThresholdSpec
from scitools_hook.models.snapshot import ProjectSnapshot

CYCLOMATIC = "routine.CyclomaticStrict"


def spec(scope: Scope, metric: str, **limit: float) -> ThresholdSpec:
    """One configured threshold, built the way the loader builds it."""
    return ThresholdSpec.model_validate({"scope": scope, "metric": metric, "limit": Limit(**limit)})


def _record(scope: str, path: str, longname: str, line: int, **metrics: float) -> dict[str, Any]:
    """One entity in the wire shape ``ProjectSnapshot`` validates."""
    return {
        "ref": {
            "key": {"scope": scope, "path": path, "longname": longname, "parameters": None},
            "kind": f"Python {scope.title()}",
            "name": longname.rsplit(".", 1)[-1],
            "line": line,
        },
        "language": "Python",
        "metrics": dict(metrics),
        "archs": [],
    }


def snapshot_of(
    values: list[float], metric: str = "CyclomaticStrict", scope: Scope = "routine"
) -> ProjectSnapshot:
    """A project whose ``scope`` entities carry exactly ``values`` for ``metric``.

    One entity per value, each in its own file with its own line, so an assertion about the
    worst offenders is an assertion about identifiable entities and not about a tie-break.
    """
    entities = [
        _record(scope, f"src/f{index}.py", f"mod.f{index}", index + 1, **{metric: value})
        for index, value in enumerate(values)
    ]
    return ProjectSnapshot.model_validate(
        {"side": "after", "languages": ["Python"], "entities": entities}
    )


def only(result: Recommendation) -> MetricAdvice:
    """The single piece of advice a one-threshold run produced."""
    assert result.skipped == (), result.skipped
    (advice,) = result.advice
    return advice


def priced(advice: MetricAdvice) -> dict[float, int]:
    """The trade table as ``limit -> entities outside``."""
    return {candidate.limit: candidate.outside for candidate in advice.candidates}


# --- the readable ladder ----------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.0, 1.0),
        (0.5, 1.0),
        (1.0, 1.0),
        (7.0, 8.0),
        (8.0, 8.0),
        (9.0, 10.0),
        (11.0, 12.0),
        (16.0, 20.0),
        (81.0, 100.0),
        (101.0, 120.0),
        (570.0, 600.0),
        (2517.0, 3000.0),
    ],
)
def test_a_proposal_is_rounded_up_to_a_number_an_operator_would_write(
    value: float, expected: float
) -> None:
    """The ladder rounds **up**, so rounding can only widen a limit, never tighten one.

    Each pair is a literal. A limit of 7 or 570 is not a limit anybody writes by hand, and a
    block nobody pastes is a feature nobody uses.
    """
    assert readable_at_least(value) == expected


def test_the_ladder_stops_rather_than_proposing_a_limit_that_switches_a_rule_off() -> None:
    """Above a thousand billion there is no level left to propose; the answer is ``None``.

    The reachable case is ``CountPath``, whose maximum on a real repository is 955,514,880 --
    inside the ladder -- so this asserts the guard with the value that trips it rather than
    with the one that motivated it.
    """
    assert readable_at_least(1e12) == 1e12
    assert readable_at_least(1.1e12) is None


# --- nearest-rank percentiles -----------------------------------------------------


def test_a_percentile_is_a_value_some_entity_actually_has() -> None:
    """Nearest rank, not interpolation: ten integers give integer percentiles."""
    values = [1.0, 1.0, 2.0, 3.0, 5.0, 8.0, 13.0, 21.0, 34.0, 55.0]

    shape = distribution(values)

    assert shape.count == 10
    assert shape.p50 == 5.0
    assert shape.p90 == 34.0
    assert shape.p95 == 55.0
    assert shape.p99 == 55.0
    assert shape.maximum == 55.0


def test_the_percentile_and_the_coverage_it_implies_can_never_disagree() -> None:
    """``share_within(percentile(v, s)) >= s`` -- the property the report's two halves share.

    The population is deliberately handed over **unsorted**, in the order a snapshot walk
    produces. The first implementation documented "ascending" and trusted the caller: it
    answered 1.0 for the median of this list, whose true median is 3.0, and reported a coverage
    of 29% for its own 50th percentile. Nothing else in the suite would have caught it.
    """
    values = [float(index % 7) for index in range(200)]
    assert values != sorted(values), "the input must be unsorted for this test to mean anything"

    assert percentile(values, 0.5) == 3.0
    for share in (0.5, 0.9, 0.95, 0.99, 1.0):
        assert share_within(values, percentile(values, share)) >= share


def test_a_single_entity_is_its_own_every_percentile() -> None:
    """The degenerate population still answers, because a one-routine repository exists."""
    assert percentile([4.0], 0.95) == 4.0
    assert distribution([4.0]).maximum == 4.0


# --- the verdict: keep -------------------------------------------------------------


def test_a_limit_the_repository_is_already_inside_is_kept_not_tightened() -> None:
    """The central claim, and the one this feature exists to get right.

    Ninety-six routines at 1 and four at 20 put the 95th percentile at **1** against a
    configured limit of **10**. A recommender that reported a percentile would propose
    tightening 10 to 1 and light up four routines for nothing. The answer here is ``keep``,
    and the tighter rows are still priced so an operator who wants them can see what they cost.
    """
    project = snapshot_of([1.0] * 96 + [20.0] * 4)

    advice = only(recommend(project, [spec("routine", "CyclomaticStrict", max=10)], 0.95))

    assert advice.verdict == "keep"
    assert advice.proposed is None
    assert advice.configured == 10.0
    assert advice.distribution.p95 == 1.0
    assert advice.share_inside == pytest.approx(0.96)
    assert priced(advice)[10.0] == 4


def test_a_kept_limit_still_prices_the_tighter_lines_an_operator_might_choose() -> None:
    """``keep`` is a recommendation, not a refusal to inform: the cheaper rows are on the page."""
    project = snapshot_of([1.0] * 90 + [7.0] * 10)

    advice = only(recommend(project, [spec("routine", "CyclomaticStrict", max=10)], 0.95))

    assert advice.verdict == "keep"
    assert priced(advice) == {6.0: 10, 8.0: 0, 10.0: 0}


def test_a_value_exactly_on_the_limit_is_inside_it() -> None:
    """``analysis.thresholds`` breaches on ``>``; the trade table must count the same way."""
    project = snapshot_of([10.0] * 50 + [11.0] * 50)

    advice = only(recommend(project, [spec("routine", "CyclomaticStrict", max=10)], 0.5))

    assert advice.share_inside == pytest.approx(0.5)
    assert priced(advice)[10.0] == 50
    assert priced(advice)[8.0] == 100


def test_coverage_exactly_at_the_target_counts_as_fitting() -> None:
    """Nineteen of twenty inside is 95% exactly, and 95% is enough: the bound is inclusive.

    Both numbers are literals. The paired test below moves **one** entity across and requires
    the opposite verdict, so neither answer can be the one this code always gives.
    """
    project = snapshot_of([1.0] * 19 + [50.0])

    advice = only(recommend(project, [spec("routine", "CyclomaticStrict", max=10)], 0.95))

    assert advice.share_inside == pytest.approx(0.95)
    assert advice.verdict == "keep"


def test_one_entity_short_of_the_target_is_not_fitting() -> None:
    """Eighteen of twenty is 90%, and the same limit is now reported as outgrown."""
    project = snapshot_of([1.0] * 18 + [50.0] * 2)

    advice = only(recommend(project, [spec("routine", "CyclomaticStrict", max=10)], 0.95))

    assert advice.share_inside == pytest.approx(0.90)
    assert advice.verdict == "raise"


# --- the verdict: raise ------------------------------------------------------------


def test_a_limit_the_repository_has_outgrown_is_raised_to_the_smallest_that_fits() -> None:
    """Ten per cent of the routines outside a limit is a gate that blocks on existing debt.

    Ninety routines at 1 and ten at 20 leave 10% outside a limit of 10, so the limit does not
    describe this repository. The proposal is 20 -- the smallest ladder rung that contains
    95% -- and the table prices both, which is the decision the operator is being handed.
    """
    project = snapshot_of([1.0] * 90 + [20.0] * 10)

    advice = only(recommend(project, [spec("routine", "CyclomaticStrict", max=10)], 0.95))

    assert advice.verdict == "raise"
    assert advice.proposed == 20.0
    assert priced(advice)[10.0] == 10
    assert priced(advice)[20.0] == 0


def test_a_proposal_is_never_below_the_limit_already_in_force() -> None:
    """The ladder rung that fits can sit *below* the configured limit; that is still ``keep``.

    Values of 3 against a configured 10 make the fitting rung 3. Proposing it would be a
    baseline with extra steps, and it is the exact failure the module docstring names.
    """
    project = snapshot_of([3.0] * 100)

    advice = only(recommend(project, [spec("routine", "CyclomaticStrict", max=10)], 0.95))

    assert advice.verdict == "keep"
    assert advice.proposed is None
    assert advice.distribution.p95 == 3.0
    assert priced(advice)[10.0] == 0


def test_the_proposed_row_is_marked_in_the_trade_table() -> None:
    """The proposal is one priced row of the table, not a number beside it."""
    project = snapshot_of([1.0] * 90 + [20.0] * 10)

    advice = only(recommend(project, [spec("routine", "CyclomaticStrict", max=10)], 0.95))

    marked = [candidate.limit for candidate in advice.candidates if candidate.proposed]
    configured = [candidate.limit for candidate in advice.candidates if candidate.configured]
    assert marked == [20.0]
    assert configured == [10.0]


# --- the tail --------------------------------------------------------------------


def test_a_maximum_far_above_the_bulk_is_reported_as_a_tail_not_as_a_level() -> None:
    """``CountPath``'s shape, in miniature: a median of 1 and one routine at 5,000.

    The limit is kept -- every routine is inside 100 -- and the metric is flagged so the
    report can say what the actual problem is, which is one routine and not a number.
    """
    project = snapshot_of([1.0] * 99 + [5000.0], metric="CountPath")

    advice = only(recommend(project, [spec("routine", "CountPath", max=100)], 0.95))

    assert advice.verdict == "keep"
    assert advice.distribution.p95 == 1.0
    assert advice.tail_ratio == 5000.0
    assert advice.tail_dominated is True


def test_a_population_with_no_tail_is_not_flagged() -> None:
    """``CyclomaticStrict`` on a real repository scores about 6x and must stay unflagged.

    45 against a 95th percentile of 7 is 6.4, so this pins the *other* side of the same
    constant: a metric whose worst entity is merely bad is not an outlier report.
    """
    project = snapshot_of([7.0] * 99 + [45.0])

    advice = only(recommend(project, [spec("routine", "CyclomaticStrict", max=10)], 0.95))

    assert advice.distribution.p95 == 7.0
    assert advice.tail_ratio == pytest.approx(45.0 / 7.0)
    assert advice.tail_dominated is False


def test_the_shipped_constants_are_the_numbers_the_documentation_claims() -> None:
    """The two numbers every verdict above depends on, asserted once, as literals.

    Nothing else in this file reads them. If either is edited, this test fails and the
    behavioural tests keep passing -- which is the point: a change to the policy has to be a
    deliberate edit here rather than a silent re-baselining of every expectation.
    """
    assert TARGET_COVERAGE == 0.95
    assert TAIL_RATIO == 50.0
    assert OFFENDERS_SHOWN == 3


# --- who the offenders are ---------------------------------------------------------


def test_the_worst_entities_are_named_worst_first_and_capped() -> None:
    """A count without names is a number generator; the report has to say which routines."""
    project = snapshot_of([1.0, 9.0, 4.0, 30.0, 12.0])

    advice = only(recommend(project, [spec("routine", "CyclomaticStrict", max=10)], 0.95))

    assert [item.value for item in advice.offenders] == [30.0, 12.0, 9.0]
    assert [item.longname for item in advice.offenders] == ["mod.f3", "mod.f4", "mod.f1"]
    assert [item.path for item in advice.offenders] == [
        "src/f3.py",
        "src/f4.py",
        "src/f1.py",
    ]
    assert [item.line for item in advice.offenders] == [4, 5, 2]


# --- what is not measured, and why -------------------------------------------------


def test_a_population_threshold_is_reported_as_unmeasured_rather_than_dropped() -> None:
    """A stats prefix reduces the whole scope, so no per-entity trade exists for it."""
    project = snapshot_of([1.0, 2.0])

    result = recommend(
        project,
        [
            spec("project", "AVG:CyclomaticStrict", max=3),
            spec("routine", "CyclomaticStrict", max=10),
        ],
        0.95,
    )

    assert [item.rule for item in result.advice] == [CYCLOMATIC]
    assert [item.rule for item in result.skipped] == ["project.AVG:CyclomaticStrict"]
    assert "no per-entity trade" in result.skipped[0].reason


def test_a_minimum_only_threshold_is_reported_as_unmeasured() -> None:
    """This measures ceilings; a comment-ratio floor is a different question."""
    project = snapshot_of([0.2, 0.4], metric="RatioCommentToCode", scope="file")

    result = recommend(project, [spec("file", "RatioCommentToCode", min=0.1)], 0.95)

    assert result.advice == ()
    assert [item.rule for item in result.skipped] == ["file.RatioCommentToCode"]
    assert "only a minimum" in result.skipped[0].reason


def test_a_metric_no_entity_carries_is_reported_as_unmeasured() -> None:
    """``PercentLackOfCohesion`` is unavailable for Python; the report says so rather than
    inventing a distribution from an empty vector."""
    project = snapshot_of([1.0, 2.0])

    result = recommend(project, [spec("class", "PercentLackOfCohesion", max=70)], 0.95)

    assert result.advice == ()
    assert [item.rule for item in result.skipped] == ["class.PercentLackOfCohesion"]
    assert "no value" in result.skipped[0].reason


def test_the_run_counts_what_it_measured_per_scope() -> None:
    """The denominator of every share in the report, and the plural that names it."""
    project = ProjectSnapshot.model_validate(
        {
            "side": "after",
            "languages": ["Python"],
            "entities": [
                _record("routine", "src/a.py", "a.one", 1, CyclomaticStrict=1),
                _record("routine", "src/a.py", "a.two", 5, CyclomaticStrict=2),
                _record("class", "src/a.py", "a.K", 9, CountDeclMethod=3),
                _record("file", "src/a.py", "src/a.py", 1, CountLineCode=20),
            ],
        }
    )

    result = recommend(project, [spec("routine", "CyclomaticStrict", max=10)], 0.95)

    assert result.counts == {"routine": 2, "class": 1, "file": 1}


def test_a_scope_is_pluralised_rather_than_given_a_trailing_s() -> None:
    """``1345 classs`` is what the first run against a real repository printed."""
    assert plural("class", 1345) == "classes"
    assert plural("class", 1) == "class"
    assert plural("routine", 2) == "routines"


# --- the pasteable deviations ------------------------------------------------------


def test_only_a_proposal_produces_a_line_to_paste() -> None:
    """A ``keep`` writes nothing: a file restating the value already in force decided nothing."""
    kept = snapshot_of([1.0] * 100)
    outgrown = snapshot_of([1.0] * 90 + [20.0] * 10)
    threshold = [spec("routine", "CyclomaticStrict", max=10)]

    assert deviations(recommend(kept, threshold, 0.95)) == []
    (line,) = deviations(recommend(outgrown, threshold, 0.95))
    assert (line.scope, line.metric, line.limit) == ("routine", "CyclomaticStrict", 20.0)


def test_every_pasteable_line_carries_the_measurement_that_produced_it() -> None:
    """The house style: a line that deviates says what was measured, not that it was decided."""
    project = snapshot_of([1.0] * 90 + [20.0] * 10)

    (line,) = deviations(recommend(project, [spec("routine", "CyclomaticStrict", max=10)], 0.95))

    assert "measured 100 routines" in line.evidence
    assert "p50 1" in line.evidence
    assert "p95 20" in line.evidence
    assert "max 20" in line.evidence
    assert "10 outside (10.0%) at the configured 10" in line.evidence
    assert "0 outside (0.0%) at 20" in line.evidence


def test_an_empty_project_recommends_nothing_and_claims_nothing() -> None:
    """No entities is an answer, not a distribution over zero values."""
    empty = ProjectSnapshot.model_validate({"side": "after", "languages": [], "entities": []})

    result = recommend(empty, [spec("routine", "CyclomaticStrict", max=10)], 0.95)

    assert result.advice == ()
    assert result.counts == {}
    assert [item.rule for item in result.skipped] == [CYCLOMATIC]
