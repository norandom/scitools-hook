"""Threshold evaluation over entities and populations (task 4.1; req 3.4, 3.6, 5.1-5.6).

Every case runs against the synthetic ``after`` snapshot, so the numbers below are the ones
`tests/fixtures/__init__.py` documents: ``app.build_parser`` is the worst routine
(CyclomaticStrict 12, CountLineCode 75), ``engine.Engine`` the worst class
(CountClassCoupled 6), ``src/analysis/engine.py`` the largest file (CountLineCode 210), the
routine and project populations of ``CyclomaticStrict`` both average 6.125, the class
records carry no ``CountLineCode``, and ``PercentLackOfCohesion`` is unavailable for Python.
"""

from __future__ import annotations

from typing import Literal

import pytest
from fixtures import snapshot_fixture

from scitools_hook.analysis.thresholds import ThresholdOutcome, evaluate_thresholds
from scitools_hook.config.metric_names import Scope, parse_metric_name
from scitools_hook.config.models import IgnoreRules, Limit, Severity, ThresholdSpec
from scitools_hook.models.findings import EffectiveThreshold, Finding
from scitools_hook.models.snapshot import EntityKey, ProjectSnapshot

APP = "src/cli/app.py"
ENGINE = "src/analysis/engine.py"
BUILD_PARSER = EntityKey(scope="routine", path=APP, longname="app.build_parser", parameters="")
ENGINE_CLASS = EntityKey(scope="class", path=ENGINE, longname="engine.Engine")


@pytest.fixture
def after() -> ProjectSnapshot:
    """The ``after`` side of the synthetic project; every test evaluates this snapshot."""
    return snapshot_fixture("after")


def threshold(
    scope: Scope,
    metric: str,
    limit: Limit,
    severity: Severity = "error",
    source: Literal["config", "baseline"] = "config",
) -> EffectiveThreshold:
    """One configured threshold whose effective limit equals the configured one."""
    spec = ThresholdSpec(scope=scope, metric=metric, limit=limit, severity=severity)
    return EffectiveThreshold(
        spec=spec, metric=parse_metric_name(metric), limit=limit, source=source
    )


def evaluate(snapshot: ProjectSnapshot, *specs: EffectiveThreshold) -> ThresholdOutcome:
    """Evaluate ``specs`` over every entity of ``snapshot``."""
    return evaluate_thresholds(snapshot, set(snapshot.entities), specs)


def longnames(findings: list[Finding]) -> set[str]:
    """The qualified names the findings point at."""
    return {finding.entity.key.longname for finding in findings if finding.entity is not None}


def test_a_value_over_the_maximum_is_reported(after: ProjectSnapshot) -> None:
    """A routine above its cyclomatic limit is a blocking error finding (req 5.1)."""
    outcome = evaluate(after, threshold("routine", "CyclomaticStrict", Limit(max=10)))

    (finding,) = outcome.findings
    assert finding.kind == "threshold"
    assert finding.rule == "routine.CyclomaticStrict"
    assert finding.metric == "CyclomaticStrict"
    assert finding.scope == "routine"
    assert finding.entity is not None and finding.entity.key == BUILD_PARSER
    assert finding.path == APP
    assert finding.line == 34
    assert finding.value == pytest.approx(12.0)
    assert finding.limit == pytest.approx(10.0)
    assert finding.severity == "error"
    assert finding.blocking is True
    assert "12" in finding.message and "10" in finding.message


def test_a_class_and_a_file_over_their_maxima_are_reported(after: ProjectSnapshot) -> None:
    """Class and file scopes are evaluated the same way as routines (req 5.2, 5.3)."""
    outcome = evaluate(
        after,
        threshold("class", "CountClassCoupled", Limit(max=5)),
        threshold("file", "CountLineCode", Limit(max=100)),
    )

    assert longnames([f for f in outcome.findings if f.scope == "class"]) == {"engine.Engine"}
    assert longnames([f for f in outcome.findings if f.scope == "file"]) == {
        ENGINE,
        APP,
        "src/understand/adapter.py",
    }


def test_a_ratio_under_the_minimum_is_reported(after: ProjectSnapshot) -> None:
    """A ``min`` bound catches a comment-to-code ratio that is too low (req 5.3)."""
    outcome = evaluate(
        after, threshold("file", "RatioCommentToCode", Limit(min=0.2), severity="warning")
    )

    assert longnames(outcome.findings) == {
        ENGINE,
        APP,
        "src/analysis/rules.py",
        "src/understand/adapter.py",
    }
    below = next(f for f in outcome.findings if f.path == APP)
    assert below.value == pytest.approx(0.12)
    assert below.limit == pytest.approx(0.2)
    assert below.severity == "warning"
    assert below.blocking is False
    assert "below" in below.message


def test_both_bounds_of_one_limit_are_checked(after: ProjectSnapshot) -> None:
    """A limit with ``max`` and ``min`` reports whichever bound an entity breaks."""
    outcome = evaluate(after, threshold("file", "RatioCommentToCode", Limit(max=0.2, min=0.15)))

    breaches = {finding.path: finding.limit for finding in outcome.findings}
    assert breaches == {APP: pytest.approx(0.15), "src/util/text.py": pytest.approx(0.2)}


def test_a_stats_prefixed_threshold_is_reduced_over_the_population(
    after: ProjectSnapshot,
) -> None:
    """``AVG:`` is evaluated against the population, not against entities (req 3.4, 5.4)."""
    outcome = evaluate(after, threshold("project", "AVG:CyclomaticStrict", Limit(max=3)))

    (finding,) = outcome.findings
    assert finding.rule == "project.AVG:CyclomaticStrict"
    assert finding.metric == "AVG:CyclomaticStrict"
    assert finding.scope == "project"
    assert finding.entity is None
    assert finding.path == ""
    assert finding.value == pytest.approx(6.125)
    assert finding.limit == pytest.approx(3.0)


def test_a_stats_prefix_on_an_element_scope_is_reduced_over_that_scope(
    after: ProjectSnapshot,
) -> None:
    """The prefix, not the scope, makes a threshold a population one (req 3.4, 5.4).

    ``routine`` has entities of its own, so without the prefix this spec would be checked
    entity by entity. With it, the eight routine ``CyclomaticStrict`` values are reduced to
    their mean of 6.125 and reported as one project-level finding that belongs to no entity
    and tracks no highest value.
    """
    outcome = evaluate(after, threshold("routine", "AVG:CyclomaticStrict", Limit(max=3)))

    (finding,) = outcome.findings
    assert finding.rule == "routine.AVG:CyclomaticStrict"
    assert finding.metric == "AVG:CyclomaticStrict"
    assert finding.scope == "routine"
    assert finding.entity is None
    assert finding.path == ""
    assert finding.value == pytest.approx(6.125)
    assert finding.limit == pytest.approx(3.0)
    assert outcome.highest == []


def test_a_stats_prefixed_threshold_within_its_limit_is_silent(after: ProjectSnapshot) -> None:
    """The same population under a looser limit produces nothing."""
    outcome = evaluate(after, threshold("project", "AVG:CyclomaticStrict", Limit(max=10)))

    assert outcome.findings == []
    assert outcome.reducer_failures == {}


def test_a_plain_project_metric_is_read_from_its_captured_vector(after: ProjectSnapshot) -> None:
    """Project metrics have no entities; they arrive as a single-value vector (req 5.4)."""
    outcome = evaluate(after, threshold("project", "MaxCyclomaticStrict", Limit(max=10)))

    (finding,) = outcome.findings
    assert finding.value == pytest.approx(12.0)
    assert finding.entity is None

    within = evaluate(after, threshold("project", "MaxCyclomaticStrict", Limit(max=15)))
    assert within.findings == []


def test_an_ignored_entity_is_excluded_and_counted(after: ProjectSnapshot) -> None:
    """An ignored routine is evaluated by no rule but still counted (req 3.6)."""
    outcome = evaluate_thresholds(
        after,
        set(after.entities),
        [threshold("routine", "CyclomaticStrict", Limit(max=10))],
        None,
        IgnoreRules(routines=[r"^app\.build_parser$"]),
    )

    assert outcome.findings == []
    assert outcome.ignored_counts == {"routine": 1}
    assert all(highest.value <= 8 for highest in outcome.highest)


def test_a_metric_absent_for_a_language_is_reported_once(after: ProjectSnapshot) -> None:
    """The snapshot's own ``unavailable`` map seeds the report, once per language (req 5.5).

    ``PercentLackOfCohesion`` is declared unavailable for Python by the extractor. Evaluating
    with an EMPTY key set means no entity is ever inspected, so the snapshot's declaration is
    the only possible source of the report; the entity-by-entity discovery of the same fact is
    pinned by the next test.
    """
    outcome = evaluate_thresholds(
        after, set(), [threshold("class", "PercentLackOfCohesion", Limit(max=70))]
    )

    assert outcome.findings == []
    assert outcome.highest == []
    assert outcome.unavailable == {"Python": ["PercentLackOfCohesion"]}


def test_a_metric_no_entity_carries_is_discovered_during_evaluation(
    after: ProjectSnapshot,
) -> None:
    """A metric missing from an entity is skipped for it and reported for its language.

    Requirement 5.5's first clause is about discovery while evaluating, not about the
    extractor's declaration: neither Python class record carries ``CountLineCode`` and the
    snapshot does not list it as unavailable, so the only way it can reach the report is
    entity by entity. Nothing is evaluated, so there is no finding and no highest value.
    """
    assert after.unavailable == {"Python": ["PercentLackOfCohesion"]}

    outcome = evaluate(after, threshold("class", "CountLineCode", Limit(max=100)))

    assert outcome.findings == []
    assert outcome.highest == []
    assert outcome.unavailable == {"Python": ["CountLineCode"]}


def test_the_catalogue_contributes_unavailable_metrics(after: ProjectSnapshot) -> None:
    """Metrics the catalogue already knows to be unavailable are reported too (req 5.5)."""
    outcome = evaluate_thresholds(
        after,
        set(after.entities),
        [threshold("routine", "CountParams", Limit(max=5))],
        {"C++": ["CountParams", "CountPath"]},
    )

    assert outcome.unavailable == {"C++": ["CountParams"]}


def test_a_population_that_cannot_be_reduced_is_reported_once(after: ProjectSnapshot) -> None:
    """A missing or unusable population is recorded per rule, not per attempt (req 5.4)."""
    spec = threshold("project", "AVG:CountPath", Limit(max=20))

    outcome = evaluate(after, spec, spec)

    assert outcome.findings == []
    assert list(outcome.reducer_failures) == ["project.AVG:CountPath"]
    assert "CountPath" in outcome.reducer_failures["project.AVG:CountPath"]


def test_highest_values_are_tracked_per_metric_and_ranked(after: ProjectSnapshot) -> None:
    """The worst value per metric is reported even when it is not a violation (req 5.6)."""
    outcome = evaluate(
        after,
        threshold("routine", "CyclomaticStrict", Limit(max=10)),
        threshold("routine", "CountLineCode", Limit(max=60)),
        threshold("file", "CountLineCode", Limit(max=500)),
    )

    assert [(h.scope, h.metric, h.value) for h in outcome.highest] == [
        ("file", "CountLineCode", 210.0),
        ("routine", "CountLineCode", 75.0),
        ("routine", "CyclomaticStrict", 12.0),
    ]
    worst_routine = outcome.highest[1]
    assert worst_routine.entity is not None and worst_routine.entity.key == BUILD_PARSER


def test_findings_leave_the_before_value_and_the_hint_to_later_steps(
    after: ProjectSnapshot,
) -> None:
    """The ratchet fills ``before`` and the pipeline attaches the hint, not this evaluator."""
    outcome = evaluate(
        after,
        threshold("routine", "CyclomaticStrict", Limit(max=3)),
        threshold("project", "AVG:CountLineCode", Limit(max=30)),
    )

    assert outcome.findings
    assert all(finding.before is None and finding.hint == "" for finding in outcome.findings)
    assert all(finding.preexisting is False for finding in outcome.findings)


def test_the_effective_limit_and_its_source_come_from_the_baseline(
    after: ProjectSnapshot,
) -> None:
    """An adaptive run checks the baseline limit and says where it came from (req 8.5)."""
    spec = ThresholdSpec(scope="class", metric="CountClassCoupled", limit=Limit(max=12))
    lowered = EffectiveThreshold(
        spec=spec,
        metric=parse_metric_name("CountClassCoupled"),
        limit=Limit(max=5),
        source="baseline",
    )

    (finding,) = evaluate(after, lowered).findings

    assert finding.limit == pytest.approx(5.0)
    assert finding.limit_source == "baseline"
    assert finding.entity is not None and finding.entity.key == ENGINE_CLASS


def test_only_the_requested_keys_are_evaluated(after: ProjectSnapshot) -> None:
    """Staged mode evaluates the affected entities, not the whole database (req 4.2)."""
    outcome = evaluate_thresholds(
        after, {ENGINE_CLASS}, [threshold("routine", "CyclomaticStrict", Limit(max=1))]
    )

    assert outcome.findings == []
    assert outcome.highest == []


def test_a_key_that_is_not_in_the_snapshot_is_skipped(after: ProjectSnapshot) -> None:
    """A deleted entity may still be in the affected set; it is not a failure."""
    gone = EntityKey(scope="routine", path=APP, longname="app.legacy_entry", parameters="")

    outcome = evaluate_thresholds(
        after, {gone, BUILD_PARSER}, [threshold("routine", "CyclomaticStrict", Limit(max=10))]
    )

    assert longnames(outcome.findings) == {"app.build_parser"}
