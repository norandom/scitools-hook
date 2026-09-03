"""Threshold recommendation: measure the repository and say what limits fit its shape.

This is the third thing, and it is deliberately not either of the other two.

* ``baseline`` records **where you are** -- today's worst value per rule, so existing debt
  reports as pre-existing. Descriptive, and it moves every time the code does.
* the shipped defaults are **somebody's opinion** -- ``CyclomaticStrict 10`` is McCabe's
  conventional number, derived from nothing in your repository.
* this module answers **where to aim**: for each configured ceiling, how much of this
  repository is already inside it, what each candidate limit would cost in findings, and who
  the worst offenders are.

**The measurement that shaped every decision below.** On a real 770-file Python repository
(7,429 routines, 1,345 classes, Understand's own builtin stubs and the interpreter's
site-packages excluded) the shipped defaults already contain 97.9%-99.7% of every population:
``CyclomaticStrict`` p50 1, p95 7, max 45 against a default of 10; ``CountStmt`` p50 5, p95 19
against 40. A recommender that reported a percentile would therefore have *tightened*
``CyclomaticStrict`` from 10 to 7 and produced a mass of findings for almost no benefit --
while missing the only genuinely actionable fact in the whole table, which is that
``CountPath`` has a median of 1 and a maximum of 955,514,880. That is not a threshold level.
It is a handful of routines.

Three rules come out of that, and they are the whole policy:

**Never propose tightening.** A limit that already contains :data:`TARGET_COVERAGE` of the
population is reported as ``keep``, with the coverage and the percentiles that say so. The
tighter candidates are still *shown*, with the exact number of entities each would put
outside, because an operator may well want to hold a tighter line -- but choosing to is a
policy decision, and a tool that manufactures one from a percentile is a tool that always
proposes a change, which is a tool nobody trusts. Only a limit the repository has already
outgrown is proposed against, and only upwards.

**Show the trade, never the bare number.** :class:`Candidate` is what an operator actually
decides between: at this limit, N entities are outside, and here they are by name. The
proposal is one row of that table with an arrow on it, not the table's purpose.

**Separate the tail from the level.** :attr:`MetricAdvice.tail_dominated` marks a metric whose
maximum is :data:`TAIL_RATIO` times its 95th percentile or more. No ceiling describes such a
population -- moving the limit either changes nothing or condemns a third of the repository --
so those metrics are reported as a list of outliers to fix rather than as a number to set.

Nothing here reads a clock, a file or an environment: it is a pure function of a whole-project
:class:`~scitools_hook.models.snapshot.ProjectSnapshot` and the thresholds in force.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final, Literal

from scitools_hook.config.metric_names import ELEMENT_SCOPES, Scope
from scitools_hook.config.models import ThresholdSpec
from scitools_hook.config.template import RecommendedThreshold
from scitools_hook.models.snapshot import ProjectSnapshot

TARGET_COVERAGE: Final = 0.95
"""The share of a population a limit must contain before it is reported as fitting.

0.95 rather than 0.99 or 1.0, and the reason is the measurement in the module docstring: on a
repository whose defaults already hold 97.9%-99.7%, a target of 1.0 would propose raising
every limit to the single worst entity -- which is what ``baseline`` records, and is exactly
the confusion this module exists to avoid. A limit is meant to leave the tail outside; the
tail is the work.
"""

TAIL_RATIO: Final = 50.0
"""How far above the 95th percentile a maximum must sit to be called a tail, not a level.

Measured on the repository in the module docstring, this number separates the six routine
metrics cleanly and is not a close call in either direction: ``CountPath`` scores 119,439,360
(max 955,514,880 against a p95 of 8) while the next highest is ``CountLineCode`` at 10.1 and
``CyclomaticStrict`` at 6.4. Anything from about 15 to a million would classify that table
identically; 50 is stated as a round number inside that gap rather than fitted to it.
"""

OFFENDERS_SHOWN: Final = 3
"""How many worst entities a metric names. Enough to see what the tail is made of."""

_PLURALS: Final[dict[Scope, str]] = {
    "routine": "routines",
    "class": "classes",
    "file": "files",
    "project": "projects",
    "arch": "architecture nodes",
}
"""Written out because ``+ "s"`` produced ``1345 classs`` in the first run against a real
repository. A report an operator will not read twice is a report they will not trust once."""


def plural(scope: Scope, count: int) -> str:
    """``1 class`` / ``1345 classes`` -- the scope named for how many of it there are."""
    return scope if count == 1 else _PLURALS[scope]


Verdict = Literal["keep", "raise"]
"""``keep``: the configured limit already fits. ``raise``: this repository has outgrown it."""

_LADDER_BASE: Final[tuple[float, ...]] = (
    1,
    2,
    3,
    4,
    5,
    6,
    8,
    10,
    12,
    15,
    20,
    25,
    30,
    40,
    50,
    60,
    80,
)
"""The readable numbers a proposal is rounded up to, before scaling by powers of ten.

Every shipped default except two is on this ladder (10, 8, 4, 3, 60, 40, 5, 100, 20, 15, 12,
500, 25), which is the point: a proposal has to look like a limit a person would write, or
nobody will paste it. ``70`` and ``5`` are the exceptions and both are percentages or ratios,
which this module does not propose against.
"""

MAX_LADDER: Final = 1e12
"""Where the ladder stops climbing. Past this a maximum is a tail, not a limit to propose."""


# --- what one metric's population looks like --------------------------------------


@dataclass(frozen=True, slots=True)
class Offender:
    """One entity at the top of a metric's population, named so it can be opened."""

    value: float
    path: str
    longname: str
    line: int | None = None


@dataclass(frozen=True, slots=True)
class Distribution:
    """The shape of one metric's population over one scope.

    The percentiles are **nearest-rank**: ``p95`` is the smallest observed value at least 95%
    of the population is at or below. Two properties follow and both are relied on here --
    every percentile is a value some entity actually has (so an integer metric never reports
    ``81.72``), and ``share_within(p95) >= 0.95`` holds by construction, so a percentile and
    the coverage table below it can never contradict each other.
    """

    count: int
    p50: float
    p90: float
    p95: float
    p99: float
    maximum: float


@dataclass(frozen=True, slots=True)
class Candidate:
    """One limit an operator could choose, and what choosing it would cost.

    ``outside`` is the number of entities that would be reported at this limit today. It is a
    count of *entities*, not of findings: the ratchet may downgrade a pre-existing one to a
    warning, which makes the number an upper bound on the noise and never an under-estimate.
    """

    limit: float
    outside: int
    share_outside: float
    configured: bool = False
    proposed: bool = False


@dataclass(frozen=True, slots=True)
class MetricAdvice:
    """Everything this module has to say about one configured ceiling."""

    rule: str
    scope: Scope
    metric: str
    configured: float
    verdict: Verdict
    distribution: Distribution
    candidates: tuple[Candidate, ...]
    offenders: tuple[Offender, ...]
    tail_ratio: float
    tail_dominated: bool
    proposed: float | None = None
    """The limit to move to, or ``None`` for ``keep``. Never below :attr:`configured`."""

    @property
    def share_inside(self) -> float:
        """The share of the population the **configured** limit already contains."""
        for candidate in self.candidates:
            if candidate.configured:
                return 1.0 - candidate.share_outside
        raise AssertionError("every advice carries its configured limit as a candidate")


@dataclass(frozen=True, slots=True)
class Skipped:
    """A configured threshold this module has nothing to say about, and why not.

    Reported rather than dropped. A recommendation that silently covered two thirds of the
    configuration would read as a verdict on all of it, which is the same class of defect as a
    gate that silently stops measuring a file.
    """

    rule: str
    reason: str


@dataclass(frozen=True, slots=True)
class Recommendation:
    """One whole run: what was measured, what fits, and what was left alone."""

    counts: dict[Scope, int]
    advice: tuple[MetricAdvice, ...]
    skipped: tuple[Skipped, ...] = ()
    scoped: tuple[str, ...] = ()
    """Names of the configured path scopes, so the report can disclose what it did not do.

    Every population here is measured across the **whole** project against the **global**
    limit. A ``[scope.tests]`` that raises ``CyclomaticStrict`` to 15 for one tree does not
    change that: those routines are still counted against 10 in the table below. Reporting
    ``keep 10`` while silently including files nothing judges by 10 would be a claim the run
    did not establish, so the names travel out of here and the report says so. Splitting the
    populations per scope is the right answer and is not what this does today.
    """

    @property
    def changes(self) -> tuple[MetricAdvice, ...]:
        """The advice that proposes a different limit; empty when every default fits."""
        return tuple(item for item in self.advice if item.proposed is not None)

    @property
    def tails(self) -> tuple[MetricAdvice, ...]:
        """The metrics whose maximum is a tail rather than a level (:data:`TAIL_RATIO`)."""
        return tuple(item for item in self.advice if item.tail_dominated)


# --- the measurement --------------------------------------------------------------


NO_VALUE: Final = "this project reports no value for it, so there is nothing to measure"
POPULATION: Final = (
    "a population threshold is reduced over the whole scope, so it has no per-entity trade"
)
NO_MAXIMUM: Final = "only a minimum is configured; this measures ceilings on entities"


def recommend(
    snapshot: ProjectSnapshot,
    specs: Sequence[ThresholdSpec],
    target: float = TARGET_COVERAGE,
    scopes: Sequence[str] = (),
) -> Recommendation:
    """Measure ``snapshot`` against the ceilings in ``specs`` and say which of them fit.

    ``specs`` are the thresholds actually in force -- the merged configuration, not the shipped
    defaults -- so on a repository that already tunes its limits the answer is about *those*
    limits. ``snapshot`` must be a whole-project extraction: a bounded one holds the entities
    of one change, and a percentile over a handful of files is not a statement about a
    repository. The caller guarantees that; nothing here can check it.
    """
    populations = _populations(snapshot)
    advice: list[MetricAdvice] = []
    skipped: list[Skipped] = []
    for spec in specs:
        reason = _unmeasurable(spec)
        if reason is not None:
            skipped.append(Skipped(rule=spec.rule, reason=reason))
            continue
        entries = populations.get((spec.scope, spec.ref.metric), ())
        if not entries:
            skipped.append(Skipped(rule=spec.rule, reason=NO_VALUE))
            continue
        advice.append(_advise(spec, entries, target))
    return Recommendation(
        counts=_counts(snapshot),
        advice=tuple(advice),
        skipped=tuple(skipped),
        scoped=tuple(scopes),
    )


def _unmeasurable(spec: ThresholdSpec) -> str | None:
    """Why this threshold has no per-entity trade, or ``None`` when it has one."""
    if spec.ref.is_population or spec.scope not in ELEMENT_SCOPES:
        return POPULATION
    if spec.limit.max is None:
        return NO_MAXIMUM
    return None


def _counts(snapshot: ProjectSnapshot) -> dict[Scope, int]:
    """How many entities of each element scope the snapshot holds; the report's denominator."""
    counts: dict[Scope, int] = {}
    for key in snapshot.entities:
        if key.scope in ELEMENT_SCOPES:
            counts[key.scope] = counts.get(key.scope, 0) + 1
    return counts


def _populations(snapshot: ProjectSnapshot) -> dict[tuple[Scope, str], tuple[Offender, ...]]:
    """Every ``(scope, metric)`` this snapshot carries, as entities that hold a value.

    Built from ``snapshot.entities`` rather than from ``snapshot.populations``: the population
    vectors are bare floats, and a recommendation that could not name the routine at the top
    of the tail would be the number generator this module exists not to be.
    """
    found: dict[tuple[Scope, str], list[Offender]] = {}
    for key, record in snapshot.entities.items():
        if key.scope not in ELEMENT_SCOPES:
            continue
        for metric, value in record.metrics.items():
            found.setdefault((key.scope, metric), []).append(
                Offender(
                    value=value,
                    path=key.path,
                    longname=key.longname,
                    line=record.ref.line,
                )
            )
    return {slot: tuple(entries) for slot, entries in found.items()}


def _advise(spec: ThresholdSpec, entries: Sequence[Offender], target: float) -> MetricAdvice:
    """One metric, measured: its shape, the trade at each candidate, and the verdict."""
    assert spec.limit.max is not None  # noqa: S101 - _unmeasurable filtered these out
    configured = float(spec.limit.max)
    values = sorted(item.value for item in entries)
    shape = distribution(values)  # already ascending; `distribution` sorts defensively anyway
    fit = _smallest_fitting(values, target)
    proposed = fit if fit is not None and fit > configured else None
    limits = _candidate_limits(configured, proposed, shape)
    candidates = tuple(
        Candidate(
            limit=limit,
            outside=_outside(values, limit),
            share_outside=_outside(values, limit) / len(values),
            configured=limit == configured,
            proposed=proposed is not None and limit == proposed,
        )
        for limit in limits
    )
    ratio = shape.maximum / max(shape.p95, 1.0)
    return MetricAdvice(
        rule=spec.rule,
        scope=spec.scope,
        metric=spec.ref.metric,
        configured=configured,
        verdict="raise" if proposed is not None else "keep",
        proposed=proposed,
        distribution=shape,
        candidates=candidates,
        offenders=_worst(entries),
        tail_ratio=ratio,
        tail_dominated=ratio >= TAIL_RATIO,
    )


def distribution(values: Sequence[float]) -> Distribution:
    """The nearest-rank shape of a non-empty population, in any order."""
    ordered = sorted(values)
    return Distribution(
        count=len(ordered),
        p50=_at_rank(ordered, 0.50),
        p90=_at_rank(ordered, 0.90),
        p95=_at_rank(ordered, 0.95),
        p99=_at_rank(ordered, 0.99),
        maximum=ordered[-1],
    )


def percentile(values: Sequence[float], share: float) -> float:
    """The nearest-rank percentile of a non-empty ``values``, in any order.

    The smallest observed value that at least ``share`` of the population is at or below --
    ``ceil(share * n)`` in one-based ranks. No interpolation: the answer is always a value some
    entity has, which is what lets the report print ``p95 7`` for an integer metric and lets
    ``share_within(percentile(v, s)) >= s`` hold exactly.

    **It sorts, rather than requiring a sorted input.** The first version documented "ascending"
    and trusted the caller, and the property test above caught it immediately: handed the
    entities in snapshot order it answered 1 where the median was 3, silently, with no way for
    a caller to notice. A function whose contract can be violated without a symptom is the
    silent-green shape this project keeps meeting, so the contract is enforced instead of
    stated. The cost is one sort per call; :func:`distribution` sorts once for its four.
    """
    return _at_rank(sorted(values), share)


def _at_rank(ordered: Sequence[float], share: float) -> float:
    """The ``ceil(share * n)``-th smallest of an already-ascending, non-empty population.

    The rank is computed in millionths and rounded first, so ``0.95 * 20`` is 19 rather than
    the 20 that ``ceil`` of a binary-float 19.000000000000004 would give.
    """
    rank = -(-int(round(share * len(ordered) * 1_000_000)) // 1_000_000)
    return ordered[min(max(rank, 1), len(ordered)) - 1]


def share_within(values: Sequence[float], limit: float) -> float:
    """The share of ``values`` at or below ``limit``; the complement of a candidate's cost."""
    return 1.0 - _outside(values, limit) / len(values)


def _outside(values: Sequence[float], limit: float) -> int:
    """How many entities a limit would report; ``analysis.thresholds`` breaches on ``>``."""
    return sum(1 for value in values if value > limit)


def _smallest_fitting(values: Sequence[float], target: float) -> float | None:
    """The smallest readable limit containing ``target`` of the population, or ``None``.

    ``None`` when even :data:`MAX_LADDER` does not reach it, which means the population has no
    level -- the ``CountPath`` case. The caller then proposes nothing and the tail report says
    why, rather than emitting a limit of 10^12 that would switch the rule off in all but name.
    """
    wanted = percentile(values, target)
    step = readable_at_least(wanted)
    return step if step is not None and share_within(values, step) >= target else None


def readable_at_least(value: float) -> float | None:
    """The smallest number on :data:`_LADDER_BASE`, scaled by a power of ten, that is >= ``value``.

    ``None`` above :data:`MAX_LADDER`. A limit an operator would not write is a limit an
    operator will not paste, so a proposal of 7 becomes 8 and one of 101 becomes 120 -- always
    upwards, so rounding can only widen a proposal and never tighten one behind the target.
    """
    if value <= 0:
        return _LADDER_BASE[0]
    scale = 1.0
    while scale <= MAX_LADDER:
        for step in _LADDER_BASE:
            candidate = step * scale
            if candidate >= value:
                return candidate if candidate <= MAX_LADDER else None
        scale *= 10
    return None


def _candidate_limits(
    configured: float, proposed: float | None, shape: Distribution
) -> tuple[float, ...]:
    """The limits the trade table prices: the configured one, the proposal, and its neighbours.

    Two rungs either side of the configured limit, so an operator weighing 10 against 15 sees
    both and the two steps between them, and so the cost of holding a *tighter* line is on the
    page even though this module will never propose one.
    """
    anchor = configured if proposed is None else proposed
    wanted = {configured, anchor, *_neighbours(configured), *_neighbours(anchor)}
    return tuple(sorted(limit for limit in wanted if limit <= max(shape.maximum, anchor)))


def _neighbours(limit: float) -> tuple[float, ...]:
    """The two ladder rungs below ``limit`` and the two above it, as far as they exist."""
    ladder = _ladder_around(limit)
    if limit not in ladder:
        return tuple(ladder[:2])
    at = ladder.index(limit)
    return tuple(ladder[max(at - 2, 0) : at + 3])


def _ladder_around(limit: float) -> list[float]:
    """Enough of the ladder to hold ``limit`` and its neighbours, ascending."""
    rungs: list[float] = []
    scale = 1.0
    while scale <= MAX_LADDER:
        rungs.extend(step * scale for step in _LADDER_BASE)
        if rungs[-1] > limit * 10:
            break
        scale *= 10
    return rungs


def _worst(entries: Sequence[Offender]) -> tuple[Offender, ...]:
    """The :data:`OFFENDERS_SHOWN` highest entities, worst first, ties broken by name."""
    ranked = sorted(entries, key=lambda item: (-item.value, item.path, item.longname))
    return tuple(ranked[:OFFENDERS_SHOWN])


# --- the configuration a recommendation would produce ------------------------------


def deviations(recommendation: Recommendation) -> list[RecommendedThreshold]:
    """The pasteable lines: one per proposal, each carrying the measurement behind it.

    Only deviations, which is the house style ``config.template`` already uses for detection.
    A ``keep`` produces no line at all -- the evidence for keeping a limit is in the report,
    and writing out a value identical to the one already in force would make a file that says
    nothing look like a file that decided something.
    """
    return [
        RecommendedThreshold(
            scope=item.scope,
            metric=item.metric,
            limit=item.proposed,
            evidence=evidence(item),
        )
        for item in recommendation.changes
        if item.proposed is not None
    ]


def evidence(item: MetricAdvice) -> str:
    """The one-line measurement that justifies a proposed limit, for the comment above it."""
    shape = item.distribution
    return (
        f"measured {shape.count} {plural(item.scope, shape.count)}: p50 {_number(shape.p50)}, "
        f"p95 {_number(shape.p95)}, max {_number(shape.maximum)}; "
        f"{_cost(item, item.configured)} at the configured {_number(item.configured)}, "
        f"{_cost(item, item.proposed)} at {_number(item.proposed)}"
    )


def _cost(item: MetricAdvice, limit: float | None) -> str:
    """How many entities one candidate leaves outside, as the report words it."""
    for candidate in item.candidates:
        if candidate.limit == limit:
            return f"{candidate.outside} outside ({candidate.share_outside:.1%})"
    return "not measured"


def _number(value: float | None) -> str:
    """A metric value without the trailing ``.0`` almost all of them would carry."""
    return "none" if value is None else f"{value:g}"


def counts_line(counts: dict[Scope, int]) -> str:
    """``7429 routines, 1345 classes, 770 files`` -- what a run measured, in scope order."""
    parts = [
        f"{counts[scope]} {plural(scope, counts[scope])}"
        for scope in ELEMENT_SCOPES
        if counts.get(scope)
    ]
    return ", ".join(parts) if parts else "nothing"


def scopes_measured(entries: Iterable[Scope]) -> tuple[Scope, ...]:
    """``entries`` in the canonical scope order, for a stable report."""
    seen = set(entries)
    return tuple(scope for scope in ELEMENT_SCOPES if scope in seen)
