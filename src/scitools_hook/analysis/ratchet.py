"""Before/after comparison of affected entities, and the before value classify needs (req 4.4-4.6).

Two steps live here, both of them joining the two sides of the change by
:class:`~scitools_hook.models.snapshot.EntityKey`, which is the only identity that survives
across two Understand databases:

* :func:`evaluate_ratchet` reports an affected entity whose metric got **worse** than it was
  before the change, even when the new value is still within the absolute limit (req 4.4) --
  that is the whole point of the ratchet, and it is what stops a codebase from degrading one
  commit at a time. Worse means higher for a ``max`` limit and lower for a ``min`` one; an
  entity the change added has no pre-change value and is therefore judged by absolute
  thresholds alone (req 4.5).
* :func:`attach_before` fills ``Finding.before`` on the threshold findings of step 4.1, whose
  evaluator only ever sees the after snapshot. ``analysis.classify`` reads that value to
  decide whether a violation was already there before the change (req 4.6); the check
  pipeline therefore calls ``evaluate_thresholds`` -> ``attach_before`` -> ``evaluate_ratchet``
  -> ``classify``, in that order.

Neither step decides whether a finding blocks: they set ``blocking`` from the severity, as
``analysis.thresholds`` does, and ``analysis.classify`` has the last word (req 4.6, 4.7).
Population thresholds (``AVG:CountLineCode``) and scopes without entities are not ratcheted:
they have no per-entity before value to compare. A metric missing on either side is skipped;
reporting it as unavailable is ``analysis.thresholds``' job (req 5.5), which sees the same
entities.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Iterator, Sequence
from typing import Literal

from scitools_hook.config.metric_names import ELEMENT_SCOPES, format_metric_name
from scitools_hook.config.models import Limit
from scitools_hook.models.findings import EffectiveThreshold, Finding, build_rule_name
from scitools_hook.models.snapshot import EntityKey, EntityRecord, ProjectSnapshot

Bound = Literal["max", "min"]
"""Which bound of a limit a worsening value moved towards."""


def evaluate_ratchet(
    after: ProjectSnapshot,
    before: ProjectSnapshot,
    keys: Collection[EntityKey],
    specs: Sequence[EffectiveThreshold],
) -> list[Finding]:
    """Report every entity of ``keys`` whose value got worse between the two snapshots.

    ``keys`` is the affected set: entities the staged change touched, plus those whose
    dependencies moved (req 4.2). A key missing from either side yields nothing -- it is
    either new (req 4.5) or deleted (req 4.10) -- and so does a metric with the ratchet
    switched off in configuration (req 4.4).
    """
    findings: list[Finding] = []
    for threshold in specs:
        if not _is_ratcheted(threshold):
            continue
        findings.extend(_compare_all(threshold, after, before, _in_scope(keys, threshold)))
    return findings


def attach_before(findings: Iterable[Finding], before: ProjectSnapshot) -> list[Finding]:
    """Return ``findings`` with ``before`` filled in wherever the entity exists on both sides.

    Only threshold findings are touched, and only those that belong to an entity: a
    population finding (``entity=None``) has no before value, and the other evaluators fill
    their own. Nothing is modified in place: a finding that gains a before value comes back
    as a new object, and every other one is passed through unchanged.
    """
    return [_with_before(finding, before) for finding in findings]


def _is_ratcheted(threshold: EffectiveThreshold) -> bool:
    """Whether this threshold is compared across the change at all (req 4.4, 4.8)."""
    return (
        threshold.spec.ratchet
        and not threshold.metric.is_population
        and threshold.spec.scope in ELEMENT_SCOPES
    )


def _in_scope(keys: Iterable[EntityKey], threshold: EffectiveThreshold) -> list[EntityKey]:
    """The requested keys of this threshold's scope, in a stable order."""
    scope = threshold.spec.scope
    return sorted((key for key in keys if key.scope == scope), key=lambda key: key.token)


def _compare_all(
    threshold: EffectiveThreshold,
    after: ProjectSnapshot,
    before: ProjectSnapshot,
    keys: Sequence[EntityKey],
) -> Iterator[Finding]:
    """Compare one threshold's metric for every key of its scope."""
    for key in keys:
        finding = _compare(threshold, after.entities.get(key), before.entities.get(key))
        if finding is not None:
            yield finding


def _compare(
    threshold: EffectiveThreshold, after: EntityRecord | None, before: EntityRecord | None
) -> Finding | None:
    """One entity's before/after comparison, or ``None`` when there is nothing to compare."""
    if before is None:
        return None  # req 4.5: an entity the change added has no pre-change value
    if after is None:
        return None  # the change deleted the entity (req 4.10)
    metric = threshold.metric.metric
    was, now = before.metrics.get(metric), after.metrics.get(metric)
    if was is None or now is None:
        return None
    bound = _worse_bound(threshold.limit, was, now)
    if bound is None:
        return None
    return _ratchet_finding(threshold, after, was, now, bound)


def _worse_bound(limit: Limit, was: float, now: float) -> Bound | None:
    """The bound the value moved towards, or ``None`` when it did not get worse.

    A limit with both bounds is a maximum *and* a minimum, so either movement is worse.
    """
    if limit.max is not None and now > was:
        return "max"
    if limit.min is not None and now < was:
        return "min"
    return None


def _ratchet_finding(
    threshold: EffectiveThreshold, record: EntityRecord, was: float, now: float, bound: Bound
) -> Finding:
    """One worsened entity (req 7.1); ``hint`` is attached by the pipeline."""
    metric = format_metric_name(threshold.metric)
    subject = f"{record.key.scope} {record.key.longname}"
    return Finding(
        kind="ratchet",
        rule=build_rule_name(threshold.spec.scope, metric),
        metric=metric,
        scope=threshold.spec.scope,
        entity=record.ref,
        path=record.key.path,
        line=record.ref.line,
        value=now,
        before=was,
        limit=threshold.limit.max if bound == "max" else threshold.limit.min,
        limit_source=threshold.source,
        severity=threshold.spec.severity,
        blocking=threshold.spec.severity == "error",
        message=_message(subject, metric, was, now, bound),
    )


def _message(subject: str, metric: str, was: float, now: float, bound: Bound) -> str:
    """One line stating what got worse and by how much (req 7.1)."""
    verb = "rose" if bound == "max" else "fell"
    return (
        f"{subject} {metric} {verb} from {_number(was)} to {_number(now)}; "
        f"an affected entity may not get worse than it was"
    )


def _with_before(finding: Finding, before: ProjectSnapshot) -> Finding:
    """``finding`` with its pre-change value, or unchanged when there is none to give."""
    if finding.kind != "threshold" or finding.entity is None or finding.metric is None:
        return finding
    record = before.entities.get(finding.entity.key)
    if record is None:
        return finding
    value = record.metrics.get(finding.metric)
    if value is None:
        return finding
    return finding.model_copy(update={"before": value})


def _number(value: float) -> str:
    """Render a metric value without a trailing ``.0`` on the whole numbers most metrics are."""
    return f"{value:g}"
