"""``.pre-commit-hooks.yaml``: the Gate's contract with the pre-commit framework (task 7.3).

This file is read by another program, on another machine, from a checkout of this repository
at a tag -- so it is parsed here rather than grepped, and every field is asserted with the
type the framework will see (``true``, not "something truthy"). Requirements 11.7 and 11.8.

Two of the assertions are about *other* files, and they are the ones most likely to earn
their keep: the entry names a console script that has to exist in ``pyproject.toml``, and the
argument grammar it relies on lives in ``cli/common.py``. Both have already broken once on
this project -- ``check --files a.py b.py c.py`` exited 2 with "Got unexpected extra
argument(s)", which is every commit touching more than one file -- and neither breakage is
visible from this file alone.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

from scitools_hook.cli.common import SelectionMode, resolve_selection

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
DEFINITION = REPOSITORY_ROOT / ".pre-commit-hooks.yaml"
PROJECT = REPOSITORY_ROOT / "pyproject.toml"


@pytest.fixture(scope="module")
def hook() -> dict[str, Any]:
    """The single hook definition, parsed."""
    parsed = yaml.safe_load(DEFINITION.read_text(encoding="utf-8"))
    assert isinstance(parsed, list), "the framework expects a list of hook definitions"
    assert len(parsed) == 1, "one hook is defined here; a second needs its own tests"
    definition = parsed[0]
    assert isinstance(definition, dict)
    return definition


def test_the_definition_lives_where_the_framework_looks_for_it() -> None:
    """The framework reads this exact name from the root of the referenced repository."""
    assert DEFINITION.is_file()


def test_the_hook_is_identified_and_described(hook: dict[str, Any]) -> None:
    """``id`` is what a repository writes in its own configuration, so it is part of the API."""
    assert hook["id"] == "scitools-hook"
    assert hook["name"]
    assert hook["description"]


def test_the_entry_calls_the_console_script_this_package_installs(hook: dict[str, Any]) -> None:
    """The entry is only as good as the script name, which is declared in ``pyproject.toml``.

    Renaming the console script without editing this file would leave a hook definition that
    fails on every user's machine and on none of ours.
    """
    scripts = tomllib.loads(PROJECT.read_text(encoding="utf-8"))["project"]["scripts"]
    command, *arguments = hook["entry"].split()

    assert command in scripts
    assert arguments == ["check"]


def test_the_framework_passes_the_staged_files(hook: dict[str, Any]) -> None:
    """Requirement 11.8: the file list arrives from the framework, not from a second scan."""
    assert hook["pass_filenames"] is True
    assert hook["types"] == ["file"]


def test_the_hook_runs_serially(hook: dict[str, Any]) -> None:
    """Mandatory, not tidy: parallel invocations would each build their own database.

    Asserted with ``is True`` because the framework reads a boolean; a string ``"true"`` is
    truthy in Python and would pass a laxer assertion while meaning something else in YAML.
    """
    assert hook["require_serial"] is True


def test_the_hook_is_installed_as_a_python_package(hook: dict[str, Any]) -> None:
    """``language: python`` is what lets the framework install this package for the user.

    No ``language_version`` pin: requirement 12.2 and the worker fallback let the Gate run
    under whatever Python 3.12 or later the framework provides.
    """
    assert hook["language"] == "python"
    assert "language_version" not in hook


def test_the_entry_plus_appended_paths_selects_exactly_those_files(hook: dict[str, Any]) -> None:
    """The grammar the entry depends on: the framework appends paths as bare arguments.

    ``resolve_selection`` is asked directly rather than through a command body, so this stays
    a test of the argument grammar after task 9.2 fills in what ``check`` then does.
    """
    _, *arguments = hook["entry"].split()
    assert arguments == ["check"], "this test is written for the bare `check` entry"

    choice = resolve_selection(
        staged=False,
        worktree=False,
        all_=False,
        files=None,
        paths=["src/a.py", "src/b.py"],
        env={},
    )

    assert choice.mode is SelectionMode.FILES
    assert choice.files == ("src/a.py", "src/b.py")
