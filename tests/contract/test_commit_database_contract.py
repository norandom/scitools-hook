"""A database built from a commit, against the installed build (requirement 3.1).

`-gitcommit` decides where file *contents* are read from and nothing else; `-refdb` decides
which files exist, by copying the reference's settings and file set and rescanning them
against the pinned commit. Both halves are measured here rather than believed, because the
before side of every range check will rest on them from task 4.1 onwards.

The fixture's base commit differs from its head in one file's contents, not in the file set,
so a commit-built database of the base holds the same files as the reference -- which is what
makes the two comparable at all.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from contract_project import (
    FILES,
    SampleProject,
    extract,
    real_env,
    sample_project,  # noqa: F401 -- imported so the session fixture is registered here
)

from scitools_hook.models.progress import NullCommandLog
from scitools_hook.models.snapshot import ProjectSnapshot
from scitools_hook.understand.und_cli import (
    ALL,
    GitSource,
    UndCli,
    create_from_commit,
    set_git_repository,
)

pytestmark = pytest.mark.contract


def wrapper() -> UndCli:
    """The real wrapper over the installed build."""
    return UndCli(real_env("upython"), NullCommandLog())


def listed(project: SampleProject, db: Path) -> list[str]:
    """The files a database holds, named relative to the repository."""
    done = subprocess.run(
        [str(real_env("upython").und), "-db", str(db), "list", "files"],
        capture_output=True,
        text=True,
        timeout=300,
        check=True,
    )
    root = f"{project.repo}/"
    return sorted(line.strip()[len(root) :] for line in done.stdout.splitlines() if root in line)


def test_a_commit_built_database_holds_the_references_file_set(
    sample_project: SampleProject,  # noqa: F811
) -> None:
    """With ``-refdb`` the file set is copied and rescanned against the commit (measured).

    The new database is created **beside** the reference, which Build 1262 requires: in
    another directory it answers a warning about relative paths and exits 1.
    """
    db = sample_project.workdir / "before-file-set.und"
    source = GitSource(
        repo=sample_project.repo,
        commit=sample_project.base_commit,
        refdb=sample_project.db("alpha"),
    )

    create_from_commit(wrapper(), db, ["Python", "C++"], source)

    assert db.exists(), "und create -gitcommit wrote no database"
    assert listed(sample_project, db) == sorted(FILES)


def test_a_commit_built_database_analyses_and_holds_the_commits_contents(
    sample_project: SampleProject,  # noqa: F811
) -> None:
    """The point of the whole route: the *base* commit's text, not the working tree's.

    The fixture's base commit has ``main.py`` without its module docstring, so the file is one
    line shorter there than on disk. Reading that back proves the contents came from the
    commit rather than from the checkout the database was created beside.
    """
    db = sample_project.workdir / "before-contents.und"
    source = GitSource(
        repo=sample_project.repo,
        commit=sample_project.base_commit,
        refdb=sample_project.db("alpha"),
    )
    create_from_commit(wrapper(), db, ["Python", "C++"], source)
    wrapper().analyze(db, ALL)

    at_base = extract(db, sample_project.repo, ("main.py",), side="before")
    at_head = extract(sample_project.db("alpha"), sample_project.repo, ("main.py",))

    assert _code_lines(at_base) < _code_lines(at_head), (
        "the commit-built database read main.py from the checkout, not from the base commit"
    )


def _code_lines(snapshot: ProjectSnapshot) -> float:
    """``CountLineCode`` of the one file the snapshot was asked about."""
    files = [
        record.metrics["CountLineCode"]
        for key, record in snapshot.entities.items()
        if key.scope == "file"
    ]
    assert len(files) == 1, f"expected one file record, got {files}"
    return files[0]


def test_a_database_remembers_the_repository_it_was_told_about(
    sample_project: SampleProject,  # noqa: F811
    tmp_path: Path,
) -> None:
    """What the git-derived architectures read; ``und list settings`` answers it back."""
    db = tmp_path / "plain.und"
    wrapper().create(db, ["Python"])

    set_git_repository(wrapper(), db, sample_project.repo)

    done = subprocess.run(
        [str(real_env("upython").und), "-db", str(db), "list", "settings"],
        capture_output=True,
        text=True,
        timeout=300,
        check=True,
    )
    recorded = [line for line in done.stdout.splitlines() if "GitRepositoryDirectory" in line]
    assert recorded, "the build recorded no GitRepositoryDirectory setting"
    assert str(sample_project.repo) in recorded[0]
