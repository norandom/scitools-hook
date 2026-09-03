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

from scitools_hook.analysis.classify import (
    ACKNOWLEDGED_DETAIL,
    PARTIAL_WARNING,
    classify,
)
from scitools_hook.analysis.ratchet import attach_before, evaluate_ratchet
from scitools_hook.analysis.thresholds import evaluate_thresholds
from scitools_hook.config.metric_names import parse_metric_name
from scitools_hook.config.models import (
    Limit,
    ParseAcknowledgement,
    ParseSettings,
    RatchetSettings,
    Severity,
    SeverityMap,
    ThresholdSpec,
)
from scitools_hook.models.findings import PARSE_ERROR_RULE, EffectiveThreshold, Finding
from scitools_hook.models.snapshot import EntityKey, EntityRecord, EntityRef, ProjectSnapshot

PATH = "src/cli/app.py"
METRIC = "CyclomaticStrict"
RULE = "routine.CyclomaticStrict"
LIMIT = Limit(max=10)
"""One limit for the whole matrix: 11, 12 and 15 are over it, 5, 8 and 10 are not."""

LENIENT = RatchetSettings()
STRICT = RatchetSettings(strict=True)
FROZEN = RatchetSettings(below_limit_severity="error")
"""The shipped settings, requirement 4.7's strict mode, and the pre-11.15 freeze restored."""

CELLS: dict[str, tuple[float | None, float]] = {
    "worse_over": (12.0, 15.0),
    "same_over": (12.0, 12.0),
    "better_over": (20.0, 12.0),
    "new_over": (None, 15.0),
    "worse_under": (5.0, 8.0),
    "same_under": (5.0, 5.0),
    "better_under": (8.0, 5.0),
    "new_under": (None, 5.0),
    "crossing": (8.0, 12.0),
    "to_the_limit": (8.0, 10.0),
    "past_the_limit": (10.0, 11.0),
}
"""Cell name -> (value before the change or ``None`` for a new routine, value after).

The last three are task 11.15's boundary, written as literals against a literal limit of 10
so that nothing here is derived from the rule under test: 8 -> 12 crosses it, 8 -> 10 stops
exactly on it, and 10 -> 11 is the same one-step growth starting from the limit instead of
below it. ``to_the_limit`` and ``past_the_limit`` differ by one in both numbers and are the
pair that fails if the comparison is ever written ``<`` where it must be ``<=``.
"""

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
    # Under the limit: only the ratchet can speak, and since task 11.15 it speaks as a
    # warning -- the growth is reported and does not block, in strict mode as well, because
    # strict is requirement 4.7's word on *pre-existing violations* and this is neither.
    ("worse_under", False): {"ratchet": (False, False)},
    ("worse_under", True): {"ratchet": (False, False)},
    ("same_under", False): {},
    ("same_under", True): {},
    ("better_under", False): {},
    ("better_under", True): {},
    ("new_under", False): {},
    ("new_under", True): {},
    # Growth that crosses the limit blocks, and it blocks twice: the absolute threshold now
    # sees a violation whose before value was inside the limit, so it is not pre-existing.
    ("crossing", False): {"threshold": (False, True), "ratchet": (False, True)},
    ("crossing", True): {"threshold": (False, True), "ratchet": (False, True)},
    # Growth that stops exactly on the limit has not broken it: reported, not blocking, and
    # the absolute threshold says nothing at all because `value > max` is false at equality.
    ("to_the_limit", False): {"ratchet": (False, False)},
    ("to_the_limit", True): {"ratchet": (False, False)},
    # One more, from the limit rather than below it, and both findings block.
    ("past_the_limit", False): {"threshold": (False, True), "ratchet": (False, True)},
    ("past_the_limit", True): {"threshold": (False, True), "ratchet": (False, True)},
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


def pipeline(ratchet: RatchetSettings, severities: SeverityMap | None = None) -> list[Finding]:
    """Replay the check pipeline's rule order over the matrix snapshots."""
    after, before = side("after"), side("before")
    keys = set(after.entities) | set(before.entities)
    thresholds = [spec()]
    findings = attach_before(evaluate_thresholds(after, keys, thresholds).findings, before)
    findings += evaluate_ratchet(after, before, keys, thresholds)
    return classify(findings, ratchet, severities or {})


def cell_findings(findings: list[Finding], cell: str) -> list[Finding]:
    """The findings belonging to one matrix cell's routine."""
    return [
        finding
        for finding in findings
        if finding.entity is not None and finding.entity.key == key_of(cell)
    ]


@pytest.mark.parametrize(("cell", "strict"), sorted(MATRIX))
def test_the_classification_matrix(cell: str, strict: bool) -> None:
    """Every cell of worse/same/better x new/existing x under/over x strict (4.4-4.7)."""
    findings = cell_findings(pipeline(STRICT if strict else LENIENT), cell)

    expected = MATRIX[(cell, strict)]
    assert len(findings) == len(expected)
    assert {f.kind: (f.preexisting, f.blocking) for f in findings} == expected


def test_the_matrix_produces_exactly_the_expected_findings() -> None:
    """Nothing is dropped and nothing extra is invented: eleven findings over eleven cells."""
    findings = pipeline(LENIENT)

    assert len(findings) == sum(len(MATRIX[(cell, False)]) for cell in CELLS)
    assert len(findings) == 11


# --- growth inside the limit is reported and does not block (task 11.15) -----------


@pytest.mark.parametrize("cell", ["worse_under", "to_the_limit"])
def test_growth_inside_the_limit_is_reported_as_a_warning(cell: str) -> None:
    """The defect this task exists for: the finding is still made, and it stops refusing.

    Requirement 4.4 asks for a report of an entity that got worse while staying inside its
    limit, and that report is still here -- one ratchet finding, carrying both values. What
    changed is its severity, and with it the exit code of every commit that touches a routine
    and makes it one line longer.
    """
    (finding,) = cell_findings(pipeline(LENIENT), cell)

    assert finding.kind == "ratchet"
    assert finding.severity == "warning"
    assert finding.blocking is False
    assert (finding.before, finding.value, finding.limit) == (*CELLS[cell], 10.0)


@pytest.mark.parametrize("cell", ["crossing", "past_the_limit", "worse_over"])
def test_growth_that_reaches_the_limit_still_blocks(cell: str) -> None:
    """The other side of the boundary: nothing was traded away above the limit.

    ``crossing`` starts inside and ends outside, ``past_the_limit`` starts exactly on it, and
    ``worse_over`` was already over before the change. All three keep an error-severity
    ratchet finding that blocks.
    """
    (ratchet,) = [f for f in cell_findings(pipeline(LENIENT), cell) if f.kind == "ratchet"]

    assert ratchet.severity == "error"
    assert ratchet.blocking is True


def test_the_boundary_is_the_limit_itself_and_not_one_below_it() -> None:
    """8 -> 10 does not block and 10 -> 11 does, against a limit written as the literal 10.

    The two cells differ by one in both numbers, so a comparison written ``<`` instead of
    ``<=`` moves exactly one of them and this fails. The limit is asserted as a literal
    beside them rather than read back out of the finding, which is what stops the test
    agreeing with a limit the run never used.
    """
    findings = pipeline(LENIENT)
    (inside,) = cell_findings(findings, "to_the_limit")
    outside = [f for f in cell_findings(findings, "past_the_limit") if f.kind == "ratchet"]

    assert (inside.before, inside.value) == (8.0, 10.0)
    assert inside.limit == 10.0
    assert inside.blocking is False
    assert [(f.before, f.value, f.limit, f.blocking) for f in outside] == [(10.0, 11.0, 10.0, True)]


@pytest.mark.parametrize("cell", ["worse_under", "to_the_limit"])
def test_below_limit_severity_error_restores_the_freeze(cell: str) -> None:
    """The measurement that says the passing cases above pass *because of this change*.

    One configuration key, the same snapshots, and the refusal comes back: every below-limit
    growth blocks again, which is exactly the behaviour the field report was made against.
    Without this the two tests above would pass just as happily against a ratchet that had
    been deleted.
    """
    (finding,) = cell_findings(pipeline(FROZEN), cell)

    assert finding.severity == "error"
    assert finding.blocking is True


def test_below_limit_severity_is_a_ceiling_and_never_promotes_a_warning() -> None:
    """A rule the operator demoted stays demoted, even under the freeze (req 3.7).

    The setting exists to soften a refusal; manufacturing one out of a rule an operator wrote
    down as a warning would be the same defect pointing the other way.
    """
    (finding,) = cell_findings(pipeline(FROZEN, {RULE: "warning"}), "worse_under")

    assert finding.severity == "warning"
    assert finding.blocking is False


def test_a_below_limit_ratchet_message_does_not_claim_a_rule_the_run_will_not_enforce() -> None:
    """What the two cases print, since only one of them is a refusal (req 7.1)."""
    findings = pipeline(LENIENT)
    (inside,) = cell_findings(findings, "worse_under")
    (outside,) = [f for f in cell_findings(findings, "worse_over") if f.kind == "ratchet"]

    assert inside.message == (
        "routine app.worse_under CyclomaticStrict rose from 5 to 8, still within the maximum 10"
    )
    assert outside.message == (
        "routine app.worse_over CyclomaticStrict rose from 12 to 15; "
        "an affected entity may not get worse than it was"
    )


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
    (classified,) = classify([threshold_finding(15.0, None, 10.0)], LENIENT, {RULE: "warning"})

    assert classified.severity == "warning"
    assert classified.blocking is False


def test_a_severity_map_entry_can_promote_a_warning_to_a_blocking_error() -> None:
    """The map is the last word on severity, in both directions (req 3.7)."""
    (classified,) = classify([warning_finding(15.0, None, 10.0)], LENIENT, {RULE: "error"})

    assert classified.severity == "error"
    assert classified.blocking is True


def test_a_severity_map_entry_for_another_rule_changes_nothing() -> None:
    """Severities are keyed by rule name; an entry for a different rule does not apply."""
    (classified,) = classify(
        [threshold_finding(15.0, None, 10.0)], LENIENT, {"routine.MaxNesting": "warning"}
    )

    assert classified.severity == "error"
    assert classified.blocking is True


def test_a_warning_never_blocks_even_in_strict_mode() -> None:
    """Only ``error`` findings block a commit (req 3.7, 7.9)."""
    (classified,) = classify([warning_finding(15.0, 5.0, 10.0)], STRICT, {})

    assert classified.severity == "warning"
    assert classified.blocking is False
    assert classified.preexisting is False


def test_a_ratchet_finding_is_never_pre_existing() -> None:
    """A ratchet finding records a value that just got worse, so it is never pre-existing.

    Its value (12) is over the limit (10), so this is a ratchet finding that still blocks;
    the flag under test is ``preexisting``, and reading the finding as a threshold one would
    make it pre-existing (12 was already over 10) and non-blocking, so the kind has to decide.
    Task 11.15 is why the value is 12 rather than the 8 this test used to carry: below the
    limit the finding is now a warning, and a warning could pass this assertion for the wrong
    reason.
    """
    ratchet = threshold_finding(12.0, 11.0, 10.0).model_copy(update={"kind": "ratchet"})

    (classified,) = classify([ratchet], LENIENT, {})

    assert classified.preexisting is False
    assert classified.blocking is True


def test_a_ratchet_finding_inside_its_limit_is_still_not_pre_existing() -> None:
    """The below-limit finding stops blocking without ever being called pre-existing (11.15).

    ``preexisting`` is a statement about the code -- the violation was already there -- and
    this change did cause the growth, so saying otherwise would corrupt ``preexisting_count``
    and let strict mode block it. The severity is what carries the decision instead.
    """
    ratchet = threshold_finding(8.0, 5.0, 10.0).model_copy(update={"kind": "ratchet"})

    (classified,) = classify([ratchet], STRICT, {})

    assert classified.preexisting is False
    assert classified.severity == "warning"
    assert classified.blocking is False


def test_a_value_below_a_min_limit_that_did_not_worsen_is_pre_existing() -> None:
    """The ``min`` side of the pre-existing test: the ratio was already too low (req 4.6)."""
    improved = threshold_finding(0.1, 0.05, 0.2)

    (classified,) = classify([improved], LENIENT, {})

    assert classified.preexisting is True
    assert classified.blocking is False


def test_a_value_below_a_min_limit_that_fell_further_blocks() -> None:
    """A pre-existing violation that got worse is not pre-existing any more (req 4.4)."""
    worsened = threshold_finding(0.05, 0.1, 0.2)

    (classified,) = classify([worsened], LENIENT, {})

    assert classified.preexisting is False
    assert classified.blocking is True


def test_a_before_value_within_the_limit_does_not_make_a_finding_pre_existing() -> None:
    """Pre-existing means the limit was already broken, not merely that a before exists."""
    fresh = threshold_finding(15.0, 8.0, 10.0)

    (classified,) = classify([fresh], LENIENT, {})

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

    (lenient,) = classify([structural], LENIENT, {})
    (strict,) = classify([structural], STRICT, {})

    assert (lenient.preexisting, lenient.blocking) == (True, False)
    assert (strict.preexisting, strict.blocking) == (True, True)


def test_classify_returns_new_findings_and_leaves_the_originals_untouched() -> None:
    """Findings are a shared contract; the classification step never edits them in place."""
    original = threshold_finding(12.0, 20.0, 10.0)

    (classified,) = classify([original], LENIENT, {})

    assert original.preexisting is False
    assert original.blocking is True
    assert classified is not original
    assert (classified.preexisting, classified.blocking) == (True, False)


# --- acknowledged parse errors -----------------------------------------------------

UNPARSED = "src/pkg/generic.py"


def parse_finding(path: str = UNPARSED) -> Finding:
    """The finding task 11.11 raises for a selected file the analysis could not read."""
    return Finding(
        kind="parse",
        rule=PARSE_ERROR_RULE,
        scope="file",
        path=path,
        message=f"{path} was not analysed: Understand could not read it",
    )


def acknowledgement(
    *paths: str, reason: str = "PEP 695; Understand 6.5 stops there."
) -> ParseSettings:
    return ParseSettings(acknowledged=[ParseAcknowledgement(paths=list(paths), reason=reason)])


def test_an_unacknowledged_parse_error_blocks() -> None:
    """The behaviour task 11.11 established, and the baseline the acknowledgement changes."""
    (finding,) = classify([parse_finding()], LENIENT, severities={})
    assert finding.blocking is True and finding.severity == "error"


def test_an_acknowledged_parse_error_stops_blocking_and_keeps_everything_else() -> None:
    settings = acknowledgement(UNPARSED)
    (finding,) = classify([parse_finding()], LENIENT, severities={}, parse=settings)
    assert finding.blocking is False
    assert finding.severity == "error", "it is still an error; it just does not block"
    assert finding.path == UNPARSED, "and it still names the file"


def test_an_acknowledged_finding_says_the_file_was_not_fully_measured() -> None:
    """Property three: an acknowledged file must never read as a checked-and-clean one."""
    settings = acknowledgement(UNPARSED, reason="Understand 6.5 stops at line 108.")
    (finding,) = classify([parse_finding()], LENIENT, severities={}, parse=settings)
    assert "Understand 6.5 stops at line 108." in finding.message
    assert PARTIAL_WARNING in finding.message
    assert finding.details[ACKNOWLEDGED_DETAIL] == "Understand 6.5 stops at line 108."


def test_strict_mode_does_not_reinstate_the_block() -> None:
    """An acknowledgement is a statement about the analyser, not about a violation's age."""
    settings = acknowledgement(UNPARSED)
    (finding,) = classify([parse_finding()], STRICT, severities={}, parse=settings)
    assert finding.blocking is False


def test_a_file_the_acknowledgement_does_not_name_still_blocks() -> None:
    settings = acknowledgement("src/other.py")
    (finding,) = classify([parse_finding()], LENIENT, severities={}, parse=settings)
    assert finding.blocking is True
    assert ACKNOWLEDGED_DETAIL not in finding.details


def test_a_glob_acknowledgement_covers_the_files_under_it() -> None:
    settings = acknowledgement("src/pkg/**")
    (finding,) = classify([parse_finding()], LENIENT, severities={}, parse=settings)
    assert finding.blocking is False


def test_an_acknowledgement_cannot_demote_a_finding_about_the_code() -> None:
    """The property that keeps this from being an ignore list: only ``parse`` is eligible."""
    threshold = Finding(
        kind="threshold",
        rule=RULE,
        metric=METRIC,
        scope="routine",
        path=UNPARSED,
        value=12.0,
        limit=10.0,
        message="over the limit",
    )
    settings = acknowledgement(UNPARSED)
    (finding,) = classify([threshold], LENIENT, severities={}, parse=settings)
    assert finding.blocking is True
    assert finding.message == "over the limit"


def test_no_parse_settings_leaves_every_finding_exactly_as_it_was() -> None:
    findings = [parse_finding(), parse_finding("src/other.py")]
    assert classify(findings, LENIENT, severities={}) == classify(
        findings, LENIENT, severities={}, parse=ParseSettings()
    )
