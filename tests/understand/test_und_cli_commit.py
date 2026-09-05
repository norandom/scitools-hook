"""Databases built from a git commit, and the repository a database remembers (req 3.1).

Understand 8.0 can read a database's file *contents* from a commit instead of from the
checkout on disk. That is what lets the before side of a check exist without exporting a
shadow tree, and it is the only thing `-gitcommit` does: it does not choose the files.
Which files a commit-built database holds depends on `-refdb`, measured on Build 1262 --
with it, the reference's settings and file set are copied and then rescanned against the
pinned commit; without it the database starts empty and needs `und add`.

Both commands are **module functions taking the wrapper**, not methods on it, for the reason
recorded in the implementation notes of task 1.5: `UndCli` is five over its coupling limit
already, so a new type named inside the class is refused by the gate. `GitSource` is named
here instead, and three of the five architecture commands already live this way.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from und_stub import RecordingLog, UndStub, cli, db_path, write_stub

from scitools_hook.errors import AnalysisFailedError
from scitools_hook.understand.und_cli import GitSource, create_from_commit, set_git_repository


@pytest.fixture
def stub(tmp_path: Path) -> UndStub:
    """A stubbed ``und`` executable with an empty plan, ready to be scripted."""
    return write_stub(tmp_path)


@pytest.fixture
def log() -> RecordingLog:
    """A fresh recording command log."""
    return RecordingLog(entries=[])


def a_source(tmp_path: Path, *, refdb: bool = True) -> GitSource:
    """The three git options a commit-built database is created with."""
    return GitSource(
        repo=tmp_path / "repo",
        commit="3ca0a97",
        refdb=(tmp_path / "cache" / "reference.und") if refdb else None,
    )


# --- creating a database from a commit ---------------------------------------------


def test_the_argv_carries_the_repository_the_commit_and_the_reference(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """Measured on Build 1262: this exact order creates a database of 91 files from 3ca0a97."""
    db = db_path(tmp_path)
    source = a_source(tmp_path)

    create_from_commit(cli(stub, log), db, ["Python"], source)

    assert stub.argv == [
        "-quiet",
        "-db",
        str(db),
        "create",
        "-gitrepo",
        str(source.repo),
        "-gitcommit",
        "3ca0a97",
        "-refdb",
        str(source.refdb),
        "-languages",
        "Python",
        "-local",
    ]


def test_a_creation_without_a_reference_database_omits_the_switch(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """``-refdb`` is optional, and an empty database is a legitimate thing to ask for."""
    create_from_commit(
        cli(stub, log), db_path(tmp_path), ["Python"], a_source(tmp_path, refdb=False)
    )

    assert "-refdb" not in stub.argv
    assert "-gitcommit" in stub.argv


def test_every_configured_language_reaches_the_command(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """``-languages`` takes them one after another, as ``create`` already does."""
    create_from_commit(cli(stub, log), db_path(tmp_path), ["Python", "C++"], a_source(tmp_path))

    languages = stub.argv[stub.argv.index("-languages") + 1 :]
    assert languages[:2] == ["Python", "C++"]


def test_a_refused_creation_is_an_analysis_failure_carrying_unds_words(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """A commit that does not exist, a repository that is not one: und says which."""
    stub.plan({"create": {"stderr": "Error: unable to find commit deadbee\n", "rc": 1}})

    with pytest.raises(AnalysisFailedError) as caught:
        create_from_commit(cli(stub, log), db_path(tmp_path), ["Python"], a_source(tmp_path))

    assert "deadbee" in str(caught.value.stderr)


def test_the_creation_is_recorded_in_the_command_log(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """Requirement 12.8: every external command is one line of `--verbose` output."""
    create_from_commit(cli(stub, log), db_path(tmp_path), ["Python"], a_source(tmp_path))

    assert log.codes == [0]


def test_a_reference_database_in_another_directory_is_refused_by_name(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """Measured on Build 1262: und answers this with a *warning* and exit status 1.

    The warning's text names neither the switch nor the requirement, so the refusal is made
    here instead, before a process starts, with both paths in it.
    """
    elsewhere = GitSource(repo=tmp_path / "repo", commit="3ca0a97", refdb=tmp_path / "other.und")

    with pytest.raises(AnalysisFailedError) as caught:
        create_from_commit(cli(stub, log), db_path(tmp_path), ["Python"], elsewhere)

    assert "sibling" in str(caught.value)
    assert stub.calls == [], "no process should start for a pair und would refuse"


# --- telling a database where its repository is ------------------------------------


def test_the_repository_directory_is_written_as_a_setting(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """Measured: ``und -db X settings -GitRepositoryDirectory Y`` exits 0 and ``list`` reads it
    back. The git-derived architectures run ``git log`` in this directory (requirement 4.3)."""
    db = db_path(tmp_path)
    repo = tmp_path / "repo"

    set_git_repository(cli(stub, log), db, repo)

    assert stub.argv == ["-db", str(db), "settings", "-GitRepositoryDirectory", str(repo)]


def test_the_repository_setting_is_never_written_under_quiet(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """``-quiet`` has hidden a refusal from this module before; the whole answer is kept."""
    set_git_repository(cli(stub, log), db_path(tmp_path), tmp_path / "repo")

    assert "-quiet" not in stub.argv


def test_a_refused_setting_is_an_analysis_failure(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """A build that does not know the setting says so, and is not passed over in silence."""
    stub.plan(
        {"settings": {"stderr": "Error: -GitRepositoryDirectory is not recognized\n", "rc": 1}}
    )

    with pytest.raises(AnalysisFailedError):
        set_git_repository(cli(stub, log), db_path(tmp_path), tmp_path / "repo")


def test_a_setting_refused_at_status_zero_is_still_refused(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """``und`` prints ``Error:`` at status 0 for an unrecognised setting (measured on 8.0)."""
    stub.plan({"settings": {"stdout": "Error: -Nonsense is not a recognized setting\n"}})

    with pytest.raises(AnalysisFailedError):
        set_git_repository(cli(stub, log), db_path(tmp_path), tmp_path / "repo")
