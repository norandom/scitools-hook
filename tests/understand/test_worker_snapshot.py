"""The ``snapshot`` operation on the fake project: entities, metrics, populations, edges, nodes.

The request is self-describing -- files of interest, kind strings per scope, metrics and
synthetic ids, ignore regexes, population metrics, the architecture and its depth -- and the
answer is one ``ProjectSnapshot`` document. Each section here reads one part of it. The
analysis root, which decides how paths are named, has a module of its own
(``test_worker_snapshot_root``); the dependency graphs have another.
"""

from __future__ import annotations

import pytest
from api_fakes import (
    FakeArch,
    FakeDb,
    FakeUnderstand,
    envelope,
    install,
)
from worker_projects import (
    FILE_KIND,
    a_file,
    fake_project,
    listing,
    mapping,
    records,
    snapshot,
    snapshot_request,
)

from scitools_hook.models.snapshot import EntityKey, ProjectSnapshot
from scitools_hook.understand import worker

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
    """One architecture edge per pair of nodes whose files depend on each other, refs summed.

    ``util -> native`` is here because ``util/text.py`` depends on ``native/util.c``: the
    architecture edges are the file edges resolved to their nodes, so a file dependency
    across two nodes is an architecture dependency whether or not ``Arch.depends()`` would
    have named it.
    """
    assert listing(snapshot(monkeypatch), "arch_edges") == [
        {
            "src": "Directory Structure/cli",
            "dst": "Directory Structure/util",
            "refs": 3,
            "crosses_arch": True,
        },
        {
            "src": "Directory Structure/util",
            "dst": "Directory Structure/native",
            "refs": 2,
            "crosses_arch": True,
        },
    ]


def test_snapshot_drops_an_architecture_edge_that_trims_onto_its_own_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # At depth 0 every node trims to the architecture itself, so nothing crosses.
    assert snapshot(monkeypatch, depth=0)["arch_edges"] == []


def deep_architecture() -> FakeDb:
    """A two-level tree whose ``cli`` file depends on a file below the level in question."""
    app = a_file("cli/app.py")
    text = a_file("util/helpers/text.py")
    app.deps = {text: [object()] * 5}
    text.deps_by = {app: [object()] * 5}
    root = FakeArch(
        "Directory Structure",
        children=[
            FakeArch("Directory Structure/cli", ents=[app]),
            FakeArch(
                "Directory Structure/util",
                children=[FakeArch("Directory Structure/util/helpers", ents=[text])],
            ),
        ],
    )
    return FakeDb([root], entities={FILE_KIND: [app, text]})


def test_snapshot_trims_an_architecture_edge_to_the_requested_depth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file may depend on one deeper than the level the structural rules work at.

    The dependency belongs to the depth the caller asked for, so its target is the node that
    publishes the file at that depth; dropping it instead would hide the dependency from
    every rule at that level.
    """
    install(monkeypatch, FakeUnderstand(db=deep_architecture()))
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
