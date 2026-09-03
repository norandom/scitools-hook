"""Architecture nodes, dependency edges and exported graphs, measured (task 10.1).

Requirement 6.7 makes the ``Directory Structure`` architecture the default source of nodes at
a configurable depth, and requirement 9.4 asks for an SVG per affected entity. Both are
adapter-shaped: everything above them works on ``arch_nodes``, ``arch_edges``, ``file_edges``
and a list of written files, and none of those can be produced without Understand.

The sample project is built so the depth actually decides something. ``pkg/`` holds a file
*and* a subdirectory, ``app/`` is a sibling that depends on ``pkg/``, and ``main.py`` sits
directly in the analysis root -- which between them cover the three shapes the node walk has
been wrong about before.

One test here is an expected failure. It is not a stylistic choice: the architecture
dependency Understand reports for this project survives at depth 1 and is dropped at the
shipped default depth of 2, so the arch-cycle and coupling rules evaluate an empty edge set on
an ordinary layout. That is a defect in the worker, outside this task's boundary, and it is
recorded here in the form that will stop being an expected failure the moment it is fixed.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from contract_project import (
    FILES,
    SampleProject,
    comma_decimal_locale,
    extract,
    real_env,
    sample_project,  # noqa: F401 -- imported so the session fixture is registered here
)

from scitools_hook.models.change import GraphTarget
from scitools_hook.models.progress import NullCommandLog
from scitools_hook.models.snapshot import EntityKey, ProjectSnapshot
from scitools_hook.understand.api_runner import ApiRunner
from scitools_hook.understand.graphs import GraphExporter

pytestmark = pytest.mark.contract

ARCH = "Directory Structure"
"""The architecture requirement 6.7 uses when the configuration names none."""

CORE = EntityKey(scope="file", path="pkg/core.py", longname="pkg/core.py", parameters=None)
"""A file with both an incoming and an outgoing dependency, so its graphs are not empty."""

RUN = EntityKey(
    scope="routine", path="pkg/core.py", longname="core.Engine.run", parameters="self,value"
)
"""A method that calls another routine, so its butterfly graph has two wings."""

LABEL = re.compile(r"<text[^>]*>([^<]*)</text>")
"""The caption of one node of an exported graph."""

SINGLE_NUMBER = re.compile(r'([a-z-]+)="(-?\d+),(\d+)"')
"""An attribute whose whole value is one comma-decimal number -- the locale hazard.

Coordinates legitimately use commas (``points="12,7 66,7"``), so the pattern deliberately
matches only a value that is a single number and nothing else.
"""


@pytest.fixture(scope="module")
def alpha(sample_project: SampleProject) -> ProjectSnapshot:  # noqa: F811
    """The sample project at the shipped default architecture depth."""
    return extract(sample_project.db("alpha"), sample_project.root("alpha"), FILES, "before")


@pytest.fixture(scope="module")
def shallow(sample_project: SampleProject) -> ProjectSnapshot:  # noqa: F811
    """The same project one architecture level higher, so the depth is shown to matter."""
    return extract(
        sample_project.db("alpha"), sample_project.root("alpha"), FILES, "before", depth=1
    )


def nodes(snapshot: ProjectSnapshot) -> dict[str, list[str]]:
    """The architecture nodes as ``path -> members``."""
    return {node.path: node.members for node in snapshot.arch_nodes}


def edges(snapshot: ProjectSnapshot) -> set[tuple[str, str, int, bool]]:
    """The file dependency edges as comparable tuples."""
    return {(edge.src, edge.dst, edge.refs, edge.crosses_arch) for edge in snapshot.file_edges}


def labels(svg: str) -> set[str]:
    """The node captions of an exported graph, with Understand's direction glyphs removed.

    A caption reads ``▶ core.py ▶`` for the entity the graph is centred on and ``▷ entry.py``
    for one that only reaches it, so the glyphs are stripped rather than matched.
    """
    return {text.replace("▶", "").replace("▷", "").strip() for text in LABEL.findall(svg)}


# --- architecture nodes at a configured depth (requirement 6.7) -------------------


def test_the_directory_structure_nodes_at_depth_two(alpha: ProjectSnapshot) -> None:
    """The whole node set at the shipped default, members included.

    Three things are decided at once here and each has been wrong before: the shadow
    directory's own name is **not** part of a node path; a branch shallower than the
    requested depth (``app/``, ``native/``) contributes its own leaf rather than vanishing;
    and a file no node at this depth holds -- ``main.py`` in the root, ``pkg/core.py`` beside
    a subdirectory -- is attributed to the architecture itself, so it stays inside every
    node-level rule instead of being silently exempt.
    """
    assert nodes(alpha) == {
        ARCH: ["main.py", "pkg/core.py"],
        f"{ARCH}/app": ["app/entry.py"],
        f"{ARCH}/native": ["native/shape.cpp", "native/shape.h"],
        f"{ARCH}/pkg/inner": ["pkg/inner/leaf.py"],
    }


def test_a_shallower_depth_gives_a_different_node_set(shallow: ProjectSnapshot) -> None:
    """The discriminator for the test above: depth is read, not ignored.

    At depth 1 ``pkg`` is a node holding both of its files, and only ``main.py`` falls back
    to the architecture. Without this, "depth 2" could be any number at all.
    """
    assert nodes(shallow) == {
        ARCH: ["main.py"],
        f"{ARCH}/app": ["app/entry.py"],
        f"{ARCH}/native": ["native/shape.cpp", "native/shape.h"],
        f"{ARCH}/pkg": ["pkg/core.py", "pkg/inner/leaf.py"],
    }


def test_every_analysed_file_belongs_to_exactly_one_published_node(
    alpha: ProjectSnapshot,
) -> None:
    """Requirement 9.7 needs an architecture path per entity, and no file may fall out.

    A file that belonged to no node would be exempt from every structural rule while still
    appearing in the threshold report -- the silent-coverage shape, one layer down.
    """
    placed = [member for node in alpha.arch_nodes for member in node.members]

    assert sorted(placed) == sorted(FILES)
    assert len(placed) == len(set(placed))
    for key in alpha.entities:
        assert alpha.entities[key].archs, key.token


# --- dependency edges (requirements 6.1, 6.4, 6.7) --------------------------------


def test_the_file_dependency_edges_carry_their_reference_counts(alpha: ProjectSnapshot) -> None:
    """Every import and every include, with the reference count the ratchet compares.

    The count is what makes a *ref-count* change distinguishable from a *topology* change, so
    it is asserted rather than the mere presence of an edge. The C++ include is here too: a
    dependency edge set that only knew about Python imports would leave every native file
    structurally invisible.
    """
    assert edges(alpha) == {
        ("main.py", "app/entry.py", 3, True),
        ("app/entry.py", "pkg/core.py", 4, True),
        ("pkg/core.py", "pkg/inner/leaf.py", 3, True),
        ("native/shape.cpp", "native/shape.h", 4, False),
    }


def test_crossing_an_architecture_boundary_is_decided_by_the_depth(
    alpha: ProjectSnapshot, shallow: ProjectSnapshot
) -> None:
    """The same edge crosses at one depth and not at the other, which is the point of 6.7.

    ``pkg/core.py -> pkg/inner/leaf.py`` is inside one node at depth 1 and crosses a boundary
    at depth 2, because ``pkg/inner`` becomes a node of its own there.
    """
    inside_pkg = ("pkg/core.py", "pkg/inner/leaf.py")

    at_two = {(edge.src, edge.dst): edge.crosses_arch for edge in alpha.file_edges}
    at_one = {(edge.src, edge.dst): edge.crosses_arch for edge in shallow.file_edges}

    assert at_two[inside_pkg] is True
    assert at_one[inside_pkg] is False


def test_a_class_that_uses_another_class_is_a_class_edge(alpha: ProjectSnapshot) -> None:
    """Class-scope edges exist and are keyed by ``EntityKey.token`` on both ends."""
    engine = EntityKey(scope="class", path="pkg/core.py", longname="core.Engine", parameters=None)
    leaf = EntityKey(scope="class", path="pkg/inner/leaf.py", longname="leaf.Leaf", parameters=None)

    assert {(edge.src, edge.dst) for edge in alpha.class_edges} == {(engine.token, leaf.token)}


def test_the_architecture_dependency_understand_reports_is_published_at_depth_one(
    shallow: ProjectSnapshot,
) -> None:
    """``Arch.depends()`` answers exactly one edge for this project, and it arrives.

    Understand does not report a dependency from a parent node to one of its own
    descendants, so ``main.py -> app/entry.py`` has no architecture edge even though it
    crosses a boundary. ``app -> pkg`` is a genuine sibling dependency and is the one edge
    the architecture rules have to see.
    """
    assert [(edge.src, edge.dst, edge.refs) for edge in shallow.arch_edges] == [
        (f"{ARCH}/app", f"{ARCH}/pkg", 4)
    ]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "worker._arch_edges trims a reported dependency to the requested depth and then "
        "requires the trimmed path to be a published node. At depth 2 the target trims to "
        "'Directory Structure/pkg', which is not published because pkg/core.py falls back to "
        "the architecture itself -- so the edge is dropped and the arch-cycle and coupling "
        "rules see an empty edge set at the SHIPPED DEFAULT depth. Outside task 10.1's "
        "boundary; remove this marker when it is fixed."
    ),
)
def test_the_same_architecture_dependency_survives_at_the_default_depth(
    alpha: ProjectSnapshot,
) -> None:
    """The defect, stated as the property that should hold rather than as the symptom.

    Nothing about the project changed between depth 1 and depth 2, and Understand still
    reports ``app -> pkg`` at 4 references. The file edge ``app/entry.py -> pkg/core.py`` is
    still marked as crossing a boundary. So the answer contradicts itself: a crossing is
    published at file level and denied at architecture level.
    """
    crossing = [edge for edge in alpha.file_edges if edge.crosses_arch]
    assert crossing, "the fixture must still have a boundary-crossing file edge"

    sources = {edge.src for edge in alpha.arch_edges}
    assert f"{ARCH}/app" in sources
    assert sum(edge.refs for edge in alpha.arch_edges) == 4


# --- exported graphs (requirement 9.4) --------------------------------------------


def test_real_graphs_are_written_as_svg_files_the_summary_can_reference(
    sample_project: SampleProject,  # noqa: F811
    tmp_path: Path,
) -> None:
    """One SVG per target, in the directory the operator chose, with real content in it.

    The node captions are asserted, not just the XML prolog: an SVG with a prolog and no
    nodes is still a well-formed document, and a change summary that referenced one would
    look complete. A butterfly graph has to show **both** wings -- ``run`` is reached from
    ``entry_point`` and reaches ``widen`` -- because a picture drawn with only the callees
    would be a ``Calls`` graph under the wrong name.
    """
    exporter = GraphExporter(ApiRunner(real_env("upython"), NullCommandLog()))
    out_dir = tmp_path / "graphs"
    targets = [
        GraphTarget(key=CORE, graph="Depends On"),
        GraphTarget(key=CORE, graph="Butterfly"),
        GraphTarget(key=RUN, graph="Butterfly"),
    ]
    expected = {
        (CORE, "Depends On"): ("core.py", "leaf.py"),
        (CORE, "Butterfly"): ("core.py", "leaf.py", "entry.py"),
        (RUN, "Butterfly"): ("run", "widen", "entry_point"),
    }

    written = exporter.export(
        sample_project.db("alpha"), sample_project.root("alpha"), targets, out_dir
    )

    assert len(written) == 3
    assert {(graph.key, graph.graph) for graph in written} == set(expected)
    for graph in written:
        text = graph.path.read_text(encoding="utf-8")
        assert graph.path.parent == out_dir.resolve()
        assert text.lstrip().startswith("<?xml")
        assert "<svg" in text
        assert set(expected[(graph.key, graph.graph)]) <= labels(text), graph.path.name
    assert exporter.warnings == []


def test_an_exported_svg_uses_a_dot_decimal_under_a_comma_decimal_locale(
    sample_project: SampleProject,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The locale hazard, provoked rather than hoped for.

    Understand's graph engine formats SVG attribute values with the C library's numeric
    locale, so under a comma-decimal locale a butterfly graph comes out carrying
    ``stroke-opacity="0,000000"`` -- well-formed XML, an invalid presentation value, and a
    stroke that renders opaque where it was meant to be transparent. The worker forces
    ``LC_NUMERIC=C`` before the engine initialises; this runs the export with the hazard
    switched on in the environment and asserts the fix still holds.

    The locale has to be one the system actually has: setting ``LC_NUMERIC`` to a locale that
    is not installed changes nothing, and the test would then prove nothing.

    The failure mode is reachable and was measured directly rather than assumed: the same
    graph drawn under ``upython`` with ``LC_NUMERIC=de_DE.UTF-8`` and no ``LC_NUMERIC=C``
    reset comes out as ``opacity="0,000000"``, and with the reset as ``opacity="0.000000"``.
    :data:`SINGLE_NUMBER` is what tells that apart from the commas SVG uses legitimately
    between polygon coordinates.
    """
    monkeypatch.setitem(os.environ, "LC_NUMERIC", comma_decimal_locale())
    exporter = GraphExporter(ApiRunner(real_env("upython"), NullCommandLog()))

    written = exporter.export(
        sample_project.db("alpha"),
        sample_project.root("alpha"),
        [GraphTarget(key=CORE, graph="Butterfly")],
        tmp_path / "graphs",
    )

    assert len(written) == 1
    text = written[0].path.read_text(encoding="utf-8")
    assert 'stroke-opacity="0.000000"' in text, "the butterfly graph must carry an opacity"
    assert SINGLE_NUMBER.findall(text) == []
