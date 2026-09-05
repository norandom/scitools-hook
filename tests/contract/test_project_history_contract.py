"""The contract project is a git repository, not just a directory (requirements 3.2, 4.3).

Three things this specification adds are about a *repository*: a before database built from a
commit, a git-derived architecture generated from commit dates and authors, and the
comparison pair those two register with each other. None of them can be measured against a
directory of files, so the fixture writes two commits and leaves the working tree at the
second -- exactly the sources every contract test written before this expects to find.

These tests are the fixture's own contract. They are cheap, and they fail loudly rather than
letting a later task discover that its repository has no history.
"""

from __future__ import annotations

import subprocess

import pytest
from contract_project import (
    FILES,
    SampleProject,
    sample_project,  # noqa: F401 -- imported so the session fixture is registered here
)

pytestmark = pytest.mark.contract


def git(project: SampleProject, *args: str) -> str:
    """Read something back out of the fixture's repository."""
    done = subprocess.run(
        ["git", "-C", str(project.repo), *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return done.stdout.strip()


def test_the_analysis_root_is_a_repository_with_two_commits(
    sample_project: SampleProject,  # noqa: F811
) -> None:
    """One commit would be a history with nothing before it, which is not a before side."""
    assert (sample_project.repo / ".git").is_dir()
    assert git(sample_project, "rev-list", "--count", "HEAD") == "2"


def test_the_base_commit_is_the_first_of_the_two_and_is_not_head(
    sample_project: SampleProject,  # noqa: F811
) -> None:
    """What a range check would compare against, and what `-gitcommit` would be pinned to."""
    head = git(sample_project, "rev-parse", "HEAD")

    assert sample_project.base_commit == git(sample_project, "rev-parse", "HEAD~1")
    assert sample_project.base_commit != head
    assert sample_project.history.head == head


def test_the_working_tree_holds_the_sources_every_other_contract_test_expects(
    sample_project: SampleProject,  # noqa: F811
) -> None:
    """The history is additive: it changed what came *before*, never what is on disk now."""
    assert git(sample_project, "status", "--porcelain") == ""
    tracked = sorted(git(sample_project, "ls-files").splitlines())

    assert tracked == sorted(FILES)


def test_the_base_commit_holds_something_else_than_head_does(
    sample_project: SampleProject,  # noqa: F811
) -> None:
    """A before side that equals the after side would make every ratchet test vacuous."""
    changed = git(sample_project, "diff", "--name-only", f"{sample_project.base_commit}..HEAD")

    assert changed.splitlines() == ["main.py"]


def test_the_repository_carries_the_dates_a_git_architecture_reads(
    sample_project: SampleProject,  # noqa: F811
) -> None:
    """`Git Stability` and `Git Owner` run `git log` in the project's directory (req 4.3)."""
    log = git(sample_project, "log", "--format=%H %ae %ad", "--date=iso")

    assert len(log.splitlines()) == 2
    assert all("gate@example.invalid" in line for line in log.splitlines())
