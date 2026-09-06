"""Whether anything in the project references a recorded routine (requirement 6.2).

The unused-routine rule rests entirely on this flag, and the flag has three states rather than
two. ``True`` and ``False`` are measurements; ``None`` is "the worker was not asked", which the
rule reports as unavailable rather than as a project full of dead code -- the difference
between a measurement and its absence, which requirement 6.4 asks to stay visible.

Two decisions are worth naming because getting either wrong makes the rule useless in a way
nothing else would catch:

* **A reference from outside the analysis root counts for nothing.** Understand injects the
  interpreter's own standard library into a Python project, and measured on Build 1262 over
  this repository, 332 routines are referenced *only* from those files. A rule that took a
  stub's reference as use would stay silent about exactly the dead code it exists to find.
* **A use is not only a call.** A routine passed as a value, registered as a handler or named
  in a decorator is used and never called. ``callby, useby`` answers both, and measured on the
  same database those two selectors answer with ``Call`` (11 977) and ``Use`` (544) and nothing
  else.
"""

from __future__ import annotations

from typing import Any

import pytest
from api_fakes import FakeUnderstand, install
from worker_projects import fake_project, records, snapshot_request

from scitools_hook.understand import worker

ASKING: dict[str, object] = {"record_referenced": True}
"""The request key; off unless the rule is on, because it costs a query per recorded routine."""


def a_run(monkeypatch: pytest.MonkeyPatch, **overrides: object):
    """Run the extraction and answer the document beside the project it read."""
    project = fake_project()
    install(monkeypatch, FakeUnderstand(db=project.db))
    document: dict[str, Any] = worker.dispatch("snapshot", snapshot_request(**overrides))
    assert "error" not in document, document
    return document, project


def referenced(document: dict[str, Any], longname: str) -> bool | None:
    """The flag on one recorded entity."""
    found = records(document)[longname]
    answer = found["referenced"]
    assert answer is None or isinstance(answer, bool)
    return answer


def reached_from(ent: Any, container: Any, kind: str = "python Callby") -> None:
    """Make ``container`` reference ``ent``, the way ``Ent.refs("callby")`` reports one."""
    ent.refs_by = [container]
    ent.refs_by_kind = kind


# --- the two measurements -------------------------------------------------------------


def test_a_routine_something_in_the_project_calls_is_referenced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = fake_project()
    reached_from(project.build_parser, project.text)
    install(monkeypatch, FakeUnderstand(db=project.db))
    document: dict[str, Any] = worker.dispatch("snapshot", snapshot_request(**ASKING))

    assert referenced(document, "app.build_parser") is True


def test_a_routine_nothing_reaches_is_not_referenced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The finding this whole rule exists for: dead code an agent forgot to delete."""
    document, _ = a_run(monkeypatch, **ASKING)

    assert referenced(document, "app.build_parser") is False


def test_a_reference_from_outside_the_analysis_root_counts_for_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Understand injects the standard library; a stub's reference is not this project's use."""
    project = fake_project()
    reached_from(project.build_parser, project.outside)
    install(monkeypatch, FakeUnderstand(db=project.db))
    document: dict[str, Any] = worker.dispatch("snapshot", snapshot_request(**ASKING))

    assert referenced(document, "app.build_parser") is False


def test_a_use_counts_as_well_as_a_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """A routine registered as a handler or named in a decorator is used and never called."""
    project = fake_project()
    reached_from(project.build_parser, project.text, "python Useby")
    install(monkeypatch, FakeUnderstand(db=project.db))
    document: dict[str, Any] = worker.dispatch("snapshot", snapshot_request(**ASKING))

    assert referenced(document, "app.build_parser") is True


# --- the third state --------------------------------------------------------------------


def test_a_run_that_did_not_ask_records_nothing_rather_than_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement 6.4: "not measured" must not read as "nothing references it"."""
    document, _ = a_run(monkeypatch)

    assert referenced(document, "app.build_parser") is None


def test_only_routines_carry_the_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """A file or a class is not a thing this rule has an opinion about."""
    document, _ = a_run(monkeypatch, **ASKING)

    assert referenced(document, "cli/app.py") is None
    assert referenced(document, "app.Runner") is None
