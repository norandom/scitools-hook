"""The ``recommend`` subcommand: what limits fit this repository, and what each would cost.

**Why a command and not a flag on ``config`` or ``init``.** Both were considered and both are
the wrong home, for the same measurable reason: neither of those commands touches Understand.
``config_cmd``'s own docstring states it -- ``init`` writes the shipped template and ``config``
prints what the loader merged, so that ``scitools-hook init``, the command an operator runs
*before* anything else works, cannot fail with "no usable Understand installation found", and
so that ``config`` keeps requirement 12.5's promise of running outside a working tree. A
``--recommend`` flag on either would make one command sometimes need a licence, a database and
a full whole-project analysis and sometimes not, and would put the slowest thing this tool does
behind the flag of its fastest. ``config --detect`` is not a precedent for it: detection reads
the *declarations* in a tracked file list and starts no process at all.

What ``recommend`` actually is, is a measurement of the project -- which makes ``baseline`` its
sibling, not ``config``. The two are registered next to each other and their help lines are
written as a contrasting pair on purpose, because the failure this feature is most likely to
cause is somebody using one believing it is the other.

**It never writes a configuration.** The report goes to standard output, so the whole command
is the ``--print`` path other commands offer as an option; ``--output`` names a file for the
*report*, exactly as ``check --output`` does, and ``--toml`` narrows the report to the lines
an operator would paste. Applying them would require merging into an existing configuration,
and that merge is the judgement this command exists to inform rather than to make.
"""

from __future__ import annotations

from typing import Annotated, Final

import typer

from scitools_hook.cli import common, pipelines
from scitools_hook.report.recommend import render_configuration, render_recommendation_report

HELP = "Measure this repository and propose thresholds that fit it, with the cost of each."

LONG_HELP = f"""{HELP}

Not a baseline. `baseline` records WHERE YOU ARE -- today's worst value per rule, so existing
debt reports as pre-existing. This says WHERE TO AIM: for every ceiling in force, how much of
the repository is already inside it, what each candidate limit would cost in entities
reported, and who the worst offenders are. A limit that already fits is reported `keep`.

Nothing is written to the configuration and nothing is applied. Paste what you agree with.
"""

DEFAULT_TARGET: Final = 0.95
"""The share of a population a limit must contain to be reported as fitting.

Stated here as a literal as well as in ``analysis.recommend``: this is the CLI's declared
default and a test asserts the number an operator sees in ``--help``, so the two cannot drift
apart silently while every relative assertion still passes.
"""

TARGET_HELP = "Share of a scope's entities a limit must contain to fit (0 < share <= 1)."
TOML_HELP = "Print only the configuration lines to paste, without the evidence report."

BAD_TARGET: Final = "--target takes a share above 0 and at most 1, for example 0.95"

TargetOption = Annotated[float, typer.Option("--target", metavar="SHARE", help=TARGET_HELP)]
TomlOption = Annotated[bool, typer.Option("--toml", help=TOML_HELP)]


def register(app: typer.Typer) -> None:
    """Add ``recommend`` to ``app``."""
    app.command(name="recommend", help=LONG_HELP)(recommend)


def recommend(
    ctx: typer.Context,
    target: TargetOption = DEFAULT_TARGET,
    toml: TomlOption = False,
    output: common.OutputOption = None,
) -> None:
    """Measure the repository and price every configured ceiling."""
    options = common.global_options(ctx)
    share = checked_target(target)
    run = pipelines.assemble(options)
    measured = run.recommend().run(share)
    report = (
        render_configuration(measured, share)
        if toml
        else render_recommendation_report(measured, share)
    )
    common.emit_findings(report, output)


def checked_target(target: float) -> float:
    """Refuse a share that is not one, before a database is opened.

    A target of 0 makes every limit fit and a target above 1 makes none of them fit; both
    produce a confident, entirely wrong report rather than an error, which is the silent green
    this project keeps meeting. The refusal is a ``UsageError`` so it exits 2 with the other
    invalid-configuration failures, and it happens before ``assemble`` starts any Understand
    work, so a typo costs no analysis.
    """
    if not 0.0 < target <= 1.0:
        raise typer.BadParameter(BAD_TARGET, param_hint="--target")
    return target
