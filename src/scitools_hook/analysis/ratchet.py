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
* :func:`within_limit` says whether a ratchet finding's after value has broken its own limit.
  Requirement 4.4 asks for a *report* and this module still makes one either way; what the
  answer decides is the **severity** ``analysis.classify`` gives it, through
  ``settings.ratchet.below_limit_severity`` (task 11.15). Reported and blocking are two
  different things here, and conflating them is what made a 27-line routine gaining one line
  refuse a commit against a limit of 60 -- the measurement is in
  ``config.models.RatchetSettings``, and so is the decay this trade accepts.
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

**Two before values are not measurements of the change, and this module drops both**
(task 11.9). Requirement 4.5 already says an entity the change *added* has no pre-change
value; these are the same statement about a value that exists but does not mean what the
comparison assumes.

* **The entity got simpler and the count went up.** ``MaxNesting``'s hint offers two
  remedies, and the second one -- "invert the condition and return early so the body stops
  nesting" -- makes the routine *longer*: measured through the installed CLI, flattening a
  five-deep routine into guard clauses moved ``MaxNesting`` 5 -> 2 while
  ``routine.CountLineCode`` and ``routine.CountStmt`` both moved 8 -> 11. A ratchet on the
  count refuses that fix. :data:`COUNTS_DECOMPOSITION_RAISES` names the counts this can
  happen to and :data:`COMPLEXITY_EVIDENCE` the metrics that have to have moved the right way
  for it to be believed; the counts that *no* entity can ever show the improvement for do not
  ratchet at all (``config.models.DECOMPOSITION_COUNTS``).
* **The before side could not be parsed.** A file ``und analyze`` failed on is not merely
  incomplete in the database: the entity at the parse site absorbs the remainder of the file
  into its own metrics and the declarations after it are absent altogether (task 11.11
  measured ``config/models.py`` reporting 3 classes for 15, and a 30-line function reporting
  ``CountStmt`` 66). Comparing against numbers of that shape reports *the analysis getting
  better* as the code getting worse -- ``file.CountDeclClass rose from 3 to 15`` for a commit
  that fixed a syntax error. So an entity whose file is named in ``before.parse_errors`` is
  not ratcheted; the parse error itself is still reported by the run (req 2.6), so nothing
  goes quiet.

**A routine whose parameter list changed is still the same routine** (task 11.6).
``EntityKey`` keeps ``parameters`` so that a real C++ overload pair stays two entities, which
means the key of a routine that gained an argument differs on the two sides: it reads as one
entity removed and one added, requirement 4.4 never fires, and *"an agent added parameters and
grew the function" -- the central case this gate exists to catch -- slips through*. Measured
through the installed CLI, one repository, two runs whose sources differ in nothing but the
parameter list: the routine that grew with its signature untouched drew the whole set of
routine-scope ratchet findings, ``routine deep.walk CountLineCode rose from 6 to 10`` among
them; the same growth with three parameters added drew **none at routine scope**, leaving only
the file-scope findings, whose keys have no parameter list to change.

:func:`pair_changed_signatures` closes that without weakening the key. Within one
``EntityKey.family`` -- ``(scope, path, longname)``, everything a signature change leaves
alone -- a key present only in ``after`` is paired with a key present only in ``before`` when
there is **exactly one of each**. That is precisely "the same routine, new signature", and
every other shape degrades to the behaviour this module already had:

* a genuinely new overload beside an unchanged one is one added and none removed, so it is new
  (req 4.5) and the unchanged overload keeps matching itself by its own key;
* a deleted overload is one removed and none added, so it stays deleted (req 4.10);
* two signatures changing at once in the same family is two and two, which no evidence can
  resolve, so nothing is paired and both read as new. An unpaired entity is not ratcheted --
  the same silence as today, and the honest answer rather than a guess.

Only ``evaluate_ratchet`` pairs. :func:`attach_before` deliberately does not, and the
asymmetry is the point: a paired before value *adds* a ratchet finding, while the before value
``attach_before`` supplies is what lets ``analysis.classify`` call a violation **pre-existing**
and therefore stop blocking. A guess may add a finding; it may not excuse one.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal, Self

from scitools_hook.config.metric_names import ELEMENT_SCOPES, format_metric_name
from scitools_hook.config.models import DECOMPOSITION_COUNTS, Limit
from scitools_hook.models.findings import EffectiveThreshold, Finding, build_rule_name
from scitools_hook.models.snapshot import EntityKey, EntityRecord, ProjectSnapshot

Bound = Literal["max", "min"]
"""Which bound of a limit a worsening value moved towards."""

COUNTS_DECOMPOSITION_RAISES: Final[frozenset[str]] = DECOMPOSITION_COUNTS | frozenset(
    {"routine.CountLineCode", "routine.CountStmt"}
)
"""Rules a decomposition can raise, and therefore the only ones the exemption applies to.

The eight in ``config.models.DECOMPOSITION_COUNTS`` do not ratchet by default, so in a
shipped configuration this set is reached through its other two members -- but an operator
who switches one of the eight back on gets the exemption with it rather than the raw
comparison. Every other ratcheted rule is left alone: a value that rises on
``CyclomaticStrict``, ``MaxNesting``, ``CountPath`` or ``MaxInheritanceTree`` has no
decomposition reading, so nothing should forgive it.
"""

COMPLEXITY_EVIDENCE: Final[frozenset[str]] = frozenset(
    {"CyclomaticStrict", "CyclomaticModified", "MaxNesting", "CountPath", "MaxCyclomaticStrict"}
)
"""The metrics whose fall is taken as proof that a count rose because the entity got simpler.

Each is a shape-of-the-control-flow measure that every hint in the catalogue asks to lower
and none asks to raise, and each is a default threshold -- so the numbers are in the snapshot
already. A configuration that drops all five from its thresholds drops the evidence with
them, and the exemption simply never fires; that is the honest failure, not a guess.

``Essential`` is deliberately absent, in both roles. Task 10.4 measured six guard clauses
scoring ``Essential`` 7 against 1 for the same logic as an elif ladder, so on Python it rises
under exactly the "return early" refactoring this exemption exists to permit: counting it as
evidence would be wrong, and letting it veto would cancel the exemption on its main case.
"""


def evaluate_ratchet(
    after: ProjectSnapshot,
    before: ProjectSnapshot,
    keys: Collection[EntityKey],
    specs: Sequence[EffectiveThreshold],
) -> list[Finding]:
    """Report every entity of ``keys`` whose value got worse between the two snapshots.

    ``keys`` is the affected set: entities the staged change touched, plus those whose
    dependencies moved (req 4.2). A key missing from the before side is looked for once more
    through :func:`pair_changed_signatures`, which finds the same routine under a new
    parameter list (task 11.6); a key still missing from either side yields nothing -- it is
    either new (req 4.5) or deleted (req 4.10) -- and so does a metric with the ratchet
    switched off in configuration (req 4.4), an entity in a file the before side could not
    parse, and a count a measured decomposition raised (both task 11.9; see the module
    docstring).
    """
    findings: list[Finding] = []
    pre_change = _PreChange.of(after, before)
    for threshold in specs:
        if not _is_ratcheted(threshold):
            continue
        scoped = _in_scope(keys, threshold)
        findings.extend(_compare_all(threshold, after, pre_change, scoped))
    return findings


def pair_changed_signatures(
    after: ProjectSnapshot, before: ProjectSnapshot
) -> dict[EntityKey, EntityKey]:
    """Which after key is the before key's routine under a new signature (task 11.6).

    One entry per family -- ``EntityKey.family``, the ``(scope, path, longname)`` a parameter
    list change leaves alone -- in which **exactly one** key exists only on the after side and
    **exactly one** exists only on the before side. Anything else is left out: a family with a
    new overload has one added and none removed, a family with a deleted one has none added,
    and a family with two of each carries no evidence about which became which. See the module
    docstring for why the ambiguous case is answered with silence.

    The empty answer is the common one -- a project whose signatures did not change pairs
    nothing -- so this is built once per run rather than per threshold.
    """
    added = _grouped_by_family(key for key in after.entities if key not in before.entities)
    removed = _grouped_by_family(key for key in before.entities if key not in after.entities)
    pairs: dict[EntityKey, EntityKey] = {}
    for family, new_keys in added.items():
        old_keys = removed.get(family, ())
        if len(new_keys) == 1 and len(old_keys) == 1:
            pairs[new_keys[0]] = old_keys[0]
    return pairs


def _grouped_by_family(keys: Iterable[EntityKey]) -> dict[tuple[str, str, str], list[EntityKey]]:
    """The given keys gathered under ``EntityKey.family``."""
    families: dict[tuple[str, str, str], list[EntityKey]] = {}
    for key in keys:
        families.setdefault(key.family, []).append(key)
    return families


def unparsed_files(snapshot: ProjectSnapshot) -> frozenset[str]:
    """The repository-relative paths ``und analyze`` failed on for this side (task 11.11).

    ``ParseError.path`` is repository-relative for a file inside the analysed shadow and
    absolute for anything else Understand read on the way -- the interpreter's own standard
    library above all -- which is what makes this comparable with ``EntityKey.path`` without
    knowing where either database was built. An absolute path simply never matches an entity,
    which is the right answer for a parse error in a file no commit owns.
    """
    return frozenset(error.path.as_posix() for error in snapshot.parse_errors)


def attach_before(findings: Iterable[Finding], before: ProjectSnapshot) -> list[Finding]:
    """Return ``findings`` with ``before`` filled in wherever the entity exists on both sides.

    Only threshold findings are touched, and only those that belong to an entity: a
    population finding (``entity=None``) has no before value, and the other evaluators fill
    their own. Nothing is modified in place: a finding that gains a before value comes back
    as a new object, and every other one is passed through unchanged.

    A file the before side could not parse is skipped here for the same reason the ratchet
    skips it, and the consequence is sharper: ``analysis.classify`` calls a violation
    **pre-existing** -- and therefore non-blocking -- when the before value already broke the
    limit, so an inflated before value excuses the very violation it invented. Task 11.11
    measured a 30-line routine at the parse site reporting ``CountStmt`` 66; that number would
    forgive any statement-count violation the change introduced. Leaving ``before`` unset says
    "not known", which blocks, instead of "was worse", which does not.
    """
    blind = unparsed_files(before)
    return [_with_before(finding, before, blind) for finding in findings]


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


@dataclass(frozen=True, slots=True)
class _PreChange:
    """The before side of the change, and the two things needed to read an entity out of it.

    Grouped rather than passed as three arguments, because a comparison needs all three
    together and this project caps a routine at five parameters. Built once per run: both
    members are derived from the whole of the two snapshots and neither depends on the
    threshold being compared.
    """

    snapshot: ProjectSnapshot
    blind: Collection[str]
    paired: Mapping[EntityKey, EntityKey]

    @classmethod
    def of(cls, after: ProjectSnapshot, before: ProjectSnapshot) -> Self:
        """The before side of a change into ``after``, ready to be asked about any key."""
        return cls(
            snapshot=before,
            blind=unparsed_files(before),
            paired=pair_changed_signatures(after, before),
        )

    def unreadable(self, key: EntityKey) -> bool:
        """Whether this side failed to parse ``key``'s file; see the module docstring."""
        return key.path in self.blind

    def record_of(self, key: EntityKey) -> EntityRecord | None:
        """``key``'s pre-change record: its own, or the one its signature change left behind.

        The exact key is asked for first, so a routine that did not change signature never
        depends on the pairing at all, and an unpaired key answers ``None`` as it always did.
        """
        record = self.snapshot.entities.get(key)
        if record is not None:
            return record
        was = self.paired.get(key)
        return None if was is None else self.snapshot.entities.get(was)


def _compare_all(
    threshold: EffectiveThreshold,
    after: ProjectSnapshot,
    before: _PreChange,
    keys: Sequence[EntityKey],
) -> Iterator[Finding]:
    """Compare one threshold's metric for every key of its scope."""
    for key in keys:
        if before.unreadable(key):
            continue  # the before side of this file did not parse; see the module docstring
        finding = _compare(threshold, after.entities.get(key), before.record_of(key))
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
    worse = _worse_bound(threshold.limit, was, now)
    if worse is None:
        return None
    if _a_decomposition_raised_it(threshold, before, after):
        return None
    return _ratchet_finding(threshold, after, was, now, worse)


def _a_decomposition_raised_it(
    threshold: EffectiveThreshold, before: EntityRecord, after: EntityRecord
) -> bool:
    """Whether this count went up because the entity itself measurably got simpler (11.9).

    Both halves are required. A rule outside :data:`COUNTS_DECOMPOSITION_RAISES` is never
    forgiven whatever else moved, and a count is forgiven only on the *same entity's* own
    evidence -- the improvement a decomposition produces elsewhere, on a routine that did not
    exist before, is not readable here and is not what this asks about.
    """
    return threshold.rule in COUNTS_DECOMPOSITION_RAISES and _got_simpler(
        before.metrics, after.metrics
    )


def _got_simpler(was: Mapping[str, float], now: Mapping[str, float]) -> bool:
    """Whether :data:`COMPLEXITY_EVIDENCE` improved and none of it worsened.

    "At least one fell" is what makes this evidence rather than an absence of it: a change
    that moved no complexity metric at all has shown nothing, and forgiving a count on that
    would exempt every addition. A metric absent from either side is not evidence in either
    direction, so it is passed over rather than read as unchanged.
    """
    improved = False
    for metric in COMPLEXITY_EVIDENCE:
        old, new = was.get(metric), now.get(metric)
        if old is None or new is None:
            continue
        if new > old:
            return False
        improved = improved or new < old
    return improved


@dataclass(frozen=True, slots=True)
class _Worse:
    """The bound a value moved towards, and the number that bound stands at.

    The two travel together because neither answers anything alone: the direction says which
    comparison to make and the limit is what to compare against, and carrying them as one
    value is what keeps :func:`_ratchet_finding` and :func:`_message` inside this project's
    five-parameter cap. Pairing them here is also the narrowing -- ``Limit`` holds two
    optional bounds, and :func:`_worse_bound` returns this only for a bound that exists, so
    ``limit`` is a number rather than ``float | None`` everywhere downstream.
    """

    bound: Bound
    limit: float


def _worse_bound(limit: Limit, was: float, now: float) -> _Worse | None:
    """The bound the value moved towards, or ``None`` when it did not get worse.

    A limit with both bounds is a maximum *and* a minimum, so either movement is worse.
    """
    if limit.max is not None and now > was:
        return _Worse("max", limit.max)
    if limit.min is not None and now < was:
        return _Worse("min", limit.min)
    return None


def _ratchet_finding(
    threshold: EffectiveThreshold, record: EntityRecord, was: float, now: float, worse: _Worse
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
        limit=worse.limit,
        limit_source=threshold.source,
        severity=threshold.spec.severity,
        blocking=threshold.spec.severity == "error",
        message=_message(subject, metric, was, now, worse),
    )


def within_limit(finding: Finding) -> bool:
    """Whether this ratchet finding's after value is still inside the limit it moved towards.

    The one predicate behind both halves of task 11.15, so the sentence the finding prints and
    the severity ``analysis.classify`` gives it cannot drift apart: this module asks it while
    wording the message, and ``classify`` asks it again about the finished finding when it
    applies ``settings.ratchet.below_limit_severity``.

    Everything it needs is on the finding itself, and the direction is read from the movement
    rather than from a bound the finding does not carry: a ratchet finding exists *because*
    the value moved the wrong way, and :func:`_worse_bound` answers ``max`` only when the
    value rose and ``min`` only when it fell. So ``value > before`` is the ``max`` case
    exactly, and ``Finding.limit`` is already the bound that movement broke.

    ``False`` for anything that is not a ratchet finding, and for one missing a number the
    comparison needs -- "not known to be inside its limit" is the answer that keeps a refusal
    rather than inventing one.
    """
    if finding.kind != "ratchet":
        return False
    now, was, limit = finding.value, finding.before, finding.limit
    if now is None or was is None or limit is None:
        return False
    return _inside(limit, now, "max" if now > was else "min")


def _inside(limit: float, value: float, bound: Bound) -> bool:
    """Whether ``value`` is still on the allowed side of the bound it moved towards.

    The comparison is the same one ``analysis.thresholds`` makes, boundary included: a value
    *equal* to its maximum is inside it, so a routine that grew to exactly the limit is the
    last growth this reports without refusing.
    """
    return value <= limit if bound == "max" else value >= limit


def _message(subject: str, metric: str, was: float, now: float, worse: _Worse) -> str:
    """One line stating what got worse and by how much (req 7.1).

    Two sentences, because two things are true and only one of them is a refusal. Past the
    limit the entity may not get worse than it was and the line says so; inside the limit it
    may, so the line reports the movement and names the bound that is still holding instead
    of claiming a rule the run is not going to enforce (task 11.15).
    """
    verb = "rose" if worse.bound == "max" else "fell"
    moved = f"{subject} {metric} {verb} from {_number(was)} to {_number(now)}"
    if not _inside(worse.limit, now, worse.bound):
        return f"{moved}; an affected entity may not get worse than it was"
    edge = "maximum" if worse.bound == "max" else "minimum"
    return f"{moved}, still within the {edge} {_number(worse.limit)}"


def _with_before(finding: Finding, before: ProjectSnapshot, blind: Collection[str]) -> Finding:
    """``finding`` with its pre-change value, or unchanged when there is none to give."""
    if finding.kind != "threshold" or finding.entity is None or finding.metric is None:
        return finding
    if finding.entity.key.path in blind:
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
