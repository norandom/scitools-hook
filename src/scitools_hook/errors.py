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


class ReportUndeliverableError(GateError):
    """The analysis ran and produced findings, but the report could not be written.

    A separate code from ``ConfigError`` because the two ask the operator for different
    things, and conflating them made the gate say something untrue. A full device, an exceeded
    quota, or a standard output redirected into a closed pipe are not configuration mistakes
    -- ``/dev/full`` is an existing, writable path -- and the previous answer, exit 2
    "configuration error", sent a hook author looking for a bad setting they did not have.
    Exit 70 "unexpected internal error" was worse on the standard-output side: it invited a
    bug report about a full disk.

    **The distinction is worth a code because the two need different automation.** A
    configuration error is fixed by editing configuration and will fail identically on the next
    run; an undelivered report is an environment fault that a retry may well clear, and CI can
    reasonably treat it as infrastructure rather than as a rejected commit. What it must never
    become is exit 1: the findings were never delivered, so reporting them as blocking
    violations would fail a commit over something nobody measured -- the same reasoning that
    kept ``typer.Abort`` out of the control-flow tuple.
    """

    exit_code = ExitCode.REPORT_UNDELIVERABLE

    def __init__(self, message: str, **context: Unpack[ConfigContext]) -> None:
        """Takes the same locating context as :class:`ConfigError` without being one.

        ``key`` and ``file`` are duplicated rather than inherited because the *code* is the
        whole point of this class: subclassing ``ConfigError`` to reuse three lines of
        ``__init__`` would give every delivery failure ``ConfigError``'s exit code again
        through ``isinstance``, and ``exit_code_for`` reads ``type(error).exit_code``. The
        fields themselves are needed for the same reason they are needed there --
        ``cli.common._context_lines`` renders ``key``, so an operator is told which option
        named the destination that could not be written.
        """
        super().__init__(message, hint=context.get("hint"))
        self.file = context.get("file")
        self.key = context.get("key")


class ArchitectureNotFoundError(ConfigError):
    """A configured architecture does not exist; ``available`` lists the ones that do."""

    def __init__(
        self, message: str, *, available: Sequence[str] = (), **context: Unpack[ConfigContext]
    ) -> None:
        super().__init__(message, **context)
        self.available = list(available)
