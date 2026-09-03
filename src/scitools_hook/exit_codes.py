"""Process statuses: the gate's own exit codes, and the two it records for child processes.

Two kinds of number live here, and the distinction is the point of keeping them together
rather than the reason to split them:

* :class:`ExitCode` is what **this** process exits with (requirement 1.6). Every failure kind
  maps to exactly one distinct, documented code.
* :data:`TIMEOUT_RC` and :data:`MISSING_RC` are what the gate **records** for a child process
  that had to be killed or could never be started (requirement 12.8). They are written to the
  ``--verbose`` command log and are never this process's own status.

Nothing here imports the rest of the package, which is what lets every layer import it. That
is also why the two recorded statuses live here rather than in the adapters that write them:
:mod:`scitools_hook.paths` was moved to this same root tier after the same helper was copied
into four layers and two of the copies were fixed while the others were not. A constant that
four layers share has to sit below all four, or it gets spelled four times -- and it had been:
``git.repo``, ``understand.und_cli`` and ``understand.api_runner`` each defined this pair, and
task 11.2 added a fourth consumer in ``runner.context``.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Final

TIMEOUT_RC: Final = 124
"""Status recorded for a child process that had to be killed; GNU ``timeout(1)``'s convention.

**One convention across the whole tool**, which is why it is defined once here rather than per
adapter. The ``--verbose`` log mixes ``git``, ``und``, the API worker and the installation
probes in one stream, so an operator who has learnt that 124 means "killed at its limit" must
not have to learn a different number per adapter. 124 is the status GNU ``timeout(1)`` reports
for a command it killed, so the number is one an operator already reads that way.

It is deliberately outside :class:`ExitCode`: this is a status the gate *writes down about
something else*, never one it exits with. ``ExitCode`` has no member with this value and the
census test in ``tests/test_exit_codes.py`` keeps the two sets disjoint.
"""

MISSING_RC: Final = 127
"""Status recorded for a child process that never started; the shell's "not found" convention.

The counterpart of :data:`TIMEOUT_RC`, held to the same rule for the same reason. 127 is what a
shell reports for a command it could not execute, so recording it says "this never ran" in the
vocabulary the operator already has -- where recording 0 would report an executable that was
never found as a success.
"""


class ExitCode(IntEnum):
    """Distinct exit codes; the integers are part of the documented CLI contract."""

    OK = 0
    VIOLATIONS = 1
    CONFIG_ERROR = 2
    UNDERSTAND_NOT_FOUND = 3
    LICENSE_UNAVAILABLE = 4
    ANALYSIS_FAILED = 5
    NOT_A_GIT_REPO = 6
    REPORT_UNDELIVERABLE = 7
    UNEXPECTED = 70


_DESCRIPTIONS: dict[ExitCode, str] = {
    ExitCode.OK: "no blocking violations",
    ExitCode.VIOLATIONS: "blocking violations found",
    ExitCode.CONFIG_ERROR: "configuration error (unknown key, metric, scope, regex, architecture)",
    ExitCode.UNDERSTAND_NOT_FOUND: "no usable SciTools Understand installation found",
    ExitCode.LICENSE_UNAVAILABLE: "Understand reported no valid license",
    ExitCode.ANALYSIS_FAILED: "analysis failed (und error, timeout or unusable database)",
    ExitCode.NOT_A_GIT_REPO: "not inside a git repository",
    ExitCode.REPORT_UNDELIVERABLE: "the analysis ran but its report could not be delivered",
    ExitCode.UNEXPECTED: "unexpected internal error",
}


def describe(code: ExitCode | int) -> str:
    """Return the one-line documented meaning of ``code``.

    Accepts a plain integer as well; raises ``ValueError`` for an unknown code.
    """
    return _DESCRIPTIONS[ExitCode(code)]
