"""The fake projects the worker's snapshot tests are built on, and the requests that read them.

A three-file project plus the library file Understand injects, builders for a file, a routine,
a class, a variable and a namespace with the metric values a test wants, the kind strings the
requests carry, and the readers (``records``, ``listing``, ``mapping``) that pull one part out
of a snapshot document. Shared by every ``test_worker_*`` module; the fake ``understand``
module itself is ``api_fakes``.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import pytest
from api_fakes import (
    FakeArch,
    FakeDb,
    FakeEnt,
    FakeRef,
    FakeUnderstand,
    install,
)
from conftest import understand_probe

from scitools_hook.models.snapshot import EntityKey
from scitools_hook.understand import worker

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
WORKER_PATH: Final = REPO_ROOT / "src" / "scitools_hook" / "understand" / "worker.py"
"""The file under test, addressed as a path: two tests must not import it."""
SUBPROCESS_TIMEOUT_S: Final = 300


# --- snapshot: the fake project -------------------------------------------------

ROUTINE_KIND: Final = "python function ~unknown ~unresolved"
CLASS_KIND: Final = "python class ~unknown ~unresolved"
"""The kind strings the catalogue tests ask about; the snapshot ones below carry no language."""

FILE_KIND: Final = "file ~unknown ~unresolved"
ROUTINE_KINDS: Final = "function ~unknown ~unresolved, method ~unknown ~unresolved"
CLASS_KINDS: Final = "class ~unknown ~unresolved"
KINDS: Final = {"routine": ROUTINE_KINDS, "class": CLASS_KINDS, "file": FILE_KIND}

ANALYSIS_ROOT: Final = "/ws/after"
"""The directory ``und add`` was pointed at; every long name below sits under it."""

FILE_ONLY: Final = {"file": FILE_KIND}
"""``kinds_by_scope`` for a test that only cares about files and their architecture."""


def a_file(path: str, language: str = "Python", **fields: object) -> FakeEnt:
    """A file entity as Understand reports one: an absolute ``longname``, a relative name."""
    return FakeEnt(
        path=path,
        qualified=f"{ANALYSIS_ROOT}/{path}",
        kind_path=f"{language} File",
        simple=path.rsplit("/", 1)[-1],
        lang=language,
        **fields,  # type: ignore[arg-type]
    )


def a_routine(name: str, container: FakeEnt, **fields: object) -> FakeEnt:
    """A routine defined in ``container``; ``parameters`` distinguishes overloads."""
    return FakeEnt(
        qualified=name,
        kind_path="python Function",
        simple=name.rsplit(".", 1)[-1],
        lang=container.lang,
        params="argv",
        container=container,
        line_no=9,
        **fields,  # type: ignore[arg-type]
    )


def a_class(name: str, container: FakeEnt, **fields: object) -> FakeEnt:
    """A class defined in ``container``."""
    return FakeEnt(
        qualified=name,
        kind_path="python Class",
        simple=name.rsplit(".", 1)[-1],
        lang=container.lang,
        container=container,
        line_no=5,
        **fields,  # type: ignore[arg-type]
    )


def a_variable(name: str, container: FakeEnt, kind: str = "c Object Local") -> FakeEnt:
    """An object entity — a local, a parameter, a member, a macro — that no scope keys."""
    return FakeEnt(
        qualified=name,
        kind_path=kind,
        simple=name.rsplit("::", 1)[-1],
        lang="C++",
        container=container,
        line_no=3,
    )


def a_namespace(name: str, container: FakeEnt) -> FakeEnt:
    """A namespace: project code, keyed by no scope, and not an object either.

    It is the counter-example the through-walk exists for: every class declared in it points
    back at it, and it points back at every entity that mentions it, so a walk that treats
    "not keyable" as "walk through it" hands each class the namespace's whole user list.
    """
    return FakeEnt(
        qualified=name,
        kind_path="c Namespace",
        simple=name,
        lang="C++",
        container=container,
        line_no=1,
    )


@dataclass
class FakeProject:
    """The fake database and the entities a test wants to reach by name."""

    db: FakeDb
    app: FakeEnt
    text: FakeEnt
    native: FakeEnt
    injected: FakeEnt
    vendored: FakeEnt
    outside: FakeEnt
    build_parser: FakeEnt
    wrap_lines: FakeEnt
    clamp: FakeEnt
    runner: FakeEnt
    helper: FakeEnt


def fake_project() -> FakeProject:
    """A three-file project plus the library file Understand injects into a Python project.

    ``cli/app.py`` depends on ``util/text.py``; ``util/text.py`` depends on ``native/util.c``
    and nothing depends on it, so it is a direct neighbour rather than a requested file.
    ``native/util.c`` is otherwise unrelated, which is what makes the neighbourhood bound
    observable.
    """
    app, text, native, injected, vendored, outside = _fake_files()
    build_parser, wrap_lines, clamp, stub, imported = _fake_routines(
        app, text, native, injected, outside
    )
    runner, helper = _fake_classes(app, text)
    db = FakeDb(
        [_fake_architecture(app, text, native, vendored)],
        entities={
            FILE_KIND: [app, text, native, injected, vendored, outside],
            ROUTINE_KINDS: [build_parser, wrap_lines, clamp, stub, imported],
            CLASS_KINDS: [runner, helper],
        },
        project_metrics={"MaxCyclomaticStrict": 7, "MaxNesting": 4, "CountLineCode": 37},
    )
    return FakeProject(
        db,
        app,
        text,
        native,
        injected,
        vendored,
        outside,
        build_parser,
        wrap_lines,
        clamp,
        runner,
        helper,
    )


def _fake_files() -> tuple[FakeEnt, FakeEnt, FakeEnt, FakeEnt, FakeEnt, FakeEnt]:
    """The six files: three of the project's own, one injected, one vendored, one outside."""
    app = a_file("cli/app.py")
    text = a_file("util/text.py")
    native = a_file("native/util.c", language="C++")
    injected = a_file("/opt/scitools/conf/understand/python/python3/builtins.py", lib="Standard")
    vendored = a_file("vendor/six.py", lib="Standard", values={"CountLineCode": 400})
    outside = a_file("/usr/include/sample.h", language="C++", values={"CountLineCode": 900})
    app.values = {"CountLineCode": 26, "MaxCyclomaticStrict": 7, "RatioCommentToCode": "0,15"}
    text.values = {"CountLineCode": 2, "MaxCyclomaticStrict": 1, "RatioCommentToCode": "1,00"}
    native.values = {"CountLineCode": 9, "MaxCyclomaticStrict": 3, "RatioCommentToCode": "0,11"}
    app.deps = {text: [object()] * 3}
    text.deps_by = {app: [object()] * 3}
    text.deps = {native: [object()] * 2}
    native.deps_by = {text: [object()] * 2}
    return app, text, native, injected, vendored, outside


def _fake_routines(
    app: FakeEnt, text: FakeEnt, native: FakeEnt, injected: FakeEnt, outside: FakeEnt
) -> tuple[FakeEnt, FakeEnt, FakeEnt, FakeEnt, FakeEnt]:
    """One routine per file, with the metric values the threshold tests read back."""
    build_parser = a_routine(
        "app.build_parser",
        app,
        values={"CyclomaticStrict": 7, "MaxNesting": 4, "CountLineCode": 17, "CountParams": None},
        declared_params=1,
    )
    wrap_lines = a_routine(
        "text.wrap_lines",
        text,
        values={"CyclomaticStrict": 1, "MaxNesting": 0, "CountLineCode": 2, "CountParams": None},
        declared_params=2,
    )
    clamp = a_routine(
        "clamp",
        native,
        values={"CyclomaticStrict": 3, "MaxNesting": 1, "CountLineCode": 9, "CountParams": None},
        declared_params=3,
    )
    stub = a_routine("builtins.abs", injected, lib="Standard")
    # An out-of-root header Understand parses without marking it a library: the entity is
    # ordinary, only the file it is defined in is outside the repository.
    imported = a_routine("sample_helper", outside, values={"CyclomaticStrict": 5})
    return build_parser, wrap_lines, clamp, stub, imported


def _fake_classes(app: FakeEnt, text: FakeEnt) -> tuple[FakeEnt, FakeEnt]:
    """Two classes, one depending on the other, so the class edges have something to carry."""
    runner = a_class(
        "app.Runner",
        app,
        values={"CountDeclMethod": 4, "CountDeclPropertyAuto": 1, "PercentLackOfCohesion": None},
    )
    helper = a_class("text.Helper", text, values={"CountDeclMethod": 2})
    runner.deps = {helper: [object()] * 2}
    helper.deps_by = {runner: [object()] * 2}
    return runner, helper


def _fake_architecture(app: FakeEnt, text: FakeEnt, native: FakeEnt, vendored: FakeEnt) -> FakeArch:
    """The directory structure Understand would build over the four in-tree files."""
    return FakeArch(
        "Directory Structure",
        children=[
            FakeArch("Directory Structure/cli", ents=[app]),
            FakeArch("Directory Structure/util", ents=[text]),
            FakeArch("Directory Structure/native", ents=[native]),
            FakeArch("Directory Structure/vendor", ents=[vendored]),
        ],
    )


def snapshot_request(**overrides: object) -> dict[str, object]:
    """A well-formed ``snapshot`` request; a test overrides only the key it is about."""
    request: dict[str, object] = {
        "db": "/cache/after.und",
        "side": "after",
        "root": ANALYSIS_ROOT,
        "files": ["cli/app.py"],
        "kinds_by_scope": dict(KINDS),
        "metrics_by_scope": {
            "routine": ["CyclomaticStrict", "MaxNesting", "CountParams"],
            "class": ["CountDeclMethod", "CountDeclMethodNonStub", "PercentLackOfCohesion"],
            "file": ["CountLineCode", "MaxCyclomaticStrict", "RatioCommentToCode"],
        },
        "synthetic": ["CountParams", "CountDeclMethodNonStub"],
        "population_metrics": {},
        "ignore": {},
        "architecture": "Directory Structure",
        "depth": 1,
        "include_edges": True,
    }
    request.update(overrides)
    return request


def snapshot(monkeypatch: pytest.MonkeyPatch, **overrides: object) -> dict[str, Any]:
    """Run the ``snapshot`` operation against the fake project and return its document."""
    install(monkeypatch, FakeUnderstand(db=fake_project().db))
    result = worker.dispatch("snapshot", snapshot_request(**overrides))
    assert "error" not in result, result
    return result


def records(document: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """The entity records of a document, keyed by their qualified name."""
    entities: list[dict[str, Any]] = document["entities"]
    return {record["ref"]["key"]["longname"]: record for record in entities}


def listing(document: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    """One of the document's lists, typed so that a test can index into its members."""
    found: list[dict[str, Any]] = document[key]
    return found


def mapping(document: Mapping[str, Any], key: str) -> dict[str, Any]:
    """One of the document's objects, typed so that a test can index into it."""
    found: dict[str, Any] = document[key]
    return found


def a_dep_ref(target: FakeEnt, line: int, kind: str) -> FakeRef:
    """One of the references ``Ent.depends()`` hands back for a pair of files."""
    return FakeRef(target, line, kind, True)


# --- running the real worker (the contract modules) ---------------------------------


def upython_or_skip() -> Path:
    """The interpreter Understand ships next to ``und``; skip when this build has none."""
    probe = understand_probe()
    assert probe.und is not None, "the contract gate only lets this run with a usable probe"
    upython = probe.und.parent / "upython"
    if not upython.exists():
        pytest.skip(f"no upython next to {probe.und}")
    return upython


def run_worker(interpreter: Path, op: str, request: Mapping[str, object]) -> dict[str, Any]:
    """Run the worker as the ``ApiRunner`` will: JSON in on stdin, one JSON document out."""
    proc = subprocess.run(
        [str(interpreter), str(WORKER_PATH), op],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_S,
        check=False,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    answer = json.loads(proc.stdout)
    assert isinstance(answer, dict)
    return answer


def file_key(path: str) -> EntityKey:
    """The key of a file entity; its long name is the path, never the absolute one."""
    return EntityKey(scope="file", path=path, longname=path, parameters=None)


def routine_key(path: str, longname: str, parameters: str) -> EntityKey:
    """The key of a routine defined in ``path``."""
    return EntityKey(scope="routine", path=path, longname=longname, parameters=parameters)
