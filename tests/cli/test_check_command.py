"""The ``check`` command: options, renderers, destinations and exit codes (task 9.2).

The pipeline itself is task 8.3's and is tested there; what is tested here is the wiring --
that the option grammar reaches the settings and the pipeline unmangled, that each renderer's
document reaches the destination the operator named, and that the process exit code is the
one requirement 7.9 and requirement 1.6 promise.

Every double records what it was asked and answers *from* it (``fakes.cli``), so a command
that drops an option changes the rendered output rather than a counter nobody asserts.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from fakes.cli import StubAssembler, a_finding, a_result, describe
from typer.testing import CliRunner

from scitools_hook.cli import app as app_module
from scitools_hook.cli import check as check_module
from scitools_hook.cli import common, pipelines
from scitools_hook.config.defaults import default_settings
from scitools_hook.config.loader import load_settings
from scitools_hook.errors import AnalysisFailedError, LicenseError, ReportUndeliverableError
from scitools_hook.exit_codes import ExitCode
from scitools_hook.report.human import ColorMode, Verbosity, render_human
from scitools_hook.report.json_out import render_json
from scitools_hook.report.sarif import render_sarif
from scitools_hook.runner.pipeline import Selection

BLOCKING = (a_finding(),)
WARNING_ONLY = (a_finding(severity="warning", blocking=False, message="close to the limit"),)


@pytest.fixture
def assembler(monkeypatch: pytest.MonkeyPatch) -> StubAssembler:
    """Replace the real assembly, so no git repository and no Understand are needed."""
    stub = StubAssembler()
    monkeypatch.setattr(pipelines, "assemble", stub)
    return stub


def run(*args: str) -> object:
    """Invoke the real application with ``args``."""
    return CliRunner().invoke(app_module.app, list(args))


# --- the help documents every option (req 12.1) -----------------------------------


CHECK_OPTIONS = (
    "--staged",
    "--worktree",
    "--all",
    "--files",
    "--format",
    "--output",
    "--strict",
    "--adaptive",
    "--no-adaptive",
    "--show-highest",
    "--sarif",
)


@pytest.mark.parametrize("option", CHECK_OPTIONS)
def test_the_help_documents_every_option_check_accepts(option: str) -> None:
    result = run("check", "--help")
    assert result.exit_code == 0
    assert option in result.stdout


def test_the_help_names_the_formats_check_can_actually_render() -> None:
    """Requirement 12.4's "where applicable": there is no Markdown view of findings."""
    result = run("check", "--help")
    assert "human" in result.stdout
    assert "json" in result.stdout
    assert "sarif" in result.stdout


def test_check_offers_exactly_the_formats_it_has_renderers_for() -> None:
    offered = {member.value for member in check_module.CheckFormat}
    assert offered == {"human", "json", "sarif"}
    assert offered < {member.value for member in common.OutputFormat}


def test_a_format_check_cannot_render_is_refused_rather_than_ignored() -> None:
    """``markdown`` renders a change summary, which ``check`` does not produce."""
    result = run("check", "--format", "markdown")
    assert result.exit_code == int(ExitCode.CONFIG_ERROR)
    assert result.stdout == ""


# --- the selection reaches the pipeline (req 12.3, 11.8) --------------------------


SELECTIONS = (
    pytest.param(["--staged"], Selection(mode="staged"), id="staged"),
    pytest.param(["--worktree"], Selection(mode="worktree"), id="worktree"),
    pytest.param(["--all"], Selection(mode="all"), id="all"),
    pytest.param(["--files", "a.py"], Selection(mode="files", files=["a.py"]), id="files"),
    pytest.param(
        ["--files", "a.py", "b.py"],
        Selection(mode="files", files=["a.py", "b.py"]),
        id="files-and-trailing-paths",
    ),
)


@pytest.mark.parametrize(("argv", "expected"), SELECTIONS)
def test_the_selection_the_options_name_is_the_one_the_pipeline_runs(
    assembler: StubAssembler, argv: list[str], expected: Selection
) -> None:
    result = run("check", *argv)
    assert result.exit_code == 0, result.stdout + result.stderr
    assert assembler.assembly.check_pipeline.selections == [expected]


def test_the_runner_selection_is_built_from_the_resolved_choice(
    assembler: StubAssembler,
) -> None:
    """``Selection(mode=choice.mode.value, files=list(choice.files))``, 8.3's handoff."""
    run("check", "--files", "src/a.py")
    only = assembler.assembly.check_pipeline.selections[0]
    assert isinstance(only.mode, str)
    assert only.files == ["src/a.py"]


def test_two_selection_flags_are_refused_before_anything_is_assembled(
    assembler: StubAssembler,
) -> None:
    """``resolve_selection`` stays the first thing the body does, so nothing is built."""
    result = run("check", "--staged", "--all")
    assert result.exit_code == int(ExitCode.CONFIG_ERROR)
    assert assembler.overrides == []
    assert result.stdout == ""
    assert "--staged" in result.stderr


# --- the renderers and their destinations (req 7.3, 7.4, 7.5, 12.4) ---------------


def test_human_findings_go_to_standard_output(assembler: StubAssembler) -> None:
    assembler.assembly.check_pipeline.findings = BLOCKING
    result = run("check", "--all")
    expected = render_human(
        a_result("all", BLOCKING),
        Verbosity.NORMAL,
        ColorMode.OFF,
        True,
        False,
    )
    assert result.stdout == expected + "\n"


def test_the_json_document_is_the_only_thing_on_standard_output(
    assembler: StubAssembler,
) -> None:
    assembler.assembly.check_pipeline.findings = BLOCKING
    result = run("check", "--all", "--format", "json")
    assert result.stdout == render_json(a_result("all", BLOCKING)) + "\n"
    assert json.loads(result.stdout)["schema_version"] == 1


def test_the_sarif_document_can_be_the_report_itself(assembler: StubAssembler) -> None:
    assembler.assembly.check_pipeline.findings = BLOCKING
    result = run("check", "--all", "--format", "sarif")
    expected = a_result("all", BLOCKING)
    assert result.stdout == render_sarif(expected, expected.tool_version) + "\n"


def test_the_report_can_be_written_to_a_file_instead(
    assembler: StubAssembler, tmp_path: Path
) -> None:
    target = tmp_path / "findings.json"
    result = run("check", "--all", "--format", "json", "--output", str(target))
    assert result.stdout == ""
    assert json.loads(target.read_text(encoding="utf-8"))["selection"] == "all"


def test_a_report_that_cannot_be_delivered_names_the_option_that_asked_for_it(
    assembler: StubAssembler, tmp_path: Path
) -> None:
    result = run("check", "--all", "--output", str(tmp_path / "no-such-dir" / "out.txt"))
    assert result.exit_code == int(ExitCode.REPORT_UNDELIVERABLE)
    assert "--output" in result.stderr


# --- the second destination: --sarif PATH beside --format ------------------------


def test_sarif_is_written_beside_the_chosen_format_not_instead_of_it(
    assembler: StubAssembler, tmp_path: Path
) -> None:
    """Two destinations in one run: the human report on stdout, SARIF in the file."""
    assembler.assembly.check_pipeline.findings = BLOCKING
    target = tmp_path / "report.sarif"
    result = run("check", "--all", "--sarif", str(target))
    expected = a_result("all", BLOCKING)
    assert (
        result.stdout == render_human(expected, Verbosity.NORMAL, ColorMode.OFF, True, False) + "\n"
    )
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["version"] == "2.1.0"
    assert len(document["runs"][0]["results"]) == 1


def test_sarif_and_a_json_report_are_two_documents_in_two_places(
    assembler: StubAssembler, tmp_path: Path
) -> None:
    assembler.assembly.check_pipeline.findings = BLOCKING
    target = tmp_path / "report.sarif"
    result = run("check", "--all", "--format", "json", "--sarif", str(target))
    assert json.loads(result.stdout)["schema_version"] == 1
    assert json.loads(target.read_text(encoding="utf-8"))["version"] == "2.1.0"


def test_an_undeliverable_sarif_file_names_the_sarif_option(
    assembler: StubAssembler, tmp_path: Path
) -> None:
    """Not ``--output``: the operator never passed it, and 9.1 fixed exactly that defect."""
    result = run("check", "--all", "--sarif", str(tmp_path / "missing" / "r.sarif"))
    assert result.exit_code == int(ExitCode.REPORT_UNDELIVERABLE)
    assert "--sarif" in result.stderr
    assert "--output" not in result.stderr


def test_a_blocking_run_delivers_its_report_before_it_fails_on_the_sarif_file(
    assembler: StubAssembler, tmp_path: Path
) -> None:
    """The findings are the answer; a second destination failing must not swallow them."""
    assembler.assembly.check_pipeline.findings = BLOCKING
    result = run("check", "--all", "--sarif", str(tmp_path / "missing" / "r.sarif"))
    assert "CyclomaticStrict" in result.stdout
    assert result.exit_code == int(ExitCode.REPORT_UNDELIVERABLE)


def test_a_sarif_destination_that_would_block_names_the_sarif_option(
    assembler: StubAssembler, tmp_path: Path
) -> None:
    """The FIFO guard is carried to the second destination, and so is the option it names.

    Measured before this was fixed: ``check --sarif <fifo>`` was refused with ``key:
    --output``. The kind-based refusal in ``cli/common`` carried the literal spelling, which
    was correct while ``--output`` was the only file destination and wrong the moment this
    task added a second one.
    """
    fifo = tmp_path / "pipe.sarif"
    os.mkfifo(fifo)
    result = run("check", "--all", "--sarif", str(fifo))
    assert result.exit_code == int(ExitCode.REPORT_UNDELIVERABLE)
    assert "--sarif" in result.stderr
    assert "--output" not in result.stderr
    assert "named pipe" in result.stderr


def test_a_blocking_destination_still_names_output_when_that_is_what_was_given(
    assembler: StubAssembler, tmp_path: Path
) -> None:
    """The other side of the same discriminator, so neither spelling can be hard-coded."""
    fifo = tmp_path / "pipe.txt"
    os.mkfifo(fifo)
    result = run("check", "--all", "--output", str(fifo))
    assert result.exit_code == int(ExitCode.REPORT_UNDELIVERABLE)
    assert "--output" in result.stderr
    assert "--sarif" not in result.stderr


def test_a_sarif_destination_that_stores_nothing_is_the_operators_choice(
    assembler: StubAssembler,
) -> None:
    """``/dev/null`` is a discard on this option too; nothing can tell it from a mistake."""
    assembler.assembly.check_pipeline.findings = BLOCKING
    result = run("check", "--all", "--sarif", "/dev/null")
    assert result.exit_code == int(ExitCode.VIOLATIONS)


# --- the settings overrides (req 4.7, 5.6, 8.2) ----------------------------------


def test_no_flag_overrides_nothing(assembler: StubAssembler) -> None:
    """An absent flag must not push a value: it would outrank the configuration file."""
    run("check", "--all")
    assert assembler.last_overrides == {}


def test_strict_reaches_the_settings_rather_than_the_pipeline(
    assembler: StubAssembler,
) -> None:
    run("check", "--all", "--strict")
    assert assembler.last_overrides == {"ratchet.strict": True}


@pytest.mark.parametrize(("flag", "expected"), [("--adaptive", True), ("--no-adaptive", False)])
def test_the_adaptive_flag_carries_both_answers(
    assembler: StubAssembler, flag: str, expected: bool
) -> None:
    run("check", "--all", flag)
    assert assembler.last_overrides == {"baseline.adaptive": expected}


def test_show_highest_reaches_the_settings(assembler: StubAssembler) -> None:
    run("check", "--all", "--show-highest")
    assert assembler.last_overrides == {"output.show_highest": True}


def test_every_override_key_names_a_setting_that_exists() -> None:
    """The dotted spellings are asserted against the loader, not against themselves."""
    overrides: dict[str, object] = {
        "ratchet.strict": True,
        "baseline.adaptive": True,
        "output.show_highest": True,
    }
    settings, _ = load_settings(None, overrides, {})
    assert settings.ratchet.strict is True
    assert settings.baseline.adaptive is True
    assert settings.output.show_highest is True


def test_the_highest_values_section_follows_the_effective_setting(
    assembler: StubAssembler,
) -> None:
    """``--show-highest`` is a setting, so a configuration file must be able to set it too."""
    assembler.assembly.ctx.settings = assembler.assembly.ctx.settings.model_copy(
        update={
            "output": assembler.assembly.ctx.settings.output.model_copy(
                update={"show_highest": True}
            )
        }
    )
    result = run("check", "--all")
    expected = render_human(a_result("all"), Verbosity.NORMAL, ColorMode.OFF, True, True)
    assert result.stdout == expected + "\n"


class Terminal:
    """A standard output that says it is a terminal, so the colour decision has two answers."""

    def isatty(self) -> bool:
        """Yes: this is what makes the two branches of ``color_for`` distinguishable."""
        return True

    def write(self, text: str) -> int:
        """Accept anything; nothing here reads it back."""
        return len(text)

    def flush(self) -> None:
        """Nothing is buffered."""


def test_a_report_going_to_a_file_is_never_coloured_even_from_a_terminal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``--output`` is not a terminal; asking ``sys.stdout`` would colour a file (9.1)."""
    monkeypatch.setattr(sys, "stdout", Terminal())
    options = common.GlobalOptions(cwd=tmp_path, env={})
    result = a_result("all", BLOCKING)
    settings = default_settings()
    on_terminal = check_module.render(
        result, check_module.CheckFormat.HUMAN, settings, options, None
    )
    to_file = check_module.render(
        result, check_module.CheckFormat.HUMAN, settings, options, tmp_path / "report.txt"
    )
    assert "\x1b[" in on_terminal
    assert "\x1b[" not in to_file


def test_quiet_reaches_the_renderer(assembler: StubAssembler) -> None:
    assembler.assembly.check_pipeline.findings = WARNING_ONLY
    result = run("--quiet", "check", "--all")
    expected = render_human(
        a_result("all", WARNING_ONLY), Verbosity.QUIET, ColorMode.OFF, True, False
    )
    assert result.stdout == expected + "\n"


# --- the exit codes are the contract (req 7.9, 1.6, 12.5) ------------------------


def test_a_clean_run_exits_zero(assembler: StubAssembler) -> None:
    result = run("check", "--all")
    assert result.exit_code == int(ExitCode.OK)


def test_a_run_with_only_warnings_exits_zero(assembler: StubAssembler) -> None:
    assembler.assembly.check_pipeline.findings = WARNING_ONLY
    result = run("check", "--all")
    assert result.exit_code == int(ExitCode.OK)


def test_a_blocking_finding_exits_with_the_violations_code(
    assembler: StubAssembler,
) -> None:
    assembler.assembly.check_pipeline.findings = BLOCKING
    result = run("check", "--all")
    assert result.exit_code == int(ExitCode.VIOLATIONS)


TYPED_ERRORS = (
    pytest.param(AnalysisFailedError("und failed"), ExitCode.ANALYSIS_FAILED, id="analysis"),
    pytest.param(LicenseError("no license"), ExitCode.LICENSE_UNAVAILABLE, id="license"),
    pytest.param(
        ReportUndeliverableError("disk full"), ExitCode.REPORT_UNDELIVERABLE, id="undeliverable"
    ),
)


@pytest.mark.parametrize(("error", "expected"), TYPED_ERRORS)
def test_a_typed_error_from_the_pipeline_keeps_its_own_exit_code(
    assembler: StubAssembler, error: Exception, expected: ExitCode
) -> None:
    """``GateGroup`` maps these; the command adds no ``try``/``except`` of its own."""
    assembler.assembly.check_pipeline.error = error
    result = run("check", "--all")
    assert result.exit_code == int(expected)
    assert result.stdout == ""


def test_an_unexpected_error_is_not_confused_with_an_analysis_failure(
    assembler: StubAssembler,
) -> None:
    assembler.assembly.check_pipeline.error = RuntimeError("boom")
    result = run("check", "--all")
    assert result.exit_code == int(ExitCode.UNEXPECTED)
    assert "RuntimeError" in result.stderr


def test_check_outside_a_repository_exits_with_the_not_a_git_repository_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requirement 12.5, and it must not depend on whether Understand is installed."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SCITOOLS_HOME", str(tmp_path / "no-understand-here"))
    result = run("check", "--all")
    assert result.exit_code == int(ExitCode.NOT_A_GIT_REPO)
    assert result.stdout == ""
    assert "git working tree" in result.stderr


def test_the_selection_is_described_in_the_document(assembler: StubAssembler) -> None:
    """The stub answers from what it was asked, so this fails if the selection is dropped."""
    result = run("check", "--files", "one.py", "--format", "json")
    assert json.loads(result.stdout)["selection"] == describe(
        Selection(mode="files", files=["one.py"])
    )
