"""The ``explain`` subcommand: describe a change rather than gate it.

STUB (task 9.1): registered with the shared selection, format and output options so the
grammar is fixed and tested. Task 9.2 replaces the body and adds ``--range A..B``,
``--graphs``, ``--impact`` and ``--out DIR``.
"""

from __future__ import annotations

import typer

from scitools_hook.cli import common

HELP = "Explain what a change did to the code, for a reviewer or an agent."

NOT_IMPLEMENTED = "`explain` has no pipeline yet (task 9.2)"


def register(app: typer.Typer) -> None:
    """Add ``explain`` to ``app``."""
    app.command(name="explain", help=HELP)(explain)


def explain(
    ctx: typer.Context,
    staged: common.StagedOption = False,
    worktree: common.WorktreeOption = False,
    all_: common.AllOption = False,
    files: common.FilesOption = None,
    output_format: common.FormatOption = common.OutputFormat.HUMAN,
    output: common.OutputOption = None,
    paths: common.PathsArgument = None,
) -> None:
    """Explain what a change did to the code."""
    options = common.global_options(ctx)
    selection = common.resolve_selection(
        staged=staged,
        worktree=worktree,
        all_=all_,
        files=files,
        paths=paths,
        env=options.env,
    )
    raise NotImplementedError(
        f"{NOT_IMPLEMENTED}; selection={common.describe_selection(selection)}"
    )
