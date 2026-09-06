"""Whether the installed build can tell a called routine from an uncalled one (6.1, 6.2).

The rule rests on one flag and the flag rests on ``Ent.refs("callby, useby")``. Nothing in the
unit tests can say whether that selector answers anything at all on a real database, or
whether Understand records a reference for the shapes this project actually contains -- a
method reached through an instance, a classmethod nobody calls, a module-level entry point.

The fixture is read in both directions on purpose. ``Engine.run`` is called through
``entry_point`` and must come back referenced; ``Engine.label`` is a staticmethod nothing
names and must come back unreferenced. A build that answered the same for both -- in either
direction -- would pass a one-sided test and make the rule either silent or unusable.
"""

from __future__ import annotations

import pytest
from contract_project import (
    FILES,
    SampleProject,
    contract_settings,
    extract_with,
    sample_project,  # noqa: F401 -- imported so the session fixture is registered here
)

pytestmark = pytest.mark.contract

CALLED = "Engine.run"
"""Called by ``entry_point``, which is called by ``main``; the fixture's live path."""

UNCALLED = "Engine.label"
"""A staticmethod nothing in the fixture names. ``Engine.build`` is the other one."""


def flags(project: SampleProject, asked: bool) -> dict[str, bool | None]:
    """Each recorded routine's ``referenced`` flag, keyed by the tail of its long name."""
    settings = contract_settings()
    settings.structure.unused_routines = "warning" if asked else None
    snapshot = extract_with(project.db("alpha"), project.root("alpha"), FILES, settings)
    return {
        _tail(key.longname): record.referenced
        for key, record in snapshot.entities.items()
        if key.scope == "routine"
    }


def _tail(longname: str) -> str:
    """The last two segments of a qualified name, which is how the fixture is described."""
    return ".".join(longname.split(".")[-2:])


def test_contract_a_called_routine_comes_back_referenced(
    sample_project: SampleProject,  # noqa: F811
) -> None:
    found = flags(sample_project, asked=True)

    assert found.get(CALLED) is True, found


def test_contract_a_routine_nothing_names_comes_back_unreferenced(
    sample_project: SampleProject,  # noqa: F811
) -> None:
    """The finding the whole rule exists for, measured rather than assumed."""
    found = flags(sample_project, asked=True)

    assert found.get(UNCALLED) is False, found


def test_contract_a_run_that_did_not_ask_records_nothing(
    sample_project: SampleProject,  # noqa: F811
) -> None:
    """Requirement 6.4: absence of a measurement must not read as a project of dead code."""
    found = flags(sample_project, asked=False)

    assert set(found.values()) == {None}, found
