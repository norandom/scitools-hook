"""Exit-code mapping and error rendering for every failure the CLI can reach (task 9.1).

Three properties are pinned here, each of which the requirements name explicitly:

* **every** ``GateError`` subclass exits with its own documented code (req 1.6). The set of
  classes is discovered reflectively rather than listed, so a subclass added to
  ``errors.py`` tomorrow is covered by these tests the moment it exists -- a hand-written
  table would silently stop covering the hierarchy it claims to.
* an *unexpected* error is a one-line message naming the exception type, with the traceback
  only under ``--verbose``, and exits 70 -- distinct from the analysis-failure code 5
  (req 12.7, reconciled in implementation note 1.2).
* findings never share standard output with diagnostics: an error writes to stderr and
  leaves stdout empty (req 7.7).
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import pytest
import typer
from typer.testing import CliRunner

from scitools_hook import errors
from scitools_hook.cli import common
from scitools_hook.errors import (
    AnalysisFailedError,
    ArchitectureNotFoundError,
    ConfigError,
    GateError,
    LicenseError,
    NotAGitRepositoryError,
    UnderstandNotFoundError,
)
from scitools_hook.exit_codes import ExitCode

RAISER = "raise_the_error_under_test"
"""Name of the frame the traceback must mention, so ``--verbose`` is checked on content."""


def gate_error_subclasses() -> list[type[GateError]]:
    """Every ``GateError`` subclass defined in ``errors.py``, discovered reflectively.

    Implementation note 1.2 requires the whole hierarchy to live in that module precisely so
    this walk can see all of it; a class defined elsewhere is deliberately ignored rather
    than silently widening the contract these tests claim to cover.
    """
    found: dict[str, type[GateError]] = {}
    pending: list[type[GateError]] = [GateError]
    while pending:
        for subclass in pending.pop().__subclasses__():
            if subclass.__module__ != errors.__name__ or subclass.__name__ in found:
                continue
            found[subclass.__name__] = subclass
            pending.append(subclass)
    return sorted(found.values(), key=lambda cls: cls.__name__)


SUBCLASSES = gate_error_subclasses()
IDS = [cls.__name__ for cls in SUBCLASSES]


def app_that_raises(error: BaseException) -> typer.Typer:
    """A minimal application wired exactly as ``cli/app.py`` wires the real one."""
    app = typer.Typer(
        cls=common.GateGroup,
        name="probe",
        rich_markup_mode=None,
        add_completion=False,
        pretty_exceptions_enable=False,
    )

    @app.callback()
    def root(
        ctx: typer.Context,
        verbose: Annotated[bool, typer.Option("--verbose")] = False,
    ) -> None:
        """Probe root."""
        ctx.obj = common.GlobalOptions(cwd=Path("."), env={}, verbose=verbose)

    @app.command()
    def boom() -> None:
        """Raise the error under test."""

        def raise_the_error_under_test() -> None:
            raise error

        raise_the_error_under_test()

    return app


# --- the mapping itself ---------------------------------------------------------


def test_the_hierarchy_is_not_empty() -> None:
    """A reflective test that discovered nothing would pass while proving nothing."""
    assert len(SUBCLASSES) >= 6


@pytest.mark.parametrize("subclass", SUBCLASSES, ids=IDS)
def test_exit_code_for_reads_the_class_attribute(subclass: type[GateError]) -> None:
    assert common.exit_code_for(subclass("boom")) is subclass.exit_code


@pytest.mark.parametrize("subclass", SUBCLASSES, ids=IDS)
def test_each_error_class_exits_with_its_own_code(subclass: type[GateError]) -> None:
    result = CliRunner().invoke(app_that_raises(subclass("boom")), ["boom"])
    assert result.exit_code == int(subclass.exit_code)
    assert result.stdout == ""
    assert "boom" in result.stderr


def test_the_subclasses_cover_every_failure_exit_code() -> None:
    """Every code except the two success-ish ones is reachable through a typed error."""
    codes = {cls.exit_code for cls in SUBCLASSES}
    codes.add(GateError.exit_code)
    assert codes == {code for code in ExitCode if code not in (ExitCode.OK, ExitCode.VIOLATIONS)}


def test_documented_codes_are_the_ones_the_cli_uses() -> None:
    assert ConfigError.exit_code == ExitCode.CONFIG_ERROR == 2
    assert UnderstandNotFoundError.exit_code == ExitCode.UNDERSTAND_NOT_FOUND == 3
    assert LicenseError.exit_code == ExitCode.LICENSE_UNAVAILABLE == 4
    assert AnalysisFailedError.exit_code == ExitCode.ANALYSIS_FAILED == 5
    assert NotAGitRepositoryError.exit_code == ExitCode.NOT_A_GIT_REPO == 6


def test_architecture_not_found_keeps_the_configuration_code() -> None:
    """It is a ``ConfigError``; inheriting the code is the point of the hierarchy."""
    assert common.exit_code_for(ArchitectureNotFoundError("nope")) is ExitCode.CONFIG_ERROR


# --- unexpected errors ----------------------------------------------------------


def test_unexpected_error_exits_seventy_with_a_one_line_message() -> None:
    result = CliRunner().invoke(app_that_raises(RuntimeError("kaboom")), ["boom"])
    assert result.exit_code == int(ExitCode.UNEXPECTED) == 70
    assert result.stdout == ""
    assert "RuntimeError" in result.stderr
    assert "kaboom" in result.stderr


def test_unexpected_error_prints_no_traceback_without_verbose() -> None:
    result = CliRunner().invoke(app_that_raises(RuntimeError("kaboom")), ["boom"])
    assert "Traceback" not in result.stderr
    assert RAISER not in result.stderr


def test_unexpected_error_prints_the_traceback_under_verbose() -> None:
    result = CliRunner().invoke(app_that_raises(RuntimeError("kaboom")), ["--verbose", "boom"])
    assert result.exit_code == 70
    assert "Traceback (most recent call last)" in result.stderr
    assert RAISER in result.stderr
    assert result.stdout == ""


def test_the_unexpected_code_is_distinct_from_the_analysis_failure_code() -> None:
    """Requirement 12.7's explicit demand, reconciled to 70 by implementation note 1.2."""
    assert ExitCode.UNEXPECTED != ExitCode.ANALYSIS_FAILED
    unexpected = CliRunner().invoke(app_that_raises(RuntimeError("x")), ["boom"])
    failed = CliRunner().invoke(app_that_raises(AnalysisFailedError("x")), ["boom"])
    assert unexpected.exit_code == 70
    assert failed.exit_code == 5


def test_a_gate_error_prints_no_traceback_even_under_verbose() -> None:
    """A typed failure is already explained; ``--verbose`` adds commands, not stack noise."""
    result = CliRunner().invoke(app_that_raises(ConfigError("bad key")), ["--verbose", "boom"])
    assert result.exit_code == 2
    assert "Traceback" not in result.stderr
    assert "bad key" in result.stderr


# --- rendering the context each error carries -----------------------------------


def test_understand_not_found_lists_every_location_tried() -> None:
    error = UnderstandNotFoundError(
        "no usable installation",
        hint="set SCITOOLS_HOME or pass --scitools-home",
        tried=["cli:/opt/a", "env:/opt/b", "path:/usr/bin/und"],
    )
    result = CliRunner().invoke(app_that_raises(error), ["boom"])
    assert result.exit_code == 3
    for location in ("cli:/opt/a", "env:/opt/b", "path:/usr/bin/und"):
        assert location in result.stderr
    assert "set SCITOOLS_HOME or pass --scitools-home" in result.stderr


def test_config_error_names_its_file_and_key() -> None:
    error = ConfigError("unknown metric", file=Path("/repo/scitools-hook.toml"), key="thresholds.x")
    result = CliRunner().invoke(app_that_raises(error), ["boom"])
    assert "/repo/scitools-hook.toml" in result.stderr
    assert "thresholds.x" in result.stderr


def test_analysis_failure_shows_the_command_and_its_stderr() -> None:
    error = AnalysisFailedError(
        "und failed", command=["und", "-db", "x y.und", "analyze"], stderr="Error: broken\nline 2"
    )
    result = CliRunner().invoke(app_that_raises(error), ["boom"])
    assert result.exit_code == 5
    assert "'x y.und'" in result.stderr
    assert "Error: broken" in result.stderr
    assert "line 2" in result.stderr


def test_license_error_quotes_what_understand_said() -> None:
    error = LicenseError("no license", und_output="Licensing Error: No license for CodeCheck.")
    result = CliRunner().invoke(app_that_raises(error), ["boom"])
    assert result.exit_code == 4
    assert "Licensing Error: No license for CodeCheck." in result.stderr


def test_architecture_not_found_lists_the_available_ones() -> None:
    error = ArchitectureNotFoundError("no such architecture", available=["Directory Structure"])
    result = CliRunner().invoke(app_that_raises(error), ["boom"])
    assert result.exit_code == 2
    assert "Directory Structure" in result.stderr


def test_an_error_with_no_context_prints_only_the_message() -> None:
    result = CliRunner().invoke(app_that_raises(NotAGitRepositoryError("outside a repo")), ["boom"])
    assert result.exit_code == 6
    assert result.stderr.strip() == "error: outside a repo"


# --- control flow that must NOT be treated as a failure -------------------------


def test_typer_exit_is_not_swallowed_by_the_handler() -> None:
    result = CliRunner().invoke(app_that_raises(typer.Exit(code=1)), ["boom"])
    assert result.exit_code == 1
    assert result.stderr == ""


def test_typer_exit_zero_still_succeeds() -> None:
    result = CliRunner().invoke(app_that_raises(typer.Exit(code=0)), ["boom"])
    assert result.exit_code == 0


def test_a_usage_error_uses_the_configuration_exit_code() -> None:
    """Click's own usage failures exit 2, which is the code the Gate documents for input."""
    result = CliRunner().invoke(app_that_raises(RuntimeError("unused")), ["boom", "--nope"])
    assert result.exit_code == int(ExitCode.CONFIG_ERROR)
    assert "--nope" in result.stderr
    assert result.stdout == ""


# --- the exact shape of a rendered error -----------------------------------------


def test_a_tried_list_renders_as_a_labelled_block_then_the_hint() -> None:
    """Pins the labels, their order, the indentation and the newline that joins them."""
    error = UnderstandNotFoundError(
        "no usable installation", hint="set SCITOOLS_HOME", tried=["/opt/a", "/opt/b"]
    )
    result = CliRunner().invoke(app_that_raises(error), ["boom"])
    assert result.stderr == (
        "error: no usable installation\n"
        "  tried:\n"
        "    - /opt/a\n"
        "    - /opt/b\n"
        "  hint: set SCITOOLS_HOME\n"
    )


def test_a_command_and_its_output_render_as_labelled_blocks() -> None:
    error = AnalysisFailedError(
        "und failed", hint="rebuild the database", command=["und", "a b"], stderr="one\ntwo"
    )
    result = CliRunner().invoke(app_that_raises(error), ["boom"])
    assert result.stderr == (
        "error: und failed\n"
        "  command: und 'a b'\n"
        "  stderr:\n"
        "    one\n"
        "    two\n"
        "  hint: rebuild the database\n"
    )


def test_a_configuration_error_renders_its_file_then_its_key() -> None:
    error = ConfigError("unknown metric", file=Path("/repo/scitools-hook.toml"), key="thresholds.x")
    result = CliRunner().invoke(app_that_raises(error), ["boom"])
    assert result.stderr == (
        "error: unknown metric\n  file: /repo/scitools-hook.toml\n  key: thresholds.x\n"
    )


def test_an_available_list_is_labelled_available() -> None:
    error = ArchitectureNotFoundError("no such architecture", available=["Directory Structure"])
    result = CliRunner().invoke(app_that_raises(error), ["boom"])
    assert result.stderr == (
        "error: no such architecture\n  available:\n    - Directory Structure\n"
    )


def test_understand_output_is_labelled_as_understand_speaking() -> None:
    error = LicenseError("no license", und_output="Licensing Error: no seats")
    result = CliRunner().invoke(app_that_raises(error), ["boom"])
    assert result.stderr == (
        "error: no license\n  understand said:\n    Licensing Error: no seats\n"
    )


def test_the_traceback_lines_are_real_lines() -> None:
    """A traceback joined wrongly still contains its words; its last line pins the join."""
    result = CliRunner().invoke(app_that_raises(RuntimeError("kaboom")), ["--verbose", "boom"])
    lines = result.stderr.splitlines()
    assert lines[0] == "error: RuntimeError: kaboom"
    assert lines[1] == "Traceback (most recent call last):"
    assert lines[-1] == "RuntimeError: kaboom"


# --- contexts that never published the global options ----------------------------


def app_without_global_options() -> typer.Typer:
    """Two commands and no callback, so the group dispatches but publishes nothing."""
    app = typer.Typer(
        cls=common.GateGroup,
        name="bare",
        rich_markup_mode=None,
        add_completion=False,
        pretty_exceptions_enable=False,
    )

    @app.command()
    def boom() -> None:
        """Fail without any global options published."""

        def raise_the_error_under_test() -> None:
            raise RuntimeError("kaboom")

        raise_the_error_under_test()

    @app.command()
    def ask(ctx: typer.Context) -> None:
        """Ask for global options that were never published."""
        common.global_options(ctx)

    return app


def test_asking_for_absent_global_options_is_an_unexpected_error() -> None:
    result = CliRunner().invoke(app_without_global_options(), ["ask"])
    assert result.exit_code == int(ExitCode.UNEXPECTED)
    assert "did not publish the global options" in result.stderr


def test_without_published_options_no_traceback_is_printed() -> None:
    """The verbose decision falls back to off rather than assuming detail was asked for."""
    result = CliRunner().invoke(app_without_global_options(), ["boom"])
    assert result.exit_code == int(ExitCode.UNEXPECTED)
    assert "Traceback" not in result.stderr
