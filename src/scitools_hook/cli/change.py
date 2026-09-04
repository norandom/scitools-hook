"""Which change a command judges: a selection of files, or a range of commits.

Two commands ask this now. ``explain --range`` describes what happened between two commits;
``check --range`` judges it, which is what a pre-push hook needs -- at push time nothing is
staged and the working tree is beside the point, so the only honest question is what the
commits being pushed did to the code.

**A commit range is a fifth target, not a fifth selection flag.** Requirement 12.3's four
flags are mutually exclusive answers to "which files"; ``--range BASE..HEAD`` answers "which
two commits", which the runner models as a separate plan mode for that reason. A range *and*
a selection flag name two different changes, so that pair is refused rather than resolved by
precedence, exactly as any two selection flags are.

Here rather than in ``cli.common`` because that module is already 39 declared functions
against a limit of 25 -- its own gate says so -- and "which change" is a cohesive question
rather than one more utility.
"""

from __future__ import annotations

from typing import Annotated, Final

import typer

from scitools_hook.cli import common
from scitools_hook.errors import ConfigError
from scitools_hook.models.git import CommitRange
from scitools_hook.runner.pipeline import Selection

RANGE_OPTION: Final = "--range"

TARGET_KEY: Final = f"{RANGE_OPTION}/{common.SELECTION_KEY}"
"""What a range-versus-selection conflict is about, carried on the error for a caller."""

TARGET_HINT: Final = (
    "a range names what happened between two commits and the selection flags name what is "
    "happening now; pass one or the other"
)

RangeOption = Annotated[
    str | None,
    typer.Option(RANGE_OPTION, metavar="A..B", help="Judge what happened between two commits."),
]
"""The option as ``check`` spells it; ``explain`` declares its own help for the same flag."""


def resolve_target(
    range_: str | None, selection: common.SelectionChoice, *, named: bool
) -> Selection | CommitRange:
    """The one change this run covers: a commit range, or a selection (req 9.1, 12.3).

    ``named`` says whether a selection flag was actually given, which the resolved choice
    cannot answer for itself -- it carries a *default* when none was. Without it, every
    ``--range`` run would look like a conflict with the default selection.
    """
    if range_ is None:
        return Selection(mode=selection.mode.value, files=list(selection.files))
    if named:
        raise ConfigError(
            f"{RANGE_OPTION} cannot be combined with {flag_of(selection.mode)}: "
            "they name different changes",
            key=TARGET_KEY,
            hint=TARGET_HINT,
        )
    return CommitRange.parse(range_)


def flag_of(mode: common.SelectionMode) -> str:
    """The option spelling that selects ``mode``.

    Derived rather than looked up: every ``SelectionMode`` value is its flag without the
    dashes, which is a relationship a test asserts against ``common.SELECTION_FLAGS`` rather
    than a coincidence -- and deriving it leaves no branch that no input can reach.
    """
    return f"--{mode.value}"
