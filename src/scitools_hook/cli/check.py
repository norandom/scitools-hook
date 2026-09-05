"""The ``check`` subcommand: gate a change against the thresholds, ratchet and rules.

The command owns the option grammar and nothing else. What to analyse is decided by
:func:`~scitools_hook.cli.common.resolve_selection`, how to reach Understand by
:func:`~scitools_hook.cli.pipelines.assemble`, what the answer is by
:class:`~scitools_hook.runner.check.CheckPipeline`, and how it reads by the renderers in
:mod:`scitools_hook.report`. Four decisions are this module's own.

**``resolve_selection`` stays the first thing the body does** (task 9.1's seam). Two
selection flags name different sets of files, so the refusal must happen before a repository
is discovered, a database is opened or a single ``und`` process is started -- that is what
makes ``check --staged --all`` exit 2 having printed nothing on standard output.

**``--strict``, ``--adaptive/--no-adaptive`` and ``--show-highest`` are settings, not pipeline
arguments** (task 8.3's handoff). The pipeline reads ``ratchet.strict``, ``baseline.adaptive``
and ``output.show_highest`` off ``ctx.settings``, so the flags travel as dotted overrides
through ``ContextOptions.cli_overrides``, above every configuration file (req 3.2). An
*absent* flag pushes nothing: ``--adaptive/--no-adaptive`` is a three-valued option
(``True``/``False``/unset) precisely so that not passing it leaves the configuration alone,
and ``--strict`` has no negative spelling -- the design's option list gives it none -- so it
can turn strict mode on but never off.

**``--sarif PATH`` is a second destination, not a second format.** It writes SARIF beside
whatever ``--format`` renders, which is the shape CI wants: a human or JSON report on the
console *and* a file for the code-scanning upload. The two writes go through the same
:func:`~scitools_hook.cli.common.emit_findings` guard -- so a named pipe is refused rather
than hung on, an existing report is replaced atomically, and a failure exits 7 -- and each
names the option the operator actually passed, because reporting ``--output`` for a path
given to ``--sarif`` is the defect task 9.1 fixed one option over. The primary report is
delivered *first*, so a run whose SARIF file cannot be written still shows its findings.
``--sarif /dev/null`` stores nothing and is left alone: it is a legitimate discard, and
nothing at the filesystem level distinguishes a discard from a mistake.

**Understand's own SARIF is placed before either write.** The companions are copies of
files the run already produced (requirement 2.1); putting them first is what lets the
primary report name each one, and it keeps that report ahead of the Gate's own SARIF, so a
destination that cannot be written still shows its findings.

**The exit code is the verdict, and only the verdict.** ``blocking_count > 0`` is exit 1
(req 7.9); everything else is an error raising its own typed exception, which
:class:`~scitools_hook.cli.common.GateGroup` maps. There is deliberately no ``try``/``except``
in this module: a handler here would be a second, drifting copy of the mapping.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Final

import typer

from scitools_hook.cli import change, common, pipelines
from scitools_hook.config.models import Settings
from scitools_hook.exit_codes import ExitCode
from scitools_hook.models.findings import RunResult
from scitools_hook.report.human import render_human
from scitools_hook.report.json_out import render_json
from scitools_hook.report.sarif import render_sarif
from scitools_hook.runner.companions import write_beside

HELP = "Check a change against the maintainability rules."

STRICT_KEY: Final = "ratchet.strict"
ADAPTIVE_KEY: Final = "baseline.adaptive"
SHOW_HIGHEST_KEY: Final = "output.show_highest"
"""The dotted settings keys the three mode flags override (req 4.7, 8.2, 5.6)."""

SARIF_OPTION: Final = "--sarif"
"""The spelling a failed SARIF delivery names, so it points at the option that was given."""


class CheckFormat(StrEnum):
    """The renderings ``check`` has (req 12.4, "where applicable").

    A subset of :class:`~scitools_hook.cli.common.OutputFormat` on purpose: ``markdown``
    renders a *change summary*, which is ``explain``'s answer and not this command's, so it
    is left out of the choice list rather than accepted and refused later. Click then rejects
    it as a usage error naming the three that work, and ``--help`` says which those are.
    """

    HUMAN = "human"
    JSON = "json"
    SARIF = "sarif"


FormatOption = Annotated[
    CheckFormat,
    typer.Option("--format", case_sensitive=False, help="How to render the findings."),
]
SarifOption = Annotated[
    Path | None,
    typer.Option(SARIF_OPTION, metavar="PATH", help="Also write the findings here as SARIF 2.1.0."),
]
StrictOption = Annotated[
    bool,
    typer.Option("--strict", help="Count pre-existing violations in affected code as blocking."),
]
AdaptiveOption = Annotated[
    bool | None,
    typer.Option(
        "--adaptive/--no-adaptive",
        help="Apply the recorded baseline as the effective limit; unset leaves configuration.",
    ),
]
ShowHighestOption = Annotated[
    bool,
    typer.Option("--show-highest", help="Report the highest value found per metric."),
]


def register(app: typer.Typer) -> None:
    """Add ``check`` to ``app``."""
    app.command(name="check", help=HELP)(check)


def check(
    ctx: typer.Context,
    staged: common.StagedOption = False,
    worktree: common.WorktreeOption = False,
    all_: common.AllOption = False,
    files: common.FilesOption = None,
    range_: change.RangeOption = None,
    output_format: FormatOption = CheckFormat.HUMAN,
    output: common.OutputOption = None,
    sarif: SarifOption = None,
    strict: StrictOption = False,
    adaptive: AdaptiveOption = None,
    show_highest: ShowHighestOption = False,
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
    named = any((staged, worktree, all_, files, paths))
    target = change.resolve_target(range_, selection, named=named)
    run = pipelines.assemble(
        options, overrides(strict=strict, adaptive=adaptive, show_highest=show_highest)
    )
    result = run.check().run(target)
    if sarif is not None:
        result = write_beside(result, sarif)
    common.emit_findings(render(result, output_format, run.ctx.settings, options, output), output)
    if sarif is not None:
        common.emit_findings(render_sarif(result, result.tool_version), sarif, option=SARIF_OPTION)
    raise typer.Exit(code=int(verdict(result)))


def overrides(*, strict: bool, adaptive: bool | None, show_highest: bool) -> dict[str, object]:
    """The settings this command line sets, as the loader's dotted keys (req 3.2).

    A flag that was not given contributes no key at all, rather than a ``None`` the loader
    would drop: the difference is invisible to the loader and visible to a reader, and this
    is the map a test asserts against.
    """
    chosen: dict[str, object] = {}
    if strict:
        chosen[STRICT_KEY] = True
    if adaptive is not None:
        chosen[ADAPTIVE_KEY] = adaptive
    if show_highest:
        chosen[SHOW_HIGHEST_KEY] = True
    return chosen


def render(
    result: RunResult,
    output_format: CheckFormat,
    settings: Settings,
    options: common.GlobalOptions,
    output: Path | None,
) -> str:
    """One run, rendered the way ``--format`` asked for (req 7.3, 7.4, 7.5).

    Colour is decided from the *destination*: a file is never a terminal, so ``--output
    report.txt`` gets no escapes even from an interactive session (task 9.1's ``color_for``).
    ``show_highest`` comes from the settings rather than from the flag, so requirement 5.6
    can also be answered by a configuration file.
    """
    if output_format is CheckFormat.JSON:
        return render_json(result)
    if output_format is CheckFormat.SARIF:
        return render_sarif(result, result.tool_version)
    return render_human(
        result,
        options.verbosity,
        options.color_for(output),
        True,
        settings.output.show_highest,
    )


def verdict(result: RunResult) -> ExitCode:
    """Requirement 7.9: blocking findings, and nothing else, make a run fail."""
    return ExitCode.VIOLATIONS if result.blocking_count > 0 else ExitCode.OK
