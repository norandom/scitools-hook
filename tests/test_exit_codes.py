"""Exit codes (requirement 1.6) and their mapping from the typed error hierarchy.

Every failure kind must map to exactly one distinct, documented exit code, and
``ExitCode`` must be the single source of truth for the integers.
"""

from __future__ import annotations

import inspect
from enum import IntEnum

import pytest

from scitools_hook import errors
from scitools_hook.errors import (
    AnalysisFailedError,
    ArchitectureNotFoundError,
    ConfigError,
    GateError,
    LicenseError,
    NotAGitRepositoryError,
    UnderstandNotFoundError,
)
from scitools_hook.exit_codes import ExitCode, describe

DOCUMENTED_CODES: dict[str, int] = {
    "OK": 0,
    "VIOLATIONS": 1,
    "CONFIG_ERROR": 2,
    "UNDERSTAND_NOT_FOUND": 3,
    "LICENSE_UNAVAILABLE": 4,
    "ANALYSIS_FAILED": 5,
    "NOT_A_GIT_REPO": 6,
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
