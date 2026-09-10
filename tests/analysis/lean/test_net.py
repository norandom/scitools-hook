"""The net delta of a change, and the optional growth finding (req 7.1-7.5).

Four of these tests are the task's own definition of done, and each one guards a different
way the arithmetic goes quietly wrong: a deleted file that counts as nothing rather than as a
reduction, a routine whose parameter list changed counted once as removed and once as added,
a whole-project run answering ``0`` where it has nothing to subtract from, and a maximum that
fires at the limit rather than past it.

The snapshots are built by hand for the reason ``test_layering`` gives: a delta is a statement
about *two* databases, and no repository supplies a stable pair of them.
"""

from __future__ import annotations

from typing import Final

from scitools_hook.analysis.lean.net import net_delta, net_growth_finding
from scitools_hook.models.change import AffectedSet, NetDelta
from scitools_hook.models.snapshot import (
    EntityKey,
    EntityRecord,
    EntityRef,
    ProjectSnapshot,
    Side,
)

SERVICE: Final = "src/app/service.py"
GONE: Final = "src/app/legacy.py"
OUTSIDE: Final = "src/app/untouched.py"


def routine(
    path: str,
    longname: str,
    statements: float,
    lines: float,
    parameters: str = "(request)",
) -> EntityRecord:
    """One routine record, carrying the two counts the delta is summed over."""
    key = EntityKey(scope="routine", path=path, longname=longname, parameters=parameters)
    return EntityRecord(
        ref=EntityRef(key=key, kind="Function", name=longname.rpartition(".")[2], line=1),
        language="Python",
        metrics={"CountStmt": statements, "CountLineCode": lines},
    )


def unmeasured(path: str, longname: str, **counts: float) -> EntityRecord:
    """A routine record carrying only the counts named, and nothing for the rest."""
    key = EntityKey(scope="routine", path=path, longname=longname, parameters="(request)")
    return EntityRecord(
        ref=EntityRef(key=key, kind="Function", name=longname.rpartition(".")[2], line=1),
        language="Python",
        metrics=dict(counts),
    )


def file_record(path: str, statements: float, lines: float) -> EntityRecord:
    """The file's own record, whose counts are the whole file and must not be summed."""
    key = EntityKey(scope="file", path=path, longname=path)
    return EntityRecord(
        ref=EntityRef(key=key, kind="File", name=path.rpartition("/")[2], line=1),
        language="Python",
        metrics={"CountStmt": statements, "CountLineCode": lines},
    )


def snap(side: Side, records: list[EntityRecord]) -> ProjectSnapshot:
    """One side of the change, carrying only what the delta reads."""
    return ProjectSnapshot(side=side, entities={record.key: record for record in records})


def touching(*files: str, deleted: tuple[str, ...] = ()) -> AffectedSet:
    """The change's file set, with the paths it deleted named separately."""
    return AffectedSet(files=set(files), deleted_files=set(deleted))


# --- the arithmetic ---------------------------------------------------------------


def test_a_routine_that_grew_is_a_positive_delta() -> None:
    """Requirement 7.1: after minus before, in statements and in source lines."""
    after = snap("after", [routine(SERVICE, "app.handle", 12, 20)])
    before = snap("before", [routine(SERVICE, "app.handle", 8, 14)])

    delta = net_delta(after, before, touching(SERVICE))

    assert delta == NetDelta(statements=4, lines=6, routines=1)


def test_a_routine_that_shrank_is_a_negative_delta() -> None:
    """The direction the rules exist to encourage, and the one ponytail cannot report."""
    after = snap("after", [routine(SERVICE, "app.handle", 8, 14)])
    before = snap("before", [routine(SERVICE, "app.handle", 12, 23)])

    delta = net_delta(after, before, touching(SERVICE))

    assert delta == NetDelta(statements=-4, lines=-9, routines=1)


def test_an_added_routine_counts_its_whole_size() -> None:
    """Requirement 7.2: no before side for this key, so the missing side counts as zero."""
    after = snap(
        "after",
        [routine(SERVICE, "app.handle", 8, 14), routine(SERVICE, "app.retry", 5, 9)],
    )
    before = snap("before", [routine(SERVICE, "app.handle", 8, 14)])

    delta = net_delta(after, before, touching(SERVICE))

    assert delta == NetDelta(statements=5, lines=9, routines=2)


def test_a_deleted_file_counts_negative() -> None:
    """Requirement 7.2: a file the change removed contributes its routines as a reduction."""
    after = snap("after", [routine(SERVICE, "app.handle", 8, 14)])
    before = snap(
        "before",
        [
            routine(SERVICE, "app.handle", 8, 14),
            routine(GONE, "legacy.render", 9, 15),
            routine(GONE, "legacy.escape", 4, 7),
        ],
    )

    delta = net_delta(after, before, touching(SERVICE, deleted=(GONE,)))

    assert delta == NetDelta(statements=-13, lines=-22, routines=3)


def test_a_routine_deleted_from_a_surviving_file_counts_negative() -> None:
    """The same subtraction where the file stayed: ``files``, not only ``deleted_files``."""
    after = snap("after", [routine(SERVICE, "app.handle", 8, 14)])
    before = snap(
        "before",
        [routine(SERVICE, "app.handle", 8, 14), routine(SERVICE, "app.retry", 5, 9)],
    )

    delta = net_delta(after, before, touching(SERVICE))

    assert delta == NetDelta(statements=-5, lines=-9, routines=2)


def test_a_replacement_that_removes_more_than_it_adds_reads_as_a_reduction() -> None:
    """Requirement 7.2 in one change: two routines gone, one shorter one in their place."""
    after = snap("after", [routine(SERVICE, "app.render", 6, 11)])
    before = snap(
        "before",
        [routine(GONE, "legacy.render", 9, 15), routine(GONE, "legacy.escape", 4, 7)],
    )

    delta = net_delta(after, before, touching(SERVICE, deleted=(GONE,)))

    assert delta == NetDelta(statements=-7, lines=-11, routines=3)


def test_a_change_that_replaced_exactly_what_it_removed_is_zero_and_not_nothing() -> None:
    """Zero is a measurement. Requirement 7.4 reserves ``None`` for having nothing to compare."""
    after = snap("after", [routine(SERVICE, "app.handle", 8, 14)])
    before = snap("before", [routine(SERVICE, "app.handle", 8, 14)])

    delta = net_delta(after, before, touching(SERVICE))

    assert delta == NetDelta(statements=0, lines=0, routines=1)


# --- what the delta refuses to count ----------------------------------------------


def test_a_whole_project_run_has_no_delta() -> None:
    """Requirement 7.4: ``--all`` has no before side, so the answer is absence, not zero."""
    after = snap("after", [routine(SERVICE, "app.handle", 8, 14)])

    assert net_delta(after, None, touching(SERVICE)) is None


def test_a_routine_outside_the_affected_set_is_not_counted() -> None:
    """The delta is the change's own figure: a file it did not touch contributes nothing."""
    after = snap(
        "after",
        [routine(SERVICE, "app.handle", 12, 20), routine(OUTSIDE, "other.load", 40, 60)],
    )
    before = snap(
        "before",
        [routine(SERVICE, "app.handle", 8, 14), routine(OUTSIDE, "other.load", 3, 5)],
    )

    delta = net_delta(after, before, touching(SERVICE))

    assert delta == NetDelta(statements=4, lines=6, routines=1)


def test_the_file_record_of_an_affected_file_is_not_counted() -> None:
    """Both counts exist at file scope too; summing them would count every line twice."""
    after = snap(
        "after",
        [routine(SERVICE, "app.handle", 12, 20), file_record(SERVICE, 120, 200)],
    )
    before = snap(
        "before",
        [routine(SERVICE, "app.handle", 8, 14), file_record(SERVICE, 80, 140)],
    )

    delta = net_delta(after, before, touching(SERVICE))

    assert delta == NetDelta(statements=4, lines=6, routines=1)


def test_a_missing_count_counts_as_zero_rather_than_dropping_the_routine() -> None:
    """A record without ``CountStmt`` still moves ``lines``, and is still one routine."""
    after = snap("after", [unmeasured(SERVICE, "app.handle", CountLineCode=20)])
    before = snap("before", [routine(SERVICE, "app.handle", 8, 14)])

    delta = net_delta(after, before, touching(SERVICE))

    assert delta == NetDelta(statements=-8, lines=6, routines=1)


# --- pairing, which is what stops one routine being counted twice ------------------


def test_a_renamed_signature_is_one_routine_and_not_two() -> None:
    """A changed parameter list is a different ``EntityKey``; the ratchet's pairing joins it.

    The sums agree either way -- ``+12`` and ``-8`` total the same as ``12 - 8`` -- so the
    fact that fails without pairing is :attr:`NetDelta.routines`, which is the figure the
    report prints beside the delta ("over N routines"). Two is the wrong answer: one routine
    was edited, not one deleted and another written.
    """
    after = snap("after", [routine(SERVICE, "app.handle", 12, 20, parameters="(request, retry)")])
    before = snap("before", [routine(SERVICE, "app.handle", 8, 14, parameters="(request)")])

    delta = net_delta(after, before, touching(SERVICE))

    assert delta == NetDelta(statements=4, lines=6, routines=1)


def test_an_overload_added_beside_an_existing_one_is_not_a_rename() -> None:
    """The ratchet pairs a family only when exactly one key was added and one removed."""
    after = snap(
        "after",
        [
            routine(SERVICE, "app.handle", 8, 14, parameters="(request)"),
            routine(SERVICE, "app.handle", 5, 9, parameters="(request, retry)"),
        ],
    )
    before = snap("before", [routine(SERVICE, "app.handle", 8, 14, parameters="(request)")])

    delta = net_delta(after, before, touching(SERVICE))

    assert delta == NetDelta(statements=5, lines=9, routines=2)


def test_a_rename_in_an_untouched_file_does_not_enter_the_sum() -> None:
    """Pairing is project-wide; the population is not. The affected filter comes first."""
    after = snap("after", [routine(OUTSIDE, "other.load", 40, 60, parameters="(path, mode)")])
    before = snap("before", [routine(OUTSIDE, "other.load", 3, 5, parameters="(path)")])

    delta = net_delta(after, before, touching(SERVICE))

    assert delta == NetDelta(statements=0, lines=0, routines=0)


# --- the optional maximum ---------------------------------------------------------


def test_no_finding_below_the_configured_maximum() -> None:
    """Requirement 7.5: the delta never blocks by default, and reports nothing under a limit."""
    delta = NetDelta(statements=12, lines=30, routines=7)

    assert net_growth_finding(delta, 50, "warning") is None


def test_no_finding_at_the_configured_maximum() -> None:
    """Exceeding is strict: a change landing exactly on the maximum is inside it."""
    delta = NetDelta(statements=50, lines=90, routines=7)

    assert net_growth_finding(delta, 50, "warning") is None


def test_a_reduction_is_never_a_finding() -> None:
    """A maximum of zero is legal, and a change that removed lines is still under it."""
    delta = NetDelta(statements=-4, lines=-9, routines=3)

    assert net_growth_finding(delta, 0, "error") is None


def test_growth_past_the_maximum_is_a_project_scope_finding() -> None:
    """Requirement 7.5: one finding about the run, at the severity the operator configured."""
    delta = NetDelta(statements=64, lines=120, routines=7)

    finding = net_growth_finding(delta, 50, "warning")

    assert finding is not None
    assert finding.kind == "structural"
    assert finding.rule == "structure.net_growth"
    assert finding.scope == "project"
    assert finding.metric is None
    assert finding.path == ""
    assert finding.entity is None
    assert finding.value == 64
    assert finding.before is None
    assert finding.limit == 50
    assert finding.limit_source == "config"
    assert finding.severity == "warning"
    assert finding.blocking is False
    assert finding.preexisting is False
    assert finding.hint == ""
    assert finding.details == {"net_lines": 120, "net_routines": 7}
    assert "64" in finding.message
    assert "50" in finding.message


def test_a_maximum_of_zero_reports_any_growth() -> None:
    """``ge=0`` on the setting means "this change may not make the project longer"."""
    finding = net_growth_finding(NetDelta(statements=1, lines=2, routines=1), 0, "warning")

    assert finding is not None
    assert finding.value == 1


def test_an_error_severity_makes_the_growth_finding_blocking() -> None:
    """Requirement 7.5: it blocks only where the operator asked for an error, as elsewhere."""
    finding = net_growth_finding(NetDelta(statements=64, lines=120, routines=7), 50, "error")

    assert finding is not None
    assert finding.severity == "error"
    assert finding.blocking is True
