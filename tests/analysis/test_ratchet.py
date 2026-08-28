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

from typing import Literal

import pytest
from fixtures import snapshot_fixture

from scitools_hook.analysis.ratchet import attach_before, evaluate_ratchet
from scitools_hook.analysis.thresholds import evaluate_thresholds
from scitools_hook.config.metric_names import Scope, parse_metric_name
from scitools_hook.config.models import Limit, Severity, ThresholdSpec
from scitools_hook.models.findings import EffectiveThreshold, Finding
from scitools_hook.models.snapshot import EntityKey, ProjectSnapshot

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
    """One configured threshold whose effective limit equals the configured one."""
    spec = ThresholdSpec(scope=scope, metric=metric, limit=limit, severity=severity)
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


# --- attach_before ---------------------------------------------------------------


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
