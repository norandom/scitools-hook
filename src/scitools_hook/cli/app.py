"""Typer application and console-script entry point."""

from __future__ import annotations

import typer

from scitools_hook import __version__

app = typer.Typer(
    name="scitools-hook",
    help="Maintainability gate backed by SciTools Understand.",
    no_args_is_help=True,
)


@app.callback()
def root() -> None:
    """Maintainability gate backed by SciTools Understand."""


@app.command()
def version() -> None:
    """Print the installed scitools-hook version."""
    typer.echo(__version__)


def main() -> None:
    """Console-script entry point."""
    app()


if __name__ == "__main__":
    main()
