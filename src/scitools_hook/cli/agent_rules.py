"""The ``agent-rules`` subcommand: the deterministic rules block an agent can be given.

``report.agent_rules`` renders the block and inserts it between its markers; this module
decides *which* configuration it renders and where the result goes. Three decisions:

* **The limits printed are the ones a run would enforce, baseline included.** The snippet
  exists so an agent can meet the gate without being told to run it, so it is built from
  ``analysis.baseline.apply`` exactly as ``CheckPipeline`` builds its own effective
  thresholds -- including that the baseline is applied whether or not ``baseline.adaptive``
  is on, because that is what the pipeline does. An agent shown the configured limit while
  the run enforces a narrower one would chase the wrong number.
* **It needs no repository and no Understand.** The rules come from configuration, which
  merges with or without a repository-level file (req 12.5's reading: a subcommand stops for
  want of git only when it needs git). A missing installation must not stand between an agent
  and the rules it is being asked to follow.
* **``--write`` reads before it writes, so the read is guarded too.** The block is inserted
  into a file the operator owns, which means the file is read first -- and a FIFO at that
  path blocks forever on the read just as surely as on the write, so the kind is settled by
  ``paths.classify_file`` before anything is opened. The refusal carries
  ``ReportUndeliverableError`` rather than a configuration error so that one physical cause
  gets one answer: that is the code ``common.emit_findings`` already gives for the same path
  when the write is the half that meets it.

With ``--write`` standard output stays empty -- the file is the answer, exactly as
``--output`` works for ``check`` -- and the confirmation goes to standard error.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Final

import typer

from scitools_hook.analysis import baseline as baseline_rules
from scitools_hook.cli import common
from scitools_hook.cli.config_cmd import effective_configuration
from scitools_hook.config.models import Settings
from scitools_hook.errors import ConfigError, ReportUndeliverableError
from scitools_hook.models.findings import EffectiveThreshold
from scitools_hook.paths import classify_file
from scitools_hook.report.agent_rules import insert_between_markers, render_rules
from scitools_hook.runner.baseline_store import BaselineStore, baseline_path
from scitools_hook.runner.context import find_repository

HELP = "Print the effective rules as a block a coding agent can follow."

WRITE_HELP = "Insert the block into this file between the scitools-hook markers."
WRITE_OPTION: Final = "--write"

WROTE: Final = "wrote the rules block into"

UNUSABLE_HINT: Final = "Name a regular file the block can be inserted into."


def register(app: typer.Typer) -> None:
    """Add ``agent-rules`` to ``app``."""
    app.command(name="agent-rules", help=HELP)(agent_rules)


def agent_rules(
    ctx: typer.Context,
    write: Annotated[
        Path | None, typer.Option(WRITE_OPTION, metavar="FILE", help=WRITE_HELP)
    ] = None,
) -> None:
    """Print the effective rules for an agent, or insert them into a file (req 10.1, 10.3)."""
    options = common.global_options(ctx)
    repo = find_repository(options.cwd, options.command_log())
    settings, _ = effective_configuration(options, repo)
    root = None if repo is None else repo.root
    snippet = render_rules(settings, _effective_thresholds(settings, root))
    if write is None:
        common.emit_findings(snippet, None)
        return
    common.emit_findings(_merged(write, snippet), write, option=WRITE_OPTION)
    common.echo_err(f"{WROTE} {write}")


def _effective_thresholds(settings: Settings, root: Path | None) -> list[EffectiveThreshold]:
    """The limits a run would enforce: configured, narrowed by the baseline (req 8.2, 8.5).

    Every problem reading the baseline is reported on the diagnostics channel and stepped
    over (req 8.6), which is what the check pipeline does with the same two lists: a baseline
    the Gate cannot use must not stop an agent from being told the configured rules.
    """
    specs = list(settings.thresholds)
    stored, unreadable = BaselineStore(baseline_path(settings, root)).load(specs)
    effective, issues = baseline_rules.apply(specs, stored)
    for issue in (*unreadable, *issues):
        common.echo_err(issue.message if issue.key is None else f"{issue.key}: {issue.message}")
    return effective


def _merged(target: Path, snippet: str) -> str:
    """``target``'s content with the block inserted, or the refusal that says why not."""
    try:
        return insert_between_markers(_existing(target), snippet)
    except ConfigError as unusable:
        # `insert_between_markers` is given a string, so it cannot know which file the markers
        # came from; requirement 3.8's "name the file" is attached here. (Precedent:
        # `config.loader.attach_source` does the same for a key rejected without a file.)
        raise ConfigError(
            unusable.message, file=target, key=WRITE_OPTION, hint=unusable.hint
        ) from unusable


def _existing(target: Path) -> str:
    """What is in ``target`` now, or ``""`` when it is not there yet.

    A name that is taken by anything other than a regular file is refused rather than read:
    a FIFO blocks in ``read_text`` with no writer, a directory raises where the message would
    name the wrong problem, and a dangling symlink would have the block written wherever the
    link points -- possibly outside the repository (req 2.2).
    """
    verdict = classify_file(target)
    if verdict.absent:
        return ""
    if not verdict.usable:
        raise ReportUndeliverableError(
            f"cannot write to {target}: it {verdict.reason}",
            key=WRITE_OPTION,
            hint=UNUSABLE_HINT,
        )
    try:
        return target.read_text(encoding="utf-8")
    except (OSError, ValueError) as unreadable:
        # ValueError covers a file that is not UTF-8 text: inserting into it would have to
        # choose an error policy for bytes the operator wrote, and every choice is a silent
        # edit of a file the Gate does not own.
        raise ReportUndeliverableError(
            f"cannot read {target}: {unreadable}", key=WRITE_OPTION, hint=UNUSABLE_HINT
        ) from unreadable
