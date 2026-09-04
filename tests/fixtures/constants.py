"""Literal values the suite asserts on in more than one module.

Not about the synthetic snapshot project -- that vocabulary is in this package's
``__init__`` -- but shared for the same reason: each of these was written out in five test
modules until ``structure.duplicate_definition`` counted them, and a value duplicated five
times is a value that can be changed in four places and still look right.
"""

from __future__ import annotations

from typing import Final

TIMEOUT_KILLED_STATUS: Final = 124
"""What ``timeout(1)`` exits with when it kills the command it was watching (POSIX)."""

SHELL_COMMAND_NOT_FOUND_STATUS: Final = 127
"""What a shell exits with when the command does not exist (POSIX)."""

STARTED_AT: Final = "2026-01-02T03:04:05+00:00"
"""The frozen clock a run reports, so a report's provenance line is comparable."""

BUILD: Final = "(Build 1204)"
"""The build suffix the fake Understand reports, as the real 6.5.1204 spells it."""
