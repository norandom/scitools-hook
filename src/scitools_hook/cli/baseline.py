"""The ``baseline`` subcommand: capture the adaptive baseline the ratchet tightens against.

One run, one question -- what is the worst value of every configured threshold today? --
answered by :class:`~scitools_hook.runner.baseline_cmd.BaselineCmd` over a whole-project
extraction. Two decisions belong to this module.

**``--file`` is passed through exactly as typed, and its absence is passed through as
absence.** Task 8.4's handoff makes the asymmetry explicit and it is deliberate: a
*configured* ``baseline.file`` has to name the same file whether a hook runs it from the
repository root or CI runs it from somewhere else, so the runner resolves it against the
root, whereas a path typed on a command line means what it means in the directory it was
typed in. This command therefore neither resolves nor defaults the value -- passing ``None``
is what asks the runner for the configured location.

**A run that captured nothing says so on standard output.** ``BaselineCmd`` deliberately
writes no file when the repository holds nothing Understand can parse, because a baseline
recording no value at all is indistinguishable from one that was never taken. Reporting the
same "recorded ..." line there would put that indistinguishability back into the operator's
view of the run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Final

import typer

from scitools_hook.cli import common, pipelines
from scitools_hook.runner.baseline_cmd import BaselineCapture

HELP = "Capture the adaptive baseline from the current state of the project."

LONG_HELP = f"""{HELP}

Records WHERE YOU ARE: the worst value this project currently reports for every configured
threshold, so existing debt reports as pre-existing and nothing gets worse. It is written to
a file and the ratchet reads it.

This is not `recommend`, which measures the shape of the repository and says WHERE TO AIM.
A baseline is today's maximum; a recommendation is a ceiling the bulk of the code is already
inside, with the entities outside it left as work to do.
"""
"""The one-line summary, and the paragraph that stops it being read as a recommendation.

The two commands both measure the whole project and both answer with numbers, and using one
believing it is the other fails silently in both directions: a pasted recommendation read as a
baseline reports nothing today and blocks the first commit that touches the tail, while a
baseline read as a recommendation freezes the worst routine in the repository as the limit.
`--help` is where an operator meets them, so it is where the distinction is drawn.
"""

NOTHING_WRITTEN: Final = "no baseline was written: nothing in this repository could be analyzed"
"""What an empty capture reports; the runner has already said why on the diagnostics channel."""

FileOption = Annotated[
    Path | None,
    typer.Option(
        "--file", metavar="PATH", help="Write the baseline here instead of the configured file."
    ),
]


def register(app: typer.Typer) -> None:
    """Add ``baseline`` to ``app``."""
    app.command(name="baseline", help=LONG_HELP)(baseline)


def baseline(ctx: typer.Context, file: FileOption = None) -> None:
    """Capture the adaptive baseline."""
    options = common.global_options(ctx)
    captured = pipelines.assemble(options).baseline().run(file)
    common.emit_findings(describe(captured), None)


def describe(captured: BaselineCapture) -> str:
    """One line saying what was recorded and where (req 8.1).

    The count is read off the captured document rather than off the configured thresholds:
    a threshold this project reports no value for is omitted from the file, and claiming it
    was recorded would overstate what the next run will actually hold the code to.
    """
    if not captured.written:
        return NOTHING_WRITTEN
    limits = len(captured.baseline.values)
    return f"recorded {limits} limit{'' if limits == 1 else 's'} in {captured.path}"
