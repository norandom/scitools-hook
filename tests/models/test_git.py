"""Git records: staged changes, sync targets and the shadow sync delta (4.1, 4.3, 10.5)."""

from __future__ import annotations

import json

import pytest
from pydantic import TypeAdapter, ValidationError

from scitools_hook.models.git import (
    CommitTarget,
    IndexTarget,
    StagedChange,
    SyncDelta,
    SyncTarget,
    WorktreeTarget,
)

TARGETS: TypeAdapter[SyncTarget] = TypeAdapter(SyncTarget)


# --- staged changes ------------------------------------------------------------


def test_staged_change_defaults_old_path_to_none() -> None:
    change = StagedChange(status="M", path="src/cli/app.py")
    assert change.old_path is None


def test_staged_change_keeps_the_rename_source() -> None:
    change = StagedChange(status="R", path="src/cli/app.py", old_path="src/app.py")
    assert (change.status, change.old_path) == ("R", "src/app.py")


def test_staged_change_rejects_an_unknown_status() -> None:
    with pytest.raises(ValidationError):
        StagedChange(status="U", path="a.py")  # type: ignore[arg-type]


# --- sync targets --------------------------------------------------------------


def test_each_sync_target_names_its_kind() -> None:
    assert IndexTarget().kind == "index"
    assert WorktreeTarget().kind == "worktree"
    assert CommitTarget(commit="0f1e2d3").kind == "commit"


def test_commit_target_requires_a_commit() -> None:
    with pytest.raises(ValidationError):
        CommitTarget()  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "target", [IndexTarget(), WorktreeTarget(), CommitTarget(commit="0f1e2d3")]
)
def test_sync_targets_round_trip_through_the_discriminated_union(target: SyncTarget) -> None:
    wire = json.loads(TARGETS.dump_json(target))
    assert TARGETS.validate_python(wire) == target


def test_unknown_sync_target_kind_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TARGETS.validate_python({"kind": "stash"})


# --- sync delta ----------------------------------------------------------------


def test_sync_delta_defaults_to_an_incremental_empty_delta() -> None:
    delta = SyncDelta()
    assert (delta.added, delta.modified, delta.deleted) == ([], [], [])
    assert delta.full is False


def test_sync_delta_round_trips_through_json() -> None:
    delta = SyncDelta(
        added=["src/cli/check.py"],
        modified=["src/cli/app.py"],
        deleted=["src/legacy.py"],
        full=True,
    )
    assert SyncDelta.model_validate(json.loads(delta.model_dump_json())) == delta
