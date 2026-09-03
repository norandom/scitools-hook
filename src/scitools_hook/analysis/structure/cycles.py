"""New dependency cycles between files and between architecture nodes (req 6.1, 6.2).

A cycle is a strongly connected component of two or more nodes in the after-side dependency
graph. It is **new** when its member set is not contained in any component of the before
side: a cycle both sides have is not this change's doing, and a cycle that grew a member is
reported, because ``{a, b, c}`` is no subset of ``{a, b}`` — the change made it worse.

A node that depends on itself is deliberately not a cycle. Understand reports such an edge
for a file that references its own contents, so a self-dependency says nothing about the
structure a reviewer cares about; only components of two or more nodes are reported.

**A dependency that does not run when the module is loaded cannot close an import cycle**, and
at the file level the graph is reduced to the ones that do (:func:`at_import_time`). Measured
on a 770-file Python project: of the two file cycles reported over every reference, one -- nine
files across two packages -- was closed entirely by four imports inside ``if TYPE_CHECKING:``
and two inside a function body, the latter carrying a comment saying they were written that way
to keep the module importable. That is a cycle-breaking mitigation being reported as the cycle
it breaks, the same shape as task 11.9's ratchet blocking the extraction its own hint
recommends. The other cycle -- eighteen files, closed by ordinary module-level imports -- is
unaffected by the reduction, which is what tells the two apart. Coupling, fan, layers and the
new-dependency limit still count every reference: a deferred import is real coupling, it simply
cannot deadlock a module load.

Both levels share this module: ``level="file"`` reads a snapshot's ``file_edges`` and emits
``structure.file_cycle`` findings, ``level="arch"`` reads its ``arch_edges`` and emits
``structure.arch_cycle`` findings naming the architecture nodes. Every finding carries
``details = {"members": [...], "closing_refs": [...]}``: every member of the cycle, sorted,
and the edges inside it rendered as ``"a -> b (2 refs)"``, which is what requirements 6.1
and 6.2 mean by "the references that close it". ``path`` is the first member in sorted
order, so that the human renderer, which groups by path, has a deterministic home for the
finding; at the architecture level that is a node path rather than a file.

Whole-project mode passes ``before_edges=None``: there is no before side, so every cycle is
reported as an absolute inventory, and none of them is called new (req 4.8). As in the
other evaluators, ``hint`` is left for the pipeline to attach and ``blocking`` follows the
configured severity (req 3.7).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Literal, overload

from scitools_hook.analysis.structure.graph import (
    DependencyGraph,
    GraphEdge,
    strongly_connected_components,
)
from scitools_hook.config.models import Severity
from scitools_hook.models.findings import Finding, structure_rule
from scitools_hook.models.snapshot import DepEdge

Level = Literal["file", "arch"]
"""Which dependency graph is being checked; also the scope the findings carry."""

_RULE: dict[Level, str] = {
    "file": structure_rule("file_cycle"),
    "arch": structure_rule("arch_cycle"),
}
_NOUN: dict[Level, str] = {"file": "files", "arch": "architecture nodes"}


def find_new_cycles(
    before_edges: Sequence[DepEdge] | None,
    after_edges: Sequence[DepEdge],
    severity: Severity,
    level: Level,
) -> list[Finding]:
    """Report every cycle in ``after_edges`` that ``before_edges`` does not already contain.

    ``before_edges`` is ``None`` in whole-project mode, where every cycle is reported
    (req 4.8). At the file level both sides are first reduced to the dependencies that
    actually run when the source is imported (:func:`at_import_time`). Findings come back
    sorted by their members, one per cycle.
    """
    closing = _closing(after_edges, level)
    after = DependencyGraph.from_edges(closing)
    known = _cycles_of(_closing(before_edges, level))
    return [
        _finding(after, cycle, severity, level, compared=before_edges is not None)
        for cycle in _cycles_of(closing)
        if not any(cycle <= before for before in known)
    ]


def at_import_time(edges: Iterable[DepEdge]) -> list[DepEdge]:
    """The edges that can actually close an import cycle: everything but the deferred ones.

    An edge whose :attr:`~scitools_hook.models.snapshot.DepEdge.import_time` is ``None`` was
    never measured -- every language but Python, and any Python file that would not parse --
    and is kept, because "not measured" must read as the older, louder behaviour and never as
    "nothing runs". An edge measured at **zero** is dropped: every reference it stands for sits
    inside an ``if TYPE_CHECKING:`` block or a function body, so importing the source does not
    import the target and the two files cannot deadlock each other's loading.
    """
    return [edge for edge in edges if edge.import_time is None or edge.import_time > 0]


@overload
def _closing(edges: Sequence[DepEdge], level: Level) -> Sequence[DepEdge]: ...


@overload
def _closing(edges: None, level: Level) -> None: ...


def _closing(edges: Sequence[DepEdge] | None, level: Level) -> Sequence[DepEdge] | None:
    """The edges that may close a cycle at this level; only files defer a dependency.

    Architecture edges come from ``Arch.depends()`` and carry no import-time measurement, so
    they are passed through untouched -- an architecture cycle is still reported exactly as it
    was, which is what keeps a real one firing while this rule removes a false one below it.
    """
    if edges is None or level != "file":
        return edges
    return at_import_time(edges)


def _cycles_of(edges: Sequence[DepEdge] | None) -> list[frozenset[str]]:
    """The cyclic components of ``edges``: the strongly connected ones of two or more nodes."""
    if edges is None:
        return []
    components = strongly_connected_components(DependencyGraph.from_edges(edges))
    return [component for component in components if len(component) >= 2]


def _finding(
    graph: DependencyGraph,
    cycle: frozenset[str],
    severity: Severity,
    level: Level,
    compared: bool,
) -> Finding:
    """One cycle as a finding (req 6.1, 6.2); ``hint`` is attached by the pipeline."""
    members = sorted(cycle)
    closing = [_render(edge) for edge in graph.internal_edges(cycle)]
    return Finding(
        kind="structural",
        rule=_RULE[level],
        scope=level,
        path=members[0],
        limit_source="rule",
        severity=severity,
        blocking=severity == "error",
        message=_message(members, closing, level, compared),
        details={"members": members, "closing_refs": closing},
    )


def _render(edge: GraphEdge) -> str:
    """One closing reference, in the form the finding shows: ``"a -> b (2 refs)"``."""
    plural = "ref" if edge.refs == 1 else "refs"
    return f"{edge.src} -> {edge.dst} ({edge.refs} {plural})"


def _message(members: Sequence[str], closing: Sequence[str], level: Level, compared: bool) -> str:
    """One line naming the cycle's members and the references that close it (req 7.1)."""
    novelty = " that did not exist before the change" if compared else ""
    return (
        f"{len(members)} {_NOUN[level]} form a dependency cycle{novelty}: "
        f"{', '.join(members)}; closed by {', '.join(closing)}"
    )
