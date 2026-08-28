"""Fan-in and fan-out limits for files and classes, plus the fan-out ratchet (req 6.4).

Fan-in is how many distinct entities depend on an entity, fan-out how many it depends on.
Both are counted on the after snapshot: file fan over ``file_edges``, whose endpoints are
repo-relative paths, and class fan over ``class_edges``, whose endpoints are
:attr:`~scitools_hook.models.snapshot.EntityKey.token` strings — the reversible JSON form of
a key, which is what the extractor writes for class edges and what the fixtures hold. An
endpoint is therefore looked up by ``key.token`` and *shown* by the qualified name the token
decodes to, so neither a message nor a details list ever carries a raw token.

Only the entities the change affected are evaluated: ``keys_files`` and ``keys_classes`` are
the affected set (req 4.2), so a file that was already over its limit and that this change
did not touch stays out of the report. A self-dependency does not count towards either
direction, for the same reason a self-loop is not a cycle: Understand emits it for a file
that references its own contents, and it says nothing about coupling to anything else.

Beside the absolute limits, requirement 6.4 asks for a ratchet: an affected entity whose
**fan-out grew** is reported even when it stays under the limit, because a change that makes
an entity depend on more than it did makes the code worse. Fan-in is not ratcheted -- being
used more is not a regression. An entity that grew *and* broke its limit yields both
findings, the absolute one and the ratchet one, exactly as thresholds and ratchets pair up
elsewhere. Whole-project mode passes ``before = None`` and gets absolute findings only
(req 4.8).

A direction with no configured limit is switched off entirely, ratchet included: ``fan`` is
the configuration of this rule, and ``analysis.ratchet`` likewise only ever ratchets a
metric that is configured. Fan limits are maxima; a ``min`` bound is meaningless for fan and
is not evaluated. Findings carry the default fan severity (``structure.fan_severity``,
``warning``); an operator's override reaches them through the severity map that
``analysis.classify`` applies (req 3.7), as it does for every rule. ``hint`` is left for the
pipeline, and ``limit_source`` is ``"rule"``.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Final, Literal

from scitools_hook.analysis.structure.graph import DependencyGraph
from scitools_hook.config.models import FanKey, Limit, Severity
from scitools_hook.models.findings import Finding, structure_rule
from scitools_hook.models.snapshot import DepEdge, EntityKey, EntityRef, ProjectSnapshot

Direction = Literal["fan_in", "fan_out"]
"""Which way the dependencies of a fan count."""

FanScope = Literal["file", "class"]
"""The two scopes that have a fan (req 6.4)."""

_DIRECTIONS: Final[tuple[Direction, ...]] = ("fan_in", "fan_out")
_KEYS: Final[dict[tuple[FanScope, Direction], FanKey]] = {
    ("file", "fan_in"): "file_fan_in",
    ("file", "fan_out"): "file_fan_out",
    ("class", "fan_in"): "class_fan_in",
    ("class", "fan_out"): "class_fan_out",
}
_DETAIL: Final[dict[Direction, str]] = {
    "fan_in": "depended_on_by",
    "fan_out": "depends_on",
}
_NOUN: Final[dict[FanScope, str]] = {"file": "files", "class": "classes"}
_SEVERITY: Final[Severity] = "warning"
"""Default of ``structure.fan_severity``; an override is applied by ``analysis.classify``."""


@dataclass(frozen=True, slots=True)
class _Subject:
    """One entity a fan is evaluated for, with everything a finding needs to name it."""

    node: str
    """The entity's endpoint in the dependency graph: a file path or an entity key token."""

    scope: FanScope
    path: str
    name: str
    ref: EntityRef | None


@dataclass(frozen=True, slots=True)
class _Fan:
    """Both neighbourhoods of one edge list, by graph endpoint, labelled and sorted."""

    depends_on: Mapping[str, tuple[str, ...]]
    depended_on_by: Mapping[str, tuple[str, ...]]

    def neighbours(self, node: str, direction: Direction) -> tuple[str, ...]:
        """The entities on one side of ``node``; empty for a node with no such edge."""
        table = self.depends_on if direction == "fan_out" else self.depended_on_by
        return table.get(node, ())

    def count(self, node: str, direction: Direction) -> int:
        """The fan of ``node`` in one direction: how many distinct entities it counts."""
        return len(self.neighbours(node, direction))


def evaluate_fan(
    after: ProjectSnapshot,
    before: ProjectSnapshot | None,
    keys_files: Collection[str],
    keys_classes: Collection[EntityKey],
    fan: Mapping[FanKey, Limit],
) -> list[Finding]:
    """Check the fan limits of the affected files and classes, and the fan-out ratchet (6.4).

    ``keys_files`` holds repo-relative paths and ``keys_classes`` entity keys of the affected
    set; ``fan`` is ``structure.fan``. ``before = None`` is whole-project mode, which has no
    ratchet (req 4.8). Findings come back files first, then classes, each in path order.
    """
    files = _side(
        _file_subjects(after, keys_files),
        after.file_edges,
        None if before is None else before.file_edges,
        fan,
    )
    classes = _side(
        _class_subjects(after, keys_classes),
        after.class_edges,
        None if before is None else before.class_edges,
        fan,
    )
    return [*files, *classes]


def _side(
    subjects: Iterable[_Subject],
    after_edges: Iterable[DepEdge],
    before_edges: Iterable[DepEdge] | None,
    fan: Mapping[FanKey, Limit],
) -> list[Finding]:
    """Every finding of one scope, built from that scope's two edge lists."""
    now = _fan_of(after_edges)
    was = None if before_edges is None else _fan_of(before_edges)
    findings: list[Finding] = []
    for subject in subjects:
        findings.extend(_subject_findings(subject, now, was, fan))
    return findings


def _subject_findings(
    subject: _Subject, now: _Fan, was: _Fan | None, fan: Mapping[FanKey, Limit]
) -> Iterator[Finding]:
    """One entity's findings: the broken absolute limits, then the fan-out ratchet."""
    for direction in _DIRECTIONS:
        limit = _limit_of(fan, subject.scope, direction)
        if limit is not None and now.count(subject.node, direction) > limit:
            yield _threshold_finding(subject, now, direction, limit)
    ratchet = _ratchet_finding(subject, now, was, fan)
    if ratchet is not None:
        yield ratchet


def _threshold_finding(subject: _Subject, now: _Fan, direction: Direction, limit: float) -> Finding:
    """One entity over an absolute fan limit (req 6.4); ``hint`` is attached by the pipeline."""
    neighbours = list(now.neighbours(subject.node, direction))
    return Finding(
        kind="structural",
        rule=structure_rule(direction),
        scope=subject.scope,
        entity=subject.ref,
        path=subject.path,
        line=None if subject.ref is None else subject.ref.line,
        value=len(neighbours),
        limit=limit,
        limit_source="rule",
        severity=_SEVERITY,
        blocking=_SEVERITY == "error",
        message=_threshold_message(subject, direction, len(neighbours), limit),
        details={_DETAIL[direction]: neighbours},
    )


def _ratchet_finding(
    subject: _Subject, now: _Fan, was: _Fan | None, fan: Mapping[FanKey, Limit]
) -> Finding | None:
    """An affected entity whose fan-out grew, even inside the limit (req 6.4)."""
    limit = _limit_of(fan, subject.scope, "fan_out")
    if was is None or limit is None:
        return None
    before_count = was.count(subject.node, "fan_out")
    neighbours = list(now.neighbours(subject.node, "fan_out"))
    if len(neighbours) <= before_count:
        return None
    return Finding(
        kind="ratchet",
        rule=structure_rule("fan_out"),
        scope=subject.scope,
        entity=subject.ref,
        path=subject.path,
        line=None if subject.ref is None else subject.ref.line,
        value=len(neighbours),
        before=before_count,
        limit=limit,
        limit_source="rule",
        severity=_SEVERITY,
        blocking=_SEVERITY == "error",
        message=_ratchet_message(subject, before_count, len(neighbours)),
        details={_DETAIL["fan_out"]: neighbours},
    )


def _limit_of(fan: Mapping[FanKey, Limit], scope: FanScope, direction: Direction) -> float | None:
    """The configured maximum for one scope and direction; ``None`` switches the rule off."""
    limit = fan.get(_KEYS[scope, direction])
    return None if limit is None else limit.max


def _fan_of(edges: Iterable[DepEdge]) -> _Fan:
    """Both neighbourhoods of an edge list, self-references excluded, labelled and sorted."""
    graph = DependencyGraph.from_edges(edges)
    outbound: dict[str, tuple[str, ...]] = {}
    inbound: dict[str, list[str]] = {node: [] for node in graph.nodes}
    for node in graph.nodes:
        targets = [target for target in graph.successors_of(node) if target != node]
        outbound[node] = _labelled(targets)
        for target in targets:
            inbound[target].append(node)
    return _Fan(outbound, {node: _labelled(sources) for node, sources in inbound.items()})


def _labelled(endpoints: Iterable[str]) -> tuple[str, ...]:
    """The display names of graph endpoints, sorted, as a finding lists them."""
    return tuple(sorted(_label(endpoint) for endpoint in endpoints))


def _label(endpoint: str) -> str:
    """A graph endpoint as it is shown: a class's qualified name, or the file path itself."""
    try:
        return EntityKey.from_token(endpoint).longname
    except ValueError:
        return endpoint


def _file_subjects(after: ProjectSnapshot, keys: Collection[str]) -> list[_Subject]:
    """The affected files, in path order, carrying the entity the snapshot holds for each."""
    refs = {
        record.key.path: record.ref
        for record in after.entities.values()
        if record.key.scope == "file"
    }
    return [_Subject(path, "file", path, path, refs.get(path)) for path in sorted(keys)]


def _class_subjects(after: ProjectSnapshot, keys: Collection[EntityKey]) -> list[_Subject]:
    """The affected classes, in key order, looked up in the graph by their key token."""
    return [
        _Subject(key.token, "class", key.path, key.longname, _ref_of(after, key))
        for key in sorted(keys, key=lambda key: key.token)
    ]


def _ref_of(after: ProjectSnapshot, key: EntityKey) -> EntityRef | None:
    """The entity reference of ``key``, or ``None`` when the snapshot does not hold it."""
    record = after.entities.get(key)
    return None if record is None else record.ref


def _threshold_message(subject: _Subject, direction: Direction, count: int, limit: float) -> str:
    """One line stating the fan, its direction and the limit it broke (req 7.1)."""
    subject_text = f"{subject.scope} {subject.name}"
    noun = _NOUN[subject.scope]
    if direction == "fan_out":
        return f"{subject_text} depends on {count} {noun}, above the fan-out maximum of {limit:g}"
    return f"{subject_text} is depended on by {count} {noun}, above the fan-in maximum of {limit:g}"


def _ratchet_message(subject: _Subject, was: int, now: int) -> str:
    """One line stating that an affected entity now depends on more than it did (req 6.4)."""
    return (
        f"{subject.scope} {subject.name} fan-out rose from {was} to {now} "
        f"{_NOUN[subject.scope]}; an affected entity may not depend on more than it did"
    )
