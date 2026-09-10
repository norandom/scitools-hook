"""The human report of the lean-code family (lean-code req 7.1, 7.3, 7.4, 7.6, 8.2).

Three things a reader sees and nothing else: the net line under the summary, the "nothing to
cut" line under that, and the worked example under a finding's hint in verbose output. What
produces the figures belongs elsewhere -- ``analysis/lean/net.py`` computes the delta and the
check pipeline attaches the example -- so every run below is hand-built and the tests are
about the text. The same family in the machine formats is
``tests/report/test_lean_machine_output.py``; that the ``agent-rules`` snippet promises an
agent the same net line this module asserts is held in ``tests/report/test_agent_rules.py``,
beside the snippet it is about.

The three properties worth stating, because each was a wrong answer somewhere first:

* **An absent delta and a delta of zero are different answers** (7.4). ``--all`` has no before
  side, so ``net_delta`` is ``None`` and *no line is printed*; a change that replaced exactly
  what it removed has a real zero and prints ``net: +0 lloc``. A renderer that prints zero for
  both tells an agent its whole-project run measured a change it never looked at.
* **The "nothing to cut" line has three conditions and every one of them is load-bearing**
  (7.6): a lean rule is switched on, no lean finding was raised, and the delta is at or below
  zero. Drop the first and the Gate congratulates a repository where nothing looked; drop the
  second and it congratulates a change that just collected six lean findings; drop the third
  and it congratulates a change that added two hundred lines. Each is tested by a run where
  that condition alone fails, because a condition fused into one boolean expression can be
  deleted with every other test still green.
* **The example is verbose-only** (8.2). It is five lines of code per finding: right when an
  agent asked for detail, noise in the default report, and absent from ``--quiet``, whose
  whole contract is the summary and the blocking findings.
"""

from __future__ import annotations

import re
from typing import Any, Final

import pytest
from fixtures import ENGINE

from scitools_hook.config.models import LeanRules
from scitools_hook.models.change import NetDelta
from scitools_hook.models.findings import Finding, RunResult, structure_rule
from scitools_hook.report.human import (
    EXAMPLE_LABEL,
    ColorMode,
    ReportSettings,
    Verbosity,
    render_human,
)
from scitools_hook.report.lean_examples import EXAMPLE_SUFFIX, EXAMPLES

ANSI: Final = re.compile(r"\x1b\[[0-9;]*m")

PASS_THROUGH: Final = structure_rule("pass_through")
"""One lean rule, standing for the family: its findings suppress the "nothing to cut" line."""

EXAMPLE: Final = EXAMPLES[f"{PASS_THROUGH}{EXAMPLE_SUFFIX}"]
"""The shipped worked example for that rule, which the verbose report must print whole.

The catalogue's own text rather than a fixture, because this is the one place the example is
rendered as something a person reads: an assertion that a made-up string survives the renderer
would say nothing about whether the block an agent is actually shown comes out intact.
"""

GREW: Final = NetDelta(statements=12, lines=30, routines=7)
SHRANK: Final = NetDelta(statements=-4, lines=-9, routines=3)
LEVEL: Final = NetDelta(statements=0, lines=0, routines=3)

ON: Final = LeanRules(pass_through="warning")
"""A configuration with one lean rule switched on, which is all requirement 7.6 asks."""


def run(
    *findings: Finding, net_delta: NetDelta | None = None, selection: str = "staged"
) -> RunResult:
    """One run carrying the findings and the delta a test is about, and nothing else."""
    return RunResult(
        tool_version="0.1.0",
        understand_version="Understand 8.0",
        repo_root="/repo",
        selection=selection,
        started_at="2026-01-01T09:00:00Z",
        seconds=1.5,
        findings=list(findings),
        net_delta=net_delta,
        blocking_count=sum(1 for finding in findings if finding.blocking),
    )


def lean_finding(example: object = EXAMPLE) -> Finding:
    """A pass-through finding as the pipeline hands it over: hint attached, example beside it.

    It carries a path and a line rather than an ``EntityRef``: the qualified name decides the
    head line, which the tests below never read, and a renderer test that does not read a
    field should not make its module depend on the model that carries it.
    """
    details: dict[str, Any] = {} if example is None else {"example": example}
    return Finding(
        kind="structural",
        rule=PASS_THROUGH,
        scope="routine",
        path=ENGINE,
        line=20,
        severity="warning",
        message="engine.save_user forwards to repo.insert and has one caller",
        hint="yagni: delete save_user and call repo.insert from its one caller",
        details=details,
    )


def other_finding() -> Finding:
    """A finding from outside the family: it must not suppress the "nothing to cut" line."""
    return Finding(
        kind="threshold",
        rule="routine.CyclomaticStrict",
        metric="CyclomaticStrict",
        scope="routine",
        path=ENGINE,
        line=42,
        value=20.0,
        limit=10.0,
        severity="warning",
        message="routine engine.Engine.evaluate CyclomaticStrict is 20, over the maximum of 10",
    )


def text(result: RunResult, *, lean: LeanRules | None = None, **kwargs: Any) -> str:
    """The human report of ``result``, uncoloured, with the lean configuration under test."""
    return render_human(result, color=ColorMode.OFF, settings=ReportSettings(lean=lean), **kwargs)


def summary_index(lines: list[str]) -> int:
    """Where the summary line sits, so a test can say what follows it."""
    return next(index for index, line in enumerate(lines) if line.startswith("summary: "))


# --- the net line, both signs and its absence (7.1, 7.3) --------------------------


def test_a_change_that_added_code_prints_the_delta_with_its_sign() -> None:
    """The exact shape the agent-rules snippet promises an agent it will see.

    The literal is written out here and again in ``test_agent_rules.py``, on purpose: the
    string is the requirement, and each module asserts its own producer against it, so a
    change to the format fails on both sides rather than moving one of them quietly.
    """
    assert "net: +12 lloc (+30 lines) over 7 routines" in text(run(net_delta=GREW))


def test_a_change_that_removed_code_prints_a_negative_delta() -> None:
    assert "net: -4 lloc (-9 lines) over 3 routines" in text(run(net_delta=SHRANK))


def test_a_change_that_replaced_what_it_removed_prints_a_signed_zero() -> None:
    """A real zero is a measurement and says so; it is not the absence below."""
    assert "net: +0 lloc (+0 lines) over 3 routines" in text(run(net_delta=LEVEL))


def test_a_delta_over_one_routine_says_routine_rather_than_routines() -> None:
    delta = NetDelta(statements=3, lines=5, routines=1)

    assert "net: +3 lloc (+5 lines) over 1 routine" in text(run(net_delta=delta))


def test_a_run_with_no_before_side_prints_no_net_line_at_all() -> None:
    """Requirement 7.4: ``--all`` omits the figure rather than printing a zero (7.4)."""
    assert "net:" not in text(run(selection="all"))


def test_the_net_line_follows_the_summary_line() -> None:
    """The snippet tells an agent the check "ends with one line for the whole change"."""
    lines = text(run(other_finding(), net_delta=SHRANK)).splitlines()

    assert lines[summary_index(lines) + 1].startswith("net: -4 lloc")


def test_quiet_still_prints_the_net_line() -> None:
    """Requirement 7.8 keeps the summary, and 7.1 puts the delta in it."""
    got = text(run(net_delta=SHRANK), verbosity=Verbosity.QUIET)

    assert "net: -4 lloc (-9 lines) over 3 routines" in got


# --- the "nothing to cut" line and each of its three conditions (7.6) -------------


def test_lean_rules_on_with_nothing_found_and_a_smaller_change_says_so() -> None:
    got = text(run(other_finding(), net_delta=SHRANK), lean=ON)

    assert "lean already: nothing to cut, net -4 lloc" in got


def test_the_lean_already_line_follows_the_net_line() -> None:
    lines = text(run(net_delta=SHRANK), lean=ON).splitlines()

    assert lines[summary_index(lines) + 2].startswith("lean already:")


def test_a_change_that_broke_even_has_nothing_to_cut_either() -> None:
    """ "At or below zero": a change that added nothing is inside requirement 7.6."""
    assert "lean already: nothing to cut, net +0 lloc" in text(run(net_delta=LEVEL), lean=ON)


def test_a_change_that_added_lines_is_not_told_it_has_nothing_to_cut() -> None:
    """The third condition on its own: rules on, nothing found, but the change grew."""
    assert "lean already:" not in text(run(net_delta=GREW), lean=ON)


def test_a_lean_finding_stops_the_line_however_small_the_change_is() -> None:
    """The second condition on its own: the delta is negative and a rule still fired."""
    assert "lean already:" not in text(run(lean_finding(), net_delta=SHRANK), lean=ON)


def test_with_every_lean_rule_off_nothing_claims_there_is_nothing_to_cut() -> None:
    """The first condition on its own: no rule looked, so the Gate says nothing."""
    assert "lean already:" not in text(run(net_delta=SHRANK), lean=LeanRules())


def test_a_caller_that_passes_no_configuration_gets_no_lean_already_line() -> None:
    assert "lean already:" not in text(run(net_delta=SHRANK))


def test_a_run_with_no_before_side_has_no_lean_already_line() -> None:
    """No delta is not a delta at or below zero: there is nothing to be already lean about."""
    assert "lean already:" not in text(run(selection="all"), lean=ON)


SWITCHES: Final = tuple(
    name for name, field in LeanRules.model_fields.items() if field.default is None
)
"""Every ``[lean]`` key that is off until an operator sets it: the family's nine switches.

Read from the model rather than listed here, so a tenth rule added to ``LeanRules`` joins this
test by existing. That is the point: the renderer answers "is any lean rule on" from a list of
its own, and a switch missing from that list would let the Gate report "nothing to cut" about
a rule that did look.
"""


@pytest.mark.parametrize("switch", SWITCHES)
def test_any_lean_switch_is_enough_to_earn_the_line(switch: str) -> None:
    """Every switch counts, not only the five the extractor walks references for.

    ``over_export`` is answered from metrics the snapshot already carries and ``max_net_growth``
    is a maximum rather than a rule, so neither is in ``LeanRules.wants_references`` -- and a
    renderer that asked that property instead would stay silent for an operator who enabled
    only those.
    """
    lean = LeanRules(**{switch: 0 if switch == "max_net_growth" else "warning"})

    assert "lean already:" in text(run(net_delta=SHRANK), lean=lean)


def test_the_nine_switches_are_all_of_them() -> None:
    """A guard on the guard: the derived list must not quietly shrink to nothing."""
    assert len(SWITCHES) == 9


# --- the worked example under the hint, verbose only (8.2) ------------------------


def test_verbose_output_prints_the_example_under_the_hint() -> None:
    lines = text(run(lean_finding()), verbosity=Verbosity.VERBOSE).splitlines()
    hint = next(index for index, line in enumerate(lines) if line.strip().startswith("hint: "))

    assert lines[hint + 1].strip().startswith("example: ")
    assert lines[hint + 1].startswith("    example: ")


def test_verbose_output_prints_every_line_of_the_example() -> None:
    """The before/after block is the example; a first line alone would teach nothing."""
    got = text(run(lean_finding()), verbosity=Verbosity.VERBOSE)

    for line in EXAMPLE.splitlines():
        assert line.strip() in got, line


def test_the_default_report_carries_the_hint_and_not_the_example() -> None:
    got = text(run(lean_finding()))

    assert "hint: yagni:" in got
    assert "example:" not in got


def test_quiet_carries_no_example_even_when_the_finding_blocks() -> None:
    got = text(run(lean_finding()), verbosity=Verbosity.QUIET)

    assert "example:" not in got


def test_a_finding_without_an_example_prints_none() -> None:
    got = text(run(lean_finding(example=None)), verbosity=Verbosity.VERBOSE)

    assert "example:" not in got


def test_an_example_an_operator_emptied_prints_nothing_rather_than_a_heading() -> None:
    got = text(run(lean_finding(example="")), verbosity=Verbosity.VERBOSE)

    assert "example:" not in got


def test_an_operators_example_keeps_its_shape_whatever_toml_wrapped_it_in() -> None:
    """A ``[hints]`` override written as a TOML multi-line string opens and closes on a line.

    Left as written, the leading newline prints ``example:`` over nothing and the trailing one
    ends the finding with a blank line, which in this layout reads as the end of the group.
    """
    wrapped = text(run(lean_finding(example=f"\n{EXAMPLE}\n")), verbosity=Verbosity.VERBOSE)

    assert wrapped == text(run(lean_finding()), verbosity=Verbosity.VERBOSE)


def test_an_example_of_nothing_but_newlines_prints_nothing() -> None:
    got = text(run(lean_finding(example="\n\n")), verbosity=Verbosity.VERBOSE)

    assert "example:" not in got


def test_an_example_that_is_not_text_is_ignored_rather_than_rendered() -> None:
    """``details`` is a free-form bag; the renderer reads what it can use and skips the rest."""
    got = text(run(lean_finding(example=42)), verbosity=Verbosity.VERBOSE)

    assert "example:" not in got


def test_colour_wraps_the_example_and_changes_nothing_else() -> None:
    plain = text(run(lean_finding()), verbosity=Verbosity.VERBOSE)
    colored = render_human(run(lean_finding()), Verbosity.VERBOSE, ColorMode.ON)

    assert "\x1b[" in colored
    assert ANSI.sub("", colored) == plain


def test_the_blank_line_inside_an_example_stays_blank_under_colour() -> None:
    """An escape pair around nothing is a line that looks empty and is not.

    The assertion is on the example's *own* blank line -- the one under its head line, which
    the shipped text puts between the location and the before/after block -- rather than on
    the presence of some empty line anywhere: sections are joined by a blank line, so any
    report with two sections satisfies that weaker claim whatever this arm does.
    """
    colored = render_human(run(lean_finding()), Verbosity.VERBOSE, ColorMode.ON)
    lines = colored.splitlines()
    head = next(index for index, line in enumerate(lines) if EXAMPLE_LABEL in line)

    assert EXAMPLE.splitlines()[1] == ""
    assert lines[head + 1] == ""
