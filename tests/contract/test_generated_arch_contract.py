"""A git-derived architecture, generated against the installed build (requirement 4.3).

Task 5.1 measured that the Gate's own database cannot produce one: built over an exported
shadow tree, ``Git Stability`` generates and exports **zero** members while ``Directory
Structure`` exports 260 from the same database. The plugin runs ``git log`` and matches its
output to file paths, and a shadow tree's paths are not paths git has heard of.

This is the fallback route measured end to end: a repository-rooted database pinned to a
commit, generated in, exported, and answered as repository-relative members. The comparison
against a shadow-rooted database is asserted in the same module, because "99 members" means
nothing without the "0" beside it -- a build that fixed the shadow case would make this whole
route unnecessary and must not do so silently.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from contract_project import TIMEOUT_S, build_database, real_env, write_tree

from scitools_hook.models.progress import NullCommandLog
from scitools_hook.understand.generated_arch import Generation, generate_for_commit
from scitools_hook.understand.und_arch import DIRECTORY_STRUCTURE
from scitools_hook.understand.und_cli import UndCli, generate_arch, set_git_repository

pytestmark = pytest.mark.contract

STABILITY = "Git Stability"
"""One of the three architectures Build 1262 derives from a repository's history."""

SOURCES = {
    "pkg/core.py": "def add(a, b):\n    return a + b\n",
    "pkg/other.py": "def unused():\n    return 0\n",
    "pkg/deep/inner.py": "def inner():\n    return 1\n",
}
"""Three files in two directories, so a generated architecture has something to group."""


def git(root: Path, *args: str) -> str:
    """Run git in ``root`` with the developer's configuration and hooks kept out of it."""
    done = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
        check=False,
        env={
            **os.environ,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Gate Contract",
            "GIT_AUTHOR_EMAIL": "gate@example.invalid",
            "GIT_COMMITTER_NAME": "Gate Contract",
            "GIT_COMMITTER_EMAIL": "gate@example.invalid",
        },
    )
    assert done.returncode == 0, f"git {' '.join(args)}: {done.stderr.strip()}"
    return done.stdout.strip()


def a_repository(tmp_path: Path) -> tuple[Path, str]:
    """A repository with two commits, so ``git log`` has a history to derive from."""
    root = tmp_path / "repo"
    write_tree(root, SOURCES)
    git(root, "init", "--quiet", "--initial-branch=main")
    git(root, "add", "--all")
    git(root, "commit", "--quiet", "--no-verify", "--message", "first")
    (root / "pkg" / "core.py").write_text(
        "def add(a, b):\n    return a + b\n\n\ndef also(a):\n    return a\n", encoding="utf-8"
    )
    git(root, "add", "--all")
    git(root, "commit", "--quiet", "--no-verify", "--message", "second")
    return root, git(root, "rev-parse", "HEAD")


def a_request(tmp_path: Path, root: Path, commit: str) -> Generation:
    """The generation database goes beside the test's other databases, never in the cache."""
    return Generation(db=tmp_path / "generate.und", repo=root, commit=commit, languages=("Python",))


def test_contract_the_commit_route_generates_a_populated_git_architecture(
    tmp_path: Path,
) -> None:
    """The whole of task 5.2: the fallback route works, and this is the measurement."""
    root, head = a_repository(tmp_path)
    cli = UndCli(real_env("upython"), NullCommandLog())

    generated = generate_for_commit(cli, a_request(tmp_path, root, head), STABILITY)

    members = sorted(generated.paths())
    assert generated.name == STABILITY
    assert members == sorted(SOURCES), f"every project file has to land in a bucket: {members}"


def test_contract_the_members_come_back_relative_to_the_repository(tmp_path: Path) -> None:
    """The declared-architecture step places repository-relative members into a shadow tree."""
    root, head = a_repository(tmp_path)
    cli = UndCli(real_env("upython"), NullCommandLog())

    generated = generate_for_commit(cli, a_request(tmp_path, root, head), STABILITY)

    for member in generated.paths():
        assert not Path(member).is_absolute(), member
        assert (root / member).is_file(), member


def test_contract_a_shadow_rooted_database_generates_nothing_at_all(tmp_path: Path) -> None:
    """Task 5.1's measurement, kept on the record beside the route it forced.

    The shadow stands in for the Gate's own layout: a copy of the repository's files, outside
    the repository, which is exactly what ``ShadowSync`` exports. ``Directory Structure`` is
    asserted on the same database, because a zero that is really "this database is broken"
    would prove nothing.
    """
    root, _ = a_repository(tmp_path)
    shadow = tmp_path / "shadow"
    write_tree(shadow, SOURCES)
    db = tmp_path / "shadow.und"
    build_database(db, shadow, ("python",))
    cli = UndCli(real_env("upython"), NullCommandLog())
    set_git_repository(cli, db, root)

    with pytest.raises(Exception) as caught:
        generate_arch(cli, db, STABILITY)

    assert "no members" in str(caught.value) or "empty" in str(caught.value).lower(), caught.value
    structure = cli.export_arch(db, DIRECTORY_STRUCTURE, tmp_path / "dirs.xml")
    assert sorted(Path(one).name for one in structure.paths()) == sorted(
        Path(one).name for one in SOURCES
    ), "the same database exports the built-in architecture, so it is the git plugin that failed"
