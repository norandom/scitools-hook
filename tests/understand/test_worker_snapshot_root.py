"""The analysis root: how the shadow directory's own name is kept out of every path (11.10).

A database is built over ``<root>/before`` or ``<root>/after``, and Understand names every
file and architecture node under that segment. Nothing downstream may see it -- the two sides
of a change must name one file the same way -- so these tests build shadow roots of every
shape the walk has been wrong about (a top-level file, a nested tree, a name clash between
the segment and a package) and assert the segment is gone.
"""

from __future__ import annotations

import pytest
from api_fakes import (
    FakeArch,
    FakeDb,
    FakeEnt,
    FakeUnderstand,
    envelope,
    install,
)
from worker_projects import (
    ANALYSIS_ROOT,
    FILE_KIND,
    FILE_ONLY,
    a_file,
    fake_project,
    listing,
    records,
    snapshot,
    snapshot_request,
)

from scitools_hook.models.snapshot import EntityKey
from scitools_hook.understand import worker

# --- snapshot: the analysis root ------------------------------------------------


def shadow_file(path: str, segment: str, root: str = ANALYSIS_ROOT) -> FakeEnt:
    """A file as Understand reports it from a root that also holds a top-level file.

    ``relname`` then carries the root directory's own name (``after/main.py``) while
    ``longname`` stays the plain absolute path.
    """
    return FakeEnt(
        path=f"{segment}/{path}",
        qualified=f"{root}/{path}",
        kind_path="python File",
        simple=path.rsplit("/", 1)[-1],
    )


def shadow_db(segment: str = "after", root: str = ANALYSIS_ROOT) -> FakeDb:
    """A shadow root holding ``main.py`` beside a ``pkg`` package, as Understand sees it."""
    main = shadow_file("main.py", segment, root)
    core = shadow_file("pkg/core.py", segment, root)
    inner = FakeArch(f"Directory Structure/{segment}/pkg", ents=[core])
    shadow = FakeArch(f"Directory Structure/{segment}", children=[inner], ents=[main])
    top = FakeArch("Directory Structure", children=[shadow])
    return FakeDb([top], entities={FILE_KIND: [main, core]})


def root_only_db(segment: str, root: str) -> FakeDb:
    """A shadow root holding nothing but files, so the inserted level is itself a leaf."""
    main = shadow_file("main.py", segment, root)
    shadow = FakeArch(f"Directory Structure/{segment}", ents=[main])
    top = FakeArch("Directory Structure", children=[shadow])
    return FakeDb([top], entities={FILE_KIND: [main]})


def nested_db() -> FakeDb:
    """Every source under ``src/app``, with nothing analysed above it.

    Understand roots ``Directory Structure`` at the parent of the deepest common ancestor of
    the *analysed* files (verified live), so the architecture has a single child ``app`` and
    ``relname`` is relative to ``src``, not to the analysis root.
    """
    entry = FakeEnt(
        path="app/entry.py",
        qualified=f"{ANALYSIS_ROOT}/src/app/entry.py",
        kind_path="python File",
        simple="entry.py",
    )
    mod = FakeEnt(
        path="app/core/deep/mod.py",
        qualified=f"{ANALYSIS_ROOT}/src/app/core/deep/mod.py",
        kind_path="python File",
        simple="mod.py",
    )
    deep = FakeArch("Directory Structure/app/core/deep", ents=[mod])
    core = FakeArch("Directory Structure/app/core", children=[deep])
    app = FakeArch("Directory Structure/app", children=[core], ents=[entry])
    top = FakeArch("Directory Structure", children=[app])
    return FakeDb([top], entities={FILE_KIND: [entry, mod]})


def test_snapshot_takes_a_key_path_from_the_long_name_not_from_the_relative_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``relname`` is prefixed with the analysis root's own name once a file sits in it.

    Verified live: a root holding only subdirectories answers ``pkg/core.py``, but the same
    root with a top-level file answers ``before/main.py`` and ``before/pkg/core.py``. The
    gate analyses shadows called ``before`` and ``after``, so keys built from that name never
    match across a change: every requested file misses the ``files`` set and the run reports
    no entities at all while still validating. Any repository with a top-level source file
    has this layout.
    """
    install(monkeypatch, FakeUnderstand(db=shadow_db()))
    document = worker.dispatch(
        "snapshot", snapshot_request(files=["main.py", "pkg/core.py"], kinds_by_scope=FILE_ONLY)
    )
    assert set(records(document)) == {"main.py", "pkg/core.py"}


def test_snapshot_falls_back_to_the_relative_name_outside_the_analysis_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A file the caller's root does not cover keeps Understand's own relative name; only an
    # absolute one puts a file out of the repository altogether.
    stray = FakeEnt(path="vendor/x.py", qualified="/elsewhere/vendor/x.py", kind_path="python File")
    inside = a_file("cli/app.py")
    root = FakeArch("Directory Structure", ents=[inside])
    install(monkeypatch, FakeUnderstand(db=FakeDb([root], entities={FILE_KIND: [stray, inside]})))
    document = worker.dispatch(
        "snapshot",
        snapshot_request(files=["vendor/x.py", "cli/app.py"], kinds_by_scope=FILE_ONLY, depth=0),
    )
    assert set(records(document)) == {"vendor/x.py", "cli/app.py"}
    # The architecture does not contain it, so no node is claimed for it either.
    assert records(document)["vendor/x.py"]["archs"] == []


def test_snapshot_never_names_the_shadow_in_an_architecture_node_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The analysis root becomes an extra Directory Structure level when a file sits in it.

    That level is named after the shadow, so it differs between the two sides; resolving it
    before ``depth`` is applied keeps both the node paths and the meaning of ``depth``
    identical on the two sides.
    """
    install(monkeypatch, FakeUnderstand(db=shadow_db()))
    document = worker.dispatch(
        "snapshot", snapshot_request(files=["main.py", "pkg/core.py"], kinds_by_scope=FILE_ONLY)
    )
    assert listing(document, "arch_nodes") == [
        # `main.py` sits in the analysis root, so no deeper node holds it and the
        # architecture itself does (req 9.7); it is listed once, under that node only.
        {"path": "Directory Structure", "members": ["main.py"]},
        {"path": "Directory Structure/pkg", "members": ["pkg/core.py"]},
    ]
    assert records(document)["pkg/core.py"]["archs"] == ["Directory Structure/pkg"]


def test_snapshot_keeps_a_directory_level_the_repository_really_has(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The inserted level is told from a real directory by name, and by nothing weaker.

    Understand roots ``Directory Structure`` at the parent of the deepest common ancestor of
    the *analysed* files, and files it does not analyse never enter it (verified: adding
    ``README.md`` and ``pyproject.toml`` changes nothing). A repository whose sources all sit
    under ``src/app`` therefore shows the same shape as a shadow — a single child holding
    every file, whose own name does not start their repository-relative paths. Stripping it
    would delete a real directory, disagree with the ``archs`` operation, drop ``entry.py``
    out of every node-level rule, and move the whole node set the day a file is added a level
    up.
    """
    install(monkeypatch, FakeUnderstand(db=nested_db()))
    document = worker.dispatch(
        "snapshot",
        snapshot_request(
            files=["src/app/entry.py", "src/app/core/deep/mod.py"], kinds_by_scope=FILE_ONLY
        ),
    )
    assert listing(document, "arch_nodes") == [
        {
            "path": "Directory Structure/app",
            "members": ["src/app/core/deep/mod.py", "src/app/entry.py"],
        }
    ]
    assert records(document)["src/app/entry.py"]["archs"] == ["Directory Structure/app"]


def shadow_edges_db(segment: str, root: str) -> FakeDb:
    """A shadow root with a top-level file and two packages, one depending on the other."""
    main = shadow_file("main.py", segment, root)
    app = shadow_file("cli/app.py", segment, root)
    text = shadow_file("util/text.py", segment, root)
    app.deps = {text: [object()] * 2}
    text.deps_by = {app: [object()] * 2}
    cli_node = FakeArch(f"Directory Structure/{segment}/cli", ents=[app])
    util_node = FakeArch(f"Directory Structure/{segment}/util", ents=[text])
    shadow = FakeArch(f"Directory Structure/{segment}", children=[cli_node, util_node], ents=[main])
    top = FakeArch("Directory Structure", children=[shadow])
    return FakeDb([top], entities={FILE_KIND: [main, app, text]})


def child_to_root_db(segment: str, root: str) -> FakeDb:
    """A shadow root whose package depends on a module sitting in the analysis root."""
    main = shadow_file("main.py", segment, root)
    core = shadow_file("pkg/core.py", segment, root)
    core.deps = {main: [object()] * 3}
    main.deps_by = {core: [object()] * 3}
    pkg_node = FakeArch(f"Directory Structure/{segment}/pkg", ents=[core])
    shadow = FakeArch(f"Directory Structure/{segment}", children=[pkg_node], ents=[main])
    top = FakeArch("Directory Structure", children=[shadow])
    return FakeDb([top], entities={FILE_KIND: [main, core]})


@pytest.mark.parametrize("segment", ["before", "after"])
def test_snapshot_keeps_an_architecture_edge_that_points_at_the_analysis_root(
    monkeypatch: pytest.MonkeyPatch, segment: str
) -> None:
    """A node depending on a root-level file yields a real edge that must not be dropped.

    The walk root has no OUTGOING dependencies -- it holds every analysed file, so there is
    nothing outside it to depend on -- but the INCOMING direction is real: Understand reports
    ``Directory Structure/pkg -> Directory Structure`` at 3 refs (measured live). Dropping it
    leaves a document that contradicts itself: the node is published, the file edge is marked
    as crossing, and yet the coupling rule (req 6.6) sums nothing and the arch-cycle rule
    (req 6.2) sees an isolated node.
    """
    root = f"/ws/{segment}"
    install(monkeypatch, FakeUnderstand(db=child_to_root_db(segment, root)))

    document = worker.dispatch(
        "snapshot",
        snapshot_request(root=root, files=["main.py", "pkg/core.py"], kinds_by_scope=FILE_ONLY),
    )

    assert listing(document, "arch_edges") == [
        {
            "src": "Directory Structure/pkg",
            "dst": "Directory Structure",
            "refs": 3,
            "crosses_arch": True,
        }
    ]


@pytest.mark.parametrize("segment", ["before", "after"])
def test_snapshot_keeps_the_architecture_edges_of_a_shadow_with_a_top_level_file(
    monkeypatch: pytest.MonkeyPatch, segment: str
) -> None:
    """Re-anchoring the node names must not lose the edges between them.

    ``Arch.depends()`` answers with nodes named for the shadow, so an endpoint that is not
    put back onto the architecture matches no node of the answer and every architecture edge
    disappears — silently switching off arch cycles, layer rules and coupling for every
    repository that has a top-level source file.
    """
    root = f"/ws/{segment}"
    install(monkeypatch, FakeUnderstand(db=shadow_edges_db(segment, root)))
    document = worker.dispatch(
        "snapshot", snapshot_request(root=root, files=["cli/app.py"], kinds_by_scope=FILE_ONLY)
    )
    assert listing(document, "arch_edges") == [
        {
            "src": "Directory Structure/cli",
            "dst": "Directory Structure/util",
            "refs": 2,
            "crosses_arch": True,
        }
    ]


def name_clash_db(segment: str, root: str) -> FakeDb:
    """A repository whose sources really do all live in a directory called like the shadow."""
    inner = FakeEnt(
        path=f"{segment}/x.py",
        qualified=f"{root}/{segment}/x.py",
        kind_path="python File",
        simple="x.py",
    )
    node = FakeArch(f"Directory Structure/{segment}", ents=[inner])
    top = FakeArch("Directory Structure", children=[node])
    return FakeDb([top], entities={FILE_KIND: [inner]})


def test_snapshot_keeps_a_directory_that_is_named_like_the_shadow_it_sits_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The name alone is not enough: a repository may have a directory called ``after``.

    The two are told apart by whether that name starts the repository-relative paths of the
    files the node holds — for a real directory it does, for the inserted level it cannot.
    """
    install(monkeypatch, FakeUnderstand(db=name_clash_db("after", ANALYSIS_ROOT)))
    document = worker.dispatch(
        "snapshot", snapshot_request(files=["after/x.py"], kinds_by_scope=FILE_ONLY)
    )
    assert listing(document, "arch_nodes") == [
        {"path": "Directory Structure/after", "members": ["after/x.py"]}
    ]
    assert records(document)["after/x.py"]["archs"] == ["Directory Structure/after"]


@pytest.mark.parametrize("segment", ["before", "after"])
def test_snapshot_names_the_architecture_for_a_root_that_holds_only_files(
    monkeypatch: pytest.MonkeyPatch, segment: str
) -> None:
    """The inserted level can be a leaf, and then it *is* the node at the requested depth.

    Reporting it under its own name would put ``Directory Structure/before`` on one side of a
    change against ``Directory Structure/after`` on the other — the very symptom this task
    was rejected for, through the node list instead of the entity keys.
    """
    root = f"/ws/{segment}"
    install(monkeypatch, FakeUnderstand(db=root_only_db(segment, root)))
    document = worker.dispatch(
        "snapshot", snapshot_request(root=root, files=["main.py"], kinds_by_scope=FILE_ONLY)
    )
    assert listing(document, "arch_nodes") == [
        {"path": "Directory Structure", "members": ["main.py"]}
    ]
    assert records(document)["main.py"]["archs"] == ["Directory Structure"]


@pytest.mark.parametrize("segment", ["before", "after"])
def test_snapshot_attributes_a_file_in_the_analysis_root_to_the_architecture(
    monkeypatch: pytest.MonkeyPatch, segment: str
) -> None:
    """A file no node at this depth holds still belongs to the architecture (req 9.7).

    ``setup.py``, ``conftest.py`` and ``main.go`` live directly in the repository root. Left
    unplaced they would be silently exempt from every node-level structural rule — a hole in
    coverage, not a ratchet difference — and requirement 9.7 would have no path to show for
    them. They are listed under the architecture itself, and under it only, so that the file
    -> node index of the change summary agrees with every record.
    """
    root = f"/ws/{segment}"
    install(monkeypatch, FakeUnderstand(db=shadow_db(segment, root)))
    document = worker.dispatch(
        "snapshot",
        snapshot_request(root=root, files=["main.py", "pkg/core.py"], kinds_by_scope=FILE_ONLY),
    )
    assert records(document)["main.py"]["archs"] == ["Directory Structure"]
    assert listing(document, "arch_nodes") == [
        {"path": "Directory Structure", "members": ["main.py"]},
        {"path": "Directory Structure/pkg", "members": ["pkg/core.py"]},
    ]


@pytest.mark.parametrize(
    "root",
    [
        pytest.param(f"{ANALYSIS_ROOT}/", id="a trailing separator"),
        pytest.param(f"{ANALYSIS_ROOT}///", id="several trailing separators"),
        pytest.param("\\ws\\after", id="a windows root"),
    ],
)
def test_snapshot_normalises_the_analysis_root_it_was_given(
    monkeypatch: pytest.MonkeyPatch, root: str
) -> None:
    """The root is compared against ``Ent.longname()`` as text, so its form has to be fixed.

    An unnormalised root matches nothing, every file falls back to ``relname``, and the answer
    is the silent, entirely green, zero-entity snapshot this task was rejected for.
    """
    canonical = set(records(snapshot(monkeypatch)))
    assert set(records(snapshot(monkeypatch, root=root))) == canonical


def test_snapshot_does_not_take_a_sibling_directory_for_the_analysis_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/ws/afterthought`` is not inside ``/ws/after``: the separator is part of the boundary.

    Without it the root would be stripped mid-name and the file keyed as ``hought/x.py``.
    """
    sibling = FakeEnt(
        path="sibling/x.py", qualified="/ws/afterthought/x.py", kind_path="python File"
    )
    inside = a_file("cli/app.py")
    top = FakeArch("Directory Structure", ents=[inside])
    install(monkeypatch, FakeUnderstand(db=FakeDb([top], entities={FILE_KIND: [sibling, inside]})))
    document = worker.dispatch(
        "snapshot",
        snapshot_request(files=["sibling/x.py", "cli/app.py"], kinds_by_scope=FILE_ONLY, depth=0),
    )
    assert set(records(document)) == {"sibling/x.py", "cli/app.py"}


def test_snapshot_refuses_an_analysis_root_that_names_no_file_of_the_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wrong root is a caller error, and it is the one that hides best.

    Requiring the root only removed the case of forgetting it. A root that names another
    directory resolves nothing, every requested file misses, and the answer is a valid, empty,
    green snapshot — indistinguishable from a change that really touched nothing. It is
    refused with an envelope naming what the database actually holds.
    """
    project = fake_project()
    install(monkeypatch, FakeUnderstand(db=project.db))
    error = envelope(worker.dispatch("snapshot", snapshot_request(root="/completely/wrong")))
    assert error["type"] == "AnalysisRootMismatch"
    assert "/completely/wrong" in error["message"]
    assert error["found"], "the envelope names the long names that were found"
    assert project.db.closed


def test_snapshot_accepts_any_root_for_a_database_that_holds_no_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An empty database resolves nothing because there is nothing to resolve; that is an
    # empty snapshot, not a caller error.
    top = FakeArch("Directory Structure")
    install(monkeypatch, FakeUnderstand(db=FakeDb([top], entities={FILE_KIND: []})))
    document = worker.dispatch("snapshot", snapshot_request(kinds_by_scope=FILE_ONLY))
    assert listing(document, "entities") == []


def test_snapshot_keeps_a_real_directory_that_happens_to_hold_every_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the inserted level is removed, never a directory the repository really has.

    A repository whose sources all live under one directory keeps that directory as a node;
    the two cases are told apart by whether the node's own name starts the paths of its files.
    """
    core = a_file("src/core.py")
    node = FakeArch("Directory Structure/src", ents=[core])
    root = FakeArch("Directory Structure", children=[node])
    install(monkeypatch, FakeUnderstand(db=FakeDb([root], entities={FILE_KIND: [core]})))
    document = worker.dispatch(
        "snapshot", snapshot_request(files=["src/core.py"], kinds_by_scope=FILE_ONLY)
    )
    assert listing(document, "arch_nodes") == [
        {"path": "Directory Structure/src", "members": ["src/core.py"]}
    ]


def test_snapshot_does_not_call_an_edge_crossing_when_one_end_has_no_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file directly in the analysis root belongs to no node at the configured depth.

    An unknown node is not a different node: claiming otherwise would report a layer
    violation for every top-level file of every repository.
    """
    app = a_file("cli/app.py")
    orphan = a_file("main.py")
    app.deps = {orphan: [object()]}
    orphan.deps_by = {app: [object()]}
    root = FakeArch(
        "Directory Structure", children=[FakeArch("Directory Structure/cli", ents=[app])]
    )
    install(monkeypatch, FakeUnderstand(db=FakeDb([root], entities={FILE_KIND: [app, orphan]})))
    document = worker.dispatch(
        "snapshot", snapshot_request(files=["cli/app.py"], kinds_by_scope=FILE_ONLY)
    )
    assert listing(document, "file_edges") == [
        {"src": "cli/app.py", "dst": "main.py", "refs": 1, "crosses_arch": False}
    ]


def test_snapshot_takes_the_first_architecture_node_of_a_file_in_sorted_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A user-defined architecture may put one file in several nodes at the same depth.

    Understand's walk order is not the repository's, so the node a file is reported under —
    and every ``crosses_arch`` derived from it — is chosen by sorted order, or the two sides
    of a change could disagree about a file that never moved.
    """
    app = a_file("cli/app.py")
    other = a_file("util/text.py")
    app.deps = {other: [object()]}
    other.deps_by = {app: [object()]}
    root = FakeArch(
        "Directory Structure",
        children=[
            FakeArch("Directory Structure/zeta", ents=[app, other]),
            FakeArch("Directory Structure/alpha", ents=[app]),
        ],
    )
    install(monkeypatch, FakeUnderstand(db=FakeDb([root], entities={FILE_KIND: [app, other]})))
    document = worker.dispatch(
        "snapshot", snapshot_request(files=["cli/app.py"], kinds_by_scope=FILE_ONLY)
    )
    assert records(document)["cli/app.py"]["archs"] == [
        "Directory Structure/alpha",
        "Directory Structure/zeta",
    ]
    assert listing(document, "file_edges")[0]["crosses_arch"] is True


def test_snapshot_orders_the_entity_records_by_their_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The answer is a document a human diffs and a caller may cache, so its order is fixed.

    Records are collected scope by scope, which is not the order ``ProjectSnapshot``
    serializes them in; sorting by the key token makes the two agree.
    """
    document = snapshot(monkeypatch, files=["cli/app.py", "util/text.py", "native/util.c"])
    entities = listing(document, "entities")
    tokens = [EntityKey.model_validate(record["ref"]["key"]).token for record in entities]
    assert len({record["ref"]["key"]["scope"] for record in entities}) == 3
    assert tokens == sorted(tokens)


def test_snapshot_ignore_regexes_are_case_sensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Req 3.6 is one ignore grammar, and ``analysis.population.IgnoreFilter`` compiles it
    without flags: a pattern that matched here and not there would exclude an entity from the
    snapshot while the evaluators still counted it as ignored."""
    document = snapshot(monkeypatch, ignore={"routine": [r"^APP\.BUILD_"], "file": ["^CLI/"]})
    assert "app.build_parser" in records(document)
    assert "cli/app.py" in records(document)
