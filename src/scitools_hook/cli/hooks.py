"""The ``install-hook`` and ``uninstall-hook`` subcommands (req 11.1, 11.2, 11.6, 11.9).

STUB (task 9.1). Task 9.3 replaces both bodies and adds ``install-hook --force --global``.
"""

from __future__ import annotations

import typer

from scitools_hook.cli import common

INSTALL_HELP = "Install the pre-commit shim into this repository's hooks directory."
UNINSTALL_HELP = "Remove the pre-commit shim and restore whatever it replaced."

INSTALL_NOT_IMPLEMENTED = "`install-hook` has no implementation yet (task 9.3)"
UNINSTALL_NOT_IMPLEMENTED = "`uninstall-hook` has no implementation yet (task 9.3)"


def register(app: typer.Typer) -> None:
    """Add ``install-hook`` and ``uninstall-hook`` to ``app``."""
    app.command(name="install-hook", help=INSTALL_HELP)(install_hook)
    app.command(name="uninstall-hook", help=UNINSTALL_HELP)(uninstall_hook)


def install_hook(ctx: typer.Context) -> None:
    """Install the pre-commit shim."""
    common.global_options(ctx)
    raise NotImplementedError(INSTALL_NOT_IMPLEMENTED)


def uninstall_hook(ctx: typer.Context) -> None:
    """Remove the pre-commit shim."""
    common.global_options(ctx)
    raise NotImplementedError(UNINSTALL_NOT_IMPLEMENTED)
