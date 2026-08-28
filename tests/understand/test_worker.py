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
from pathlib import Path
from types import ModuleType
from typing import Any, Final

import pytest
from conftest import SampleDatabases, understand_probe

from scitools_hook.understand import worker

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
WORKER_PATH: Final = REPO_ROOT / "src" / "scitools_hook" / "understand" / "worker.py"
"""The file under test, addressed as a path: two tests must not import it."""

SUBPROCESS_TIMEOUT_S: Final = 300


# --- a fake `understand` module -------------------------------------------------


class FakeUnderstandError(Exception):
    """Stand-in for ``understand.UnderstandError``; the worker maps its text to a type."""


class FakeEnt:
    """An entity; ``relname`` is ``None`` for everything that is not a file (verified)."""

    def __init__(self, relname: str | None) -> None:
        self._relname = relname

    def relname(self) -> str | None:
        """The project-relative path of a file entity, or ``None`` for other kinds."""
        return self._relname


class FakeArch:
    """An architecture node: a long name, child nodes and member entities."""

    def __init__(
        self,
        longname: str,
        children: Sequence[FakeArch] = (),
        ents: Sequence[FakeEnt] = (),
    ) -> None:
        self._longname = longname
        self._children = list(children)
        self._ents = list(ents)

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

    def walk(self) -> list[FakeArch]:
        """This node and every descendant, depth first."""
        found = [self]
        for child in self._children:
            found.extend(child.walk())
        return found


class FakeDb:
    """An opened database: root architectures, a lookup, and a recorded ``close``."""

    def __init__(self, roots: Sequence[FakeArch] = (), lookup_error: str | None = None) -> None:
        self._roots = list(roots)
        self._lookup_error = lookup_error
        self.closed = False

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

    assert result["error"]["type"] == "BadRequest"
    assert "describe" in result["error"]["message"]


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
    error = envelope(worker.dispatch("snapshot", {}))
    assert error["type"] == "UnknownOperation"
    assert "snapshot" in error["message"]
    for known in ("ping", "catalogue", "archs"):
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
