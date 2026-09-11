"""The net delta of a change: did it leave the project longer or shorter (req 7.1-7.5).

This is the one figure in the lean-code family that is not a finding, and it is the reason the
family is legible to an agent at all. ``net: -4 lloc`` is a fact that can be read before
anything is staged; a list of six rule names is a reading task. It is also the figure
ponytail's scoreboard cannot produce -- it scores a tree, not a change, so it has nothing to
subtract from and says so -- which is what makes it worth the arithmetic here.

**Statements lead, source lines follow.** :attr:`~scitools_hook.models.change.NetDelta.
statements` is Understand's ``CountStmt``: the logical lines, the figure formatting cannot
move. ``CountLineCode`` travels beside it because the two disagreeing is information -- a
change that removes statements while adding source lines has spread the same logic wider --
and neither one is a judgement on its own. Both names are read from ``config.models``,
where ``understand.snapshot`` reads them to request the two counts on every routine record
of every run (follow-up 14): a spelling of its own here would let the request and this
reader drift apart, and the delta would then sum zeros without a word.

**The population is the change's own files, on either side.** Every routine record whose path
is in ``affected.files | affected.deleted_files``, in the after snapshot *or* the before one.
A routine that exists only before contributes its whole size negatively and one that exists
only after contributes its whole size positively (req 7.2), because a missing side counts as
zero -- so a deleted file is a reduction rather than an absence, which is the case a delta
computed over the after side alone gets silently wrong. File-scope records are excluded: their
counts are the whole file, and summing them beside the routines they contain would count the
same lines twice.

**Absence and zero are different answers.** ``before is None`` is a whole-project run
(``--all``), which has nothing to subtract from, and the answer is ``None`` rather than ``0``
(req 7.4). A change that replaced exactly what it removed is a genuine ``0``, and the report
renders the two differently: one prints no net line at all, the other prints ``net: +0 lloc``.

**The pairing, and why it is this module's one import outside ``config`` and ``models``.**
:class:`~scitools_hook.models.snapshot.EntityKey` carries the parameter list, so a routine
whose signature changed is a *different* key on the two sides: unpaired, it reads as one
routine deleted at its full size and another added at its full size. The sums survive that --
``+12`` and ``-8`` total what ``12 - 8`` does -- but :attr:`~scitools_hook.models.change.
NetDelta.routines` does not, and that is the number the report prints beside the delta
("over N routines"). ``analysis.ratchet.pair_changed_signatures`` already joins those two
sides by :attr:`~scitools_hook.models.snapshot.EntityKey.family`, which is exactly this
problem solved once, so the design names it as this module's single sanctioned dependency
inside the analysis layer rather than have a second copy of the join drift away from it.

*How often it matters, measured on this repository.* Over the last 120 commits, 22 of them --
about one in five -- change at least one routine's parameter list, 65 routines in all
(counted textually over the ``def`` lines of the Python diffs, which is an approximation of
Understand's own pairing and not a substitute for it). Without the join, one commit in five
would overstate the routine count it reports.

**The maximum never blocks unless an operator asks it to** (req 7.5). ``max_net_growth`` is
unset by default and :func:`net_growth_finding` takes the configured severity, blocking only
at ``error`` -- the same contract every other rule in this family follows.
"""

from __future__ import annotations

from collections.abc import Mapping

from scitools_hook.analysis.ratchet import pair_changed_signatures
from scitools_hook.config.models import LINE_METRIC, STATEMENT_METRIC, Severity
from scitools_hook.models.change import AffectedSet, NetDelta
from scitools_hook.models.findings import Finding, structure_rule
from scitools_hook.models.snapshot import EntityKey, EntityRecord, ProjectSnapshot

_NET_GROWTH_RULE = structure_rule("net_growth")

_Pair = tuple[EntityRecord | None, EntityRecord | None]
"""One routine as the change left it and as it was, either side possibly absent."""


def net_delta(
    after: ProjectSnapshot, before: ProjectSnapshot | None, affected: AffectedSet
) -> NetDelta | None:
    """The change's net movement in statements and source lines, or ``None`` (req 7.1-7.4).

    ``None`` means "no before side to subtract from" -- a whole-project run -- and is not the
    same answer as a delta of zero (req 7.4). Otherwise every routine of the change's affected
    and deleted files is counted on both sides, a missing side as zero (req 7.2).
    """
    if before is None:
        return None
    paths = set(affected.files) | set(affected.deleted_files)
    pairs = _paired(
        _routines(after, paths), _routines(before, paths), pair_changed_signatures(after, before)
    )
    return NetDelta(
        statements=sum(_movement(now, was, STATEMENT_METRIC) for now, was in pairs),
        lines=sum(_movement(now, was, LINE_METRIC) for now, was in pairs),
        routines=len(pairs),
    )


def net_growth_finding(delta: NetDelta, limit: int, severity: Severity) -> Finding | None:
    """One project-scope finding when the change added more statements than ``limit`` (7.5).

    Strictly more: a change landing exactly on the configured maximum is inside it. A limit of
    ``0`` is legal and means "this change may not make the project longer".
    """
    if delta.statements <= limit:
        return None
    return Finding(
        kind="structural",
        rule=_NET_GROWTH_RULE,
        scope="project",
        path="",
        value=float(delta.statements),
        limit=float(limit),
        limit_source="config",
        severity=severity,
        blocking=severity == "error",
        message=(
            f"this change adds a net {delta.statements} logical lines over {delta.routines} "
            f"routines, above the configured maximum of {limit}"
        ),
        # `lines` and `routines` are named apart from the figures a per-entity rule would
        # publish under those words. `details` is one flat namespace across the whole
        # `structure.` category (see `lean.layering` for the convention and what it cost to
        # learn), and a rule reporting line *numbers* -- the duplicate-block rule, which
        # names a range -- has the better claim on a bare `lines`. This one carries the
        # delta's other two figures, so they travel with the scalar in `value`.
        details={"net_lines": delta.lines, "net_routines": delta.routines},
    )


def _routines(snapshot: ProjectSnapshot, paths: set[str]) -> dict[EntityKey, EntityRecord]:
    """The routine records of the given paths on one side, by key."""
    records: dict[EntityKey, EntityRecord] = {}
    for key, record in snapshot.entities.items():
        if key.scope != "routine":
            continue
        if key.path not in paths:
            continue
        records[key] = record
    return records


def _paired(
    after_side: dict[EntityKey, EntityRecord],
    before_side: dict[EntityKey, EntityRecord],
    renames: Mapping[EntityKey, EntityKey],
) -> list[_Pair]:
    """Every routine of the population once, joined to its other side where it has one.

    A before record joined to an after one is not offered a second time: the leftover pass
    carries only what the change deleted.
    """
    joined: list[_Pair] = []
    matched: set[EntityKey] = set()
    for key, now in after_side.items():
        was = _counterpart(key, before_side, renames)
        if was is not None:
            matched.add(was.key)
        joined.append((now, was))
    for key, was in before_side.items():
        if key in matched:
            continue
        joined.append((None, was))
    return joined


def _counterpart(
    key: EntityKey,
    before_side: dict[EntityKey, EntityRecord],
    renames: Mapping[EntityKey, EntityKey],
) -> EntityRecord | None:
    """This routine as the before side held it: same key, or the key it was renamed from.

    The subscript on the last line is total, and deliberately not a ``.get`` with a default
    that would never be taken: a rename pair shares an :attr:`~scitools_hook.models.snapshot.
    EntityKey.family`, which carries the scope and the path, so a before key paired with an
    after key that passed both filters in :func:`_routines` has passed them too and is in
    ``before_side``. A ``.get`` here would read as a case worth looking for, and there is
    none; if this ever raises, the pairing helper's notion of a family has changed and the
    right answer is to find out how, not to fall back to counting the routine twice.
    """
    if key in before_side:
        return before_side[key]
    renamed = renames.get(key)
    if renamed is None:
        return None
    return before_side[renamed]


def _movement(now: EntityRecord | None, was: EntityRecord | None, metric: str) -> int:
    """One routine's contribution to the delta: after minus before, a missing side zero."""
    return _count(now, metric) - _count(was, metric)


def _count(record: EntityRecord | None, metric: str) -> int:
    """One side's count, zero for a routine that side does not have or did not measure."""
    if record is None:
        return 0
    return round(record.metrics.get(metric, 0.0))
