"""``install-skills``: how the shipped agent skills reach a repository.

What the skills *are* is asserted in ``tests/skills/test_packaged_skills.py``; this file is
only about the command. The expected text is read off ``src/`` rather than through
:mod:`scitools_hook.skills`, deliberately: the reader is part of what the command does, and a
test that computes its expectation with the code under test cannot see the two drift apart.

The properties worth asserting are the ones that decide whether an operator ends up with a
working skill or with nothing:

* **A copy the operator edited is refused, not overwritten**, and ``--force`` is what takes
  the shipped version back. Losing an edit on an unrelated "install the tool" run is silent
  data loss, which this project refuses everywhere else.
* **Running it twice writes nothing the second time.** An install step that is not idempotent
  cannot be put in a setup script.
* **The default is resolved against the repository root**, so the command means the same
  thing from any depth of the tree, while a typed ``--dir`` keeps meaning what it means
  where it was typed.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

import pytest
from conftest import MakeGitRepo
from typer.testing import CliRunner
from typer.testing import Result as CliResult

from scitools_hook.cli import app as app_module
from scitools_hook.cli.skills import DEFAULT_DIR, FORCE_OPTION, INSTALLED, REPLACED, UNCHANGED
from scitools_hook.exit_codes import ExitCode

SKILL_SOURCE = Path(__file__).resolve().parents[2] / "src" / "scitools_hook" / "skills"
"""The documents as they sit in the tree: this file's independent oracle."""

SHIPPED = ("scitools-gate", "scitools-improve")


def shipped_text(name: str) -> str:
    """The skill's document, read from the source tree rather than from the package."""
    return (SKILL_SOURCE / name / "SKILL.md").read_text(encoding="utf-8")


def invoke(argv: list[str], *, cwd: Path) -> CliResult:
    """Run one command in ``cwd``, the way the console script would."""
    with contextlib.chdir(cwd):
        return CliRunner().invoke(app_module.app, argv)


# --- installing ------------------------------------------------------------------


def test_it_writes_both_skills_under_the_repository_root(tmp_path: Path) -> None:
    """The default location is vendor-neutral and relative to the root."""
    result = invoke(["install-skills"], cwd=tmp_path)
    assert result.exit_code == ExitCode.OK
    for name in SHIPPED:
        target = tmp_path / DEFAULT_DIR / name / "SKILL.md"
        assert target.read_text(encoding="utf-8") == shipped_text(name)
        assert f"{INSTALLED}: {name}" in result.stdout


def test_it_writes_to_the_root_from_a_subdirectory(git_repo: MakeGitRepo) -> None:
    """A setup step must not depend on which directory it was run from."""
    builder = git_repo("repo")
    nested = builder.path / "src" / "deep"
    nested.mkdir(parents=True)
    assert invoke(["install-skills"], cwd=nested).exit_code == ExitCode.OK
    assert (builder.path / DEFAULT_DIR / "scitools-gate" / "SKILL.md").is_file()
    assert not (nested / DEFAULT_DIR).exists()


def test_dir_is_taken_where_it_was_typed(tmp_path: Path) -> None:
    """``--dir`` is a path an operator typed, so it means what it means in their cwd.

    The asymmetry with the default is the one ``baseline --file`` already draws: a
    *configured* location has to mean the same thing from anywhere, a typed one does not.
    """
    result = invoke(["install-skills", "--dir", ".claude/skills"], cwd=tmp_path)
    assert result.exit_code == ExitCode.OK
    assert (tmp_path / ".claude/skills/scitools-improve/SKILL.md").is_file()


def test_it_runs_outside_a_repository(tmp_path: Path) -> None:
    """Skills are documents; refusing them for want of git would help nobody (req 12.5)."""
    assert invoke(["install-skills"], cwd=tmp_path).exit_code == ExitCode.OK
    assert (tmp_path / DEFAULT_DIR / "scitools-gate" / "SKILL.md").is_file()


def test_running_it_twice_writes_nothing_the_second_time(tmp_path: Path) -> None:
    """Idempotent, and it says so rather than claiming to have installed again."""
    invoke(["install-skills"], cwd=tmp_path)
    target = tmp_path / DEFAULT_DIR / "scitools-gate" / "SKILL.md"
    before = target.stat().st_mtime_ns
    result = invoke(["install-skills"], cwd=tmp_path)
    assert result.exit_code == ExitCode.OK
    assert UNCHANGED in result.stdout
    assert target.stat().st_mtime_ns == before


# --- what it refuses to destroy --------------------------------------------------


def test_an_edited_skill_is_refused_and_left_alone(tmp_path: Path) -> None:
    """An operator's edit survives an unrelated re-run, and the refusal names --force."""
    invoke(["install-skills"], cwd=tmp_path)
    target = tmp_path / DEFAULT_DIR / "scitools-gate" / "SKILL.md"
    edited = target.read_text(encoding="utf-8") + "\n## Local rule\n"
    target.write_text(edited, encoding="utf-8")

    result = invoke(["install-skills"], cwd=tmp_path)

    assert result.exit_code == ExitCode.CONFIG_ERROR
    assert FORCE_OPTION in result.stderr
    assert target.read_text(encoding="utf-8") == edited


def test_force_takes_the_shipped_version_back(tmp_path: Path) -> None:
    """The way out of the refusal, and it reports the replacement as one."""
    invoke(["install-skills"], cwd=tmp_path)
    target = tmp_path / DEFAULT_DIR / "scitools-gate" / "SKILL.md"
    target.write_text("gone\n", encoding="utf-8")

    result = invoke(["install-skills", "--force"], cwd=tmp_path)

    assert result.exit_code == ExitCode.OK
    assert f"{REPLACED}: scitools-gate" in result.stdout
    assert target.read_text(encoding="utf-8") == shipped_text("scitools-gate")


@pytest.mark.parametrize("name", SHIPPED)
def test_a_directory_where_a_skill_goes_is_refused_by_kind(tmp_path: Path, name: str) -> None:
    """Settled before anything is opened, as every other destination in this CLI is."""
    (tmp_path / DEFAULT_DIR / name / "SKILL.md").mkdir(parents=True)
    result = invoke(["install-skills"], cwd=tmp_path)
    assert result.exit_code == ExitCode.REPORT_UNDELIVERABLE
