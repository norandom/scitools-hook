"""The agent skills this package ships, as data.

Separate from ``tests/cli/test_skills_command.py`` because it is a different question with a
different reach: nothing here starts a command, builds a repository or touches an exit code.
It asks only whether the two documents are inside the installed package and well formed --
read through :mod:`importlib.resources`, not off ``src/``, so a packaging mistake that leaves
them out of the wheel fails here rather than in somebody's install.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scitools_hook import skills

REPO_ROOT = Path(__file__).resolve().parents[2]

INSTALLED_COPIES = (".agents/skills", ".claude/skills")
"""Where this repository keeps the skills it ships: vendor-neutral, and for Claude Code."""


def test_both_skills_are_readable_from_the_installed_package() -> None:
    """The documents are package data, not files that happen to sit beside the source."""
    shipped = skills.shipped()
    assert [skill.name for skill in shipped] == ["scitools-gate", "scitools-improve"]
    for skill in shipped:
        assert skill.text.startswith("---\n"), f"{skill.name} has no front matter"
        assert f"name: {skill.name}\n" in skill.text
        assert "description:" in skill.text
        assert skill.relative_path == f"{skill.name}/SKILL.md"


def test_a_name_this_release_does_not_ship_is_a_key_error() -> None:
    """So a typo cannot be installed as an empty skill."""
    with pytest.raises(KeyError):
        skills.read("scitools-improv")


@pytest.mark.parametrize("directory", INSTALLED_COPIES)
@pytest.mark.parametrize("name", skills.NAMES)
def test_the_checked_in_copies_match_what_is_packaged(directory: str, name: str) -> None:
    """The copies this repository commits are the shipped ones, byte for byte.

    They exist so an agent working *on this project* can load the skills from the place its
    host reads. Nothing else keeps them in step with ``src/scitools_hook/skills``, so a change
    made in one copy and not the others fails here rather than shipping a skill that
    disagrees with the documentation.
    """
    skill = skills.read(name)
    target = REPO_ROOT / directory / skill.relative_path
    assert target.is_file(), f"missing; run: scitools-hook install-skills --dir {directory}"
    assert target.read_text(encoding="utf-8") == skill.text
