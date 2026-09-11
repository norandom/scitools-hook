"""How ``--quiet`` and ``--verbose`` resolve to a standard-output verbosity (task 7.1).

Kept apart from ``test_cli_options.py``, which is already far over the per-file routine
limit; these two tests would have pushed it further.
"""

from __future__ import annotations

from pathlib import Path

from scitools_hook.cli import common
from scitools_hook.report.human import Verbosity


def test_verbose_becomes_verbose_verbosity() -> None:
    """Lean-code requirement 8.2's worked example is printed at this verbosity and nowhere
    else, and until task 7.1 no command line produced it."""
    assert common.GlobalOptions(cwd=Path("."), env={}, verbose=True).verbosity is Verbosity.VERBOSE


def test_quiet_beats_verbose_on_standard_output() -> None:
    """The documented precedence: quiet first, then verbose, then normal.

    The opposite of the diagnostic stream, where ``--verbose`` overrides ``--quiet`` (see
    ``test_cli_options.py``). On stdout ``--quiet`` is a promise about what a caller has to
    read -- the summary and the blocking findings -- and five lines of example under a
    warning would break it.
    """
    both = common.GlobalOptions(cwd=Path("."), env={}, quiet=True, verbose=True)
    assert both.verbosity is Verbosity.QUIET
