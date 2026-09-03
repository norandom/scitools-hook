"""The typer application: global options, subcommand registration, and the entry point.

This module assembles; it decides almost nothing. The rules every command obeys live in
:mod:`scitools_hook.cli.common` and reach the commands structurally rather than by
convention:

* the application is built with :class:`~scitools_hook.cli.common.GateGroup`, so every
  subcommand -- including the ones tasks 9.2 and 9.3 fill in, and the ones nested under
  ``db`` -- maps a failure to its documented exit code without a decorator to forget;
* :func:`~scitools_hook.cli.common.document_help` puts the exit-code table -- and the note
  saying global options come before the subcommand -- in every command's ``--help``
  (req 12.1) once, after registration, rather than on each command;
* the root callback publishes a :class:`~scitools_hook.cli.common.GlobalOptions` on the
  context, which is the only thing a command needs in order to build its run.

``rich_markup_mode`` is off deliberately. Findings are rendered with their own escapes and a
fixed-width layout, and a rich console would re-wrap them and read ``[...]`` inside a message
as markup; help text is kept in the same plain regime so there is one rendering story, not
two. Shell-completion installation is off because it writes to the user's shell profile,
which is not something a gate run from a hook should ever do.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer

from scitools_hook import __version__
from scitools_hook.cli import agent_rules, baseline, check, common, config_cmd, db, doctor, hooks
from scitools_hook.cli import explain as explain_cmd
from scitools_hook.cli import recommend as recommend_cmd
from scitools_hook.cli import skills as skills_cmd

HELP = """Maintainability gate backed by SciTools Understand.

Runs the same way locally, in a git hook, in CI and when driven by an agent: findings go to
standard output, diagnostics to standard error, and nothing ever prompts. Global options
belong before the subcommand: scitools-hook --verbose check --staged.
"""

REGISTRARS = (
    check.register,
    explain_cmd.register,
    baseline.register,
    recommend_cmd.register,
    config_cmd.register,
    doctor.register,
    db.register,
    hooks.register,
    agent_rules.register,
    skills_cmd.register,
)
"""One registrar per module; requirement 12.1's ten subcommands come out of these ten.

``recommend`` is registered directly after ``baseline`` and that ordering is deliberate:
``--help`` lists commands in registration order, so the two measurements of a repository
sit next to each other and their contrasting one-line help is read as a pair.

``install-skills`` is registered last, beside ``agent-rules``: both hand something to an
agent rather than measuring anything, and both are steps in enabling a repository rather
than in running the gate.
"""


def version_callback(value: bool) -> None:
    """Print the installed version and stop, before any option is acted on.

    The version is this invocation's *answer*, so it goes to standard output -- through
    ``common.emit_findings``, which is therefore the only place this project writes standard
    output, with no exception to remember (click writes ``--help`` itself, which is not ours).
    A subcommand must never add a second writer: everything a command has to say that is not
    its answer goes to stderr through ``common.echo_err``.

    Being an *eager* callback, this runs inside ``parse_args``, before ``GateGroup.invoke``
    exists to catch anything -- so its safety comes from ``GateGroup.make_context``, which
    wraps parsing for exactly this reason, and not from being a command.
    """
    if value:
        common.emit_findings(__version__, None)
        raise typer.Exit(code=0)


def build_app() -> typer.Typer:
    """Assemble the application: root options, every subcommand, and the help epilogs."""
    app = typer.Typer(
        cls=common.GateGroup,
        name="scitools-hook",
        help=HELP,
        epilog=common.HELP_EPILOG,
        no_args_is_help=True,
        add_completion=False,
        rich_markup_mode=None,
        pretty_exceptions_enable=False,
    )
    app.callback()(root)
    for register in REGISTRARS:
        register(app)
    common.document_help(app)
    return app


def root(
    ctx: typer.Context,
    scitools_home: Annotated[
        Path | None,
        typer.Option("--scitools-home", metavar="DIR", help="Understand installation to use."),
    ] = None,
    config: Annotated[
        Path | None,
        typer.Option("--config", metavar="PATH", help="Configuration file to read instead."),
    ] = None,
    api_mode: Annotated[
        common.ApiMode | None,
        typer.Option("--api-mode", case_sensitive=False, help="How to reach Understand's API."),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option(common.VERBOSE_FLAG, help="Print external commands, timings and tracebacks."),
    ] = False,
    color: Annotated[
        bool | None,
        typer.Option("--color/--no-color", help="Force colour on or off, whatever stdout is."),
    ] = None,
    quiet: Annotated[
        bool, typer.Option("--quiet", help="Print only the summary and blocking findings.")
    ] = False,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=version_callback,
            is_eager=True,
            help="Print the installed version and exit.",
        ),
    ] = False,
) -> None:
    """Maintainability gate backed by SciTools Understand."""
    ctx.obj = common.GlobalOptions(
        cwd=Path.cwd(),
        env=dict(os.environ),
        scitools_home=scitools_home,
        config=config,
        api_mode=api_mode,
        verbose=verbose,
        color=color,
        quiet=quiet,
    )


app = build_app()
"""The application the console script and ``python -m scitools_hook.cli.app`` both run."""


def main() -> None:
    """Console-script entry point."""
    app()


if __name__ == "__main__":
    main()
