"""The review-at-scale view of one change: entity deltas, dependency deltas, rankings (req 9).

:func:`build_summary` is the last pure step of ``explain``. It joins the two snapshots by
:class:`~scitools_hook.models.snapshot.EntityKey`, the only identity that survives across two
Understand databases, and produces the document the renderers of task 5.3 turn into text,
Markdown and JSON. It computes nothing that needs a database: the impact sets (req 9.5) and
the exported graphs (req 9.4) come from the Understand worker and are carried through
untouched.

The decisions this module makes, all of them visible in ``tests/analysis/test_change_summary``:

* **What a delta covers (9.1).** Every file of ``affected.files`` *and of*
  ``affected.deleted_files`` becomes a group, empty groups included, so the summary is the
  change's full file inventory. A deleted file's entities are all ``removed``, which is
  exactly what requirement 9.1 asks the summary to list. An entity present on both sides
  whose metrics all stand still is **omitted**: the summary is what moved.
* **What a delta is.** ``before`` and ``after`` hold the full metric maps of the two sides;
  ``delta`` holds only the metrics that *moved*, as ``after - before`` with a side that does
  not report a metric counting as zero. So an added entity's delta is its own size, a removed
  entity's delta is negative, and ``status == "modified"`` exactly when ``delta`` is non-empty
  for an entity both sides know. A missing ``before`` snapshot (a repository with no ``HEAD``,
  or whole-project mode) is treated as an empty side: everything is then genuinely new.
* **How rankings compare different metrics (9.3).** Ranking is per **(entity, metric) pair**
  by the raw magnitude of the movement (``top_by_delta``) or of the current value
  (``top_by_value``), and every ranked entry is narrowed to the one metric it is ranked on --
  because a row is only readable if it shows the number that earned it its rank. Magnitude is
  what 9.3 orders by, so a removal outranks a smaller addition: deleting 22 lines is a bigger
  movement than adding 6, and a signed ordering would bury every removal below every addition.
  Note the cost: the same entity may take several slots, so at ``DEFAULT_TOP_N`` a change that
  moved one routine on four metrics crowds out three other entities. This does not normalise
  across metrics -- a cyclomatic delta of 6 and a line delta of 35 are still compared raw --
  and it is worth revisiting against configured limits once real change sizes are seen. The
  same entity may hold several rows; that is the intent -- a routine that moved on four
  metrics *is* four findings' worth of risk. Ties break on the entity key and then the metric
  name, so the ranking is stable run after run. A removed entity has no current value and
  therefore never appears in ``top_by_value``.
* **Architecture paths (9.2, 9.7).** ``ProjectSnapshot.arch_nodes`` carries the architecture
  trimmed to the configured depth, and membership is recorded per *file* -- routines and
  classes inherit their container file's path (see :mod:`scitools_hook.models.snapshot`), so
  the node of a file is the architecture path of every entity in it.
  :func:`architecture_index` builds that lookup once, the after side winning over the before
  side so that a file the change deleted still resolves. Every entity delta carries its
  container file's node in ``arch_path``, ranking rows included, which is what puts the
  architecture path of requirement 9.7 inside the document the renderers receive -- they see
  the :class:`~scitools_hook.models.change.ChangeSummary` and nothing else. Both ends of every
  dependency delta are annotated with the same lookup, ``crosses_arch`` is set when the two
  ends sit in *different known* nodes, and the list is ordered by source node, which is the
  "grouped by architecture node" of requirement 9.2. An entity or an end outside every node
  keeps ``None`` and is never marked as crossing: an unknown boundary is not a crossed one.
* **Parameters.** The design lists seven (``before, after, affected, impact, graphs, paths,
  top_n``); this project caps a routine at five. The three that only travel through the
  builder -- the impact sets, the graph files and the ranking width -- are therefore grouped
  into :class:`ReviewAids`, which also gives them defaults for the common ``explain`` run that
  asks for neither graphs nor impact.

Nothing here touches the filesystem: ``db_path`` and ``open_command`` (req 9.8) are built
from :class:`~scitools_hook.models.cache.CachePaths` as strings, the command naming the
``understand`` GUI executable that sits next to ``und`` in a SciTools installation.
"""

from __future__ import annotations

import shlex
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, Literal

from scitools_hook.models.cache import CachePaths
from scitools_hook.models.change import (
    AffectedSet,
    ChangeSummary,
    DependencyDelta,
    EntityDelta,
    GraphFile,
    ImpactSet,
)
from scitools_hook.models.snapshot import EntityKey, EntityRecord, ProjectSnapshot

DEFAULT_TOP_N: Final = 10
"""How many rows each ranking carries when the caller does not ask for a width (req 9.3)."""

GUI_EXECUTABLE: Final = "understand"
"""The Understand GUI executable, next to ``und`` in a SciTools installation (req 9.8)."""

Pair = tuple[str, str]
"""One dependency as ``(source file, target file)``."""

Status = Literal["added", "removed"]
"""What one side of the change did to an entity or a dependency."""


@dataclass(frozen=True, slots=True)
class ReviewAids:
    """What ``explain`` attaches to the summary, plus how wide the rankings are.

    The impact sets (req 9.5) and graph files (req 9.4) are produced by the Understand
    worker; the builder carries them into the document and never computes them.
    """

    impact: Mapping[EntityKey, ImpactSet] = field(default_factory=dict)
    graphs: Sequence[GraphFile] = ()
    top_n: int = DEFAULT_TOP_N


def build_summary(
    before: ProjectSnapshot | None,
    after: ProjectSnapshot,
    affected: AffectedSet,
    paths: CachePaths,
    aids: ReviewAids | None = None,
) -> ChangeSummary:
    """The change summary of one change (req 9.1-9.3, 9.5, 9.7, 9.8).

    ``before`` is ``None`` when there is no pre-change side to compare against, in which case
    every affected entity and every dependency is reported as added.
    """
    attached = ReviewAids() if aids is None else aids
    touched = sorted(affected.files | affected.deleted_files)
    nodes = architecture_index(after, before)
    files = {path: _file_deltas(path, before, after, nodes.get(path)) for path in touched}
    deltas = [delta for group in files.values() for delta in group]
    by_delta, by_value = _rankings(deltas, attached.top_n)
    return ChangeSummary(
        files=files,
        dependencies=_dependency_deltas(before, after, set(touched), nodes),
        top_by_delta=by_delta,
        top_by_value=by_value,
        impact=dict(attached.impact),
        graphs=sorted(
            attached.graphs, key=lambda graph: (graph.key.token, graph.graph, str(graph.path))
        ),
        db_path=str(paths.after_db),
        open_command=open_command(paths),
    )


def architecture_index(
    after: ProjectSnapshot, before: ProjectSnapshot | None = None
) -> dict[str, str]:
    """File path -> its architecture node path, the path a reviewer locates it by (req 9.7).

    The after side wins, so a file that moved between nodes is reported where it is now; the
    before side fills in the files the change deleted. A file in several nodes takes the
    first in sorted order, and a file in none is absent from the mapping.
    """
    index = _index_of(after)
    if before is not None:
        for member, node in _index_of(before).items():
            index.setdefault(member, node)
    return index


def open_command(paths: CachePaths) -> str:
    """The command that opens this repository's database in the Understand GUI (req 9.8)."""
    return shlex.join([GUI_EXECUTABLE, str(paths.after_db)])


def _index_of(snapshot: ProjectSnapshot) -> dict[str, str]:
    """One snapshot's file -> architecture node mapping; the first node in sorted order wins."""
    index: dict[str, str] = {}
    for node in sorted(snapshot.arch_nodes, key=lambda node: node.path):
        for member in node.members:
            index.setdefault(member, node.path)
    return index


# --- entity deltas (req 9.1) ----------------------------------------------------


def _file_deltas(
    path: str,
    before: ProjectSnapshot | None,
    after: ProjectSnapshot,
    arch_path: str | None,
) -> list[EntityDelta]:
    """What the change did to the entities of one file, in a stable order; unchanged omitted.

    ``arch_path`` is the file's architecture node, which every entity it defines inherits
    (req 9.7); it is ``None`` for a file no architecture contains.
    """
    was, now = _records_in(before, path), _records_in(after, path)
    keys = sorted(set(was) | set(now), key=lambda key: key.token)
    candidates = (_delta(was.get(key), now.get(key), arch_path) for key in keys)
    return [delta for delta in candidates if delta is not None]


def _records_in(snapshot: ProjectSnapshot | None, path: str) -> dict[EntityKey, EntityRecord]:
    """The entities one snapshot defines in ``path``; empty when there is no such snapshot."""
    if snapshot is None:
        return {}
    return {key: record for key, record in snapshot.entities.items() if key.path == path}


def _delta(
    before: EntityRecord | None, after: EntityRecord | None, arch_path: str | None
) -> EntityDelta | None:
    """One entity's movement, or ``None`` when both sides report it unchanged."""
    if after is None:
        return None if before is None else _one_sided(before, "removed", arch_path)
    if before is None:
        return _one_sided(after, "added", arch_path)
    movement = _movement(before.metrics, after.metrics)
    if not movement:
        return None
    return EntityDelta(
        ref=after.ref,
        status="modified",
        before=dict(before.metrics),
        after=dict(after.metrics),
        delta=movement,
        arch_path=arch_path,
    )


def _one_sided(record: EntityRecord, status: Status, arch_path: str | None) -> EntityDelta:
    """An entity only one side has: its own metrics are the whole movement."""
    before = {} if status == "added" else dict(record.metrics)
    after = dict(record.metrics) if status == "added" else {}
    return EntityDelta(
        ref=record.ref,
        status=status,
        before=before,
        after=after,
        delta=_movement(before, after),
        arch_path=arch_path,
    )


def _movement(before: Mapping[str, float], after: Mapping[str, float]) -> dict[str, float]:
    """Every metric that moved, as ``after - before``; a side missing a metric counts as zero."""
    moved: dict[str, float] = {}
    for metric in sorted(set(before) | set(after)):
        change = after.get(metric, 0.0) - before.get(metric, 0.0)
        if change:
            moved[metric] = change
    return moved


# --- dependency deltas (req 9.2, 9.7) -------------------------------------------


def _dependency_deltas(
    before: ProjectSnapshot | None,
    after: ProjectSnapshot,
    touched: set[str],
    nodes: Mapping[str, str],
) -> list[DependencyDelta]:
    """Every dependency the change added or removed at an affected file, by node (req 9.2)."""
    was, now = _pairs(before), _pairs(after)
    deltas = _deltas_for(now - was, "added", touched, nodes)
    deltas += _deltas_for(was - now, "removed", touched, nodes)
    return sorted(deltas, key=lambda delta: (delta.src_node or "", delta.src, delta.dst))


def _pairs(snapshot: ProjectSnapshot | None) -> set[Pair]:
    """The file dependencies one snapshot holds; empty when there is no such snapshot."""
    if snapshot is None:
        return set()
    return {(edge.src, edge.dst) for edge in snapshot.file_edges}


def _deltas_for(
    pairs: Iterable[Pair], status: Status, touched: set[str], nodes: Mapping[str, str]
) -> list[DependencyDelta]:
    """The dependencies of ``pairs`` with at least one end among the affected files."""
    return [
        _dependency(pair, status, nodes)
        for pair in sorted(pairs)
        if pair[0] in touched or pair[1] in touched
    ]


def _dependency(pair: Pair, status: Status, nodes: Mapping[str, str]) -> DependencyDelta:
    """One dependency delta with the architecture nodes of its two ends (req 9.2, 9.7)."""
    src, dst = pair
    src_node, dst_node = nodes.get(src), nodes.get(dst)
    return DependencyDelta(
        src=src,
        dst=dst,
        status=status,
        src_node=src_node,
        dst_node=dst_node,
        crosses_arch=_crosses(src_node, dst_node),
    )


def _crosses(src_node: str | None, dst_node: str | None) -> bool:
    """Whether the two ends sit in different architecture nodes, both of them known (req 9.2)."""
    if src_node is None or dst_node is None:
        return False
    return src_node != dst_node


# --- rankings (req 9.3) ---------------------------------------------------------


def _rankings(
    deltas: Sequence[EntityDelta], top_n: int
) -> tuple[list[EntityDelta], list[EntityDelta]]:
    """The two rankings: largest movements, and largest values the change leaves behind."""
    by_delta = [
        (delta, metric, abs(value)) for delta in deltas for metric, value in delta.delta.items()
    ]
    by_value = [
        (delta, metric, abs(value)) for delta in deltas for metric, value in delta.after.items()
    ]
    return _top(by_delta, top_n), _top(by_value, top_n)


def _top(candidates: Sequence[tuple[EntityDelta, str, float]], top_n: int) -> list[EntityDelta]:
    """The ``top_n`` largest (entity, metric) pairs, each narrowed to the metric it ranks on."""
    ordered = sorted(candidates, key=lambda row: (-row[2], row[0].ref.key.token, row[1]))
    return [_narrow(delta, metric) for delta, metric, _ in ordered[: max(top_n, 0)]]


def _narrow(delta: EntityDelta, metric: str) -> EntityDelta:
    """One ranking row: the same entity and status, restricted to one metric (req 9.3)."""
    return EntityDelta(
        ref=delta.ref,
        status=delta.status,
        before=_only(delta.before, metric),
        after=_only(delta.after, metric),
        delta=_only(delta.delta, metric),
        arch_path=delta.arch_path,
    )


def _only(metrics: Mapping[str, float], metric: str) -> dict[str, float]:
    """``metrics`` restricted to one entry, empty when that side does not report it."""
    return {metric: metrics[metric]} if metric in metrics else {}
