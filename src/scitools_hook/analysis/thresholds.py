"""Absolute threshold evaluation over entities and populations (req 3.4, 3.6, 5.1-5.6).

:func:`evaluate_thresholds` is the entry point the design names. It returns a
:class:`ThresholdOutcome` rather than a bare list of findings, because one run has to report
four more things that no finding can carry and ``RunResult`` needs: the per-scope ignored
counts (3.6), the metrics that were unavailable per language (5.5), the highest value per
metric among the evaluated entities (5.6) and the populations that could not be reduced to a
value (3.4).

``scopes`` is the one thing that makes a threshold depend on *where* an entity lives.
:func:`resolve_for_path` states the merge and is the only place it is decided; the evaluation
below simply asks it once per path. With no scopes configured -- the shipped case -- nothing
changes: the same specs are checked against every entity, in the same order.

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
from typing import Final, Literal

from scitools_hook.analysis.population import IgnoreFilter, filter_keys, reduce
from scitools_hook.config.metric_names import ELEMENT_SCOPES, Scope, format_metric_name
from scitools_hook.config.models import (
    IgnoreRules,
    Limit,
    PathScope,
    ScopeOverride,
    Severity,
    ThresholdSpec,
)
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


# --- path scopes ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScopeResolution:
    """The thresholds one path is judged by, and what the path scopes did to get there.

    Everything a scope changed is reported, not just the result: ``applied`` names the scopes
    in the order they were overlaid, ``disabled`` the rules they switched off, and ``ignored``
    the overrides that selected nothing. ``config --why`` prints all four, which is what makes
    a scope auditable rather than a second place for limits to disappear.
    """

    path: str
    applied: tuple[str, ...] = ()
    thresholds: tuple[EffectiveThreshold, ...] = ()
    disabled: tuple[str, ...] = ()
    ignored: tuple[str, ...] = ()


def resolve_for_path(
    path: str, specs: Sequence[EffectiveThreshold], scopes: Mapping[str, PathScope]
) -> ScopeResolution:
    """The thresholds that apply to ``path`` once every matching scope has been overlaid.

    **The merge, stated once.** Precedence is ``built-in defaults < user file < repository
    file < environment < command line < path scope``. Everything left of the last step is
    already done: ``specs`` arrives as the merged global configuration, with the adaptive
    baseline applied. A scope is the final, most specific layer, and it works **per rule**:

    * a rule the scope names gets the scope's limit, severity and ratchet, falling back to
      the global spec's for whatever the scope leaves out;
    * ``Metric = false`` removes the rule for this path -- it is not evaluated, and no finding
      can come from it;
    * a rule the scope names and no global threshold defines is *added*, provided the scope
      gives it a limit;
    * every rule no scope mentions keeps its global value, untouched.

    A scope's limit replaces the *effective* limit, the adaptive baseline included. That is a
    real consequence and it is the intended one: a scope is an explicit statement about a
    region, and a recorded observation should not narrow it back.

    **When two scopes match one file, both apply, in declaration order, and the later one
    wins per rule.** Not "the most specific", which needs a specificity order nobody agrees
    on, and not an error, which would make two independent scopes -- "tests" and "the vendored
    package" -- impossible to write over one tree. The order is the order the scopes appear in
    the merged configuration, so a repository file's scope overlays a user file's, and
    :attr:`ScopeResolution.applied` reports it so an operator never has to infer it.
    """
    applied = tuple(name for name, scope in scopes.items() if scope.matched_by(path) is not None)
    if not applied:
        return ScopeResolution(path=path, thresholds=tuple(specs))
    return _overlay(path, applied, specs, scopes)


def _overlay(
    path: str,
    applied: Sequence[str],
    specs: Sequence[EffectiveThreshold],
    scopes: Mapping[str, PathScope],
) -> ScopeResolution:
    """Apply every matching scope's overrides to ``specs``, keeping the global order."""
    resolved: dict[str, EffectiveThreshold | None] = {item.rule: item for item in specs}
    ignored: list[str] = []
    for name in applied:
        for scope, metric, override in _override_entries(scopes[name]):
            rule = build_rule_name(scope, metric)
            replacement = _apply_override(resolved.get(rule), scope, metric, override)
            if isinstance(replacement, _Unusable):
                ignored.append(f"{name}: {rule} has no limit here and none globally")
                continue
            resolved[rule] = replacement
    kept = tuple(item for item in resolved.values() if item is not None)
    disabled = tuple(sorted(rule for rule, item in resolved.items() if item is None))
    return ScopeResolution(
        path=path,
        applied=tuple(applied),
        thresholds=kept,
        disabled=disabled,
        ignored=tuple(ignored),
    )


def _override_entries(scope: PathScope) -> list[tuple[Scope, str, ScopeOverride]]:
    """One scope's overrides as ``(threshold scope, metric, override)``, in declaration order."""
    return [
        (threshold_scope, metric, override)
        for threshold_scope, table in scope.thresholds.items()
        for metric, override in table.items()
    ]


class _Unusable:
    """Sentinel: an override that names no limit and has no global threshold to modify."""


_UNUSABLE: Final = _Unusable()


def _apply_override(
    base: EffectiveThreshold | None, scope: Scope, metric: str, override: ScopeOverride
) -> EffectiveThreshold | None | _Unusable:
    """One rule after one scope: the replacement, ``None`` for disabled, or :data:`_UNUSABLE`."""
    if override.disabled:
        return None
    limit = override.limit if override.limit is not None else _base_limit(base)
    if limit is None:
        return _UNUSABLE
    spec = ThresholdSpec.model_validate(
        {
            "scope": scope,
            "metric": metric,
            "limit": limit,
            "severity": _first(override.severity, base.spec.severity if base else None, "error"),
            **_ratchet_field(override, base),
        }
    )
    source = "config" if override.limit is not None or base is None else base.source
    return EffectiveThreshold(spec=spec, metric=spec.ref, limit=limit, source=source)


def _base_limit(base: EffectiveThreshold | None) -> Limit | None:
    """The limit an override inherits when it names none of its own."""
    return base.limit if base is not None else None


def _ratchet_field(override: ScopeOverride, base: EffectiveThreshold | None) -> dict[str, object]:
    """``{"ratchet": ...}`` only when something says so.

    Left out entirely otherwise, because ``ThresholdSpec`` resolves the default from the
    metric -- a decomposition count ships with the ratchet off -- and writing the field
    explicitly, in either direction, is what turns that default off.
    """
    if override.ratchet is not None:
        return {"ratchet": override.ratchet}
    if base is not None:
        return {"ratchet": base.spec.ratchet}
    return {}


def _first(*values: Severity | None) -> Severity:
    """The first value that is not ``None``; the last argument is the fallback."""
    for value in values:
        if value is not None:
            return value
    raise AssertionError("_first needs a non-None fallback as its last argument")


def evaluate_thresholds(
    snapshot: ProjectSnapshot,
    keys: Iterable[EntityKey],
    specs: Sequence[EffectiveThreshold],
    catalogue_unavailable: Mapping[str, Sequence[str]] | None = None,
    ignore: IgnoreRules | None = None,
    scopes: Mapping[str, PathScope] | None = None,
) -> ThresholdOutcome:
    """Check ``specs`` against the entities named by ``keys`` and against the populations.

    ``keys`` is the affected set in staged mode and every entity of the snapshot in
    whole-project mode (req 4.8); entities matching ``ignore`` are dropped and counted
    (req 3.6). ``catalogue_unavailable`` maps a language to the metrics Understand does not
    provide for it; together with the snapshot's own record it seeds the unavailable report
    (req 5.5), which is otherwise discovered entity by entity. ``scopes`` is
    ``settings.scope``: the path scopes whose thresholds replace the global ones for the
    files they name (:func:`resolve_for_path`). It never removes a file from the analysis --
    only a ``[project] exclude`` line does that -- so a scope cannot hide an entity, only
    change the numbers it is judged by.
    """
    selected = filter_keys(keys, IgnoreFilter.from_rules(ignore) if ignore is not None else None)
    records = _records_by_scope(snapshot, selected.keys)
    tally = _Tally()
    _evaluate_all(snapshot, records, specs, scopes or {}, tally)
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


def _evaluate_all(
    snapshot: ProjectSnapshot,
    records: Mapping[Scope, Sequence[EntityRecord]],
    specs: Sequence[EffectiveThreshold],
    scopes: Mapping[str, PathScope],
    tally: _Tally,
) -> None:
    """Run every spec, in one walk without path scopes and in two walks with them.

    The scope-free branch is written out rather than folded into the general one, and the
    reason is measured: it walks ``specs`` in configuration order, dispatching each to the
    population or the element evaluator as it goes, so the findings come out in **exactly**
    the order they always have. Two pipeline tests pin that order, and running the general
    path unconditionally reordered them -- every population finding ahead of every element
    one -- for configurations that use no scope at all. A feature nobody switched on must not
    move a byte.
    """
    if not scopes:
        for threshold in specs:
            if _is_population(threshold):
                _evaluate_population(threshold, snapshot, tally)
            else:
                _evaluate_elements(threshold, records.get(threshold.spec.scope, ()), tally)
        return
    for threshold in specs:
        if _is_population(threshold):
            _evaluate_population(threshold, snapshot, tally)
    _evaluate_scoped_elements(records, specs, scopes, tally)


def _evaluate_scoped_elements(
    records: Mapping[Scope, Sequence[EntityRecord]],
    specs: Sequence[EffectiveThreshold],
    scopes: Mapping[str, PathScope],
    tally: _Tally,
) -> None:
    """Check the element specs one file at a time, since a scope makes them file-dependent."""
    element = [threshold for threshold in specs if not _is_population(threshold)]
    for path, grouped in sorted(_records_by_path(records).items()):
        for threshold in resolve_for_path(path, element, scopes).thresholds:
            _evaluate_elements(threshold, grouped.get(threshold.spec.scope, ()), tally)


def _records_by_path(
    records: Mapping[Scope, Sequence[EntityRecord]],
) -> dict[str, dict[Scope, list[EntityRecord]]]:
    """Regroup the entities by the file holding them, keeping the scope grouping inside."""
    grouped: dict[str, dict[Scope, list[EntityRecord]]] = {}
    for scope, entities in records.items():
        for record in entities:
            grouped.setdefault(record.key.path, {}).setdefault(scope, []).append(record)
    return grouped


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
    # Flattened into one generator rather than three nested loops: the nesting measured
    # MaxNesting 4 against this project's own maximum of 3 (task 4.1's note, repaid in 10.4).
    reported = (
        (language, metric)
        for source in sources
        for language, metrics in source.items()
        for metric in metrics
    )
    for language, metric in reported:
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
