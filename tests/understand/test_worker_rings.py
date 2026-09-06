"""Recording two rings of neighbourhood in one walk (requirement 8.3).

The check pipeline reads each side twice today: once for the selected files, which is all the
affected-set resolver needs, and once for the affected files *and their neighbourhood*, which
is what the rules have to see one step past the change. Measured, the four extractions are 88%
of a warm one-line check, and the expensive half of each is the **walk** -- every entity of
every scope, with its metrics -- not the recording.

So one walk can record both rings and the second pass can go. This module is about that one
key, and about the two things that must not change with it:

* **the walk still happens once per call.** Widening what is *recorded* must not widen what is
  *read*, or the change would cost what it saves.
* **zero is exactly today's behaviour**, because whole-project mode still asks for it and every
  document written before the key existed has to keep validating.
"""

from __future__ import annotations

from typing import Any

import pytest
from api_fakes import FakeUnderstand, install
from worker_projects import fake_project, records, snapshot_request

from scitools_hook.understand import worker

SELECTED = "cli/app.py"
"""The one file the request names; ``util/text.py`` is one step away and ``native/util.c`` two."""


def a_run(monkeypatch: pytest.MonkeyPatch, **overrides: object) -> dict[str, Any]:
    """One extraction over the fake project."""
    install(monkeypatch, FakeUnderstand(db=fake_project().db))
    document: dict[str, Any] = worker.dispatch("snapshot", snapshot_request(**overrides))
    assert "error" not in document, document
    return document


def recorded_paths(document: dict[str, Any]) -> set[str]:
    """The files the document holds entities for."""
    return {record["ref"]["key"]["path"] for record in records(document).values()}


def test_zero_rings_records_the_selection_and_nothing_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Today's behaviour, which whole-project mode still asks for."""
    assert recorded_paths(a_run(monkeypatch)) == {SELECTED}
    assert recorded_paths(a_run(monkeypatch, neighbourhood_rings=0)) == {SELECTED}


def test_one_ring_records_the_files_a_step_away(monkeypatch: pytest.MonkeyPatch) -> None:
    """``cli/app.py`` depends on ``util/text.py``; nothing else is one step from it."""
    found = recorded_paths(a_run(monkeypatch, neighbourhood_rings=1))

    assert found == {SELECTED, "util/text.py"}


def test_two_rings_records_the_files_two_steps_away(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``util/text.py`` depends on ``native/util.c``, which is the second ring."""
    found = recorded_paths(a_run(monkeypatch, neighbourhood_rings=2))

    assert found == {SELECTED, "util/text.py", "native/util.c"}


def test_the_second_ring_is_absent_at_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pair is the point: a widening that always widened would prove nothing."""
    assert "native/util.c" not in recorded_paths(a_run(monkeypatch))


def test_the_walk_still_runs_once_per_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    """Widening what is recorded must not widen what is read, or the change costs what it saves.

    ``FakeEnt.asked`` records every metric call, and the walk's own call is the one naming
    the scope's whole metric list. It happens once per entity whatever the ring count is: the
    walk is unconditional and the recording is the only thing this key moves.

    A recorded routine does pick up further calls -- the call graph reads its complexity -- and
    that is the second pass's cost arriving in the first, which is the point of the change.
    """
    project = fake_project()
    install(monkeypatch, FakeUnderstand(db=project.db))
    worker.dispatch("snapshot", snapshot_request(neighbourhood_rings=2))

    walked = [names for names in project.clamp.asked if "MaxNesting" in names]
    assert len(walked) == 1, project.clamp.asked


def test_a_ring_count_the_worker_will_not_take_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two is the widest ring any rule reads; more would cost document size for nothing."""
    install(monkeypatch, FakeUnderstand(db=fake_project().db))

    answer = worker.dispatch("snapshot", snapshot_request(neighbourhood_rings=3))

    assert answer["error"]["type"] == "BadRequest", answer
    assert "neighbourhood_rings" in answer["error"]["message"]
