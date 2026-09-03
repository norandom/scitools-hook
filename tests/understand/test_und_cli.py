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
import tempfile
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
from scitools_hook.understand.und_cli import (
    CONFIG_HINT,
    EXPORT_PREFIX,
    MISSING_RC,
    TIMEOUT_RC,
    VIOLATIONS_EXPORT,
    UndCli,
    _list_file,
)

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


# --- the two recorded statuses, and the timing they are recorded with -------------

TIMEOUT_KILLED_STATUS = 124
"""GNU ``timeout(1)``'s status for a command it had to kill (requirement 12.8).

Written here as a literal and **deliberately not** taken from
:data:`~scitools_hook.exit_codes.TIMEOUT_RC`: a test that reads a constant back out of the
code under test asserts nothing about its value. Measured under exactly the spelling this
file used before task 11.2 (``assert log.codes == [TIMEOUT_RC]``), ``124 -> 0`` survived the
**entire 3067-test suite** -- so a build recording an ``und`` that hung at its limit as a
success passed every gate. 124 is the number an operator already reads as "killed at its
limit", which is why it is the right one to record.
"""

SHELL_COMMAND_NOT_FOUND_STATUS = 127
"""The shell's status for an executable that could not be started (requirement 12.8).

The counterpart of :data:`TIMEOUT_KILLED_STATUS`, held to the same rule for the same measured
reason: ``127 -> 0`` also survived all 3067 tests while this file compared the log against
``MISSING_RC`` imported from the module under test.
"""

SLOW_UND_SLEEP_S = 0.3
"""How long the stubbed ``und`` sleeps when a test needs a duration it knows independently.

Measured on this module: a stub sleeping 0.30 s is recorded as 0.32 s, against 0.02 s for one
that does not sleep and 0.00028 s for an executable that never started. The recorded number
has to be pinned against a quantity the test knows *without* asking the module under test,
because every assertion here used to be a comparison against zero -- and a
:func:`time.monotonic` delta is non-negative by construction, so ``seconds >= 0`` cannot fail.
"""

SLOW_UND_FLOOR_S = 0.25
"""The floor asserted against :data:`SLOW_UND_SLEEP_S`, leaving room for a coarse clock."""

KILLED_FLOOR_S = 0.5
"""The floor for a command killed at a one-second limit: it cannot have taken less than this.

Half the limit rather than the limit itself, so a slow machine's rounding cannot make a
correct implementation fail; still far above the ``0.0`` a constant would record.
"""

CLOCK_READING_CEILING_S = 10.0
"""The ceiling every recorded duration is held under, which fails a clock *reading*.

:func:`time.monotonic` is the machine's uptime on Linux (measured here in the hundreds of
thousands of seconds), so recording it instead of the delta clears every floor and fails this.
No ``und`` call in this file is allowed anywhere near ten seconds.
"""


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
for name in PLAN.get("mkdir", []):
    os.makedirs(os.path.join(OUT, name), exist_ok=True)
for name in PLAN.get("symlink_loop", []):
    target = os.path.join(OUT, name)
    os.symlink(target, target)
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


def assume_unsorted_readdir(directory: Path) -> None:
    """Assert there is something for sorting to do before pinning a sorted message.

    The filesystem decides what order ``iterdir`` hands entries back in. Where that order is
    already alphabetical there is nothing an unsorted implementation could get wrong, and a
    test claiming otherwise would be claiming more than it checked.
    """
    listed = [path.name for path in directory.iterdir()]
    if listed == sorted(listed):
        pytest.skip(f"this filesystem lists {directory} already sorted; nothing to pin")


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
    stub.plan({"codecheck": {"write": {f"{VIOLATIONS_EXPORT}.csv": "Check ID,File\n"}}})
    cli(stub, log).codecheck(database, "Quick Check", [tmp_path / "a.py"], out_dir)
    assert stub.argv[:4] == ["-db", str(database), "codecheck", "-files"]
    assert stub.argv[-2:] == ["Quick Check", str(out_dir)]


def test_codecheck_file_list_is_not_at_prefixed(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """``-files`` documents a list file that "does not have to start with @"."""
    out_dir = tmp_path / "cc"
    stub.plan({"codecheck": {"write": {f"{VIOLATIONS_EXPORT}.csv": "Check ID,File\n"}}})
    cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    listing = stub.argv[stub.argv.index("-files") + 1]
    assert not listing.startswith("@")
    assert stub.lists[Path(listing).name] == f"{tmp_path / 'a.py'}\n"


def test_codecheck_returns_the_csv_it_found(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    out_dir = tmp_path / "cc"
    stub.plan({"codecheck": {"write": {f"{VIOLATIONS_EXPORT}.csv": "Check ID,File\n"}}})
    found = cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    assert found == out_dir / f"{VIOLATIONS_EXPORT}.csv"


def test_codecheck_picks_the_per_violation_export_not_the_alphabetically_first(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """``codecheck`` writes three exports; sorted() would hand back the files-tree one."""
    out_dir = tmp_path / "cc"
    stub.plan(
        {
            "codecheck": {
                "write": {
                    "CodeCheckResultByFile.csv": "File\n",
                    "CodeCheckResultByTable.csv": "Table\n",
                    f"{VIOLATIONS_EXPORT}.csv": "Violation\n",
                }
            }
        }
    )
    found = cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    assert found == out_dir / f"{VIOLATIONS_EXPORT}.csv"
    assert found != sorted(out_dir.glob("*.csv"))[0]


def test_codecheck_refuses_to_guess_between_several_exports(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """Without the per-violation export, picking one of several would hand back a schema."""
    out_dir = tmp_path / "cc"
    stub.plan(
        {
            "codecheck": {
                "write": {
                    "CodeCheckResultByFile.csv": "File\n",
                    "CodeCheckResultByTable.csv": "Table\n",
                }
            }
        }
    )
    with pytest.raises(AnalysisFailedError) as caught:
        cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    complaint = str(caught.value)
    assert "CodeCheckResultByFile.csv" in complaint
    assert "CodeCheckResultByTable.csv" in complaint


def test_the_export_names_are_the_ones_und_is_built_with() -> None:
    """Pinned to the literal, not to itself: a drifted value would take the wrong file.

    ``test_contract_the_fixture_headers_and_export_names_are_compiled_into_und`` in
    ``test_codecheck_runner.py`` checks the same strings against the executable's own bytes,
    but that test needs an Understand install; this one holds on any machine.
    """
    assert VIOLATIONS_EXPORT == "CodeCheckResultByViolation"
    assert EXPORT_PREFIX == "CodeCheckResult"


def test_codecheck_finds_an_export_whose_extension_is_upper_case(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """``glob("*.csv")`` is case-sensitive on Linux, so a ``.CSV`` export would be invisible."""
    out_dir = tmp_path / "cc"
    stub.plan({"codecheck": {"write": {f"{VIOLATIONS_EXPORT}.CSV": "Check ID,File\n"}}})
    found = cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    assert found == out_dir / f"{VIOLATIONS_EXPORT}.CSV"


def test_codecheck_ignores_the_reports_that_are_not_csv(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """``-html`` and the compliance reports drop other files beside the CSV exports."""
    out_dir = tmp_path / "cc"
    stub.plan(
        {
            "codecheck": {
                "write": {
                    "CodeCheckResultByFile.csv": "File\n",
                    "index.html": "<html></html>",
                    "summary.pdf": "%PDF",
                }
            }
        }
    )
    found = cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    assert found == out_dir / "CodeCheckResultByFile.csv"


def test_codecheck_recognises_the_per_violation_export_whatever_its_case(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """Understand also runs on a case-insensitive filesystem, where the name may arrive
    lower-cased. Written beside another export, a stem compared case-sensitively would not
    match at all and the wrapper would refuse to choose."""
    out_dir = tmp_path / "cc"
    stub.plan(
        {
            "codecheck": {
                "write": {
                    "codecheckresultbyviolation.csv": "Violation\n",
                    "CodeCheckResultByFile.csv": "File\n",
                }
            }
        }
    )
    found = cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    assert found == out_dir / "codecheckresultbyviolation.csv"


def test_codecheck_recognises_a_lone_export_whatever_its_case(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """The same for the fallback's scope check: the prefix is a name, not a byte sequence."""
    out_dir = tmp_path / "cc"
    stub.plan({"codecheck": {"write": {"codecheckresultbytable.csv": "Table\n"}}})
    found = cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    assert found == out_dir / "codecheckresultbytable.csv"


def test_codecheck_accepts_a_lone_export_that_is_still_one_of_codechecks_own(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    out_dir = tmp_path / "cc"
    stub.plan({"codecheck": {"write": {"CodeCheckResultByTable.csv": "Table\n"}}})
    found = cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    assert found == out_dir / "CodeCheckResultByTable.csv"


def test_codecheck_requires_an_export_name_to_start_with_the_prefix(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """``startswith``, not ``in``: a name that merely contains the prefix is somebody else's."""
    out_dir = tmp_path / "cc"
    stub.plan({"codecheck": {"write": {"stale-CodeCheckResultByTable.csv": "T\n"}}})
    with pytest.raises(AnalysisFailedError) as caught:
        cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    assert "stale-CodeCheckResultByTable.csv" in str(caught.value)


def test_codecheck_refuses_a_lone_csv_that_is_not_a_codecheck_export(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """One file is not one *right* file; an unrelated CSV would be read as violations."""
    out_dir = tmp_path / "cc"
    stub.plan({"codecheck": {"write": {"results.csv": "whatever\n"}}})
    with pytest.raises(AnalysisFailedError) as caught:
        cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    assert "results.csv" in str(caught.value)


def test_codecheck_does_not_read_an_unexaminable_entry_as_absent(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """``Path.is_file`` answers False for every ``OSError``, turning "cannot tell" into "no".

    Measured: a symlink loop named ``CodeCheckResultByViolation.csv`` makes ``is_file()``
    answer False while ``stat`` raises ELOOP. The per-violation export then vanishes from
    the listing, the lone-export fallback hands back the by-table schema instead, and
    nothing says a word — which is precisely what that fallback's docstring promises it
    prevents.
    """
    out_dir = tmp_path / "cc"
    stub.plan(
        {
            "codecheck": {
                "symlink_loop": [f"{VIOLATIONS_EXPORT}.csv"],
                "write": {"CodeCheckResultByTable.csv": "Table\n"},
            }
        }
    )
    with pytest.raises(AnalysisFailedError) as caught:
        cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    assert f"{VIOLATIONS_EXPORT}.csv" in str(caught.value)


def test_codecheck_does_not_mistake_a_directory_for_an_export(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """A directory named ``CodeCheckResultByViolation.csv`` is not the per-violation export."""
    out_dir = tmp_path / "cc"
    stub.plan(
        {
            "codecheck": {
                "mkdir": [f"{VIOLATIONS_EXPORT}.csv"],
                "write": {"CodeCheckResultByTable.csv": "Table\n"},
            }
        }
    )
    found = cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    assert found == out_dir / "CodeCheckResultByTable.csv"


def test_codecheck_ignores_a_hidden_file(stub: UndStub, log: RecordingLog, tmp_path: Path) -> None:
    """``und`` writes no dotfiles, so one in the output directory belongs to something else."""
    out_dir = tmp_path / "cc"
    stub.plan(
        {
            "codecheck": {
                "write": {
                    f".{VIOLATIONS_EXPORT}.csv": "hidden\n",
                    "CodeCheckResultByTable.csv": "Table\n",
                }
            }
        }
    )
    found = cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    assert found == out_dir / "CodeCheckResultByTable.csv"


def test_codecheck_lists_the_exports_it_refused_in_sorted_order(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """The message states an order, so two runs over one directory must read the same."""
    out_dir = tmp_path / "cc"
    written = ["Zulu", "Alpha", "Mike", "Bravo"]
    stub.plan({"codecheck": {"write": {f"CodeCheckResultBy{name}.csv": "x\n" for name in written}}})
    with pytest.raises(AnalysisFailedError) as caught:
        cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    assume_unsorted_readdir(out_dir)
    expected = ", ".join(sorted(f"CodeCheckResultBy{name}.csv" for name in written))
    assert expected in str(caught.value)


def test_codecheck_lists_a_stale_directorys_contents_in_sorted_order(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    out_dir = tmp_path / "cc"
    out_dir.mkdir()
    stale = ["zulu.txt", "alpha.txt", "mike.txt", "bravo.txt"]
    for name in stale:
        (out_dir / name).write_text("stale\n", encoding="utf-8")
    with pytest.raises(AnalysisFailedError) as caught:
        cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    assume_unsorted_readdir(out_dir)
    assert ", ".join(sorted(stale)) in str(caught.value)


def test_codecheck_refuses_an_output_directory_holding_anything_at_all(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """An HTML report from an earlier run is the same evidence as a stale CSV."""
    out_dir = tmp_path / "cc"
    out_dir.mkdir()
    (out_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    with pytest.raises(AnalysisFailedError) as caught:
        cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    assert "index.html" in str(caught.value)
    assert stub.calls == []


def test_codecheck_reports_an_output_directory_it_cannot_create_as_an_analysis_failure(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """A file where the directory should be raises ``FileExistsError`` out of ``mkdir``.

    That is an ``OSError``, which no caught-error tuple in the package expects, so it would
    leave the typed envelope every other failure here travels in.
    """
    blocked = tmp_path / "cc"
    blocked.write_text("not a directory\n", encoding="utf-8")
    with pytest.raises(AnalysisFailedError) as caught:
        cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], blocked)
    assert str(blocked) in str(caught.value)


def test_codecheck_reports_an_unreadable_output_directory_as_an_analysis_failure(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """Listing an existing directory raises ``PermissionError``, an ``OSError`` like any other."""
    out_dir = tmp_path / "cc"
    out_dir.mkdir(mode=0o000)
    try:
        with pytest.raises(AnalysisFailedError) as caught:
            cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    finally:
        out_dir.chmod(0o755)
    assert str(out_dir) in str(caught.value)


@pytest.mark.parametrize(
    "call",
    ["analyze", "remove_files"],
    ids=["analyze", "remove-files"],
)
def test_a_name_that_cannot_be_encoded_is_typed_for_every_list_file_caller(
    stub: UndStub, log: RecordingLog, tmp_path: Path, call: str
) -> None:
    """``git`` decodes names with ``surrogateescape``; ``write_text`` will not take one.

    ``UnicodeEncodeError`` is a ``ValueError`` — neither ``GateError`` nor ``OSError`` — so
    it is caught nowhere. The guard belongs in ``_list_file`` rather than in one caller,
    because ``analyze`` and ``remove_files`` write the same list file from the same kind of
    name.
    """
    stub.plan({call.split("_")[0]: {}})
    wrapper = cli(stub, log)
    unencodable = [Path("/src/caf\udce9.c")]
    with pytest.raises(AnalysisFailedError) as caught:
        if call == "analyze":
            wrapper.analyze(db_path(tmp_path), unencodable)
        else:
            wrapper.remove_files(db_path(tmp_path), unencodable)
    assert "list file" in str(caught.value)
    assert stub.calls == []


def test_the_list_file_never_lands_in_the_working_tree(tmp_path: Path) -> None:
    """Requirement 2.2, asserted rather than only claimed in a docstring.

    ``_list_file`` writing into the process cwd would put ``files.txt`` in the repository
    root for a pre-commit hook, which runs there — the same shape as the ``_prefix``
    ``.resolve()`` defect the project record already carries. Nothing pinned it: swapping
    ``TemporaryDirectory()`` for ``TemporaryDirectory(dir=".")`` left the suite green.
    """
    with _list_file([Path("/src/a.py")]) as listing:
        assert listing.is_file()
        assert listing.is_relative_to(Path(tempfile.gettempdir())), listing
        assert not listing.is_relative_to(Path.cwd()), listing
        assert listing.read_text(encoding="utf-8") == "/src/a.py\n"


def test_the_list_file_is_gone_once_the_command_has_run(tmp_path: Path) -> None:
    """It exists only while ``und`` is reading it, which is the other half of that claim."""
    with _list_file([Path("/src/a.py")]) as listing:
        kept = listing
    assert not kept.exists()


def test_a_refusal_names_the_lever_an_operator_actually_has(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """``CONFIG_HINT`` is a constant whose whole value is its wording, and nothing read it."""
    out_dir = tmp_path / "cc"
    stub.plan(
        {
            "codecheck": {
                "write": {"CodeCheckResultByFile.csv": "F\n", "CodeCheckResultByTable.csv": "T\n"}
            }
        }
    )
    with pytest.raises(AnalysisFailedError) as caught:
        cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    assert caught.value.hint == CONFIG_HINT
    assert "codecheck.config" in CONFIG_HINT


def test_codecheck_refuses_an_output_directory_that_is_not_empty(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """A reused directory would hand back the previous run's export as this run's results."""
    out_dir = tmp_path / "cc"
    out_dir.mkdir()
    (out_dir / f"{VIOLATIONS_EXPORT}.csv").write_text("stale\n", encoding="utf-8")
    stub.plan({"codecheck": {"write": {}}})
    with pytest.raises(AnalysisFailedError) as caught:
        cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    assert str(out_dir) in str(caught.value)
    assert stub.calls == []


def test_codecheck_names_the_output_directory_when_it_refuses_to_choose(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    out_dir = tmp_path / "cc"
    stub.plan(
        {
            "codecheck": {
                "write": {
                    "CodeCheckResultByFile.csv": "File\n",
                    "CodeCheckResultByTable.csv": "T\n",
                }
            }
        }
    )
    with pytest.raises(AnalysisFailedError) as caught:
        cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    assert str(out_dir) in str(caught.value)


def test_codecheck_creates_the_output_directory(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    out_dir = tmp_path / "deep" / "cc"
    stub.plan({"codecheck": {"write": {f"{VIOLATIONS_EXPORT}.csv": "Check ID,File\n"}}})
    cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    assert out_dir.is_dir()


def test_codecheck_without_a_csv_fails_loudly(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """A silent empty directory would read as "no violations"; it means "no results".

    The assertion is on a phrase, not the word "csv": ``tmp_path`` is named after the test
    that asked for it, so ``"csv" in str(exc)`` was satisfied by the directory
    ``test_codecheck_without_a_csv_f0`` whatever the code actually said.
    """
    out_dir = tmp_path / "cc"
    with pytest.raises(AnalysisFailedError) as caught:
        cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    assert "wrote no csv file" in str(caught.value).lower()


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
    """The killed command is logged as 124, and with the time it actually spent hanging.

    Both halves are asserted against numbers this test knows on its own. The status is the
    literal :data:`TIMEOUT_KILLED_STATUS`, not ``TIMEOUT_RC`` imported from the module under
    test -- that comparison is a tautology and ``124 -> 0`` survived all 3067 tests under it.
    The duration is held above :data:`KILLED_FLOOR_S`, which is derived from the one-second
    limit this test sets rather than from anything the module reports: ``record(argv, 0.0,
    TIMEOUT_RC)`` also survived all 3067 tests, so ``--verbose`` could have claimed a command
    that hung for its whole limit took no time at all.
    """
    stub.plan({"analyze": {"sleep": 5}})
    with pytest.raises(AnalysisFailedError):
        cli(stub, log, timeout_s=1).analyze(db_path(tmp_path), None, all=True)
    assert log.codes == [TIMEOUT_KILLED_STATUS]
    (_, seconds, _) = log.entries[-1]
    assert seconds >= KILLED_FLOOR_S, f"a command killed at a 1s limit logged {seconds}s"
    assert seconds < CLOCK_READING_CEILING_S, f"{seconds}s is a clock reading, not a duration"


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
    """An ``und`` that never started is reported, and logged as 127 with the time it took.

    The status is the literal :data:`SHELL_COMMAND_NOT_FOUND_STATUS` for the reason given in
    :func:`test_a_timeout_is_still_recorded_in_the_command_log`. The duration is asserted
    because failing to start still takes measurable time (measured: 0.00028 s) and
    ``--verbose`` prints that line like any other -- ``record(argv, 0.0, MISSING_RC)`` survived
    all 3067 tests before this assertion existed.
    """
    missing = UndCli(understand_env(tmp_path / "nowhere" / "und"), log)
    with pytest.raises(AnalysisFailedError) as caught:
        missing.version()
    assert caught.value.command[0].endswith("und")
    assert log.codes == [SHELL_COMMAND_NOT_FOUND_STATUS]
    (_, seconds, _) = log.entries[-1]
    assert seconds > 0.0, "a command that never started still took time to fail"
    assert seconds < CLOCK_READING_CEILING_S, f"{seconds}s is a clock reading, not a duration"


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
    # `> 0.0`, not `>= 0`: a `time.monotonic()` delta is non-negative by construction, so the
    # comparison this replaces could not fail, and `record(argv, 0.0, rc)` survived all 3067
    # tests. The quantity itself is pinned by the sleeping stub in the next test.
    assert all(seconds > 0.0 for _, seconds, _ in log.entries)
    assert all(seconds < CLOCK_READING_CEILING_S for _, seconds, _ in log.entries)


def test_the_recorded_duration_is_the_length_of_the_call_it_timed(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """Requirement 12.8: the seconds in the log are *this command's*, not a plausible number.

    Every other duration assertion in this file is a comparison against a bound, and no bound
    at zero can pin a quantity. This one hands the test a duration it knows independently of
    the module under test -- the stub sleeps :data:`SLOW_UND_SLEEP_S` -- and holds the
    recorded number between a floor and a ceiling that fail different mistakes: the floor
    fails any constant below 0.25 (``0.0`` included) and a span measured around the wrong
    call; the ceiling fails a clock *reading* recorded in place of a delta.
    """
    stub.plan({"list": {"stdout": METRICS_OUTPUT, "sleep": SLOW_UND_SLEEP_S}})

    cli(stub, log).list_metrics(db_path(tmp_path))

    assert len(log.entries) == 1, "one command in, one line out"
    (_, seconds, rc) = log.entries[-1]
    assert rc == 0
    assert seconds >= SLOW_UND_FLOOR_S, f"a call that slept {SLOW_UND_SLEEP_S}s logged {seconds}s"
    assert seconds < CLOCK_READING_CEILING_S, f"{seconds}s is a clock reading, not a duration"


def test_the_two_recorded_statuses_are_the_conventional_numbers() -> None:
    """The statuses this wrapper logs for an ``und`` that was killed or never started.

    Pinned as literals here because the names are now imported from
    :mod:`scitools_hook.exit_codes` rather than defined in the wrapper (task 11.2): ``git``,
    the API worker and the installation probes write the same two numbers into the same
    ``--verbose`` stream, and an operator who has learnt that 124 means "killed" and 127 means
    "never started" must not have to learn a different pair per adapter. The behavioural tests
    assert the same two literals where they are recorded; this one is about the values the
    module exports, which is what the other consumers import.
    """
    assert TIMEOUT_RC == TIMEOUT_KILLED_STATUS
    assert MISSING_RC == SHELL_COMMAND_NOT_FOUND_STATUS


def test_a_failing_command_is_recorded_before_the_error_is_raised(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    stub.plan({"analyze": {"stderr": BAD_DB_STDERR, "rc": 1}})
    with pytest.raises(AnalysisFailedError):
        cli(stub, log).analyze(db_path(tmp_path), None, all=True)
    assert log.codes == [1]
    (_, seconds, _) = log.entries[-1]
    assert seconds > 0.0
    assert seconds < CLOCK_READING_CEILING_S


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
