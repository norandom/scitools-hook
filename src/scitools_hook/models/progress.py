"""Ports for user-visible side effects, so the core stays free of terminals and clocks.

``Progress`` reports long phases (req 4.11: a phase over five seconds prints a line) and
``CommandLog`` records every external command with its timing (req 12.8). Both are
``Protocol``s: the runner injects a real implementation, tests inject a recorder, and the
no-op implementations here are the default so no component has to check for ``None``.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Progress(Protocol):
    """Reports the progress of long-running phases to the user (stderr, req 4.11)."""

    def start(self, phase: str) -> None:
        """A phase begins."""

    def finish(self, phase: str, seconds: float) -> None:
        """A phase ended after ``seconds``."""

    def note(self, message: str) -> None:
        """A one-line diagnostic that belongs with the progress output."""


@runtime_checkable
class CommandLog(Protocol):
    """Records every external command the adapters run (req 12.8)."""

    def record(self, argv: list[str], seconds: float, rc: int) -> None:
        """One command finished with return code ``rc`` after ``seconds``."""


class NullProgress:
    """Progress that reports nothing; the default outside the CLI."""

    def start(self, phase: str) -> None:
        """Do nothing."""

    def finish(self, phase: str, seconds: float) -> None:
        """Do nothing."""

    def note(self, message: str) -> None:
        """Do nothing."""


class NullCommandLog:
    """Command log that records nothing; the default when ``--verbose`` is off."""

    def record(self, argv: list[str], seconds: float, rc: int) -> None:
        """Do nothing."""
