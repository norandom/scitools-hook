"""Installing, chaining and removing the pre-commit shim (task 7.3, requirement 11).

This is the component that **writes into the operator's repository**, so the tests are
written around the two ways that goes wrong quietly:

* **Destroying a hook that was already there.** Every refusal is checked by comparing the
  file's *bytes and mode* before and after, not by checking that some file still exists; and
  the chained copy is compared the same way, because "restored" that silently drops the
  executable bit leaves a hook git will never run again.
* **Writing where requirement 2.2 forbids.** ``core.hooksPath`` can point inside the working
  tree -- ``.`` and any relative value do -- and installing there puts a file in ``git
  status``. The default case is checked against ``git status --porcelain`` directly, and the
  configured cases are refused on both arms of ``global_``.

The ``global_`` parameter gets both of its arms tested with **different directories** for the
repository-level and user-level settings, so a branch that reads the wrong one cannot pass by
answering the same path twice. That is this module's recorded defect shape: nine defects in
``git/repo.py`` were one case handled correctly on one side of a discriminator and wrongly on
the other, with both branches covered but never with the input that told them apart.
"""

from __future__ import annotations

import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
from conftest import FakeCommandLog, GitRepoBuilder, MakeGitRepo

from scitools_hook.errors import ConfigError
from scitools_hook.git import hooks
from scitools_hook.git.hooks import HookInstaller
from scitools_hook.git.repo import GitRepo

FOREIGN_HOOK = "#!/bin/sh\necho somebody else's hook\nexit 0\n"
"""A hook the operator wrote: the thing the installer must never quietly destroy."""

FOREIGN_MODE = 0o750
"""Deliberately not the mode the installer writes, so a restore that guesses is visible."""

LEAKED_GIT_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_CONFIG_NOSYSTEM",
    "GIT_CONFIG_COUNT",
    "GIT_CEILING_DIRECTORIES",
    "GIT_OBJECT_DIRECTORY",
    "GIT_COMMON_DIR",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)
"""Everything an outer ``git`` invocation exports that would steer these subprocesses."""


@dataclass(frozen=True)
class GitEnv:
    """The throwaway git environment the installer sees while one test runs."""

    home: Path
    config: Path
    global_config: Path
    system_config: Path

    def set_global_hooks_path(self, path: Path) -> None:
        """Point the *user-level* ``core.hooksPath`` at ``path`` (requirement 11.9)."""
        self.global_config.write_text(f"[core]\n\thooksPath = {path}\n", encoding="utf-8")


@pytest.fixture(autouse=True)
def git_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> GitEnv:
    """Isolate every ``git`` the installer starts from the developer's own configuration.

    Written out here rather than imported from ``tests/git/test_repo.py``: a test module
    importing a sibling test module is exactly what breaks under the ``--import-mode=importlib``
    switch task 10.3 is committed to making, and twenty lines of duplication is a smaller debt
    than a collection error nobody expects.
    """
    home = tmp_path / "git-home"
    config = tmp_path / "xdg-config"
    home.mkdir(parents=True)
    config.mkdir(parents=True)
    environment = GitEnv(
        home=home,
        config=config,
        global_config=home / "gitconfig",
        system_config=home / "gitsystem",
    )
    environment.global_config.write_text("", encoding="utf-8")
    environment.system_config.write_text("", encoding="utf-8")
    for leaked in LEAKED_GIT_VARS:
        monkeypatch.delenv(leaked, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(environment.global_config))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(environment.system_config))
    return environment


@pytest.fixture
def repository(git_repo: MakeGitRepo) -> GitRepoBuilder:
    """A repository with one commit, which is the state a hook is installed into."""
    builder = git_repo()
    builder.write("a.py", "x = 1\n")
    builder.stage()
    builder.commit("first")
    return builder


def installer(builder: GitRepoBuilder) -> HookInstaller:
    """The installer for a builder's repository."""
    return HookInstaller(GitRepo.discover(builder.path, FakeCommandLog()))


def mode_of(path: Path) -> int:
    """The permission bits of ``path``, with the file-type bits removed."""
    return stat.S_IMODE(path.stat().st_mode)


def foreign_hook_at(directory: Path) -> Path:
    """Put a hook the Gate did not write at ``directory/pre-commit`` and return it."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / hooks.HOOK_NAME
    path.write_text(FOREIGN_HOOK, encoding="utf-8")
    path.chmod(FOREIGN_MODE)
    return path


# --- the template itself --------------------------------------------------------


def test_the_template_is_shipped_beside_the_installer() -> None:
    """A missing data file would break ``install-hook`` for everyone but a source checkout."""
    assert hooks.TEMPLATE_PATH.is_file()


def test_the_template_is_pure_ascii() -> None:
    """The proof behind ``encoding="utf-8"`` on the write, which is otherwise unpinnable.

    A round trip cannot see an encoding argument, and under a UTF-8 locale the bytes are
    identical either way (measured elsewhere in this project). Here the argument is provably
    equivalent instead: every ASCII-compatible codec, the C locale's included, encodes this
    content identically. The assertion is what keeps that proof true -- add one accented
    character to the template and this test, not a mysterious CI failure, reports it.
    """
    assert hooks.TEMPLATE_PATH.read_bytes().isascii()


def test_render_substitutes_the_resolved_command_and_leaves_no_placeholder() -> None:
    """The one substitution the shim carries, from a fixed set of strings rather than input."""
    rendered = hooks.render(hooks.RESOLVED_UVX)
    assert hooks.RESOLVED_UVX in rendered
    assert hooks.RESOLVED_PLACEHOLDER not in rendered


def test_the_rendered_shim_starts_with_a_shebang_and_carries_the_marker() -> None:
    """Git executes the file directly, and uninstall recognises it by the marker line."""
    lines = hooks.render(hooks.RESOLVED_DIRECT).splitlines()
    assert lines[0] == "#!/bin/sh"
    assert hooks.MARKER in lines


# --- installing (requirements 11.1, 11.3) ---------------------------------------


def test_install_writes_an_executable_shim_where_git_looks_for_it(
    repository: GitRepoBuilder,
) -> None:
    """Requirement 11.1: the shim goes into the hooks directory and is executable."""
    report = installer(repository).install()

    expected = repository.path / ".git" / "hooks" / "pre-commit"
    assert report.path == expected
    assert report.action == "installed"
    assert report.chained is None
    assert expected.read_text(encoding="utf-8").startswith("#!/bin/sh")
    assert mode_of(expected) == hooks.SHIM_MODE
    assert os.access(expected, os.X_OK)


def test_install_writes_nothing_into_the_working_tree(repository: GitRepoBuilder) -> None:
    """Requirement 2.2, asked of git itself rather than of a path comparison."""
    installer(repository).install()
    assert repository.run("status", "--porcelain") == ""


def test_the_shim_does_not_depend_on_the_configuration_it_gates(
    git_repo: MakeGitRepo,
) -> None:
    """Requirement 11.3: changing a limit must never mean reinstalling the hook.

    Two repositories with different configuration files produce byte-identical shims. A
    build that baked a threshold, a path or a repository name into the script fails here.
    """
    first = git_repo("strict")
    first.write("scitools-hook.toml", "[thresholds.routine]\nCyclomatic = 5\n")
    first.stage()
    first.commit("first")
    second = git_repo("relaxed")
    second.write("scitools-hook.toml", "[thresholds.routine]\nCyclomatic = 50\n")
    second.stage()
    second.commit("first")

    installer(first).install()
    installer(second).install()

    assert (first.path / ".git/hooks/pre-commit").read_bytes() == (
        second.path / ".git/hooks/pre-commit"
    ).read_bytes()


def test_install_creates_a_hooks_directory_that_does_not_exist(
    repository: GitRepoBuilder,
) -> None:
    """A configured ``core.hooksPath`` need not exist yet; git looks there either way."""
    elsewhere = repository.path.parent / "hooks-elsewhere"
    repository.run("config", "core.hooksPath", str(elsewhere))

    report = installer(repository).install()

    assert report.path == elsewhere / "pre-commit"
    assert report.path.is_file()


def test_install_refuses_a_second_time_and_changes_nothing(repository: GitRepoBuilder) -> None:
    """Requirement 11.2's refusal, checked on the bytes rather than on the report alone."""
    first = installer(repository).install()
    before = first.path.read_bytes()

    second = installer(repository).install()

    assert second.action == "refused"
    assert second.path == first.path
    assert second.chained is None
    assert first.path.read_bytes() == before
    assert not first.path.with_name("pre-commit" + hooks.CHAINED_SUFFIX).exists()


def test_install_refuses_an_existing_hook_it_did_not_write(repository: GitRepoBuilder) -> None:
    """The important half of the refusal: somebody else's hook, untouched to the byte."""
    existing = foreign_hook_at(repository.path / ".git" / "hooks")

    report = installer(repository).install()

    assert report.action == "refused"
    assert existing.read_text(encoding="utf-8") == FOREIGN_HOOK
    assert mode_of(existing) == FOREIGN_MODE
    assert not existing.with_name("pre-commit" + hooks.CHAINED_SUFFIX).exists()


def test_a_foreign_hook_that_merely_mentions_the_marker_is_still_foreign(
    repository: GitRepoBuilder,
) -> None:
    """The marker has to be a line of its own, or a hook that quotes it would be deleted."""
    directory = repository.path / ".git" / "hooks"
    directory.mkdir(parents=True, exist_ok=True)
    existing = directory / "pre-commit"
    existing.write_text(f"#!/bin/sh\necho 'not a {hooks.MARKER} at all'\n", encoding="utf-8")
    existing.chmod(FOREIGN_MODE)

    assert installer(repository).install().action == "refused"
    assert installer(repository).uninstall().action == "refused"
    assert existing.exists()


# --- forcing and chaining (requirement 11.2) ------------------------------------


def test_force_keeps_the_existing_hook_and_chains_to_it(repository: GitRepoBuilder) -> None:
    """Requirement 11.2: the existing hook's content is kept and run after the Gate's check."""
    existing = foreign_hook_at(repository.path / ".git" / "hooks")

    report = installer(repository).install(force=True)

    assert report.action == "installed"
    assert report.chained == existing.with_name("pre-commit" + hooks.CHAINED_SUFFIX)
    assert report.chained is not None
    assert report.chained.read_text(encoding="utf-8") == FOREIGN_HOOK
    assert mode_of(report.chained) == FOREIGN_MODE
    assert hooks.MARKER in existing.read_text(encoding="utf-8")


def test_force_over_our_own_shim_does_not_chain_the_shim_to_itself(
    repository: GitRepoBuilder,
) -> None:
    """Reinstalling must replace the shim, never turn the previous one into a chained hook.

    Chaining our own shim would run the Gate twice per commit and, worse, make ``uninstall``
    "restore" a shim -- leaving the repository gated by a file the operator cannot see the
    origin of.
    """
    first = installer(repository).install()

    report = installer(repository).install(force=True)

    assert report.action == "installed"
    assert report.chained is None
    assert not first.path.with_name("pre-commit" + hooks.CHAINED_SUFFIX).exists()
    assert hooks.MARKER in first.path.read_text(encoding="utf-8")


def test_force_refuses_when_a_chained_hook_is_already_stored(
    repository: GitRepoBuilder,
) -> None:
    """Two hooks and one slot: overwriting the stored one would lose the operator's file."""
    directory = repository.path / ".git" / "hooks"
    foreign_hook_at(directory)
    stored = directory / ("pre-commit" + hooks.CHAINED_SUFFIX)
    stored.write_text("#!/bin/sh\necho stored\n", encoding="utf-8")

    with pytest.raises(ConfigError) as refusal:
        installer(repository).install(force=True)

    assert str(stored) in str(refusal.value)
    assert stored.read_text(encoding="utf-8") == "#!/bin/sh\necho stored\n"


def test_reinstalling_reports_a_chained_hook_that_is_already_stored(
    repository: GitRepoBuilder,
) -> None:
    """After a forced install, installing again with force keeps and reports the chained hook."""
    foreign_hook_at(repository.path / ".git" / "hooks")
    first = installer(repository).install(force=True)

    second = installer(repository).install(force=True)

    assert second.chained == first.chained
    assert second.chained is not None
    assert second.chained.read_text(encoding="utf-8") == FOREIGN_HOOK


# --- uninstalling (requirement 11.6) --------------------------------------------


def test_uninstall_restores_the_chained_hook_byte_for_byte(repository: GitRepoBuilder) -> None:
    """Requirement 11.6: what was here before comes back exactly, mode included."""
    existing = foreign_hook_at(repository.path / ".git" / "hooks")
    installed = installer(repository).install(force=True)

    report = installer(repository).uninstall()

    assert report.action == "restored"
    assert report.path == existing
    assert report.chained == installed.chained
    assert existing.read_text(encoding="utf-8") == FOREIGN_HOOK
    assert mode_of(existing) == FOREIGN_MODE
    assert installed.chained is not None
    assert not installed.chained.exists()


def test_uninstall_removes_a_shim_that_chained_nothing(repository: GitRepoBuilder) -> None:
    """The ordinary case: the shim goes, and nothing is left behind in its place."""
    installed = installer(repository).install()

    report = installer(repository).uninstall()

    assert report.action == "uninstalled"
    assert report.chained is None
    assert not installed.path.exists()


def test_uninstall_reports_that_there_was_nothing_to_remove(repository: GitRepoBuilder) -> None:
    """Distinct from a refusal: nothing was installed, so nothing was left behind either."""
    report = installer(repository).uninstall()

    assert report.action == "absent"
    assert report.path == repository.path / ".git" / "hooks" / "pre-commit"


def test_uninstall_refuses_a_hook_it_did_not_write(repository: GitRepoBuilder) -> None:
    """Requirement 11.6 says "only the shim it installed", checked on bytes and mode."""
    existing = foreign_hook_at(repository.path / ".git" / "hooks")

    report = installer(repository).uninstall()

    assert report.action == "refused"
    assert existing.read_text(encoding="utf-8") == FOREIGN_HOOK
    assert mode_of(existing) == FOREIGN_MODE


def test_uninstall_leaves_a_stored_hook_alone_when_the_shim_is_not_ours(
    repository: GitRepoBuilder,
) -> None:
    """A refusal must not tidy up around itself: the stored hook is the operator's too."""
    directory = repository.path / ".git" / "hooks"
    foreign_hook_at(directory)
    stored = directory / ("pre-commit" + hooks.CHAINED_SUFFIX)
    stored.write_text("#!/bin/sh\necho stored\n", encoding="utf-8")

    assert installer(repository).uninstall().action == "refused"
    assert stored.exists()


def test_install_and_uninstall_round_trip_leaves_the_repository_as_it_was(
    repository: GitRepoBuilder,
) -> None:
    """The property an operator actually cares about: trying the Gate costs them nothing."""
    directory = repository.path / ".git" / "hooks"
    existing = foreign_hook_at(directory)
    before = sorted(path.name for path in directory.iterdir())

    installer(repository).install(force=True)
    installer(repository).uninstall()

    assert sorted(path.name for path in directory.iterdir()) == before
    assert existing.read_bytes() == FOREIGN_HOOK.encode("utf-8")
    assert mode_of(existing) == FOREIGN_MODE


# --- the global hooks path (requirement 11.9) -----------------------------------


def test_install_global_writes_into_the_user_hooks_path(
    repository: GitRepoBuilder, git_env: GitEnv, tmp_path: Path
) -> None:
    """Requirement 11.9, with the two settings pointing at *different* directories.

    Both arms of ``global_`` are exercised against the same repository, so a version reading
    the repository setting for both cannot pass by answering one path twice.
    """
    user_hooks = tmp_path / "user-hooks"
    repository_hooks = tmp_path / "repository-hooks"
    git_env.set_global_hooks_path(user_hooks)
    repository.run("config", "core.hooksPath", str(repository_hooks))

    globally = installer(repository).install(global_=True)
    locally = installer(repository).install()

    assert globally.path == user_hooks / "pre-commit"
    assert locally.path == repository_hooks / "pre-commit"
    assert globally.path.is_file()
    assert locally.path.is_file()


def test_install_global_falls_back_to_the_user_configuration_directory(
    repository: GitRepoBuilder, git_env: GitEnv
) -> None:
    """With no ``core.hooksPath`` anywhere, the XDG location is used and created."""
    report = installer(repository).install(global_=True)

    assert report.path == git_env.config / "git" / "hooks" / "pre-commit"
    assert report.path.is_file()
    assert mode_of(report.path) == hooks.SHIM_MODE


def test_uninstall_global_removes_the_shim_from_the_user_hooks_path(
    repository: GitRepoBuilder, git_env: GitEnv, tmp_path: Path
) -> None:
    """The other arm of the same discriminator: an install you cannot undo is not an install."""
    user_hooks = tmp_path / "user-hooks"
    repository_hooks = tmp_path / "repository-hooks"
    git_env.set_global_hooks_path(user_hooks)
    repository.run("config", "core.hooksPath", str(repository_hooks))
    installer(repository).install(global_=True)
    installer(repository).install()

    report = installer(repository).uninstall(global_=True)

    assert report.action == "uninstalled"
    assert not (user_hooks / "pre-commit").exists()
    assert (repository_hooks / "pre-commit").is_file()


# --- refusing to write into the working tree (requirement 2.2) ------------------


def test_install_refuses_a_hooks_path_that_is_the_working_tree_root(
    repository: GitRepoBuilder,
) -> None:
    """``core.hooksPath = .`` resolves to the root, where git runs nothing and we write."""
    repository.run("config", "core.hooksPath", ".")

    with pytest.raises(ConfigError) as refusal:
        installer(repository).install()

    assert refusal.value.key == "core.hooksPath"
    assert not (repository.path / "pre-commit").exists()


def test_install_refuses_a_hooks_path_inside_the_working_tree(
    repository: GitRepoBuilder,
) -> None:
    """A relative value resolves against the worktree root, so the shim would be committable.

    This is the common ``.githooks`` layout, and refusing it is a deliberate decision rather
    than an oversight: requirement 11's whole objective is switching the Gate on *without*
    committing hook scripts, and ``.pre-commit-hooks.yaml`` is the supported answer for a
    repository that wants its hooks in the tree.
    """
    repository.run("config", "core.hooksPath", ".githooks")

    with pytest.raises(ConfigError) as refusal:
        installer(repository).install()

    assert refusal.value.key == "core.hooksPath"
    assert "working tree" in str(refusal.value)
    assert not (repository.path / ".githooks" / "pre-commit").exists()


def test_install_global_refuses_a_user_hooks_path_inside_the_working_tree(
    repository: GitRepoBuilder, git_env: GitEnv
) -> None:
    """The same refusal on the other arm, reached with an absolute value this time."""
    git_env.set_global_hooks_path(repository.path / "in-tree-hooks")

    with pytest.raises(ConfigError):
        installer(repository).install(global_=True)

    assert not (repository.path / "in-tree-hooks").exists()


def test_the_git_directory_is_not_the_working_tree(repository: GitRepoBuilder) -> None:
    """The refusal must not swallow the default: ``.git/hooks`` is inside ``root`` on disk.

    Without this the guard would be written the obvious way -- "is it under the root?" -- and
    refuse every ordinary repository. ``git status`` never shows anything under ``.git``,
    which is what makes that location the right one.
    """
    report = installer(repository).install()

    assert repository.path in report.path.parents
    assert report.path.is_file()


# --- hostile shapes at the hook's own path --------------------------------------


def test_install_refuses_when_the_hook_path_is_a_directory(repository: GitRepoBuilder) -> None:
    """A directory named ``pre-commit`` is not absent and not a hook; say which."""
    (repository.path / ".git" / "hooks" / "pre-commit").mkdir(parents=True, exist_ok=True)

    with pytest.raises(ConfigError) as refusal:
        installer(repository).install(force=True)

    assert "pre-commit" in str(refusal.value)


def test_install_refuses_when_the_hooks_path_is_a_regular_file(
    repository: GitRepoBuilder, tmp_path: Path
) -> None:
    """A ``core.hooksPath`` pointing at a file cannot hold a hook, and never will."""
    occupied = tmp_path / "not-a-directory"
    occupied.write_text("", encoding="utf-8")
    repository.run("config", "core.hooksPath", str(occupied))

    with pytest.raises(ConfigError) as refusal:
        installer(repository).install()

    assert str(occupied) in str(refusal.value)


def test_uninstall_refuses_when_the_hook_path_is_a_directory(
    repository: GitRepoBuilder,
) -> None:
    """The same shape on the removal side, where the temptation is to delete it anyway."""
    (repository.path / ".git" / "hooks" / "pre-commit").mkdir(parents=True, exist_ok=True)

    with pytest.raises(ConfigError):
        installer(repository).uninstall()

    assert (repository.path / ".git" / "hooks" / "pre-commit").is_dir()


# --- the installed shim, as git will run it -------------------------------------


def test_the_installed_shim_runs_and_blocks_a_commit(repository: GitRepoBuilder) -> None:
    """The installer's output is a working hook, not just a file with the right bytes."""
    report = installer(repository).install()
    bindir = repository.path.parent / "stub-bin"
    bindir.mkdir()
    gate = bindir / "scitools-hook"
    gate.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    gate.chmod(0o755)

    finished = subprocess.run(
        [str(report.path)],
        capture_output=True,
        env={"PATH": str(bindir)},
        cwd=repository.path,
        timeout=60,
        check=False,
    )

    assert finished.returncode == 1
