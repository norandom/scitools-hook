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

LEAN_REFERENCE_RULES: Final[tuple[str, ...]] = (
    "unused_parameters",
    "unused_classes",
    "unused_variables",
    "pass_through",
    "single_implementation",
)
"""The lean rules that need the per-entity reference walk, and therefore a new snapshot.

``over_export`` is not here: it reads file metrics, ``file_edges`` and the definitions walk,
so it turns on the fingerprint's ``definitions`` key and no reference call. Written once and
shared because the settings tests, the fingerprint tests, the feature-refusal tests and the
lean pipeline tests each parametrise over this list, and a draft in which two of them
disagreed is exactly the defect this feature's review caught.

**Written out here rather than imported from** ``config.models.REFERENCE_RULES``, which the
production code reads for the same question. Imported, a rule dropped from that constant
would silently drop every case that parametrises over it, and the suite would go green over
four rules while claiming five. So this is an independent statement of the same list, and
``test_the_suite_and_the_settings_name_the_same_reference_rules`` is what binds the two.
"""

LEAN_TOKEN_RULES: Final[tuple[str, ...]] = ("duplicates", "similar_routines")
"""The lean rules answered from the token index -- a second pass over every file."""

LEAN_RULE_SWITCHES: Final[tuple[str, ...]] = (
    *LEAN_REFERENCE_RULES,
    "over_export",
    *LEAN_TOKEN_RULES,
)
"""Every ``Severity | None`` switch in ``[lean]``; each must ship off (requirement 9.1)."""
