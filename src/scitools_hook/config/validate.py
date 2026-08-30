"""Configuration checks beyond shape: metric grammar, scopes, regexes, structure (req 3.6, 3.8).

``models`` validates the shape of every value; this module validates their meaning. The checks
that need nothing but the settings themselves always run (they are also re-run here for a
``Settings`` built in code rather than parsed from TOML); the metric names are additionally
checked against an Understand metric catalogue when one is supplied. The catalogue arrives
through the ``MetricAvailability`` protocol declared here, so ``config`` never imports the
``understand`` adapter. Every failure is a ``ConfigError`` naming the dotted key.

Availability is the one check whose answer depends on whose threshold it is. Requirement 3.8
rejects an *unknown metric name*, while requirement 3.1 promises the Gate runs on its built-in
defaults — and those defaults ship ``class.PercentLackOfCohesion``, which Understand computes
for C++ and not for Python. A metric no configured language has is therefore a ``ConfigError``
only when the Gate does not ship it: an unknown name, which is what 3.8 is about. One the Gate
ships is a known metric that this repository's languages do not compute, which is requirement
5.5 — skipped and reported, not fatal. Who *wrote* it cannot be the test: ``scitools-hook init``
(req 3.9) copies every built-in default into the repository file, so a rule keyed on provenance
would stop the Gate on a Python repository as soon as the operator ran ``init`` and uncommented
``languages``. :class:`AvailabilityReport` carries the drops out, per language, so the run
reports that they were never evaluated instead of going quietly green.

Dropping has one dangerous edge, which :func:`_check_languages` closes: a misspelt *language*
makes every question come back empty, so every shipped default would drop and the Gate would
run green on no rules at all. A configured language the catalogue computes nothing for is
therefore a configuration error naming the language.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final, Protocol, runtime_checkable

from scitools_hook.config.defaults import is_default_threshold
from scitools_hook.config.metric_names import (
    ELEMENT_SCOPES,
    SCOPES,
    SYNTHETIC_METRICS,
    MetricRef,
    Scope,
    is_valid_scope,
    parse_metric_name,
)
from scitools_hook.config.models import (
    FAN_KEYS,
    CouplingRule,
    IgnoreRules,
    LayerRule,
    Settings,
    StructureRules,
    ThresholdSpec,
)
from scitools_hook.config.models_validation import compile_patterns
from scitools_hook.errors import ConfigError

LANGUAGE_SCOPES: Final[tuple[Scope, ...]] = (*ELEMENT_SCOPES, "project")
"""Scopes whose metric list is language-specific; ``arch`` answers the same for any name."""

_LANGUAGE_HINT: Final = (
    "check the spelling; the 'Languages' block of `und list settings <database>` "
    "names the languages this Understand build knows"
)
"""There is no ``und -languages``: that invocation exits 1 with 'No valid command' (measured)."""


@runtime_checkable
class MetricAvailability(Protocol):
    """Metric catalogue seen from ``config``; ``understand.catalogue`` satisfies it (req 5.5)."""

    def available(self, language: str, scope: Scope) -> set[str]:
        """Metric identifiers Understand computes for ``language`` at ``scope``."""


def _frozen_metrics(
    metrics: Mapping[str, tuple[str, ...]] | None = None,
) -> Mapping[str, tuple[str, ...]]:
    """A read-only view of ``metrics``: the report hands out no container a caller can edit."""
    return MappingProxyType(dict(metrics or {}))


@dataclass(frozen=True, slots=True)
class AvailabilityReport:
    """What the metric catalogue left of the configured thresholds (req 3.1, 5.5).

    ``thresholds`` is what the run evaluates. ``dropped`` are the built-in defaults whose
    metric no configured language has; ``unavailable`` names those metrics per language, so a
    dropped threshold is reported rather than forgotten. It is read-only and holds tuples,
    because a frozen report that handed out a live ``dict`` would be frozen in name only;
    ``RunResult.unavailable_metrics`` takes ``{language: list(metrics) for language, metrics
    in report.unavailable.items()}`` and ``evaluate_thresholds``'s ``catalogue_unavailable``
    takes it as it is. A metric the Gate does not ship never reaches this report — it is a
    ``ConfigError``.

    Note for the caller: ``analysis.thresholds`` reports only the unavailable metrics that a
    threshold in the list it was given names, so a dropped one is filtered out there. Pass
    ``unavailable`` into ``RunResult.unavailable_metrics`` as well as into
    ``catalogue_unavailable``, or the drop disappears from the report.
    """

    thresholds: tuple[ThresholdSpec, ...] = ()
    dropped: tuple[ThresholdSpec, ...] = ()
    unavailable: Mapping[str, tuple[str, ...]] = field(default_factory=_frozen_metrics)


def validate_settings(
    settings: Settings, availability: MetricAvailability | None
) -> AvailabilityReport:
    """Check ``settings`` beyond its shape; raise ``ConfigError`` naming the dotted key.

    Pure: no file system, no Understand, no mutation. ``file`` is left unset because a
    ``Settings`` no longer knows where a value came from — ``config.loader.attach_source``
    fills it in from the provenance map. When ``availability`` is given, every metric the
    Gate does not itself ship must exist for at least one configured language at its scope
    (req 3.8); the returned report says which thresholds survived that check and which
    shipped default this repository's languages cannot compute (req 3.1, 5.5).
    """
    for spec in settings.thresholds:
        _check_threshold(spec)
    _check_ignores(settings.ignore)
    _check_structure(settings.structure)
    if availability is None:
        return AvailabilityReport(thresholds=tuple(settings.thresholds))
    return _check_availability(settings, availability)


def _threshold_key(spec: ThresholdSpec) -> str:
    return f"thresholds.{spec.scope}.{spec.metric}"


def _check_threshold(spec: ThresholdSpec) -> None:
    """Scope, metric-name grammar and synthetic-metric scope binding of one threshold."""
    key = _threshold_key(spec)
    if not is_valid_scope(spec.scope):
        raise ConfigError(
            f"{key}: unknown scope {spec.scope!r}",
            key=key,
            hint=f"expected one of {', '.join(SCOPES)}",
        )
    ref = _parsed_metric(spec.metric, key)
    if ref.is_population and spec.scope == "arch":
        raise ConfigError(
            f"{key}: a stats prefix has no population at the 'arch' scope",
            key=key,
            hint="use the routine, class, file or project scope",
        )
    _check_synthetic(spec.scope, ref, key)


def _parsed_metric(metric: str, key: str) -> MetricRef:
    try:
        return parse_metric_name(metric)
    except ConfigError as err:
        raise ConfigError(f"{key}: {err.message}", key=key, hint=err.hint) from err


def _check_synthetic(scope: Scope, ref: MetricRef, key: str) -> None:
    """A synthetic metric belongs to its own scope, or to a population at project scope."""
    synthetic = SYNTHETIC_METRICS.get(ref.metric)
    if synthetic is None or scope == synthetic.scope:
        return
    if scope == "project" and ref.is_population:
        return
    raise ConfigError(
        f"{key}: the synthetic metric {ref.metric!r} exists only at the {synthetic.scope} scope",
        key=key,
        hint=f"move it to [thresholds.{synthetic.scope}]",
    )


def _check_ignores(ignore: IgnoreRules) -> None:
    """Every ignore pattern compiles as a regular expression (req 3.6)."""
    lists = (("files", ignore.files), ("classes", ignore.classes), ("routines", ignore.routines))
    for name, patterns in lists:
        key = f"ignore.{name}"
        try:
            compile_patterns(patterns)
        except ValueError as err:
            raise ConfigError(
                f"{key}: {err}", key=key, hint="ignore lists use Python regular expressions"
            ) from err


def _check_structure(rules: StructureRules) -> None:
    """Architecture name, depth, fan keys and the layer/coupling node names (req 6.3-6.7)."""
    if not rules.architecture.strip():
        raise ConfigError(
            "structure.architecture: the architecture name is empty",
            key="structure.architecture",
            hint='e.g. "Directory Structure"',
        )
    if rules.depth < 1:
        raise ConfigError(
            f"structure.depth: {rules.depth} is below 1",
            key="structure.depth",
            hint="depth 1 is the top level of the architecture",
        )
    for fan_key in rules.fan:
        if fan_key not in FAN_KEYS:
            raise ConfigError(
                f"structure.fan.{fan_key}: unknown fan limit",
                key=f"structure.fan.{fan_key}",
                hint=f"expected one of {', '.join(FAN_KEYS)}",
            )
    _check_layers(rules.layers)
    _check_coupling(rules.coupling)


def _check_layers(layers: Sequence[LayerRule]) -> None:
    key = "structure.layers"
    for rule in layers:
        if all(name.strip() for name in (rule.name, rule.node, *rule.may_depend_on)):
            continue
        raise ConfigError(
            f"{key}: rule {rule.name!r} has an empty name, node or dependency target",
            key=key,
            hint="every layer rule needs a name, an architecture node and non-empty targets",
        )


def _check_coupling(rules: Sequence[CouplingRule]) -> None:
    key = "structure.coupling"
    for rule in rules:
        if rule.from_node.strip() and rule.to_node.strip():
            continue
        raise ConfigError(
            f"{key}: a coupling rule has an empty node name",
            key=key,
            hint="from_node and to_node name architecture nodes",
        )


def _check_availability(settings: Settings, availability: MetricAvailability) -> AvailabilityReport:
    """Split the thresholds into the ones this run can evaluate and the ones it cannot.

    A metric the catalogue reports for none of the configured languages stops the Gate when
    the Gate does not ship it — an unknown name, req 3.8 — and is dropped with a note when it
    is one of the built-in defaults, which are known metrics this repository's languages
    happen not to compute (req 3.1, 5.5).
    """
    languages = settings.project.languages or []
    if not languages:
        return AvailabilityReport(thresholds=tuple(settings.thresholds))
    _check_languages(languages, availability)
    evaluated: list[ThresholdSpec] = []
    dropped: list[ThresholdSpec] = []
    for spec in settings.thresholds:
        if _metric_exists(spec, languages, availability):
            evaluated.append(spec)
        elif is_default_threshold(spec.scope, spec.metric):
            dropped.append(spec)
        else:
            raise _unknown_metric(spec, languages)
    return AvailabilityReport(
        thresholds=tuple(evaluated),
        dropped=tuple(dropped),
        unavailable=_unavailable_metrics(languages, dropped),
    )


def _check_languages(languages: Sequence[str], availability: MetricAvailability) -> None:
    """Refuse a configured language Understand computes nothing at all for (req 3.8).

    A misspelt language answers every availability question with nothing, so without this
    check every shipped default would be dropped as unavailable and the Gate would run green
    having evaluated no rule at all — a worse silence than the one dropping fixes. The
    architecture scope is deliberately not asked: its metric list carries no language, so it
    answers the same for any string, a typo included (measured against the install). An empty
    name is refused before the catalogue is asked at all, because a kind string carrying no
    language matches *every* language — it answers the union, so a blank would quietly widen
    every threshold instead of narrowing it (measured: 50 metrics at the routine scope).
    """
    for language in languages:
        if not language.strip():
            raise ConfigError(
                "project.languages: a language name is empty",
                key="project.languages",
                hint=_LANGUAGE_HINT,
            )
        if any(availability.available(language, scope) for scope in LANGUAGE_SCOPES):
            continue
        raise ConfigError(
            f"project.languages: Understand computes no metric for language {language!r}",
            key="project.languages",
            hint=_LANGUAGE_HINT,
        )


def _unavailable_metrics(
    languages: Sequence[str], dropped: Sequence[ThresholdSpec]
) -> Mapping[str, tuple[str, ...]]:
    """The dropped metrics per language, for ``RunResult.unavailable_metrics`` (req 5.5).

    A dropped threshold is unavailable for every configured language — that is why it was
    dropped — so each language carries the whole list, and requirement 5.5's "which metrics
    for which language" holds for all of them. The names lose their stats prefix, because the
    catalogue, the snapshot and ``analysis.thresholds`` all speak the bare metric id: a
    dropped ``AVG:CyclomaticStrict`` has to be reported as ``CyclomaticStrict`` or it matches
    nothing downstream.
    """
    if not dropped:
        return _frozen_metrics()
    metrics = tuple(sorted({spec.ref.metric for spec in dropped}))
    return _frozen_metrics({language: metrics for language in sorted(languages)})


def _query_scopes(spec: ThresholdSpec) -> tuple[Scope, ...]:
    """Where a metric must exist: project-scope populations reduce over the element scopes."""
    if spec.scope == "project" and spec.ref.is_population:
        return ELEMENT_SCOPES
    return (spec.scope,)


def _metric_exists(
    spec: ThresholdSpec, languages: Sequence[str], availability: MetricAvailability
) -> bool:
    """Whether any configured language computes this metric at the scope it is configured for."""
    metric = spec.ref.metric
    if metric in SYNTHETIC_METRICS:
        return True
    return any(
        metric in availability.available(language, scope)
        for language in languages
        for scope in _query_scopes(spec)
    )


def _unknown_metric(spec: ThresholdSpec, languages: Sequence[str]) -> ConfigError:
    """The error an unknown metric name becomes: not shipped, and no language has it (3.8)."""
    key = _threshold_key(spec)
    return ConfigError(
        f"{key}: no metric {spec.ref.metric!r} for {', '.join(languages)} "
        f"at the {spec.scope} scope",
        key=key,
        hint="check the name against Understand's metric list for these languages",
    )
