"""Pre-existing, strict-mode and blocking classification (task 4.2; req 4.6, 4.7, 7.9).

The matrix the task asks for is driven through the real evaluators rather than through
hand-built findings: a synthetic pair of snapshots holds one routine per cell of
*worse / same / better* x *new / existing* x *under / over the limit*, the check pipeline's
order (thresholds -> attach_before -> ratchet -> classify) is replayed over them, and the
``preexisting``/``blocking`` flags of whatever findings that produces are compared with the
expected ones for strict mode on and off. The focused tests below pin the pieces the matrix
cannot reach: the severity map, warnings, and non-threshold kinds.
"""

from __future__ import annotations

from typing import Literal

import pytest

from scitools_hook.analysis.classify import classify
from scitools_hook.analysis.ratchet import attach_before, evaluate_ratchet
from scitools_hook.analysis.thresholds import evaluate_thresholds
from scitools_hook.config.metric_names import parse_metric_name
from scitools_hook.config.models import Limit, Severity, SeverityMap, ThresholdSpec
from scitools_hook.models.findings import EffectiveThreshold, Finding
from scitools_hook.models.snapshot import EntityKey, EntityRecord, EntityRef, ProjectSnapshot

PATH = "src/cli/app.py"
METRIC = "CyclomaticStrict"
RULE = "routine.CyclomaticStrict"
LIMIT = Limit(max=10)
"""One limit for the whole matrix: 12 and 15 are over it, 5 and 8 are under it."""

CELLS: dict[str, tuple[float | None, float]] = {
    "worse_over": (12.0, 15.0),
    "same_over": (12.0, 12.0),
    "better_over": (20.0, 12.0),
    "new_over": (None, 15.0),
    "worse_under": (5.0, 8.0),
    "same_under": (5.0, 5.0),
    "better_under": (8.0, 5.0),
    "new_under": (None, 5.0),
}
"""Cell name -> (value before the change or ``None`` for a new routine, value after)."""

Flags = tuple[bool, bool]
"""``(preexisting, blocking)`` of one finding."""

MATRIX: dict[tuple[str, bool], dict[str, Flags]] = {
    # A routine that worsened past the limit: both findings block, neither is pre-existing.
    ("worse_over", False): {"threshold": (False, True), "ratchet": (False, True)},
    ("worse_over", True): {"threshold": (False, True), "ratchet": (False, True)},
    # Over the limit before and no worse now: pre-existing, and blocking only in strict mode.
    ("same_over", False): {"threshold": (True, False)},
    ("same_over", True): {"threshold": (True, True)},
    ("better_over", False): {"threshold": (True, False)},
    ("better_over", True): {"threshold": (True, True)},
    # A new routine has no before value, so its threshold violation is its own.
    ("new_over", False): {"threshold": (False, True)},
    ("new_over", True): {"threshold": (False, True)},
    # Under the limit: only the ratchet can speak, and only when the value worsened.
    ("worse_under", False): {"ratchet": (False, True)},
    ("worse_under", True): {"ratchet": (False, True)},
    ("same_under", False): {},
    ("same_under", True): {},
    ("better_under", False): {},
    ("better_under", True): {},
    ("new_under", False): {},
    ("new_under", True): {},
}
"""(cell, strict) -> the finding kinds expected for that routine and their flags."""


def key_of(cell: str) -> EntityKey:
    """The entity key of the routine standing for ``cell``."""
    return EntityKey(scope="routine", path=PATH, longname=f"app.{cell}", parameters="")


def record(cell: str, value: float) -> EntityRecord:
    """One routine record carrying ``value`` for the matrix metric."""
    key = key_of(cell)
    ref = EntityRef(key=key, kind="Python Function", name=cell, line=1)
    return EntityRecord(ref=ref, language="Python", metrics={METRIC: value})


def side(which: Literal["before", "after"]) -> ProjectSnapshot:
    """The snapshot of one side, holding every cell that exists on it."""
    records = []
    for cell, (was, now) in CELLS.items():
        value = was if which == "before" else now
        if value is not None:
            records.append(record(cell, value))
    return ProjectSnapshot(
        side=which, languages=["Python"], entities={item.key: item for item in records}
    )


def spec(severity: Severity = "error") -> EffectiveThreshold:
    """The one threshold the matrix is evaluated against."""
    configured = ThresholdSpec(scope="routine", metric=METRIC, limit=LIMIT, severity=severity)
    return EffectiveThreshold(
        spec=configured, metric=parse_metric_name(METRIC), limit=LIMIT, source="config"
    )


def pipeline(strict: bool, severities: SeverityMap | None = None) -> list[Finding]:
    """Replay the check pipeline's rule order over the matrix snapshots."""
    after, before = side("after"), side("before")
    keys = set(after.entities) | set(before.entities)
    thresholds = [spec()]
    findings = attach_before(evaluate_thresholds(after, keys, thresholds).findings, before)
    findings += evaluate_ratchet(after, before, keys, thresholds)
    return classify(findings, strict, severities or {})


@pytest.mark.parametrize(("cell", "strict"), sorted(MATRIX))
def test_the_classification_matrix(cell: str, strict: bool) -> None:
    """Every cell of worse/same/better x new/existing x under/over x strict (4.4-4.7)."""
    findings = [
        finding
        for finding in pipeline(strict)
        if finding.entity is not None and finding.entity.key == key_of(cell)
    ]

    expected = MATRIX[(cell, strict)]
    assert len(findings) == len(expected)
    assert {f.kind: (f.preexisting, f.blocking) for f in findings} == expected


def test_the_matrix_produces_exactly_the_expected_findings() -> None:
    """Nothing is dropped and nothing extra is invented: six findings over eight cells."""
    findings = pipeline(False)

    assert len(findings) == sum(len(MATRIX[(cell, False)]) for cell in CELLS)
    assert len(findings) == 6


def threshold_finding(value: float, before: float | None, limit: float) -> Finding:
    """A threshold finding as step 4.1 and the ratchet's before-attachment leave it."""
    return Finding(
        kind="threshold",
        rule=RULE,
        metric=METRIC,
        scope="routine",
        value=value,
        before=before,
        limit=limit,
        severity="error",
        blocking=True,
        message="a routine is over its limit",
    )


def warning_finding(value: float, before: float | None, limit: float) -> Finding:
    """The same finding at ``warning`` severity, which never blocks on its own (req 3.7)."""
    return threshold_finding(value, before, limit).model_copy(
        update={"severity": "warning", "blocking": False}
    )


def test_a_severity_map_entry_overrides_the_severity_and_the_blocking_flag() -> None:
    """A rule configured as a warning stops blocking, whatever the evaluator decided (3.7)."""
    (classified,) = classify([threshold_finding(15.0, None, 10.0)], False, {RULE: "warning"})

    assert classified.severity == "warning"
    assert classified.blocking is False


def test_a_severity_map_entry_can_promote_a_warning_to_a_blocking_error() -> None:
    """The map is the last word on severity, in both directions (req 3.7)."""
    (classified,) = classify([warning_finding(15.0, None, 10.0)], False, {RULE: "error"})

    assert classified.severity == "error"
    assert classified.blocking is True


def test_a_severity_map_entry_for_another_rule_changes_nothing() -> None:
    """Severities are keyed by rule name; an entry for a different rule does not apply."""
    (classified,) = classify(
        [threshold_finding(15.0, None, 10.0)], False, {"routine.MaxNesting": "warning"}
    )

    assert classified.severity == "error"
    assert classified.blocking is True


def test_a_warning_never_blocks_even_in_strict_mode() -> None:
    """Only ``error`` findings block a commit (req 3.7, 7.9)."""
    (classified,) = classify([warning_finding(15.0, 5.0, 10.0)], True, {})

    assert classified.severity == "warning"
    assert classified.blocking is False
    assert classified.preexisting is False


def test_a_ratchet_finding_is_never_pre_existing() -> None:
    """The value just got worse, so a ratchet finding blocks without strict mode (req 4.4).

    Its value (8) is under the limit (10), which is the shape requirement 4.4 is about. Read
    as a threshold finding it would look like a value sitting comfortably inside a minimum
    and therefore pre-existing, so the kind of the finding has to decide.
    """
    ratchet = threshold_finding(8.0, 5.0, 10.0).model_copy(update={"kind": "ratchet"})

    (classified,) = classify([ratchet], False, {})

    assert classified.preexisting is False
    assert classified.blocking is True


def test_a_value_below_a_min_limit_that_did_not_worsen_is_pre_existing() -> None:
    """The ``min`` side of the pre-existing test: the ratio was already too low (req 4.6)."""
    improved = threshold_finding(0.1, 0.05, 0.2)

    (classified,) = classify([improved], False, {})

    assert classified.preexisting is True
    assert classified.blocking is False


def test_a_value_below_a_min_limit_that_fell_further_blocks() -> None:
    """A pre-existing violation that got worse is not pre-existing any more (req 4.4)."""
    worsened = threshold_finding(0.05, 0.1, 0.2)

    (classified,) = classify([worsened], False, {})

    assert classified.preexisting is False
    assert classified.blocking is True


def test_a_before_value_within_the_limit_does_not_make_a_finding_pre_existing() -> None:
    """Pre-existing means the limit was already broken, not merely that a before exists."""
    fresh = threshold_finding(15.0, 8.0, 10.0)

    (classified,) = classify([fresh], False, {})

    assert classified.preexisting is False
    assert classified.blocking is True


def test_a_finding_that_declares_itself_pre_existing_keeps_that_status() -> None:
    """Other evaluators may judge pre-existence themselves; classify only adds to it."""
    structural = Finding(
        kind="structural",
        rule="structure.file_cycle",
        scope="file",
        severity="error",
        blocking=True,
        preexisting=True,
        message="two files already depended on each other",
    )

    (lenient,) = classify([structural], False, {})
    (strict,) = classify([structural], True, {})

    assert (lenient.preexisting, lenient.blocking) == (True, False)
    assert (strict.preexisting, strict.blocking) == (True, True)


def test_classify_returns_new_findings_and_leaves_the_originals_untouched() -> None:
    """Findings are a shared contract; the classification step never edits them in place."""
    original = threshold_finding(12.0, 20.0, 10.0)

    (classified,) = classify([original], False, {})

    assert original.preexisting is False
    assert original.blocking is True
    assert classified is not original
    assert (classified.preexisting, classified.blocking) == (True, False)
