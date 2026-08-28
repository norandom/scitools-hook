"""Directed dependency graph with reference counts, and Tarjan's SCC search (req 6.1, 6.2).

Both cycle levels — files and architecture nodes — are the same problem on the same shape:
a directed graph whose nodes are names and whose edges carry how many references they stand
for. :class:`DependencyGraph` is therefore generic over node names and is built from a list
of :class:`~scitools_hook.models.snapshot.DepEdge` values, whichever edge list of a snapshot
they came from. Understand reports one edge per pair per reference kind, so edges between
the same pair are merged and their reference counts added.

:func:`strongly_connected_components` is Tarjan's algorithm written with an explicit work
stack instead of recursion: the dependency graph of a real repository can be far deeper
than CPython's recursion limit, and a gate must not fail on a large project. Adjacency lists and
components come back in sorted order so that findings are deterministic run after run.

:meth:`DependencyGraph.internal_edges` gives a component's own edges — for a cycle, exactly
the references that close it (req 6.1, 6.2).
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from typing import NamedTuple

from scitools_hook.models.snapshot import DepEdge


class GraphEdge(NamedTuple):
    """One merged edge: the pair it connects and how many references it stands for."""

    src: str
    dst: str
    refs: int


@dataclass(frozen=True, slots=True)
class DependencyGraph:
    """A directed graph of node names with a reference count per edge.

    Build it with :meth:`from_edges`; both mappings are then sorted, which is what makes
    traversal and every list derived from it deterministic.
    """

    successors: Mapping[str, tuple[str, ...]]
    """Node -> its distinct targets, sorted; every node of the graph is a key."""

    refs: Mapping[tuple[str, str], int]
    """``(src, dst)`` -> the number of references from ``src`` to ``dst``."""

    @classmethod
    def from_edges(cls, edges: Iterable[DepEdge]) -> DependencyGraph:
        """Build the graph these dependency edges form.

        Only ``src``, ``dst`` and ``refs`` matter here: ``crosses_arch`` is a property of
        the edge, not of the graph. Repeated pairs are merged and their reference counts
        added, and a node that only ever appears as a target is a node all the same.
        """
        counts: dict[tuple[str, str], int] = {}
        targets: dict[str, set[str]] = {}
        for edge in edges:
            pair = (edge.src, edge.dst)
            counts[pair] = counts.get(pair, 0) + edge.refs
            targets.setdefault(edge.src, set()).add(edge.dst)
            targets.setdefault(edge.dst, set())
        return cls(
            successors={node: tuple(sorted(reached)) for node, reached in sorted(targets.items())},
            refs=dict(sorted(counts.items())),
        )

    @property
    def nodes(self) -> tuple[str, ...]:
        """Every node of the graph, sorted."""
        return tuple(self.successors)

    def successors_of(self, node: str) -> tuple[str, ...]:
        """The nodes ``node`` depends on, sorted; empty for a node with no outgoing edge."""
        return self.successors.get(node, ())

    def refs_between(self, src: str, dst: str) -> int:
        """How many references go from ``src`` to ``dst``; zero when there is no edge."""
        return self.refs.get((src, dst), 0)

    def internal_edges(self, members: Collection[str]) -> list[GraphEdge]:
        """The edges with both ends inside ``members``, sorted; a cycle's closing references."""
        inside = set(members)
        return sorted(
            GraphEdge(src, dst, count)
            for (src, dst), count in self.refs.items()
            if src in inside and dst in inside
        )


def strongly_connected_components(graph: DependencyGraph) -> list[frozenset[str]]:
    """Tarjan's strongly connected components of ``graph``, sorted by their members.

    The components partition the nodes: a node on no cycle comes back as a component of
    one. The search keeps its own work stack, so a graph deeper than the interpreter's
    recursion limit is decomposed like any other.
    """
    state = _Search(graph)
    for node in graph.nodes:
        if node not in state.index:
            _walk(state, node)
    return sorted(state.components, key=lambda component: sorted(component))


@dataclass(slots=True)
class _Search:
    """Tarjan's bookkeeping: discovery indices, low-links and the component stack."""

    graph: DependencyGraph
    index: dict[str, int] = field(default_factory=dict)
    low: dict[str, int] = field(default_factory=dict)
    stack: list[str] = field(default_factory=list)
    on_stack: set[str] = field(default_factory=set)
    counter: int = 0
    components: list[frozenset[str]] = field(default_factory=list)

    def enter(self, node: str) -> None:
        """Give ``node`` its discovery index and push it on the component stack."""
        self.index[node] = self.low[node] = self.counter
        self.counter += 1
        self.stack.append(node)
        self.on_stack.add(node)

    def close(self, node: str) -> None:
        """Pop the component ``node`` roots, when it roots one."""
        if self.low[node] != self.index[node]:
            return
        found: list[str] = []
        while True:
            member = self.stack.pop()
            self.on_stack.discard(member)
            found.append(member)
            if member == node:
                break
        self.components.append(frozenset(found))

    def lower(self, node: str, value: int) -> None:
        """Lower ``node``'s low-link to ``value`` when that is the smaller reachable index."""
        self.low[node] = min(self.low[node], value)


def _walk(state: _Search, root: str) -> None:
    """Visit everything reachable from ``root`` that has not been visited yet."""
    state.enter(root)
    work: list[tuple[str, int]] = [(root, 0)]
    while work:
        node, visited = work[-1]
        successors = state.graph.successors_of(node)
        if visited < len(successors):
            work[-1] = (node, visited + 1)
            _step(state, work, node, successors[visited])
            continue
        work.pop()
        state.close(node)
        if work:
            state.lower(work[-1][0], state.low[node])


def _step(state: _Search, work: list[tuple[str, int]], node: str, child: str) -> None:
    """Take one edge: descend into an unvisited child, or link back to one still on the stack."""
    if child not in state.index:
        state.enter(child)
        work.append((child, 0))
    elif child in state.on_stack:
        state.lower(node, state.index[child])
