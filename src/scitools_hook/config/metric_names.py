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
    """

    id: str
    scope: Scope
    description: str
    requires: tuple[str, ...] = ()


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
}
"""Synthetic metric id -> declaration (req 3.5)."""

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
