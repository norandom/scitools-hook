"""The affected set of one staged change (task 4.6; req 4.2, 4.9, 4.10).

Most cases build tiny synthetic snapshots, because the resolver is a set computation over a
staged file list and two dependency graphs. Three cases run on the synthetic project of
``tests/fixtures``, where ``src/analysis/rules.py`` gains an edge to ``src/analysis/engine.py``
and ``src/cli/app.py`` gains one to ``src/understand/adapter.py``: staging ``app.py`` alone
must pull ``rules.py`` in, because its dependency set is what changed (req 4.2), and the same
input with no ``before`` side must not.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from fixtures import ADAPTER, APP, ENGINE, RULES, TEXT, snapshot_fixture

from scitools_hook.analysis.affected import resolve
from scitools_hook.models.change import AffectedSet
from scitools_hook.models.git import StagedChange
from scitools_hook.models.snapshot import (
    DepEdge,
    EntityKey,
    EntityRecord,
    EntityRef,
    ProjectSnapshot,
    Side,
)


def edge(src: str, dst: str, refs: int = 1) -> DepEdge:
    """One file dependency edge with a reference count."""
    return DepEdge(src=src, dst=dst, refs=refs)


def file_record(path: str) -> EntityRecord:
    """The record a snapshot holds for a file entity."""
    key = EntityKey(scope="file", path=path, longname=path)
    return EntityRecord(
        ref=EntityRef(key=key, kind="File", name=path.rpartition("/")[2], line=1),
        language="Python",
    )


def snap(
    side: Side, edges: Sequence[DepEdge] = (), records: Sequence[EntityRecord] = ()
) -> ProjectSnapshot:
    """A snapshot carrying nothing but the file edges and records an affected-set test needs."""
    return ProjectSnapshot(
        side=side,
        file_edges=list(edges),
        entities={record.key: record for record in records},
    )


def change(
    status: Literal["A", "M", "D", "R"], path: str, old_path: str | None = None
) -> StagedChange:
    """One ``git diff --name-status -M`` entry."""
    return StagedChange(status=status, path=path, old_path=old_path)


def paths_of(keys: set[EntityKey]) -> set[str]:
    """The files the affected entities are defined in."""
    return {key.path for key in keys}


def test_staged_files_are_affected_and_staged_deletions_are_not() -> None:
    """``files`` are the staged paths that still exist; a deletion is only a deletion (4.2)."""
    before = snap("before", [edge("a.py", "gone.py")])
    after = snap("after", [edge("a.py", "b.py")])

    staged = [change("A", "b.py"), change("M", "a.py"), change("D", "gone.py")]

    affected = resolve(staged, after, before)

    assert affected.files == {"a.py", "b.py"}
    assert affected.deleted_files == {"gone.py"}


def test_a_dependent_file_is_pulled_in_because_its_dependency_set_changed() -> None:
    """Staging ``app.py`` alone affects ``rules.py``, whose dependency set changed (req 4.2)."""
    after, before = snapshot_fixture("after"), snapshot_fixture("before")

    affected = resolve([change("M", APP)], after, before)

    assert affected.files == {APP, RULES}
    assert affected.deleted_files == set()
    assert paths_of(affected.keys) == {APP, RULES}
    assert len(affected.keys) == 6
    assert affected.neighbourhood == {ENGINE, ADAPTER, TEXT}


def test_a_file_is_pulled_in_by_a_lost_dependency_too() -> None:
    """A dependency set that shrank differs just as much as one that grew (req 4.2)."""
    before = snap("before", [edge("keeper.py", "dropped.py"), edge("keeper.py", "kept.py")])
    after = snap("after", [edge("keeper.py", "kept.py"), edge("stable.py", "kept.py")])

    affected = resolve([change("M", "stable.py")], after, before)

    assert affected.files == {"stable.py", "keeper.py"}


def test_a_dependency_swap_of_equal_size_is_still_a_dependency_change() -> None:
    """A file that swaps one dependency for another is affected (req 4.2).

    Both sides hold exactly one target, so a comparison by count would see nothing. The
    targets differ, and this is the shape that closes a new cycle -- ``swapper.py`` now
    depends on ``engine.py``, which already depended on it (req 6.1) -- so the sets
    themselves must be compared, never their sizes.
    """
    before = snap("before", [edge("swapper.py", "util.py"), edge("engine.py", "swapper.py")])
    after = snap("after", [edge("swapper.py", "engine.py"), edge("engine.py", "swapper.py")])

    affected = resolve([change("M", "other.py")], after, before)

    assert affected.files == {"other.py", "swapper.py"}


def test_a_reference_count_change_alone_does_not_affect_a_file() -> None:
    """The rule compares dependency *sets*: the same targets are the same dependencies (4.2)."""
    before = snap("before", [edge("busy.py", "shared.py", refs=1)])
    after = snap("after", [edge("busy.py", "shared.py", refs=9)])

    affected = resolve([change("M", "other.py")], after, before)

    assert affected.files == {"other.py"}


def test_a_deletions_only_change_yields_no_files_and_former_dependents() -> None:
    """Only deletions: ``files`` is empty, the neighbourhood is who depended on them (4.10).

    The deleted file has dependencies of its own, and losing all of them is the largest
    dependency-set difference there is: it still must not make the file an affected one.
    """
    before = snap(
        "before",
        [
            edge("dependent.py", "doomed.py", refs=3),
            edge("other.py", "doomed.py"),
            edge("doomed.py", "lib.py", refs=2),
        ],
        [file_record("doomed.py"), file_record("dependent.py")],
    )
    after = snap("after", records=[file_record("dependent.py")])

    affected = resolve([change("D", "doomed.py")], after, before)

    assert affected.files == set()
    assert affected.keys == set()
    assert affected.deleted_files == {"doomed.py"}
    assert affected.neighbourhood == {"dependent.py", "other.py"}


def test_a_deleted_file_is_never_a_neighbour() -> None:
    """A file the change removed is nothing to evaluate, so it stays out of the set (4.10)."""
    before = snap("before", [edge("also_doomed.py", "doomed.py"), edge("survivor.py", "doomed.py")])
    after = snap("after")

    affected = resolve([change("D", "doomed.py"), change("D", "also_doomed.py")], after, before)

    assert affected.files == set()
    assert affected.deleted_files == {"doomed.py", "also_doomed.py"}
    assert affected.neighbourhood == {"survivor.py"}


def test_losing_a_dependency_to_a_deleted_file_is_not_a_dependency_change() -> None:
    """An edit plus a deletion: the dependent of the deleted file is only a neighbour (4.10)."""
    before = snap(
        "before",
        [edge("edited.py", "kept.py"), edge("dependent.py", "doomed.py")],
        [file_record("edited.py"), file_record("dependent.py")],
    )
    after = snap(
        "after",
        [edge("edited.py", "kept.py")],
        [file_record("edited.py"), file_record("dependent.py")],
    )

    affected = resolve([change("M", "edited.py"), change("D", "doomed.py")], after, before)

    assert affected.files == {"edited.py"}
    assert affected.deleted_files == {"doomed.py"}
    assert paths_of(affected.keys) == {"edited.py"}
    assert affected.neighbourhood == {"kept.py", "dependent.py"}


def test_a_rename_affects_the_new_path_and_deletes_the_old() -> None:
    """``R`` carries both ends: the new path is affected, the old one is a deletion (req 4.2)."""
    before = snap("before", [edge("caller.py", "old.py"), edge("old.py", "helper.py")])
    after = snap(
        "after", [edge("caller.py", "new.py"), edge("new.py", "helper.py")], [file_record("new.py")]
    )

    affected = resolve([change("R", "new.py", old_path="old.py")], after, before)

    assert affected.files == {"new.py", "caller.py"}
    assert affected.deleted_files == {"old.py"}
    assert paths_of(affected.keys) == {"new.py"}
    assert affected.neighbourhood == {"helper.py"}


def test_a_rename_without_an_old_path_contributes_only_its_new_path() -> None:
    """Git always reports the old path of a rename; missing, only the new path is known."""
    after = snap("after", records=[file_record("new.py")])

    affected = resolve([change("R", "new.py")], after, None)

    assert affected.files == {"new.py"}
    assert affected.deleted_files == set()


def test_a_staged_file_understand_could_not_parse_is_affected_without_entities() -> None:
    """A path in no snapshot at all is still staged; it contributes no keys and no crash (4.2)."""
    before = snap("before", [edge("parsed.py", "dep.py")], [file_record("parsed.py")])
    after = snap("after", [edge("parsed.py", "dep.py")], [file_record("parsed.py")])

    affected = resolve([change("A", "notes.txt"), change("M", "parsed.py")], after, before)

    assert affected.files == {"notes.txt", "parsed.py"}
    assert paths_of(affected.keys) == {"parsed.py"}
    assert affected.neighbourhood == {"dep.py"}


def test_without_a_before_side_only_staged_files_are_affected() -> None:
    """Whole-project mode has nothing to compare, so the dependency clause cannot fire (4.8)."""
    after = snapshot_fixture("after")

    affected = resolve([change("M", APP)], after, None)

    assert affected.files == {APP}
    assert paths_of(affected.keys) == {APP}
    assert affected.neighbourhood == {ENGINE, RULES, ADAPTER, TEXT}


def test_without_a_before_side_deletions_have_no_former_dependents() -> None:
    """Former dependents come from the before graph; without one there are none (4.10)."""
    after = snap("after", [edge("survivor.py", "kept.py")])

    affected = resolve([change("D", "doomed.py")], after, None)

    assert affected.files == set()
    assert affected.deleted_files == {"doomed.py"}
    assert affected.neighbourhood == set()


def test_the_neighbourhood_holds_direct_dependents_and_dependencies_only() -> None:
    """One step out in both directions: what cycle and fan rules need, and no more (req 4.2)."""
    edges = [edge("dependent.py", "mid.py"), edge("mid.py", "used.py"), edge("used.py", "far.py")]
    snapshot = snap("after", edges)

    affected = resolve([change("M", "mid.py")], snapshot, snap("before", edges))

    assert affected.files == {"mid.py"}
    assert affected.neighbourhood == {"dependent.py", "used.py"}


def test_the_neighbourhood_excludes_the_affected_files_themselves() -> None:
    """The neighbourhood is what surrounds the change, so members of ``files`` drop out (4.2)."""
    edges = [edge("a.py", "b.py"), edge("b.py", "a.py"), edge("a.py", "a.py")]
    snapshot = snap("after", edges)

    affected = resolve([change("M", "a.py"), change("M", "b.py")], snapshot, snap("before", edges))

    assert affected.files == {"a.py", "b.py"}
    assert affected.neighbourhood == set()


def test_an_empty_staged_list_yields_an_entirely_empty_affected_set() -> None:
    """Nothing staged is nothing to analyze, whatever the two snapshots differ in (req 4.9)."""
    affected = resolve([], snapshot_fixture("after"), snapshot_fixture("before"))

    assert affected == AffectedSet()
    assert affected.files == set()
    assert affected.deleted_files == set()
    assert affected.keys == set()
    assert affected.neighbourhood == set()


def test_the_same_input_resolves_to_the_same_affected_set() -> None:
    """Sets serialize sorted, so two runs over one change are byte-identical (task 3.1)."""
    after, before = snapshot_fixture("after"), snapshot_fixture("before")
    staged = [change("M", APP), change("D", TEXT)]

    first, second = resolve(staged, after, before), resolve(list(reversed(staged)), after, before)

    assert first == second
    assert first.model_dump_json() == second.model_dump_json()
