"""The ``baseline`` command: where the capture is written and what it reports (task 9.2).

Requirement 8.1 asks for the current maximum of every configured threshold, recorded "in a
baseline file at a location the operator chooses, defaulting to a repository-level file".
The command owns the choosing; :class:`~scitools_hook.runner.baseline_cmd.BaselineCmd` owns
the capture, including what ``None`` resolves to.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fakes.cli import StubAssembler
from typer.testing import CliRunner

from scitools_hook.cli import app as app_module
from scitools_hook.cli import baseline as baseline_module
from scitools_hook.cli import pipelines
from scitools_hook.errors import AnalysisFailedError, LicenseError
from scitools_hook.exit_codes import ExitCode
from scitools_hook.models.baseline import Baseline
from scitools_hook.runner.baseline_cmd import BaselineCapture


@pytest.fixture
def assembler(monkeypatch: pytest.MonkeyPatch) -> StubAssembler:
    """Replace the real assembly, so no git repository and no Understand are needed."""
    stub = StubAssembler()
    monkeypatch.setattr(pipelines, "assemble", stub)
    return stub


def run(*args: str) -> object:
    """Invoke the real application with ``args``."""
    return CliRunner().invoke(app_module.app, list(args))


# --- the help (req 12.1) ----------------------------------------------------------


def test_the_help_documents_the_file_option() -> None:
    result = run("baseline", "--help")
    assert result.exit_code == 0
    assert "--file" in result.stdout


# --- where the capture goes (req 8.1) ---------------------------------------------


def test_without_the_option_the_command_lets_the_configuration_decide(
    assembler: StubAssembler,
) -> None:
    """``None`` is not a path this command invents: 8.4 resolves it against the repository."""
    result = run("baseline")
    assert result.exit_code == int(ExitCode.OK)
    assert assembler.assembly.baseline_command.paths == [None]


def test_an_explicit_file_is_passed_through_exactly_as_typed(
    assembler: StubAssembler, tmp_path: Path
) -> None:
    """A command-line path means what it means where it was typed; nothing re-roots it."""
    target = tmp_path / "sub" / "chosen.json"
    result = run("baseline", "--file", str(target))
    assert result.exit_code == int(ExitCode.OK)
    assert assembler.assembly.baseline_command.paths == [target]


def test_a_relative_file_is_not_resolved_by_the_command(assembler: StubAssembler) -> None:
    run("baseline", "--file", "limits.json")
    assert assembler.assembly.baseline_command.paths == [Path("limits.json")]


# --- what the run reports (req 8.1) -----------------------------------------------


def test_the_answer_names_the_file_and_how_much_it_holds(
    assembler: StubAssembler, tmp_path: Path
) -> None:
    target = tmp_path / "baseline.json"
    result = run("baseline", "--file", str(target))
    assert result.stdout == f"recorded 1 limit in {target}\n"


def test_a_run_that_analysed_nothing_says_so_instead_of_claiming_a_capture(
    assembler: StubAssembler,
) -> None:
    """A baseline that recorded nothing must not read like a finished job."""
    assembler.assembly.baseline_command.written = False
    result = run("baseline")
    assert result.stdout == baseline_module.NOTHING_WRITTEN + "\n"
    assert result.exit_code == int(ExitCode.OK)


def test_the_wording_counts_more_than_one_limit_correctly(
    assembler: StubAssembler, tmp_path: Path
) -> None:
    """The count comes from the captured document, so a plural is a fact, not a guess."""
    target = tmp_path / "b.json"

    def two(path: Path | None = None) -> BaselineCapture:
        return BaselineCapture(
            path=target,
            baseline=Baseline(
                captured_at="2026-01-02T03:04:05+00:00",
                values={"routine.CyclomaticStrict": 12.0, "file.CountLineCode": 400.0},
            ),
            missing=(),
            written=True,
        )

    assembler.assembly.baseline_command.run = two  # type: ignore[method-assign]
    result = run("baseline", "--file", str(target))
    assert result.stdout == f"recorded 2 limits in {target}\n"


# --- exit codes (req 1.6, 12.5) ---------------------------------------------------


TYPED_ERRORS = (
    pytest.param(AnalysisFailedError("und failed"), ExitCode.ANALYSIS_FAILED, id="analysis"),
    pytest.param(LicenseError("no license"), ExitCode.LICENSE_UNAVAILABLE, id="license"),
)


@pytest.mark.parametrize(("error", "expected"), TYPED_ERRORS)
def test_a_typed_error_keeps_its_own_exit_code(
    assembler: StubAssembler, error: Exception, expected: ExitCode
) -> None:
    assembler.assembly.baseline_command.error = error
    result = run("baseline")
    assert result.exit_code == int(expected)
    assert result.stdout == ""


def test_baseline_outside_a_repository_exits_with_the_not_a_git_repository_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SCITOOLS_HOME", str(tmp_path / "no-understand-here"))
    result = run("baseline")
    assert result.exit_code == int(ExitCode.NOT_A_GIT_REPO)
    assert result.stdout == ""
