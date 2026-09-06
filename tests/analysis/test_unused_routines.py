"""Affected routines nothing in the project references (requirement 6).

The shape this catches is dead code an agent forgot to delete: asked to replace one
implementation with another, an agent writes the new one, wires it in, and leaves the old one
sitting there. It still parses, every complexity limit is satisfied by code nobody runs, and
nothing in a diff says that nobody calls it any more.

Four properties, and each is a way the rule could be worse than nothing:

* **It reports only affected routines.** This is a gate on a commit, not an audit; a dead
  routine the change never touched is somebody else's finding.
* **A deleted routine cannot appear**, and needs no rule to say so -- it is not in the after
  snapshot at all (requirement 6.5).
* **A missing measurement is not a project of dead code.** ``referenced is None`` reports the
  rule once and evaluates nothing (requirement 6.4).
* **The ignore list is load-bearing.** A dunder, a collected test, an entry point named in
  packaging metadata and a decorated handler all have no reference *because there is none to
  see*, which is a property of the language and not of the analysis (requirement 6.3).
"""

from __future__ import annotations

from typing import Final

from scitools_hook.analysis.structure.unused import (
    RULE,
    UNAVAILABLE,
    find_unused_routines,
)
from scitools_hook.config.models import DEFAULT_UNUSED_IGNORE
from scitools_hook.models.snapshot import EntityKey, EntityRecord, EntityRef, ProjectSnapshot

PATH: Final = "pkg/core.py"


def key(longname: str, path: str = PATH) -> EntityKey:
    """One routine's identity, as the extraction names it."""
    return EntityKey(scope="routine", path=path, longname=longname, parameters="")


def record(longname: str, referenced: bool | None, path: str = PATH) -> EntityRecord:
    """One recorded routine with the flag under test."""
    return EntityRecord(
        ref=EntityRef(
            key=key(longname, path), kind="Function", name=longname.split(".")[-1], line=7
        ),
        language="Python",
        referenced=referenced,
    )


def a_snapshot(*routines: tuple[str, bool | None]) -> ProjectSnapshot:
    """An after snapshot holding the named routines and their flags."""
    return ProjectSnapshot(
        side="after",
        entities={key(longname): record(longname, flag) for longname, flag in routines},
    )


def rules_of(found: object) -> list[str]:
    """The rule names of what the evaluation produced."""
    return [finding.rule for finding in found.findings]  # type: ignore[attr-defined]


# --- the finding -------------------------------------------------------------------------


def test_a_routine_nothing_references_is_reported() -> None:
    snapshot = a_snapshot(("core.stale", False))

    found = find_unused_routines(snapshot, [key("core.stale")])

    assert rules_of(found) == [RULE]
    assert found.findings[0].scope == "routine"
    assert found.findings[0].path == PATH
    assert found.findings[0].line == 7
    assert "core.stale" in found.findings[0].message


def test_it_is_a_warning_by_default_and_does_not_block() -> None:
    """Requirement 6.3: the false positives are a property of the language."""
    found = find_unused_routines(a_snapshot(("core.stale", False)), [key("core.stale")])

    assert found.findings[0].severity == "warning"
    assert found.findings[0].blocking is False


def test_an_operator_may_make_it_block() -> None:
    found = find_unused_routines(a_snapshot(("core.stale", False)), [key("core.stale")], "error")

    assert found.findings[0].severity == "error"
    assert found.findings[0].blocking is True


def test_a_referenced_routine_is_not_reported() -> None:
    found = find_unused_routines(a_snapshot(("core.live", True)), [key("core.live")])

    assert found.findings == []


def test_a_routine_the_change_did_not_touch_is_not_reported() -> None:
    """A gate on a commit, not an audit: the dead code somebody else left is theirs."""
    snapshot = a_snapshot(("core.stale", False), ("core.live", True))

    found = find_unused_routines(snapshot, [key("core.live")])

    assert found.findings == []


def test_a_deleted_routine_cannot_appear() -> None:
    """Requirement 6.5, and it needs no code: a deletion is absent from the after side."""
    snapshot = a_snapshot(("core.live", True))

    found = find_unused_routines(snapshot, [key("core.live"), key("core.deleted")])

    assert found.findings == []


def test_only_routines_are_judged() -> None:
    """A file or a class is not a thing this rule has an opinion about."""
    snapshot = a_snapshot(("core.stale", False))
    a_file = EntityKey(scope="file", path=PATH, longname=PATH, parameters=None)

    found = find_unused_routines(snapshot, [a_file])

    assert found.findings == []


# --- the ignore list (requirement 6.3) ----------------------------------------------------


def test_the_shipped_list_excuses_a_dunder() -> None:
    """The interpreter calls it and never by name, so there is no reference to find."""
    snapshot = a_snapshot(("core.Engine.__enter__", False))

    found = find_unused_routines(
        snapshot, [key("core.Engine.__enter__")], "warning", DEFAULT_UNUSED_IGNORE
    )

    assert found.findings == []


def test_the_shipped_list_excuses_a_collected_test_and_an_entry_point() -> None:
    snapshot = a_snapshot(("suite.test_widening", False), ("cli.main", False))

    found = find_unused_routines(
        snapshot,
        [key("suite.test_widening"), key("cli.main")],
        "warning",
        DEFAULT_UNUSED_IGNORE,
    )

    assert found.findings == []


def test_a_pattern_of_the_operators_own_excuses_a_handler() -> None:
    snapshot = a_snapshot(("web.on_request", False))

    found = find_unused_routines(snapshot, [key("web.on_request")], "warning", [r"\.on_\w+$"])

    assert found.findings == []


def test_a_name_no_pattern_matches_is_still_reported() -> None:
    """The list must excuse shapes, not everything: an empty rule is a rule nobody reads."""
    snapshot = a_snapshot(("core.stale", False))

    found = find_unused_routines(snapshot, [key("core.stale")], "warning", DEFAULT_UNUSED_IGNORE)

    assert rules_of(found) == [RULE]


# --- no measurement (requirement 6.4) ------------------------------------------------------


def test_a_snapshot_without_the_flag_reports_the_rule_once_and_judges_nothing() -> None:
    """A warm cache holding an extraction from before the rule was turned on."""
    snapshot = a_snapshot(("core.one", None), ("core.two", None))

    found = find_unused_routines(snapshot, [key("core.one"), key("core.two")])

    assert found.findings == []
    assert found.unavailable == UNAVAILABLE
    assert "db rebuild" in found.unavailable


def test_a_snapshot_that_measured_some_of_them_is_not_unavailable() -> None:
    """One ``None`` among measured routines is that routine's absence, not the rule's."""
    snapshot = a_snapshot(("core.stale", False), ("core.unknown", None))

    found = find_unused_routines(snapshot, [key("core.stale"), key("core.unknown")])

    assert rules_of(found) == [RULE]
    assert found.unavailable == ""


def test_a_change_with_no_affected_routine_says_nothing_at_all() -> None:
    """Requirement 6.4 is about a run that could not measure, not one with nothing to measure."""
    found = find_unused_routines(a_snapshot(), [])

    assert found.findings == []
    assert found.unavailable == ""
