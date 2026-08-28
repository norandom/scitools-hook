"""Configuration checks beyond shape: metric grammar, scopes, regexes, structure (req 3.6, 3.8).

``models`` validates the shape of every value; this module validates their meaning. The checks
that need nothing but the settings themselves always run (they are also re-run here for a
``Settings`` built in code rather than parsed from TOML); the metric names are additionally
checked against an Understand metric catalogue when one is supplied. The catalogue arrives
through the ``MetricAvailability`` protocol declared here, so ``config`` never imports the
``understand`` adapter. Every failure is a ``ConfigError`` naming the dotted key.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

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


@runtime_checkable
class MetricAvailability(Protocol):
    """Metric catalogue seen from ``config``; ``understand.catalogue`` satisfies it (req 5.5)."""

    def available(self, language: str, scope: Scope) -> set[str]:
        """Metric identifiers Understand computes for ``language`` at ``scope``."""


def validate_settings(settings: Settings, availability: MetricAvailability | None) -> None:
    """Check ``settings`` beyond its shape; raise ``ConfigError`` naming the dotted key.

    Pure: no file system, no Understand, no mutation. ``file`` is left unset because a
    ``Settings`` no longer knows where a value came from — ``config.loader.attach_source``
    fills it in from the provenance map. When ``availability`` is given, every configured
    metric must exist for at least one configured language at its scope (req 3.8, 5.5).
    """
    for spec in settings.thresholds:
        _check_threshold(spec)
    _check_ignores(settings.ignore)
    _check_structure(settings.structure)
    if availability is not None:
        _check_availability(settings, availability)


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
    for field, patterns in lists:
        key = f"ignore.{field}"
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


def _check_availability(settings: Settings, availability: MetricAvailability) -> None:
    """Reject metrics the catalogue reports for none of the configured languages (req 3.8)."""
    languages = settings.project.languages or []
    if not languages:
        return
    for spec in settings.thresholds:
        _check_metric_exists(spec, languages, availability)


def _query_scopes(spec: ThresholdSpec) -> tuple[Scope, ...]:
    """Where a metric must exist: project-scope populations reduce over the element scopes."""
    if spec.scope == "project" and spec.ref.is_population:
        return ELEMENT_SCOPES
    return (spec.scope,)


def _check_metric_exists(
    spec: ThresholdSpec, languages: Sequence[str], availability: MetricAvailability
) -> None:
    metric = spec.ref.metric
    if metric in SYNTHETIC_METRICS:
        return
    for language in languages:
        for scope in _query_scopes(spec):
            if metric in availability.available(language, scope):
                return
    key = _threshold_key(spec)
    raise ConfigError(
        f"{key}: no metric {metric!r} for {', '.join(languages)} at the {spec.scope} scope",
        key=key,
        hint="check the name against Understand's metric list for these languages",
    )
