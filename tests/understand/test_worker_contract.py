"""The real worker under the real interpreter, against the sample databases (task 10.1).

Everything here is ``contract``-marked and skipped without a licensed Understand. The unit
modules drive ``dispatch`` against a fake API; these drive ``upython worker.py <op>`` against
databases built from the fixture projects, so what the fakes assume about the API is measured
rather than believed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Final

import pytest
from api_fakes import (
    envelope,
)
from conftest import SampleDatabases, Side, understand_probe
from worker_projects import (
    ROUTINE_KIND,
    SUBPROCESS_TIMEOUT_S,
    file_key,
    routine_key,
    run_worker,
    upython_or_skip,
)

from scitools_hook.config.metric_names import SCOPE_KINDS
from scitools_hook.models.snapshot import ProjectSnapshot

# --- contract: the real worker under the real interpreter -------------------------


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
    assert len(snapshot.entities) == 43, "the count moves with the fixture, the stubs do not"


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
    assert len(routines) == 25, "every routine of the fixture, the C++ methods included"
    assert max(routines) == 7
    assert snapshot.populations["project"]["MaxCyclomaticStrict"] == [7]
    assert snapshot.populations["project"]["CyclomaticStrict"] == routines


@pytest.mark.contract
def test_the_plugin_metrics_understand_8_adds_declare_their_targets_and_languages() -> None:
    """Requirement 5.1 against the installed build: the tags the declaration was written from.

    These are invisible to ``Metric.list`` -- the test below says so -- and are the reason the
    catalogue needs a second source at all. ``CognitiveComplexity`` is the control: the build
    carries it and it is C/C++ only, so a Python repository must be told it is unavailable
    rather than have it silently skipped.
    """
    wanted = [
        "CountGlobalsModified",
        "CountClassCoupledModified",
        "CorePercentage",
        "CognitiveComplexity",
        "NoSuchMetricAtAll",
    ]

    answer = run_worker(upython_or_skip(), "catalogue", {"kinds": [], "lookup": wanted})

    lookup = answer["lookup"]
    assert isinstance(lookup, dict)
    assert lookup["NoSuchMetricAtAll"] is None
    assert lookup["CountGlobalsModified"]["targets"] == ["Functions"]
    assert "Python" in lookup["CountGlobalsModified"]["languages"]
    assert lookup["CountClassCoupledModified"]["targets"] == ["Classes"]
    assert set(lookup["CorePercentage"]["targets"]) == {"Architectures", "Project"}
    assert lookup["CorePercentage"]["languages"] == ["Any"]
    assert lookup["CognitiveComplexity"]["languages"] == ["C", "C++"]
    assert "Python" not in lookup["CognitiveComplexity"]["languages"]


@pytest.mark.contract
def test_a_plugin_metric_is_absent_from_the_kind_listing_that_would_be_asked_first() -> None:
    """Why the lookup exists: the ordinary catalogue answer does not carry these at all."""
    answer = run_worker(upython_or_skip(), "catalogue", {"kinds": [ROUTINE_KIND]})

    listed = answer["metrics"][ROUTINE_KIND]
    assert "CyclomaticStrict" in listed, "the kind string still answers the built-in metrics"
    assert "CountGlobalsModified" not in listed
