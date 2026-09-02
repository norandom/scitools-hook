"""The ``check`` subcommand: gate a change against the thresholds, ratchet and rules.

STUB (task 9.1). The command is registered with the shared selection, format and output
options so the option grammar, the exit codes and the stream discipline are settled and
tested before any pipeline exists. Task 9.2 replaces the body and adds ``--strict``,
``--adaptive/--no-adaptive``, ``--show-highest`` and ``--sarif PATH``; it should keep the
first two statements, which are what make requirement 12.3's refusal happen before any work.
"""

from __future__ import annotations

import typer

from scitools_hook.cli import common

HELP = "Check a change against the maintainability rules."

NOT_IMPLEMENTED = "`check` has no pipeline yet (task 9.2)"


def register(app: typer.Typer) -> None:
    """Add ``check`` to ``app``."""
    app.command(name="check", help=HELP)(check)


def check(
    ctx: typer.Context,
    staged: common.StagedOption = False,
    worktree: common.WorktreeOption = False,
    all_: common.AllOption = False,
    files: common.FilesOption = None,
    output_format: common.FormatOption = common.OutputFormat.HUMAN,
    output: common.OutputOption = None,
    paths: common.PathsArgument = None,
) -> None:
    """Check a change against the maintainability rules."""
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
