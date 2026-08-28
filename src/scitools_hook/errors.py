"""Typed error hierarchy; each class carries its exit code and its context fields.

Rendering (messages, hints, tracebacks) lives in the CLI layer; these classes only
hold the data needed to render. ``exit_code`` is a class attribute so callers can
map a caught error without instantiating anything.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar, TypedDict, Unpack

from scitools_hook.exit_codes import ExitCode


class GateError(Exception):
    """Base of all gate failures; ``str(err)`` is the plain message."""

    exit_code: ClassVar[ExitCode] = ExitCode.UNEXPECTED

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:
        return self.message


class ConfigContext(TypedDict, total=False):
    """Optional keyword context accepted by ``ConfigError`` and its subclasses."""

    hint: str | None
    file: Path | None
    key: str | None


class ConfigError(GateError):
    """Invalid configuration; ``file`` and ``key`` locate the offending setting."""

    exit_code = ExitCode.CONFIG_ERROR

    def __init__(self, message: str, **context: Unpack[ConfigContext]) -> None:
        super().__init__(message, hint=context.get("hint"))
        self.file = context.get("file")
        self.key = context.get("key")


class UnderstandNotFoundError(GateError):
    """No usable Understand installation; ``tried`` lists every location checked."""

    exit_code = ExitCode.UNDERSTAND_NOT_FOUND

    def __init__(self, message: str, *, hint: str | None = None, tried: Sequence[str] = ()) -> None:
        super().__init__(message, hint=hint)
        self.tried = list(tried)


class LicenseError(GateError):
    """Understand reported no valid license; ``und_output`` quotes what it said."""

    exit_code = ExitCode.LICENSE_UNAVAILABLE

    def __init__(self, message: str, *, hint: str | None = None, und_output: str = "") -> None:
        super().__init__(message, hint=hint)
        self.und_output = und_output


class AnalysisFailedError(GateError):
    """An external analysis step failed; ``command`` and ``stderr`` say which and why."""

    exit_code = ExitCode.ANALYSIS_FAILED

    def __init__(
        self,
        message: str,
        *,
        hint: str | None = None,
        command: Sequence[str] = (),
        stderr: str = "",
    ) -> None:
        super().__init__(message, hint=hint)
        self.command = list(command)
        self.stderr = stderr


class NotAGitRepositoryError(GateError):
    """A subcommand that needs git was run outside a git repository."""

    exit_code = ExitCode.NOT_A_GIT_REPO


class ArchitectureNotFoundError(ConfigError):
    """A configured architecture does not exist; ``available`` lists the ones that do."""

    def __init__(
        self, message: str, *, available: Sequence[str] = (), **context: Unpack[ConfigContext]
    ) -> None:
        super().__init__(message, **context)
        self.available = list(available)
