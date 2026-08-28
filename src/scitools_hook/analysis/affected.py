"""What one staged change affects: files, the entities in them, and their neighbourhood.

Requirement 4.2 defines the affected set as the entities defined in staged files **plus the
files whose dependency set changed because of the change**. The second half is what makes a
gate honest: staging ``app.py`` can add an import to it and thereby close a cycle through
``rules.py``, a file the change never touched. The clause is evaluated on the two dependency
graphs — a file is affected when its outgoing target set differs between ``before`` and
``after``. Only the *set* is compared: a pair whose reference count moved carries the same
dependency, and any file whose content changed is staged anyway.

Three deliberate choices, all visible in ``tests/analysis/test_affected.py``:

* **Deletions are not affected files, and neither is losing a dependency to one.** A deleted
  file's own dependency set trivially differs, but there is nothing left to evaluate, so it
  lands in ``deleted_files`` only and drops out of ``files`` and out of the neighbourhood. Its
  dependents lose an edge without changing themselves, so the comparison ignores deleted
  targets on both sides: a file is affected when its dependencies on the *surviving* files
  differ. That is what makes a deletions-only change yield ``files = ∅`` while the structural
  rules still run on the survivors (req 4.10) — the deleted files' **former dependents**, read
  from the before graph, are the neighbourhood in that case.
* **The neighbourhood excludes the affected files themselves.** It is the ring around the
  change: direct dependents and direct dependencies of ``files``, which is what the cycle and
  fan rules need in order to see one step past the change, and no more.
* **An empty staged list resolves to an empty affected set**, whatever the snapshots say. No
  staged change is nothing to analyze (req 4.9), so there is nothing to widen either.

Without a ``before`` side — whole-project mode, or a repository with no ``HEAD`` — there is
nothing to compare: the dependency-difference clause cannot fire and deleted files have no
recorded former dependents, so ``files`` is exactly the staged, still existing paths.

Renames carry both ends: ``R`` puts its new path in ``files`` and its ``old_path`` in
``deleted_files``. Files Understand could not parse are staged like any other: they appear in
``files``, contribute no keys, and have no place in either graph.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from scitools_hook.analysis.structure.graph import DependencyGraph
from scitools_hook.models.change import AffectedSet
from scitools_hook.models.git import StagedChange
from scitools_hook.models.snapshot import EntityKey, ProjectSnapshot


def resolve(
    staged: Sequence[StagedChange], after: ProjectSnapshot, before: ProjectSnapshot | None
) -> AffectedSet:
    """The affected set of ``staged``, given the two sides of the change (req 4.2, 4.10).

    ``before`` is ``None`` in whole-project mode, where no comparison is possible. The result
    holds sets only, so it depends on the staged entries, not on the order they arrive in.
    """
    if not staged:
        return AffectedSet()
    after_graph = DependencyGraph.from_edges(after.file_edges)
    before_graph = None if before is None else DependencyGraph.from_edges(before.file_edges)
    deleted = _deleted_paths(staged)
    widened = _dependency_changed(before_graph, after_graph, deleted)
    files = (_surviving_paths(staged) | widened) - deleted
    return AffectedSet(
        files=files,
        deleted_files=deleted,
        keys=_keys_in(after, files),
        neighbourhood=_neighbourhood(after_graph, before_graph, files, deleted),
    )


def _surviving_paths(staged: Iterable[StagedChange]) -> set[str]:
    """The staged paths that still exist after the change: everything git did not call ``D``."""
    return {change.path for change in staged if change.status != "D"}


def _deleted_paths(staged: Iterable[StagedChange]) -> set[str]:
    """What the change removes: staged deletions plus the old path of every rename."""
    removed = {change.path for change in staged if change.status == "D"}
    renamed = {
        change.old_path for change in staged if change.status == "R" and change.old_path is not None
    }
    return removed | renamed


def _dependency_changed(
    before: DependencyGraph | None, after: DependencyGraph, deleted: set[str]
) -> set[str]:
    """Files whose dependencies on the surviving files differ between the two sides (req 4.2).

    Empty without a before side. A file that gained a dependency and one that lost a
    dependency are both affected; a file whose targets are unchanged is not, however many
    references now run along an edge. Dependencies on ``deleted`` files are left out of both
    sides, so losing an edge to a file this change removed is not a dependency change of the
    dependent — that file is a former dependent, and belongs in the neighbourhood (req 4.10).
    """
    if before is None:
        return set()
    return {
        node
        for node in set(before.nodes) | set(after.nodes)
        if _targets(before, node, deleted) != _targets(after, node, deleted)
    }


def _targets(graph: DependencyGraph, node: str, deleted: set[str]) -> set[str]:
    """What ``node`` depends on in ``graph``, files the change removed excluded."""
    return set(graph.successors_of(node)) - deleted


def _keys_in(after: ProjectSnapshot, files: set[str]) -> set[EntityKey]:
    """Every entity the after side defines in one of ``files``: the affected keys (req 4.2)."""
    return {key for key in after.entities if key.path in files}


def _neighbourhood(
    after: DependencyGraph, before: DependencyGraph | None, files: set[str], deleted: set[str]
) -> set[str]:
    """The ring around the change: one step out from ``files``, plus deleted files' dependents.

    Members of ``files`` and of ``deleted`` are not their own neighbours — the first are the
    change, the second are gone.
    """
    dependents = _dependents_of(after)
    neighbours: set[str] = set()
    for path in files:
        neighbours |= set(after.successors_of(path)) | dependents.get(path, set())
    if before is not None:
        former = _dependents_of(before)
        for path in deleted:
            neighbours |= former.get(path, set())
    return neighbours - files - deleted


def _dependents_of(graph: DependencyGraph) -> dict[str, set[str]]:
    """Node -> the nodes that depend on it directly: the graph's edges reversed."""
    dependents: dict[str, set[str]] = {}
    for node in graph.nodes:
        for target in graph.successors_of(node):
            dependents.setdefault(target, set()).add(node)
    return dependents
