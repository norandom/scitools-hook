"""The lean-code family's human report through the installed command (lean-code req 7.1,
7.3, 7.6, 8.2).

The fixture and the run are ``lean_fixture``'s; this module reads the text: the net line, the
lean-already line and the worked example at each of the three verbosities.

One property is the one worth stating. **The verbose report is reachable.** Requirement 8.2
puts the worked example in the verbose human output, the renderer has printed it at
``Verbosity.VERBOSE`` since task 2.5, and until task 7.1 no command line produced that
verbosity. ``--verbose`` now does, below ``--quiet`` in precedence, so ``--quiet --verbose`` is
still the summary and the blocking findings.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from e2e.lean_fixture import EXCUSED, NET_LINE, RULE, a_workspace, checked, enabled, lines_of
from scitools_hook.exit_codes import ExitCode
from scitools_hook.report.human import EXAMPLE_LABEL, LEAN_ALREADY_PREFIX
from scitools_hook.report.lean_examples import EXAMPLE_SUFFIX, EXAMPLES

EXAMPLE: Final = EXAMPLES[f"{RULE}{EXAMPLE_SUFFIX}"]
"""The shipped worked example for the rule, which the verbose report must print whole."""


# --- the net line and the lean-already line (7.1, 7.3, 7.6) -------------------------


def test_the_human_report_ends_with_the_net_line(tmp_path: Path) -> None:
    workspace = a_workspace(tmp_path)
    enabled(workspace)

    done = checked(workspace)

    assert done.returncode == int(ExitCode.OK), done.stdout + done.stderr
    assert NET_LINE in lines_of(done), done.stdout


def test_a_forwarding_routine_is_reported_as_a_warning_and_stops_the_lean_already_line(
    tmp_path: Path,
) -> None:
    """Requirement 7.6's second condition: a lean finding was raised, so no congratulation."""
    workspace = a_workspace(tmp_path)
    enabled(workspace)

    done = checked(workspace)

    assert done.returncode == int(ExitCode.OK), done.stdout + done.stderr
    assert RULE in done.stdout
    assert "hint: yagni:" in done.stdout, "the hint opens with its ponytail tag (8.1)"
    assert not any(line.startswith(LEAN_ALREADY_PREFIX) for line in lines_of(done)), done.stdout


def test_with_the_routine_excused_the_report_says_there_is_nothing_to_cut(
    tmp_path: Path,
) -> None:
    """A lean rule looked, found nothing, and the change got shorter: all three of 7.6."""
    workspace = a_workspace(tmp_path)
    enabled(workspace, EXCUSED)

    done = checked(workspace)

    assert done.returncode == int(ExitCode.OK), done.stdout + done.stderr
    lines = lines_of(done)
    assert RULE not in done.stdout
    assert lines[-2] == NET_LINE, done.stdout
    assert lines[-1] == f"{LEAN_ALREADY_PREFIX}-1 lloc", done.stdout


# --- the verbose report is reachable, and quiet still wins (8.2) ------------------


def test_verbose_prints_the_worked_example_under_the_hint(tmp_path: Path) -> None:
    """``--verbose`` before the subcommand, the way every global option goes."""
    workspace = a_workspace(tmp_path)
    enabled(workspace)

    done = checked(workspace, before=("--verbose",))

    assert done.returncode == int(ExitCode.OK), done.stdout + done.stderr
    lines = lines_of(done)
    hint_at = next(index for index, line in enumerate(lines) if "hint: yagni:" in line)
    head, *rest = EXAMPLE.split("\n")
    assert lines[hint_at + 1] == f"    {EXAMPLE_LABEL}{head}", lines[hint_at : hint_at + 3]
    printed = {one.strip() for one in lines}
    for line in rest:
        if line.strip():
            assert line.strip() in printed, f"the example is printed whole: {line!r}"


def test_the_default_report_carries_the_hint_and_not_the_example(tmp_path: Path) -> None:
    workspace = a_workspace(tmp_path)
    enabled(workspace)

    done = checked(workspace)

    assert "hint: yagni:" in done.stdout
    assert EXAMPLE_LABEL not in done.stdout


def test_quiet_with_verbose_still_resolves_to_quiet(tmp_path: Path) -> None:
    """Quiet is the summary and the blocking findings; a warning and its example are neither."""
    workspace = a_workspace(tmp_path)
    enabled(workspace)

    done = checked(workspace, before=("--quiet", "--verbose"))

    assert done.returncode == int(ExitCode.OK), done.stdout + done.stderr
    assert EXAMPLE_LABEL not in done.stdout
    assert RULE not in done.stdout, "a warning is not a blocking finding"
    assert NET_LINE in lines_of(done), "the net line is part of the summary and survives quiet"
