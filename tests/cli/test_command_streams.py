"""Stream discipline and exit status, measured on real processes with separate pipes.

Requirement 7.4 says the JSON document is the only thing on standard output and requirement
7.7 says diagnostics go to standard error. Neither can be established in-process: a
``CliRunner`` result is two capture buffers that never break, never block and never see the
interpreter's exit-time flush, and an in-process assertion about "the exit code" is an
assertion about an exception object rather than about a process status.

So every test here starts a real interpreter, gives it two real pipes, and reads the status
the operating system reports. The pipeline is still a double -- the same narrow one the
in-process tests use, reached by putting the ``tests`` directory on ``PYTHONPATH`` -- because
what is being measured is the CLI's stream and status behaviour, not Understand's.

The one exception is the not-a-git-repository case, which runs the **real** assembly on
purpose: requirement 12.5's promise is precisely that the answer does not depend on any of
the machinery a double would replace.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scitools_hook.exit_codes import ExitCode

TESTS_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = TESTS_DIR.parent / "src"
TIMEOUT_S = 120
"""A run that takes this long has hung; the double answers in milliseconds."""

DIAGNOSTIC = "progress: assembling the run"
"""What the double says on the diagnostics channel while the command is running."""

BOOTSTRAP = f"""
import os, sys
from fakes.cli import StubAssembler, a_finding
from scitools_hook.cli import common, pipelines
from scitools_hook.cli.app import main

stub = StubAssembler()
if os.environ.get("STUB_BLOCKING") == "1":
    stub.assembly.check_pipeline.findings = (a_finding(),)
record = stub.__call__


def noisy(options, overrides=None):
    common.echo_err({DIAGNOSTIC!r})
    return record(options, overrides)


pipelines.assemble = noisy
sys.argv = ["scitools-hook"] + sys.argv[1:]
main()
"""
"""Runs the real console entry point with the real application and a doubled pipeline."""


def run(*argv: str, blocking: bool = False) -> subprocess.CompletedProcess[str]:
    """Run the CLI in a child process with ``stdout`` and ``stderr`` on separate pipes."""
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": f"{TESTS_DIR}{os.pathsep}{SRC_DIR}",
        "STUB_BLOCKING": "1" if blocking else "0",
        "NO_COLOR": "1",
    }
    return subprocess.run(
        [sys.executable, "-c", BOOTSTRAP, *argv],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
        check=False,
        env=env,
    )


# --- requirement 7.4: nothing but the document ------------------------------------


def test_the_json_document_is_the_whole_of_standard_output() -> None:
    """Parsed from the raw bytes of the pipe, so anything before or after it fails here."""
    done = run("check", "--all", "--format", "json")
    document = json.loads(done.stdout)
    assert document["schema_version"] == 2
    assert done.stdout.startswith("{")
    assert done.stdout.endswith("}\n")


def test_the_diagnostics_of_the_same_run_are_on_standard_error() -> None:
    """The two streams are read separately, so this fails if the diagnostic leaked."""
    done = run("check", "--all", "--format", "json")
    assert DIAGNOSTIC in done.stderr
    assert DIAGNOSTIC not in done.stdout


def test_a_verbose_json_run_still_writes_only_the_document(tmp_path: Path) -> None:
    """``--verbose`` adds the command log, which requirement 12.8 puts on standard error."""
    done = run("--verbose", "check", "--all", "--format", "json")
    assert json.loads(done.stdout)["schema_version"] == 2
    assert DIAGNOSTIC in done.stderr


def test_a_json_report_and_a_sarif_file_do_not_share_standard_output(
    tmp_path: Path,
) -> None:
    """Two destinations in one run: stdout stays a single document, the file gets SARIF."""
    target = tmp_path / "report.sarif"
    done = run("check", "--all", "--format", "json", "--sarif", str(target), blocking=True)
    assert json.loads(done.stdout)["blocking_count"] == 1
    assert json.loads(target.read_text(encoding="utf-8"))["version"] == "2.1.0"
    assert done.returncode == int(ExitCode.VIOLATIONS)


def test_the_summary_of_an_explain_run_is_the_whole_of_standard_output() -> None:
    done = run("explain", "--all", "--format", "json")
    assert json.loads(done.stdout)["db_path"] == "/cache/repo/after.und"
    assert done.returncode == int(ExitCode.OK)


# --- requirement 7.9 and 1.6: the status a process actually exits with ------------


def test_a_clean_run_exits_zero() -> None:
    done = run("check", "--all")
    assert done.returncode == int(ExitCode.OK), done.stderr


def test_a_blocking_run_exits_one() -> None:
    done = run("check", "--all", blocking=True)
    assert done.returncode == int(ExitCode.VIOLATIONS), done.stderr
    assert "CyclomaticStrict" in done.stdout


def test_two_selection_flags_exit_with_the_configuration_code() -> None:
    done = run("check", "--staged", "--all")
    assert done.returncode == int(ExitCode.CONFIG_ERROR)
    assert done.stdout == ""


def test_a_baseline_run_exits_zero_and_reports_where_it_wrote() -> None:
    done = run("baseline")
    assert done.returncode == int(ExitCode.OK), done.stderr
    assert done.stdout.startswith("recorded ")


# --- requirement 12.5, with the real assembly -------------------------------------


REAL = (sys.executable, "-m", "scitools_hook.cli.app")
"""The application as the console script runs it, with nothing substituted."""


@pytest.mark.parametrize("command", ("check", "explain", "baseline"))
def test_a_command_run_outside_a_repository_exits_with_the_git_code(
    command: str, tmp_path: Path
) -> None:
    """No repository is a different answer from no Understand, and it must win.

    ``SCITOOLS_HOME`` names a directory that does not exist, so an implementation that
    located Understand first would exit 3 here instead of 6 -- which is what makes this a
    test of the ordering and not just of the message.
    """
    done = subprocess.run(
        [*REAL, command],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
        check=False,
        env={
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": str(SRC_DIR),
            "SCITOOLS_HOME": str(tmp_path / "no-understand-here"),
            "HOME": str(tmp_path),
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
        },
    )
    assert done.returncode == int(ExitCode.NOT_A_GIT_REPO), done.stderr
    assert done.stdout == ""
    assert "git working tree" in done.stderr
