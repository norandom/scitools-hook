"""Where the run says Understand's own SARIF documents went (requirements 2.1, 2.4).

Its own module rather than a block in ``test_human``: that file is already 38 functions past
its own ``CountDeclFunction`` limit, and adding a seventh parameter to its shared ``run``
helper was refused by the ratchet -- correctly, since a helper taking every field of a result
is how a test file stops being readable.

The section is a *note*, like the ignored counts and the tightened limits beside it: it says
what the run did with the files, never what it found, so quiet mode drops it and nothing here
can move an exit code.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from scitools_hook.models.findings import RunResult, UnderstandSarif
from scitools_hook.report.human import COMPANION_HEADER, ColorMode, Verbosity, render_human

COMPANIONS: Final = (
    UnderstandSarif(
        kind="analysis",
        source="/cache/understand.understand-analysis.sarif",
        written="/out/gate.understand-analysis.sarif",
    ),
    UnderstandSarif(kind="codecheck", problem="could not be read: no such file"),
)
"""One document written beside the Gate's and one that could not be produced."""


def run(understand_sarif: Sequence[UnderstandSarif] = ()) -> RunResult:
    """A run that found nothing and carries only the companions under test."""
    return RunResult(
        tool_version="0.1.0",
        understand_version="Understand 7.0",
        repo_root="/repo",
        selection="staged",
        started_at="2026-01-01T09:00:00Z",
        seconds=1.5,
        understand_sarif=list(understand_sarif),
    )


def test_the_companion_section_says_where_each_document_went() -> None:
    """Requirement 2.1: the run names the files, because the operator uploads them."""
    text = render_human(run(understand_sarif=COMPANIONS), color=ColorMode.OFF)

    assert COMPANION_HEADER in text
    assert "  analysis  /out/gate.understand-analysis.sarif" in text


def test_a_document_that_could_not_be_produced_says_so_in_the_same_place() -> None:
    """Requirement 2.4: reported, not raised, and beside the ones that worked."""
    text = render_human(run(understand_sarif=COMPANIONS), color=ColorMode.OFF)

    assert "  codecheck  not written: could not be read: no such file" in text


def test_a_document_nothing_asked_for_says_where_it_is_instead() -> None:
    """A run without ``--sarif`` still prepared one; the operator can pass the option."""
    prepared = (UnderstandSarif(kind="analysis", source="/cache/one.sarif"),)

    text = render_human(run(understand_sarif=prepared), color=ColorMode.OFF)

    assert "  analysis  prepared at /cache/one.sarif" in text
    assert "--sarif PATH" in text


def test_a_run_that_produced_no_companions_prints_no_section() -> None:
    """The key ships off, and an untouched repository must not grow an empty block."""
    assert COMPANION_HEADER not in render_human(run(), color=ColorMode.OFF)


def test_quiet_mode_drops_the_companion_section_with_the_other_notes() -> None:
    """Requirement 7.8: quiet is findings and the summary, and this is neither."""
    text = render_human(run(understand_sarif=COMPANIONS), Verbosity.QUIET, ColorMode.OFF)

    assert COMPANION_HEADER not in text
