"""The ``init`` and ``config`` subcommands: write a configuration, or show the effective one.

STUB (task 9.1). Task 9.3 replaces both bodies and adds ``init --force``; both commands must
work outside a git repository (req 12.5), which ``RunContext.repo`` already allows for.
"""

from __future__ import annotations

import typer

from scitools_hook.cli import common

INIT_HELP = "Write a configuration file for this repository."
CONFIG_HELP = "Show the effective configuration and where each setting came from."

INIT_NOT_IMPLEMENTED = "`init` has no implementation yet (task 9.3)"
CONFIG_NOT_IMPLEMENTED = "`config` has no implementation yet (task 9.3)"


def register(app: typer.Typer) -> None:
    """Add ``init`` and ``config`` to ``app``."""
    app.command(name="init", help=INIT_HELP)(init)
    app.command(name="config", help=CONFIG_HELP)(config)


def init(ctx: typer.Context) -> None:
    """Write a configuration file for this repository."""
    common.global_options(ctx)
    raise NotImplementedError(INIT_NOT_IMPLEMENTED)


def config(ctx: typer.Context) -> None:
    """Show the effective configuration with the source of every setting."""
    common.global_options(ctx)
    raise NotImplementedError(CONFIG_NOT_IMPLEMENTED)
