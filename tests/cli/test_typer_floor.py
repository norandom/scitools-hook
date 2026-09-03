"""The typer version this CLI's control flow rests on, and the floor that guarantees it.

Extracted from ``test_cli_app.py`` when that file crossed ``file.CountLineCode``. The seam is
a real one rather than a size-driven cut: everything here is about the *declared dependency*
-- whether the installed typer has what ``cli/common.py`` imports at module scope, and whether
``pyproject.toml`` says so -- while the file it came from is about the assembled application.

Neither the type check nor the rest of the suite can catch a loosened floor: both run against
whichever single typer is installed, and the failure only appears on a machine that resolves
an older one.
"""

from __future__ import annotations

import re
from pathlib import Path

import typer

from scitools_hook.cli import common

PYPROJECT = Path(common.__file__).resolve().parents[3] / "pyproject.toml"
TYPER_FLOOR = (0, 27, 2)
"""First typer that defines ``TyperException``.

Measured across three releases: 0.12.5 and 0.27.1 do not define it, and
``_CONTROL_FLOW = (typer.Exit, typer.TyperException)`` is evaluated at import -- so on either
of those the CLI raises ``AttributeError`` before any command runs. Neither the type check
nor the test suite can see that, because both run against whatever single version is
installed; only the declared floor can prevent it.
"""


def test_click_exceptions_are_typer_exceptions() -> None:
    """The subclass relation ``_CONTROL_FLOW`` depends on, asserted rather than assumed.

    Typer 0.27 vendors click and makes ``ClickException`` a ``TyperException``, which is what
    lets one tuple entry cover every usage error. A future typer that unvendors click would
    silently stop matching them here, and usage errors raised inside a command body would
    start exiting 70 instead of 2. This fails loudly instead.
    """
    assert isinstance(typer.BadParameter("bad"), typer.TyperException)
    assert issubclass(typer.BadParameter, typer.TyperException)


def test_the_declared_typer_floor_is_the_version_that_has_what_the_cli_imports() -> None:
    """A loosened floor is a CLI that cannot start; nothing else in the gates would notice."""
    requirement = re.search(
        r'"typer>=([0-9]+(?:\.[0-9]+)*)"', PYPROJECT.read_text(encoding="utf-8")
    )
    assert requirement is not None, "no typer requirement found in pyproject.toml"
    declared = tuple(int(part) for part in requirement.group(1).split("."))
    assert declared >= TYPER_FLOOR, f"declared floor {declared} predates TyperException"


def test_the_installed_typer_satisfies_the_declared_floor() -> None:
    installed = tuple(int(part) for part in typer.__version__.split(".")[:3])
    assert installed >= TYPER_FLOOR
