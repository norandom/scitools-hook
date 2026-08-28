"""Layer rules: the new dependency edges a change may not draw (req 6.3).

A :class:`~scitools_hook.config.models.LayerRule` names one architecture node and the nodes
it may depend on. This module judges the **file** edges of the after side against those
rules and reports one finding per forbidden new edge, naming — as requirement 6.3 asks —
the source entity, the target entity and the rule that was violated.

Four things are deliberately silent, because a layer rule is about the direction a *change*
took, between two nodes a rule actually constrains:

* an edge that was already there before the change: the rule judges what this change did,
  not what the repository inherited, and a growing reference count on an existing edge is
  not a new edge either — newness is a property of the pair;
* an edge whose target node is listed in ``may_depend_on``: that is exactly the architecture
  the operator asked for;
* an edge inside one node (``node_of(src) == node_of(dst)``): a node always depends on
  itself, and a layer rule is about crossing into another node;
* an edge either end of which belongs to no node at all: a rule allows *nodes*, so a file
  outside the architecture can neither break one nor be judged against a list of node names.

Two rules may name the same node; each is enforced on its own, so an edge that breaks both
is reported twice, once per rule name. Findings come back sorted by the edge they report and
then by the rule, so the output is the same run after run. ``before_edges = None`` is
whole-project mode (req 4.8): there is no before side, so every edge is judged.

As in the other structural evaluators, ``hint`` is left for the pipeline to attach,
``limit_source`` is ``"rule"`` — a layer rule has no numeric limit — and ``blocking``
follows the rule's configured severity (req 3.7).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence

from scitools_hook.config.models import LayerRule
from scitools_hook.models.findings import Finding, structure_rule
from scitools_hook.models.snapshot import DepEdge

NodeOf = Callable[[str], str | None]
"""Maps a file path to its architecture node, or ``None`` when it is in none."""

_RULE = structure_rule("layer")


def evaluate_layers(
    after_edges: Sequence[DepEdge],
    before_edges: Sequence[DepEdge] | None,
    node_of: NodeOf,
    rules: Sequence[LayerRule],
) -> list[Finding]:
    """Report every new file edge that breaks one of the configured layer rules (req 6.3).

    ``after_edges`` and ``before_edges`` are the two sides' file edges; ``node_of`` names the
    architecture node of a file, in the architecture the nodes of ``rules`` live in.
    ``before_edges = None`` means there is no before side, so every edge counts as new
    (req 4.8). Findings come back sorted by edge, then by configuration order of the rules.
    """
    known = _pairs(before_edges) if before_edges is not None else set()
    by_node = _rules_by_node(rules)
    findings: list[Finding] = []
    for src, dst in sorted(_pairs(after_edges) - known):
        findings.extend(_judge(src, dst, node_of, by_node))
    return findings


def _pairs(edges: Sequence[DepEdge]) -> set[tuple[str, str]]:
    """The distinct ``(src, dst)`` pairs of ``edges``; a reference count is not identity."""
    return {(edge.src, edge.dst) for edge in edges}


def _rules_by_node(rules: Sequence[LayerRule]) -> dict[str, list[LayerRule]]:
    """The rules constraining each node, in configuration order; a node may have several."""
    grouped: dict[str, list[LayerRule]] = {}
    for rule in rules:
        grouped.setdefault(rule.node, []).append(rule)
    return grouped


def _judge(
    src: str, dst: str, node_of: NodeOf, by_node: dict[str, list[LayerRule]]
) -> Iterator[Finding]:
    """One finding per rule of the source's node that does not allow this edge (req 6.3)."""
    from_node, to_node = node_of(src), node_of(dst)
    if from_node is None or to_node is None or from_node == to_node:
        return
    for rule in by_node.get(from_node, []):
        if to_node not in rule.may_depend_on:
            yield _finding(rule, src, dst, from_node, to_node)


def _finding(rule: LayerRule, src: str, dst: str, from_node: str, to_node: str) -> Finding:
    """One forbidden new edge as a finding (req 6.3); ``hint`` is attached by the pipeline."""
    return Finding(
        kind="structural",
        rule=_RULE,
        scope="file",
        path=src,
        limit_source="rule",
        severity=rule.severity,
        blocking=rule.severity == "error",
        message=_message(rule, src, dst, from_node, to_node),
        details={
            "rule_name": rule.name,
            "from_node": from_node,
            "to_node": to_node,
            "src": src,
            "dst": dst,
        },
    )


def _message(rule: LayerRule, src: str, dst: str, from_node: str, to_node: str) -> str:
    """One line naming both entities, both nodes and the rule violated (req 6.3, 7.1)."""
    return (
        f"{src} now depends on {dst}, but layer rule {rule.name!r} does not allow "
        f"{from_node} to depend on {to_node}"
    )
