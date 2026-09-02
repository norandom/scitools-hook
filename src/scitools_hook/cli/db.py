"""The ``db`` subcommand group: where the analysis database is, and how to refresh it.

STUB (task 9.1). The three operations requirement 2.7/2.8 name are registered as a nested
application so ``db --help`` lists them; task 9.3 replaces the bodies.

The nested application is BUILT PER REGISTRATION rather than held as a module-level
singleton: ``app.build_app()`` is called by tests and could be called again by an embedder,
and a shared instance would let one assembly mutate another's commands and epilogs.
"""

from __future__ import annotations

import typer

from scitools_hook.cli import common

HELP = "Inspect and maintain the Understand database for this repository."

PATH_NOT_IMPLEMENTED = "`db path` has no implementation yet (task 9.3)"
REBUILD_NOT_IMPLEMENTED = "`db rebuild` has no implementation yet (task 9.3)"
ANALYZE_NOT_IMPLEMENTED = "`db analyze` has no implementation yet (task 9.3)"


def build_db_app() -> typer.Typer:
    """A fresh ``db`` application carrying its three operations."""
    db_app = typer.Typer(name="db", help=HELP, no_args_is_help=True, rich_markup_mode=None)
    db_app.command(name="path")(path)
    db_app.command(name="rebuild")(rebuild)
    db_app.command(name="analyze")(analyze)
    return db_app


def register(app: typer.Typer) -> None:
    """Add the ``db`` group to ``app``; it names and describes itself."""
    app.add_typer(build_db_app())


def path(ctx: typer.Context) -> None:
    """Print the path of this repository's analysis database."""
    common.global_options(ctx)
    raise NotImplementedError(PATH_NOT_IMPLEMENTED)


def rebuild(ctx: typer.Context) -> None:
    """Discard the analysis database and build it again from scratch."""
    common.global_options(ctx)
    raise NotImplementedError(REBUILD_NOT_IMPLEMENTED)


def analyze(ctx: typer.Context) -> None:
    """Bring the analysis database up to date with the working tree."""
    common.global_options(ctx)
    raise NotImplementedError(ANALYZE_NOT_IMPLEMENTED)
