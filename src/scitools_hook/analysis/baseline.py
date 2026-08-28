"""Adaptive baseline rules: parse, apply, tighten and capture (req 8.1-8.6).

A baseline is the set of values the project currently achieves, keyed by the rule name of
the threshold it belongs to (``routine.CyclomaticStrict``, ``project.AVG:CyclomaticStrict``
-- the same string as :attr:`~scitools_hook.config.models.ThresholdSpec.rule`, stats prefix
kept). Four pure functions make up the whole adaptive mode; the file itself is never touched
here, because ``runner.baseline_store.BaselineStore`` owns that boundary:

* :func:`parse_baseline` turns a raw document into a :class:`Baseline`, reporting every
  entry it had to skip instead of failing the run (req 8.6).
* :func:`apply` decides the effective limit of every configured threshold and records
  whether configuration or the baseline supplied it (req 8.2, 8.5).
* :func:`tighten` lowers baseline values that the current run beat and never raises one
  (req 8.3, 8.4).
* :func:`capture` reads the current worst value of every configured threshold from a
  snapshot (req 8.1).

Three decisions are worth stating, because the requirement leaves them open:

**A baseline can only narrow.** For a limit with a ``max`` the effective maximum is
``min(configured, baseline)``; for a ``min``-only limit it is ``max(configured, baseline)``.
A two-sided limit is narrowed on its **upper** bound only, since a baseline records observed
maxima. A baseline value outside the configured band -- one that would push ``max`` below
``min`` -- is reported and ignored (req 8.6), so no configured limit is ever loosened and no
invalid limit is ever built.

**The worst value is the one that ratchets.** :func:`capture` records the maximum of an
element scope for a threshold that has a ``max``, and the minimum for a ``min``-only
threshold, because the worst comment ratio is the lowest one. Recording the maximum there
would raise the floor to the best entity in the project and fail every other one on the very
next run. Population thresholds (a stats prefix, or the ``project``/``arch`` scopes) are
reduced exactly as ``analysis.thresholds`` reduces them, so a capture always equals the
value the evaluator will compute for the same snapshot: applying a fresh capture flags
nothing. A threshold whose metric has no data yields no entry at all.

**Tightening only ever lowers.** :func:`tighten` is symmetric across bound directions --
requirement 8.4 forbids raising a stored value, full stop -- so a ``min``-only entry follows
its metric downward and is floored by the configured minimum in :func:`apply`, which is why
that direction can still never loosen a configured limit. Keys the baseline does not already
hold are left out: a newly configured threshold enters the baseline through :func:`capture`,
where the operator asks for it explicitly.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from math import isfinite
from typing import Any, Final, Literal

from scitools_hook.analysis.population import reduce
from scitools_hook.config.metric_names import ELEMENT_SCOPES
from scitools_hook.config.models import Limit, ThresholdSpec
from scitools_hook.models.baseline import Baseline, BaselineIssue
from scitools_hook.models.findings import EffectiveThreshold, TightenedLimit
from scitools_hook.models.snapshot import ProjectSnapshot

BASELINE_VERSION: Final = 1
"""The only baseline format the Gate writes; a document claiming another one is reported."""

MEMBERS: Final = ("version", "captured_at", "values")
"""Every member a baseline document may carry; anything else is reported and ignored."""

Source = Literal["config", "baseline"]
"""Where the effective limit of a threshold came from (req 8.5)."""


def parse_baseline(
    raw: object, specs: Sequence[ThresholdSpec]
) -> tuple[Baseline | None, list[BaselineIssue]]:
    """Read ``raw`` tolerantly into a baseline, reporting everything it had to skip (req 8.6).

    ``raw`` is whatever the store decoded, so it is typed as ``object`` and inspected here:
    the tolerance requirement 8.6 asks for is worthless if a malformed document cannot even
    reach this function. Every rejected entry -- a key no configured threshold owns, a value
    that is not a finite number -- becomes a :class:`BaselineIssue` naming that key, and the
    remaining entries still form a baseline, so the run continues with configured limits for
    exactly the affected thresholds.

    The result is ``None`` only when the document holds no usable ``values`` mapping at all:
    it is not an object, or its ``values`` member is missing or not an object. Anything else
    -- an unknown ``version``, a wrong-typed ``captured_at``, an unknown member, even an
    empty ``values`` -- yields a baseline plus issues, because those problems do not stop a
    single limit from being read.
    """
    issues: list[BaselineIssue] = []
    if not isinstance(raw, Mapping):
        issues.append(BaselineIssue(message=f"a baseline is a JSON object, not {_kind(raw)}"))
        return None, issues
    values = raw.get("values")
    if not isinstance(values, Mapping):
        issues.append(BaselineIssue(message=f"baseline member 'values' is {_kind(values)}"))
        return None, issues
    _check_version(raw, issues)
    _check_members(raw, issues)
    known = {spec.rule for spec in specs}
    parsed = Baseline(captured_at=_captured_at(raw, issues), values=_values(values, known, issues))
    return parsed, issues


def apply(
    specs: Sequence[ThresholdSpec], baseline: Baseline | None
) -> tuple[list[EffectiveThreshold], list[BaselineIssue]]:
    """Resolve the effective limit of every configured threshold (req 8.2, 8.5, 8.6).

    The returned list has one entry per spec, in configuration order, whether or not the
    baseline had anything to say about it; ``source`` names the winner, which is what a
    finding reports as ``limit_source`` (req 8.5). Issues cover the baseline entries this
    call could not use: a key that is not configured (req 8.6) and a value that lies outside
    the configured band. ``baseline`` of ``None`` -- no file, or an unreadable one -- simply
    yields the configured limits and no issues.
    """
    values = baseline.values if baseline is not None else {}
    issues = _unknown_key_issues(values, {spec.rule for spec in specs})
    effective = [_apply_one(spec, values.get(spec.rule), issues) for spec in specs]
    return effective, issues


def tighten(
    baseline: Baseline, observed: Mapping[str, float]
) -> tuple[Baseline, list[TightenedLimit]]:
    """Lower every baseline value the run beat and report it; never raise one (req 8.3, 8.4).

    ``observed`` maps rule names to the values the run measured -- the highest values of the
    threshold pass, or a fresh :func:`capture`. A key the baseline does not hold is ignored,
    and so is a value that is not lower than the stored one, which is the whole of 8.4. The
    input baseline is left untouched and the returned one keeps its ``captured_at``: it still
    describes when these values were first recorded, and a `baseline` run replaces it.
    """
    values = dict(baseline.values)
    tightened: list[TightenedLimit] = []
    for rule in sorted(values):
        current = observed.get(rule)
        if current is None or current >= values[rule]:
            continue
        tightened.append(TightenedLimit(rule=rule, previous=values[rule], current=current))
        values[rule] = current
    return baseline.model_copy(update={"values": values}), tightened


def capture(
    snapshot: ProjectSnapshot, specs: Sequence[ThresholdSpec], captured_at: str | None = None
) -> Baseline:
    """Record the current worst value of every configured threshold (req 8.1).

    An element-scope threshold takes the maximum over the entities of its scope, or the
    minimum when its limit only has a ``min`` bound; a stats-prefixed or scope-level
    threshold reduces the snapshot's population vector with the same reducer the evaluator
    uses. A threshold whose metric no entity carries, and a population that cannot be
    reduced, yield no entry, so a baseline never claims a value it did not observe.

    ``captured_at`` defaults to the current UTC instant; every caller that needs a
    reproducible result -- tests, and any pipeline that stamps a whole run once -- passes its
    own, so nothing in this module reads the clock behind a caller's back.
    """
    values: dict[str, float] = {}
    for spec in specs:
        observed = _observed(snapshot, spec)
        if observed is not None:
            values[spec.rule] = observed
    return Baseline(captured_at=captured_at if captured_at is not None else _now(), values=values)


def _now() -> str:
    """The current UTC instant, in the ISO-8601 form the baseline file stores."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def _kind(value: object) -> str:
    """How to name ``value``'s type in an issue message."""
    return "missing" if value is None else f"a {type(value).__name__}"


def _check_version(raw: Mapping[Any, Any], issues: list[BaselineIssue]) -> None:
    """Report a document that claims a version this Gate does not write (req 8.6)."""
    version = raw.get("version", BASELINE_VERSION)
    if isinstance(version, bool) or version != BASELINE_VERSION:
        issues.append(
            BaselineIssue(
                message=(
                    f"baseline version {version!r} is not {BASELINE_VERSION}; "
                    f"reading it as version {BASELINE_VERSION}"
                )
            )
        )


def _check_members(raw: Mapping[Any, Any], issues: list[BaselineIssue]) -> None:
    """Report every member outside the documented baseline shape."""
    for member in raw:
        if member not in MEMBERS:
            issues.append(BaselineIssue(message=f"unknown baseline member {member!r}; ignored"))


def _captured_at(raw: Mapping[Any, Any], issues: list[BaselineIssue]) -> str:
    """The capture timestamp; a wrong-typed one is reported and read as unknown."""
    stamp = raw.get("captured_at", "")
    if isinstance(stamp, str):
        return stamp
    issues.append(BaselineIssue(message=f"baseline member 'captured_at' is {_kind(stamp)}"))
    return ""


def _values(
    values: Mapping[Any, Any], known: set[str], issues: list[BaselineIssue]
) -> dict[str, float]:
    """The usable entries of ``values``; every other one becomes an issue (req 8.6)."""
    parsed: dict[str, float] = {}
    for key, value in values.items():
        if not isinstance(key, str):
            issues.append(BaselineIssue(message=f"baseline key {key!r} is not a string"))
            continue
        if key not in known:
            issues.append(BaselineIssue(key=key, message=_unknown_key_message(key)))
            continue
        number = _as_number(value)
        if number is None:
            issues.append(
                BaselineIssue(key=key, message=f"baseline value {value!r} is not a number")
            )
            continue
        parsed[key] = number
    return parsed


def _as_number(value: object) -> float | None:
    """``value`` as a finite float, or ``None`` when it is not a number at all.

    A ``bool`` is rejected although Python counts it as an ``int``: a limit of ``True`` is a
    corrupt entry, not a limit of one. So are ``NaN`` and the infinities, which no comparison
    against a limit could answer meaningfully.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if isfinite(value) else None


def _unknown_key_message(key: str) -> str:
    """Why a baseline entry cannot be used: nothing configured owns that rule (req 8.6)."""
    return f"baseline key {key!r} is not configured as a threshold; using the configured limits"


def _unknown_key_issues(values: Mapping[str, float], known: set[str]) -> list[BaselineIssue]:
    """One issue per baseline key no configured threshold owns, in a stable order."""
    return [
        BaselineIssue(key=key, message=_unknown_key_message(key))
        for key in sorted(values)
        if key not in known
    ]


def _apply_one(
    spec: ThresholdSpec, value: float | None, issues: list[BaselineIssue]
) -> EffectiveThreshold:
    """The effective threshold for one spec, with the source of its limit (req 8.2, 8.5)."""
    limit: Limit = spec.limit
    source: Source = "config"
    if value is not None:
        limit, source = _narrow(spec, value, issues)
    return EffectiveThreshold(spec=spec, metric=spec.ref, limit=limit, source=source)


def _narrow(spec: ThresholdSpec, value: float, issues: list[BaselineIssue]) -> tuple[Limit, Source]:
    """Narrow ``spec``'s limit by a baseline value, or keep the configured one.

    The upper bound wins when the limit has one, because a baseline records observed maxima;
    only a ``min``-only limit is narrowed upward. A value that would push ``max`` below the
    configured ``min`` describes a project no configuration allows, so it is reported and
    dropped rather than turned into an impossible limit (req 8.6).
    """
    limit = spec.limit
    if limit.max is not None:
        if value >= limit.max:
            return limit, "config"
        if limit.min is not None and value < limit.min:
            issues.append(BaselineIssue(key=spec.rule, message=_below_minimum(spec, value)))
            return limit, "config"
        return Limit(max=value, min=limit.min), "baseline"
    if limit.min is not None and value > limit.min:
        return Limit(min=value), "baseline"
    return limit, "config"


def _below_minimum(spec: ThresholdSpec, value: float) -> str:
    """Why a baseline value cannot narrow a two-sided limit (req 8.6)."""
    return (
        f"baseline value {value:g} is below the configured minimum {spec.limit.min:g}; "
        f"using the configured limits"
    )


def _observed(snapshot: ProjectSnapshot, spec: ThresholdSpec) -> float | None:
    """The current value of one threshold, or ``None`` when the snapshot has no data for it."""
    if spec.ref.is_population or spec.scope not in ELEMENT_SCOPES:
        return _population_value(snapshot, spec)
    return _element_value(snapshot, spec)


def _population_value(snapshot: ProjectSnapshot, spec: ThresholdSpec) -> float | None:
    """Reduce the population vector of ``spec``'s scope exactly as the evaluator does.

    A scope without entities of its own records a plain metric as a one-element vector, so
    its maximum is that value; ``analysis.thresholds`` reads it the same way.
    """
    values = snapshot.populations.get(spec.scope, {}).get(spec.ref.metric, [])
    prefix = spec.ref.prefix
    if prefix is not None:
        return reduce(prefix, values)
    return max(values) if values else None


def _element_value(snapshot: ProjectSnapshot, spec: ThresholdSpec) -> float | None:
    """The worst value of ``spec``'s metric among the entities of its scope (req 8.1)."""
    metric = spec.ref.metric
    values = [
        record.metrics[metric]
        for record in snapshot.entities.values()
        if record.key.scope == spec.scope and metric in record.metrics
    ]
    if not values:
        return None
    return max(values) if spec.limit.max is not None else min(values)
