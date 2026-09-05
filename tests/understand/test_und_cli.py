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

import os
import stat
import sys
from pathlib import Path
from typing import Protocol

import pytest
from fakes import FakeUndCli
from fixtures.constants import SHELL_COMMAND_NOT_FOUND_STATUS, TIMEOUT_KILLED_STATUS
from und_stub import (
    BAD_DB_STDERR,
    NO_COMMAND_OUTPUT,
    VERSION_OUTPUT,
    RecordingLog,
    UndStub,
    cli,
    db_path,
    understand_env,
    write_stub,
)

from scitools_hook.errors import AnalysisFailedError
from scitools_hook.models.progress import CommandLog
from scitools_hook.models.snapshot import ParseError
from scitools_hook.models.understand import AnalyzeResult, LicenseStatus
from scitools_hook.understand.und_cli import (
    MISSING_RC,
    TIMEOUT_RC,
    UndCli,
)


@pytest.fixture
def stub(tmp_path: Path) -> UndStub:
    """A stubbed ``und`` executable with an empty plan, ready to be scripted."""
    return write_stub(tmp_path)


@pytest.fixture
def log() -> RecordingLog:
    """A fresh recording command log (requirement 12.8)."""
    return RecordingLog(entries=[])


# --- transcripts of the real und -----------------------------------------------

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


# --- the python every und call is given -------------------------------------------

# `und` decides the Python dialect by EXECUTING a bare `python` off `PATH`, and analyses a
# Python 2 model when it cannot find one -- which silently drops every routine after the
# first Python 3 construct in a file. The wrapper therefore decides that `PATH` rather than
# inheriting it. What these tests can show is the environment the wrapper hands over; what a
# real database does with it is measured under `tests/contract/`.

DECOY_PYTHON = "#!/bin/sh\necho 'Python 2.7.18'\n"
"""A `python` a developer might already have first on their PATH. Measured against the real
und: a decoy printing exactly this, placed ahead of the pin, gives the Python 2 model."""


def decoy_python_dir(root: Path) -> Path:
    """A directory holding a ``python`` that is not the one the Gate would choose."""
    root.mkdir(parents=True, exist_ok=True)
    decoy = root / "python"
    decoy.write_text(DECOY_PYTHON, encoding="utf-8")
    decoy.chmod(decoy.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return root


def prepend_to_path(monkeypatch: pytest.MonkeyPatch, directory: Path) -> None:
    """Put ``directory`` ahead of the ambient ``PATH``, keeping the rest of it.

    Keeping the rest is not politeness. The stub's own shebang is ``/usr/bin/env python3``,
    so a test that *replaced* ``PATH`` would break its own harness and read the resulting
    127 as evidence about the code under test. This project has been caught by that five
    times; the rule is to add, never to replace.
    """
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ.get('PATH', '')}")


def test_every_und_call_is_given_a_python_the_gate_chose(
    stub: UndStub, log: CommandLog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bare ``python`` resolves to the Gate's own interpreter even with none on ``PATH``.

    The ``PATH`` is emptied of everything for this test, which is the CI-container case: a
    distribution shipping ``python3`` and no ``python``. Leaving the ambient one in place
    would have proved nothing here -- ``uv run`` puts a ``python`` on it, and that is exactly
    the shared setup that hid this defect for the whole build.
    """
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    stub.plan({"version": {"stdout": VERSION_OUTPUT}})

    cli(stub, log).version()

    seen = stub.environment
    assert seen["python"] is not None, "und found no bare python at all"
    assert seen["python_real"] == os.path.realpath(sys.executable)


def test_the_pinned_python_is_ahead_of_one_the_developer_already_has(
    stub: UndStub, log: CommandLog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The decisive case: a wrong ``python`` first on ``PATH`` no longer decides the dialect.

    Before this, ``und`` inherited whatever ``PATH`` the shell had, so a machine carrying a
    Python 2 -- or a distribution carrying no ``python`` at all -- analysed the same commit
    to a different depth than CI did, with nothing in the output naming the reason.
    """
    decoy = decoy_python_dir(tmp_path / "decoy")
    prepend_to_path(monkeypatch, decoy)
    stub.plan({"version": {"stdout": VERSION_OUTPUT}})

    cli(stub, log).version()

    seen = stub.environment
    assert seen["python_real"] == os.path.realpath(sys.executable)
    assert seen["python"] != str(decoy / "python")


def test_the_pinned_directory_is_the_first_entry_and_holds_only_python(
    stub: UndStub, log: CommandLog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First, because measured on the real ``und`` the first ``python`` on ``PATH`` wins."""
    decoy = decoy_python_dir(tmp_path / "decoy")
    prepend_to_path(monkeypatch, decoy)
    stub.plan({"version": {"stdout": VERSION_OUTPUT}})

    cli(stub, log).version()

    entries = str(stub.environment["PATH"]).split(os.pathsep)
    assert str(stub.environment["python"]).startswith(f"{entries[0]}{os.sep}")
    assert entries[1] == str(decoy), "everything the caller had must still be there, behind it"


def test_the_rest_of_the_environment_reaches_und_untouched(
    stub: UndStub, log: CommandLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only ``PATH`` is decided. ``und`` reads its licence and its Qt settings from the rest.

    A wrapper that handed ``und`` a clean environment would be running a different program
    from the one an operator runs, and would fail on the licence before it failed on anything
    this test is about.
    """
    monkeypatch.setenv("SCITOOLS_HOOK_STUB_MARKER", "inherited")
    stub.plan({"version": {"stdout": VERSION_OUTPUT}})

    cli(stub, log).version()

    assert stub.environment["marker"] == "inherited"


def test_the_pinned_directory_does_not_outlive_the_call(stub: UndStub, log: CommandLog) -> None:
    """A hook runs on every commit, so a directory left behind per call would accumulate."""
    stub.plan({"version": {"stdout": VERSION_OUTPUT}})

    cli(stub, log).version()

    pinned = Path(str(stub.environment["PATH"]).split(os.pathsep)[0])
    assert not pinned.exists()


def test_a_python_that_cannot_be_pinned_stops_the_call_instead_of_running_it(
    stub: UndStub, log: CommandLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refusing is the point: an ``und`` on an uncontrolled ``PATH`` may report a green lie.

    Nothing is recorded on the command log either, because nothing was started -- the same
    treatment a list file that cannot be written already gets.
    """
    monkeypatch.setattr(sys, "executable", "")
    stub.plan({"version": {"stdout": VERSION_OUTPUT}})

    with pytest.raises(AnalysisFailedError) as refused:
        cli(stub, log).version()

    assert "cannot be pinned" in str(refused.value)
    assert stub.calls == [], "und must not have run at all"
    assert log.entries == []


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
    """Licensed, with nothing to quote, from the one licence switch the tool runs."""
    status = UndCli(understand_env(sample_databases.und), _null_log()).license_status()
    assert status.ok is True
    assert status.text == ""


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
