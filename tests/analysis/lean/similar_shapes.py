"""The fixture vocabulary the similar-routine tests are written in, shared by two modules.

The shapes are short integer tuples rather than real token streams. The rule never reads a
shape as text and never inverts a vocabulary index -- it compares a shape with another shape
of the same index -- so ``(1, 2, 3)`` exercises exactly the code a lexed routine does, and a
reader can count the differing positions at a glance. :func:`variant` is what makes that
countable: every fixture shape is :data:`BASE` with named offsets replaced, so the similarity
of any pair is ``2 * (40 - differing) / 80`` and the number a test asserts can be read off the
fixture.

It is a module rather than a ``conftest.py`` because none of it is a pytest fixture: they are
plain constructors a test calls with the spans and shapes that case is about, and a test that
took them as arguments would hide the one thing each case is built to vary. It is imported by
:mod:`test_similar` and :mod:`test_similar_nesting`, which is the project's own gate speaking
-- one module holding both would be over the ``CountDeclFunction`` maximum of 80.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final, NamedTuple

from scitools_hook.analysis.lean.similar import SimilarLimits, find_similar_routines
from scitools_hook.config.models import LeanRules
from scitools_hook.models.findings import Finding
from scitools_hook.models.snapshot import (
    EntityKey,
    EntityRecord,
    EntityRef,
    ProjectSnapshot,
    RoutineShape,
    TokenIndex,
)

SHIPPED: Final = LeanRules()
THRESHOLD: Final = SHIPPED.similar_threshold
MIN_STATEMENTS: Final = SHIPPED.similar_min_statements

BASE: Final[tuple[int, ...]] = tuple(range(40))
"""Forty normalised tokens: one routine's shape, and the stem every fixture varies."""

STATEMENTS: Final = 10.0
"""``CountStmt`` for a fixture routine, comfortably above the shipped floor of six."""

DEFAULTS: Final = SimilarLimits()
"""The shipped limits, named once so the helper below takes no call in its signature."""


def variant(*changed: int) -> tuple[int, ...]:
    """:data:`BASE` with the token at each named offset replaced by one nothing else holds.

    The replacement is derived from the offset, so two variants that change the same offset
    change it to the *same* token and agree there -- which is what lets a chain be built
    where two shapes are each similar to a third and not to each other.
    """
    shape = list(BASE)
    for offset in changed:
        shape[offset] = 900 + offset
    return tuple(shape)


class Routine(NamedTuple):
    """One fixture routine: its identity, its shape, its span and its statement count.

    A tuple of plain values with its helpers written beside it rather than a class with an
    ``__init__``: seven fields and a receiver is eight parameters, and this project's own gate
    refuses a routine with more than five.
    """

    path: str
    longname: str
    shape: tuple[int, ...] = BASE
    start: int = 10
    statements: float | None = STATEMENTS
    end: int | None = None
    declared: int | None = None


def key_of(item: Routine) -> EntityKey:
    """The routine's identity, in the scope the rule walks."""
    return EntityKey(scope="routine", path=item.path, longname=item.longname)


def record_of(item: Routine) -> EntityRecord:
    """The entity record the rule reads the finding's entity reference off.

    The **statement count** is not read from here: it travels on the index's own
    :class:`~scitools_hook.models.snapshot.RoutineShape`, so that the floor is answerable for
    a routine the check pipeline's narrowing left no record of (task 5.8). It is still
    written here, because a fixture whose two tables disagreed about a routine would be a
    fixture nobody could read.

    ``declared`` is Understand's declaration line and defaults to the index's own first line,
    because they usually agree; one test sets them apart to say which of the two a finding
    quotes.
    """
    metrics = {} if item.statements is None else {"CountStmt": item.statements}
    return EntityRecord(
        ref=EntityRef(
            key=key_of(item),
            kind="Private Function",
            name=item.longname.rsplit(".", 1)[-1],
            line=item.start if item.declared is None else item.declared,
        ),
        language="Python",
        metrics=metrics,
    )


def at(item: Routine) -> str:
    """The ``longname (path:line)`` form the message and ``details["family"]`` carry."""
    return f"{item.longname} ({item.path}:{item.start})"


def spanned(member: str) -> tuple[str, int]:
    """``longname (path:line)`` read back as the path and line a reader would open.

    The finding's own text parsed rather than the fixture consulted, because the property
    under test is about what the message sends a reader to, not about what built it.
    """
    path, _, line = member.rsplit("(", 1)[1].rstrip(")").rpartition(":")
    return path, int(line)


def last_of(item: Routine) -> int:
    """The routine's last line: twenty below its first unless a test says otherwise."""
    return item.start + 20 if item.end is None else item.end


def snap(routines: Sequence[Routine], unreadable: Sequence[str] = ()) -> ProjectSnapshot:
    """An after snapshot carrying these routines, their records and nothing else."""
    return ProjectSnapshot(
        side="after",
        entities={key_of(item): record_of(item) for item in routines},
        tokens=TokenIndex(
            vocabulary=[],
            files={},
            routines={
                key_of(item).token: RoutineShape(
                    path=item.path,
                    start=item.start,
                    end=last_of(item),
                    shape=list(item.shape),
                    statements=None if item.statements is None else int(item.statements),
                )
                for item in routines
            },
            unreadable=list(unreadable),
        ),
    )


def run(
    after: ProjectSnapshot,
    affected: Sequence[Routine] = (),
    limits: SimilarLimits = DEFAULTS,
    severity: str = "warning",
) -> tuple[list[Finding], tuple[str, ...]]:
    """The rule's two halves, so a test can assert on either without unpacking noise."""
    outcome = find_similar_routines(
        after,
        [key_of(item) for item in affected],
        severity,  # type: ignore[arg-type]
        limits,
    )
    return outcome.findings, outcome.unavailable


def family_of(count: int, shape: tuple[int, ...] = BASE) -> list[Routine]:
    """``count`` routines of one shape, one per file: the twelve-normalize shape."""
    return [
        Routine(f"src/app/mod{number:02d}.py", f"mod{number:02d}.normalize", shape)
        for number in range(count)
    ]
