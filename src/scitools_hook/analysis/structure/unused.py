"""The unused-routine rule: a routine this change leaves that nothing in the project uses.

The shape this catches is dead code an agent forgot to delete. An agent asked to replace one
implementation with another routinely writes the new one, wires it in, and leaves the old one
sitting there -- it still parses, it still has tests sometimes, and nothing in a diff says
that nobody calls it any more. Every complexity limit is satisfied by code nobody runs.

**The decision is over the whole project, never over the change.** Requirement 6.2 is explicit
about that and it is the difference between a rule and a nuisance: a routine called from a
file this commit did not touch is used, and reporting it because the caller was not in the
neighbourhood would make the rule wrong on almost every commit. The worker answers the
question by asking the routine itself for its references, so the answer covers the database
rather than the extraction (``worker._referenced``).

**Three states, not two.** ``referenced`` is ``True``, ``False``, or ``None`` for "the worker
was not asked". A ``None`` is reported once as unavailable and evaluates nothing, because a
run that could not measure use must not report a project full of dead code (requirement 6.4).

**A warning by default, and an ignore list.** Reference-based dead-code detection has a known
blind spot -- a dunder the interpreter calls, a test pytest collects, an entry point named in
`pyproject.toml`, a handler registered by a decorator -- and every one of those is a routine
with no reference because there is none to see. So the rule ships as a warning
(requirement 6.3), the shipped ignore list excuses the four common shapes, and an operator
adds their own. It is the one rule here whose false positives are a property of the language
rather than of the analysis.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from typing import Final, NamedTuple

from scitools_hook.config.models import Severity
from scitools_hook.models.findings import Finding, structure_rule
from scitools_hook.models.snapshot import EntityKey, ProjectSnapshot

RULE: Final = structure_rule("unused_routine")

UNAVAILABLE: Final = (
    "structure.unused_routines is on, but this snapshot carries no reference measurement, so "
    "no routine was judged unused; the analysis cache predates the rule -- run "
    "`scitools-hook db rebuild`, or make one change to force a fresh extraction"
)
"""Requirement 6.4, said once per run rather than turned into findings.

The reachable case is a warm cache holding a snapshot extracted before the rule was turned
on. Reporting every affected routine as unused there would be the worst possible answer, so
the rule reports itself instead.
"""


class Unused(NamedTuple):
    """What the rule found, and why it found nothing when that is the answer."""

    findings: list[Finding]
    unavailable: str = ""


def find_unused_routines(
    after: ProjectSnapshot,
    affected: Collection[EntityKey],
    severity: Severity = "warning",
    ignore: Sequence[str] = (),
) -> Unused:
    """One finding per affected routine nothing in the project references (req 6.1, 6.2, 6.5).

    ``affected`` is the change's own entity set, so a routine the change neither wrote nor
    touched is never reported however dead it is -- this is a gate on a commit, not an audit.
    A routine the change **deleted** cannot appear either, and needs no rule of its own: it is
    not in the after snapshot at all (requirement 6.5).
    """
    records = sorted(
        (
            (key, after.entities[key])
            for key in affected
            if key.scope == "routine" and key in after.entities
        ),
        key=lambda pair: (pair[0].path, pair[0].longname),
    )
    if records and all(record.referenced is None for _, record in records):
        return Unused(findings=[], unavailable=UNAVAILABLE)
    excused = _excused(ignore)
    return Unused(
        findings=[
            _finding(after, key, severity)
            for key, record in records
            if record.referenced is False and not _matches(excused, key.longname)
        ]
    )


def _excused(patterns: Sequence[str]) -> tuple[re.Pattern[str], ...]:
    """The ignore list, compiled. Invalid patterns are refused by the settings model."""
    return tuple(re.compile(pattern) for pattern in patterns)


def _matches(excused: Sequence[re.Pattern[str]], longname: str) -> bool:
    """Whether a routine's qualified name is one the operator excused.

    ``search`` and not ``match``, like every other ignore list in this project: a pattern
    naming a suffix -- ``\\.__\\w+__$`` for dunders -- has to be able to find it anywhere in
    the name.
    """
    return any(pattern.search(longname) for pattern in excused)


def _finding(after: ProjectSnapshot, key: EntityKey, severity: Severity) -> Finding:
    """One routine nothing references, reported where it is written."""
    record = after.entities[key]
    return Finding(
        kind="structural",
        rule=RULE,
        scope="routine",
        entity=record.ref,
        path=key.path,
        line=record.ref.line,
        value=0.0,
        limit=None,
        limit_source="rule",
        severity=severity,
        blocking=severity == "error",
        message=(
            f"nothing in this project calls or uses {key.longname}; "
            f"every reference to it is either absent or outside the analysis root"
        ),
        details={"longname": key.longname},
    )
