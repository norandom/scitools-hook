"""The stdlib-only Understand API worker: purity, dispatch, envelopes, CLI (task 6.1).

The worker is the only module in the project that may touch the ``understand`` Python API,
and it is the only module that must run under an interpreter that knows nothing about this
package: ``<SCITOOLS_HOME>/bin/<platform>/upython`` ships no third-party packages, and on
this machine importing the API into system CPython aborts the interpreter outright. Three
properties therefore matter more than any single operation:

* **Purity** — it imports the standard library and ``understand``, nothing else. Pinned by
  an AST parse of the source (no import needed) and by running the file under
  ``python -I -S``, where neither the environment nor ``site-packages`` can supply this
  package. A worker that reached for ``scitools_hook.errors`` would pass unit tests and
  fail in production under ``upython``.
* **Laziness** — importing the module must not import the API. The in-process probe of
  requirement 1.2 imports the worker to find out whether the API loads at all; if the
  import happened at module level, a failing API would take the whole CLI process down.
* **Envelopes** — every foreseeable failure (no license, unopenable database, unknown
  operation, malformed request, missing architecture) is *data* on standard output with
  exit status 0, because the caller parses the answer. Only a crash it cannot parse is
  allowed to be a traceback.

Unit tests drive :func:`dispatch` against a fake ``understand`` module injected into
``sys.modules``, so the mapping, the request validation and the ``try/finally`` around the
database are covered on a machine with no license. The ``contract``-marked tests at the end
run the real worker under ``upython`` against the sample databases; they are skipped by the
gate in ``tests/conftest.py`` when no licensed Understand is installed.
"""

from __future__ import annotations

import ast
import io
import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Final

import pytest
from conftest import SampleDatabases, Side, understand_probe

from scitools_hook.config.metric_names import SCOPE_KINDS
from scitools_hook.models.snapshot import EntityKey, ProjectSnapshot
from scitools_hook.understand import worker

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
WORKER_PATH: Final = REPO_ROOT / "src" / "scitools_hook" / "understand" / "worker.py"
"""The file under test, addressed as a path: two tests must not import it."""

SUBPROCESS_TIMEOUT_S: Final = 300


# --- a fake `understand` module -------------------------------------------------


class FakeUnderstandError(Exception):
    """Stand-in for ``understand.UnderstandError``; the worker maps its text to a type."""


@dataclass(eq=False)
class FakeKind:
    """``Ent.kind()``: only its long name reaches the answer (``python Class``)."""

    path: str

    def longname(self) -> str:
        """The fully qualified kind name."""
        return self.path


@dataclass(eq=False)
class FakeRef:
    """A reference; the worker reads the file it points into and the line it sits on."""

    target: FakeEnt
    line_no: int | None = None

    def file(self) -> FakeEnt:
        """The file entity this reference names."""
        return self.target

    def line(self) -> int | None:
        """The line the reference sits on."""
        return self.line_no


@dataclass(eq=False)
class FakeEnt:
    """An entity of any scope; ``relname`` is ``None`` for everything but a file (verified).

    Identity, not value, decides equality, because the API's ``depends()`` maps are keyed by
    entity and the worker must be able to look a target up.
    """

    path: str | None = None
    qualified: str = ""
    kind_path: str = "File"
    simple: str = ""
    lang: str = "Python"
    params: str | None = None
    values: dict[str, object] = field(default_factory=dict)
    lib: str = ""
    container: FakeEnt | None = None
    line_no: int | None = None
    declared_params: int = 0
    deps: dict[FakeEnt, list[object]] = field(default_factory=dict)
    deps_by: dict[FakeEnt, list[object]] = field(default_factory=dict)

    def relname(self) -> str | None:
        """The project-relative path of a file entity, or ``None`` for other kinds."""
        return self.path

    def longname(self) -> str:
        """Understand's qualified name; a file reports its absolute path (verified)."""
        return self.qualified

    def kind(self) -> FakeKind:
        """The entity's kind object."""
        return FakeKind(self.kind_path)

    def name(self) -> str:
        """The short name."""
        return self.simple

    def language(self) -> str:
        """The language the entity is written in."""
        return self.lang

    def parameters(self) -> str | None:
        """The declared parameters of a routine, ``None`` for every other kind."""
        return self.params

    def library(self) -> str:
        """``'Standard'`` for the stubs Understand injects, empty for project code."""
        return self.lib

    def metric(self, names: Sequence[str]) -> dict[str, object]:
        """The requested metrics; an absent one answers ``None``, as the API does."""
        return {name: self.values.get(name) for name in names}

    def ref(self, refkinds: str) -> FakeRef | None:
        """The first reference of ``refkinds``; the worker asks for the container file."""
        return None if self.container is None else FakeRef(self.container, self.line_no)

    def ents(self, refkinds: str, entkinds: str) -> list[FakeEnt]:
        """The entities reached by ``refkinds``; only the count of parameters is read."""
        return [FakeEnt() for _ in range(self.declared_params)]

    def depends(self) -> dict[FakeEnt, list[object]]:
        """What this entity depends on, with the references that make each dependency."""
        return dict(self.deps)

    def dependsby(self) -> dict[FakeEnt, list[object]]:
        """What depends on this entity."""
        return dict(self.deps_by)


class FakeArch:
    """An architecture node: a long name, child nodes and member entities."""

    def __init__(
        self,
        longname: str,
        children: Sequence[FakeArch] = (),
        ents: Sequence[FakeEnt] = (),
        depends: dict[str, int] | None = None,
    ) -> None:
        self._longname = longname
        self._children = list(children)
        self._ents = list(ents)
        self._depends = dict(depends or {})

    def longname(self) -> str:
        """The full path of the node, e.g. ``Directory Structure/cli``."""
        return self._longname

    def children(self) -> list[FakeArch]:
        """The child nodes, in declaration order."""
        return list(self._children)

    def ents(self, recursive: bool = False) -> list[FakeEnt]:
        """Member entities, optionally including those of the child nodes."""
        found = list(self._ents)
        if recursive:
            for child in self._children:
                found.extend(child.ents(True))
        return found

    def depends(self) -> dict[FakeArch, list[object]]:
        """Node -> the references that make this node depend on it (verified shape)."""
        found = {node.longname(): node for root in self.roots() for node in root.walk()}
        return {found[name]: [object()] * count for name, count in self._depends.items()}

    def roots(self) -> list[FakeArch]:
        """The architecture this node was declared under; a fake needs only itself."""
        return [_ARCH_ROOT] if _ARCH_ROOT is not None else [self]

    def walk(self) -> list[FakeArch]:
        """This node and every descendant, depth first."""
        found = [self]
        for child in self._children:
            found.extend(child.walk())
        return found


class FakeDb:
    """An opened database: entities, metrics, root architectures and a recorded ``close``."""

    def __init__(
        self,
        roots: Sequence[FakeArch] = (),
        lookup_error: str | None = None,
        entities: dict[str, list[FakeEnt]] | None = None,
        project_metrics: dict[str, object] | None = None,
    ) -> None:
        self._roots = list(roots)
        self._lookup_error = lookup_error
        self._entities = dict(entities or {})
        self._project_metrics = dict(project_metrics or {})
        self.languages: tuple[str, ...] = ("Python", "C++")
        self.closed = False

    def ents(self, kindstring: str) -> list[FakeEnt]:
        """Every entity of the kind string; an unknown kind answers with nothing."""
        return list(self._entities.get(kindstring, []))

    def metric(self, names: Sequence[str]) -> dict[str, object]:
        """The database's own metrics; an absent one answers ``None``, as the API does."""
        return {name: self._project_metrics.get(name) for name in names}

    def language(self) -> tuple[str, ...]:
        """The languages the database was analyzed with."""
        return self.languages

    def root_archs(self) -> list[FakeArch]:
        """The root architectures of the database."""
        return list(self._roots)

    def lookup_arch(self, longname: str) -> FakeArch | None:
        """The node with this long name anywhere in the tree, or ``None`` (as the API does)."""
        if self._lookup_error is not None:
            raise FakeUnderstandError(self._lookup_error)
        for root in self._roots:
            for node in root.walk():
                if node.longname() == longname:
                    return node
        return None

    def close(self) -> None:
        """Record the close; the real API crashes later if objects outlive this call."""
        self.closed = True


class FakeMetrics:
    """Stand-in for ``understand.Metric``: metric names per kind string, and descriptions."""

    def __init__(
        self,
        by_kind: dict[str, list[str]] | None = None,
        descriptions: dict[str, str] | None = None,
    ) -> None:
        self._by_kind = by_kind or {}
        self._descriptions = descriptions or {}

    def list(self, kindstring: str) -> list[str]:
        """The metrics defined for the kind string; the API returns ``[]`` for an unknown one."""
        return list(self._by_kind.get(kindstring, []))

    def description(self, metricname: str) -> str:
        """The metric's description, empty when the name is unknown (as the API does)."""
        return self._descriptions.get(metricname, "")


class FakeUnderstand(ModuleType):
    """A module-shaped stand-in for the API, injectable into ``sys.modules``."""

    UnderstandError: type[FakeUnderstandError]
    Metric: FakeMetrics

    def __init__(
        self,
        db: FakeDb | None = None,
        version: str = "6.5.1204",
        open_error: str | None = None,
        metrics: FakeMetrics | None = None,
    ) -> None:
        super().__init__("understand")
        self.UnderstandError = FakeUnderstandError
        self.Metric = metrics if metrics is not None else FakeMetrics()
        self.opened: list[str] = []
        self._db = db
        self._version = version
        self._open_error = open_error

    def version(self) -> str:
        """The API version, as ``understand.version()`` returns it (verified: ``6.5.1204``)."""
        return self._version

    def open(self, dbname: str) -> FakeDb:
        """Open the database, recording the path and raising the configured API error."""
        self.opened.append(dbname)
        if self._open_error is not None:
            raise FakeUnderstandError(self._open_error)
        if self._db is None:
            raise FakeUnderstandError("DBUnableOpen: unable to open database")
        return self._db


class InteractiveStdin(io.StringIO):
    """A terminal-like stdin: reading it would block the worker forever."""

    def isatty(self) -> bool:
        """Claim to be a terminal."""
        return True

    def read(self, size: int | None = -1, /) -> str:
        """Fail the test rather than block; the worker must skip an interactive stdin."""
        raise AssertionError("the worker must not read from an interactive stdin")


def install(monkeypatch: pytest.MonkeyPatch, api: ModuleType) -> None:
    """Put ``api`` in ``sys.modules`` so the worker's lazy ``import understand`` finds it."""
    monkeypatch.setitem(sys.modules, "understand", api)


def directory_structure() -> FakeArch:
    """The shape Understand builds for the sample project, with one uneven branch."""
    return FakeArch(
        "Directory Structure",
        children=[
            FakeArch(
                "Directory Structure/src",
                children=[
                    FakeArch("Directory Structure/src/cli", ents=[FakeEnt("src/cli/app.py")]),
                    FakeArch("Directory Structure/src/util", ents=[FakeEnt("src/util/text.py")]),
                ],
            ),
            FakeArch(
                "Directory Structure/native",
                ents=[FakeEnt("native/util.c"), FakeEnt(None), FakeEnt("native/util.h")],
            ),
        ],
    )


def envelope(result: dict[str, object]) -> dict[str, Any]:
    """The error object of an envelope, failing the test when the result is a success."""
    error = result.get("error")
    assert isinstance(error, dict), f"expected an error envelope, got {result!r}"
    return error


# --- purity: the worker stands alone --------------------------------------------


def from_root(node: ast.ImportFrom) -> str:
    """The package a ``from ... import`` reads; a relative import gets a name nothing allows."""
    if node.level:
        return f"<relative import on line {node.lineno}>"
    return (node.module or "").split(".")[0]


def imported_roots(source: str) -> set[str]:
    """Every top-level package name the source imports, from an AST parse (no execution)."""
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        if isinstance(node, ast.ImportFrom):
            roots.add(from_root(node))
    return roots


def test_the_import_scan_sees_plain_dotted_and_relative_imports() -> None:
    # The two tests below are only as good as this scan, so pin what it reports.
    source = "import a.b\nfrom c.d import e\nfrom . import f\n"
    assert imported_roots(source) == {"a", "c", "<relative import on line 3>"}


def test_the_worker_imports_nothing_from_this_package() -> None:
    # Requirement 1.2/1.4 by way of the design: worker.py must load under `upython`, which
    # has neither this package nor any third-party distribution.
    roots = imported_roots(WORKER_PATH.read_text(encoding="utf-8"))
    assert "scitools_hook" not in roots
    assert not [root for root in roots if root.startswith("<relative")]


def test_the_worker_imports_only_the_standard_library_and_understand() -> None:
    roots = imported_roots(WORKER_PATH.read_text(encoding="utf-8"))
    assert roots, "the worker is expected to import at least json and sys"
    assert roots <= set(sys.stdlib_module_names) | {"understand"}


def test_importing_the_worker_does_not_import_the_api() -> None:
    # The in-process probe imports this module to find out whether the API loads; a
    # module-level `import understand` would abort the whole process on this platform.
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import scitools_hook.understand.worker; "
            "print('understand' in sys.modules)",
        ],
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_S,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "False"


def test_the_worker_answers_ping_under_an_isolated_interpreter() -> None:
    # The task's done-criterion: `python -I worker.py ping` with no package on sys.path
    # answers with the API version or with an error envelope. `-I` drops the script
    # directory and the environment, `-S` drops site-packages, so nothing this project
    # installed is reachable.
    proc = subprocess.run(
        [sys.executable, "-I", "-S", str(WORKER_PATH), "ping"],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=SUBPROCESS_TIMEOUT_S,
        check=False,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    answer = json.loads(proc.stdout)
    if "error" in answer:
        assert answer["error"]["type"] in {"ApiUnavailable", "NoApiLicense"}
    else:
        assert answer["version"]


# --- ping ------------------------------------------------------------------------


def test_ping_reports_the_api_version(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, FakeUnderstand(version="6.5.1204"))
    assert worker.dispatch("ping", {})["version"] == "6.5.1204"


def test_ping_reports_the_interpreter_that_ran_the_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    # `doctor` (req 1.5) reports which interpreter loaded the API, so ping must say.
    install(monkeypatch, FakeUnderstand())
    assert worker.dispatch("ping", {})["python"] == ".".join(str(n) for n in sys.version_info[:3])


def test_a_missing_api_module_is_an_envelope_not_an_import_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `None` in sys.modules is what an interpreter without the API looks like to `import`.
    monkeypatch.setitem(sys.modules, "understand", None)
    error = envelope(worker.dispatch("ping", {}))
    assert error["type"] == "ApiUnavailable"
    assert "understand" in error["message"]


# --- catalogue -------------------------------------------------------------------


ROUTINE_KIND: Final = "python function ~unknown ~unresolved"
CLASS_KIND: Final = "python class ~unknown ~unresolved"


def catalogue_api(metrics: FakeMetrics) -> FakeUnderstand:
    """An API whose only useful member is ``Metric``: the catalogue opens no database."""
    return FakeUnderstand(metrics=metrics)


def test_catalogue_lists_the_metrics_of_every_requested_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metrics = FakeMetrics({ROUTINE_KIND: ["CyclomaticStrict", "CountLineCode"], CLASS_KIND: []})
    install(monkeypatch, catalogue_api(metrics))
    result = worker.dispatch("catalogue", {"kinds": [ROUTINE_KIND, CLASS_KIND]})
    assert result["metrics"] == {
        ROUTINE_KIND: ["CountLineCode", "CyclomaticStrict"],  # sorted: the answer is a contract
        CLASS_KIND: [],
    }


def test_catalogue_opens_no_database(monkeypatch: pytest.MonkeyPatch) -> None:
    api = catalogue_api(FakeMetrics({ROUTINE_KIND: ["CountLineCode"]}))
    install(monkeypatch, api)
    worker.dispatch("catalogue", {"kinds": [ROUTINE_KIND]})
    assert api.opened == []


def test_catalogue_describes_the_metrics_it_is_asked_about(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metrics = FakeMetrics(
        {ROUTINE_KIND: ["CyclomaticStrict"]}, {"CyclomaticStrict": "Strict McCabe complexity"}
    )
    install(monkeypatch, catalogue_api(metrics))
    request = {"kinds": [ROUTINE_KIND], "describe": ["CyclomaticStrict", "NoSuchMetric"]}
    result = worker.dispatch("catalogue", request)
    assert result["descriptions"] == {
        "CyclomaticStrict": "Strict McCabe complexity",
        "NoSuchMetric": "",
    }


def test_catalogue_refuses_a_null_describe_rather_than_ignoring_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit ``null`` is a malformed request, not the same as omitting the key.

    Silently dropping it would hide a caller bug: the answer would come back without the
    ``descriptions`` the caller believed it asked for.
    """
    install(monkeypatch, catalogue_api(FakeMetrics({ROUTINE_KIND: ["CountLineCode"]})))

    result = worker.dispatch("catalogue", {"kinds": [ROUTINE_KIND], "describe": None})

    error = envelope(result)
    assert error["type"] == "BadRequest"
    assert "describe" in error["message"]


def test_catalogue_omits_descriptions_when_none_are_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install(monkeypatch, catalogue_api(FakeMetrics({ROUTINE_KIND: ["CountLineCode"]})))
    assert "descriptions" not in worker.dispatch("catalogue", {"kinds": [ROUTINE_KIND]})


@pytest.mark.parametrize(
    "request_body",
    [
        pytest.param({}, id="missing"),
        pytest.param({"kinds": ROUTINE_KIND}, id="a bare string"),
        pytest.param({"kinds": [ROUTINE_KIND, 7]}, id="a non-string element"),
    ],
)
def test_catalogue_rejects_a_request_without_a_list_of_kinds(
    monkeypatch: pytest.MonkeyPatch, request_body: dict[str, object]
) -> None:
    install(monkeypatch, catalogue_api(FakeMetrics()))
    error = envelope(worker.dispatch("catalogue", request_body))
    assert error["type"] == "BadRequest"
    assert "kinds" in error["message"]


# --- archs -----------------------------------------------------------------------


def archs_request(architecture: str = "Directory Structure", depth: int = 1) -> dict[str, object]:
    """A well-formed ``archs`` request against a database path the fake never reads."""
    return {"db": "/cache/after.und", "architecture": architecture, "depth": depth}


def test_archs_returns_the_root_architecture_names(monkeypatch: pytest.MonkeyPatch) -> None:
    # Requirement 6.7: the architectures are the source of structural nodes, so the caller
    # needs to know which ones exist even on the happy path.
    install(monkeypatch, FakeUnderstand(db=FakeDb([directory_structure()])))
    result = worker.dispatch("archs", archs_request())
    assert result["root_archs"] == ["Directory Structure"]
    assert result["architecture"] == "Directory Structure"


def test_archs_returns_the_nodes_at_the_requested_depth(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, FakeUnderstand(db=FakeDb([directory_structure()])))
    result = worker.dispatch("archs", archs_request(depth=1))
    assert result["nodes"] == [
        {"path": "Directory Structure/src", "files": ["src/cli/app.py", "src/util/text.py"]},
        {"path": "Directory Structure/native", "files": ["native/util.c", "native/util.h"]},
    ]


def test_archs_at_depth_zero_is_the_architecture_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, FakeUnderstand(db=FakeDb([directory_structure()])))
    result = worker.dispatch("archs", archs_request(depth=0))
    nodes = result["nodes"]
    assert isinstance(nodes, list)
    assert [node["path"] for node in nodes] == ["Directory Structure"]
    assert nodes[0]["files"] == [
        "native/util.c",
        "native/util.h",
        "src/cli/app.py",
        "src/util/text.py",
    ]


def test_archs_keeps_a_leaf_that_is_shallower_than_the_requested_depth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `native` has no children: at depth 2 it stands in for itself, otherwise its files
    # would drop out of every structural rule that works on nodes at that depth.
    install(monkeypatch, FakeUnderstand(db=FakeDb([directory_structure()])))
    result = worker.dispatch("archs", archs_request(depth=2))
    nodes = result["nodes"]
    assert isinstance(nodes, list)
    assert [node["path"] for node in nodes] == [
        "Directory Structure/src/cli",
        "Directory Structure/src/util",
        "Directory Structure/native",
    ]


def test_archs_lists_only_file_members(monkeypatch: pytest.MonkeyPatch) -> None:
    # `native` carries a non-file member, whose relname is None; it must not reach the JSON.
    install(monkeypatch, FakeUnderstand(db=FakeDb([directory_structure()])))
    result = worker.dispatch("archs", archs_request(depth=1))
    nodes = result["nodes"]
    assert isinstance(nodes, list)
    assert nodes[1]["files"] == ["native/util.c", "native/util.h"]


def test_archs_names_the_available_architectures_when_the_requested_one_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Requirement 6.8: the answer must list the architectures that do exist.
    db = FakeDb([directory_structure(), FakeArch("Layers")])
    install(monkeypatch, FakeUnderstand(db=db))
    error = envelope(worker.dispatch("archs", archs_request(architecture="Java Packages")))
    assert error["type"] == "ArchitectureNotFound"
    assert error["available"] == ["Directory Structure", "Layers"]
    assert "Java Packages" in error["message"]
    assert db.closed


def test_archs_closes_the_database_on_the_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeDb([directory_structure()])
    install(monkeypatch, FakeUnderstand(db=db))
    worker.dispatch("archs", archs_request())
    assert db.closed


def test_archs_closes_the_database_when_the_api_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    # The API crashes the process when objects outlive an unclosed database, so the close
    # has to survive an error raised between open and return.
    db = FakeDb([directory_structure()], lookup_error="DBCorrupt: bad database file")
    install(monkeypatch, FakeUnderstand(db=db))
    error = envelope(worker.dispatch("archs", archs_request()))
    assert error["type"] == "DBCorrupt"
    assert db.closed


def test_archs_opens_the_database_it_was_given(monkeypatch: pytest.MonkeyPatch) -> None:
    api = FakeUnderstand(db=FakeDb([directory_structure()]))
    install(monkeypatch, api)
    worker.dispatch("archs", archs_request())
    assert api.opened == ["/cache/after.und"]


@pytest.mark.parametrize(
    ("request_body", "expected_key"),
    [
        pytest.param({"architecture": "Directory Structure"}, "db", id="no database"),
        pytest.param({"db": "/cache/after.und"}, "architecture", id="no architecture"),
        pytest.param({"db": 7, "architecture": "Directory Structure"}, "db", id="database type"),
        pytest.param({"db": "/cache/after.und", "architecture": []}, "architecture", id="arch"),
        pytest.param(
            {"db": "/cache/after.und", "architecture": "Directory Structure", "depth": "two"},
            "depth",
            id="depth type",
        ),
        pytest.param(
            {"db": "/cache/after.und", "architecture": "Directory Structure", "depth": -1},
            "depth",
            id="negative depth",
        ),
        pytest.param(
            {"db": "", "architecture": "Directory Structure"},
            "db",
            id="empty db path",
        ),
        pytest.param(
            {"db": "/cache/after.und", "architecture": ""},
            "architecture",
            id="empty architecture name",
        ),
    ],
)
def test_archs_rejects_a_malformed_request(
    monkeypatch: pytest.MonkeyPatch, request_body: dict[str, object], expected_key: str
) -> None:
    api = FakeUnderstand(db=FakeDb([directory_structure()]))
    install(monkeypatch, api)
    error = envelope(worker.dispatch("archs", request_body))
    assert error["type"] == "BadRequest"
    assert expected_key in error["message"]
    assert api.opened == [], "a malformed request must be rejected before a database is opened"


def test_archs_defaults_to_depth_one(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, FakeUnderstand(db=FakeDb([directory_structure()])))
    result = worker.dispatch(
        "archs", {"db": "/cache/after.und", "architecture": "Directory Structure"}
    )
    assert result["depth"] == 1
    nodes = result["nodes"]
    assert isinstance(nodes, list)
    assert [node["path"] for node in nodes] == [
        "Directory Structure/src",
        "Directory Structure/native",
    ]


# --- snapshot: the fake project -------------------------------------------------

FILE_KIND: Final = "file ~unknown ~unresolved"
ROUTINE_KINDS: Final = "function ~unknown ~unresolved, method ~unknown ~unresolved"
CLASS_KINDS: Final = "class ~unknown ~unresolved"
KINDS: Final = {"routine": ROUTINE_KINDS, "class": CLASS_KINDS, "file": FILE_KIND}

_ARCH_ROOT: FakeArch | None = None
"""Set by :func:`fake_project` so a node's ``depends`` can resolve its siblings by name."""

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

    ``cli/app.py`` depends on ``util/text.py``; ``util/text.py`` depends on nothing, so it is
    a direct neighbour rather than a requested file. ``native/util.c`` is unrelated, which is
    what makes the neighbourhood bound observable.
    """
    global _ARCH_ROOT
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
    runner = a_class(
        "app.Runner",
        app,
        values={"CountDeclMethod": 4, "CountDeclPropertyAuto": 1, "PercentLackOfCohesion": None},
    )
    helper = a_class("text.Helper", text, values={"CountDeclMethod": 2})
    runner.deps = {helper: [object()] * 2}
    helper.deps_by = {runner: [object()] * 2}
    root = FakeArch(
        "Directory Structure",
        children=[
            FakeArch(
                "Directory Structure/cli", ents=[app], depends={"Directory Structure/util": 3}
            ),
            FakeArch("Directory Structure/util", ents=[text]),
            FakeArch("Directory Structure/native", ents=[native]),
            FakeArch("Directory Structure/vendor", ents=[vendored]),
        ],
    )
    _ARCH_ROOT = root
    db = FakeDb(
        [root],
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


# --- snapshot: entities ---------------------------------------------------------


def test_snapshot_validates_into_a_project_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    # The done-criterion of the task: the worker's answer is a ProjectSnapshot document.
    document = snapshot(monkeypatch)
    parsed = ProjectSnapshot.model_validate(document)
    assert parsed.side == "after"
    assert parsed.languages == ["C++", "Python"]


def test_snapshot_records_only_entities_defined_in_the_requested_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `files` bounds the extraction (design: "entities defined in req.files").
    assert set(records(snapshot(monkeypatch))) == {
        "cli/app.py",
        "app.build_parser",
        "app.Runner",
    }


def test_snapshot_leaves_out_the_library_files_understand_injects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # tasks.md 3.2: the API does show conf/understand/python/.../builtins.py and its ~600
    # routines even though `und list files` hides them.
    document = snapshot(monkeypatch, files=["cli/app.py", "util/text.py", "native/util.c"])
    paths = [record["ref"]["key"]["path"] for record in listing(document, "entities")]
    assert not [path for path in paths if path.startswith("/")]
    assert "builtins.abs" not in records(document)


def test_snapshot_leaves_out_a_file_understand_marks_as_a_library(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``library()`` is Understand's own verdict and is not implied by the path.

    A vendored dependency added as a library sits *inside* the analysis root, so only the
    library flag keeps it and its 400 lines out of the snapshot and out of its populations.
    """
    document = snapshot(
        monkeypatch,
        files=["cli/app.py", "vendor/six.py"],
        population_metrics={"file": ["CountLineCode"]},
    )
    assert "vendor/six.py" not in records(document)
    assert 400 not in mapping(document, "populations")["file"]["CountLineCode"]


def test_snapshot_leaves_out_a_file_outside_the_analysis_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An out-of-root file answers with an absolute ``relname`` and is not always a library.

    A header pulled in from ``/usr/include`` is not part of the repository, so it can be
    neither keyed relative to it nor judged by the repository's thresholds.
    """
    document = snapshot(
        monkeypatch,
        files=["cli/app.py", "/usr/include/sample.h"],
        population_metrics={"file": ["CountLineCode"], "routine": ["CyclomaticStrict"]},
    )
    populations = mapping(document, "populations")
    assert "/usr/include/sample.h" not in records(document)
    assert 900 not in populations["file"]["CountLineCode"]
    # A routine of that header is an ordinary entity; it is its container that puts it out
    # of reach, so the container is what has to be checked.
    assert "sample_helper" not in records(document)
    assert 5 not in populations["routine"]["CyclomaticStrict"]


def test_snapshot_keys_a_file_by_its_root_relative_path_not_its_absolute_longname(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file's ``longname`` embeds the analysis root, so it cannot key the two shadows.

    Understand reports ``/ws/after/cli/app.py`` for a file whose ``relname`` is
    ``cli/app.py``; keying by the long name would make every before/after file key differ
    and silently switch off every file-scope ratchet.
    """
    key = records(snapshot(monkeypatch))["cli/app.py"]["ref"]["key"]
    assert key == {
        "scope": "file",
        "path": "cli/app.py",
        "longname": "cli/app.py",
        "parameters": None,
    }


def test_snapshot_carries_the_kind_name_and_line_of_every_entity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    found = records(snapshot(monkeypatch))
    assert found["app.build_parser"]["ref"] == {
        "key": {
            "scope": "routine",
            "path": "cli/app.py",
            "longname": "app.build_parser",
            "parameters": "argv",
        },
        "kind": "python Function",
        "name": "build_parser",
        "line": 9,
    }
    assert found["cli/app.py"]["ref"]["line"] is None


def test_snapshot_reports_the_language_and_architecture_of_every_entity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Requirement 9.7: a reviewer locates an entity by the architecture of its container file.
    record = records(snapshot(monkeypatch))["app.build_parser"]
    assert record["language"] == "Python"
    assert record["archs"] == ["Directory Structure/cli"]


def test_snapshot_skips_an_entity_whose_container_file_it_cannot_find(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = fake_project()
    project.build_parser.container = None
    install(monkeypatch, FakeUnderstand(db=project.db))
    document = worker.dispatch("snapshot", snapshot_request())
    assert "app.build_parser" not in records(document)


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
    global _ARCH_ROOT
    main = shadow_file("main.py", segment, root)
    core = shadow_file("pkg/core.py", segment, root)
    inner = FakeArch(f"Directory Structure/{segment}/pkg", ents=[core])
    shadow = FakeArch(f"Directory Structure/{segment}", children=[inner], ents=[main])
    top = FakeArch("Directory Structure", children=[shadow])
    _ARCH_ROOT = top
    return FakeDb([top], entities={FILE_KIND: [main, core]})


def root_only_db(segment: str, root: str) -> FakeDb:
    """A shadow root holding nothing but files, so the inserted level is itself a leaf."""
    global _ARCH_ROOT
    main = shadow_file("main.py", segment, root)
    shadow = FakeArch(f"Directory Structure/{segment}", ents=[main])
    top = FakeArch("Directory Structure", children=[shadow])
    _ARCH_ROOT = top
    return FakeDb([top], entities={FILE_KIND: [main]})


def nested_db() -> FakeDb:
    """Every source under ``src/app``, with nothing analysed above it.

    Understand roots ``Directory Structure`` at the parent of the deepest common ancestor of
    the *analysed* files (verified live), so the architecture has a single child ``app`` and
    ``relname`` is relative to ``src``, not to the analysis root.
    """
    global _ARCH_ROOT
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
    _ARCH_ROOT = top
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
    global _ARCH_ROOT
    stray = FakeEnt(path="vendor/x.py", qualified="/elsewhere/vendor/x.py", kind_path="python File")
    inside = a_file("cli/app.py")
    root = FakeArch("Directory Structure", ents=[inside])
    _ARCH_ROOT = root
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
    global _ARCH_ROOT
    main = shadow_file("main.py", segment, root)
    app = shadow_file("cli/app.py", segment, root)
    text = shadow_file("util/text.py", segment, root)
    app.deps = {text: [object()] * 2}
    text.deps_by = {app: [object()] * 2}
    cli_node = FakeArch(
        f"Directory Structure/{segment}/cli",
        ents=[app],
        depends={f"Directory Structure/{segment}/util": 2},
    )
    util_node = FakeArch(f"Directory Structure/{segment}/util", ents=[text])
    shadow = FakeArch(f"Directory Structure/{segment}", children=[cli_node, util_node], ents=[main])
    top = FakeArch("Directory Structure", children=[shadow])
    _ARCH_ROOT = top
    return FakeDb([top], entities={FILE_KIND: [main, app, text]})


def child_to_root_db(segment: str, root: str) -> FakeDb:
    """A shadow root whose package depends on a module sitting in the analysis root."""
    global _ARCH_ROOT
    main = shadow_file("main.py", segment, root)
    core = shadow_file("pkg/core.py", segment, root)
    core.deps = {main: [object()] * 3}
    main.deps_by = {core: [object()] * 3}
    pkg_node = FakeArch(
        f"Directory Structure/{segment}/pkg",
        ents=[core],
        depends={f"Directory Structure/{segment}": 3},
    )
    shadow = FakeArch(f"Directory Structure/{segment}", children=[pkg_node], ents=[main])
    top = FakeArch("Directory Structure", children=[shadow])
    _ARCH_ROOT = top
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
    global _ARCH_ROOT
    inner = FakeEnt(
        path=f"{segment}/x.py",
        qualified=f"{root}/{segment}/x.py",
        kind_path="python File",
        simple="x.py",
    )
    node = FakeArch(f"Directory Structure/{segment}", ents=[inner])
    top = FakeArch("Directory Structure", children=[node])
    _ARCH_ROOT = top
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
    global _ARCH_ROOT
    sibling = FakeEnt(
        path="sibling/x.py", qualified="/ws/afterthought/x.py", kind_path="python File"
    )
    inside = a_file("cli/app.py")
    top = FakeArch("Directory Structure", ents=[inside])
    _ARCH_ROOT = top
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
    global _ARCH_ROOT
    top = FakeArch("Directory Structure")
    _ARCH_ROOT = top
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
    global _ARCH_ROOT
    core = a_file("src/core.py")
    node = FakeArch("Directory Structure/src", ents=[core])
    root = FakeArch("Directory Structure", children=[node])
    _ARCH_ROOT = root
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
    global _ARCH_ROOT
    app = a_file("cli/app.py")
    orphan = a_file("main.py")
    app.deps = {orphan: [object()]}
    orphan.deps_by = {app: [object()]}
    root = FakeArch(
        "Directory Structure", children=[FakeArch("Directory Structure/cli", ents=[app])]
    )
    _ARCH_ROOT = root
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
    global _ARCH_ROOT
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
    _ARCH_ROOT = root
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


# --- snapshot: metrics, synthetics and availability ------------------------------


def test_snapshot_reports_the_metrics_of_an_entity(monkeypatch: pytest.MonkeyPatch) -> None:
    assert records(snapshot(monkeypatch))["app.build_parser"]["metrics"] == {
        "CyclomaticStrict": 7,
        "MaxNesting": 4,
        "CountParams": 1,
    }


def test_snapshot_omits_a_metric_the_language_does_not_have_and_reports_it_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Requirement 5.5: Python classes have no PercentLackOfCohesion (verified); it must be
    # reported unavailable, not defaulted to zero.
    document = snapshot(monkeypatch)
    assert "PercentLackOfCohesion" not in records(document)["app.Runner"]["metrics"]
    assert mapping(document, "unavailable") == {"Python": ["PercentLackOfCohesion"]}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param("0,15", 0.15, id="comma decimal"),
        pytest.param("0.15", 0.15, id="point decimal"),
        pytest.param(" 1,00 ", 1.0, id="padded"),
        pytest.param("1,234.5", 1234.5, id="comma as a thousands separator"),
        pytest.param(12, 12.0, id="an integer stays a number"),
    ],
)
def test_snapshot_coerces_a_locale_formatted_metric_string_to_a_number(
    monkeypatch: pytest.MonkeyPatch, raw: object, expected: float
) -> None:
    """``RatioCommentToCode`` comes back as a locale-formatted string (verified: ``'0,18'``).

    ``EntityRecord.metrics`` is ``dict[str, float]``, so passing the string through would
    fail validation, and comparing it against a limit would raise.
    """
    project = fake_project()
    project.app.values = dict(project.app.values, RatioCommentToCode=raw)
    install(monkeypatch, FakeUnderstand(db=project.db))
    document = worker.dispatch("snapshot", snapshot_request())
    assert records(document)["cli/app.py"]["metrics"]["RatioCommentToCode"] == expected
    ProjectSnapshot.model_validate(document)


def test_snapshot_treats_an_unparsable_metric_string_as_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = fake_project()
    project.app.values = dict(project.app.values, RatioCommentToCode="n/a")
    install(monkeypatch, FakeUnderstand(db=project.db))
    document = worker.dispatch("snapshot", snapshot_request())
    assert "RatioCommentToCode" not in records(document)["cli/app.py"]["metrics"]
    assert "RatioCommentToCode" in mapping(document, "unavailable")["Python"]


def test_snapshot_does_not_report_a_population_only_metric_as_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A metric collected only to build a vector is a project threshold, not an entity one.

    Routines have no ``MaxCyclomaticStrict`` (verified), so reporting it per routine would
    bury the metrics that really are missing for a language under noise from every entity.
    """
    document = snapshot(
        monkeypatch,
        metrics_by_scope={"routine": ["CyclomaticStrict"]},
        population_metrics={"project": ["MaxCyclomaticStrict"]},
    )
    assert "MaxCyclomaticStrict" not in mapping(document, "unavailable").get("Python", [])
    assert mapping(document, "populations")["project"]["MaxCyclomaticStrict"] == [7]


def test_snapshot_computes_count_params_because_the_native_metric_is_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Requirement 3.5 and research.md: native CountParams is None for Python, so the
    # synthetic must supply it and must not be reported unavailable.
    document = snapshot(monkeypatch, files=["cli/app.py", "util/text.py"])
    assert records(document)["text.wrap_lines"]["metrics"]["CountParams"] == 2
    assert "CountParams" not in mapping(document, "unavailable").get("Python", [])


def test_snapshot_computes_count_decl_method_non_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    # CountDeclMethod 4 less two per auto property (1) = 2.
    assert records(snapshot(monkeypatch))["app.Runner"]["metrics"]["CountDeclMethodNonStub"] == 2


def test_snapshot_treats_a_missing_auto_property_count_as_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Python classes have no CountDeclPropertyAuto (verified): every declared method counts.
    project = fake_project()
    project.runner.values = {"CountDeclMethod": 4, "PercentLackOfCohesion": None}
    install(monkeypatch, FakeUnderstand(db=project.db))
    document = worker.dispatch("snapshot", snapshot_request())
    assert records(document)["app.Runner"]["metrics"]["CountDeclMethodNonStub"] == 4


def test_snapshot_never_reports_a_negative_non_stub_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = fake_project()
    project.runner.values = {"CountDeclMethod": 1, "CountDeclPropertyAuto": 3}
    install(monkeypatch, FakeUnderstand(db=project.db))
    document = worker.dispatch("snapshot", snapshot_request())
    assert records(document)["app.Runner"]["metrics"]["CountDeclMethodNonStub"] == 0


def test_snapshot_reports_a_synthetic_as_unavailable_when_its_input_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = fake_project()
    project.runner.values = {}
    install(monkeypatch, FakeUnderstand(db=project.db))
    document = worker.dispatch("snapshot", snapshot_request())
    assert "CountDeclMethodNonStub" not in records(document)["app.Runner"]["metrics"]
    assert "CountDeclMethodNonStub" in mapping(document, "unavailable")["Python"]


def test_snapshot_computes_only_the_synthetics_the_request_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Without the synthetic, CountParams falls back to Understand's native metric, which is
    # unset for Python — so it becomes an unavailable metric instead of a value.
    document = snapshot(monkeypatch, synthetic=["CountDeclMethodNonStub"])
    assert "CountParams" not in records(document)["app.build_parser"]["metrics"]
    assert "CountParams" in mapping(document, "unavailable")["Python"]


# --- snapshot: ignore rules ------------------------------------------------------


def test_snapshot_drops_an_entity_matching_an_ignore_regex(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = snapshot(monkeypatch, ignore={"routine": [r"^app\.build_"]})
    assert "app.build_parser" not in records(document)
    assert "app.Runner" in records(document)


def test_snapshot_matches_a_file_ignore_regex_against_the_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = snapshot(monkeypatch, ignore={"file": ["^cli/"]})
    assert "cli/app.py" not in records(document)


def test_snapshot_drops_ignored_entities_from_the_populations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Requirement 3.6 and the 4.1 contract: population vectors arrive already filtered,
    # because evaluate_thresholds applies ignore rules to entity keys only.
    unfiltered = snapshot(monkeypatch, population_metrics={"routine": ["CyclomaticStrict"]})
    filtered = snapshot(
        monkeypatch,
        population_metrics={"routine": ["CyclomaticStrict"]},
        ignore={"routine": [r"^app\."]},
    )
    assert mapping(unfiltered, "populations")["routine"]["CyclomaticStrict"] == [1, 3, 7]
    assert mapping(filtered, "populations")["routine"]["CyclomaticStrict"] == [1, 3]


def test_snapshot_refuses_an_ignore_pattern_that_is_not_a_regular_expression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = FakeUnderstand(db=fake_project().db)
    install(monkeypatch, api)
    error = envelope(worker.dispatch("snapshot", snapshot_request(ignore={"file": ["("]})))
    assert error["type"] == "BadRequest"
    assert api.opened == []


# --- snapshot: populations -------------------------------------------------------


def test_snapshot_populations_span_the_project_not_only_the_requested_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A stats-prefixed threshold is evaluated against the whole population of its scope,
    # while only `files` produces records.
    document = snapshot(monkeypatch, population_metrics={"file": ["CountLineCode"]})
    assert mapping(document, "populations")["file"]["CountLineCode"] == [2, 9, 26]
    assert set(records(document)) == {"cli/app.py", "app.build_parser", "app.Runner"}


def test_snapshot_reads_a_plain_project_metric_from_the_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The 4.1 contract: a plain project metric is read from a single-element population
    # vector, so omitting it would stop project.MaxCyclomaticStrict from ever firing.
    document = snapshot(monkeypatch, population_metrics={"project": ["MaxCyclomaticStrict"]})
    assert mapping(document, "populations")["project"]["MaxCyclomaticStrict"] == [7]


def test_snapshot_backs_a_prefixed_project_metric_with_the_routine_population(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``project.AVG:CyclomaticStrict`` is the mean over routines (req 5.4), not a database
    metric: ``db.metric(['CyclomaticStrict'])`` is ``None`` (verified)."""
    document = snapshot(monkeypatch, population_metrics={"project": ["AVG:CyclomaticStrict"]})
    assert mapping(document, "populations")["project"]["CyclomaticStrict"] == [1, 3, 7]


def test_snapshot_prefers_the_routine_population_to_the_database_total_for_a_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``project.AVG:CountLineCode`` is the mean routine length, not the project's total.

    The database answers ``CountLineCode`` as well — 37 lines of code in all — so a prefixed
    project metric read from the database would compare a whole project against a per-routine
    limit and report a violation on every run.
    """
    document = snapshot(monkeypatch, population_metrics={"project": ["AVG:CountLineCode"]})
    assert mapping(document, "populations")["project"]["CountLineCode"] == [2, 9, 17]


def test_snapshot_falls_back_to_the_routine_population_for_an_unknown_project_metric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = snapshot(monkeypatch, population_metrics={"project": ["CyclomaticStrict"]})
    assert mapping(document, "populations")["project"]["CyclomaticStrict"] == [1, 3, 7]


def test_snapshot_omits_a_population_it_has_no_values_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = snapshot(monkeypatch, population_metrics={"project": ["NoSuchMetric"]})
    assert document["populations"] == {}


def test_snapshot_reports_only_the_populations_that_were_asked_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The routine population backs the prefixed project vector, but nothing asked for a
    # routine-scope population, so none is reported.
    document = snapshot(monkeypatch, population_metrics={"project": ["AVG:CyclomaticStrict"]})
    assert set(document["populations"]) == {"project"}


# --- snapshot: dependency edges --------------------------------------------------


def test_snapshot_reports_file_edges_with_their_reference_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert listing(snapshot(monkeypatch), "file_edges") == [
        {"src": "cli/app.py", "dst": "util/text.py", "refs": 3, "crosses_arch": True}
    ]


def test_snapshot_marks_a_file_edge_inside_one_architecture_node_as_not_crossing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = snapshot(monkeypatch, depth=0)
    assert listing(document, "file_edges")[0]["crosses_arch"] is False


def test_snapshot_bounds_file_edges_to_the_requested_files_and_their_neighbours(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # native/util.c is neither requested nor a neighbour of cli/app.py, so its edges stay out.
    project = fake_project()
    project.native.deps = {project.text: [object()]}
    install(monkeypatch, FakeUnderstand(db=project.db))
    document = worker.dispatch("snapshot", snapshot_request())
    assert [(edge["src"], edge["dst"]) for edge in listing(document, "file_edges")] == [
        ("cli/app.py", "util/text.py")
    ]


def test_snapshot_stops_at_the_direct_neighbours_of_the_requested_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``util/text.py`` is a neighbour of the requested file; what *it* depends on is not.

    Bounding the edge set is what keeps extraction proportional to the change (req 4.11)
    instead of walking the whole dependency graph of the repository.
    """
    edges = [(edge["src"], edge["dst"]) for edge in listing(snapshot(monkeypatch), "file_edges")]
    assert edges == [("cli/app.py", "util/text.py")]


def test_snapshot_reports_class_edges_between_entity_key_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # analysis/structure/fan.py looks class endpoints up by EntityKey.token.
    edge = listing(snapshot(monkeypatch), "class_edges")[0]
    assert EntityKey.from_token(edge["src"]).longname == "app.Runner"
    assert EntityKey.from_token(edge["dst"]).longname == "text.Helper"
    assert edge["refs"] == 2


def test_snapshot_leaves_an_ignored_class_out_of_the_class_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ignored entity is excluded from *all* evaluation (req 3.6), structural rules too.

    Leaving it as an edge endpoint would let it reach the fan and cycle rules through the
    back door, and would name an entity no finding could ever be attached to.
    """
    document = snapshot(monkeypatch, ignore={"class": ["Helper"]})
    assert listing(document, "class_edges") == []


def test_snapshot_omits_every_edge_when_edges_are_not_wanted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = snapshot(monkeypatch, include_edges=False)
    assert document["file_edges"] == []
    assert document["class_edges"] == []
    assert document["arch_edges"] == []
    assert document["arch_nodes"], "architecture membership is not an edge (req 9.7)"


# --- snapshot: architecture ------------------------------------------------------


def test_snapshot_lists_the_architecture_nodes_at_the_requested_depth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert listing(snapshot(monkeypatch), "arch_nodes") == [
        {"path": "Directory Structure/cli", "members": ["cli/app.py"]},
        {"path": "Directory Structure/native", "members": ["native/util.c"]},
        {"path": "Directory Structure/util", "members": ["util/text.py"]},
        # The vendored library file is a member of the directory node all the same; it is
        # not a member of the snapshot, so no rule can ever be evaluated against it.
        {"path": "Directory Structure/vendor", "members": []},
    ]


def test_snapshot_reports_architecture_edges_with_their_reference_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert listing(snapshot(monkeypatch), "arch_edges") == [
        {
            "src": "Directory Structure/cli",
            "dst": "Directory Structure/util",
            "refs": 3,
            "crosses_arch": True,
        }
    ]


def test_snapshot_drops_an_architecture_edge_that_trims_onto_its_own_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # At depth 0 every node trims to the architecture itself, so nothing crosses.
    assert snapshot(monkeypatch, depth=0)["arch_edges"] == []


def deep_architecture() -> FakeArch:
    """A two-level tree whose ``cli`` node depends on a node below the level in question."""
    global _ARCH_ROOT
    root = FakeArch(
        "Directory Structure",
        children=[
            FakeArch(
                "Directory Structure/cli",
                ents=[a_file("cli/app.py")],
                depends={"Directory Structure/util/helpers": 5},
            ),
            FakeArch(
                "Directory Structure/util",
                children=[
                    FakeArch(
                        "Directory Structure/util/helpers", ents=[a_file("util/helpers/text.py")]
                    )
                ],
            ),
        ],
    )
    _ARCH_ROOT = root
    return root


def test_snapshot_trims_an_architecture_edge_to_the_requested_depth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A node may depend on one deeper than the level the structural rules work at.

    The dependency belongs to the depth the caller asked for, so its target is trimmed to
    that depth; dropping it instead would hide the dependency from every rule at that level.
    """
    install(monkeypatch, FakeUnderstand(db=FakeDb([deep_architecture()])))
    document = worker.dispatch("snapshot", snapshot_request(files=[]))
    assert listing(document, "arch_edges") == [
        {
            "src": "Directory Structure/cli",
            "dst": "Directory Structure/util",
            "refs": 5,
            "crosses_arch": True,
        }
    ]


def test_snapshot_names_the_available_architectures_when_the_requested_one_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = fake_project()
    install(monkeypatch, FakeUnderstand(db=project.db))
    error = envelope(worker.dispatch("snapshot", snapshot_request(architecture="Layers")))
    assert error["type"] == "ArchitectureNotFound"
    assert error["available"] == ["Directory Structure"]
    assert project.db.closed


# --- snapshot: the request and the database ---------------------------------------


def test_snapshot_passes_parse_errors_through(monkeypatch: pytest.MonkeyPatch) -> None:
    # Requirement 2.6: the API exposes no parse-error accessor, so `und analyze` supplies
    # them and the worker only carries them into the snapshot.
    errors = [{"path": "cli/app.py", "line": 3, "message": "unexpected indent"}]
    document = snapshot(monkeypatch, parse_errors=errors)
    assert ProjectSnapshot.model_validate(document).parse_errors[0].message == "unexpected indent"


def test_snapshot_reports_no_parse_errors_when_none_are_supplied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert snapshot(monkeypatch)["parse_errors"] == []


def test_snapshot_describes_the_side_it_was_asked_for(monkeypatch: pytest.MonkeyPatch) -> None:
    assert snapshot(monkeypatch, side="before")["side"] == "before"


def test_snapshot_defaults_to_the_after_side(monkeypatch: pytest.MonkeyPatch) -> None:
    request = snapshot_request()
    del request["side"]
    install(monkeypatch, FakeUnderstand(db=fake_project().db))
    assert worker.dispatch("snapshot", request)["side"] == "after"


def test_snapshot_closes_the_database(monkeypatch: pytest.MonkeyPatch) -> None:
    project = fake_project()
    install(monkeypatch, FakeUnderstand(db=project.db))
    worker.dispatch("snapshot", snapshot_request())
    assert project.db.closed


def test_snapshot_closes_the_database_when_the_api_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = fake_project()
    install(monkeypatch, FakeUnderstand(db=project.db))
    error = envelope(worker.dispatch("snapshot", snapshot_request(architecture="Nope")))
    assert error["type"] == "ArchitectureNotFound"
    assert project.db.closed


@pytest.mark.parametrize(
    ("overrides", "expected_key"),
    [
        pytest.param({"db": ""}, "db", id="empty database path"),
        pytest.param({"root": ""}, "root", id="empty analysis root"),
        pytest.param({"files": "cli/app.py"}, "files", id="files not a list"),
        pytest.param({"kinds_by_scope": ["file"]}, "kinds_by_scope", id="kinds not an object"),
        pytest.param({"kinds_by_scope": {"file": ""}}, "kinds_by_scope", id="empty kind string"),
        pytest.param({"metrics_by_scope": {"file": "x"}}, "metrics_by_scope", id="metrics"),
        pytest.param({"population_metrics": 7}, "population_metrics", id="populations"),
        pytest.param({"synthetic": None}, "synthetic", id="explicit null synthetic"),
        pytest.param({"architecture": ""}, "architecture", id="empty architecture"),
        pytest.param({"depth": -1}, "depth", id="negative depth"),
        pytest.param({"include_edges": "yes"}, "include_edges", id="edges not a boolean"),
        pytest.param({"side": "sideways"}, "side", id="unknown side"),
        pytest.param({"parse_errors": "none"}, "parse_errors", id="parse errors not a list"),
    ],
)
def test_snapshot_rejects_a_malformed_request_before_opening_a_database(
    monkeypatch: pytest.MonkeyPatch, overrides: dict[str, object], expected_key: str
) -> None:
    api = FakeUnderstand(db=fake_project().db)
    install(monkeypatch, api)
    error = envelope(worker.dispatch("snapshot", snapshot_request(**overrides)))
    assert error["type"] == "BadRequest"
    assert expected_key in error["message"]
    assert api.opened == []


# --- the error envelope ----------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "expected_type"),
    [
        pytest.param("NoApiLicense: Understand license required", "NoApiLicense", id="license"),
        pytest.param("DBUnableOpen: unable to open database", "DBUnableOpen", id="unopenable"),
        pytest.param("DBOldVersion: database needs to be rebuilt", "DBOldVersion", id="old"),
        pytest.param("DBUnknownVersion: rebuild required", "DBUnknownVersion", id="unknown"),
        pytest.param("DBCorrupt: bad database file", "DBCorrupt", id="corrupt"),
        pytest.param("DBAlreadyOpen: only one database", "DBAlreadyOpen", id="already open"),
        pytest.param("something the docs never mentioned", "UnderstandError", id="unclassified"),
    ],
)
def test_an_api_error_becomes_a_typed_envelope(
    monkeypatch: pytest.MonkeyPatch, message: str, expected_type: str
) -> None:
    install(monkeypatch, FakeUnderstand(open_error=message))
    error = envelope(worker.dispatch("archs", archs_request()))
    assert error["type"] == expected_type
    assert error["message"] == message


def test_an_unknown_operation_is_an_envelope_that_names_the_known_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install(monkeypatch, FakeUnderstand())
    error = envelope(worker.dispatch("impact", {}))
    assert error["type"] == "UnknownOperation"
    assert "impact" in error["message"]
    for known in ("ping", "catalogue", "archs", "snapshot"):
        assert known in error["message"]


def test_an_unexpected_error_is_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    # The envelope is the answer for failures the caller can act on; a bug in the worker
    # must reach the runner as a crash, not as a plausible-looking result.
    class ExplodingApi(FakeUnderstand):
        def version(self) -> str:
            raise RuntimeError("boom")

    install(monkeypatch, ExplodingApi())
    with pytest.raises(RuntimeError, match="boom"):
        worker.dispatch("ping", {})


# --- the command-line entry point -------------------------------------------------


def test_main_writes_one_json_document_to_stdout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    install(monkeypatch, FakeUnderstand())
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert worker.main(["ping"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["version"] == "6.5.1204"
    assert captured.err == ""


def test_main_reads_the_request_from_stdin(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    install(monkeypatch, FakeUnderstand(db=FakeDb([directory_structure()])))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(archs_request(depth=1))))
    assert worker.main(["archs"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert [node["path"] for node in result["nodes"]] == [
        "Directory Structure/src",
        "Directory Structure/native",
    ]


def test_main_exits_zero_for_an_error_envelope(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    install(monkeypatch, FakeUnderstand(open_error="NoApiLicense: Understand license required"))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(archs_request())))
    assert worker.main(["archs"]) == 0
    assert json.loads(capsys.readouterr().out)["error"]["type"] == "NoApiLicense"


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("{not json", id="not json"),
        pytest.param("[1, 2]", id="not an object"),
        pytest.param('"ping"', id="a bare string"),
    ],
)
def test_main_rejects_a_request_body_that_is_not_a_json_object(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], body: str
) -> None:
    install(monkeypatch, FakeUnderstand())
    monkeypatch.setattr(sys, "stdin", io.StringIO(body))
    assert worker.main(["ping"]) == 0
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["type"] == "BadRequest"


def test_main_without_an_operation_answers_with_usage(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    install(monkeypatch, FakeUnderstand())
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert worker.main([]) == 0
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["type"] == "BadRequest"
    assert "worker.py" in error["message"]


def test_main_does_not_read_an_interactive_stdin(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    install(monkeypatch, FakeUnderstand())
    monkeypatch.setattr(sys, "stdin", InteractiveStdin("ignored"))
    assert worker.main(["ping"]) == 0
    assert json.loads(capsys.readouterr().out)["version"] == "6.5.1204"


def test_main_falls_back_to_the_process_arguments(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    install(monkeypatch, FakeUnderstand())
    monkeypatch.setattr(sys, "argv", ["worker.py", "ping"])
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert worker.main() == 0
    assert json.loads(capsys.readouterr().out)["version"] == "6.5.1204"


# --- contract: the real worker under the real interpreter -------------------------


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


def contract_files(databases: SampleDatabases, side: Side) -> list[str]:
    """The sample project's files as the gate names them: relative to the analysis root."""
    root = databases.root(side)
    return sorted(Path(name).relative_to(root).as_posix() for name in databases.list_files(side))


def contract_request(databases: SampleDatabases, side: Side) -> dict[str, object]:
    """The request ``SnapshotExtractor`` will build: kind strings from ``SCOPE_KINDS``."""
    return {
        "db": str(databases.db(side)),
        "side": side,
        "root": str(databases.root(side)),
        "files": contract_files(databases, side),
        "kinds_by_scope": dict(SCOPE_KINDS),
        "metrics_by_scope": {
            "routine": ["CyclomaticStrict", "MaxNesting", "CountLineCode", "CountParams"],
            "class": ["CountDeclMethod", "CountDeclMethodNonStub", "PercentLackOfCohesion"],
            "file": ["CountLineCode", "MaxCyclomaticStrict", "RatioCommentToCode"],
        },
        "synthetic": ["CountParams", "CountDeclMethodNonStub"],
        "population_metrics": {
            "routine": ["CyclomaticStrict"],
            "project": ["AVG:CyclomaticStrict", "MaxCyclomaticStrict"],
        },
        "ignore": {},
        "architecture": "Directory Structure",
        "depth": 1,
        "include_edges": True,
    }


def contract_snapshot(databases: SampleDatabases, side: Side) -> ProjectSnapshot:
    """Run the real worker against one sample database and validate what it answered."""
    document = run_worker(upython_or_skip(), "snapshot", contract_request(databases, side))
    return ProjectSnapshot.model_validate(document)


def file_key(path: str) -> EntityKey:
    """The key of a file entity; its long name is the path, never the absolute one."""
    return EntityKey(scope="file", path=path, longname=path, parameters=None)


def routine_key(path: str, longname: str, parameters: str) -> EntityKey:
    """The key of a routine defined in ``path``."""
    return EntityKey(scope="routine", path=path, longname=longname, parameters=parameters)


@pytest.mark.contract
def test_ping_under_upython_reports_the_installed_version() -> None:
    result = run_worker(upython_or_skip(), "ping", {})
    assert result["version"].split(".")[0].isdigit()


@pytest.mark.contract
def test_catalogue_under_upython_lists_real_python_metrics() -> None:
    result = run_worker(upython_or_skip(), "catalogue", {"kinds": [ROUTINE_KIND]})
    assert {"CyclomaticStrict", "CountLineCode", "MaxNesting"} <= set(
        result["metrics"][ROUTINE_KIND]
    )


@pytest.mark.contract
def test_archs_under_upython_lists_the_directory_structure(
    sample_databases: SampleDatabases,
) -> None:
    request = {
        "db": str(sample_databases.after_db),
        "architecture": "Directory Structure",
        "depth": 1,
    }
    result = run_worker(upython_or_skip(), "archs", request)
    assert result["root_archs"] == ["Directory Structure"]
    nodes = {node["path"]: node["files"] for node in result["nodes"]}
    assert nodes["Directory Structure/cli"] == ["cli/app.py"]
    assert nodes["Directory Structure/analysis"] == ["analysis/engine.py", "analysis/rules.py"]
    # The library files Understand injects (conf/understand/python/...) are not architecture
    # members, so no absolute path can appear here.
    assert not [name for files in nodes.values() for name in files if name.startswith("/")]


@pytest.mark.contract
def test_archs_under_upython_reports_a_missing_architecture(
    sample_databases: SampleDatabases,
) -> None:
    request = {"db": str(sample_databases.after_db), "architecture": "No Such Arch", "depth": 1}
    error = envelope(run_worker(upython_or_skip(), "archs", request))
    assert error["type"] == "ArchitectureNotFound"
    assert error["available"] == ["Directory Structure"]


@pytest.mark.contract
def test_an_unopenable_database_under_upython_is_a_typed_envelope(tmp_path: Path) -> None:
    request = {"db": str(tmp_path / "missing.und"), "architecture": "Directory Structure"}
    error = envelope(run_worker(upython_or_skip(), "archs", request))
    assert error["type"] == "DBUnableOpen"


SHADOW_MAIN: Final = """# mypy: ignore-errors
\"\"\"Top-level entry point, sitting directly in the analysis root.\"\"\"

from pkg.core import Engine


def run(argv):
    return Engine().work(argv)
"""

SHADOW_CORE: Final = """# mypy: ignore-errors
\"\"\"A module one directory below the analysis root.\"\"\"


class Engine:
    def work(self, argv):
        return sorted(argv)
"""

NESTED_ENTRY: Final = """# mypy: ignore-errors
\"\"\"The only source above the deepest common ancestor of the analysed files.\"\"\"

from app.core.deep.mod import work


def entry(argv):
    return work(argv)
"""

NESTED_MOD: Final = """# mypy: ignore-errors
\"\"\"Four directories below the analysis root.\"\"\"


def work(argv):
    return sorted(argv)
"""


def build_shadows(workdir: Path, sources: dict[str, str]) -> dict[Side, tuple[Path, Path]]:
    """Build a ``before``/``after`` shadow pair from the same sources, as the gate does.

    Two roots with different names and identical content: anything that differs between the
    two answers is the shadow's name leaking into the snapshot.
    """
    probe = understand_probe()
    if not probe.usable:
        pytest.skip(f"needs a licensed SciTools Understand: {probe.reason}")
    und = probe.und
    assert und is not None  # guaranteed by probe.usable
    built: dict[Side, tuple[Path, Path]] = {}
    for side in ("before", "after"):
        root = workdir / side
        for name, text in sources.items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        db = workdir / f"{side}.und"
        argv = [
            "-quiet",
            "create",
            "-db",
            str(db),
            "-languages",
            "python",
            "-local",
            "add",
            str(root),
            "analyze",
        ]
        proc = subprocess.run(
            [str(und), *argv],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_S,
            check=False,
        )
        if proc.returncode != 0 or not db.exists():
            pytest.fail(f"building the {side} shadow failed: {proc.stderr.strip()}")
        built[side] = (db, root)
    return built


@pytest.fixture(scope="session")
def shadow_databases(tmp_path_factory: pytest.TempPathFactory) -> dict[Side, tuple[Path, Path]]:
    """Two shadow trees named ``before`` and ``after``, each with a file in its own root.

    This is the layout ``models/cache.py`` mandates for every run of the gate, and the layout
    of any repository with a top-level source file. The sample project has only
    subdirectories, which is exactly why it cannot show what this fixture shows.
    """
    return build_shadows(
        tmp_path_factory.mktemp("shadow-databases"),
        {"main.py": SHADOW_MAIN, "pkg/core.py": SHADOW_CORE},
    )


@pytest.fixture(scope="session")
def nested_databases(tmp_path_factory: pytest.TempPathFactory) -> dict[Side, tuple[Path, Path]]:
    """Two shadows whose sources all sit under ``src/app``, with nothing analysed above.

    ``README.md`` and ``pyproject.toml`` are there to show that files Understand does not
    analyse do not enter the architecture, which is what makes this layout look, from the
    inside, exactly like a shadow with an inserted level.
    """
    return build_shadows(
        tmp_path_factory.mktemp("nested-databases"),
        {
            "README.md": "# sample\n",
            "pyproject.toml": '[project]\nname = "sample"\n',
            "src/app/entry.py": NESTED_ENTRY,
            "src/app/core/deep/mod.py": NESTED_MOD,
        },
    )


def shadow_snapshot(databases: dict[Side, tuple[Path, Path]], side: Side) -> ProjectSnapshot:
    """Run the real worker against one shadow database and validate what it answered."""
    db, root = databases[side]
    request = {
        "db": str(db),
        "side": side,
        "root": str(root),
        "files": ["main.py", "pkg/core.py"],
        "kinds_by_scope": dict(SCOPE_KINDS),
        "metrics_by_scope": {"routine": ["CyclomaticStrict"], "file": ["CountLineCode"]},
        "synthetic": ["CountParams"],
        "population_metrics": {},
        "ignore": {},
        "architecture": "Directory Structure",
        "depth": 1,
        "include_edges": True,
    }
    return ProjectSnapshot.model_validate(run_worker(upython_or_skip(), "snapshot", request))


def nested_snapshot(databases: dict[Side, tuple[Path, Path]], side: Side) -> ProjectSnapshot:
    """Run the real worker against one nested shadow and validate what it answered."""
    db, root = databases[side]
    request = {
        "db": str(db),
        "side": side,
        "root": str(root),
        "files": ["src/app/entry.py", "src/app/core/deep/mod.py"],
        "kinds_by_scope": dict(SCOPE_KINDS),
        "metrics_by_scope": {"file": ["CountLineCode"]},
        "synthetic": [],
        "population_metrics": {},
        "ignore": {},
        "architecture": "Directory Structure",
        "depth": 1,
        "include_edges": True,
    }
    return ProjectSnapshot.model_validate(run_worker(upython_or_skip(), "snapshot", request))


@pytest.mark.contract
def test_snapshot_under_upython_agrees_with_the_archs_operation_on_a_nested_tree(
    nested_databases: dict[Side, tuple[Path, Path]],
) -> None:
    """A repository whose sources all sit under one nested path is not a shadow level.

    Understand roots ``Directory Structure`` at the parent of the deepest common ancestor of
    the analysed files, so ``src/app`` presents a single child holding every file — the same
    shape a shadow with a top-level file presents. Reading that as the inserted level deletes
    a real directory: ``entry.py`` lands in no node at all and so becomes invisible to arch
    cycles, layer rules and coupling, its edges can never cross an architecture boundary, and
    the node it should have had (``Directory Structure/app``) is replaced by
    ``Directory Structure/core``, which names no directory the repository has and which no
    layer rule written against the ``archs`` operation could ever match.
    """
    for side in ("before", "after"):
        db, _ = nested_databases[side]
        request = {"db": str(db), "architecture": "Directory Structure", "depth": 1}
        nodes = run_worker(upython_or_skip(), "archs", request)["nodes"]
        snapshot = nested_snapshot(nested_databases, side)

        assert sorted(name for node in nodes for name in node["files"]) == [
            "app/core/deep/mod.py",
            "app/entry.py",
        ]
        assert [node["path"] for node in nodes] == ["Directory Structure/app"]
        assert [node.path for node in snapshot.arch_nodes] == [node["path"] for node in nodes]
        assert not [
            key
            for key, record in snapshot.entities.items()
            if key.scope == "file" and not record.archs
        ]

    before = nested_snapshot(nested_databases, "before")
    after = nested_snapshot(nested_databases, "after")
    assert set(before.entities) == set(after.entities)
    assert {key.path for key in after.entities if key.scope == "file"} == {
        "src/app/entry.py",
        "src/app/core/deep/mod.py",
    }


@pytest.mark.contract
def test_snapshot_under_upython_matches_keys_across_two_shadow_roots(
    shadow_databases: dict[Side, tuple[Path, Path]],
) -> None:
    """The regression this task was rejected for: the real shadow layout, both sides.

    ``Ent.relname()`` answers ``before/main.py`` and ``after/main.py`` for the same file of
    the same repository, and the architecture gains a level named after the shadow. Keyed on
    those, both sides come back with zero entity records and nothing matches — a completely
    green run that gates nothing, on the layout of essentially every real repository.
    """
    before = shadow_snapshot(shadow_databases, "before")
    after = shadow_snapshot(shadow_databases, "after")

    assert before.entities, "the before side must hold the entities of the requested files"
    assert set(before.entities) == set(after.entities)
    assert {key.path for key in after.entities if key.scope == "file"} == {
        "main.py",
        "pkg/core.py",
    }
    assert not [key for key in after.entities if key.path.startswith(("before/", "after/"))]
    assert after.entities[file_key("main.py")].metrics["CountLineCode"] == 3

    assert [node.path for node in before.arch_nodes] == [node.path for node in after.arch_nodes]
    assert [node.members for node in before.arch_nodes] == [
        node.members for node in after.arch_nodes
    ]
    assert [node.path for node in after.arch_nodes] == [
        "Directory Structure",
        "Directory Structure/pkg",
    ]
    # main.py is in no node below the root, so the architecture itself holds it (req 9.7).
    assert after.entities[file_key("main.py")].archs == ["Directory Structure"]
    assert before.entities[file_key("main.py")].archs == ["Directory Structure"]

    assert [(edge.src, edge.dst) for edge in before.file_edges] == [("main.py", "pkg/core.py")]
    assert [(edge.src, edge.dst) for edge in after.file_edges] == [("main.py", "pkg/core.py")]
    assert after.file_edges[0].crosses_arch is True


@pytest.mark.contract
def test_snapshot_under_upython_extracts_the_sample_project(
    sample_databases: SampleDatabases,
) -> None:
    """The task's done-criterion: the real worker, the real database, a real snapshot."""
    document = run_worker(
        upython_or_skip(), "snapshot", contract_request(sample_databases, "after")
    )
    snapshot = ProjectSnapshot.model_validate(document)
    assert snapshot.side == "after"
    assert set(snapshot.languages) == {"Python", "C++"}
    parser = snapshot.entities[routine_key("cli/app.py", "app.build_parser", "argv")]
    assert parser.metrics["CyclomaticStrict"] == 7
    assert parser.metrics["MaxNesting"] == 4
    assert parser.metrics["CountLineCode"] == 17
    assert parser.archs == ["Directory Structure/cli"]


@pytest.mark.contract
def test_snapshot_under_upython_sees_the_change_the_sample_project_makes(
    sample_databases: SampleDatabases,
) -> None:
    # cli/app.py gains check_command and loses legacy_entry; the C++ file is new.
    before = contract_snapshot(sample_databases, "before")
    after = contract_snapshot(sample_databases, "after")
    was = {key.longname for key in before.entities}
    now = {key.longname for key in after.entities}
    assert "app.check_command" in now - was
    assert "app.legacy_entry" in was - now
    assert "sample::count_over" in now - was
    parser = before.entities[routine_key("cli/app.py", "app.build_parser", "argv")]
    assert parser.metrics["CyclomaticStrict"] == 3
    assert parser.metrics["MaxNesting"] == 2
    assert parser.metrics["CountLineCode"] == 6


@pytest.mark.contract
def test_snapshot_under_upython_keys_files_identically_on_both_sides(
    sample_databases: SampleDatabases,
) -> None:
    """The two databases are built from different roots, and a file's key must not notice.

    ``Ent.longname()`` on a file is the absolute path and embeds that root, so keying by it
    would make every before/after file key differ and switch off every file-scope ratchet.
    """
    before = contract_snapshot(sample_databases, "before")
    after = contract_snapshot(sample_databases, "after")
    was = {key for key in before.entities if key.scope == "file"}
    now = {key for key in after.entities if key.scope == "file"}
    assert was, "the before side has file entities to compare"
    assert was < now, "only native/scan.cpp is added, so every before file key survives"
    assert not [key for key in now if key.longname != key.path]


@pytest.mark.contract
def test_snapshot_under_upython_leaves_out_the_injected_library_files(
    sample_databases: SampleDatabases,
) -> None:
    # tasks.md 3.2: Understand injects its Python stubs and ~600 `builtins.*` routines into
    # the file set, and the API reports them even though `und list files` hides them.
    snapshot = contract_snapshot(sample_databases, "after")
    assert not [key for key in snapshot.entities if key.path.startswith("/")]
    assert not [key for key in snapshot.entities if "builtin" in key.longname]
    assert len(snapshot.entities) == 21


@pytest.mark.contract
def test_snapshot_under_upython_turns_a_locale_formatted_ratio_into_a_number(
    sample_databases: SampleDatabases,
) -> None:
    # RatioCommentToCode really is a string from the API ('0,15' where LC_NUMERIC is German).
    snapshot = contract_snapshot(sample_databases, "after")
    ratio = snapshot.entities[file_key("cli/app.py")].metrics["RatioCommentToCode"]
    assert isinstance(ratio, float)
    assert 0.1 < ratio < 0.2


@pytest.mark.contract
def test_snapshot_under_upython_reports_the_dependency_edges_of_the_change(
    sample_databases: SampleDatabases,
) -> None:
    before = contract_snapshot(sample_databases, "before")
    after = contract_snapshot(sample_databases, "after")
    was = {(edge.src, edge.dst) for edge in before.file_edges}
    now = {(edge.src, edge.dst) for edge in after.file_edges}
    assert ("analysis/rules.py", "analysis/engine.py") in now - was
    assert ("cli/app.py", "understand/adapter.py") in now - was
    assert ("cli/app.py", "analysis/engine.py") in was & now
    crossing = {(edge.src, edge.dst) for edge in after.file_edges if edge.crosses_arch}
    assert ("analysis/rules.py", "analysis/engine.py") not in crossing
    assert ("cli/app.py", "understand/adapter.py") in crossing


@pytest.mark.contract
def test_snapshot_under_upython_reports_the_architecture_of_the_sample_project(
    sample_databases: SampleDatabases,
) -> None:
    snapshot = contract_snapshot(sample_databases, "after")
    nodes = {node.path: node.members for node in snapshot.arch_nodes}
    assert nodes["Directory Structure/analysis"] == ["analysis/engine.py", "analysis/rules.py"]
    assert nodes["Directory Structure/cli"] == ["cli/app.py"]
    edges = {(edge.src, edge.dst): edge.refs for edge in snapshot.arch_edges}
    assert edges[("Directory Structure/cli", "Directory Structure/understand")] == 4


@pytest.mark.contract
def test_snapshot_under_upython_reports_populations_and_unavailable_metrics(
    sample_databases: SampleDatabases,
) -> None:
    # Requirement 5.5: Python classes have no PercentLackOfCohesion (verified), and the 4.1
    # contract: a plain project metric arrives as a single-element vector.
    snapshot = contract_snapshot(sample_databases, "after")
    assert snapshot.unavailable == {"Python": ["PercentLackOfCohesion"]}
    routines = snapshot.populations["routine"]["CyclomaticStrict"]
    assert len(routines) == 11
    assert max(routines) == 7
    assert snapshot.populations["project"]["MaxCyclomaticStrict"] == [7]
    assert snapshot.populations["project"]["CyclomaticStrict"] == routines
