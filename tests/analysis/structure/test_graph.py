"""Directed dependency graph and Tarjan SCC (task 4.3; req 6.1, 6.2).

The graph is the shared substrate of the cycle rules: it is built from
:class:`~scitools_hook.models.snapshot.DepEdge` values, it keeps the reference count of
every pair, and it answers which strongly connected components the edges form. Both cycle
levels use it, so every case here works on plain node names rather than on real paths.
"""

from __future__ import annotations

from scitools_hook.analysis.structure.graph import (
    DependencyGraph,
    GraphEdge,
    strongly_connected_components,
)
from scitools_hook.models.snapshot import DepEdge


def edge(src: str, dst: str, refs: int = 1) -> DepEdge:
    """One dependency edge; ``crosses_arch`` plays no part in the graph."""
    return DepEdge(src=src, dst=dst, refs=refs)


def graph(*edges: DepEdge) -> DependencyGraph:
    """The graph these edges form."""
    return DependencyGraph.from_edges(edges)


def members(components: list[frozenset[str]]) -> list[list[str]]:
    """The components as sorted member lists, in the order they were returned."""
    return [sorted(component) for component in components]


def chain(count: int) -> list[DepEdge]:
    """A straight chain ``n0 -> n1 -> ... -> n<count-1>``: long, and free of cycles."""
    names = [f"n{index:06d}" for index in range(count)]
    return [edge(src, dst) for src, dst in zip(names, names[1:], strict=False)]


def test_two_edges_between_the_same_pair_add_their_reference_counts() -> None:
    """Understand reports one edge per reference kind; the graph counts them together."""
    built = graph(edge("a", "b", refs=2), edge("a", "b", refs=3), edge("a", "c", refs=1))

    assert built.refs_between("a", "b") == 5
    assert built.refs_between("a", "c") == 1


def test_a_pair_with_no_edge_has_no_references() -> None:
    """``refs_between`` answers zero rather than raising for an absent edge."""
    assert graph(edge("a", "b")).refs_between("b", "a") == 0


def test_a_node_that_is_only_a_target_is_still_a_node() -> None:
    """A leaf must be in the graph, or it could never join a component."""
    built = graph(edge("a", "b"))

    assert built.nodes == ("a", "b")
    assert built.successors_of("b") == ()


def test_successors_are_deduplicated_and_sorted() -> None:
    """Traversal order is deterministic, so findings come out in a stable order."""
    built = graph(edge("a", "c"), edge("a", "b"), edge("a", "c", refs=2))

    assert built.successors_of("a") == ("b", "c")


def test_a_chain_has_one_component_per_node() -> None:
    """An acyclic graph decomposes into singletons: nothing here is a cycle."""
    assert members(strongly_connected_components(graph(*chain(4)))) == [
        ["n000000"],
        ["n000001"],
        ["n000002"],
        ["n000003"],
    ]


def test_the_nodes_of_a_two_node_cycle_form_one_component() -> None:
    """Mutual dependency is one strongly connected component of two nodes."""
    components = strongly_connected_components(graph(edge("a", "b"), edge("b", "a")))

    assert members(components) == [["a", "b"]]


def test_every_node_belongs_to_exactly_one_component() -> None:
    """The components partition the graph, cycles and singletons alike."""
    built = graph(edge("a", "b"), edge("b", "c"), edge("c", "a"), edge("c", "d"))
    components = strongly_connected_components(built)

    assert members(components) == [["a", "b", "c"], ["d"]]
    assert sorted(node for component in components for node in component) == ["a", "b", "c", "d"]


def test_two_separate_cycles_are_two_components_in_a_stable_order() -> None:
    """Independent cycles stay separate, sorted by their members."""
    built = graph(edge("y", "z"), edge("z", "y"), edge("a", "b"), edge("b", "a"), edge("b", "y"))

    assert members(strongly_connected_components(built)) == [["a", "b"], ["y", "z"]]


def test_a_self_loop_is_a_component_of_one() -> None:
    """A node depending on itself does not join anything; the cycle rule decides its fate."""
    assert members(strongly_connected_components(graph(edge("a", "a")))) == [["a"]]


def test_a_chain_far_deeper_than_the_recursion_limit_is_decomposed() -> None:
    """The search is iterative: a 5000-node chain would exhaust a recursive implementation."""
    components = strongly_connected_components(graph(*chain(5_000)))

    assert len(components) == 5_000
    assert all(len(component) == 1 for component in components)


def test_internal_edges_are_the_edges_that_close_a_component() -> None:
    """The closing references of a cycle are the edges with both ends inside it (req 6.1)."""
    built = graph(edge("a", "b", refs=2), edge("b", "a", refs=3), edge("a", "out", refs=9))

    assert built.internal_edges(frozenset({"a", "b"})) == [
        GraphEdge("a", "b", 2),
        GraphEdge("b", "a", 3),
    ]


def test_internal_edges_exclude_edges_arriving_from_outside() -> None:
    """An edge into the component is not part of what closes it."""
    built = graph(edge("in", "a"), edge("a", "b"), edge("b", "a"))

    assert [(item.src, item.dst) for item in built.internal_edges(frozenset({"a", "b"}))] == [
        ("a", "b"),
        ("b", "a"),
    ]


def test_the_internal_edge_of_a_self_loop_is_the_loop_itself() -> None:
    """A one-node component has an internal edge exactly when the node depends on itself."""
    built = graph(edge("a", "a", refs=4), edge("b", "c"))

    assert built.internal_edges(frozenset({"a"})) == [GraphEdge("a", "a", 4)]
    assert built.internal_edges(frozenset({"b"})) == []


def test_an_empty_edge_list_makes_an_empty_graph() -> None:
    """A change that removed every dependency still analyses cleanly."""
    built = graph()

    assert built.nodes == ()
    assert strongly_connected_components(built) == []
