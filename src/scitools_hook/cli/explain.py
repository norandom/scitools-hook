"""The ``explain`` subcommand: describe a change rather than gate it (requirement 9).

Like ``check``, this module owns the option grammar and nothing else; unlike ``check`` it has
no verdict, so a successful run exits 0 and every failure is a typed error that
:class:`~scitools_hook.cli.common.GateGroup` maps. Four decisions are its own.

**A commit range is a fifth target, not a fifth selection flag.** Requirement 12.3's four
flags are mutually exclusive answers to "which files"; ``--range BASE..HEAD`` answers "which
two commits", which :mod:`scitools_hook.runner.pipeline` models as a separate plan mode for
that reason. It is therefore kept out of ``resolve_selection`` -- which still runs first, so
two selection flags are refused before anything is built -- and combined with it here: a
range *and* a selection flag name two different changes, so that pair is refused rather than
resolved by precedence, exactly as any two selection flags are.

**The range is parsed by :meth:`~scitools_hook.runner.explain.CommitRange.parse`, never
split here.** That parser refuses ``A...B`` by name, because git's symmetric difference
answers a different question from the one requirement 9.1 asks; a second, hand-rolled split
in this module would accept it as ``A..`` plus a head beginning with a dot.

**``--out DIR`` is passed through untouched.** The pipeline classifies the directory --
with :func:`~scitools_hook.paths.classify_directory`, never ``Path.is_dir()``, which answers
``False`` for a directory it merely cannot reach -- and creates it *before* the analysis
plan, so a mistyped path costs a second rather than a full Understand run. Classifying it
here as well would put a second, differently-worded verdict on the same path.

**``--out`` without ``--graphs`` is refused rather than ignored.** The pipeline consults the
directory only when graphs were asked for, so accepting the pair would silently do nothing
with an option the operator explicitly passed -- and ``--out`` sits one letter from
``--output``, which is a genuinely different destination. The refusal says which one to use.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Final

import typer

from scitools_hook.cli import common, pipelines
from scitools_hook.errors import ConfigError
from scitools_hook.report.markdown import Format, render_summary
from scitools_hook.runner.explain import CommitRange, ExplainOptions
from scitools_hook.runner.pipeline import Selection

HELP = "Explain what a change did to the code, for a reviewer or an agent."

RANGE_OPTION: Final = "--range"
OUT_OPTION: Final = "--out"
GRAPHS_OPTION: Final = "--graphs"

TARGET_KEY: Final = f"{RANGE_OPTION}/{common.SELECTION_KEY}"
"""What a range-versus-selection conflict is about, carried on the error for a caller."""

TARGET_HINT: Final = (
    "a range explains what happened between two commits and the selection flags explain "
    "what is happening now; pass one or the other"
)

OUT_KEY: Final = OUT_OPTION
OUT_HINT: Final = (
    f"pass {GRAPHS_OPTION} to export them there, or --output PATH to write the summary "
    "itself to a file"
)
"""``--out`` and ``--output`` are one letter and two destinations apart; say which is which."""


class ExplainFormat(StrEnum):
    """The three views requirement 9.6 asks for, in this command's spelling (req 12.4).

    A subset of :class:`~scitools_hook.cli.common.OutputFormat`: ``sarif`` is a *findings*
    format with one result per violation, and a change summary has no violations, so it is
    left out of the choice list rather than accepted and refused later. ``human`` selects the
    renderer's ``text`` view -- the option keeps ``check``'s spelling so one word means the
    same thing in both commands.
    """

    HUMAN = "human"
    JSON = "json"
    MARKDOWN = "markdown"


VIEWS: Final[dict[ExplainFormat, Format]] = {
    ExplainFormat.HUMAN: "text",
    ExplainFormat.JSON: "json",
    ExplainFormat.MARKDOWN: "markdown",
}
"""``--format`` to the renderer's own vocabulary; the two need not spell them the same."""

FormatOption = Annotated[
    ExplainFormat,
    typer.Option("--format", case_sensitive=False, help="How to render the change summary."),
]
RangeOption = Annotated[
    str | None,
    typer.Option(RANGE_OPTION, metavar="A..B", help="Explain what happened between two commits."),
]
GraphsOption = Annotated[
    bool,
    typer.Option(GRAPHS_OPTION, help="Export callers/callees and depends-on graphs as SVG."),
]
ImpactOption = Annotated[
    bool,
    typer.Option("--impact", help="List what references each changed routine and class."),
]
OutOption = Annotated[
    Path | None,
    typer.Option(OUT_OPTION, metavar="DIR", help="Directory the exported graphs are written into."),
]


def register(app: typer.Typer) -> None:
    """Add ``explain`` to ``app``."""
    app.command(name="explain", help=HELP)(explain)


def explain(
    ctx: typer.Context,
    staged: common.StagedOption = False,
    worktree: common.WorktreeOption = False,
    all_: common.AllOption = False,
    files: common.FilesOption = None,
    range_: RangeOption = None,
    graphs: GraphsOption = False,
    impact: ImpactOption = False,
    out: OutOption = None,
    output_format: FormatOption = ExplainFormat.HUMAN,
    output: common.OutputOption = None,
    paths: common.PathsArgument = None,
) -> None:
    """Explain what a change did to the code."""
    options = common.global_options(ctx)
    selection = common.resolve_selection(
        staged=staged,
        worktree=worktree,
        all_=all_,
        files=files,
        paths=paths,
        env=options.env,
    )
    named = staged or worktree or all_ or bool(files) or bool(paths)
    target = resolve_target(range_, selection, named=named)
    asked = ExplainOptions(graphs=graphs, impact=impact, out_dir=graph_dir(out, graphs=graphs))
    summary = pipelines.assemble(options).explain().run(target, asked)
    common.emit_findings(render_summary(summary, VIEWS[output_format]), output)


def resolve_target(
    range_: str | None, selection: common.SelectionChoice, *, named: bool
) -> Selection | CommitRange:
    """The one change this run describes: a commit range, or a selection (req 9.1, 12.3).

    ``named`` says whether a selection flag was actually given, which the resolved choice
    cannot answer for itself -- it carries a *default* when none was. Without it, every
    ``--range`` run would look like a conflict with the default selection.
    """
    if range_ is None:
        return Selection(mode=selection.mode.value, files=list(selection.files))
    if named:
        raise ConfigError(
            f"{RANGE_OPTION} cannot be combined with {flag_of(selection.mode)}: "
            "they name different changes",
            key=TARGET_KEY,
            hint=TARGET_HINT,
        )
    return CommitRange.parse(range_)


def flag_of(mode: common.SelectionMode) -> str:
    """The option spelling that selects ``mode``.

    Derived rather than looked up: every ``SelectionMode`` value is its flag without the
    dashes, which is a relationship a test asserts against ``common.SELECTION_FLAGS`` rather
    than a coincidence -- and deriving it leaves no branch that no input can reach.
    """
    return f"--{mode.value}"


def graph_dir(out: Path | None, *, graphs: bool) -> Path | None:
    """The graph destination, or the refusal that says it would have been ignored (req 9.4)."""
    if out is not None and not graphs:
        raise ConfigError(
            f"{OUT_OPTION} names where exported graphs go, but {GRAPHS_OPTION} was not given",
            key=OUT_KEY,
            hint=OUT_HINT,
        )
    return out
