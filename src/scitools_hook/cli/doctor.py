"""The ``doctor`` subcommand: report the environment the Gate found (req 1.5).

STUB (task 9.1). Task 9.3 replaces the body; ``doctor`` must work outside a git repository
(req 12.5), and ``runner/doctor.py`` already produces the report it will render.
"""

from __future__ import annotations

import typer

from scitools_hook.cli import common

HELP = "Report the Understand installation, licence, repository and configuration in use."

NOT_IMPLEMENTED = "`doctor` has no implementation yet (task 9.3)"


def register(app: typer.Typer) -> None:
    """Add ``doctor`` to ``app``."""
    app.command(name="doctor", help=HELP)(doctor)


def doctor(ctx: typer.Context) -> None:
    """Diagnose the environment this run would use."""
    common.global_options(ctx)
    raise NotImplementedError(NOT_IMPLEMENTED)
