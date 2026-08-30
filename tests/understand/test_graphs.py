"""The graph exporter: Understand's own pictures as SVG files (task 6.6, requirement 9.4).

This is the one operation that writes anything, and the one operation that must never run in
this process: measured on the licensed machine, an in-process ``Ent.draw`` dies with
``symbol lookup error: …/Perl/auto/Fcntl/Fcntl.so: undefined symbol: Perl_xs_handshake`` and
takes the whole interpreter with it (status 127). ``ApiRunner`` routes ``graphs`` through
``upython`` whatever the mode is, and the ``contract``-marked test below exports from an
**in-process** runner: without that routing it would not fail, it would kill pytest.

A graph that Understand will not render for a given entity is a warning, never a failure —
verified live, a routine draws ``Butterfly`` and refuses ``Depends On`` — because one
unavailable picture must not cost the reviewer every other one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pytest
from conftest import SampleDatabases
from fakes.api import FakeApiRunner
from test_api_runner import real_env

from scitools_hook.config.metric_names import SCOPE_KINDS
from scitools_hook.errors import AnalysisFailedError
from scitools_hook.models.change import GraphTarget
from scitools_hook.models.progress import NullCommandLog
from scitools_hook.models.snapshot import EntityKey
from scitools_hook.understand.api_runner import ApiRunner
from scitools_hook.understand.graphs import GraphExporter

DB: Final = Path("/cache/after.und")
ROOT: Final = Path("/cache/after")

APP: Final = EntityKey(scope="file", path="cli/app.py", longname="cli/app.py", parameters=None)
MAIN: Final = EntityKey(scope="routine", path="cli/app.py", longname="app.main", parameters="argv")


def an_exporter(answer: dict[str, object]) -> GraphExporter:
    """An exporter whose runner answers ``graphs`` with ``answer``."""
    return GraphExporter(FakeApiRunner(answers={"graphs": answer}))


def runner_of(exporter: GraphExporter) -> FakeApiRunner:
    """The fake runner behind an exporter built by :func:`an_exporter`."""
    runner = exporter.runner
    assert isinstance(runner, FakeApiRunner)
    return runner


def an_answer(path: str = "/out/cli_app_py-9307a71d6bc1-Depends_On.svg") -> dict[str, object]:
    """One exported file in the wire shape the real worker answered with."""
    return {
        "graphs": [
            {"key": APP.model_dump(mode="json"), "graph": "Depends On", "path": path},
        ],
        "warnings": [],
    }


# --- the request -----------------------------------------------------------------


def test_the_request_names_the_database_the_root_and_the_kind_strings(tmp_path: Path) -> None:
    exporter = an_exporter(an_answer())

    exporter.export(DB, ROOT, [GraphTarget(key=APP, graph="Depends On")], tmp_path)

    request = runner_of(exporter).request_for("graphs")
    assert request["db"] == str(DB)
    assert request["root"] == str(ROOT)
    assert request["kinds_by_scope"] == SCOPE_KINDS


def test_each_target_travels_as_a_key_and_a_graph_name(tmp_path: Path) -> None:
    exporter = an_exporter(an_answer())
    targets = [GraphTarget(key=APP, graph="Depends On"), GraphTarget(key=MAIN, graph="Butterfly")]

    exporter.export(DB, ROOT, targets, tmp_path)

    assert runner_of(exporter).request_for("graphs")["targets"] == [
        {"key": APP.model_dump(mode="json"), "graph": "Depends On"},
        {"key": MAIN.model_dump(mode="json"), "graph": "Butterfly"},
    ]


def test_the_output_directory_is_made_absolute_before_it_is_sent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The worker creates the directory and writes the file names into its answer, so a
    # relative path would be resolved against *its* working directory and the answer would
    # name files the caller cannot find.
    monkeypatch.chdir(tmp_path)
    exporter = an_exporter(an_answer())

    exporter.export(DB, ROOT, [GraphTarget(key=APP, graph="Butterfly")], Path("graphs"))

    sent = runner_of(exporter).request_for("graphs")["out_dir"]
    assert Path(str(sent)).is_absolute()
    assert Path(str(sent)) == (tmp_path / "graphs").resolve()


def test_exporting_nothing_opens_no_database(tmp_path: Path) -> None:
    exporter = an_exporter(an_answer())

    assert exporter.export(DB, ROOT, [], tmp_path) == []
    assert runner_of(exporter).ops == []


# --- the answer ------------------------------------------------------------------


def test_the_written_files_come_back_as_graph_files(tmp_path: Path) -> None:
    exporter = an_exporter(an_answer(path="/out/app-abc.svg"))

    found = exporter.export(DB, ROOT, [GraphTarget(key=APP, graph="Depends On")], tmp_path)

    assert [(file.key, file.graph, file.path) for file in found] == [
        (APP, "Depends On", Path("/out/app-abc.svg"))
    ]


def test_a_graph_understand_will_not_draw_is_a_warning(tmp_path: Path) -> None:
    warning = "the routine 'app.main' of cli/app.py could not be drawn: Unknown Graph"
    exporter = an_exporter({"graphs": [], "warnings": [warning]})

    found = exporter.export(DB, ROOT, [GraphTarget(key=MAIN, graph="Depends On")], tmp_path)

    assert found == []
    assert exporter.warnings == [warning]


def test_an_answer_without_a_graph_list_is_a_broken_contract(tmp_path: Path) -> None:
    exporter = an_exporter({"warnings": []})

    with pytest.raises(AnalysisFailedError):
        exporter.export(DB, ROOT, [GraphTarget(key=APP, graph="Butterfly")], tmp_path)


def test_a_graph_entry_that_does_not_validate_is_a_broken_contract(tmp_path: Path) -> None:
    exporter = an_exporter({"graphs": [{"graph": "Butterfly"}], "warnings": []})

    with pytest.raises(AnalysisFailedError):
        exporter.export(DB, ROOT, [GraphTarget(key=APP, graph="Butterfly")], tmp_path)


# --- against the real Understand -------------------------------------------------


@pytest.mark.contract
def test_real_graphs_are_exported_even_when_the_mode_is_in_process(
    sample_databases: SampleDatabases, tmp_path: Path
) -> None:
    """The routing decision, proven where it matters: this runner is in-process.

    An in-process ``Ent.draw`` aborts the interpreter, so if ``ApiRunner`` stopped routing
    ``graphs`` through ``upython`` this test would not fail — the test session would die.
    """
    exporter = GraphExporter(ApiRunner(real_env("inprocess"), NullCommandLog()))
    root = sample_databases.root("after")
    app = EntityKey(scope="file", path="cli/app.py", longname="cli/app.py", parameters=None)
    targets = [GraphTarget(key=app, graph="Depends On"), GraphTarget(key=app, graph="Butterfly")]

    found = exporter.export(sample_databases.after_db, root, targets, tmp_path / "graphs")

    assert {file.graph for file in found} == {"Depends On", "Butterfly"}
    for file in found:
        assert file.path.parent == (tmp_path / "graphs").resolve()
        assert file.path.read_text(encoding="utf-8").lstrip().startswith("<?xml")
    assert exporter.warnings == []


@pytest.mark.contract
def test_a_routine_has_no_depends_on_graph_and_says_so(
    sample_databases: SampleDatabases, tmp_path: Path
) -> None:
    exporter = GraphExporter(ApiRunner(real_env("upython"), NullCommandLog()))
    root = sample_databases.root("after")
    main = EntityKey(scope="routine", path="cli/app.py", longname="app.main", parameters="argv")

    found = exporter.export(
        sample_databases.after_db, root, [GraphTarget(key=main, graph="Depends On")], tmp_path
    )

    assert found == []
    assert any("app.main" in warning for warning in exporter.warnings)
