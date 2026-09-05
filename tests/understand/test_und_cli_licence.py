"""The wrapper's licence questions: the digit, the fallback, the options, the refusals.

``und -isundlicensed`` decides whether there is a licence; ``und license`` says which options
it carries and is the fallback verdict on a build without the switch; and any command can
answer with licensing text, which the wrapper must map to ``LicenseError`` (exit 4) rather
than report as an analysis failure. The transcripts are the real ``und``'s: 6.5 build 1204
for the reply code and the built-in refusal texts, 8.0 build 1262 for the option list.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from und_stub import (
    BAD_DB_STDERR,
    CODECHECK_NO_LICENSE,
    LICENSE_OUTPUT,
    NO_COMMAND_OUTPUT,
    NO_LICENSE_OUTPUT,
    RecordingLog,
    UndStub,
    cli,
    db_path,
    write_stub,
)

from scitools_hook.errors import AnalysisFailedError, LicenseError
from scitools_hook.models.understand import LicenseStatus
from scitools_hook.understand.und_cli import API_OPTION, LICENSE_TEXT


@pytest.fixture
def stub(tmp_path: Path) -> UndStub:
    """A stubbed ``und`` executable with an empty plan, ready to be scripted."""
    return write_stub(tmp_path)


@pytest.fixture
def log() -> RecordingLog:
    """A fresh recording command log (requirement 12.8)."""
    return RecordingLog(entries=[])


LICENSE_8_OUTPUT = """\
Reply Code : 9C0A6E2B1D4F7
Reply Date : 2036-09-05

License codes:
  license/code (INI)             : (not set)

Enabled Options

GUI Access
Perpetual License
Export & Share Reports/Metrics
Command Line Access via Und
API Access
VS Code Plugin
Onboard
"""
"""``und license`` on 8.0.1262 after the offline activation of 2026-09-05, but for the code."""

LICENSE_8_OPTIONS = [
    "GUI Access",
    "Perpetual License",
    "Export & Share Reports/Metrics",
    "Command Line Access via Und",
    "API Access",
    "VS Code Plugin",
    "Onboard",
]

WITHOUT_API_OUTPUT = """\
Reply Code : 85DF6F5DAD6BF
Reply Date : 2036-09-05

License codes:
  license/code (INI)             : (not set)

Enabled Options

GUI Access
Perpetual License
Command Line Access via Und
Onboard
"""
"""The same machine an hour earlier: licensed, and not for the API."""


# --- the verdict --------------------------------------------------------------------


def test_license_status_prefers_isundlicensed(stub: UndStub, log: RecordingLog) -> None:
    """``und -isundlicensed`` prints ``1`` with no newline, and that is the verdict.

    ``und license`` runs after it for the option list only: a build that prints none
    leaves the status at ``ok`` with no options, and nothing in its output can reverse a
    ``1``.
    """
    stub.plan({"-isundlicensed": {"stdout": "1"}, "license": {"stdout": "Error: no\n", "rc": 1}})
    status = cli(stub, log).license_status()
    assert status == LicenseStatus(ok=True)
    assert stub.calls == [["-isundlicensed"], ["license"]]


def test_license_status_reads_zero_as_unlicensed(stub: UndStub, log: RecordingLog) -> None:
    stub.plan({"-isundlicensed": {"stdout": "0"}})
    status = cli(stub, log).license_status()
    assert status.ok is False
    assert "0" in status.text


def test_license_status_reads_zero_as_unlicensed_whatever_the_exit_status(
    stub: UndStub, log: RecordingLog
) -> None:
    """8.0 exits 2 beside the 0 (its licensing reference says so); 6.5 exited 0. Same answer.

    Measured on 8.0.1262 before this: the probe fell through to ``und license``, whose 8.0
    output carries no error line, and ``doctor`` printed ``license: ok`` on a machine where
    every ``und analyze`` failed with "No Server Response".
    """
    stub.plan({"-isundlicensed": {"stdout": "0", "rc": 2}})
    status = cli(stub, log).license_status()
    assert status.ok is False
    assert stub.calls == [["-isundlicensed"]]


def test_no_server_response_is_a_licensing_failure(stub: UndStub, log: RecordingLog) -> None:
    """8.0 without a valid offline code asks its licence server; off the network, this is it."""
    assert LICENSE_TEXT.search("No Server Response")
    assert LICENSE_TEXT.search("Your current license is Invalid!")


def test_license_status_falls_back_to_the_license_command(stub: UndStub, log: RecordingLog) -> None:
    stub.plan(
        {
            "-isundlicensed": {"stderr": NO_COMMAND_OUTPUT, "rc": 1},
            "license": {"stdout": LICENSE_OUTPUT},
        }
    )
    status = cli(stub, log).license_status()
    assert status.ok is True
    assert stub.calls == [["-isundlicensed"], ["license"]]


def test_license_status_reports_the_licensing_error_text(stub: UndStub, log: RecordingLog) -> None:
    """Reporting status must answer, never raise: ``doctor`` prints this (requirement 1.5).

    The fallback is forced by making ``-isundlicensed`` fail, because a ``0`` reply is
    answered before ``und license`` is ever reached.
    """
    stub.plan(
        {
            "-isundlicensed": {"stderr": NO_COMMAND_OUTPUT, "rc": 1},
            "license": {"stdout": NO_LICENSE_OUTPUT},
        }
    )
    status = cli(stub, log).license_status()
    assert status.ok is False
    assert "No Und License Found" in status.text
    assert stub.calls == [["-isundlicensed"], ["license"]]


def test_license_status_rejects_an_error_line_from_the_license_command(
    stub: UndStub, log: RecordingLog
) -> None:
    """``und license`` can fail at rc 0 with only an ``Error:`` line to say so.

    Without this the ``_has_error_line`` disjunct is unprotected and an ``und`` that
    cannot answer at all reads as licensed.
    """
    stub.plan(
        {
            "-isundlicensed": {"stderr": NO_COMMAND_OUTPUT, "rc": 1},
            "license": {"stdout": "Error: could not contact the license server\n"},
        }
    )
    status = cli(stub, log).license_status()
    assert status.ok is False
    assert "license server" in status.text


def test_license_status_rejects_a_failing_license_command(stub: UndStub, log: RecordingLog) -> None:
    """A non-zero ``und license`` is not evidence of a license, whatever it printed.

    The output here is a healthy-looking reply code, so ``rc`` is the only thing that
    marks it bad -- which is the point: a run that printed a reply and then died has
    not established a license.
    """
    stub.plan(
        {
            "-isundlicensed": {"stderr": NO_COMMAND_OUTPUT, "rc": 1},
            "license": {"stdout": LICENSE_OUTPUT, "rc": 1},
        }
    )
    status = cli(stub, log).license_status()
    assert status.ok is False
    assert status.text


def test_license_status_answers_zero_without_running_the_license_command(
    stub: UndStub, log: RecordingLog
) -> None:
    """A ``0`` reply is conclusive, so the fallback must not be paid for."""
    stub.plan({"-isundlicensed": {"stdout": "0"}})
    assert cli(stub, log).license_status().ok is False
    assert stub.calls == [["-isundlicensed"]]


def test_license_status_never_passes_quiet(stub: UndStub, log: RecordingLog) -> None:
    """``und -quiet license`` prints nothing, which would read as a healthy license."""
    stub.plan(
        {
            "-isundlicensed": {"stderr": NO_COMMAND_OUTPUT, "rc": 1},
            "license": {"stdout": LICENSE_OUTPUT},
        }
    )
    cli(stub, log).license_status()
    assert stub.calls[-1] == ["license"]


def test_license_status_is_recorded_in_the_command_log(stub: UndStub, log: RecordingLog) -> None:
    stub.plan({"-isundlicensed": {"stdout": "1"}})
    cli(stub, log).license_status()
    assert log.codes == [0, 0]


# --- the options -----------------------------------------------------------------


def test_license_status_reads_the_enabled_options_off_the_license_command(
    stub: UndStub, log: RecordingLog
) -> None:
    """8.0 lists the options under a heading; the status carries them in the order printed."""
    stub.plan({"-isundlicensed": {"stdout": "1"}, "license": {"stdout": LICENSE_8_OUTPUT}})
    status = cli(stub, log).license_status()
    assert status.ok is True
    assert status.options == LICENSE_8_OPTIONS
    assert API_OPTION in status.options


def test_a_licence_without_the_api_option_is_still_a_licence(
    stub: UndStub, log: RecordingLog
) -> None:
    """The wrapper reports; ``doctor`` is the one that says what the missing option costs."""
    stub.plan({"-isundlicensed": {"stdout": "1"}, "license": {"stdout": WITHOUT_API_OUTPUT}})
    status = cli(stub, log).license_status()
    assert status.ok is True
    assert API_OPTION not in status.options
    assert status.options == [
        "GUI Access",
        "Perpetual License",
        "Command Line Access via Und",
        "Onboard",
    ]


def test_a_build_that_lists_no_options_answers_none(stub: UndStub, log: RecordingLog) -> None:
    """6.5 prints a reply code and nothing else: ``[]`` means unknown, and must not mean missing."""
    stub.plan({"-isundlicensed": {"stdout": "1"}, "license": {"stdout": LICENSE_OUTPUT}})
    status = cli(stub, log).license_status()
    assert status == LicenseStatus(ok=True)
    assert status.options == []


def test_the_option_list_stops_at_the_next_heading(stub: UndStub, log: RecordingLog) -> None:
    """A later build that prints another section after the options must not lengthen the list."""
    text = LICENSE_8_OUTPUT + "\nMaintenance:\n2036-09-05\n"
    stub.plan({"-isundlicensed": {"stdout": "1"}, "license": {"stdout": text}})
    assert cli(stub, log).license_status().options == LICENSE_8_OPTIONS


def test_the_fallback_verdict_carries_the_options_too(stub: UndStub, log: RecordingLog) -> None:
    """A build without ``-isundlicensed`` still lists what it enabled, and it is not thrown away."""
    stub.plan(
        {
            "-isundlicensed": {"stderr": NO_COMMAND_OUTPUT, "rc": 1},
            "license": {"stdout": LICENSE_8_OUTPUT},
        }
    )
    status = cli(stub, log).license_status()
    assert status.ok is True
    assert status.options == LICENSE_8_OPTIONS


# --- licensing text on any command -----------------------------------------------


def test_license_text_on_any_command_raises_license_error(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    stub.plan({"analyze": {"stderr": NO_LICENSE_OUTPUT, "rc": 1}})
    with pytest.raises(LicenseError) as caught:
        cli(stub, log).analyze(db_path(tmp_path), None, all=True)
    assert "No Und License Found" in caught.value.und_output


def test_codecheck_without_a_codecheck_license_raises_license_error(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """Measured on the licensed machine: its license excludes CodeCheck."""
    stub.plan({"codecheck": {"stderr": CODECHECK_NO_LICENSE, "rc": 1}})
    with pytest.raises(LicenseError):
        cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], tmp_path / "out")


def test_a_plain_failure_is_not_reported_as_a_license_problem(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    stub.plan({"analyze": {"stderr": BAD_DB_STDERR, "rc": 1}})
    with pytest.raises(AnalysisFailedError):
        cli(stub, log).analyze(db_path(tmp_path), None, all=True)
