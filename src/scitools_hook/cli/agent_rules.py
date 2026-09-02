"""The ``agent-rules`` subcommand: the deterministic rules block an agent can be given.

STUB (task 9.1). Task 9.3 replaces the body and adds ``--write FILE``; ``report/agent_rules``
already renders the block and raises ``ConfigError`` on unusable markers, which the command
must locate by attaching the target path.
"""

from __future__ import annotations

import typer

from scitools_hook.cli import common

HELP = "Print the effective rules as a block a coding agent can follow."

NOT_IMPLEMENTED = "`agent-rules` has no implementation yet (task 9.3)"


def register(app: typer.Typer) -> None:
    """Add ``agent-rules`` to ``app``."""
    app.command(name="agent-rules", help=HELP)(agent_rules)


def agent_rules(ctx: typer.Context) -> None:
    """Print the effective rules for an agent."""
    common.global_options(ctx)
    raise NotImplementedError(NOT_IMPLEMENTED)
