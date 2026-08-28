"""Process exit codes of the gate: the single source of truth (requirement 1.6).

Every failure kind maps to exactly one distinct, documented code. Later layers
import ``ExitCode`` from here; nothing here imports the rest of the package.
"""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    """Distinct exit codes; the integers are part of the documented CLI contract."""

    OK = 0
    VIOLATIONS = 1
    CONFIG_ERROR = 2
    UNDERSTAND_NOT_FOUND = 3
    LICENSE_UNAVAILABLE = 4
    ANALYSIS_FAILED = 5
    NOT_A_GIT_REPO = 6
    UNEXPECTED = 70


_DESCRIPTIONS: dict[ExitCode, str] = {
    ExitCode.OK: "no blocking violations",
    ExitCode.VIOLATIONS: "blocking violations found",
    ExitCode.CONFIG_ERROR: "configuration error (unknown key, metric, scope, regex, architecture)",
    ExitCode.UNDERSTAND_NOT_FOUND: "no usable SciTools Understand installation found",
    ExitCode.LICENSE_UNAVAILABLE: "Understand reported no valid license",
    ExitCode.ANALYSIS_FAILED: "analysis failed (und error, timeout or unusable database)",
    ExitCode.NOT_A_GIT_REPO: "not inside a git repository",
    ExitCode.UNEXPECTED: "unexpected internal error",
}


def describe(code: ExitCode | int) -> str:
    """Return the one-line documented meaning of ``code``.

    Accepts a plain integer as well; raises ``ValueError`` for an unknown code.
    """
    return _DESCRIPTIONS[ExitCode(code)]
