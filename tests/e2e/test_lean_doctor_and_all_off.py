"""``doctor``'s lean rows, and a check with every lean rule off, through the installed command
(lean-code req 9.2, 9.4).

The fixture and the run are ``lean_fixture``'s. One property is the one worth stating.
**A check with every lean rule off is the report it always was, but for the net line.**
Asserted as an equality on the text: the same run rendered with the lean section absent,
against the command's own output with the net line removed. "Contains no lean finding" would
pass a renderer that had quietly changed a heading.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from e2e.lean_fixture import NET_LINE, RULE, a_workspace, checked, lines_of
from scitools_hook.exit_codes import ExitCode
from scitools_hook.models.findings import RunResult
from scitools_hook.report.human import ColorMode, ReportSettings, Verbosity, render_human
from scitools_hook.understand.fake import FAKE_VAR

LEAN_ROWS: Final = ("feature lean references", "feature lean tokens", "feature duplicate metric")
"""The three rows requirement 9.2 asks ``doctor`` for, as the report labels them."""


def doctor_row(text: str, label: str) -> str:
    """The value of the single ``label:`` row of a ``doctor`` report."""
    found = [line.strip() for line in text.splitlines() if line.strip().startswith(f"{label}:")]
    assert len(found) == 1, f"expected one {label!r} row, got {found}"
    return found[0].split(":", 1)[1].strip()


# --- doctor (9.2) -------------------------------------------------------------------


def test_doctor_prints_one_row_per_lean_capability(tmp_path: Path) -> None:
    """Under the seam every row is ``unverified`` with its reason; a real install measures."""
    done = a_workspace(tmp_path).cli("doctor")

    assert done.returncode == int(ExitCode.OK), done.stderr
    for label in LEAN_ROWS:
        value = doctor_row(done.stdout, label)
        assert value.startswith("unverified: "), (label, value)
        assert FAKE_VAR in value or "seam" in value, (label, value)


# --- every rule off: the report it always was, but for the net line (9.4) ----------


def test_every_lean_rule_off_leaves_the_report_as_it_was_but_for_the_net_line(
    tmp_path: Path,
) -> None:
    """The expected side is the same run rendered with the lean section absent and no delta.

    ``ReportSettings()`` is the renderer's pre-family default: no lean configuration at all.
    The run is taken from the command's own JSON document, which is the ``RunResult`` whole,
    so the two renderings differ only by what the family added to the *text*.
    """
    workspace = a_workspace(tmp_path)

    human = checked(workspace)
    machine = checked(workspace, "--format", "json")

    assert human.returncode == int(ExitCode.OK), human.stdout + human.stderr
    assert machine.returncode == int(ExitCode.OK), machine.stdout + machine.stderr
    result = RunResult.model_validate_json(machine.stdout)
    assert result.net_delta is not None, "the delta is taken whether or not a lean rule is on"
    as_it_was = render_human(
        result.model_copy(update={"net_delta": None}),
        Verbosity.NORMAL,
        ColorMode.OFF,
        True,
        ReportSettings(),
    )
    lines = lines_of(human)
    without_net = [line for line in lines if line != NET_LINE]
    assert len(without_net) == len(lines) - 1, "exactly one net line"
    assert "\n".join(without_net) == as_it_was
    assert RULE not in human.stdout
