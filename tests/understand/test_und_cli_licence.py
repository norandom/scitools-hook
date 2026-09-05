"""The wrapper's licence questions: the digit, and the refusals any command can answer with.

``und -isundlicensed`` is the one licence switch the tool runs: it prints ``1`` or ``0`` and
the digit decides, whatever the exit status. Nothing else is asked -- on 8.0 the "read-only"
licence commands rewrote the licence file (2026-09-05), and licensing is the user's -- so an
answer that is neither digit is reported as not established, with und's words, and that is
the early error. Separately, any command can answer with licensing text, which the wrapper
maps to ``LicenseError`` (exit 4) rather than reporting an analysis failure.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from und_stub import (
    BAD_DB_STDERR,
    CODECHECK_NO_LICENSE,
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
from scitools_hook.understand.und_cli import (
    ALL,
    LICENSE_TEXT,
)


@pytest.fixture
def stub(tmp_path: Path) -> UndStub:
    """A stubbed ``und`` executable with an empty plan, ready to be scripted."""
    return write_stub(tmp_path)


@pytest.fixture
def log() -> RecordingLog:
    """A fresh recording command log (requirement 12.8)."""
    return RecordingLog(entries=[])


# --- the digit ---------------------------------------------------------------------


def test_license_status_asks_isundlicensed_and_nothing_else(
    stub: UndStub, log: RecordingLog
) -> None:
    """``und -isundlicensed`` prints ``1`` with no newline; one call, and that is the verdict."""
    stub.plan({"-isundlicensed": {"stdout": "1"}})
    status = cli(stub, log).license_status()
    assert status == LicenseStatus(ok=True)
    assert stub.calls == [["-isundlicensed"]]


def test_license_status_reads_zero_as_unlicensed(stub: UndStub, log: RecordingLog) -> None:
    stub.plan({"-isundlicensed": {"stdout": "0"}})
    status = cli(stub, log).license_status()
    assert status.ok is False
    assert "0" in status.text
    assert stub.calls == [["-isundlicensed"]]


def test_license_status_reads_the_digit_whatever_the_exit_status(
    stub: UndStub, log: RecordingLog
) -> None:
    """8.0 exits 2 beside the 0 (its licensing reference says so); 6.5 exited 0. Same answer.

    Measured on 8.0.1262 before this: the probe required exit status 0, fell through to
    ``und license``, whose 8.0 output carries no error line, and ``doctor`` printed
    ``license: ok`` on a machine where every ``und analyze`` failed with "No Server Response".
    """
    stub.plan({"-isundlicensed": {"stdout": "0", "rc": 2}})
    assert cli(stub, log).license_status().ok is False
    stub.plan({"-isundlicensed": {"stdout": "1", "rc": 2}})
    assert cli(stub, log).license_status().ok is True


def test_an_answer_that_is_not_a_digit_is_reported_not_trusted(
    stub: UndStub, log: RecordingLog
) -> None:
    """A build without the switch answers with its usage error; that is the problem, quoted.

    No fallback to another licence command: the early error is the whole design, and the
    words are ``und``'s own so the operator can act on them.
    """
    stub.plan({"-isundlicensed": {"stderr": NO_COMMAND_OUTPUT, "rc": 1}})
    status = cli(stub, log).license_status()
    assert status.ok is False
    assert "No valid command found" in status.text
    assert stub.calls == [["-isundlicensed"]]


def test_a_silent_answer_is_reported_as_one(stub: UndStub, log: RecordingLog) -> None:
    """Nothing on either stream is not a licence; the status says what arrived instead."""
    stub.plan({"-isundlicensed": {"stdout": ""}})
    status = cli(stub, log).license_status()
    assert status.ok is False
    assert "no output" in status.text


def test_no_server_response_is_a_licensing_failure(stub: UndStub, log: RecordingLog) -> None:
    """8.0 without a valid offline code asks its licence server; off the network, this is it."""
    assert LICENSE_TEXT.search("No Server Response")
    assert LICENSE_TEXT.search("Your current license is Invalid!")


def test_license_status_never_passes_quiet(stub: UndStub, log: RecordingLog) -> None:
    """``und -quiet -isundlicensed`` would print nothing, which now reads as not established."""
    stub.plan({"-isundlicensed": {"stdout": "1"}})
    cli(stub, log).license_status()
    assert stub.argv == ["-isundlicensed"]


def test_license_status_is_recorded_in_the_command_log(stub: UndStub, log: RecordingLog) -> None:
    stub.plan({"-isundlicensed": {"stdout": "1"}})
    cli(stub, log).license_status()
    assert log.codes == [0]


# --- licensing text on any command -----------------------------------------------


def test_license_text_on_any_command_raises_license_error(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    stub.plan({"analyze": {"stderr": NO_LICENSE_OUTPUT, "rc": 1}})
    with pytest.raises(LicenseError) as caught:
        cli(stub, log).analyze(db_path(tmp_path), ALL)
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
        cli(stub, log).analyze(db_path(tmp_path), ALL)
