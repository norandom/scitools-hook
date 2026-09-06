"""The accuracy floor: how much of an analysis Understand resolved (requirement 7).

The figure is a measurement of the *analysis*, not of the code, and it is the only number the
Gate has that says how much to trust everything else it reported. A project whose imports
Understand cannot follow produces a database with fewer edges, fewer resolved calls and fewer
entities than the code has, and every rule reading those is quietly answering a smaller
question.

Two properties carry the whole rule:

* **It never blocks.** A poor figure is usually a third-party package, an interpreter version
  Understand does not model, or a language feature it has not caught up with. None of those is
  fixable by the person making this commit, and a gate that refused the commit over them is a
  gate that gets switched off (requirement 7.3).
* **A missing figure is not a bad one.** ``None`` is what a 6.5 install reports, what a build
  that was not asked reports, and what a pass with nothing to analyse reports. None of them is
  a project that resolved nothing.
"""

from __future__ import annotations

from scitools_hook.analysis.accuracy import RULE, evaluate_accuracy


def test_a_side_below_the_floor_is_reported() -> None:
    found = evaluate_accuracy({"after": 0.4}, 0.8)

    assert [finding.rule for finding in found] == [RULE]
    assert found[0].value == 0.4
    assert found[0].limit == 0.8
    assert "40%" in found[0].message
    assert "80%" in found[0].message


def test_the_finding_never_blocks() -> None:
    """Requirement 7.3: the exit code stays a function of the findings about the code."""
    found = evaluate_accuracy({"after": 0.0}, 1.0)

    assert found[0].severity == "warning"
    assert found[0].blocking is False


def test_a_side_at_the_floor_is_not_below_it() -> None:
    assert evaluate_accuracy({"after": 0.8}, 0.8) == []


def test_a_side_above_the_floor_is_not_reported() -> None:
    assert evaluate_accuracy({"after": 0.95}, 0.8) == []


def test_both_sides_are_judged_and_each_says_which_it_is() -> None:
    """A before side that resolved badly makes the comparison less trustworthy too."""
    found = evaluate_accuracy({"after": 0.4, "before": 0.3}, 0.8)

    assert [finding.details["side"] for finding in found] == ["after", "before"]
    assert "this change proposes" in found[0].message
    assert "compared against" in found[1].message


def test_without_a_floor_nothing_is_reported() -> None:
    """The key ships unset, so an untouched configuration never meets this rule."""
    assert evaluate_accuracy({"after": 0.01}, None) == []


def test_a_side_with_no_figure_cannot_be_reported() -> None:
    """The absence of a measurement is not a measurement of zero (requirement 1.3)."""
    assert evaluate_accuracy({}, 0.8) == []
