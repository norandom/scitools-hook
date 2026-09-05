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
import os
import subprocess
import sys

import pytest
from api_fakes import (
    FakeArch,
    FakeDb,
    FakeMetrics,
    FakeMetrics8,
    FakeUnderstand,
    InteractiveStdin,
    directory_structure,
    envelope,
    install,
)
from worker_projects import (
    CLASS_KIND,
    ROUTINE_KIND,
    SUBPROCESS_TIMEOUT_S,
    WORKER_PATH,
)

from scitools_hook.understand import worker

"""The file under test, addressed as a path: two tests must not import it."""


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


def test_importing_the_api_forces_a_numeric_locale_that_writes_a_dot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Measured: with a German LC_NUMERIC every SVG the graph export writes carries
    # `stroke-opacity="0,000000"`, which is not a number a renderer will accept. The graph
    # engine reads the environment, not the process's C locale — `locale.setlocale` does not
    # help — and it reads it when it initializes, so setting the variable after the import
    # works too. It is set here because this is the one choke point every operation passes.
    monkeypatch.setenv("LC_NUMERIC", "de_DE.UTF-8")
    install(monkeypatch, FakeUnderstand())
    assert "error" not in worker.dispatch("ping", {})
    assert os.environ["LC_NUMERIC"] == "C"


def test_a_missing_api_module_is_an_envelope_not_an_import_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `None` in sys.modules is what an interpreter without the API looks like to `import`.
    monkeypatch.setitem(sys.modules, "understand", None)
    error = envelope(worker.dispatch("ping", {}))
    assert error["type"] == "ApiUnavailable"
    assert "understand" in error["message"]


# --- catalogue -------------------------------------------------------------------


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


def test_catalogue_reads_ids_off_the_metric_objects_understand_8_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """8.0's ``Metric.list()`` answers objects; the wire contract still carries id strings.

    Measured on 8.0.1262 with the 0.1.0a6 worker: the objects sorted, the answer failed to
    serialise (``TypeError: Object of type Metric is not JSON serializable``), and every
    check on a repository with ``project.languages`` configured exited 5.
    """
    metrics = FakeMetrics8({ROUTINE_KIND: ["CyclomaticStrict", "CountLineCode"], CLASS_KIND: []})
    install(monkeypatch, catalogue_api(metrics))  # type: ignore[arg-type]
    result = worker.dispatch("catalogue", {"kinds": [ROUTINE_KIND, CLASS_KIND]})
    assert result["metrics"] == {
        ROUTINE_KIND: ["CountLineCode", "CyclomaticStrict"],
        CLASS_KIND: [],
    }


def test_catalogue_describes_through_lookup_when_the_api_has_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """8.0: ``Metric.lookup(id).description()``; an unknown id answers ``""`` as before."""
    metrics = FakeMetrics8(
        {ROUTINE_KIND: ["CyclomaticStrict"]}, {"CyclomaticStrict": "Strict McCabe complexity"}
    )
    install(monkeypatch, catalogue_api(metrics))  # type: ignore[arg-type]
    result = worker.dispatch(
        "catalogue", {"kinds": [ROUTINE_KIND], "describe": ["CyclomaticStrict", "NoSuchMetric"]}
    )
    assert result["descriptions"] == {
        "CyclomaticStrict": "Strict McCabe complexity",
        "NoSuchMetric": "",
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


PLUGIN_TAGS = {
    "CountGlobalsModified": [
        "Category: Coupling",
        "Target: Functions",
        "Language: C",
        "Language: C++",
        "Language: Python",
        "Language: Pascal",
        "Language: Web",
    ],
    "CorePercentage": [
        "Category: Coupling",
        "Solution: Project Quality",
        "Language: Any",
        "Dependencies",
        "Target: Architectures",
        "Target: Project",
    ],
}
"""``Metric.lookup(id).tags()`` on Build 1262, verbatim, for one routine and one project metric.

A plugin metric is invisible to ``Metric.list(kind)`` -- measured, the routine kind string
answers 18 metrics and none of these is among them -- so its tags are the only thing that
says what it applies to. ``Any`` is Understand's word for no language restriction, and a tag
that is neither a target nor a language (``Category:``, ``Solution:``, ``Dependencies``) is
not this feature's business.
"""


def test_catalogue_answers_the_targets_and_languages_a_plugin_metric_declares(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement 5.1: a metric ``Metric.list`` never names is still offered where it applies."""
    metrics = FakeMetrics8({ROUTINE_KIND: []}, tags=PLUGIN_TAGS)
    install(monkeypatch, catalogue_api(metrics))  # type: ignore[arg-type]

    result = worker.dispatch("catalogue", {"kinds": [], "lookup": ["CountGlobalsModified"]})

    assert result["lookup"] == {
        "CountGlobalsModified": {
            "targets": ["Functions"],
            "languages": ["C", "C++", "Python", "Pascal", "Web"],
        }
    }


def test_a_metric_that_applies_to_two_scopes_declares_both(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``CorePercentage`` is an architecture *and* a project metric, and says so in its tags."""
    metrics = FakeMetrics8({}, tags=PLUGIN_TAGS)
    install(monkeypatch, catalogue_api(metrics))  # type: ignore[arg-type]

    answer = worker.dispatch("catalogue", {"kinds": [], "lookup": ["CorePercentage"]})

    assert answer["lookup"]["CorePercentage"] == {
        "targets": ["Architectures", "Project"],
        "languages": ["Any"],
    }


def test_an_id_this_build_does_not_know_answers_null(monkeypatch: pytest.MonkeyPatch) -> None:
    """``Metric.lookup`` answers ``None`` for an unknown id, and so does the catalogue."""
    install(monkeypatch, catalogue_api(FakeMetrics8({}, tags=PLUGIN_TAGS)))  # type: ignore[arg-type]

    answer = worker.dispatch("catalogue", {"kinds": [], "lookup": ["NoSuchMetricAtAll"]})

    assert answer["lookup"] == {"NoSuchMetricAtAll": None}


def test_an_api_without_lookup_answers_null_for_every_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """7.x has no ``Metric.lookup``, so nothing can be said about a plugin metric there.

    Null rather than an error: requirement 1.3 asks a 6.5 install to behave as it always
    did, and "this build cannot say" is the honest answer for it.
    """
    install(monkeypatch, catalogue_api(FakeMetrics({ROUTINE_KIND: ["CountLineCode"]})))

    answer = worker.dispatch("catalogue", {"kinds": [], "lookup": ["CountGlobalsModified"]})

    assert answer["lookup"] == {"CountGlobalsModified": None}


def test_a_catalogue_asked_for_no_lookup_carries_no_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The key is absent, not empty: an unasked question has no answer in the document."""
    install(monkeypatch, catalogue_api(FakeMetrics({ROUTINE_KIND: ["CountLineCode"]})))

    assert "lookup" not in worker.dispatch("catalogue", {"kinds": [ROUTINE_KIND]})


def test_a_lookup_that_is_not_a_list_of_strings_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every request key is validated the same way; a malformed one is a ``BadRequest``."""
    install(monkeypatch, catalogue_api(FakeMetrics()))

    error = envelope(worker.dispatch("catalogue", {"kinds": [], "lookup": "CountParams"}))

    assert error["type"] == "BadRequest"


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
    error = envelope(worker.dispatch("blueprint", {}))
    assert error["type"] == "UnknownOperation"
    assert "blueprint" in error["message"]
    for known in ("ping", "catalogue", "archs", "snapshot", "impact", "graphs"):
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


def test_the_script_entry_point_leaves_without_finalizing_the_interpreter() -> None:
    # Measured on the licensed machine: once Understand has drawn a dependency graph the
    # bundled interpreter aborts at shutdown ("PyInterpreterState_Delete: remaining
    # subinterpreters", SIGABRT) even though the answer is already complete on stdout — and a
    # non-zero exit is how the runner is told the worker is broken. The entry point therefore
    # flushes and leaves without finalizing, which an `atexit` hook that still ran would
    # disprove. No test that imports the module can see this: it is the script path.
    code = (
        "import atexit, runpy, sys;"
        "atexit.register(lambda: sys.stdout.write('finalized'));"
        f"sys.argv = [{str(WORKER_PATH)!r}, 'ping'];"
        f"runpy.run_path({str(WORKER_PATH)!r}, run_name='__main__')"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        input="",
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_S,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "finalized" not in proc.stdout, "the interpreter finalized after the answer"
    assert json.loads(proc.stdout), "the answer is written and flushed before leaving"


def test_main_falls_back_to_the_process_arguments(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    install(monkeypatch, FakeUnderstand())
    monkeypatch.setattr(sys, "argv", ["worker.py", "ping"])
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert worker.main() == 0
    assert json.loads(capsys.readouterr().out)["version"] == "6.5.1204"
