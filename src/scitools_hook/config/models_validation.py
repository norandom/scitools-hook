"""Shape checks behind the configuration models: threshold tables and regex lists (req 3.8).

These helpers work on plain data so ``models`` can call them from validators; they raise
``ValueError`` with the offending key so pydantic aggregates the failures into one report.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Final

from scitools_hook.config.metric_names import SCOPES, Scope, is_valid_scope

THRESHOLD_TABLE_KEYS: Final[frozenset[str]] = frozenset({"max", "min", "severity", "ratchet"})
"""Keys a threshold written as a TOML table may carry."""


def is_number(value: object) -> bool:
    """True for int/float but not bool (a bool is an int in Python, not in TOML)."""
    return isinstance(value, int | float) and not isinstance(value, bool)


def compile_patterns(patterns: list[str]) -> list[str]:
    """Return ``patterns`` unchanged after proving each compiles as a regular expression."""
    for pattern in patterns:
        try:
            re.compile(pattern)
        except re.error as err:
            raise ValueError(f"invalid regular expression {pattern!r}: {err}") from err
    return patterns


def _threshold_entry(scope: Scope, metric: str, raw: object) -> dict[str, object]:
    """Turn one ``Metric = value`` TOML entry into ``ThresholdSpec`` input."""
    key = f"thresholds.{scope}.{metric}"
    if is_number(raw):
        return {"scope": scope, "metric": metric, "limit": {"max": raw}}
    if not isinstance(raw, Mapping):
        raise ValueError(
            f"{key}: expected a number or a table with max/min, got {type(raw).__name__}"
        )
    unknown = sorted(set(raw) - THRESHOLD_TABLE_KEYS)
    if unknown:
        raise ValueError(
            f"{key}: unknown keys {', '.join(unknown)}; allowed: "
            f"{', '.join(sorted(THRESHOLD_TABLE_KEYS))}"
        )
    entry: dict[str, object] = {"scope": scope, "metric": metric}
    entry["limit"] = {k: raw[k] for k in ("max", "min") if k in raw}
    entry.update({k: raw[k] for k in ("severity", "ratchet") if k in raw})
    return entry


def threshold_entries[KeyT: str](tables: Mapping[KeyT, object]) -> list[dict[str, object]]:
    """Flatten ``{scope: {metric: value}}`` into ``ThresholdSpec`` inputs, keeping file order."""
    entries: list[dict[str, object]] = []
    for scope, table in tables.items():
        if not is_valid_scope(scope):
            raise ValueError(
                f"unknown threshold scope {scope!r}; expected one of {', '.join(SCOPES)}"
            )
        if not isinstance(table, Mapping):
            raise ValueError(f"[thresholds.{scope}] must be a table of 'Metric = limit' entries")
        entries.extend(_threshold_entry(scope, str(metric), raw) for metric, raw in table.items())
    return entries
