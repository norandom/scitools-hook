"""The snapshot cache changes no finding, end to end (requirements 8.6, 8.7).

The cache exists because the four snapshot extractions were 88% of a warm one-line check.
Everything it saves is worthless if it changes one finding, so the property asserted here is
equality: the same change, checked twice, reports byte-identical findings, and the second run
says out loud that it used the cache.

The seam answers from fixture documents, so what is exercised is the whole path above it --
the key, the store, the hand-over into the engine, the narrowing that follows and the JSON the
operator reads.
"""

from __future__ import annotations

import json
from pathlib import Path

from e2e.harness import DEEP, NESTED, Workspace, report
from scitools_hook.exit_codes import ExitCode


def checked(workspace: Workspace):
    """One ``check --worktree`` over an edit that breaks a rule."""
    return workspace.cli("check", "--worktree", "--format", "json")


def findings(document: object) -> str:
    """The findings of a run, as a stable string, so two runs can be compared exactly."""
    found = document["findings"]  # type: ignore[index]
    assert isinstance(found, list)
    return json.dumps(sorted(found, key=lambda one: (one["rule"], one["path"])), sort_keys=True)


def test_two_runs_of_one_change_report_identical_findings(workspace: Workspace) -> None:
    """Requirement 8.7. The cache may make a run faster; it may not make it different."""
    workspace.write(DEEP, NESTED)

    cold = checked(workspace)
    warm = checked(workspace)

    assert cold.returncode == warm.returncode == int(ExitCode.VIOLATIONS), cold.stderr
    assert findings(report(warm)) == findings(report(cold))
    assert findings(report(cold)) != "[]", "equality of nothing proves nothing"


def test_the_second_run_says_it_used_the_cache(workspace: Workspace) -> None:
    """Requirement 8.6: an optimisation nobody can see is one nobody can check."""
    workspace.write(DEEP, NESTED)

    checked(workspace)
    warm = workspace.cli("--verbose", "check", "--worktree", "--format", "json")

    assert "served from the cache" in warm.stderr, warm.stderr


def test_the_first_run_of_a_change_says_no_such_thing(workspace: Workspace) -> None:
    """The pair is the point: a line printed unconditionally would prove nothing."""
    workspace.write(DEEP, NESTED)

    cold = workspace.cli("--verbose", "check", "--worktree", "--format", "json")

    assert "served from the cache" not in cold.stderr


def test_the_stored_document_is_beside_the_databases(workspace: Workspace) -> None:
    """Requirement 2.8: nothing the Gate writes lands in the working tree."""
    workspace.write(DEEP, NESTED)
    checked(workspace)

    cache = Path(workspace.cli("db", "path").stdout.strip()).parent
    stored = sorted((cache / "snapshots").glob("*.json"))

    assert stored, sorted(cache.iterdir())
    assert all(path.name.startswith("before-") for path in stored), stored


def test_a_run_with_nothing_selected_stores_nothing(workspace: Workspace) -> None:
    """Requirement 8.5: a check with no files runs no analysis and no extraction."""
    done = workspace.cli("check", "--staged", "--format", "json")

    assert done.returncode == int(ExitCode.OK), done.stderr
    assert report(done)["analyzed_files"] == 0
    cache = Path(workspace.cli("db", "path").stdout.strip()).parent
    assert not (cache / "snapshots").exists()
