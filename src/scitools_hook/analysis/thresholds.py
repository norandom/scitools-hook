"""Absolute threshold evaluation over entities and populations (req 3.4, 3.6, 5.1-5.6).

:func:`evaluate_thresholds` is the entry point the design names. It returns a
:class:`ThresholdOutcome` rather than a bare list of findings, because one run has to report
four more things that no finding can carry and ``RunResult`` needs: the per-scope ignored
counts (3.6), the metrics that were unavailable per language (5.5), the highest value per
metric among the evaluated entities (5.6) and the populations that could not be reduced to a
value (3.4).

An element-scope spec without a stats prefix is checked against every requested entity of
its scope. Every other spec — a stats-prefixed one, or one on a scope that has no entities
of its own (``project``, ``arch``) — is checked against the population vector of its scope
and yields a project-level finding with ``entity=None`` and ``path=""``.

Findings leave ``before = None`` and ``hint = ""``: ``analysis.ratchet`` fills the before
value of an entity that exists on both sides and the check pipeline attaches the hint, so
human, JSON and SARIF output all carry the same text. ``blocking`` follows the severity
here; ``analysis.classify`` downgrades it when the violation turns out to be pre-existing
and strict mode is off (req 4.6, 4.7).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from scitools_hook.analysis.population import IgnoreFilter, filter_keys, reduce
from scitools_hook.config.metric_names import ELEMENT_SCOPES, Scope, format_metric_name
from scitools_hook.config.models import IgnoreRules, Limit
from scitools_hook.models.findings import EffectiveThreshold, Finding, HighestValue, build_rule_name
from scitools_hook.models.snapshot import EntityKey, EntityRecord, ProjectSnapshot

Bound = Literal["max", "min"]
"""Which side of a limit an entity broke."""

Breach = tuple[Bound, float]
"""The bound that was broken and the limit value it holds."""


@dataclass(frozen=True, slots=True)
class ThresholdOutcome:
    """Everything one threshold pass produced, in the shape ``RunResult`` assembles from."""

    findings: list[Finding] = field(default_factory=list)
    highest: list[HighestValue] = field(default_factory=list)
    ignored_counts: dict[Scope, int] = field(default_factory=dict)
    unavailable: dict[str, list[str]] = field(default_factory=dict)
    reducer_failures: dict[str, str] = field(default_factory=dict)
    """Rule name -> why its population could not be reduced; one entry per rule."""


@dataclass(slots=True)
class _Tally:
    """Working state of one pass; dictionaries give the report-once behaviour for free."""

    findings: list[Finding] = field(default_factory=list)
    highest: dict[tuple[Scope, str], HighestValue] = field(default_factory=dict)
    unavailable: dict[str, set[str]] = field(default_factory=dict)
    reducer_failures: dict[str, str] = field(default_factory=dict)

    def record_unavailable(self, language: str, metric: str) -> None:
        """Note that ``metric`` is not available for ``language`` (req 5.5)."""
        self.unavailable.setdefault(language, set()).add(metric)

    def track_highest(
        self, threshold: EffectiveThreshold, record: EntityRecord, value: float
    ) -> None:
        """Keep the largest value seen for this scope and metric, and who has it (req 5.6)."""
        slot = (threshold.spec.scope, threshold.metric.metric)
        current = self.highest.get(slot)
        if current is None or value > current.value:
            self.highest[slot] = HighestValue(
                scope=slot[0], metric=slot[1], value=value, entity=record.ref
            )


def evaluate_thresholds(
    snapshot: ProjectSnapshot,
    keys: Iterable[EntityKey],
    specs: Sequence[EffectiveThreshold],
    catalogue_unavailable: Mapping[str, Sequence[str]] | None = None,
    ignore: IgnoreRules | None = None,
) -> ThresholdOutcome:
    """Check ``specs`` against the entities named by ``keys`` and against the populations.

    ``keys`` is the affected set in staged mode and every entity of the snapshot in
    whole-project mode (req 4.8); entities matching ``ignore`` are dropped and counted
    (req 3.6). ``catalogue_unavailable`` maps a language to the metrics Understand does not
    provide for it; together with the snapshot's own record it seeds the unavailable report
    (req 5.5), which is otherwise discovered entity by entity.
    """
    selected = filter_keys(keys, IgnoreFilter.from_rules(ignore) if ignore is not None else None)
    records = _records_by_scope(snapshot, selected.keys)
    tally = _Tally()
    for threshold in specs:
        if _is_population(threshold):
            _evaluate_population(threshold, snapshot, tally)
        else:
            _evaluate_elements(threshold, records.get(threshold.spec.scope, ()), tally)
    _seed_unavailable(tally, specs, catalogue_unavailable, snapshot)
    return ThresholdOutcome(
        findings=tally.findings,
        highest=sorted(tally.highest.values(), key=lambda h: (-h.value, h.scope, h.metric)),
        ignored_counts=selected.ignored_counts,
        unavailable={
            language: sorted(metrics) for language, metrics in sorted(tally.unavailable.items())
        },
        reducer_failures=tally.reducer_failures,
    )


def _records_by_scope(
    snapshot: ProjectSnapshot, keys: set[EntityKey]
) -> dict[Scope, list[EntityRecord]]:
    """Group the requested entities the snapshot knows by scope, in a stable order.

    A key the snapshot does not hold is skipped: an affected entity may have been deleted by
    the change (req 4.10).
    """
    grouped: dict[Scope, list[EntityRecord]] = {}
    for key in sorted(keys, key=lambda entity: entity.token):
        record = snapshot.entities.get(key)
        if record is not None:
            grouped.setdefault(key.scope, []).append(record)
    return grouped


def _is_population(threshold: EffectiveThreshold) -> bool:
    """Whether this threshold is evaluated over a population instead of over entities."""
    return threshold.metric.is_population or threshold.spec.scope not in ELEMENT_SCOPES


def _evaluate_elements(
    threshold: EffectiveThreshold, records: Sequence[EntityRecord], tally: _Tally
) -> None:
    """Check one element-scope threshold against every requested entity of its scope."""
    metric = threshold.metric.metric
    for record in records:
        value = record.metrics.get(metric)
        if value is None:
            tally.record_unavailable(record.language, metric)
            continue
        tally.track_highest(threshold, record, value)
        breach = _breach(threshold.limit, value)
        if breach is not None:
            tally.findings.append(_element_finding(threshold, record, value, breach))


def _evaluate_population(
    threshold: EffectiveThreshold, snapshot: ProjectSnapshot, tally: _Tally
) -> None:
    """Check one population threshold; an unusable vector is recorded once (req 3.4, 5.4)."""
    metric = threshold.metric.metric
    values = snapshot.populations.get(threshold.spec.scope, {}).get(metric, [])
    prefix = threshold.metric.prefix
    value = reduce(prefix, values) if prefix is not None else _project_value(values)
    if value is None:
        tally.reducer_failures.setdefault(_rule_of(threshold), _no_value_reason(threshold, values))
        return
    breach = _breach(threshold.limit, value)
    if breach is not None:
        tally.findings.append(_population_finding(threshold, value, breach))


def _project_value(values: Sequence[float]) -> float | None:
    """The value of a plain metric on a scope without entities.

    The extractor captures such a metric as a one-element vector; the maximum keeps the
    check deterministic should a scope ever report several.
    """
    return max(values) if values else None


def _no_value_reason(threshold: EffectiveThreshold, values: Sequence[float]) -> str:
    """Why a population threshold produced no value, for the run's diagnostics."""
    subject = f"{threshold.spec.scope} population of {threshold.metric.metric}"
    if not values:
        return f"the {subject} is empty"
    return f"{threshold.metric.prefix} cannot be computed over the {subject}"


def _breach(limit: Limit, value: float) -> Breach | None:
    """The bound ``value`` breaks, or ``None`` when it stays within the limit."""
    if limit.max is not None and value > limit.max:
        return "max", limit.max
    if limit.min is not None and value < limit.min:
        return "min", limit.min
    return None


def _element_finding(
    threshold: EffectiveThreshold, record: EntityRecord, value: float, breach: Breach
) -> Finding:
    """One entity's threshold violation (req 7.1); ``before`` and ``hint`` are filled later."""
    subject = f"{record.key.scope} {record.key.longname}"
    return Finding(
        kind="threshold",
        rule=_rule_of(threshold),
        metric=format_metric_name(threshold.metric),
        scope=threshold.spec.scope,
        entity=record.ref,
        path=record.key.path,
        line=record.ref.line,
        value=value,
        limit=breach[1],
        limit_source=threshold.source,
        severity=threshold.spec.severity,
        blocking=threshold.spec.severity == "error",
        message=_message(subject, format_metric_name(threshold.metric), value, breach),
    )


def _population_finding(threshold: EffectiveThreshold, value: float, breach: Breach) -> Finding:
    """A project-level violation: no entity and no path, as the design requires."""
    metric = format_metric_name(threshold.metric)
    return Finding(
        kind="threshold",
        rule=_rule_of(threshold),
        metric=metric,
        scope=threshold.spec.scope,
        entity=None,
        path="",
        value=value,
        limit=breach[1],
        limit_source=threshold.source,
        severity=threshold.spec.severity,
        blocking=threshold.spec.severity == "error",
        message=_message(threshold.spec.scope, metric, value, breach),
    )


def _seed_unavailable(
    tally: _Tally,
    specs: Sequence[EffectiveThreshold],
    catalogue: Mapping[str, Sequence[str]] | None,
    snapshot: ProjectSnapshot,
) -> None:
    """Add the metrics a language is known to lack, limited to the ones a threshold names.

    The catalogue and the snapshot both report per language what Understand does not provide
    (req 5.5); reporting only configured metrics keeps the run's list about this run.
    """
    named = {threshold.metric.metric for threshold in specs}
    sources: tuple[Mapping[str, Sequence[str]], ...] = (catalogue or {}, snapshot.unavailable)
    for source in sources:
        for language, metrics in source.items():
            for metric in metrics:
                if metric in named:
                    tally.record_unavailable(language, metric)


def _rule_of(threshold: EffectiveThreshold) -> str:
    """Rule name of a threshold, canonical prefix included; equals ``EffectiveThreshold.rule``."""
    return build_rule_name(threshold.spec.scope, format_metric_name(threshold.metric))


def _message(subject: str, metric: str, value: float, breach: Breach) -> str:
    """One line stating what is wrong, by how much (req 7.1)."""
    bound, limit = breach
    relation = "exceeds the maximum" if bound == "max" else "is below the minimum"
    return f"{subject} {metric} is {_number(value)}, which {relation} {_number(limit)}"


def _number(value: float) -> str:
    """Render a metric value without a trailing ``.0`` on the whole numbers most metrics are."""
    return f"{value:g}"
