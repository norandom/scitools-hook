"""Exit codes (requirement 1.6) and their mapping from the typed error hierarchy.

Every failure kind must map to exactly one distinct, documented exit code, and
``ExitCode`` must be the single source of truth for the integers.

The module also holds the two statuses the gate *records* for a child process that had to be
killed or could never be started (requirement 12.8), so the second half of this file asserts
the property that put them there: one convention across every adapter that writes the
``--verbose`` log, and no overlap with the gate's own exit codes.
"""

from __future__ import annotations

import inspect
from enum import IntEnum

import pytest
from fixtures.constants import SHELL_COMMAND_NOT_FOUND_STATUS, TIMEOUT_KILLED_STATUS

from scitools_hook import errors
from scitools_hook.errors import (
    AnalysisFailedError,
    ArchitectureNotFoundError,
    ConfigError,
    GateError,
    LicenseError,
    NotAGitRepositoryError,
    ReportUndeliverableError,
    UnderstandNotFoundError,
)
from scitools_hook.exit_codes import MISSING_RC, TIMEOUT_RC, ExitCode, describe

DOCUMENTED_CODES: dict[str, int] = {
    "OK": 0,
    "VIOLATIONS": 1,
    "CONFIG_ERROR": 2,
    "UNDERSTAND_NOT_FOUND": 3,
    "LICENSE_UNAVAILABLE": 4,
    "ANALYSIS_FAILED": 5,
    "NOT_A_GIT_REPO": 6,
    "REPORT_UNDELIVERABLE": 7,
    "UNEXPECTED": 70,
}

# (error class, documented exit code) for every class in the hierarchy.
ERROR_CODES: list[tuple[type[GateError], ExitCode]] = [
    (GateError, ExitCode.UNEXPECTED),
    (ConfigError, ExitCode.CONFIG_ERROR),
    (UnderstandNotFoundError, ExitCode.UNDERSTAND_NOT_FOUND),
    (LicenseError, ExitCode.LICENSE_UNAVAILABLE),
    (AnalysisFailedError, ExitCode.ANALYSIS_FAILED),
    (NotAGitRepositoryError, ExitCode.NOT_A_GIT_REPO),
    (ReportUndeliverableError, ExitCode.REPORT_UNDELIVERABLE),
    (ArchitectureNotFoundError, ExitCode.CONFIG_ERROR),
]


def _all_subclasses(cls: type[GateError]) -> set[type[GateError]]:
    found: set[type[GateError]] = set()
    for sub in cls.__subclasses__():
        found.add(sub)
        found |= _all_subclasses(sub)
    return found


# --- ExitCode enum -----------------------------------------------------------


def test_exit_code_is_an_int_enum() -> None:
    assert issubclass(ExitCode, IntEnum)
    assert ExitCode.OK == 0
    assert int(ExitCode.UNEXPECTED) == 70


def test_members_are_exactly_the_documented_codes() -> None:
    assert {member.name: member.value for member in ExitCode} == DOCUMENTED_CODES


def test_values_are_distinct() -> None:
    values = [member.value for member in ExitCode]
    assert len(set(values)) == len(values)


@pytest.mark.parametrize("code", list(ExitCode), ids=lambda c: c.name)
def test_describe_covers_every_member_with_one_line(code: ExitCode) -> None:
    text = describe(code)
    assert isinstance(text, str)
    assert text.strip()
    assert "\n" not in text


def test_descriptions_are_distinct() -> None:
    texts = [describe(code) for code in ExitCode]
    assert len(set(texts)) == len(texts)


def test_describe_accepts_a_plain_integer() -> None:
    assert describe(70) == describe(ExitCode.UNEXPECTED)


def test_describe_rejects_an_unknown_code() -> None:
    with pytest.raises(ValueError):
        describe(99)


# --- the two statuses recorded for child processes (req 12.8) ----------------


def test_the_recorded_child_statuses_are_the_conventional_numbers() -> None:
    """The two numbers the ``--verbose`` log uses for a killed and an unstartable child."""
    assert TIMEOUT_RC == TIMEOUT_KILLED_STATUS
    assert MISSING_RC == SHELL_COMMAND_NOT_FOUND_STATUS


def test_a_recorded_child_status_is_never_one_of_the_gates_own_exit_codes() -> None:
    """The two kinds of number in this module must not be confusable.

    :class:`ExitCode` is what *this* process exits with; :data:`TIMEOUT_RC` and
    :data:`MISSING_RC` are what it *writes down* about a child. Keeping them in one module is
    a decision about placement, not about meaning, so the sets are asserted disjoint: a member
    added at 124 or 127 would make ``describe(MISSING_RC)`` answer, and a status meant for the
    log would start reading like a documented exit code of the gate.
    """
    assert {TIMEOUT_RC, MISSING_RC}.isdisjoint({member.value for member in ExitCode})
    for status in (TIMEOUT_RC, MISSING_RC):
        with pytest.raises(ValueError):
            describe(status)


def test_every_adapter_records_the_same_two_statuses() -> None:
    """One convention across the tool, asserted across all four adapters that write the log.

    The ``--verbose`` stream mixes ``git``, ``und``, the API worker and the installation
    probes, so an operator who has learnt that 124 means "killed" and 127 means "never
    started" must not have to learn a different pair per adapter. **All four now import the
    pair from this module**, which makes divergence impossible rather than merely absent.

    ``git.repo`` was the last exception, for a boundary reason rather than a technical one:
    task 11.2 excluded that module because task 11.1 was landing in it, so its two literals
    stayed a separate definition and this assertion was what stopped the copy from drifting.
    Task 11.3 replaced them with the import. What is left here is a **census**, not a
    drift-guard: it fails if any adapter grows a private copy again, which is the shape the
    duplication took the first time.
    """
    from scitools_hook.git import repo as git_repo
    from scitools_hook.runner import context as run_context
    from scitools_hook.understand import api_runner, und_cli

    recorded = {
        "git.repo": (git_repo.TIMEOUT_RC, git_repo.MISSING_RC),
        "understand.und_cli": (und_cli.TIMEOUT_RC, und_cli.MISSING_RC),
        "understand.api_runner": (api_runner.TIMEOUT_RC, api_runner.MISSING_RC),
        "runner.context": (run_context.TIMEOUT_RC, run_context.MISSING_RC),
    }
    assert set(recorded.values()) == {(TIMEOUT_KILLED_STATUS, SHELL_COMMAND_NOT_FOUND_STATUS)}, (
        f"the adapters disagree about what to record: {recorded}"
    )


# --- error hierarchy -> exit code --------------------------------------------


@pytest.mark.parametrize(("cls", "code"), ERROR_CODES, ids=lambda x: getattr(x, "__name__", x))
def test_error_class_maps_to_its_documented_code(cls: type[GateError], code: ExitCode) -> None:
    assert cls.exit_code is code
    assert cls("boom").exit_code is code


def test_direct_gate_error_subclasses_have_distinct_codes() -> None:
    direct = GateError.__subclasses__()
    codes = [sub.exit_code for sub in direct]
    assert len(set(codes)) == len(codes), codes


def test_architecture_not_found_is_a_config_error_and_shares_its_code() -> None:
    assert issubclass(ArchitectureNotFoundError, ConfigError)
    assert ArchitectureNotFoundError.exit_code is ExitCode.CONFIG_ERROR
    assert ArchitectureNotFoundError.exit_code is ConfigError.exit_code


def test_every_failure_code_has_a_dedicated_error_class() -> None:
    covered = {sub.exit_code for sub in _all_subclasses(GateError)}
    outcome_codes = {ExitCode.OK, ExitCode.VIOLATIONS}
    # UNEXPECTED is reserved for exceptions that are not GateErrors at all.
    assert covered == set(ExitCode) - outcome_codes - {ExitCode.UNEXPECTED}


def test_no_undocumented_error_class_exists() -> None:
    defined = {
        obj
        for _, obj in inspect.getmembers(errors, inspect.isclass)
        if issubclass(obj, GateError) and obj.__module__ == errors.__name__
    }
    assert defined == {cls for cls, _ in ERROR_CODES}


def test_the_package_version_is_the_one_the_distribution_declares() -> None:
    """One version, not two that can drift apart.

    They did drift: `pyproject.toml` reached `0.1.0a1` while `scitools_hook/__init__.py` still
    carried a literal `0.1.0`, so the built wheel installed and ran while reporting the older
    number -- and `__version__` is written into `RunResult.tool_version`, hence into every SARIF
    report. This asserts the module agrees with the distribution metadata, which is what
    `pyproject.toml` produces, so the two cannot disagree again.
    """
    import tomllib
    from importlib.metadata import version
    from pathlib import Path

    import scitools_hook

    declared = tomllib.loads(
        (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]["version"]
    assert scitools_hook.__version__ == version("scitools-hook") == declared
