"""The two dependency graphs the snapshot carries: import time, and calls (req 9.2, 9.6).

Which file dependencies run when a module is imported is a property of the module's text --
``if TYPE_CHECKING:`` and function-local imports defer -- so the worker parses the source and
counts, per edge, how many references the import executes. The call graph is read from
Understand's call references over the whole project, with the resolution rate that says how
much of it Understand could bind.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

import pytest
from api_fakes import (
    FakeArch,
    FakeDb,
    FakeEnt,
    FakeUnderstand,
    install,
)
from worker_projects import (
    ANALYSIS_ROOT,
    CLASS_KINDS,
    FILE_KIND,
    FILE_ONLY,
    ROUTINE_KINDS,
    a_class,
    a_dep_ref,
    a_file,
    a_routine,
    a_variable,
    listing,
    mapping,
    records,
    snapshot,
    snapshot_request,
)

from scitools_hook.models.snapshot import EntityKey, ProjectSnapshot
from scitools_hook.understand import worker

# --- snapshot: which file dependencies run when the module is imported --------------
#
# Understand's `depends()` counts every reference regardless of guard or scope, so a
# `TYPE_CHECKING` import and a function-local one look exactly like a module-level one. They
# are not: neither runs when the module is loaded, so neither can close an import cycle --
# and the second is the standard idiom for *breaking* one. The worker measures the split; the
# cycle rule reads it; every other structural rule keeps counting every reference.


DEFERRING_SOURCE: Final = '''"""A module with one import of each kind."""

from typing import TYPE_CHECKING

import runtime

if TYPE_CHECKING:
    from typedep import Thing


def go(value):
    from localdep import helper

    return helper(value, Thing)
'''
"""Line 5 is a module-level import, line 7 a ``TYPE_CHECKING`` one, line 12 a local one."""


@dataclass
class ImportProject:
    """A Python module with one import of each kind, plus the files it reaches."""

    db: FakeDb
    mod: FakeEnt
    runtime: FakeEnt
    typedep: FakeEnt
    localdep: FakeEnt


def import_project(source: str | None = DEFERRING_SOURCE) -> ImportProject:
    """``mod.py`` importing three modules three different ways."""
    mod = a_file("mod.py", source=source)
    runtime = a_file("runtime.py")
    typedep = a_file("typedep.py")
    localdep = a_file("localdep.py")
    mod.deps = {
        runtime: [a_dep_ref(runtime, 5, "python Import")],
        typedep: [
            a_dep_ref(typedep, 8, "python Import From"),
            a_dep_ref(typedep, 15, "python Use"),
        ],
        localdep: [
            a_dep_ref(localdep, 12, "python Import"),
            a_dep_ref(localdep, 14, "python Call"),
        ],
    }
    top = FakeArch("Directory Structure", children=[FakeArch("Directory Structure/mod")])
    db = FakeDb([top], entities={FILE_KIND: [mod, runtime, typedep, localdep]})
    return ImportProject(db, mod, runtime, typedep, localdep)


def import_time_of(document: Mapping[str, Any]) -> dict[str, object]:
    """The published import-time count per destination; a missing key reads as ``None``."""
    return {
        edge["dst"]: edge.get("import_time")
        for edge in listing(document, "file_edges")
        if edge["src"] == "mod.py"
    }


def import_snapshot(
    monkeypatch: pytest.MonkeyPatch, project: ImportProject | None = None
) -> dict[str, Any]:
    """Run ``snapshot`` over the import project and return the document."""
    found = project or import_project()
    install(monkeypatch, FakeUnderstand(db=found.db))
    result = worker.dispatch(
        "snapshot", snapshot_request(files=["mod.py"], kinds_by_scope=FILE_ONLY, depth=1)
    )
    assert "error" not in result, result
    return result


def test_snapshot_counts_a_module_level_import_as_import_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert import_time_of(import_snapshot(monkeypatch))["runtime.py"] == 1


def test_snapshot_does_not_count_a_type_checking_import_as_import_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `if TYPE_CHECKING:` is erased by the interpreter; there is no reading in which the
    # import runs. The `Use` at line 15 is not an import either, and in Python a name from
    # another module is unreachable without one.
    assert import_time_of(import_snapshot(monkeypatch))["typedep.py"] == 0


def test_snapshot_does_not_count_a_function_local_import_as_import_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The idiom for breaking an import cycle. Counting it closes the cycle it breaks.
    assert import_time_of(import_snapshot(monkeypatch))["localdep.py"] == 0


def test_snapshot_still_counts_every_reference_of_a_deferred_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The coupling is real and `refs` is what fan, layers and the new-dependency limit read;
    # only the cycle rule reads `import_time`.
    edges = {
        edge["dst"]: edge["refs"] for edge in listing(import_snapshot(monkeypatch), "file_edges")
    }
    assert edges == {"runtime.py": 1, "typedep.py": 2, "localdep.py": 2}


def test_snapshot_leaves_import_time_off_a_cpp_file_edge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The language guard, and the reason it is not optional.

    A C++ ``#include`` produces ``Include``/``Type``/``Use``/``Init``/``Return`` references
    and no import reference at all (measured), so a language-blind rule would score every C++
    edge zero and switch off C++ cycle detection entirely. The field must be absent.
    """
    header = a_file("lib.h", language="C++", source="#pragma once\nint f();\n")
    unit = a_file("lib.cpp", language="C++", source='#include "lib.h"\nint f() { return 1; }\n')
    unit.deps = {header: [a_dep_ref(header, 1, "c Include")]}
    top = FakeArch("Directory Structure", children=[FakeArch("Directory Structure/lib")])
    install(monkeypatch, FakeUnderstand(db=FakeDb([top], entities={FILE_KIND: [unit, header]})))
    document = worker.dispatch(
        "snapshot", snapshot_request(files=["lib.cpp"], kinds_by_scope=FILE_ONLY, depth=1)
    )
    assert listing(document, "file_edges") == [
        {"src": "lib.cpp", "dst": "lib.h", "refs": 1, "crosses_arch": False}
    ]


def test_snapshot_leaves_import_time_off_a_python_file_it_cannot_parse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An unparsable module is "not measured", which every consumer reads as the older, louder
    # behaviour. Scoring it zero would silently switch cycle detection off for that file.
    document = import_snapshot(monkeypatch, import_project(source="def broken(:\\n"))
    assert import_time_of(document) == {
        "runtime.py": None,
        "typedep.py": None,
        "localdep.py": None,
    }


def test_snapshot_leaves_import_time_off_a_file_whose_source_it_cannot_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `Ent.contents()` raising must cost this file its measurement and nothing else.
    document = import_snapshot(monkeypatch, import_project(source=None))
    assert set(import_time_of(document).values()) == {None}
    assert len(listing(document, "file_edges")) == 3


def test_snapshot_counts_an_import_in_an_else_branch_of_a_type_checking_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``else:`` under ``if TYPE_CHECKING:`` is the branch that runs, and must still count."""
    source = (
        "from typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    pass\n"
        "else:\n    import runtime\n"
    )
    project = import_project(source=source)
    project.mod.deps = {project.runtime: [a_dep_ref(project.runtime, 6, "python Import")]}
    assert import_time_of(import_snapshot(monkeypatch, project))["runtime.py"] == 1


def test_snapshot_counts_an_import_a_decorator_makes_at_import_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A decorator runs when the module is loaded; it is above the body and not in its span."""
    source = "import runtime\n\n\n@runtime.register\ndef go():\n    return 1\n"
    project = import_project(source=source)
    project.mod.deps = {
        project.runtime: [
            a_dep_ref(project.runtime, 1, "python Import"),
            a_dep_ref(project.runtime, 4, "python Use"),
        ]
    }
    assert import_time_of(import_snapshot(monkeypatch, project))["runtime.py"] == 1


def test_snapshot_does_not_count_an_import_inside_a_nested_function(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = (
        "def outer():\n    def inner():\n        import runtime\n\n"
        "        return runtime\n\n    return inner\n"
    )
    project = import_project(source=source)
    project.mod.deps = {project.runtime: [a_dep_ref(project.runtime, 3, "python Import")]}
    assert import_time_of(import_snapshot(monkeypatch, project))["runtime.py"] == 0


def test_snapshot_counts_an_import_in_a_class_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A class body runs at import; only a *function* body defers."""
    source = "class Holder:\n    import runtime\n\n    value = 1\n"
    project = import_project(source=source)
    project.mod.deps = {project.runtime: [a_dep_ref(project.runtime, 2, "python Import")]}
    assert import_time_of(import_snapshot(monkeypatch, project))["runtime.py"] == 1


def test_snapshot_does_not_count_a_not_type_checking_guard_as_erased(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``if not TYPE_CHECKING:`` guards the branch that *does* run; reading it as erased
    would drop a real import-time dependency, which is the direction of error to avoid."""
    source = "from typing import TYPE_CHECKING\n\nif not TYPE_CHECKING:\n    import runtime\n"
    project = import_project(source=source)
    project.mod.deps = {project.runtime: [a_dep_ref(project.runtime, 4, "python Import")]}
    assert import_time_of(import_snapshot(monkeypatch, project))["runtime.py"] == 1


def test_snapshot_leaves_import_time_off_a_class_edge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A class has no import of its own; the construct is a property of a module.
    document = snapshot(monkeypatch)
    assert all("import_time" not in edge for edge in listing(document, "class_edges"))


def test_snapshot_leaves_import_time_off_an_architecture_edge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Architecture edges come from `Arch.depends()`; this is what keeps a real architecture
    # cycle firing while the file-level reduction removes a false one below it.
    document = snapshot(monkeypatch)
    assert all("import_time" not in edge for edge in listing(document, "arch_edges"))


# --- snapshot: the call graph -----------------------------------------------------
#
# A project of its own, because the call graph needs shapes `fake_project` deliberately does
# not have: a call that resolves, one that lands on a class, one that lands on an attribute
# and therefore on nothing callable, a self-call, a library call, and a routine outside the
# requested files so that the bound on what is published is observable.


CALL_ROOT: Final = ANALYSIS_ROOT


def routine_token(path: str, longname: str, parameters: str | None = "argv") -> str:
    """The ``EntityKey.token`` the worker writes for one routine, as the endpoints carry it."""
    return json.dumps(["routine", path, longname, parameters], separators=(",", ":"))


@dataclass
class CallProject:
    """The fake project the call-graph tests drive, with every entity a test names."""

    db: FakeDb
    main: FakeEnt
    helper: FakeEnt
    init: FakeEnt
    leaf: FakeEnt
    run: FakeEnt
    clamp: FakeEnt
    scale: FakeEnt
    engine: FakeEnt
    bare: FakeEnt


def call_project() -> CallProject:
    """Five Python routines, two C++ ones, two classes, and one call site of every shape.

    ``app.py`` is the requested file. ``app.main`` calls a sibling routine, constructs
    ``core.Engine`` and dispatches through an attribute Understand could not bind;
    ``app.helper`` calls into ``util.py`` and calls itself; ``core.run`` calls back into
    ``app.py`` from outside the requested files, so the published bound can be seen.
    """
    app = a_file("app.py")
    core = a_file("core.py")
    util = a_file("util.py")
    native = a_file("native.c", language="C++")
    injected = a_file("/opt/scitools/conf/understand/python/python3/builtins.py", lib="Standard")

    leaf = a_routine("util.leaf", util, values={"CyclomaticStrict": None})
    stub = a_routine("builtins.abs", injected, lib="Standard")
    leaf.refs_to = [stub]

    engine = a_class("core.Engine", core)
    init = a_routine("core.Engine.__init__", core, values={"CyclomaticStrict": 2})
    init.simple = "__init__"
    init.refs_to = [leaf]
    engine.members = [init]

    bare = a_class("util.Bare", util)

    helper = a_routine("app.helper", app, values={"CyclomaticStrict": 4})
    helper.refs_to = [leaf, helper]

    # `self.dispatch(...)`: Understand binds the call to the *attribute*, not to a routine.
    attribute = FakeEnt(
        qualified="app.Runner.dispatch",
        kind_path="python Variable Attribute Instance",
        simple="dispatch",
        lang="Python",
        container=app,
    )
    main = a_routine("app.main", app, values={"CyclomaticStrict": 3})
    main.refs_to = [helper, engine, attribute]

    run = a_routine("core.run", core, values={"CyclomaticStrict": 9})
    run.refs_to = [main, bare]

    scale = a_routine("scale", native, values={"CyclomaticStrict": 1})
    parameter = a_variable("clamp::fp", native, kind="c Parameter")
    clamp = a_routine("clamp", native, values={"CyclomaticStrict": 5})
    clamp.refs_to = [scale, parameter]

    top = FakeArch("Directory Structure", children=[FakeArch("Directory Structure/app")])
    db = FakeDb(
        [top],
        entities={
            FILE_KIND: [app, core, util, native, injected],
            ROUTINE_KINDS: [main, helper, init, leaf, run, clamp, scale, stub],
            CLASS_KINDS: [engine, bare],
        },
    )
    return CallProject(db, main, helper, init, leaf, run, clamp, scale, engine, bare)


def call_snapshot(monkeypatch: pytest.MonkeyPatch, **overrides: object) -> dict[str, Any]:
    """Run ``snapshot`` against the call-graph project and return its document."""
    install(monkeypatch, FakeUnderstand(db=call_project().db))
    request = snapshot_request(files=["app.py"], depth=1, **overrides)
    result = worker.dispatch("snapshot", request)
    assert "error" not in result, result
    return result


def call_edges(document: Mapping[str, Any]) -> list[tuple[str, str, int]]:
    """The call edges as ``(caller long name, callee long name, call sites)`` triples."""
    return [
        (
            json.loads(edge["src"])[2],
            json.loads(edge["dst"])[2],
            edge["refs"],
        )
        for edge in listing(document, "call_edges")
    ]


def call_nodes(document: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """The call nodes keyed by the long name of the routine each one stands for."""
    return {json.loads(node["node"])[2]: node for node in listing(document, "call_nodes")}


def test_snapshot_reports_a_call_between_two_routines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The base case: one routine calling another is one edge between their key tokens.
    document = call_snapshot(monkeypatch)
    assert ("app.main", "app.helper", 1) in call_edges(document)


def test_snapshot_names_call_edge_endpoints_by_entity_key_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The endpoints are the same reversible form the class edges carry, so `EntityKey`
    # decodes them and no finding ever shows a raw token.
    edges = listing(call_snapshot(monkeypatch), "call_edges")
    assert {edge["src"] for edge in edges} <= {
        routine_token("app.py", "app.main"),
        routine_token("app.py", "app.helper"),
        routine_token("core.py", "core.Engine.__init__"),
    }
    for edge in edges:
        assert EntityKey.from_token(edge["src"]).scope == "routine"


def test_snapshot_counts_a_call_edge_by_its_call_sites(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `refs` on a call edge counts call sites, as it counts references everywhere else.
    project = call_project()
    project.main.refs_to = [project.helper, project.helper, project.helper]
    install(monkeypatch, FakeUnderstand(db=project.db))
    document = worker.dispatch("snapshot", snapshot_request(files=["app.py"], depth=1))
    assert ("app.main", "app.helper", 3) in call_edges(document)


def test_snapshot_maps_a_call_on_a_class_to_its_constructor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `Engine()` is a call reference to the class; a graph that only followed routines would
    # lose every construction — a quarter of the call sites of a measured real project.
    assert ("app.main", "core.Engine.__init__", 1) in call_edges(call_snapshot(monkeypatch))


def test_snapshot_counts_a_call_on_a_class_without_a_constructor_as_external(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `util.Bare` declares no `__init__`, so `core.run` gains no edge and the call site is
    # external rather than unresolved: it did bind to something callable.
    document = call_snapshot(monkeypatch)
    assert not [edge for edge in call_edges(document) if edge[1] == "util.Bare"]
    assert mapping(document, "call_resolution")["Python"]["external"] == 2


def test_snapshot_does_not_make_a_self_call_an_edge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A routine calling itself is not a cycle, for the same reason a file that references
    # itself is not; the call site is still counted resolved, because it did bind.
    assert not [edge for edge in call_edges(call_snapshot(monkeypatch)) if edge[0] == edge[1]]


def test_snapshot_counts_a_call_that_bound_to_nothing_callable_as_unresolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `self.dispatch(...)` binds to a `Variable Attribute Instance`. This is the false
    # negative the whole confidence report exists for.
    document = call_snapshot(monkeypatch)
    assert mapping(document, "call_resolution")["Python"]["unresolved"] == 1
    assert call_nodes(document)["app.main"]["unresolved_calls"] == 1


def test_snapshot_gives_a_routine_with_no_blind_spot_a_zero_unresolved_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert call_nodes(call_snapshot(monkeypatch))["app.helper"]["unresolved_calls"] == 0


def test_snapshot_counts_a_call_into_the_standard_library_as_external(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `util.leaf` calls `builtins.abs`, which is callable and is not a node of this graph.
    document = call_snapshot(monkeypatch)
    assert not [edge for edge in call_edges(document) if edge[1] == "builtins.abs"]
    assert mapping(document, "call_resolution")["Python"]["resolved"] == 6


def test_snapshot_reports_call_resolution_per_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # C++ resolves a direct call and cannot resolve a call through a function pointer; the
    # two languages must not be averaged into one figure that hides the weaker one.
    resolution = mapping(call_snapshot(monkeypatch), "call_resolution")
    assert resolution["C++"] == {"resolved": 1, "external": 0, "unresolved": 1}
    assert resolution["Python"] == {"resolved": 6, "external": 2, "unresolved": 1}


def test_snapshot_counts_call_resolution_over_the_whole_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `clamp` and `scale` live in a file the request never named, and their call sites still
    # count: the rate describes the substrate, not the change.
    document = call_snapshot(monkeypatch)
    assert "clamp" not in call_nodes(document)
    assert mapping(document, "call_resolution")["C++"]["resolved"] == 1


def test_snapshot_bounds_the_published_call_graph_to_what_the_request_reaches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `core.run` calls into `app.py` but nothing in `app.py` reaches it, so it is neither a
    # node nor the source of an edge — the same bound the file edges carry (req 4.11).
    document = call_snapshot(monkeypatch)
    assert set(call_nodes(document)) == {
        "app.main",
        "app.helper",
        "core.Engine.__init__",
        "util.leaf",
    }
    assert not [edge for edge in call_edges(document) if edge[0] == "core.run"]


def test_snapshot_publishes_a_node_for_every_routine_the_request_reaches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A reach rule sums complexity over routines outside the requested files, so their nodes
    # have to be published even though no entity record is.
    document = call_snapshot(monkeypatch)
    assert call_nodes(document)["util.leaf"]["node"] == routine_token("util.py", "util.leaf")
    assert "util.leaf" not in records(document)


def test_snapshot_reports_the_complexity_of_every_call_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nodes = call_nodes(call_snapshot(monkeypatch))
    assert nodes["app.main"]["complexity"] == 3.0
    assert nodes["core.Engine.__init__"]["complexity"] == 2.0


def test_snapshot_leaves_an_unmeasured_complexity_null_rather_than_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A zero is a claim the database never made, and a reach rule has to be able to say how
    # much of what it summed was unmeasured.
    assert call_nodes(call_snapshot(monkeypatch))["util.leaf"]["complexity"] is None


def test_snapshot_reads_the_complexity_metric_the_request_never_asked_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The reach rule needs `CyclomaticStrict` for routines no threshold judges, so the call
    # pass reads it itself rather than depending on `metrics_by_scope`.
    document = call_snapshot(monkeypatch, metrics_by_scope={})
    assert call_nodes(document)["app.main"]["complexity"] == 3.0


def test_snapshot_call_graph_validates_into_a_project_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parsed = ProjectSnapshot.model_validate(call_snapshot(monkeypatch))
    assert parsed.call_graph_holds == {
        routine_token("app.py", "app.main"),
        routine_token("app.py", "app.helper"),
        routine_token("core.py", "core.Engine.__init__"),
        routine_token("util.py", "util.leaf"),
    }
    assert parsed.call_resolution["Python"].total == 9
    assert parsed.call_resolution["Python"].bound == pytest.approx(8 / 9)


def test_snapshot_leaves_out_the_call_graph_when_no_edges_were_asked_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The affected-set pass asks for no edges; it must not pay for the call walk either.
    document = call_snapshot(monkeypatch, include_edges=False)
    assert document["call_edges"] == []
    assert document["call_nodes"] == []
    assert document["call_resolution"] == {}


def test_snapshot_call_edges_and_nodes_are_sorted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = call_snapshot(monkeypatch)
    edges = listing(document, "call_edges")
    nodes = listing(document, "call_nodes")
    assert edges == sorted(edges, key=lambda edge: (edge["src"], edge["dst"]))
    assert nodes == sorted(nodes, key=lambda node: node["node"])


def test_snapshot_publishes_a_whole_cycle_that_starts_outside_the_requested_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The forward closure is what makes a cycle rule sound on a bounded extraction.

    A routine on a cycle through a requested routine is forward-reachable from it by
    definition, so bounding the published graph to the forward closure cannot cut a cycle in
    half -- which is the only reason a cycle found in the published subgraph is a cycle in the
    project. Here neither member of the cycle lives in the requested file.
    """
    project = call_project()
    outer = a_routine("core.cycle_a", project.run.container, values={"CyclomaticStrict": 1})
    inner = a_routine("core.cycle_b", project.run.container, values={"CyclomaticStrict": 1})
    outer.refs_to = [inner]
    inner.refs_to = [outer]
    project.main.refs_to = [*project.main.refs_to, outer]
    project.db._entities[ROUTINE_KINDS] = [*project.db._entities[ROUTINE_KINDS], outer, inner]
    install(monkeypatch, FakeUnderstand(db=project.db))
    document = worker.dispatch("snapshot", snapshot_request(files=["app.py"], depth=1))
    assert {"core.cycle_a", "core.cycle_b"} <= set(call_nodes(document))
    assert ("core.cycle_a", "core.cycle_b", 1) in call_edges(document)
    assert ("core.cycle_b", "core.cycle_a", 1) in call_edges(document)


def test_snapshot_leaves_an_ignored_routine_out_of_the_call_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An ignored entity is never remembered, so it is neither a node nor an edge endpoint
    # (req 3.6) — and the call site that named it is then external, not resolved.
    document = call_snapshot(monkeypatch, ignore={"routine": ["^app\\.helper$"]})
    assert "app.helper" not in call_nodes(document)
    assert not [edge for edge in call_edges(document) if "app.helper" in edge[:2]]
