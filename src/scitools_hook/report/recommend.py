"""The readable half of ``recommend``: the evidence an operator decides on.

One pure function of a :class:`~scitools_hook.analysis.recommend.Recommendation`, in the same
regime as ``report.human``: no colour, no environment, no clock, byte-deterministic.

The layout is built around the one thing this report exists to show, which is **the trade**.
A percentile alone is a number generator; what an operator choosing between 10 and 15 needs
is how many entities each of those puts outside and which entities they are. So every metric
gets a small table of candidate limits with their cost, the configured limit marked in it,
and the worst offenders named underneath -- and the *proposal*, when there is one, is one
marked row of that table rather than the table's purpose.

Two sections sit outside the per-metric tables and both were forced by the measurement in
``analysis.recommend``:

* **Outliers.** A metric whose maximum is fifty times its 95th percentile has no level to
  set. ``CountPath`` on the repository this was built against has a median of 1 and a maximum
  of 955,514,880, and no ceiling between those two numbers describes anything. Those metrics
  are printed as a list of routines to fix, with the ratio that says why they are a tail.
* **Not measured.** Every configured threshold this run had nothing to say about, with the
  reason. A report that silently covered the ceilings and ignored the minima, the population
  thresholds and the metrics Understand does not provide for this language would read as a
  verdict on the whole configuration.
"""

from __future__ import annotations

from collections.abc import Sequence

from scitools_hook.analysis.recommend import (
    MetricAdvice,
    Offender,
    Recommendation,
    counts_line,
    deviations,
    plural,
)
from scitools_hook.config.template import render_recommendation

LEGEND: tuple[str, ...] = (
    "# recommend: what limits fit this repository, with the cost of each -- NOT a baseline.",
    "# `baseline` records WHERE YOU ARE: today's worst value per rule, so existing debt reports",
    "# as pre-existing. This says WHERE TO AIM. Nothing here is written and nothing is applied.",
    "# A ceiling that already contains the target share of its population is reported `keep`:",
    "# the tighter rows below it are priced, not proposed.",
)
"""Printed above every report. The confusion it forestalls is the whole point of the feature."""

NOTHING_MEASURED = (
    "no configured threshold could be measured on this repository; see 'not measured' below"
)

TAIL_LEGEND = (
    "# outliers: the maximum is far above the 95th percentile, so no limit describes this "
    "population."
)
TAIL_ADVICE = "# Fix the entities below; moving the limit changes nothing or condemns everything."

SKIPPED_LEGEND = "# not measured, and why -- this report is a verdict on the ceilings only"

SCOPED_NOTE = (
    "# NOTE: {names} change the limits for some files. Every population below is measured "
    "across the WHOLE project against the GLOBAL limit, so a file those scopes judge by "
    "different numbers is still counted here against the global one."
)
"""Printed whenever a path scope is configured, because the report would otherwise overstate.

A run that says ``keep 10`` after counting a tree that nothing judges by 10 has asserted
something it did not establish. Splitting the populations per scope is the right answer;
saying plainly that this does not do it is the honest one until it does.
"""


def render_recommendation_report(recommendation: Recommendation, target: float) -> str:
    """The whole report: legend, one block per ceiling, the tails, the skips, the paste block."""
    parts = [
        "\n".join(LEGEND),
        f"# measured {counts_line(recommendation.counts)}",
        *_scope_note(recommendation),
        "",
        *_metric_blocks(recommendation),
        *_tail_section(recommendation),
        *_skipped_section(recommendation),
        render_configuration(recommendation, target),
    ]
    return "\n".join(part for part in parts if part is not None)


def render_configuration(recommendation: Recommendation, target: float) -> str:
    """Only the pasteable configuration, for an operator piping it somewhere."""
    return render_recommendation(
        deviations(recommendation), counts_line(recommendation.counts), target
    )


def _scope_note(recommendation: Recommendation) -> list[str]:
    """The disclosure a configured path scope requires, and nothing when there is none."""
    if not recommendation.scoped:
        return []
    names = ", ".join(f"[scope.{name}]" for name in recommendation.scoped)
    return [SCOPED_NOTE.format(names=names)]


def _metric_blocks(recommendation: Recommendation) -> list[str]:
    """One block per configured ceiling, in configuration order."""
    if not recommendation.advice:
        return [NOTHING_MEASURED, ""]
    return [block for item in recommendation.advice for block in (_metric_block(item), "")]


def _metric_block(item: MetricAdvice) -> str:
    """One ceiling: its verdict, its shape, the priced candidates, and the worst entities."""
    lines = [_verdict_line(item), f"  {_shape_line(item)}", *_table(item)]
    lines.append(f"  worst: {_offenders(item.offenders)}")
    return "\n".join(lines)


def _verdict_line(item: MetricAdvice) -> str:
    """``routine.CyclomaticStrict  keep 10`` or ``file.CountDeclClass  raise 3 -> 5``."""
    if item.proposed is None:
        return f"{item.rule}  keep {_number(item.configured)}"
    return f"{item.rule}  raise {_number(item.configured)} -> {_number(item.proposed)}"


def _shape_line(item: MetricAdvice) -> str:
    """The distribution, then how much of it the configured limit already holds."""
    shape = item.distribution
    return (
        f"{shape.count} {plural(item.scope, shape.count)}: p50 {_number(shape.p50)}, "
        f"p90 {_number(shape.p90)}, "
        f"p95 {_number(shape.p95)}, p99 {_number(shape.p99)}, max {_number(shape.maximum)} "
        f"-- {item.share_inside:.1%} inside the configured {_number(item.configured)}"
    )


def _table(item: MetricAdvice) -> list[str]:
    """The trade, priced: one row per candidate limit, marked where it is the one in force."""
    rows = ["    limit   outside    share"]
    for candidate in item.candidates:
        mark = " <- configured" if candidate.configured else ""
        if candidate.proposed:
            mark = " <- proposed"
        rows.append(
            f"  {_number(candidate.limit):>7} {candidate.outside:>9} "
            f"{candidate.share_outside:>8.1%}{mark}"
        )
    return rows


def _offenders(offenders: Sequence[Offender]) -> str:
    """The worst entities, one after another, each locatable without a second command."""
    if not offenders:
        return "(none)"
    return "; ".join(_one_offender(item) for item in offenders)


def _one_offender(item: Offender) -> str:
    """``45 pkg.mod.f (src/mod.py:12)``, or just ``2516 src/mod.py`` for a file entity.

    A file's long name **is** its repository-relative path, so the parenthetical would repeat
    it verbatim -- ``2516 src/a.py (src/a.py)``. ``report.human`` drops a repeated entity name
    for the same reason; dropping it here keeps the two reports reading the same way.
    """
    where = f" ({item.path}{_at(item.line)})" if item.longname != item.path else _at(item.line)
    return f"{_number(item.value)} {item.longname}{where}"


def _at(line: int | None) -> str:
    return "" if line is None else f":{line}"


def _tail_section(recommendation: Recommendation) -> list[str]:
    """The metrics that are a tail rather than a level, if any."""
    tails = recommendation.tails
    if not tails:
        return []
    lines = [TAIL_LEGEND, TAIL_ADVICE]
    for item in tails:
        shape = item.distribution
        lines.append(
            f"{item.rule}  p50 {_number(shape.p50)}, p95 {_number(shape.p95)}, "
            f"max {_number(shape.maximum)} ({item.tail_ratio:,.0f}x p95)"
        )
        lines.extend(f"  {_one_offender(offender)}" for offender in item.offenders)
    return ["\n".join(lines), ""]


def _skipped_section(recommendation: Recommendation) -> list[str]:
    """Every configured threshold this run had nothing to say about, with the reason."""
    if not recommendation.skipped:
        return []
    lines = [SKIPPED_LEGEND]
    lines.extend(f"{item.rule}  {item.reason}" for item in recommendation.skipped)
    return ["\n".join(lines), ""]


def _number(value: float) -> str:
    """A metric value without the trailing ``.0`` almost all of them would carry."""
    return f"{value:g}"
