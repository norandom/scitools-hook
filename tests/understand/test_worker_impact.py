"""The ``impact`` and ``graphs`` operations (req 9.4, 9.5): the blast radius and the drawings.

``impact`` walks reverse references from the changed entities to a depth and answers who
reaches them; ``graphs`` asks Understand to draw the affected entities. The reference project
gives three entities a caller each; the opaque project holds C++ entities the gate cannot key,
which the walk passes through and never reports.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

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
    CLASS_KINDS,
    FILE_KIND,
    KINDS,
    ROUTINE_KINDS,
    a_class,
    a_file,
    a_namespace,
    a_routine,
    a_variable,
)

from scitools_hook.models.change import GraphFile, ImpactSet
from scitools_hook.models.snapshot import EntityKey
from scitools_hook.understand import worker

# --- impact and graphs: the fake project ----------------------------------------


@dataclass
class ReferenceProject:
    """A fake database wired for the reverse walk: who references whom, and from where."""

    db: FakeDb
    app: FakeEnt
    text: FakeEnt
    main: FakeEnt
    check: FakeEnt
    wrap: FakeEnt
    runner: FakeEnt


def reference_project() -> ReferenceProject:
    """Two files, three routines and a class, with the reverse references measured live.

    ``text.wrap_lines`` is referenced by ``app.main`` **and** by the file that imports it
    (both shapes were measured against the sample project); ``app.main`` is referenced by
    ``app.check_command``, which in turn is referenced by ``app.main`` again — the cycle that
    makes deduplication observable. A routine of a vendored library file also references
    ``text.wrap_lines`` and must never reach the answer.
    """
    app = a_file("cli/app.py")
    text = a_file("util/text.py")
    vendored = a_file("vendor/six.py", lib="Standard")
    main = a_routine("app.main", app)
    check = a_routine("app.check_command", app)
    wrap = a_routine("text.wrap_lines", text)
    runner = a_class("app.Runner", app)
    stub = a_routine("six.compat", vendored)
    wrap.refs_by = [main, app, stub]
    main.refs_by = [check]
    check.refs_by = [main]
    runner.refs_by = [main]
    app.refs_by = [text]
    for ent in (app, text, runner):
        ent.drawable = ("Butterfly", "Depends On", "Depended On By")
    root = FakeArch("Directory Structure", ents=[app, text])
    db = FakeDb(
        [root],
        entities={
            FILE_KIND: [app, text, vendored],
            ROUTINE_KINDS: [main, check, wrap, stub],
            CLASS_KINDS: [runner],
        },
    )
    return ReferenceProject(db, app, text, main, check, wrap, runner)


def routine_ref(longname: str, path: str) -> EntityKey:
    """The key of a routine of the reference project; every fake routine takes ``argv``."""
    return EntityKey(scope="routine", path=path, longname=longname, parameters="argv")


WRAP_KEY: Final = routine_ref("text.wrap_lines", "util/text.py")
MAIN_KEY: Final = routine_ref("app.main", "cli/app.py")
CHECK_KEY: Final = routine_ref("app.check_command", "cli/app.py")
APP_KEY: Final = EntityKey(scope="file", path="cli/app.py", longname="cli/app.py")
TEXT_KEY: Final = EntityKey(scope="file", path="util/text.py", longname="util/text.py")
RUNNER_KEY: Final = EntityKey(scope="class", path="cli/app.py", longname="app.Runner")


def wire_keys(keys: object) -> object:
    """Entity keys in the wire shape a caller sends, leaving a malformed value untouched."""
    if not isinstance(keys, list):
        return keys
    return [key.model_dump() if isinstance(key, EntityKey) else key for key in keys]


def impact_request(**overrides: object) -> dict[str, Any]:
    """A well-formed ``impact`` request; a test overrides only the field it is about."""
    request: dict[str, Any] = {
        "db": "/cache/after.und",
        "root": ANALYSIS_ROOT,
        "kinds_by_scope": dict(KINDS),
        "keys": [WRAP_KEY],
        "depth": 2,
    }
    request.update(overrides)
    request["keys"] = wire_keys(request["keys"])
    return request


def impact(monkeypatch: pytest.MonkeyPatch, **overrides: object) -> dict[str, Any]:
    """Run ``impact`` against the reference project and return its document."""
    install(monkeypatch, FakeUnderstand(db=reference_project().db))
    result = worker.dispatch("impact", impact_request(**overrides))
    assert "error" not in result, result
    return result


def impact_of(document: Mapping[str, Any], key: EntityKey) -> dict[str, Any]:
    """One entity's impact set, validated through the model the summary consumes."""
    sets: dict[str, Any] = document["impact"]
    assert key.token in sets, f"{key.token} missing from {sorted(sets)}"
    ImpactSet.model_validate(sets[key.token])
    found: dict[str, Any] = sets[key.token]
    return found


def at_depth(document: Mapping[str, Any], key: EntityKey, depth: str) -> list[str]:
    """The long names reported at one depth of one entity's impact set, in answer order."""
    by_depth = impact_of(document, key)["by_depth"]
    refs: list[dict[str, Any]] = by_depth.get(depth, [])
    return [ref["key"]["longname"] for ref in refs]


def warnings_of(document: Mapping[str, Any]) -> list[str]:
    """The warnings of an ``impact`` or ``graphs`` answer."""
    found: list[str] = document["warnings"]
    return found


# --- impact ---------------------------------------------------------------------


def test_impact_reports_the_entities_that_reference_the_key_at_depth_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Requirement 9.5: the blast radius of one routine, one level out.
    document = impact(monkeypatch, depth=1)
    assert at_depth(document, WRAP_KEY, "1") == ["cli/app.py", "app.main"]


def test_impact_describes_every_referencing_entity_the_way_the_summary_shows_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refs: list[dict[str, Any]] = impact_of(impact(monkeypatch, depth=1), WRAP_KEY)["by_depth"]["1"]
    assert refs[1] == {
        "key": MAIN_KEY.model_dump(),
        "kind": "python Function",
        "name": "main",
        "line": 9,
    }
    assert refs[0]["key"] == APP_KEY.model_dump()
    assert refs[0]["line"] is None


def test_impact_walks_the_reverse_references_transitively(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = impact(monkeypatch)
    assert at_depth(document, WRAP_KEY, "1") == ["cli/app.py", "app.main"]
    assert at_depth(document, WRAP_KEY, "2") == ["util/text.py", "app.check_command"]


def test_impact_stops_at_the_requested_depth(monkeypatch: pytest.MonkeyPatch) -> None:
    document = impact(monkeypatch, depth=1)
    assert at_depth(document, WRAP_KEY, "2") == []
    assert impact_of(document, WRAP_KEY)["total"] == 2


def test_impact_never_reports_an_entity_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    # `app.main` calls `app.check_command`, which calls `app.main` back: an entity already
    # seen at depth 1 must not reappear deeper, or every count is inflated by the cycle.
    document = impact(monkeypatch, keys=[MAIN_KEY], depth=3)
    assert at_depth(document, MAIN_KEY, "1") == ["app.check_command"]
    assert at_depth(document, MAIN_KEY, "2") == []
    assert impact_of(document, MAIN_KEY)["total"] == 1


def test_impact_never_reports_the_entity_it_was_asked_about(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = impact(monkeypatch, keys=[CHECK_KEY], depth=4)
    reported = [
        name for depth in ("1", "2", "3", "4") for name in at_depth(document, CHECK_KEY, depth)
    ]
    assert "app.check_command" not in reported
    assert reported == ["app.main"]


def test_impact_counts_every_depth(monkeypatch: pytest.MonkeyPatch) -> None:
    document = impact(monkeypatch)
    assert impact_of(document, WRAP_KEY)["total"] == 4


def test_impact_leaves_out_a_reference_from_a_library_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `six.compat` sits in a vendored file Understand marks as a library: it is not the
    # reviewer's blast radius and it is not keyable, so it never reaches the answer.
    document = impact(monkeypatch, depth=3)
    reported = [name for depth in ("1", "2", "3") for name in at_depth(document, WRAP_KEY, depth)]
    assert "six.compat" not in reported


def test_impact_orders_every_depth_by_entity_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # Understand's walk order is not the repository's; two runs of the same request must
    # produce the same document, or the summary and its diff churn.
    project = reference_project()
    project.wrap.refs_by = list(reversed(project.wrap.refs_by))
    install(monkeypatch, FakeUnderstand(db=project.db))
    document = worker.dispatch("impact", impact_request(depth=1))
    assert at_depth(document, WRAP_KEY, "1") == ["cli/app.py", "app.main"]


def test_impact_answers_for_every_key_it_was_given(monkeypatch: pytest.MonkeyPatch) -> None:
    document = impact(monkeypatch, keys=[WRAP_KEY, RUNNER_KEY], depth=1)
    assert at_depth(document, WRAP_KEY, "1") == ["cli/app.py", "app.main"]
    assert at_depth(document, RUNNER_KEY, "1") == ["app.main"]


def test_impact_of_an_entity_nothing_references_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = impact(monkeypatch, keys=[TEXT_KEY], depth=2)
    assert impact_of(document, TEXT_KEY) == {"by_depth": {}, "total": 0}
    assert warnings_of(document) == []


def test_impact_at_depth_zero_reports_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    document = impact(monkeypatch, depth=0)
    assert impact_of(document, WRAP_KEY) == {"by_depth": {}, "total": 0}


def test_impact_warns_about_a_key_the_database_does_not_hold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A routine deleted by the change is asked about against the after database: that is a
    # warning and an empty set, never an envelope, or the whole summary is lost with it.
    missing = routine_ref("app.gone", "cli/app.py")
    document = impact(monkeypatch, keys=[missing, WRAP_KEY], depth=1)
    assert impact_of(document, missing) == {"by_depth": {}, "total": 0}
    assert at_depth(document, WRAP_KEY, "1") == ["cli/app.py", "app.main"]
    assert len(warnings_of(document)) == 1
    assert "app.gone" in warnings_of(document)[0]


def test_impact_closes_the_database(monkeypatch: pytest.MonkeyPatch) -> None:
    project = reference_project()
    install(monkeypatch, FakeUnderstand(db=project.db))
    worker.dispatch("impact", impact_request())
    assert project.db.closed


def test_impact_closes_the_database_when_the_api_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = reference_project()
    project.wrap.refs_error = "DBCorrupt: bad database file"
    install(monkeypatch, FakeUnderstand(db=project.db))
    error = envelope(worker.dispatch("impact", impact_request()))
    assert error["type"] == "DBCorrupt"
    assert project.db.closed


def test_impact_refuses_an_analysis_root_that_names_no_file_of_the_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The 6.2 envelope: a wrong root resolves nothing, so every key would come back as a
    # warning and the reviewer would read an empty blast radius as "nothing depends on it".
    install(monkeypatch, FakeUnderstand(db=reference_project().db))
    error = envelope(worker.dispatch("impact", impact_request(root="/completely/wrong")))
    assert error["type"] == "AnalysisRootMismatch"


@pytest.mark.parametrize(
    ("overrides", "expected_key"),
    [
        pytest.param({"db": ""}, "db", id="empty database path"),
        pytest.param({"root": ""}, "root", id="empty analysis root"),
        pytest.param({"kinds_by_scope": {}}, "kinds_by_scope", id="no kind strings"),
        pytest.param({"keys": "all"}, "keys", id="keys not a list"),
        pytest.param({"keys": [{"scope": "routine"}]}, "keys", id="key missing fields"),
        pytest.param(
            {"keys": [{**MAIN_KEY.model_dump(), "path": 7}]}, "keys", id="path not a string"
        ),
        pytest.param(
            {"keys": [{**MAIN_KEY.model_dump(), "parameters": 7}]}, "keys", id="bad parameters"
        ),
        pytest.param({"depth": -1}, "depth", id="negative depth"),
        pytest.param({"depth": "two"}, "depth", id="depth not an integer"),
    ],
)
def test_impact_rejects_a_malformed_request_before_opening_a_database(
    monkeypatch: pytest.MonkeyPatch, overrides: dict[str, object], expected_key: str
) -> None:
    api = FakeUnderstand(db=reference_project().db)
    install(monkeypatch, api)
    error = envelope(worker.dispatch("impact", impact_request(**overrides)))
    assert error["type"] == "BadRequest"
    assert expected_key in error["message"]
    assert api.opened == []


# --- impact: entities the gate cannot key, walked through but never reported ------


@dataclass
class OpaqueProject:
    """A C++ shape measured on a real database: the only referencer of a type is a variable."""

    db: FakeDb
    shapes: FakeEnt
    box: FakeEnt
    area: FakeEnt
    outer: FakeEnt
    local: FakeEnt
    vendored_local: FakeEnt
    method: FakeEnt


def opaque_project() -> OpaqueProject:
    """``Box`` is referenced only by ``box``, a local no scope of this gate can key.

    Measured live on a purpose-built C++ database: ``class Box`` used as ``Box box(3);
    box.area();`` carries exactly one reference back — ``C Typedby`` to a ``C Object Local``
    — and that local's own ``C Useby`` names the routine. The same shape holds for a struct
    passed as a parameter and for a class held as a member. A second local sits in a vendored
    library file: walking through *that* would join two unrelated parts of a project through
    Understand's own stubs, so it must be refused.
    """
    shapes = a_file("native/shapes.cpp", language="C++")
    vendored = a_file("vendor/six.py", lib="Standard")
    box = a_class("Box", shapes)
    area = a_routine("box_area", shapes)
    outer = a_routine("outer", shapes)
    local = a_variable("box_area::box", shapes)
    vendored_local = a_variable("six::cache", vendored)
    namespace = a_namespace("lens", shapes)
    stranger = a_routine("uses_another_class", shapes)
    box.refs_by = [local, vendored_local, namespace]
    box.refs_by_kind = "c Typedby"
    local.refs_by = [area]
    local.refs_by_kind = "c Useby"
    area.refs_by = [outer]
    vendored_local.refs_by = [outer]
    namespace.refs_by = [stranger]
    method = a_routine("Box::area", shapes)
    method.refs_by = [box]
    method.refs_by_kind = "c Declarein"
    db = FakeDb(
        [FakeArch("Directory Structure", ents=[shapes])],
        entities={
            FILE_KIND: [shapes, vendored],
            ROUTINE_KINDS: [area, outer, stranger, method],
            CLASS_KINDS: [box],
        },
    )
    return OpaqueProject(db, shapes, box, area, outer, local, vendored_local, method)


BOX_KEY: Final = EntityKey(scope="class", path="native/shapes.cpp", longname="Box")
AREA_KEY: Final = EntityKey(
    scope="routine", path="native/shapes.cpp", longname="box_area", parameters="argv"
)
OUTER_KEY: Final = EntityKey(
    scope="routine", path="native/shapes.cpp", longname="outer", parameters="argv"
)
SHAPES_KEY: Final = EntityKey(scope="file", path="native/shapes.cpp", longname="native/shapes.cpp")


def opaque_impact(
    monkeypatch: pytest.MonkeyPatch, project: OpaqueProject, **overrides: object
) -> dict[str, Any]:
    """Run ``impact`` against the opaque project and return its document."""
    install(monkeypatch, FakeUnderstand(db=project.db))
    result = worker.dispatch("impact", impact_request(keys=[BOX_KEY], **overrides))
    assert "error" not in result, result
    return result


def test_impact_walks_through_an_entity_it_cannot_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Requirement 9.5 for C++: composition, local instantiation and parameter passing all
    # put an object between a type and the routine that uses it. Stopping at the object
    # answers "nothing depends on this class" for the commonest way a C++ type is used.
    document = opaque_impact(monkeypatch, opaque_project(), depth=1)
    assert at_depth(document, BOX_KEY, "1") == ["box_area"]


def test_impact_spends_no_depth_level_on_an_entity_it_cannot_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The hop through the object is free, so `depth` counts the same reference distance in
    # C++ as in Python: `box.area()` is one level out exactly as `wrap_lines()` is.
    document = opaque_impact(monkeypatch, opaque_project(), depth=2)
    assert at_depth(document, BOX_KEY, "1") == ["box_area"]
    assert at_depth(document, BOX_KEY, "2") == ["outer"]


def test_impact_never_reports_an_entity_it_cannot_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = opaque_impact(monkeypatch, opaque_project(), depth=3)
    reported = [name for level in ("1", "2", "3") for name in at_depth(document, BOX_KEY, level)]
    assert "box_area::box" not in reported
    assert reported == ["box_area", "outer"]


def test_impact_does_not_walk_through_an_entity_outside_the_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `six::cache` lives in a vendored library file and names `outer`. Walking through it
    # would put `outer` one level from `Box`, joining two unrelated parts of the project
    # through code the reviewer never wrote — the same door the `builtins.*` stubs come in by.
    document = opaque_impact(monkeypatch, opaque_project(), depth=1)
    assert at_depth(document, BOX_KEY, "1") == ["box_area"]


def test_impact_terminates_on_a_cycle_of_entities_it_cannot_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = opaque_project()
    first = a_variable("loop::first", project.shapes)
    second = a_variable("loop::second", project.shapes)
    first.refs_by = [second]
    second.refs_by = [first, project.outer]
    project.box.refs_by = [*project.box.refs_by, first]
    document = opaque_impact(monkeypatch, project, depth=1)
    assert at_depth(document, BOX_KEY, "1") == ["box_area", "outer"]


def test_impact_follows_a_reverse_reference_of_any_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Measured: a thrown-and-caught C++ exception class carries `C Throwby Exception` and
    # `C Catchby Exception`, and a function pointer carries `C Assignby FunctionPtr`. A
    # hand-listed set of reverse kinds loses all three, so direction decides, not a list.
    project = opaque_project()
    project.box.refs_by = [project.area]
    project.box.refs_by_kind = "c Catchby Exception"
    document = opaque_impact(monkeypatch, project, depth=1)
    assert at_depth(document, BOX_KEY, "1") == ["box_area"]


def test_impact_ignores_a_forward_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    # A reference this entity makes is the opposite of a blast radius: `Box` using `outer`
    # says nothing about who has to change when `Box` changes.
    project = opaque_project()
    project.box.refs_by = []
    project.box.refs_to = [project.outer]
    document = opaque_impact(monkeypatch, project, depth=2)
    assert impact_of(document, BOX_KEY) == {"by_depth": {}, "total": 0}


def test_impact_ignores_the_reference_to_the_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `definein`/`declarein` point at the file an entity is written in. The container holds
    # the entity, it does not reference it, and reporting it would put every routine's own
    # file one level out and the file's whole blast radius two levels out.
    document = opaque_impact(monkeypatch, opaque_project(), depth=2)
    reported = [name for level in ("1", "2") for name in at_depth(document, BOX_KEY, level)]
    assert "native/shapes.cpp" not in reported


def test_impact_does_not_walk_through_an_entity_that_is_not_an_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Measured on a real database: two classes of one namespace, each used by a routine that
    # knows nothing of the other, come back with *identical* depth-1 sets once the walk hops
    # through the namespace — the namespace's user count wearing each class's name. A
    # reviewer reading that judges the risk of a change by a number that is not about the
    # entity they asked about, which is the opposite of what requirement 9.5 promises.
    document = opaque_impact(monkeypatch, opaque_project(), depth=3)
    reported = [name for level in ("1", "2", "3") for name in at_depth(document, BOX_KEY, level)]
    assert "uses_another_class" not in reported
    assert "lens" not in reported


@pytest.mark.parametrize(
    "kind",
    [
        pytest.param("c Object Local", id="a local"),
        pytest.param("c Parameter", id="a parameter"),
        pytest.param("c Private Member Object", id="a member object"),
        pytest.param("c Macro Functional", id="a macro"),
        pytest.param("Python Variable Global", id="a module variable"),
    ],
)
def test_impact_walks_through_every_kind_of_object(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    # The positive list, one kind at a time: these are the entities that legitimately stand
    # between a type and the code using it, and every one was seen on a real database
    # (`C Object Local`, `C Parameter`, `C Private Member Object` in this project's own
    # fixture; `C Macro` and a module-level variable in the probes).
    project = opaque_project()
    project.local.kind_path = kind
    document = opaque_impact(monkeypatch, project, depth=1)
    assert at_depth(document, BOX_KEY, "1") == ["box_area"]


def test_impact_does_not_report_the_class_a_method_is_declared_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Measured: `app::Alpha::one` carries `C Declarein -> app::Alpha`, and a class is keyable,
    # so without the exclusion every method would report the class that holds it as one of the
    # entities that depend on it — and then the class's whole blast radius one level further.
    install(monkeypatch, FakeUnderstand(db=opaque_project().db))
    key = EntityKey(
        scope="routine", path="native/shapes.cpp", longname="Box::area", parameters="argv"
    )
    document = worker.dispatch("impact", impact_request(keys=[key], depth=1))
    assert impact_of(document, key) == {"by_depth": {}, "total": 0}


def test_impact_never_reports_an_entity_that_references_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A recursive routine genuinely holds a reverse reference to itself, and `Beginby`/`Endby`
    # name their own entity too. Nothing excludes those kinds; the answer stays clean because
    # an entity is never reported inside its own impact set.
    project = opaque_project()
    project.box.refs_by = [project.box, project.local]
    document = opaque_impact(monkeypatch, project, depth=1)
    assert at_depth(document, BOX_KEY, "1") == ["box_area"]


def test_the_worker_excludes_only_containment_reference_kinds() -> None:
    # The walk selects reverse references by Understand's own direction bit, so no reference
    # kind can be forgotten; the only hand-written list left is what to leave out, and each
    # token has to name a kind that leads to a container rather than to a user:
    #   definein/declarein — the file, class or namespace an entity is written in. Measured:
    #     `app::Alpha::one` carries `C Declarein -> app::Alpha`, so dropping either token
    #     makes every method report the class that holds it. Both have a test above.
    # Three tokens were dropped after measuring them, rather than kept on the strength of
    # their names: `beginby`/`endby` were only ever self-references (0 non-self occurrences
    # across three databases) and an entity is already kept out of its own impact set, and
    # `containin` was not emitted at all — not by a nested class, a nested function, a
    # comprehension, a generator, a lambda or a coroutine.
    # Subset, not equality: adding a token here can only lose real references, so the test
    # fails on an addition, while a removal is caught by the behavioural tests above.
    excluded = {token.strip() for token in worker.STRUCTURAL_REFS.split(",")}
    assert excluded <= {"definein", "declarein"}
    assert excluded, "the containment kinds must be named somewhere"


# --- graphs ---------------------------------------------------------------------


def graph_request(
    directory: Path, drawings: Sequence[tuple[EntityKey, str]], **overrides: object
) -> dict[str, Any]:
    """A well-formed ``graphs`` request for the reference project."""
    request: dict[str, Any] = {
        "db": "/cache/after.und",
        "root": ANALYSIS_ROOT,
        "kinds_by_scope": dict(KINDS),
        "targets": [{"key": key.model_dump(), "graph": graph} for key, graph in drawings],
        "out_dir": str(directory),
    }
    request.update(overrides)
    return request


def graphs(
    monkeypatch: pytest.MonkeyPatch,
    out_dir: Path,
    targets: Sequence[tuple[EntityKey, str]] = ((WRAP_KEY, "Butterfly"),),
    **overrides: object,
) -> dict[str, Any]:
    """Run ``graphs`` against the reference project and return its document."""
    install(monkeypatch, FakeUnderstand(db=reference_project().db))
    result = worker.dispatch("graphs", graph_request(out_dir, targets, **overrides))
    assert "error" not in result, result
    return result


def exported(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The exported graph files, validated through the model the summary consumes."""
    files: list[dict[str, Any]] = document["graphs"]
    for entry in files:
        GraphFile.model_validate(entry)
    return files


def test_graphs_exports_one_svg_per_target(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Requirement 9.4: a butterfly per routine, a depends-on graph per file.
    out_dir = tmp_path / "graphs"
    document = graphs(monkeypatch, out_dir, [(WRAP_KEY, "Butterfly"), (APP_KEY, "Depends On")])
    files = exported(document)
    assert [entry["graph"] for entry in files] == ["Butterfly", "Depends On"]
    assert [entry["key"] for entry in files] == [WRAP_KEY.model_dump(), APP_KEY.model_dump()]
    for entry in files:
        written = Path(entry["path"])
        assert written.parent == out_dir
        assert written.suffix == ".svg"
        assert written.read_text(encoding="utf-8")
    assert warnings_of(document) == []


def test_graphs_creates_the_output_directory_it_was_given(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out_dir = tmp_path / "review" / "graphs"
    document = graphs(monkeypatch, out_dir)
    assert out_dir.is_dir()
    assert Path(exported(document)[0]["path"]).exists()


def test_graphs_writes_nothing_outside_the_output_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A long name carries `::`, `<`, `>` and `/`; a file name built from it verbatim would
    # escape the directory the operator chose.
    out_dir = tmp_path / "graphs"
    hostile = EntityKey(
        scope="routine",
        path="cli/app.py",
        longname="../../ns::Widget<int>::do/it",
        parameters="argv",
    )
    project = reference_project()
    project.main.qualified = "../../ns::Widget<int>::do/it"
    install(monkeypatch, FakeUnderstand(db=project.db))
    document = worker.dispatch("graphs", graph_request(out_dir, [(hostile, "Butterfly")]))
    assert "error" not in document, document
    written = Path(exported(document)[0]["path"])
    assert written.parent == out_dir
    assert list(tmp_path.iterdir()) == [out_dir]
    assert re.fullmatch(r"[A-Za-z0-9_.-]+", written.name), written.name


def test_graphs_gives_two_entities_of_the_same_name_two_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Two routines can share a long name across files (and two overloads share it inside
    # one file): a file name built from the name alone would have one graph overwrite the
    # other and the summary would point both entities at the same picture.
    out_dir = tmp_path / "graphs"
    project = reference_project()
    project.wrap.qualified = "app.main"
    install(monkeypatch, FakeUnderstand(db=project.db))
    twin = routine_ref("app.main", "util/text.py")
    document = worker.dispatch(
        "graphs", graph_request(out_dir, [(MAIN_KEY, "Butterfly"), (twin, "Butterfly")])
    )
    files = exported(document)
    assert len(files) == 2
    assert files[0]["path"] != files[1]["path"]
    assert len(list(out_dir.iterdir())) == 2


def test_graphs_gives_two_graphs_of_one_entity_two_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out_dir = tmp_path / "graphs"
    document = graphs(monkeypatch, out_dir, [(APP_KEY, "Butterfly"), (APP_KEY, "Depends On")])
    files = exported(document)
    assert files[0]["path"] != files[1]["path"]
    assert len(list(out_dir.iterdir())) == 2


def test_graphs_names_the_same_file_for_the_same_request_twice(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    first = graphs(monkeypatch, tmp_path / "a")
    second = graphs(monkeypatch, tmp_path / "b")
    assert Path(exported(first)[0]["path"]).name == Path(exported(second)[0]["path"]).name


def test_graphs_records_a_failed_draw_as_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Verified live: `Depends On` on a routine raises UnderstandError('Unknown Graph'). One
    # target that will not render must not cost the reviewer every other graph.
    out_dir = tmp_path / "graphs"
    document = graphs(monkeypatch, out_dir, [(WRAP_KEY, "Depends On"), (APP_KEY, "Depends On")])
    files = exported(document)
    assert [entry["key"] for entry in files] == [APP_KEY.model_dump()]
    assert len(warnings_of(document)) == 1
    assert "text.wrap_lines" in warnings_of(document)[0]
    assert "Depends On" in warnings_of(document)[0]
    assert "Unknown Graph" in warnings_of(document)[0]
    assert len(list(out_dir.iterdir())) == 1


def test_graphs_warns_about_a_target_the_database_does_not_hold(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out_dir = tmp_path / "graphs"
    missing = routine_ref("app.gone", "cli/app.py")
    document = graphs(monkeypatch, out_dir, [(missing, "Butterfly"), (WRAP_KEY, "Butterfly")])
    assert len(exported(document)) == 1
    assert len(warnings_of(document)) == 1
    assert "app.gone" in warnings_of(document)[0]


def test_graphs_asks_the_api_for_the_graph_and_the_file_it_chose(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out_dir = tmp_path / "graphs"
    project = reference_project()
    install(monkeypatch, FakeUnderstand(db=project.db))
    document = worker.dispatch("graphs", graph_request(out_dir, [(WRAP_KEY, "Butterfly")]))
    assert project.wrap.drawn == [("Butterfly", exported(document)[0]["path"])]


def test_graphs_closes_the_database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project = reference_project()
    install(monkeypatch, FakeUnderstand(db=project.db))
    worker.dispatch("graphs", graph_request(tmp_path / "graphs", [(WRAP_KEY, "Butterfly")]))
    assert project.db.closed


def test_graphs_refuses_an_analysis_root_that_names_no_file_of_the_database(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install(monkeypatch, FakeUnderstand(db=reference_project().db))
    request = graph_request(
        tmp_path / "graphs", [(WRAP_KEY, "Butterfly")], root="/completely/wrong"
    )
    error = envelope(worker.dispatch("graphs", request))
    assert error["type"] == "AnalysisRootMismatch"


@pytest.mark.parametrize(
    ("overrides", "expected_key"),
    [
        pytest.param({"db": ""}, "db", id="empty database path"),
        pytest.param({"root": ""}, "root", id="empty analysis root"),
        pytest.param({"out_dir": ""}, "out_dir", id="empty output directory"),
        pytest.param({"out_dir": 7}, "out_dir", id="output directory not a string"),
        pytest.param({"targets": {}}, "targets", id="targets not a list"),
        pytest.param({"targets": [{"graph": "Butterfly"}]}, "targets", id="target without a key"),
        pytest.param(
            {"targets": [{"key": MAIN_KEY.model_dump(), "graph": ""}]},
            "targets",
            id="empty graph name",
        ),
        pytest.param(
            {"targets": [{"key": MAIN_KEY.model_dump(), "graph": 7}]},
            "targets",
            id="graph name not a string",
        ),
    ],
)
def test_graphs_rejects_a_malformed_request_before_opening_a_database(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, overrides: dict[str, object], expected_key: str
) -> None:
    api = FakeUnderstand(db=reference_project().db)
    install(monkeypatch, api)
    request = graph_request(tmp_path / "graphs", [(WRAP_KEY, "Butterfly")], **overrides)
    error = envelope(worker.dispatch("graphs", request))
    assert error["type"] == "BadRequest"
    assert expected_key in error["message"]
    assert api.opened == []
    assert not (tmp_path / "graphs").exists()
