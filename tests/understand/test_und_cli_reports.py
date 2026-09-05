"""``und analyze``'s optional reports: the accuracy figure and the SARIF diagnostics (2.1, 7.1).

Both are 8.0 switches, and both are off unless asked for, because requirement 1.3 says a 6.5
run must be byte-for-byte the run 0.1.0a8 made. So the argv is asserted in both directions:
what an unasked call sends, and what an asked one adds.

The accuracy line is Understand's own, measured on Build 1262::

    Analyze Completed (Errors:0 Warnings:72)
    25 of 92 parsed files had no errors or warnings (27%)

It counts files with **no warning either**, which is why this repository scores 27% while its
analysis has zero errors -- 72 unresolved third-party imports are warnings. The figure is
recorded as the fraction, not the percentage, so a floor can be written as ``0.8``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import SampleDatabases
from und_stub import RecordingLog, UndStub, cli, db_path, understand_env, write_stub

from scitools_hook.models.progress import NullCommandLog
from scitools_hook.understand.und_cli import ALL, UndCli

ACCURACY_OUTPUT = """\
Analyze Completed (Errors:0 Warnings:72)
25 of 92 parsed files had no errors or warnings (27%)
"""
"""``und analyze -all -accuracy`` on Build 1262, verbatim."""

PERFECT_OUTPUT = """\
Analyze Completed (Errors:0 Warnings:0)
92 of 92 parsed files had no errors or warnings (100%)
"""
"""The other end: everything resolved, which must read as 1.0 and not as a missing figure."""

NOTHING_RESOLVED = """\
Analyze Completed (Errors:3 Warnings:0)
0 of 3 parsed files had no errors or warnings (0%)
"""
"""Zero is a figure, and must not be confused with the ``None`` a 6.5 build answers."""

OLD_OUTPUT = "Analyze Completed (Errors:0 Warnings:2)\n"
"""A build that does not know the switch: a summary line and no accuracy line at all."""


@pytest.fixture
def stub(tmp_path: Path) -> UndStub:
    """A stubbed ``und`` executable with an empty plan, ready to be scripted."""
    return write_stub(tmp_path)


@pytest.fixture
def log() -> RecordingLog:
    """A fresh recording command log."""
    return RecordingLog(entries=[])


# --- the argv, in both directions --------------------------------------------------


def test_an_unasked_analysis_sends_the_argv_it_always_sent(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """Requirement 1.3: on 6.5, and on 8.0 with the keys off, nothing changes."""
    stub.plan({"analyze": {"stdout": OLD_OUTPUT}})

    cli(stub, log).analyze(db_path(tmp_path), ALL)

    assert stub.argv[-4:] == ["analyze", "-all", "-errors", "-warnings"]
    assert "-accuracy" not in stub.argv
    assert "-sarif" not in stub.argv


def test_asking_for_the_accuracy_adds_one_switch_and_nothing_else(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    stub.plan({"analyze": {"stdout": ACCURACY_OUTPUT}})

    cli(stub, log).analyze(db_path(tmp_path), ALL, accuracy=True)

    assert "-accuracy" in stub.argv
    assert "-sarif" not in stub.argv


def test_asking_for_the_sarif_names_the_file_after_the_switch(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """``-sarif`` takes the output file as its argument (``und help analyze``)."""
    target = tmp_path / "parselog.sarif"
    stub.plan({"analyze": {"stdout": OLD_OUTPUT}})

    cli(stub, log).analyze(db_path(tmp_path), ALL, sarif=target)

    assert stub.argv[stub.argv.index("-sarif") + 1] == str(target)


def test_both_reports_can_be_asked_for_at_once(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    target = tmp_path / "parselog.sarif"
    stub.plan({"analyze": {"stdout": ACCURACY_OUTPUT, "write_switch": {"-sarif": "{}"}}})

    result = cli(stub, log).analyze(db_path(tmp_path), ALL, accuracy=True, sarif=target)

    assert "-accuracy" in stub.argv
    assert "-sarif" in stub.argv
    assert result.accuracy == pytest.approx(25 / 92)
    assert result.sarif_path == target


def test_the_sarif_path_that_comes_back_is_the_file_and_not_the_switch(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """A build that ignored ``-sarif`` and exited 0 must not report a document (req 2.1).

    The same rule ``doctor``'s probe follows: the answer is the file. Reported as written, a
    document that was never written becomes a companion the run promises and cannot produce,
    or worse a stale one from an earlier pass.
    """
    target = tmp_path / "never-written.sarif"
    stub.plan({"analyze": {}})

    result = cli(stub, log).analyze(db_path(tmp_path), ALL, sarif=target)

    assert "-sarif" in stub.argv
    assert result.sarif_path is None


# --- what comes back ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("output", "expected"),
    [(ACCURACY_OUTPUT, 25 / 92), (PERFECT_OUTPUT, 1.0), (NOTHING_RESOLVED, 0.0)],
    ids=["measured", "everything", "nothing"],
)
def test_the_accuracy_line_is_read_as_a_fraction_of_the_files(
    stub: UndStub, log: RecordingLog, tmp_path: Path, output: str, expected: float
) -> None:
    """The counted files, not the rounded percentage: ``27%`` loses too much to compare."""
    stub.plan({"analyze": {"stdout": output}})

    result = cli(stub, log).analyze(db_path(tmp_path), ALL, accuracy=True)

    assert result.accuracy == pytest.approx(expected)


def test_a_build_that_prints_no_accuracy_line_answers_no_figure(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """6.5 knows no such switch; a missing figure is not a figure of zero (requirement 7.1)."""
    stub.plan({"analyze": {"stdout": OLD_OUTPUT}})

    result = cli(stub, log).analyze(db_path(tmp_path), ALL, accuracy=True)

    assert result.accuracy is None


def test_the_sarif_path_is_recorded_only_when_one_was_asked_for(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    stub.plan({"analyze": {"stdout": OLD_OUTPUT}})

    result = cli(stub, log).analyze(db_path(tmp_path), ALL)

    assert result.sarif_path is None


def test_the_parse_errors_and_warnings_still_come_back_beside_the_new_figures(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """The reports are additive: requirement 2.6's answer is unchanged by asking for them."""
    stub.plan({"analyze": {"stdout": ACCURACY_OUTPUT}})

    result = cli(stub, log).analyze(db_path(tmp_path), ALL, accuracy=True)

    assert result.warnings == 72
    assert result.parse_errors == []


def test_an_empty_file_list_still_starts_no_process_and_reports_nothing(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """Nothing to analyse is nothing to report, whichever switches were asked for."""
    result = cli(stub, log).analyze(db_path(tmp_path), [], accuracy=True)

    assert stub.calls == []
    assert result.accuracy is None
    assert result.sarif_path is None


# --- contract: the real switches against the installed build -----------------------


@pytest.mark.contract
def test_contract_a_real_analysis_reports_an_accuracy_between_zero_and_one(
    sample_databases: SampleDatabases,
) -> None:
    """The transcript above is a transcript; this is the build answering for itself.

    Asserted as a range rather than a value: the figure is a property of the sample project
    and of the parser's opinion of it, and pinning it exactly would make an unrelated change
    to the fixture look like a defect here. What matters is that a figure arrives at all,
    which is what a 6.5 build cannot do.
    """
    wrapper = UndCli(understand_env(sample_databases.und), NullCommandLog())

    result = wrapper.analyze(sample_databases.after_db, ALL, accuracy=True)

    assert result.accuracy is not None, "Build 1262 answers -accuracy; this one did not"
    assert 0.0 <= result.accuracy <= 1.0


@pytest.mark.contract
def test_contract_a_real_analysis_writes_the_sarif_it_was_asked_for(
    sample_databases: SampleDatabases, tmp_path: Path
) -> None:
    """``-sarif`` writes SARIF 2.1.0 naming Understand as the tool that produced it."""
    import json

    target = tmp_path / "parselog.sarif"
    wrapper = UndCli(understand_env(sample_databases.und), NullCommandLog())

    result = wrapper.analyze(sample_databases.after_db, ALL, sarif=target)

    assert result.sarif_path == target
    assert target.exists(), "und analyze -sarif wrote nothing"
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["version"] == "2.1.0"
    assert document["runs"][0]["tool"]["driver"]["name"]
