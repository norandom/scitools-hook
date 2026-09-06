"""How much of an analysis Understand resolved, and the floor below which that is news (7).

``und analyze -accuracy`` reports ``N of M parsed files had no errors or warnings (P%)``.
It is not a measurement of the code: it is a measurement of **the analysis**, and it is the
only number the Gate has that says how much to trust everything else it reported. A project
whose imports Understand cannot follow produces a database with fewer edges, fewer resolved
calls and fewer entities than the code really has, and every rule that reads those is quietly
answering a smaller question.

**It never blocks, and that is a decision rather than a default.** A poor figure is usually
somebody else's third-party package, an interpreter version Understand does not model, or a
language feature it has not caught up with -- none of which the person making this commit can
fix, and all of which would make the gate something to switch off. So the floor raises a
warning that says the run is less trustworthy than it looks, and the exit code stays a
function of the findings about the code (requirement 7.3).

**A missing figure is not a bad one.** ``None`` is what a 6.5 install reports, what a build
that was not asked reports, and what a warm run with nothing to analyse reports; a floor
compares against a number or against nothing at all.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from scitools_hook.models.findings import Finding, analysis_rule

RULE: Final = analysis_rule("accuracy")

SIDE_WORDS: Final[dict[str, str]] = {
    "after": "the code this change proposes",
    "before": "the code it is compared against",
}
"""What each side *is*, because "before" and "after" say nothing about why it matters."""


def evaluate_accuracy(figures: Mapping[str, float], floor: float | None) -> list[Finding]:
    """One non-blocking finding per side whose resolution falls below ``floor`` (req 7.3).

    ``figures`` holds only the sides that have one. The absence of a measurement is not a
    measurement of zero, and a build that reports no accuracy must read exactly as it did
    before the switch existed (requirement 1.3), so a side with none is simply not in the
    mapping and no finding can be made about it.
    """
    if floor is None:
        return []
    return [
        _finding(side, found, floor) for side, found in sorted(figures.items()) if found < floor
    ]


def _finding(side: str, found: float, floor: float) -> Finding:
    """One side's resolution, reported against the floor an operator set."""
    return Finding(
        kind="threshold",
        rule=RULE,
        scope="project",
        path="",
        value=found,
        limit=floor,
        limit_source="config",
        severity="warning",
        blocking=False,
        message=(
            f"Understand resolved {found:.0%} of the {side} analysis, below the configured "
            f"{floor:.0%}; every rule below reads {SIDE_WORDS.get(side, side)} through that "
            f"analysis, so this run says less than it appears to"
        ),
        details={"side": side},
    )
