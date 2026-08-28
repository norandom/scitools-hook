"""The shared test infrastructure itself: git repo builder, fakes, contract gate (task 3.2).

These are smoke tests for the fixtures in ``tests/conftest.py``. They exist because every
later task builds on them: the git builder must be able to produce the index/worktree
divergence requirement 4.1 turns on and the ``.git/hooks`` directory requirement 11.1 writes
into, the fakes must really satisfy the ports in ``models/progress``, and the contract gate
must skip rather than fail on a machine without a licensed Understand.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import (
    FakeCommandLog,
    FakeProgress,
    GitRepoBuilder,
    MakeGitRepo,
    SampleDatabases,
    understand_probe,
)

from scitools_hook.models.progress import CommandLog, Progress

# --- the temporary git repository builder --------------------------------------


def test_the_factory_returns_an_initialized_repository(git_repo: MakeGitRepo) -> None:
    repo = git_repo()
    assert (repo.path / ".git").is_dir()
    # An unborn branch: HEAD points at main before the first commit exists.
    assert repo.run("symbolic-ref", "--short", "HEAD") == "main"
    assert repo.run("status", "--porcelain") == ""


def test_the_repository_has_a_hooks_directory_to_install_into(git_repo: MakeGitRepo) -> None:
    # Requirement 11.1: install-hook writes a shim into the repository's hooks directory.
    repo = git_repo()
    hooks = Path(repo.run("rev-parse", "--git-path", "hooks"))
    assert (repo.path / hooks).is_dir()


def test_two_repositories_from_one_test_do_not_collide(git_repo: MakeGitRepo) -> None:
    first, second = git_repo("one"), git_repo("two")
    assert first.path != second.path
    first.write("a.py", "a = 1\n")
    first.stage("a.py")
    first.commit("first")
    assert second.run("status", "--porcelain") == ""


def test_a_commit_records_the_staged_content(git_repo: MakeGitRepo) -> None:
    repo = git_repo()
    repo.write("src/app.py", "def main() -> None:\n    pass\n")
    repo.stage("src/app.py")
    head = repo.commit("add app")
    assert head == repo.run("rev-parse", "HEAD")
    assert repo.run("ls-tree", "--name-only", "-r", "HEAD") == "src/app.py"


def test_the_index_holds_the_staged_content_while_the_worktree_diverges(
    git_repo: MakeGitRepo,
) -> None:
    # Requirement 4.1: staged mode reads the index, and ignores unstaged modifications.
    repo = git_repo()
    repo.write("src/app.py", "def main() -> None:\n    pass\n")
    repo.stage("src/app.py")
    repo.commit("initial")

    staged = "def main() -> None:\n    return None\n"
    working = "def main() -> None:\n    raise SystemExit(1)\n"
    repo.write("src/app.py", staged)
    repo.stage("src/app.py")
    repo.unstaged_edit("src/app.py", working)

    assert repo.run("diff", "--cached", "--name-status") == "M\tsrc/app.py"
    assert repo.staged_content("src/app.py") == staged
    assert (repo.path / "src/app.py").read_text() == working
    assert repo.run("diff", "--name-only") == "src/app.py"


def test_a_staged_rename_is_reported_as_a_rename(git_repo: MakeGitRepo) -> None:
    repo = git_repo()
    repo.write("src/old.py", "VALUE = 1\n" * 10)
    repo.stage("src/old.py")
    repo.commit("initial")

    repo.rename("src/old.py", "src/new.py")

    status = repo.run("diff", "--cached", "--name-status", "-M")
    assert status.startswith("R")
    assert status.endswith("src/old.py\tsrc/new.py")
    assert not (repo.path / "src/old.py").exists()


def test_an_unstaged_rename_leaves_the_index_untouched(git_repo: MakeGitRepo) -> None:
    repo = git_repo()
    repo.write("src/old.py", "VALUE = 1\n")
    repo.stage("src/old.py")
    repo.commit("initial")

    repo.rename("src/old.py", "src/new.py", staged=False)

    assert repo.run("diff", "--cached", "--name-status") == ""
    assert (repo.path / "src/new.py").exists()


def test_a_deletion_can_be_staged(git_repo: MakeGitRepo) -> None:
    # Requirement 4.10: the deletions-only change must be expressible.
    repo = git_repo()
    repo.write("gone.py", "x = 1\n")
    repo.stage("gone.py")
    repo.commit("initial")

    repo.delete("gone.py")

    assert repo.run("diff", "--cached", "--name-status") == "D\tgone.py"


def test_the_builder_type_is_the_helper_class(git_repo: MakeGitRepo) -> None:
    assert isinstance(git_repo(), GitRepoBuilder)


# --- fakes for the Progress and CommandLog ports --------------------------------


def test_the_fake_command_log_satisfies_the_port(command_log: FakeCommandLog) -> None:
    assert isinstance(command_log, CommandLog)
    assert command_log.calls == []


def test_the_fake_command_log_records_every_command(command_log: FakeCommandLog) -> None:
    command_log.record(["git", "diff", "--cached"], 0.02, 0)
    command_log.record(["und", "analyze"], 4.5, 1)
    assert command_log.calls == [
        (["git", "diff", "--cached"], 0.02, 0),
        (["und", "analyze"], 4.5, 1),
    ]
    assert command_log.commands == ["git diff --cached", "und analyze"]


def test_the_fake_command_log_copies_the_argv_it_is_given(command_log: FakeCommandLog) -> None:
    argv = ["und", "create"]
    command_log.record(argv, 1.0, 0)
    argv.append("-db")
    assert command_log.calls[0][0] == ["und", "create"]


def test_the_fake_progress_satisfies_the_port(progress: FakeProgress) -> None:
    assert isinstance(progress, Progress)
    progress.start("analyze")
    progress.note("2 files re-analyzed")
    progress.finish("analyze", 6.5)
    assert progress.started == ["analyze"]
    assert progress.finished == [("analyze", 6.5)]
    assert progress.notes == ["2 files re-analyzed"]


# --- the contract gate ----------------------------------------------------------


def test_the_contract_marker_is_registered(pytestconfig: pytest.Config) -> None:
    markers = pytestconfig.getini("markers")
    assert any(marker.startswith("contract:") for marker in markers)


def test_the_probe_reports_a_reason_exactly_when_understand_is_unusable() -> None:
    probe = understand_probe()
    assert probe.usable is (probe.reason is None)
    if probe.usable:
        assert probe.und is not None
    else:
        assert probe.reason


def test_the_probe_is_computed_once_per_session() -> None:
    assert understand_probe() is understand_probe()


def test_the_probe_names_an_install_directory_without_und(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCITOOLS_HOME", str(tmp_path))
    probe = understand_probe.__wrapped__()  # the session cache would hide the environment
    assert not probe.usable
    assert probe.reason is not None
    assert "SCITOOLS_HOME" in probe.reason and str(tmp_path) in probe.reason


def test_the_probe_names_the_missing_variable_when_und_is_not_on_the_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SCITOOLS_HOME", raising=False)
    monkeypatch.setattr("conftest.shutil.which", lambda name: None)
    probe = understand_probe.__wrapped__()
    assert not probe.usable
    assert probe.reason == "SCITOOLS_HOME is not set and no 'und' executable is on PATH"


# --- the sample project and the databases built from it -------------------------


def test_the_sample_project_has_a_before_and_an_after_tree(sample_project: Path) -> None:
    for side in ("before", "after"):
        assert (sample_project / side).is_dir()
    suffixes = {p.suffix for p in (sample_project / "before").rglob("*") if p.is_file()}
    assert suffixes >= {".py", ".c"}


def test_the_after_tree_adds_a_cxx_file(sample_project: Path) -> None:
    before = {p.name for p in (sample_project / "before").rglob("*.cpp")}
    after = {p.name for p in (sample_project / "after").rglob("*.cpp")}
    assert not before
    assert after


@pytest.mark.contract
def test_the_sample_databases_are_two_openable_databases(
    sample_databases: SampleDatabases,
) -> None:
    for side in ("before", "after"):
        db = sample_databases.db(side)
        assert db.exists()
        assert sample_databases.root(side).is_dir()
        assert sample_databases.list_files(side)
