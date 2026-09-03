"""Cache layout: the repository id, cache paths and the persisted sync state (2.1, 2.2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from scitools_hook.models.cache import APP_NAME, CachePaths, SyncState, cache_root, repo_id
from scitools_hook.models.snapshot import ParseError

# --- repo_id -------------------------------------------------------------------


def test_repo_id_is_sixteen_hex_characters(tmp_path: Path) -> None:
    identifier = repo_id(tmp_path / ".git")
    assert len(identifier) == 16
    assert all(char in "0123456789abcdef" for char in identifier)


def test_repo_id_is_stable_for_the_same_common_dir(tmp_path: Path) -> None:
    common = tmp_path / ".git"
    common.mkdir()
    assert repo_id(common) == repo_id(common)


def test_repo_id_ignores_the_route_taken_to_the_same_directory(tmp_path: Path) -> None:
    common = tmp_path / "repo" / ".git"
    common.mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "repo")
    assert repo_id(link / ".git") == repo_id(common)
    assert repo_id(tmp_path / "repo" / "." / ".git") == repo_id(common)


def test_repo_id_differs_between_repositories(tmp_path: Path) -> None:
    first = tmp_path / "one" / ".git"
    second = tmp_path / "two" / ".git"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    assert repo_id(first) != repo_id(second)


# --- CachePaths ----------------------------------------------------------------


def test_cache_paths_live_under_the_user_cache_directory(tmp_path: Path) -> None:
    """Under the application's own directory, not loose in the cache base.

    This assertion used to omit the ``APP_NAME`` segment and so encoded the defect: with
    ``HOME`` set, a base is always supplied, and the tool was writing bare hash directories
    straight into ``~/.cache``.
    """
    common = tmp_path / "repo" / ".git"
    common.mkdir(parents=True)
    paths = CachePaths.for_repo(common, db_location="cache", cache_dir=tmp_path / "cache")
    assert paths.root == tmp_path / "cache" / APP_NAME / repo_id(common)


def test_cache_paths_live_under_the_git_dir_when_configured(tmp_path: Path) -> None:
    common = tmp_path / "repo" / ".git"
    common.mkdir(parents=True)
    paths = CachePaths.for_repo(common, db_location="gitdir")
    assert paths.root == common.resolve() / APP_NAME


def test_cache_paths_default_to_the_platform_user_cache(tmp_path: Path) -> None:
    common = tmp_path / "repo" / ".git"
    common.mkdir(parents=True)
    paths = CachePaths.for_repo(common)
    assert paths.root.name == repo_id(common)
    assert paths.root.parent.name == APP_NAME


def test_every_cache_path_sits_inside_the_cache_root(tmp_path: Path) -> None:
    common = tmp_path / "repo" / ".git"
    common.mkdir(parents=True)
    paths = CachePaths.for_repo(common, cache_dir=tmp_path / "cache")
    members = [
        paths.before_tree,
        paths.after_tree,
        paths.before_db,
        paths.after_db,
        paths.state,
        paths.graphs,
    ]
    assert len(set(members)) == len(members)
    for member in members:
        assert member.parent == paths.root


def test_cache_paths_never_reach_into_the_working_tree(tmp_path: Path) -> None:
    worktree = tmp_path / "repo"
    common = worktree / ".git"
    common.mkdir(parents=True)
    in_cache = CachePaths.for_repo(common, db_location="cache", cache_dir=tmp_path / "cache")
    in_gitdir = CachePaths.for_repo(common, db_location="gitdir")
    assert not in_cache.root.is_relative_to(worktree)
    assert in_gitdir.root.is_relative_to(common.resolve())


def test_cache_paths_round_trip_through_json(tmp_path: Path) -> None:
    paths = CachePaths.for_repo(tmp_path / ".git", cache_dir=tmp_path / "cache")
    assert CachePaths.model_validate(json.loads(paths.model_dump_json())) == paths


# --- SyncState -----------------------------------------------------------------


def test_sync_state_starts_empty() -> None:
    state = SyncState()
    assert state.after_target is None
    assert state.after_tree_id is None
    assert state.before_commit is None
    assert state.languages == []
    assert state.created_with == ""


def test_sync_state_records_the_after_target_kind() -> None:
    for kind in ("index", "worktree", "commit"):
        assert SyncState(after_target=kind).after_target == kind


def test_sync_state_rejects_an_unknown_after_target() -> None:
    with pytest.raises(ValidationError):
        SyncState(after_target="stash")  # type: ignore[arg-type]


def test_sync_state_round_trips_through_json() -> None:
    state = SyncState(
        after_target="index",
        after_tree_id="4b825dc642cb6eb9a060e54bf8d69288fbee4904",
        before_commit="0f1e2d3",
        languages=["Python", "C++"],
        created_with="6.5.1204",
        parse_errors={
            "after": [ParseError(path=Path("pkg/generic.py"), line=1, message="expected token")],
            "before": [],
        },
    )
    assert SyncState.model_validate(json.loads(state.model_dump_json())) == state


def test_sync_state_carries_no_parse_errors_until_a_run_records_some() -> None:
    """The default is empty, so a cache written before task 11.13 reads as "nothing recorded"."""
    assert SyncState().parse_errors == {}


def test_sync_state_rejects_a_side_that_is_not_a_side() -> None:
    """The record is keyed by side; a third key would be a database nothing ever analyses."""
    with pytest.raises(ValidationError):
        SyncState(parse_errors={"sideways": []})  # type: ignore[dict-item]


def test_a_supplied_cache_base_is_still_namespaced_under_the_application(tmp_path: Path) -> None:
    """The tool must not scatter unlabelled hash directories into a user's cache.

    This is the defect the whole suite missed, and the reason it missed it is worth keeping:
    ``user_cache_dir(APP_NAME)`` carries the application name itself, so the fallback arm was
    correct, while the supplied-base arm was not. On Linux ``runner.context.cache_dir(env)``
    returns ``~/.cache`` whenever ``HOME`` is set, so a base is *always* supplied and the
    fallback is effectively dead -- the real result was ``~/.cache/<repo_id>``, measured as
    ``/home/mc/.cache/1c23f1c40aae2d9b``, against this module's documented
    ``<user cache dir>/scitools-hook/<repo_id>/``.

    **Every existing test passed an explicit ``tmp_path`` base**, which is precisely why none
    of them could see it: the defect lived only in the arm the tests were replacing. A fixture
    that always overrides one branch of a decision cannot test that decision.
    """
    root = cache_root(tmp_path / "repo" / ".git", "cache", tmp_path / "xdg")
    assert root.parent == tmp_path / "xdg" / APP_NAME
    assert root.name == repo_id(tmp_path / "repo" / ".git")


def test_the_gitdir_arm_is_not_double_namespaced(tmp_path: Path) -> None:
    """``gitdir`` already ends in the application name; it must not gain a second one."""
    common = tmp_path / "repo" / ".git"
    common.mkdir(parents=True)
    root = cache_root(common, "gitdir", tmp_path / "xdg")
    assert root == common.resolve() / APP_NAME
    assert str(root).count(APP_NAME) == 1
