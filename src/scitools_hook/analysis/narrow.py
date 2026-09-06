"""Cut a wide snapshot down to the one a bounded extraction would have produced (req 8.3).

Task 9.2 lets one walk record two rings of neighbourhood. This is the other half: the
consumers of a snapshot were written against a document bounded to *their* set of files, and
handing them a wider one would change what the rules see -- an entity two steps away that no
rule was ever meant to judge, an edge between two files the change did not touch.

So a run extracts once, wide, and narrows the document twice: once to the selected files for
the affected-set resolver, and once to the affected files and their neighbourhood for the
rules. Both narrowings have to produce exactly what a direct extraction for that set would,
or requirement 8.7's promise -- the cache and the single pass change no finding -- is broken.

**Two things are narrowed and everything else is left alone**, and the split is not arbitrary:

* **entities** are bounded by the request, so they are cut to the given files;
* **file and class edges** are bounded by the request *plus one dependency step*, which is how
  the worker scopes them (``_neighbourhood``), so the same rule is applied here to the edges
  the wide document already carries;
* **populations, the call graph, the architecture, the parse errors, the unavailable metrics
  and the definitions are whole-project by construction.** The worker computes each of them
  over the database rather than over the request, precisely so that a project-wide percentile
  is a statement about the project. Narrowing them would make them statements about the
  change, which is the bug this module exists to avoid rather than to introduce.
"""

from __future__ import annotations

from collections.abc import Collection

from scitools_hook.models.snapshot import DepEdge, ProjectSnapshot


def narrow(snapshot: ProjectSnapshot, files: Collection[str]) -> ProjectSnapshot:
    """The document a direct extraction for ``files`` would have produced.

    ``files`` must be a subset of what the wide document covers; nothing here can add an
    entity that was never recorded, and a caller asking for one silently gets a smaller
    answer rather than an error, because the check that would catch it is the caller's own
    selection logic and repeating it here would be a second place to disagree.
    """
    wanted = frozenset(files)
    return snapshot.model_copy(
        update={
            "entities": {
                key: record for key, record in snapshot.entities.items() if key.path in wanted
            },
            "file_edges": _within(snapshot.file_edges, _one_ring(snapshot.file_edges, wanted)),
            "class_edges": _within(
                snapshot.class_edges,
                _one_ring(snapshot.class_edges, _class_scope(snapshot, wanted)),
            ),
        }
    )


def _class_scope(snapshot: ProjectSnapshot, files: frozenset[str]) -> frozenset[str]:
    """The class endpoints of the wanted files, as ``EntityKey.token`` values.

    Class edges name their endpoints by token rather than by path, so the seeds for the
    one-ring rule have to be translated before the same rule can be applied to them."""
    return frozenset(
        key.token for key in snapshot.entities if key.scope == "class" and key.path in files
    )


def _one_ring(edges: Collection[DepEdge], seeds: frozenset[str]) -> frozenset[str]:
    """The seeds plus everything one dependency step from them, in either direction.

    Both directions, because that is what ``worker._targets`` asks -- ``depends()`` *and*
    ``dependsby()``. An edge into an affected file is as much a fact about the change as an
    edge out of it, and a fan-in rule reads exactly the first kind.
    """
    return seeds | {
        other
        for edge in edges
        for other in ((edge.dst,) if edge.src in seeds else ())
        + ((edge.src,) if edge.dst in seeds else ())
    }


def _within(edges: Collection[DepEdge], scope: frozenset[str]) -> list[DepEdge]:
    """The edges whose both endpoints are inside ``scope``, in the order they arrived."""
    return [edge for edge in edges if edge.src in scope and edge.dst in scope]
