"""The two coupling rules: new dependencies per file, and references per node pair (6.5, 6.6).

Both answer "how much is this wired to that?", one at the file level across the change and
one at the architecture level after it, so they live together.

:func:`new_dependencies` counts, for each affected file, the dependencies present after the
change that were not there before, and reports a file that gained more than the configured
maximum (req 6.5). That is the rule which catches a change wiring one file to many new files
while every individual metric stays inside its limit, so what it counts is *distinct new
targets*, not references: Understand emits one edge per pair and reference kind, and a
growing reference count on a dependency the file already had is not a new dependency. A new
self-reference is not one either, for the same reason it is not a cycle (req 6.1). Only the
files of the affected set are counted; another file's new dependencies are not this change's
doing. ``before_edges = None`` is whole-project mode: there is no before side, so a file's
whole dependency set is reported as an inventory and the message drops the word *new*
(req 4.8).

:func:`evaluate_coupling` sums the references on the after architecture edges between the
pair of nodes a :class:`~scitools_hook.config.models.CouplingRule` names and reports a pair
over its maximum (req 6.6). The sum is directional -- ``from_node -> to_node`` only -- and
edges repeated for several reference kinds are added up, which is what
:class:`~scitools_hook.analysis.structure.graph.DependencyGraph` does when it merges them.

Both rules are ``>`` rules: a file exactly at its limit and a pair exactly at its maximum are
allowed. As in the other structural evaluators, ``before`` stays ``None`` (neither finding
carries a per-entity pre-change metric), ``hint`` is left for the pipeline, ``limit_source``
is ``"rule"`` and ``blocking`` follows the configured severity (req 3.7).
"""

from __future__ import annotations

from collections.abc import Collection, Sequence

from scitools_hook.analysis.structure.graph import DependencyGraph
from scitools_hook.config.models import CouplingRule, Severity
from scitools_hook.models.findings import Finding, structure_rule
from scitools_hook.models.snapshot import DepEdge

_NEW_DEPS_RULE = structure_rule("new_dependencies")
_COUPLING_RULE = structure_rule("coupling")


def new_dependencies(
    before_edges: Sequence[DepEdge] | None,
    after_edges: Sequence[DepEdge],
    files: Collection[str],
    max_new: int,
    severity: Severity = "error",
) -> list[Finding]:
    """Report every affected file that gained more than ``max_new`` dependencies (req 6.5).

    ``files`` is the affected set and ``max_new`` is
    ``structure.max_new_dependencies_per_file``; ``None`` there switches the rule off and the
    pipeline does not call this function at all. ``severity`` is
    ``structure.new_dependencies_severity``. Findings come back in path order.
    """
    was = _targets_by_file(before_edges if before_edges is not None else [])
    now = _targets_by_file(after_edges)
    compared = before_edges is not None
    findings: list[Finding] = []
    for path in sorted(files):
        added = sorted(now.get(path, set()) - was.get(path, set()))
        if len(added) > max_new:
            findings.append(_new_deps_finding(path, added, max_new, severity, compared))
    return findings


def evaluate_coupling(
    after_arch_edges: Sequence[DepEdge], rules: Sequence[CouplingRule]
) -> list[Finding]:
    """Report every configured node pair whose references exceed its maximum (req 6.6).

    ``after_arch_edges`` are the architecture edges of the after side; the references of a
    pair are summed across the edges that connect it. Findings come back sorted by pair.
    """
    graph = DependencyGraph.from_edges(after_arch_edges)
    findings: list[Finding] = []
    for rule in rules:
        refs = graph.refs_between(rule.from_node, rule.to_node)
        if refs > rule.max_refs:
            findings.append(_coupling_finding(rule, refs))
    return sorted(findings, key=lambda finding: (finding.path, str(finding.details["to_node"])))


def _targets_by_file(edges: Sequence[DepEdge]) -> dict[str, set[str]]:
    """The distinct files each file depends on, self-references excluded."""
    targets: dict[str, set[str]] = {}
    for edge in edges:
        if edge.src != edge.dst:
            targets.setdefault(edge.src, set()).add(edge.dst)
    return targets


def _new_deps_finding(
    path: str, added: list[str], max_new: int, severity: Severity, compared: bool
) -> Finding:
    """One file over the new-dependency limit (req 6.5); ``hint`` is attached by the pipeline."""
    return Finding(
        kind="structural",
        rule=_NEW_DEPS_RULE,
        scope="file",
        path=path,
        value=len(added),
        limit=max_new,
        limit_source="rule",
        severity=severity,
        blocking=severity == "error",
        message=_new_deps_message(path, added, max_new, compared),
        details={"new_dependencies": added},
    )


def _new_deps_message(path: str, added: Sequence[str], max_new: int, compared: bool) -> str:
    """One line naming the file, how many dependencies it gained and which (req 7.1)."""
    gained = (
        f"gained {len(added)} new dependencies in this change"
        if compared
        else f"depends on {len(added)} files"
    )
    return f"{path} {gained}, more than the maximum of {max_new}: {', '.join(added)}"


def _coupling_finding(rule: CouplingRule, refs: int) -> Finding:
    """One node pair over its reference maximum (req 6.6); ``hint`` comes from the pipeline."""
    return Finding(
        kind="structural",
        rule=_COUPLING_RULE,
        scope="arch",
        path=rule.from_node,
        value=refs,
        limit=rule.max_refs,
        limit_source="rule",
        severity=rule.severity,
        blocking=rule.severity == "error",
        message=(
            f"{rule.from_node} makes {refs} references to {rule.to_node} after the change, "
            f"more than the maximum of {rule.max_refs}"
        ),
        details={"from_node": rule.from_node, "to_node": rule.to_node},
    )
