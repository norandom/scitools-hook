"""Metric-name grammar, stats reducers, synthetic metrics and scope kinds (req 3.4, 3.5).

A threshold names a metric either plainly (``CyclomaticStrict``) or with a stats
prefix (``AVG:CyclomaticStrict``) that turns it into a population threshold.
This module owns that grammar, the reducer behind each prefix, the declaration
of the Gate's synthetic metrics and the Understand kind string of every scope
that has entities. It imports nothing above ``scitools_hook.errors``.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final, Literal, NamedTuple, TypeGuard

from scitools_hook.errors import ConfigError

Scope = Literal["routine", "class", "file", "project", "arch"]
"""Where a threshold or rule applies; ``project`` and ``arch`` have no entities of their own."""

SCOPES: Final[tuple[Scope, ...]] = ("routine", "class", "file", "project", "arch")
ELEMENT_SCOPES: Final[tuple[Scope, ...]] = ("routine", "class", "file")
"""Scopes whose values come from individual Understand entities (populations for 3.4)."""


def is_valid_scope(value: str) -> TypeGuard[Scope]:
    """Return whether ``value`` is one of ``SCOPES`` (exact, lower-case match)."""
    return value in SCOPES


Reducer = Callable[[Sequence[float]], float]

STATS_REDUCERS: Final[dict[str, Reducer]] = {
    "AVG": statistics.mean,
    "MEDIAN": statistics.median,
    "MEDIANHIGH": statistics.median_high,
    "MEDIANLOW": statistics.median_low,
    "MEDIANGROUPED": statistics.median_grouped,
    "MODE": statistics.mode,
    "STDEV": statistics.pstdev,  # population, as in srccheck
    "VARIANCE": statistics.pvariance,  # population, as in srccheck
}
"""Canonical (upper-case) stats prefix -> reducer applied to a scope's population."""


class MetricRef(NamedTuple):
    """A parsed metric name: an optional canonical stats prefix and the metric identifier."""

    prefix: str | None
    metric: str

    @property
    def is_population(self) -> bool:
        """True when the threshold applies to the population of the scope, not to elements."""
        return self.prefix is not None


def _grammar_hint() -> str:
    return (
        "expected 'Metric' or 'PREFIX:Metric' with exactly one ':', "
        f"where PREFIX is one of {', '.join(STATS_REDUCERS)}"
    )


def parse_metric_name(raw: str) -> MetricRef:
    """Parse ``raw`` into a ``MetricRef``; raise ``ConfigError`` for anything else.

    The prefix is matched case-insensitively and returned in canonical upper case.
    The metric part must be a plain identifier, as every Understand metric id is.
    """
    prefix_part, sep, metric_part = raw.partition(":")
    if not sep:
        prefix, metric = None, prefix_part
    elif ":" in metric_part:
        raise ConfigError(
            f"metric name {raw!r} contains more than one ':'", key=raw, hint=_grammar_hint()
        )
    else:
        prefix, metric = prefix_part.upper(), metric_part
        if prefix not in STATS_REDUCERS:
            raise ConfigError(
                f"unknown stats prefix {prefix_part!r} in metric name {raw!r}",
                key=raw,
                hint=_grammar_hint(),
            )
    if not metric.isidentifier():
        raise ConfigError(
            f"metric name {raw!r} has no valid metric identifier", key=raw, hint=_grammar_hint()
        )
    return MetricRef(prefix, metric)


def format_metric_name(ref: MetricRef) -> str:
    """Render ``ref`` back to its canonical ``PREFIX:Metric`` or ``Metric`` form."""
    return ref.metric if ref.prefix is None else f"{ref.prefix}:{ref.metric}"


@dataclass(frozen=True, slots=True)
class SyntheticMetric:
    """Declaration of a metric the Gate computes itself; the worker owns the computation.

    ``requires`` lists the native Understand metrics the computation reads.

    ``floor`` is a ``(metric, minimum)`` pair naming a population this metric is meaningless
    over: an entity whose ``metric`` is below ``minimum`` is not judged on the synthetic at
    all -- no finding, no ratchet comparison and no unavailable record, because the value was
    computed and is simply not a statement about that entity. It is declared here rather than
    branched on in the evaluator so that the reason travels with the metric and one guard
    serves both ``analysis.thresholds`` and ``analysis.ratchet`` (req 6.3). The ``minimum``
    is the **shipped default**, not a constant: requirement 6.3 asks for a configurable one,
    and the operator's own value overrides it where the guard is applied.
    """

    id: str
    scope: Scope
    description: str
    requires: tuple[str, ...] = ()
    floor: tuple[str, int] | None = None


SYNTHETIC_METRICS: Final[dict[str, SyntheticMetric]] = {
    "CountParams": SyntheticMetric(
        id="CountParams",
        scope="routine",
        description=(
            "Declared parameters of a routine: the entities it defines with kind "
            "'Parameter ~Catch' (Understand's native CountParams is unset for Python)."
        ),
    ),
    "CountDeclMethodNonStub": SyntheticMetric(
        id="CountDeclMethodNonStub",
        scope="class",
        description=(
            "Declared methods excluding trivial accessors: "
            "CountDeclMethod - 2 * CountDeclPropertyAuto."
        ),
        requires=("CountDeclMethod", "CountDeclPropertyAuto"),
    ),
    "LinesPerStatement": SyntheticMetric(
        id="LinesPerStatement",
        scope="routine",
        description=(
            "Source lines per statement: CountLineCode / CountStmt, undefined for a routine "
            "with no statements, and not judged below five statements."
        ),
        requires=("CountLineCode", "CountStmt"),
        floor=("CountStmt", 5),
    ),
}
"""Synthetic metric id -> declaration (req 3.5, 6.3).

``LinesPerStatement`` is the one with a floor, and the floor is why the metric is usable at
all: the ratio is arithmetic, so a two-statement routine spread over six lines scores 3.0 and
outranks a genuinely long one. Five statements is where the distribution starts to mean
something -- measured over this repository's 2 851 routines of at least five statements: p50
1.20, p90 2.00, p95 2.29, max 6.6.
"""


@dataclass(frozen=True, slots=True)
class PluginMetric:
    """A metric Understand 8.0 computes from a plugin rather than from its built-in list.

    These are invisible to ``Metric.list(kind)`` -- measured on Build 1262, the routine kind
    string answers 18 metrics and none of these is among them -- and are found instead by
    ``Metric.lookup(id)``, whose ``tags()`` name the targets and languages recorded here. That
    is why they need a declaration at all: without one, a threshold on ``CountGlobalsUsed``
    would be refused as an unknown metric by ``config.validate`` before Understand was asked.

    ``scopes`` are the Gate's scopes for Understand's ``Target:`` tags (``Functions`` ->
    ``routine``, ``Classes`` -> ``class``, ``Files`` -> ``file``, ``Architectures`` -> ``arch``,
    ``Project`` -> ``project``). ``languages`` are Understand's own ``Language:`` tags, kept
    verbatim rather than mapped onto the Gate's twelve: Understand tags C and C++ separately
    while the Gate names the pair ``C++``, and the mapping belongs where availability is
    decided, not here.

    ``Any`` is Understand's word for a metric with no language restriction.
    """

    id: str
    scopes: tuple[Scope, ...]
    languages: tuple[str, ...]


PLUGIN_METRICS: Final[dict[str, PluginMetric]] = {
    "CountGlobalsModified": PluginMetric(
        id="CountGlobalsModified",
        scopes=("routine",),
        languages=("C", "C++", "Python", "Pascal", "Web"),
    ),
    "CountGlobalsSet": PluginMetric(
        id="CountGlobalsSet",
        scopes=("routine",),
        languages=("C", "C++", "Python", "Pascal", "Web"),
    ),
    "CountGlobalsUsed": PluginMetric(
        id="CountGlobalsUsed",
        scopes=("routine",),
        languages=("C", "C++", "Python", "Pascal", "Web"),
    ),
    "CountClassCoupledModified": PluginMetric(
        id="CountClassCoupledModified",
        scopes=("class",),
        languages=("Basic", "C#", "Java", "Pascal", "Python"),
    ),
    "CorePercentage": PluginMetric(
        id="CorePercentage", scopes=("arch", "project"), languages=("Any",)
    ),
    "BidirectionalDepsPercent": PluginMetric(
        id="BidirectionalDepsPercent", scopes=("file", "class"), languages=("Any",)
    ),
    "CognitiveComplexity": PluginMetric(
        id="CognitiveComplexity", scopes=("routine",), languages=("C", "C++")
    ),
    "DuplicateLinesOfCode": PluginMetric(
        id="DuplicateLinesOfCode", scopes=("file", "arch", "project"), languages=("Any",)
    ),
    "DuplicateLinesOfCodePercent": PluginMetric(
        id="DuplicateLinesOfCodePercent", scopes=("file", "arch", "project"), languages=("Any",)
    ),
}
"""Metric id -> declaration, from ``Metric.lookup(id).tags()`` on Build 1262, 2026-09-05.

None of these is a shipped threshold (requirement 5.4): a metric measured on nobody's code
may not refuse anybody's commit, so they enter the catalogue and the configuration grammar
and wait there until a repository records a limit for one. ``CognitiveComplexity`` is listed
because the build carries it, and is C/C++ only -- on a Python repository it is reported
unavailable rather than silently skipped.

The two ``DuplicateLines`` ids are the exception to the provenance above: they come from the
duplicates *solution* (``plugins/Solutions/duplicates/``), whose own declaration names
``Language: Any`` and ``Target: Files, Architectures, Project``, and not from a lookup on an
install that had loaded it. That is deliberate -- the declaration is only the candidate list,
the build's tags decide, and a solution that is absent or disabled in the Plugin Manager
answers the lookup with nothing, so the threshold is refused with the metric named (req 5.6).
"""

SCOPE_KINDS: Final[dict[Scope, str]] = {
    "routine": (
        "function ~unknown ~unresolved, method ~unknown ~unresolved, "
        "procedure ~unknown ~unresolved, routine ~unknown ~unresolved, "
        "classmethod ~unknown ~unresolved"
    ),
    "class": (
        "class ~unknown ~unresolved, interface ~unknown ~unresolved, struct ~unknown ~unresolved"
    ),
    "file": "file ~unknown ~unresolved",
}
"""Understand ``Db.ents`` kind filter per element scope.

``project`` and ``arch`` are deliberately absent: they have no entity kind, so a caller
iterating this mapping only ever issues real entity queries. Its keys equal ``ELEMENT_SCOPES``.
"""
