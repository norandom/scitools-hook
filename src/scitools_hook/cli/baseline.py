"""The ``baseline`` subcommand: capture the adaptive baseline the ratchet tightens against.

STUB (task 9.1). Task 9.2 replaces the body and adds ``--file``.
"""

from __future__ import annotations

import typer

from scitools_hook.cli import common

HELP = "Capture the adaptive baseline from the current state of the project."

NOT_IMPLEMENTED = "`baseline` has no pipeline yet (task 9.2)"


def register(app: typer.Typer) -> None:
    """Add ``baseline`` to ``app``."""
    app.command(name="baseline", help=HELP)(baseline)


def baseline(ctx: typer.Context) -> None:
    """Capture the adaptive baseline."""
    common.global_options(ctx)
    raise NotImplementedError(NOT_IMPLEMENTED)
