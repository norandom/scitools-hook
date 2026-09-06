"""Plugin metrics, asked for where a record is produced and nowhere else (requirement 5.1).

Understand 8.0 adds metrics that a ``.upy`` plugin computes on demand rather than fields the
database already holds: ``CountGlobalsModified/Set/Used`` for routines,
``CountClassCoupledModified`` for classes, and the project-level ``CBRI`` family. They are
invisible to ``Metric.list(kind)`` and are found by ``Metric.lookup(id)``, which is why they
need a declaration at all.

**What is asserted here is a cost, not a value.** The extraction walks every entity of a scope
once, because the whole-project population vectors need every value; a record is produced only
for the files the run selected. Asking a plugin for a metric of every routine in a project
pays for a plugin run per routine and throws almost all of it away, so the ``plugin_metrics``
key is read in a call of its own, for recorded entities only. A test can see that only by
watching which names each entity was asked for, which is what ``FakeEnt.asked`` records.
"""

from __future__ import annotations

from typing import Any

import pytest
from api_fakes import FakeEnt, FakeUnderstand, install
from worker_projects import fake_project, mapping, records, snapshot_request

from scitools_hook.understand import worker

PLUGIN = "CountGlobalsUsed"
"""One of the routine-scope plugin metrics; the declaration in ``config`` names five more."""

CLASS_PLUGIN = "CountClassCoupledModified"
"""The class-scope one, so the split is asserted on more than one scope."""


def a_run(monkeypatch: pytest.MonkeyPatch, **overrides: object):
    """Run the extraction and answer the document beside the project it read."""
    project = fake_project()
    install(monkeypatch, FakeUnderstand(db=project.db))
    document: dict[str, Any] = worker.dispatch("snapshot", snapshot_request(**overrides))
    assert "error" not in document, document
    return document, project


def asked_for(ent: FakeEnt, metric: str) -> int:
    """How many of this entity's metric calls named ``metric``."""
    return sum(1 for names in ent.asked if metric in names)


def with_plugins(**overrides: object) -> dict[str, object]:
    """A request that configures one routine-scope and one class-scope plugin metric."""
    request: dict[str, object] = {
        "metrics_by_scope": {
            "routine": ["CyclomaticStrict", PLUGIN],
            "class": ["CountDeclMethod", CLASS_PLUGIN],
            "file": ["CountLineCode"],
        },
        "plugin_metrics": {"routine": [PLUGIN], "class": [CLASS_PLUGIN]},
    }
    request.update(overrides)
    return request


# --- where a plugin is asked, and where it is not -----------------------------------------


def test_an_entity_outside_the_selection_is_never_asked_for_a_plugin_metric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cost this key exists to avoid: a plugin run per entity of the whole project."""
    _, project = a_run(monkeypatch, **with_plugins())

    assert asked_for(project.wrap_lines, PLUGIN) == 0, "wrap_lines is in util/text.py"
    assert asked_for(project.clamp, PLUGIN) == 0, "clamp is in native/util.c"
    assert asked_for(project.helper, CLASS_PLUGIN) == 0, "Helper is in util/text.py"


def test_an_entity_outside_the_selection_is_still_asked_for_the_built_in_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The walk is unchanged: the population vectors need every entity's built-in values."""
    _, project = a_run(monkeypatch, **with_plugins())

    assert asked_for(project.wrap_lines, "CyclomaticStrict") == 1


def test_a_recorded_entity_is_asked_for_the_plugin_in_a_call_of_its_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One extra call, once, for the entities a record is produced for."""
    _, project = a_run(monkeypatch, **with_plugins())

    calls = project.build_parser.asked
    assert asked_for(project.build_parser, PLUGIN) == 1
    assert not any(PLUGIN in names and "CyclomaticStrict" in names for names in calls), (
        "the plugin is a call of its own, so the walk's call is the one a 6.5 install makes"
    )


def test_the_value_reaches_the_record(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement 5.1 as a rule reads it: the metric is on the entity like any other."""
    project = fake_project()
    project.build_parser.values[PLUGIN] = 4
    project.runner.values[CLASS_PLUGIN] = 2
    install(monkeypatch, FakeUnderstand(db=project.db))
    document: dict[str, Any] = worker.dispatch("snapshot", snapshot_request(**with_plugins()))

    found = records(document)

    assert found["app.build_parser"]["metrics"][PLUGIN] == 4
    assert found["app.Runner"]["metrics"][CLASS_PLUGIN] == 2


# --- populations, and what is reported unavailable ----------------------------------------


def test_no_population_vector_carries_a_plugin_metric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A population is built from every entity of the scope, which is what a plugin costs."""
    document, _ = a_run(
        monkeypatch,
        **with_plugins(population_metrics={"routine": ["CyclomaticStrict"], "project": []}),
    )

    populations = mapping(document, "populations")
    for scope, vectors in populations.items():
        assert PLUGIN not in vectors, scope
        assert CLASS_PLUGIN not in vectors, scope


def test_a_plugin_metric_the_build_has_no_value_for_is_reported_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement 5.5: reported, not defaulted, because a zero is a claim nothing made."""
    document, _ = a_run(monkeypatch, **with_plugins())

    unavailable = mapping(document, "unavailable")

    assert PLUGIN in unavailable.get("Python", [])


def test_it_is_reported_for_the_recorded_entities_language_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Counting it during the walk would report it once per entity of the whole project.

    ``native/util.c`` holds a routine that is never recorded, and the class-scope plugin is
    configured for a project whose only recorded class is Python's -- so a C entry here would
    be a plugin metric reported for a language nothing ever asked it about.
    """
    document, _ = a_run(monkeypatch, **with_plugins())

    unavailable = mapping(document, "unavailable")

    assert PLUGIN not in unavailable.get("C", [])
    assert CLASS_PLUGIN not in unavailable.get("C", [])


# --- the shipped case ----------------------------------------------------------------------


def test_a_request_configuring_no_plugin_metric_changes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No plugin metric is a shipped threshold, so an untouched repository meets none of this."""
    _, project = a_run(monkeypatch)

    assert [names for names in project.build_parser.asked if PLUGIN in names] == []
    assert project.build_parser.asked[0] == project.wrap_lines.asked[0], (
        "with no plugin key the walk asks a recorded entity exactly what it asks the rest"
    )
