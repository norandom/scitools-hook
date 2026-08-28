"""Population reducers and ignore-regex filtering (req 3.4, 3.6).

A stats-prefixed threshold (``AVG:CyclomaticStrict``) is evaluated against the population
vector of its scope rather than against individual entities. :func:`reduce` is the one place
where a prefix becomes a number, and it answers ``None`` instead of raising when the vector
cannot be reduced — an empty population, or a reducer that rejects its input — so the caller
can report the failure once per metric instead of aborting the run.

Ignore rules (req 3.6) are regular expressions per scope. They are matched with
``re.search`` against the entity's qualified ``longname``, which is what `srccheck` matches
(research.md, "regex ignore on ``longname()``"), plus the repository-relative path for the
file scope, because a file rule is naturally written as a path fragment. Every pattern is
compiled once, in :meth:`IgnoreFilter.from_rules`.

Population vectors reach the evaluators with the ignore rules already applied by the
snapshot extractor (``ExtractRequest.ignore``), so only entity keys are filtered here.
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from scitools_hook.config.metric_names import STATS_REDUCERS, Scope
from scitools_hook.config.models import IgnoreRules
from scitools_hook.models.snapshot import EntityKey, ProjectSnapshot

IGNORE_FIELDS: Final[dict[Scope, str]] = {
    "file": "files",
    "class": "classes",
    "routine": "routines",
}
"""Scope -> the :class:`~scitools_hook.config.models.IgnoreRules` field holding its patterns.

``project`` and ``arch`` are absent: they have no entities of their own to ignore.
"""


def reduce(prefix: str, values: Sequence[float]) -> float | None:
    """Reduce ``values`` with the stats reducer named ``prefix`` (req 3.4).

    ``prefix`` is matched case-insensitively, as ``parse_metric_name`` canonicalises it.
    Returns ``None`` when the reducer cannot produce a value for this vector, which is
    every ``statistics.StatisticsError`` case, empty populations included; the caller
    reports that once per metric. An unknown prefix is a programming error — configuration
    rejects those long before a run — and raises ``ValueError``.
    """
    reducer = STATS_REDUCERS.get(prefix.upper())
    if reducer is None:
        raise ValueError(
            f"unknown stats prefix {prefix!r}; expected one of {', '.join(STATS_REDUCERS)}"
        )
    try:
        return float(reducer(values))
    except statistics.StatisticsError:
        return None


@dataclass(frozen=True, slots=True)
class IgnoreFilter:
    """The configured ignore regexes, compiled once, keyed by the scope they apply to."""

    patterns: Mapping[Scope, tuple[re.Pattern[str], ...]]

    @classmethod
    def from_rules(cls, rules: IgnoreRules) -> IgnoreFilter:
        """Compile every pattern of ``rules``; the models layer already validated them."""
        compiled: dict[Scope, tuple[re.Pattern[str], ...]] = {}
        for scope, attribute in IGNORE_FIELDS.items():
            raw: list[str] = getattr(rules, attribute)
            compiled[scope] = tuple(re.compile(pattern) for pattern in raw)
        return cls(patterns=compiled)

    def is_ignored(self, key: EntityKey) -> bool:
        """Whether ``key`` matches an ignore pattern of its own scope (req 3.6)."""
        patterns = self.patterns.get(key.scope, ())
        if not patterns:
            return False
        subjects = (key.longname, key.path) if key.scope == "file" else (key.longname,)
        return any(pattern.search(subject) for pattern in patterns for subject in subjects)


@dataclass(frozen=True, slots=True)
class FilteredKeys:
    """The entities that survive the ignore rules, and how many were dropped per scope."""

    keys: set[EntityKey] = field(default_factory=set)
    ignored_counts: dict[Scope, int] = field(default_factory=dict)


def filter_keys(keys: Iterable[EntityKey], ignore: IgnoreFilter | None = None) -> FilteredKeys:
    """Split ``keys`` into the ones to evaluate and a count of the ones ignored (req 3.6).

    Scopes that ignored nothing are absent from ``ignored_counts``, so a run reports only
    the exclusions it actually made.
    """
    kept: set[EntityKey] = set()
    ignored: dict[Scope, int] = {}
    for key in keys:
        if ignore is not None and ignore.is_ignored(key):
            ignored[key.scope] = ignored.get(key.scope, 0) + 1
        else:
            kept.add(key)
    return FilteredKeys(keys=kept, ignored_counts=ignored)


def filter_snapshot_keys(
    snapshot: ProjectSnapshot, ignore: IgnoreFilter | None = None
) -> FilteredKeys:
    """Apply :func:`filter_keys` to every entity of ``snapshot`` (whole-project mode, 4.8)."""
    return filter_keys(snapshot.entities, ignore)
