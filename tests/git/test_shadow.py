"""Incremental shadow synchronisation: index, worktree and commit into the cache (task 7.2).

Every test drives a **real** ``git`` against a **real** throwaway repository, for the same
reason :mod:`tests.git.test_repo` does: the module exists to agree with git.

Four promises the task names, and how each is observed rather than inferred:

* *An unstaged edit is absent from the after shadow* (requirement 4.1). Observed with a
  **three-way fixture** -- the commit says ``COMMIT``, the index says ``INDEX``, the working
  tree says ``WORKTREE`` -- so each of the three wrong answers names itself instead of hiding
  behind a repository whose three states agree. The 7.1 review learned this the hard way.
* *A second run touches only changed paths.* Observed, not assumed: every shadow file's mtime
  is stamped to a fixed instant in the past before the second sync, so "was it rewritten" is a
  question the filesystem answers exactly, with no dependence on clock granularity.
* *A rename is handled.* Observed as one delete plus one add carrying the **new** staged bytes.
* *Nothing is written inside the repository working tree* (requirement 2.2). Observed by
  hashing the whole working tree before and after and comparing, rather than by checking only
  that the shadow filled up. ``.git`` is excluded and reported separately: it is not the
  working tree, and ``git write-tree`` deliberately adds objects to it.

The delta a sync returns is asserted alongside the bytes on disk everywhere, because 8.1
drives ``und add`` / ``und remove`` / ``analyze -files`` from it: a shadow that is right while
its delta lies would leave the database wrong and nothing would notice.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path

import pytest
from conftest import FakeCommandLog, GitRepoBuilder, MakeGitRepo

from scitools_hook.config.defaults import default_settings
from scitools_hook.config.models import ProjectSettings
from scitools_hook.errors import AnalysisFailedError
from scitools_hook.git.repo import GitRepo
from scitools_hook.git.shadow import PathFilter, ShadowSync
from scitools_hook.models.cache import CachePaths, SyncState
from scitools_hook.models.git import (
    CommitTarget,
    IndexTarget,
    SyncDelta,
    SyncTarget,
    WorktreeTarget,
)
from scitools_hook.models.snapshot import Side

LEAKED_GIT_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_CONFIG_COUNT",
    "GIT_CEILING_DIRECTORIES",
    "GIT_OBJECT_DIRECTORY",
    "GIT_COMMON_DIR",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)
"""Everything an outer ``git`` invocation exports that would steer these subprocesses."""

STAMP_NS = 1_000_000_000
"""A fixed instant in the past; every shadow file is stamped with it between two syncs."""


@pytest.fixture(autouse=True)
def isolated_git(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralise the developer's git configuration for the subprocesses the module starts.

    ``GitRepoBuilder`` isolates its own calls; the code under test starts its own, which
    inherit ``os.environ``. A global ``diff.renames = copies`` or ``core.autocrlf`` would
    otherwise change what these tests measure.
    """
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for leaked in LEAKED_GIT_VARS:
        monkeypatch.delenv(leaked, raising=False)


# --- harness --------------------------------------------------------------------


@dataclass(frozen=True)
class Harness:
    """One repository, its cache layout and a sync bound to both."""

    builder: GitRepoBuilder
    repo: GitRepo
    paths: CachePaths
    sync: ShadowSync
    state: SyncState

    @property
    def after(self) -> Path:
        """The after shadow, which every non-``before`` test reads."""
        return self.paths.after_tree

    def run(self, target: SyncTarget, side: Side = "after") -> SyncDelta:
        """Sync one side and return the delta, keeping the harness's own state up to date."""
        return self.sync.sync(side, target, self.state)


def make_harness(
    builder: GitRepoBuilder, cache: Path, project: ProjectSettings | None = None
) -> Harness:
    """Bind a ``ShadowSync`` to ``builder``'s repository with its cache under ``cache``."""
    repo = GitRepo.discover(builder.path, FakeCommandLog())
    paths = CachePaths.for_repo(repo.common_dir, "cache", cache)
    settings = ProjectSettings() if project is None else project
    return Harness(builder, repo, paths, ShadowSync(repo, paths, settings), SyncState())


@pytest.fixture
def harness(git_repo: MakeGitRepo, tmp_path: Path) -> Harness:
    """A repository with one commit, and a sync pointed at a cache outside it."""
    builder = git_repo()
    builder.write("src/a.py", "def a():\n    return 1\n")
    builder.write("readme.md", "hello\n")
    builder.stage()
    builder.commit("first")
    return make_harness(builder, tmp_path / "cache")


def three_way(builder: GitRepoBuilder, rel: str = "src/a.py") -> None:
    """Make the commit, the index and the working tree disagree on ``rel``.

    Without this every wrong answer looks like the right one -- the defect 7.1 recorded as
    "``export_commit``'s three failure modes are indistinguishable without a three-way
    fixture".
    """
    builder.write(rel, "COMMIT\n")
    builder.stage()
    builder.commit("three-way base")
    builder.write(rel, "INDEX\n")
    builder.stage(rel)
    builder.write(rel, "WORKTREE\n")


# --- observing the shadow -------------------------------------------------------


def shadow_files(root: Path) -> dict[str, str]:
    """Every regular file under ``root``, as ``relative posix path -> text``."""
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            out[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8")
    return out


def entries(root: Path) -> set[str]:
    """Every path under ``root``, directories included, so a phantom directory is visible."""
    return {path.relative_to(root).as_posix() for path in root.rglob("*")}


def stamp(root: Path) -> None:
    """Set every shadow file's mtime to a fixed past instant, so a rewrite is unambiguous."""
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            os.utime(path, ns=(STAMP_NS, STAMP_NS))


def touched(root: Path) -> set[str]:
    """The files whose mtime moved away from :data:`STAMP_NS` since :func:`stamp`."""
    moved = set()
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink() and path.stat().st_mtime_ns != STAMP_NS:
            moved.add(path.relative_to(root).as_posix())
    return moved


def tree_digest(root: Path) -> dict[str, str]:
    """Name, kind and content hash of everything under ``root``, for a before/after compare."""
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            out[rel] = f"link:{os.readlink(path)}"
        elif stat.S_ISDIR(info.st_mode):
            out[rel] = "dir"
        else:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            out[rel] = f"file:{oct(stat.S_IMODE(info.st_mode))}:{digest}"
    return out


def split_git(digest: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """Separate the working tree from ``.git``, which is not part of it."""
    inside = {k: v for k, v in digest.items() if k == ".git" or k.startswith(".git/")}
    outside = {k: v for k, v in digest.items() if k not in inside}
    return outside, inside


# --- the index target: what a commit would contain (requirement 4.1) ------------


def test_index_shadow_holds_the_staged_bytes_not_the_working_tree(harness: Harness) -> None:
    """The single most important test in the file: the gate judges the index, not the disk."""
    three_way(harness.builder)

    harness.run(IndexTarget())

    assert shadow_files(harness.after)["src/a.py"] == "INDEX\n"


def test_index_shadow_ignores_an_unstaged_edit_to_an_otherwise_untouched_file(
    harness: Harness,
) -> None:
    """An edit that was never staged must not reach the shadow even on the first full sync."""
    harness.builder.unstaged_edit("readme.md", "EDITED BUT NOT STAGED\n")

    harness.run(IndexTarget())

    assert shadow_files(harness.after)["readme.md"] == "hello\n"


def test_a_staged_deletion_leaves_the_file_out_of_the_shadow(harness: Harness) -> None:
    """Requirement 4.10's change shape: the deleted file must not be in the after shadow."""
    harness.builder.delete("readme.md")

    delta = harness.run(IndexTarget())

    assert "readme.md" not in shadow_files(harness.after)
    assert delta.added == ["src/a.py"]


def test_a_staged_rename_carries_the_new_staged_bytes(harness: Harness) -> None:
    """A rename with an unstaged edit on top: the shadow gets the staged content, not the disk."""
    harness.builder.rename("src/a.py", "src/b.py")
    harness.builder.write("src/b.py", "RENAMED AND STAGED\n")
    harness.builder.stage("src/b.py")
    harness.builder.unstaged_edit("src/b.py", "RENAMED AND EDITED ON DISK\n")

    harness.run(IndexTarget())

    files = shadow_files(harness.after)
    assert "src/a.py" not in files
    assert files["src/b.py"] == "RENAMED AND STAGED\n"


def test_nothing_is_written_inside_the_repository_working_tree(harness: Harness) -> None:
    """Requirement 2.2, asserted against the whole tree rather than against the shadow."""
    three_way(harness.builder)
    head = harness.builder.run("rev-parse", "HEAD")
    before_all = tree_digest(harness.builder.path)

    harness.run(IndexTarget())
    harness.run(CommitTarget(commit=head), side="before")
    harness.run(WorktreeTarget())
    harness.run(IndexTarget())

    after_all = tree_digest(harness.builder.path)
    before_tree, before_git = split_git(before_all)
    after_tree, after_git = split_git(after_all)
    assert after_tree == before_tree
    # `.git` is not the working tree and `write-tree` adds objects to it on purpose; the
    # assertion above would be vacuous if it silently covered `.git` too, so both halves are
    # named and only this one is allowed to move.
    assert before_git != {}


def test_the_shadow_lands_in_the_cache_and_not_beside_the_repository(harness: Harness) -> None:
    """The shadow root must sit under the cache directory the caller chose."""
    harness.run(IndexTarget())

    assert harness.after.is_dir()
    assert harness.builder.path not in harness.after.parents


# --- incremental behaviour ------------------------------------------------------


def test_a_second_index_sync_touches_only_the_changed_paths(harness: Harness) -> None:
    """The point of the cache: an unchanged file must not be rewritten."""
    harness.builder.write("src/b.py", "def b():\n    return 2\n")
    harness.builder.stage()
    harness.run(IndexTarget())
    stamp(harness.after)

    harness.builder.write("src/b.py", "def b():\n    return 22\n")
    harness.builder.stage()
    delta = harness.run(IndexTarget())

    assert touched(harness.after) == {"src/b.py"}
    assert delta.modified == ["src/b.py"]
    assert delta.added == []
    assert delta.full is False


def test_a_second_sync_with_nothing_staged_touches_nothing(harness: Harness) -> None:
    """A no-op run must be a no-op on disk, not a silent full re-export."""
    harness.run(IndexTarget())
    stamp(harness.after)

    delta = harness.run(IndexTarget())

    assert touched(harness.after) == set()
    assert (delta.added, delta.modified, delta.deleted, delta.full) == ([], [], [], False)


def test_a_second_sync_reports_a_rename_as_a_delete_and_an_add(harness: Harness) -> None:
    """8.1 drives ``und remove`` and ``und add`` from these two lists."""
    harness.run(IndexTarget())
    stamp(harness.after)

    harness.builder.rename("src/a.py", "src/renamed.py")
    delta = harness.run(IndexTarget())

    assert delta.deleted == ["src/a.py"]
    assert delta.added == ["src/renamed.py"]
    assert "src/a.py" not in shadow_files(harness.after)
    assert shadow_files(harness.after)["src/renamed.py"] == "def a():\n    return 1\n"
    assert touched(harness.after) == {"src/renamed.py"}


def test_a_second_sync_removes_a_file_deleted_from_the_index(harness: Harness) -> None:
    """The deletion has to reach the shadow, or the database keeps analysing a dead file."""
    harness.run(IndexTarget())

    harness.builder.delete("readme.md")
    delta = harness.run(IndexTarget())

    assert delta.deleted == ["readme.md"]
    assert "readme.md" not in shadow_files(harness.after)


def test_a_deletion_prunes_the_directory_it_emptied(harness: Harness) -> None:
    """An empty directory left behind is a phantom the database would keep listing."""
    harness.builder.write("pkg/only.py", "x = 1\n")
    harness.builder.stage()
    harness.builder.commit("with a package")
    harness.run(IndexTarget())
    assert "pkg" in entries(harness.after)

    harness.builder.delete("pkg/only.py")
    harness.run(IndexTarget())

    assert "pkg" not in entries(harness.after)


def test_an_unresolvable_recorded_tree_falls_back_to_a_full_sync(harness: Harness) -> None:
    """Measured: ``git gc`` prunes the index tree ``write-tree`` recorded once the index moves.

    ``git diff`` then exits 128 with ``fatal: bad object``. A sync that let that escape would
    turn a routine housekeeping command into a broken hook.
    """
    harness.run(IndexTarget())
    harness.state.after_tree_id = "0" * 40

    delta = harness.run(IndexTarget())

    assert delta.full is True
    assert sorted(shadow_files(harness.after)) == ["readme.md", "src/a.py"]


def test_a_missing_shadow_directory_forces_a_full_sync(harness: Harness) -> None:
    """State that outlives the shadow it describes must not produce a half-populated tree."""
    harness.run(IndexTarget())
    recorded = harness.state.after_tree_id
    shutil.rmtree(harness.after)

    delta = harness.run(IndexTarget())

    assert delta.full is True
    assert harness.state.after_tree_id == recorded
    assert sorted(shadow_files(harness.after)) == ["readme.md", "src/a.py"]


def test_an_emptied_shadow_directory_forces_a_full_sync(harness: Harness) -> None:
    """The directory surviving while its contents do not is the other half of the same class."""
    harness.run(IndexTarget())
    shutil.rmtree(harness.after)
    harness.after.mkdir(parents=True)

    delta = harness.run(IndexTarget())

    assert delta.full is True
    assert sorted(shadow_files(harness.after)) == ["readme.md", "src/a.py"]


# --- target kinds ---------------------------------------------------------------


TRANSITIONS = [
    ("index", "worktree", "WORKTREE\n"),
    ("index", "commit", "COMMIT\n"),
    ("worktree", "index", "INDEX\n"),
    ("worktree", "commit", "COMMIT\n"),
    ("commit", "index", "INDEX\n"),
    ("commit", "worktree", "WORKTREE\n"),
]
"""Every ordered pair of the three kinds, with the bytes the second target must produce."""


@pytest.mark.parametrize(("first", "second", "expected"), TRANSITIONS)
def test_a_target_kind_change_forces_a_full_resync(
    harness: Harness, first: str, second: str, expected: str
) -> None:
    """Both halves matter: the delta says ``full`` *and* the bytes are the new target's."""
    three_way(harness.builder)
    head = harness.builder.run("rev-parse", "HEAD")
    targets: dict[str, SyncTarget] = {
        "index": IndexTarget(),
        "worktree": WorktreeTarget(),
        "commit": CommitTarget(commit=head),
    }

    harness.run(targets[first])
    delta = harness.run(targets[second])

    assert delta.full is True
    assert shadow_files(harness.after)["src/a.py"] == expected
    assert harness.state.after_target == second


@pytest.mark.parametrize("kind", ["index", "worktree"])
def test_the_same_target_kind_twice_is_not_a_full_resync(harness: Harness, kind: str) -> None:
    """The control for the transition table: without it ``full is True`` could be a constant."""
    targets: dict[str, SyncTarget] = {"index": IndexTarget(), "worktree": WorktreeTarget()}

    harness.run(targets[kind])
    delta = harness.run(targets[kind])

    assert delta.full is False


def test_a_commit_target_materialises_that_commit_and_not_head_or_the_index(
    harness: Harness,
) -> None:
    """The three-way fixture again, one commit back, so every wrong answer names itself."""
    first = harness.builder.run("rev-parse", "HEAD")
    three_way(harness.builder)

    harness.run(CommitTarget(commit=first))

    assert shadow_files(harness.after)["src/a.py"] == "def a():\n    return 1\n"


def test_a_commit_target_syncs_incrementally_between_two_commits(harness: Harness) -> None:
    """``explain --range`` walks two commits; only the file that moved may be rewritten."""
    first = harness.builder.run("rev-parse", "HEAD")
    harness.builder.write("src/a.py", "def a():\n    return 99\n")
    harness.builder.stage()
    second = harness.builder.commit("second")

    harness.run(CommitTarget(commit=first))
    stamp(harness.after)
    delta = harness.run(CommitTarget(commit=second))

    assert delta.full is False
    assert delta.modified == ["src/a.py"]
    assert touched(harness.after) == {"src/a.py"}
    assert shadow_files(harness.after)["src/a.py"] == "def a():\n    return 99\n"


def test_a_symbolic_commit_never_serves_as_a_cache_key(harness: Harness) -> None:
    """``HEAD`` names a different commit tomorrow, so it must never be reused as an id."""
    harness.run(CommitTarget(commit="HEAD"))
    harness.builder.write("src/a.py", "def a():\n    return 2\n")
    harness.builder.stage()
    harness.builder.commit("moved HEAD")

    delta = harness.run(CommitTarget(commit="HEAD"))

    assert delta.full is True
    assert shadow_files(harness.after)["src/a.py"] == "def a():\n    return 2\n"


def test_the_before_side_uses_the_before_shadow_and_records_the_commit(harness: Harness) -> None:
    """Requirement 4.3: the pre-change metrics come from a shadow of ``HEAD``."""
    head = harness.builder.run("rev-parse", "HEAD")
    three_way(harness.builder)

    harness.run(IndexTarget())
    harness.run(CommitTarget(commit=head), side="before")

    assert shadow_files(harness.paths.before_tree)["src/a.py"] == "def a():\n    return 1\n"
    assert shadow_files(harness.paths.after_tree)["src/a.py"] == "INDEX\n"
    assert harness.state.before_commit == head
    assert harness.state.after_target == "index"


# --- the worktree target (requirement 10.5) -------------------------------------


def test_the_worktree_target_sees_the_edit_the_agent_has_not_staged(harness: Harness) -> None:
    """The mode exists so an agent can check its edits before staging them."""
    three_way(harness.builder)

    harness.run(WorktreeTarget())

    assert shadow_files(harness.after)["src/a.py"] == "WORKTREE\n"


def test_the_worktree_target_leaves_untracked_files_out(harness: Harness) -> None:
    """Tracked plus staged-new, per the design: a scratch file is not part of the project."""
    harness.builder.write("scratch.py", "throwaway = True\n")
    harness.builder.write("staged_new.py", "fresh = True\n")
    harness.builder.stage("staged_new.py")

    delta = harness.run(WorktreeTarget())

    files = shadow_files(harness.after)
    assert "scratch.py" not in files
    assert files["staged_new.py"] == "fresh = True\n"
    assert "scratch.py" not in delta.added


def test_the_worktree_target_skips_a_file_deleted_from_disk(harness: Harness) -> None:
    """An unstaged deletion: the file is still in the index but is not on disk to copy."""
    harness.builder.delete("readme.md", staged=False)

    delta = harness.run(WorktreeTarget())

    assert "readme.md" not in shadow_files(harness.after)
    assert delta.added == ["src/a.py"]


def test_a_second_worktree_sync_touches_only_the_changed_paths(harness: Harness) -> None:
    """Identical bytes must not be re-copied, or ``analyze -changed`` re-reads the world."""
    harness.run(WorktreeTarget())
    stamp(harness.after)

    harness.builder.unstaged_edit("readme.md", "hello again\n")
    delta = harness.run(WorktreeTarget())

    assert touched(harness.after) == {"readme.md"}
    assert delta.modified == ["readme.md"]
    assert delta.added == []


def test_a_worktree_sync_drops_a_file_that_left_the_index(harness: Harness) -> None:
    """Staging a deletion removes the file from the candidate set, so the shadow must lose it."""
    harness.run(WorktreeTarget())

    harness.builder.delete("readme.md")
    delta = harness.run(WorktreeTarget())

    assert delta.deleted == ["readme.md"]
    assert "readme.md" not in shadow_files(harness.after)


def test_the_worktree_state_id_is_not_shaped_like_a_git_object(harness: Harness) -> None:
    """A 64-hex digest would be indistinguishable from a sha256 commit on the before side."""
    harness.run(WorktreeTarget())

    recorded = harness.state.after_tree_id or ""
    assert recorded.startswith("worktree-")


def test_the_worktree_state_id_changes_when_a_file_changes(harness: Harness) -> None:
    """``SyncState`` calls it a content hash, so it has to answer differently for new content."""
    harness.run(WorktreeTarget())
    first = harness.state.after_tree_id

    harness.builder.unstaged_edit("readme.md", "different\n")
    harness.run(WorktreeTarget())

    assert harness.state.after_tree_id != first


# --- gitlinks and a conflicted index (the two 7.1 handoffs) ---------------------


def add_gitlink(builder: GitRepoBuilder, path: str) -> None:
    """Put a submodule entry in the index without needing a second repository to clone."""
    builder.run(
        "update-index",
        "--add",
        "--cacheinfo",
        f"160000,{'a' * 40},{path}",
    )


def test_a_gitlink_never_becomes_a_phantom_in_the_shadow(harness: Harness) -> None:
    """Measured on 7.1: ``checkout-index`` exits 0 for a gitlink and creates an *empty* dir.

    A sync that trusted ``ls-files`` would then report ``sm`` missing on every single run --
    a permanent phantom -- and ``und add`` on an empty directory does nothing about it.
    """
    add_gitlink(harness.builder, "sm")

    first = harness.run(IndexTarget())

    assert "sm" not in first.added
    assert "sm" not in entries(harness.after)


def test_a_gitlink_bump_is_not_reported_as_a_changed_file(harness: Harness) -> None:
    """The incremental path meets the gitlink through ``diff --name-status``, as an ``M``."""
    add_gitlink(harness.builder, "sm")
    harness.run(IndexTarget())
    harness.builder.run("update-index", "--cacheinfo", f"160000,{'b' * 40},sm")

    second = harness.run(IndexTarget())

    assert second.added == []
    assert second.modified == []
    assert "sm" not in entries(harness.after)


def test_a_gitlink_is_left_out_of_a_worktree_sync(harness: Harness) -> None:
    """The worktree side meets it as a real directory full of another repository's files."""
    add_gitlink(harness.builder, "sm")
    (harness.builder.path / "sm").mkdir()
    (harness.builder.path / "sm" / "inner.py").write_text("other = 1\n", encoding="utf-8")

    delta = harness.run(WorktreeTarget())

    assert "sm" not in delta.added
    assert "sm/inner.py" not in shadow_files(harness.after)


def test_a_conflicted_index_stops_the_sync_instead_of_exporting_a_hole(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """Measured on 7.1: ``checkout-index`` exits 0 writing *nothing* for an unmerged path.

    Going straight to the export would leave a shadow missing the file at the centre of the
    change. ``write-tree`` is asked first precisely so this fails loudly.
    """
    builder = git_repo()
    builder.write("c.py", "base\n")
    builder.stage()
    builder.commit("base")
    builder.run("checkout", "--quiet", "-b", "other")
    builder.write("c.py", "theirs\n")
    builder.stage()
    builder.commit("theirs")
    builder.run("checkout", "--quiet", "main")
    builder.write("c.py", "ours\n")
    builder.stage()
    builder.commit("ours")
    harness = make_harness(builder, tmp_path / "cache")
    with pytest.raises(RuntimeError):
        builder.run("merge", "other")

    with pytest.raises(AnalysisFailedError):
        harness.run(IndexTarget())

    assert shadow_files(harness.after) == {}


# --- include and exclude patterns (requirement 2.5) -----------------------------


EXCLUDED = {
    "node_modules/dep.js": "top-level dependency directory",
    "pkg/node_modules/nested.js": "the same directory one level down",
    "src/__pycache__/a.pyc": "a build artefact beside real sources",
    "build/out.txt": "a build output directory",
    "bundle.min.js": "a minified file anywhere",
    "src/thing.generated.py": "a generated file",
    "package-lock.json": "a lockfile named exactly",
    "sub/uv.lock": "a lockfile matched by suffix one level down",
}
"""One path per shipped default exclude shape, each of which must stay out of the shadow."""


@pytest.fixture
def cluttered(git_repo: MakeGitRepo, tmp_path: Path) -> Harness:
    """A repository holding one file per default-exclude shape plus two real sources."""
    builder = git_repo()
    builder.write("src/a.py", "real = 1\n")
    builder.write("keep.py", "also_real = 1\n")
    for rel in EXCLUDED:
        builder.write(rel, "noise\n")
    builder.stage()
    builder.commit("cluttered")
    return make_harness(builder, tmp_path / "cache", default_settings().project)


@pytest.mark.parametrize("kind", ["index", "commit", "worktree"])
def test_the_default_excludes_keep_noise_out_of_every_target(cluttered: Harness, kind: str) -> None:
    """All three targets share one filter; a default that only worked on one would be a lie."""
    head = cluttered.builder.run("rev-parse", "HEAD")
    targets: dict[str, SyncTarget] = {
        "index": IndexTarget(),
        "commit": CommitTarget(commit=head),
        "worktree": WorktreeTarget(),
    }

    delta = cluttered.run(targets[kind])

    assert sorted(shadow_files(cluttered.after)) == ["keep.py", "src/a.py"]
    assert delta.added == ["keep.py", "src/a.py"]


def test_an_include_pattern_narrows_the_shadow_to_a_subtree(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """Requirement 2.5's other half: only what the operator asked for is analysed."""
    builder = git_repo()
    builder.write("src/a.py", "inside = 1\n")
    builder.write("docs/b.py", "outside = 1\n")
    builder.stage()
    builder.commit("mixed")
    harness = make_harness(builder, tmp_path / "cache", ProjectSettings(include=["src/**"]))

    harness.run(IndexTarget())

    assert sorted(shadow_files(harness.after)) == ["src/a.py"]


def test_a_pattern_added_between_runs_removes_what_it_now_excludes(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """A shadow that kept a newly excluded file would keep feeding it to the database."""
    builder = git_repo()
    builder.write("src/a.py", "keep = 1\n")
    builder.write("vendor/v.py", "drop = 1\n")
    builder.stage()
    builder.commit("both")
    permissive = make_harness(builder, tmp_path / "cache")
    permissive.run(IndexTarget())
    assert "vendor/v.py" in shadow_files(permissive.after)

    strict = make_harness(builder, tmp_path / "cache", ProjectSettings(exclude=["vendor/**"]))
    delta = strict.sync.sync("after", IndexTarget(), permissive.state)

    assert delta.deleted == ["vendor/v.py"]
    assert sorted(shadow_files(strict.after)) == ["src/a.py"]


# --- symbolic links -------------------------------------------------------------


def test_a_tracked_symlink_stays_a_symlink_on_both_targets(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """``checkout-index`` recreates a link (measured), so the worktree side must agree.

    Copying through the link instead would put the target's bytes in the shadow, and a link
    pointing outside the repository would drag foreign content into the analysis.
    """
    builder = git_repo()
    builder.write("real.py", "value = 1\n")
    (builder.path / "link.py").symlink_to("real.py")
    builder.stage()
    builder.commit("with a link")
    index = make_harness(builder, tmp_path / "index-cache")
    worktree = make_harness(builder, tmp_path / "worktree-cache")

    index.run(IndexTarget())
    worktree.run(WorktreeTarget())

    for shadow in (index.after, worktree.after):
        assert (shadow / "link.py").is_symlink()
        assert os.readlink(shadow / "link.py") == "real.py"


# --- the pattern language -------------------------------------------------------


PATTERN_CASES = [
    ("**", "main.py", True, "the default include must reach a file at the root"),
    ("**", "a/b/c.py", True, "and one at any depth"),
    ("src/**", "src/a.py", True, "a subtree pattern reaches into the subtree"),
    ("src/**", "srcbut/a.py", False, "and stops at the directory boundary"),
    ("src/**", "docs/a.py", False, "and does not reach a sibling"),
    ("node_modules/**", "node_modules/d.js", True, "a directory pattern at the root"),
    ("node_modules/**", "pkg/node_modules/d.js", True, "and at any depth below it"),
    ("/node_modules/**", "pkg/node_modules/d.js", False, "a leading slash anchors it"),
    ("/node_modules/**", "node_modules/d.js", True, "at the root, where it still applies"),
    ("*.min.js", "bundle.min.js", True, "a bare wildcard matches at the root"),
    ("*.min.js", "web/bundle.min.js", True, "and at any depth, as git's own rule has it"),
    ("*.min.js", "bundle.min.js.map", False, "and does not match a longer name"),
    ("build", "build/out.o", True, "a directory name excludes everything under it"),
    ("build", "rebuild/out.o", False, "without matching a partial segment"),
    ("a/**/b.py", "a/b.py", True, "a middle ** may match nothing"),
    ("a/**/b.py", "a/x/y/b.py", True, "or several segments"),
    ("?.py", "a.py", True, "a question mark matches one character"),
    ("?.py", "ab.py", False, "and not two"),
    ("*.py", "a/b.py", True, "a star matches inside one segment at any depth"),
    ("x/*.py", "x/y/b.py", False, "and never crosses a separator"),
]
"""One row per rule of the pattern language, each with the reason it is there."""


@pytest.mark.parametrize(("pattern", "path", "expected", "why"), PATTERN_CASES)
def test_pattern_matching_follows_the_documented_rules(
    pattern: str, path: str, expected: bool, why: str
) -> None:
    """Excludes and includes share one matcher, so one table pins both."""
    excluding = PathFilter.from_settings(ProjectSettings(exclude=[pattern]))
    including = PathFilter.from_settings(ProjectSettings(include=[pattern]))

    assert excluding.allows(path) is not expected, why
    assert including.allows(path) is expected, why


def test_an_empty_include_list_selects_nothing() -> None:
    """Read literally, because the default is ``["**"]`` and an empty list is a deliberate act."""
    assert PathFilter.from_settings(ProjectSettings(include=[])).allows("a.py") is False


def test_a_blank_pattern_is_ignored_rather_than_matching_everything() -> None:
    """A stray empty string in a list must not silently exclude the whole project."""
    assert PathFilter.from_settings(ProjectSettings(exclude=[""])).allows("a.py") is True


def test_a_pattern_metacharacter_is_taken_literally() -> None:
    """The language has no character classes, so ``[`` names a file rather than opening one."""
    assert PathFilter.from_settings(ProjectSettings(exclude=["[ab].py"])).allows("a.py") is True
    assert PathFilter.from_settings(ProjectSettings(exclude=["[ab].py"])).allows("[ab].py") is False


def test_deleting_an_excluded_file_reports_no_deletion(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """8.1 runs ``und remove -file @list``, which exits 1 on a path the database never held.

    An excluded file was never in the shadow, so its deletion is not a deletion the database
    has to hear about.
    """
    builder = git_repo()
    builder.write("src/a.py", "keep = 1\n")
    builder.write("vendor/v.py", "never analysed = 1\n")
    builder.stage()
    builder.commit("with a vendored file")
    harness = make_harness(builder, tmp_path / "cache", ProjectSettings(exclude=["vendor/**"]))
    harness.run(IndexTarget())

    builder.delete("vendor/v.py")
    delta = harness.run(IndexTarget())

    assert delta.deleted == []
    assert delta.added == []
    assert delta.modified == []


def test_a_full_resync_drops_a_file_the_new_target_does_not_have(harness: Harness) -> None:
    """A full sync must rebuild, not overlay: a survivor would be analysed as part of the change.

    The transition table cannot see this on its own -- every file in it exists on both sides
    of the transition -- so the file here exists in the index and in no commit.
    """
    head = harness.builder.run("rev-parse", "HEAD")
    harness.builder.write("staged_only.py", "never committed = 1\n")
    harness.builder.stage()
    harness.run(IndexTarget())
    assert "staged_only.py" in shadow_files(harness.after)

    delta = harness.run(CommitTarget(commit=head))

    assert delta.full is True
    assert "staged_only.py" not in shadow_files(harness.after)
    assert "staged_only.py" not in delta.added


def test_changing_an_excluded_file_produces_no_delta_at_all(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """An excluded file must not reach the shadow through the incremental path either.

    Exporting it and sweeping it afterwards would leave the same bytes on disk but report the
    path as *deleted*, and ``und remove`` exits 1 on a path the database never held.
    """
    builder = git_repo()
    builder.write("src/a.py", "keep = 1\n")
    builder.write("vendor/v.py", "never analysed = 1\n")
    builder.stage()
    builder.commit("with a vendored file")
    harness = make_harness(builder, tmp_path / "cache", ProjectSettings(exclude=["vendor/**"]))
    harness.run(IndexTarget())

    builder.write("vendor/v.py", "still never analysed = 2\n")
    builder.stage()
    delta = harness.run(IndexTarget())

    assert (delta.added, delta.modified, delta.deleted) == ([], [], [])
    assert sorted(shadow_files(harness.after)) == ["src/a.py"]


def test_a_path_that_changes_between_file_and_directory_is_handled_both_ways(
    harness: Harness,
) -> None:
    """``foo.py`` becoming ``foo/__init__.py`` and back: the ordering claim, pinned.

    Deletions are applied before exports so that neither direction needs a special case, and
    both directions are exercised because a fix on one of them is this project's most
    repeated defect shape.
    """
    harness.builder.write("foo.py", "value = 1\n")
    harness.builder.stage()
    harness.builder.commit("a module")
    harness.run(IndexTarget())

    harness.builder.rename("foo.py", "foo/__init__.py")
    harness.builder.commit("a package")
    into_directory = harness.run(IndexTarget())

    assert into_directory.deleted == ["foo.py"]
    assert into_directory.added == ["foo/__init__.py"]
    assert shadow_files(harness.after)["foo/__init__.py"] == "value = 1\n"

    harness.builder.rename("foo/__init__.py", "foo.py")
    harness.builder.commit("a module again")
    back_to_file = harness.run(IndexTarget())

    assert back_to_file.deleted == ["foo/__init__.py"]
    assert back_to_file.added == ["foo.py"]
    assert shadow_files(harness.after)["foo.py"] == "value = 1\n"
    assert "foo" not in entries(harness.after)
