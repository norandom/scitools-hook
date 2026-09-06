"""Narrowing a wide snapshot to what a bounded extraction would have produced (req 8.3, 8.7).

One walk records two rings; the consumers were written against a document bounded to their own
set of files. Narrowing is what makes those two facts compatible, and the whole of its
correctness is one claim: **narrowing a wide document to a set produces the document a direct
extraction for that set would have produced.** The test that matters here asserts exactly that,
by extracting the fake project both ways.

What is *not* narrowed matters as much. Populations, the call graph, the architecture, the
parse errors, the unavailable metrics and the definitions are whole-project by construction --
the worker computes each over the database rather than over the request, so that a project-wide
percentile is a statement about the project. Cutting them down would turn each into a statement
about the change, which is the bug this module exists to avoid.

The module lives beside the worker's tests rather than the analysis layer's because the
claim can only be made against a real extraction, and ``api_fakes`` is importable from
this directory alone -- the test directories carry no ``__init__.py``.
"""

from __future__ import annotations

from typing import Any

import pytest
from api_fakes import FakeUnderstand, install
from worker_projects import fake_project, snapshot_request

from scitools_hook.analysis.narrow import narrow
from scitools_hook.models.snapshot import ProjectSnapshot
from scitools_hook.understand import worker

SELECTED = "cli/app.py"
NEIGHBOUR = "util/text.py"
FAR = "native/util.c"
"""One step and two steps from the selection, along the fake project's dependencies."""


def extracted(monkeypatch: pytest.MonkeyPatch, **overrides: object) -> ProjectSnapshot:
    """One extraction of the fake project, validated into the model the rules read."""
    install(monkeypatch, FakeUnderstand(db=fake_project().db))
    document: dict[str, Any] = worker.dispatch("snapshot", snapshot_request(**overrides))
    assert "error" not in document, document
    return ProjectSnapshot.model_validate(document)


def paths(snapshot: ProjectSnapshot) -> set[str]:
    """The files the document holds entities for."""
    return {key.path for key in snapshot.entities}


def edges(snapshot: ProjectSnapshot) -> set[tuple[str, str]]:
    """The file dependency edges, as endpoint pairs."""
    return {(edge.src, edge.dst) for edge in snapshot.file_edges}


# --- the claim the whole module rests on ---------------------------------------------------


def test_a_narrowed_wide_document_equals_a_direct_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement 8.7: one pass plus narrowing must change nothing a rule can see."""
    wide = extracted(monkeypatch, neighbourhood_rings=2)
    direct = extracted(monkeypatch, files=[SELECTED])

    narrowed = narrow(wide, {SELECTED})

    assert narrowed.entities == direct.entities
    assert narrowed.file_edges == direct.file_edges
    assert narrowed.class_edges == direct.class_edges


def test_it_holds_for_the_wider_set_the_rules_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The second narrowing a run does: the affected files and their neighbourhood."""
    wide = extracted(monkeypatch, neighbourhood_rings=2)
    direct = extracted(monkeypatch, files=[SELECTED, NEIGHBOUR])

    narrowed = narrow(wide, {SELECTED, NEIGHBOUR})

    assert narrowed.entities == direct.entities
    assert narrowed.file_edges == direct.file_edges
    assert narrowed.class_edges == direct.class_edges


# --- what narrowing does -------------------------------------------------------------------


def test_entities_outside_the_set_are_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    wide = extracted(monkeypatch, neighbourhood_rings=2)
    assert {SELECTED, NEIGHBOUR, FAR} <= paths(wide)

    assert paths(narrow(wide, {SELECTED})) == {SELECTED}


def test_an_edge_one_step_from_the_set_survives(monkeypatch: pytest.MonkeyPatch) -> None:
    """The worker scopes edges to the request plus one step, and so does this."""
    wide = extracted(monkeypatch, neighbourhood_rings=2)

    kept = edges(narrow(wide, {SELECTED}))

    assert (SELECTED, NEIGHBOUR) in kept


def test_an_edge_wholly_outside_the_one_ring_is_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``util/text.py -> native/util.c`` is two steps from the selection, so it goes."""
    wide = extracted(monkeypatch, neighbourhood_rings=2)
    assert (NEIGHBOUR, FAR) in edges(wide)

    assert (NEIGHBOUR, FAR) not in edges(narrow(wide, {SELECTED}))


# --- what narrowing must not touch ----------------------------------------------------------


def test_the_whole_project_facts_are_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each is computed over the database, so cutting it would change what it is a claim about."""
    wide = extracted(monkeypatch, neighbourhood_rings=2)

    narrowed = narrow(wide, {SELECTED})

    assert narrowed.populations == wide.populations
    assert narrowed.call_edges == wide.call_edges
    assert narrowed.call_nodes == wide.call_nodes
    assert narrowed.call_resolution == wide.call_resolution
    assert narrowed.arch_nodes == wide.arch_nodes
    assert narrowed.arch_edges == wide.arch_edges
    assert narrowed.parse_errors == wide.parse_errors
    assert narrowed.unavailable == wide.unavailable
    assert narrowed.definitions == wide.definitions


def test_the_side_and_the_languages_survive(monkeypatch: pytest.MonkeyPatch) -> None:
    wide = extracted(monkeypatch, neighbourhood_rings=2)

    narrowed = narrow(wide, {SELECTED})

    assert narrowed.side == wide.side
    assert narrowed.languages == wide.languages


def test_narrowing_to_everything_changes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """The identity case, which a rule that over-cut would fail."""
    wide = extracted(monkeypatch, neighbourhood_rings=2)

    assert narrow(wide, paths(wide)) == wide
