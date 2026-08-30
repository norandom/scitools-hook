"""The ``und`` command wrapper: argv construction, output parsing, error mapping (task 6.5).

Every unit test here drives a **stubbed executable** — a small Python script written into
``tmp_path`` and made executable — instead of the real ``und``. That keeps the whole suite
runnable without a license while still exercising the parts that matter: the exact argv,
the exit status, both output streams, and a command that never returns.

The stub replays a plan (``plan.json`` beside it) keyed by subcommand, appends every argv
it was given to ``calls.jsonl``, and snapshots the content of every file named on the
command line *while it runs* — which is the only moment a temporary ``@list`` file still
exists, so the list-file tests can prove what ``und`` would actually have read.

The scripted outputs are transcripts of the real ``und`` 6.5 (Build 1204) on a licensed
machine, not invented text; the ``contract``-marked tests at the end re-run the three cheap,
deterministic commands against the real executable so the transcripts cannot silently rot.
"""

from __future__ import annotations

import json
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import pytest
from fakes import FakeUndCli

from scitools_hook.errors import AnalysisFailedError, LicenseError
from scitools_hook.models.progress import CommandLog
from scitools_hook.models.snapshot import ParseError
from scitools_hook.models.understand import AnalyzeResult, LicenseStatus, UnderstandEnv
from scitools_hook.understand.und_cli import MISSING_RC, TIMEOUT_RC, UndCli

# --- transcripts of the real und -----------------------------------------------

VERSION_OUTPUT = "(Build 1204)\n"
"""``und version`` on 6.5 build 1204: a build number and no product version."""

NO_COMMAND_OUTPUT = (
    'Error: No valid command found. Type "und help" for help.\n'
    'Error: Unrecognized arguments. Type "und help" for help.\n'
)
"""What ``und -version`` answers: this build has no ``--version`` switch."""

LICENSE_OUTPUT = "Reply Code : D36C3CA9FF44A\nReply Date : 2036-08-28\n\n"
"""``und license`` with a valid license: a reply code, and no mention of a problem."""

NO_LICENSE_OUTPUT = "Licensing Error: No Und License Found\n"
"""The licensing text built into the executable; the wrapper must map it, not report it."""

CODECHECK_NO_LICENSE = "Licensing Error: No license for CodeCheck. \nStopping CodeCheck. \n"
"""Measured verbatim on the licensed machine, whose license excludes CodeCheck."""

BAD_DB_STDERR = (
    "Error: unable to open /nonexistent/nope.und\n"
    "Error: An open database is required for this action. \n"
)
"""``und -db <missing> analyze -all``: exit status 1 and this on standard error."""

ANALYZE_OUTPUT = """\
Error: expected identifier at token :
  File: /src/bad.py Line: 1
Error: expected token ':' at token EOF
  File: /src/bad.py
Warning: unable to find import module totally_missing_module_xyz
  File: /src/imp.py Line: 1
Warning: unable to find import module sibling
  File: /src/imp.py Line: 2
Error: expected identifier at token :
  File: /src/bad.py Line: 1
Error: expected token ':' at token EOF
  File: /src/bad.py
Analyze Completed (Errors:4 Warnings:2)
"""
"""A real Python analysis: pass 1 and pass 2 each report the same errors, hence the repeats."""

ANALYZE_C_OUTPUT = """\
Error: expected '}'
  File: /src/bad.c Line: 2 Col: 15
Error: anonymous structs must be struct or union members
  File: /src/bad.c Line: 2 Col: 1
Analyze Completed (Errors:2 Warnings:0)
"""
"""The C parser adds a column to the location line; ``ParseError`` has nowhere to put it."""

METRICS_OUTPUT = """\

--------------------------Metric Settings--------------------------
Option                          Current Setting  Available Settings
  WriteColumnTitles              On              On/Off
  Cyclomatic                     Normal          All/Normal/Strict/Modified/StrictModified

Metrics (+ if selected):
     AvgCountLine                            AvgCountLineBlank
     CountDeclMethodAll                   +  CountLine
  +  Cyclomatic                              MaxCyclomatic
"""
"""``und -db X list -metrics settings``: a settings table, then the two-column metric list."""

METRICS_THEN_REPORTS = (
    METRICS_OUTPUT
    + """

--------------------------Report Settings--------------------------
Option                          Current Setting  Available Settings
  DisplayCreationDate            Off             On/Off
"""
)
"""``list -all settings`` really does put another section under the metric list."""


# --- the stubbed executable ------------------------------------------------------

STUB_SOURCE = '''#!/usr/bin/env python3
"""Stand-in for ``und``: record the call, snapshot any list files, replay a plan."""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ARGV = sys.argv[1:]

with open(os.path.join(HERE, "calls.jsonl"), "a", encoding="utf-8") as handle:
    handle.write(json.dumps(ARGV) + "\\n")

SEEN = {}
for token in ARGV:
    named = token[1:] if token.startswith("@") else token
    if os.path.isfile(named):
        with open(named, encoding="utf-8") as handle:
            SEEN[os.path.basename(named)] = handle.read()
with open(os.path.join(HERE, "lists.json"), "w", encoding="utf-8") as handle:
    json.dump(SEEN, handle)

with open(os.path.join(HERE, "plan.json"), encoding="utf-8") as handle:
    PLANS = json.load(handle)
PLAN = next((PLANS[token] for token in ARGV if token in PLANS), PLANS.get("default", {}))

time.sleep(PLAN.get("sleep", 0))
OUT = ARGV[-1] if ARGV and os.path.isdir(ARGV[-1]) else HERE
for name, text in PLAN.get("write", {}).items():
    with open(os.path.join(OUT, name), "w", encoding="utf-8") as handle:
        handle.write(text)
sys.stdout.write(PLAN.get("stdout", ""))
sys.stderr.write(PLAN.get("stderr", ""))
sys.exit(PLAN.get("rc", 0))
'''


@dataclass(frozen=True)
class UndStub:
    """An executable that impersonates ``und`` and reports what it was asked to do."""

    root: Path

    @property
    def path(self) -> Path:
        """The executable an :class:`UnderstandEnv` should point at."""
        return self.root / "und"

    def plan(self, plans: Mapping[str, Mapping[str, object]]) -> None:
        """Script the answers, keyed by any argv token (a subcommand) or ``default``."""
        (self.root / "plan.json").write_text(json.dumps(plans), encoding="utf-8")

    @property
    def calls(self) -> list[list[str]]:
        """Every argv the stub was run with, in order, without the executable itself."""
        recorded = self.root / "calls.jsonl"
        if not recorded.exists():
            return []
        return [json.loads(line) for line in recorded.read_text(encoding="utf-8").splitlines()]

    @property
    def argv(self) -> list[str]:
        """The single call the test made; fails loudly when there was not exactly one."""
        assert len(self.calls) == 1, f"expected one und call, got {self.calls}"
        return self.calls[0]

    @property
    def lists(self) -> dict[str, str]:
        """Content of every file named on the last command line, read while it ran."""
        snapshot = self.root / "lists.json"
        if not snapshot.exists():
            return {}
        return dict(json.loads(snapshot.read_text(encoding="utf-8")))

    def env(self) -> UnderstandEnv:
        """An installation whose ``und`` is this stub."""
        return understand_env(self.path)


def understand_env(und: Path) -> UnderstandEnv:
    """The minimal :class:`UnderstandEnv` the wrapper needs: it only ever reads ``und``."""
    home = und.parent
    return UnderstandEnv(
        home=home,
        und=und,
        upython=None,
        python_api_dir=home / "Python",
        version=VERSION_OUTPUT.strip(),
        source="test",
        api_mode="upython",
    )


@pytest.fixture
def stub(tmp_path: Path) -> UndStub:
    """A stubbed ``und`` executable with an empty plan, ready to be scripted."""
    root = tmp_path / "bin"
    root.mkdir()
    script = root / "und"
    script.write_text(STUB_SOURCE, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    made = UndStub(root)
    made.plan({})
    return made


@dataclass
class RecordingLog:
    """A ``CommandLog`` that keeps what it was told, so timing and rc can be asserted."""

    entries: list[tuple[list[str], float, int]]

    def record(self, argv: list[str], seconds: float, rc: int) -> None:
        """Keep one finished command."""
        self.entries.append((list(argv), seconds, rc))

    @property
    def codes(self) -> list[int]:
        """The exit status of every recorded command."""
        return [rc for _, _, rc in self.entries]


@pytest.fixture
def log() -> RecordingLog:
    """A fresh recording command log (requirement 12.8)."""
    return RecordingLog(entries=[])


def cli(stub: UndStub, log: CommandLog, timeout_s: int = 900) -> UndCli:
    """The wrapper under test, pointed at the stub."""
    return UndCli(stub.env(), log, timeout_s=timeout_s)


def db_path(tmp_path: Path) -> Path:
    """A database path; a ``.und`` database is a directory, so nothing is created here."""
    return tmp_path / "cache" / "after.und"


# --- version --------------------------------------------------------------------


def test_version_returns_what_und_printed(stub: UndStub, log: RecordingLog) -> None:
    stub.plan({"version": {"stdout": VERSION_OUTPUT}})
    assert cli(stub, log).version() == "(Build 1204)"


def test_version_argv_is_the_bare_subcommand(stub: UndStub, log: RecordingLog) -> None:
    stub.plan({"version": {"stdout": VERSION_OUTPUT}})
    cli(stub, log).version()
    assert stub.argv == ["version"]


def test_version_never_passes_quiet(stub: UndStub, log: RecordingLog) -> None:
    """``und -quiet version`` prints nothing at all, so ``-quiet`` would empty the answer."""
    stub.plan({"version": {"stdout": VERSION_OUTPUT}})
    cli(stub, log).version()
    assert "-quiet" not in stub.argv


def test_version_rejects_an_error_reported_with_a_zero_status(
    stub: UndStub, log: RecordingLog
) -> None:
    """A switch this build does not accept must never be mistaken for a version string."""
    stub.plan({"version": {"stdout": NO_COMMAND_OUTPUT, "rc": 0}})
    with pytest.raises(AnalysisFailedError) as caught:
        cli(stub, log).version()
    assert "No valid command found" in str(caught.value)


def test_version_rejects_empty_output(stub: UndStub, log: RecordingLog) -> None:
    stub.plan({"version": {"stdout": "", "rc": 0}})
    with pytest.raises(AnalysisFailedError):
        cli(stub, log).version()


def test_version_failure_carries_the_command_and_stderr(stub: UndStub, log: RecordingLog) -> None:
    stub.plan({"version": {"stderr": NO_COMMAND_OUTPUT, "rc": 1}})
    with pytest.raises(AnalysisFailedError) as caught:
        cli(stub, log).version()
    assert caught.value.command[-1] == "version"
    assert caught.value.command[0] == str(stub.path)
    assert "No valid command found" in caught.value.stderr


# --- license --------------------------------------------------------------------


def test_license_status_prefers_isundlicensed(stub: UndStub, log: RecordingLog) -> None:
    """``und -isundlicensed`` prints ``1`` with no newline; one call, no fallback."""
    stub.plan({"-isundlicensed": {"stdout": "1"}})
    status = cli(stub, log).license_status()
    assert status == LicenseStatus(ok=True)
    assert stub.calls == [["-isundlicensed"]]


def test_license_status_reads_zero_as_unlicensed(stub: UndStub, log: RecordingLog) -> None:
    stub.plan({"-isundlicensed": {"stdout": "0"}})
    status = cli(stub, log).license_status()
    assert status.ok is False
    assert "0" in status.text


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
    assert log.codes == [0]


# --- create, add, remove ---------------------------------------------------------


def test_create_puts_the_global_switches_before_the_subcommand(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """``und create … -quiet`` is rejected as an unused argument; placement is not cosmetic."""
    database = db_path(tmp_path)
    cli(stub, log).create(database, ["python", "c++"])
    assert stub.argv == [
        "-quiet",
        "-db",
        str(database),
        "create",
        "-languages",
        "python",
        "c++",
        "-local",
    ]


def test_create_can_leave_out_local(stub: UndStub, log: RecordingLog, tmp_path: Path) -> None:
    cli(stub, log).create(db_path(tmp_path), ["python"], local=False)
    assert "-local" not in stub.argv


def test_create_failure_maps_to_analysis_failed(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    stub.plan({"create": {"stderr": BAD_DB_STDERR, "rc": 1}})
    with pytest.raises(AnalysisFailedError) as caught:
        cli(stub, log).create(db_path(tmp_path), ["python"])
    assert "unable to open" in caught.value.stderr


def test_add_joins_excludes_with_commas(stub: UndStub, log: RecordingLog, tmp_path: Path) -> None:
    """``-exclude`` takes one comma-separated argument; measured to drop a ``build`` tree."""
    database = db_path(tmp_path)
    root = tmp_path / "work"
    cli(stub, log).add(database, root, ["build", "*.tmp"])
    assert stub.argv == [
        "-quiet",
        "-db",
        str(database),
        "add",
        "-exclude",
        "build,*.tmp",
        str(root),
    ]


def test_add_without_excludes_omits_the_switch(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    cli(stub, log).add(db_path(tmp_path), tmp_path / "work", [])
    assert "-exclude" not in stub.argv


def test_remove_files_passes_a_list_file(stub: UndStub, log: RecordingLog, tmp_path: Path) -> None:
    database = db_path(tmp_path)
    cli(stub, log).remove_files(database, [tmp_path / "a.py", tmp_path / "b.py"])
    assert stub.argv[:5] == ["-quiet", "-db", str(database), "remove", "-file"]
    assert stub.argv[5].startswith("@")


def test_remove_files_list_holds_one_path_per_line(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """Measured: ``und`` reads one path per line and ignores ``#`` comments."""
    cli(stub, log).remove_files(db_path(tmp_path), [tmp_path / "a.py", tmp_path / "b.py"])
    (written,) = stub.lists.values()
    assert written == f"{tmp_path / 'a.py'}\n{tmp_path / 'b.py'}\n"


def test_remove_files_deletes_the_list_file_afterwards(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    cli(stub, log).remove_files(db_path(tmp_path), [tmp_path / "a.py"])
    listing = Path(stub.argv[-1][1:])
    assert not listing.exists()


def test_remove_files_with_nothing_to_remove_runs_no_command(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    cli(stub, log).remove_files(db_path(tmp_path), [])
    assert stub.calls == []
    assert log.entries == []


# --- analyze ---------------------------------------------------------------------


def test_analyze_all_asks_for_errors_and_warnings(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """``-quiet`` silences the parse errors requirement 2.6 needs; ``-errors`` keeps them."""
    database = db_path(tmp_path)
    stub.plan({"analyze": {"stdout": ANALYZE_OUTPUT}})
    cli(stub, log).analyze(database, None, all=True)
    assert stub.argv == ["-db", str(database), "analyze", "-all", "-errors", "-warnings"]


def test_analyze_without_a_file_list_analyzes_what_changed(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    stub.plan({"analyze": {"stdout": ANALYZE_OUTPUT}})
    cli(stub, log).analyze(db_path(tmp_path), None)
    assert "-changed" in stub.argv
    assert "-all" not in stub.argv


def test_analyze_files_uses_an_at_prefixed_list_file(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """A bare list file name is read as a source file; the ``@`` prefix is required."""
    stub.plan({"analyze": {"stdout": ANALYZE_OUTPUT}})
    cli(stub, log).analyze(db_path(tmp_path), [tmp_path / "a.py"])
    assert "-files" in stub.argv
    listing = stub.argv[stub.argv.index("-files") + 1]
    assert listing.startswith("@")
    assert stub.lists[Path(listing[1:]).name] == f"{tmp_path / 'a.py'}\n"


def test_analyze_of_an_empty_file_list_runs_no_command(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """Measured: ``analyze -files @<empty>`` exits 0 having done nothing, so skip the run."""
    result = cli(stub, log).analyze(db_path(tmp_path), [])
    assert result.parse_errors == []
    assert stub.calls == []


def test_analyze_parses_errors_with_their_file_and_line(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    stub.plan({"analyze": {"stdout": ANALYZE_OUTPUT}})
    result = cli(stub, log).analyze(db_path(tmp_path), None, all=True)
    assert result.parse_errors == [
        ParseError(path=Path("/src/bad.py"), line=1, message="expected identifier at token :"),
        ParseError(path=Path("/src/bad.py"), line=None, message="expected token ':' at token EOF"),
    ]


def test_analyze_does_not_repeat_an_error_reported_by_both_passes(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """Understand's Python analyzer reports pass 1 and pass 2; the answer must list one."""
    stub.plan({"analyze": {"stdout": ANALYZE_OUTPUT}})
    result = cli(stub, log).analyze(db_path(tmp_path), None, all=True)
    assert len(result.parse_errors) == 2


def test_analyze_takes_the_warning_count_from_the_summary(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    stub.plan({"analyze": {"stdout": ANALYZE_OUTPUT}})
    assert cli(stub, log).analyze(db_path(tmp_path), None, all=True).warnings == 2


def test_analyze_reads_a_location_that_also_carries_a_column(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """The C parser appends ``Col:``; the line number must survive it."""
    stub.plan({"analyze": {"stdout": ANALYZE_C_OUTPUT}})
    result = cli(stub, log).analyze(db_path(tmp_path), None, all=True)
    assert [(str(error.path), error.line) for error in result.parse_errors] == [
        ("/src/bad.c", 2),
        ("/src/bad.c", 2),
    ]


def test_analyze_of_a_clean_project_reports_nothing(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    stub.plan({"analyze": {"stdout": "Analyze Completed (Errors:0 Warnings:0)\n"}})
    result = cli(stub, log).analyze(db_path(tmp_path), None, all=True)
    assert result.parse_errors == []
    assert result.warnings == 0


def test_analyze_without_a_summary_line_still_answers(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """Measured: an analysis with nothing to do prints no summary at all and exits 0."""
    stub.plan({"analyze": {"stdout": ""}})
    result = cli(stub, log).analyze(db_path(tmp_path), None, all=True)
    assert result.warnings == 0
    assert result.seconds >= 0


def test_analyze_counts_warning_lines_when_und_printed_no_summary(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """Without the closing tally the printed lines are the only count there is."""
    stub.plan(
        {
            "analyze": {
                "stdout": (
                    "Warning: unable to find import module one\n"
                    "  File: /src/imp.py Line: 1\n"
                    "Warning: unable to find import module two\n"
                    "  File: /src/imp.py Line: 2\n"
                )
            }
        }
    )
    assert cli(stub, log).analyze(db_path(tmp_path), None, all=True).warnings == 2


def test_analyze_records_how_long_it_took(stub: UndStub, log: RecordingLog, tmp_path: Path) -> None:
    stub.plan({"analyze": {"stdout": ANALYZE_OUTPUT, "sleep": 0.05}})
    result = cli(stub, log).analyze(db_path(tmp_path), None, all=True)
    assert result.seconds >= 0.05


def test_analyze_failure_maps_to_analysis_failed(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    stub.plan({"analyze": {"stderr": BAD_DB_STDERR, "rc": 1}})
    with pytest.raises(AnalysisFailedError) as caught:
        cli(stub, log).analyze(db_path(tmp_path), None, all=True)
    assert caught.value.stderr.startswith("Error: unable to open")
    assert "analyze" in caught.value.command


def test_analyze_keeps_reporting_parse_errors_rather_than_failing(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """Requirement 2.6: parse errors are data, not a failure — every rule still runs."""
    stub.plan({"analyze": {"stdout": ANALYZE_OUTPUT, "rc": 0}})
    result = cli(stub, log).analyze(db_path(tmp_path), None, all=True)
    assert result.parse_errors


# --- licence mapping across commands ---------------------------------------------


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


# --- list -metrics settings -------------------------------------------------------


def test_list_metrics_argv(stub: UndStub, log: RecordingLog, tmp_path: Path) -> None:
    database = db_path(tmp_path)
    stub.plan({"list": {"stdout": METRICS_OUTPUT}})
    cli(stub, log).list_metrics(database)
    assert stub.argv == ["-db", str(database), "list", "-metrics", "settings"]


def test_list_metrics_reads_both_columns_and_drops_the_selected_marker(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    stub.plan({"list": {"stdout": METRICS_OUTPUT}})
    assert cli(stub, log).list_metrics(db_path(tmp_path)) == [
        "AvgCountLine",
        "AvgCountLineBlank",
        "CountDeclMethodAll",
        "CountLine",
        "Cyclomatic",
        "MaxCyclomatic",
    ]


def test_list_metrics_ignores_the_settings_table_above_the_metric_list(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """``WriteColumnTitles`` is a setting, not a metric, and reads like one if unguarded."""
    stub.plan({"list": {"stdout": METRICS_OUTPUT}})
    assert "WriteColumnTitles" not in cli(stub, log).list_metrics(db_path(tmp_path))


def test_list_metrics_stops_where_the_metric_list_stops(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """A section printed under the list must not be harvested as more metric names."""
    stub.plan({"list": {"stdout": METRICS_THEN_REPORTS}})
    found = cli(stub, log).list_metrics(db_path(tmp_path))
    assert found[-1] == "MaxCyclomatic"
    assert "DisplayCreationDate" not in found


def test_list_metrics_rejects_an_error_reported_with_a_zero_status(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    stub.plan({"list": {"stdout": NO_COMMAND_OUTPUT, "rc": 0}})
    with pytest.raises(AnalysisFailedError):
        cli(stub, log).list_metrics(db_path(tmp_path))


# --- codecheck --------------------------------------------------------------------


def test_codecheck_switches_precede_the_two_positional_arguments(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """``und codecheck [-switches] <configuration> <output directory>``."""
    database = db_path(tmp_path)
    out_dir = tmp_path / "cc"
    stub.plan({"codecheck": {"write": {"violations.csv": "Check ID,File\n"}}})
    cli(stub, log).codecheck(database, "Quick Check", [tmp_path / "a.py"], out_dir)
    assert stub.argv[:4] == ["-db", str(database), "codecheck", "-files"]
    assert stub.argv[-2:] == ["Quick Check", str(out_dir)]


def test_codecheck_file_list_is_not_at_prefixed(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """``-files`` documents a list file that "does not have to start with @"."""
    out_dir = tmp_path / "cc"
    stub.plan({"codecheck": {"write": {"violations.csv": "Check ID,File\n"}}})
    cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    listing = stub.argv[stub.argv.index("-files") + 1]
    assert not listing.startswith("@")
    assert stub.lists[Path(listing).name] == f"{tmp_path / 'a.py'}\n"


def test_codecheck_returns_the_csv_it_found(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    out_dir = tmp_path / "cc"
    stub.plan({"codecheck": {"write": {"violations.csv": "Check ID,File\n"}}})
    found = cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    assert found == out_dir / "violations.csv"


def test_codecheck_creates_the_output_directory(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    out_dir = tmp_path / "deep" / "cc"
    stub.plan({"codecheck": {"write": {"violations.csv": "Check ID,File\n"}}})
    cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    assert out_dir.is_dir()


def test_codecheck_without_a_csv_fails_loudly(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """A silent empty directory would read as "no violations"; it means "no results"."""
    out_dir = tmp_path / "cc"
    with pytest.raises(AnalysisFailedError) as caught:
        cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    assert "csv" in str(caught.value).lower()


# --- timeouts, missing executables and the command log ----------------------------


def test_a_command_that_never_returns_becomes_an_analysis_failure(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """``subprocess.TimeoutExpired`` is not an ``OSError``; catching only ``OSError`` misses it."""
    stub.plan({"analyze": {"sleep": 5}})
    with pytest.raises(AnalysisFailedError) as caught:
        cli(stub, log, timeout_s=1).analyze(db_path(tmp_path), None, all=True)
    assert "timed out" in str(caught.value)
    assert caught.value.command[0] == str(stub.path)


def test_a_timeout_is_still_recorded_in_the_command_log(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    stub.plan({"analyze": {"sleep": 5}})
    with pytest.raises(AnalysisFailedError):
        cli(stub, log, timeout_s=1).analyze(db_path(tmp_path), None, all=True)
    assert log.codes == [TIMEOUT_RC]


def test_a_timeout_does_not_leave_the_list_file_behind(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """A hung analysis must not leak its scratch directory into the system temp area."""
    stub.plan({"analyze": {"sleep": 5}})
    with pytest.raises(AnalysisFailedError):
        cli(stub, log, timeout_s=1).analyze(db_path(tmp_path), [tmp_path / "a.py"])
    (argv, _, _) = log.entries[0]
    listing = Path(argv[argv.index("-files") + 1][1:])
    assert not listing.exists()
    assert not listing.parent.exists()


def test_an_unrunnable_und_becomes_an_analysis_failure(tmp_path: Path, log: RecordingLog) -> None:
    missing = UndCli(understand_env(tmp_path / "nowhere" / "und"), log)
    with pytest.raises(AnalysisFailedError) as caught:
        missing.version()
    assert caught.value.command[0].endswith("und")
    assert log.codes == [MISSING_RC]


def test_every_command_is_recorded_with_its_argv_timing_and_status(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """Requirement 12.8: ``--verbose`` prints each external command with its timing."""
    database = db_path(tmp_path)
    stub.plan({"analyze": {"stdout": ANALYZE_OUTPUT}, "list": {"stdout": METRICS_OUTPUT}})
    wrapper = cli(stub, log)
    wrapper.create(database, ["python"])
    wrapper.add(database, tmp_path / "work", [])
    wrapper.analyze(database, None, all=True)
    wrapper.list_metrics(database)
    assert [argv[0] for argv, _, _ in log.entries] == [str(stub.path)] * 4
    assert [argv[argv.index(str(database)) + 1] for argv, _, _ in log.entries] == [
        "create",
        "add",
        "analyze",
        "list",
    ]
    assert log.codes == [0, 0, 0, 0]
    assert all(seconds >= 0 for _, seconds, _ in log.entries)


def test_a_failing_command_is_recorded_before_the_error_is_raised(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    stub.plan({"analyze": {"stderr": BAD_DB_STDERR, "rc": 1}})
    with pytest.raises(AnalysisFailedError):
        cli(stub, log).analyze(db_path(tmp_path), None, all=True)
    assert log.codes == [1]


# --- the fake used by the tasks that depend on this one ----------------------------


def test_fake_und_cli_records_every_call() -> None:
    fake = FakeUndCli()
    fake.create(Path("db.und"), ["python"])
    fake.analyze(Path("db.und"), None, all=True)
    assert [call.command for call in fake.calls] == ["create", "analyze"]
    assert fake.calls[0].arguments["languages"] == ["python"]
    assert fake.calls[1].arguments["all"] is True


def test_fake_und_cli_returns_the_configured_analyze_results() -> None:
    first = AnalyzeResult(warnings=3, seconds=1.0)
    second = AnalyzeResult(
        parse_errors=[ParseError(path=Path("a.py"), line=2, message="boom")], seconds=2.0
    )
    fake = FakeUndCli(analyze_results=[first, second])
    assert fake.analyze(Path("db.und"), None, all=True) == first
    assert fake.analyze(Path("db.und"), [Path("a.py")]) == second


def test_fake_und_cli_falls_back_to_an_empty_result_when_none_are_left() -> None:
    fake = FakeUndCli(analyze_results=[AnalyzeResult(seconds=1.0)])
    fake.analyze(Path("db.und"), None, all=True)
    spare = fake.analyze(Path("db.und"), None, all=True)
    assert spare.parse_errors == []
    assert spare.warnings == 0


def test_fake_und_cli_answers_the_remaining_commands() -> None:
    fake = FakeUndCli(
        version_text="(Build 1204)",
        metrics=["CountLine"],
        violations_csv=Path("violations.csv"),
    )
    assert fake.version() == "(Build 1204)"
    assert fake.license_status() == LicenseStatus(ok=True)
    assert fake.list_metrics(Path("db.und")) == ["CountLine"]
    assert fake.codecheck(Path("db.und"), "Quick", [], Path("out")) == Path("violations.csv")


def test_fake_und_cli_is_usable_wherever_the_real_one_is() -> None:
    """Annotating it as ``UndCli`` is what makes mypy compare the method signatures."""
    substitute: UndCli = FakeUndCli()
    assert isinstance(substitute, UndCli)


# --- contract: the same three commands against the real und ------------------------


class SampleSet(Protocol):
    """The part of ``conftest.SampleDatabases`` these tests use.

    Declared here rather than imported so this module does not do ``from conftest import``,
    which task 6.1 recorded as the thing that breaks under ``--import-mode=importlib``.
    """

    und: Path
    after_db: Path


@pytest.mark.contract
def test_contract_version_reports_a_build(sample_databases: SampleSet) -> None:
    """The real ``und version`` answers with a build number and no product version."""
    reported = UndCli(understand_env(sample_databases.und), _null_log()).version()
    assert "Build" in reported
    assert not reported.startswith("6.")


@pytest.mark.contract
def test_contract_license_status_is_ok_on_a_licensed_machine(sample_databases: SampleSet) -> None:
    status = UndCli(understand_env(sample_databases.und), _null_log()).license_status()
    assert status == LicenseStatus(ok=True)


@pytest.mark.contract
def test_contract_list_metrics_offers_the_metrics_the_gate_relies_on(
    sample_databases: SampleSet,
) -> None:
    wrapper = UndCli(understand_env(sample_databases.und), _null_log())
    available = wrapper.list_metrics(sample_databases.after_db)
    assert {"CountLineCode", "Cyclomatic", "MaxNesting"} <= set(available)


def _null_log() -> CommandLog:
    """A command log for the contract tests, which assert on Understand rather than logging."""
    return RecordingLog(entries=[])
