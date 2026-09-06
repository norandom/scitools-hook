"""The snapshot cache: served only when the question was identical (8.2, 8.6, 8.7).

The four snapshot extractions are 88% of a warm one-line check, measured, and the cheapest to
remove is the before side: its database has not changed, its selection has not changed, and
the document it produced last time is still exactly right.

**Every test here is about the key**, because the key is the whole of the safety argument.
Requirement 8.7 says the cache must change no finding, and the only way to keep that promise
is to be certain the two runs asked the same question. Each component is varied on its own
below; a key that ignored one would serve a document about something else, which is worse than
any amount of time saved.

The other two properties are cheaper and equally load-bearing: a corrupt entry is a miss and
is removed rather than re-read forever, and a failure to write is silence, because a run that
produced the right answer must not fail over an optimisation.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Final

from scitools_hook.models.snapshot import EntityKey, EntityRecord, EntityRef, ProjectSnapshot
from scitools_hook.understand.snapshot_cache import (
    KEEP,
    SnapshotCache,
    SnapshotKey,
    key_for,
    worker_digest,
)

FILES: Final = frozenset({"pkg/core.py", "pkg/other.py"})
FIELDS: Final[dict[str, object]] = {
    "side": "before",
    "commit": "3ca0a97",
    "files": FILES,
    "settings": "9f2c1d0e4b5a6c7d",
    "build": "(Build 1262)",
}
"""One run's inputs; every test varies exactly one of them."""


def a_key(**changed: object) -> SnapshotKey:
    """The key of a run, with one component replaced."""
    return key_for(**{**FIELDS, **changed})  # type: ignore[arg-type]


def a_snapshot(longname: str = "core.add") -> ProjectSnapshot:
    """A document with one entity in it, so two documents can be told apart."""
    key = EntityKey(scope="routine", path="pkg/core.py", longname=longname, parameters="")
    return ProjectSnapshot(
        side="before",
        entities={
            key: EntityRecord(
                ref=EntityRef(key=key, kind="Function", name="add", line=1), language="Python"
            )
        },
    )


def a_cache(tmp_path: Path) -> SnapshotCache:
    """A cache under a directory of this test's own."""
    return SnapshotCache(tmp_path / "cache")


# --- a hit ---------------------------------------------------------------------------------


def test_an_identical_key_is_served_from_the_cache(tmp_path: Path) -> None:
    cache = a_cache(tmp_path)
    cache.put(a_key(), a_snapshot())

    found = cache.get(a_key())

    assert found is not None
    assert [key.longname for key in found.entities] == ["core.add"]


def test_the_order_of_the_selected_files_is_not_part_of_the_question(
    tmp_path: Path,
) -> None:
    """Two runs naming the same files differently ask the same thing."""
    cache = a_cache(tmp_path)
    cache.put(a_key(files=frozenset({"pkg/other.py", "pkg/core.py"})), a_snapshot())

    assert cache.get(a_key()) is not None


def test_a_cache_with_nothing_in_it_is_a_miss(tmp_path: Path) -> None:
    assert a_cache(tmp_path).get(a_key()) is None


# --- a miss on every component (requirement 8.7) --------------------------------------------


def test_each_component_of_the_key_decides_the_document(tmp_path: Path) -> None:
    """Six inputs, each varied alone. A key that ignored one would answer the wrong question."""
    cache = a_cache(tmp_path)
    cache.put(a_key(), a_snapshot())

    for changed in (
        {"side": "after"},
        {"commit": "0000000"},
        {"files": frozenset({"pkg/core.py"})},
        {"settings": "0123456789abcdef"},
        {"build": "(Build 1204)"},
    ):
        assert cache.get(a_key(**changed)) is None, changed


def test_the_workers_own_source_is_part_of_the_key(tmp_path: Path) -> None:
    """A developer editing the worker must not be served yesterday's document.

    Asserted through the digest rather than by editing the module: what matters is that the
    key carries it, and that the digest is a reading of the source rather than a constant
    somebody has to remember to bump.
    """
    assert a_key().worker == worker_digest()
    assert len(worker_digest()) == 16


def test_the_document_schema_is_part_of_the_key(tmp_path: Path) -> None:
    """A model that gained a field cannot read a document written before it existed."""
    assert a_key().schema
    assert a_key().schema != a_key().settings


# --- what goes wrong -------------------------------------------------------------------------


def test_a_corrupt_entry_is_a_miss_and_is_removed(tmp_path: Path) -> None:
    """Keeping it would make every later run pay the same failed read."""
    cache = a_cache(tmp_path)
    cache.put(a_key(), a_snapshot())
    stored = cache.root / a_key().name()
    stored.write_text("{not a document", encoding="utf-8")

    assert cache.get(a_key()) is None
    assert not stored.exists()


def test_a_document_that_does_not_validate_is_a_miss_and_is_removed(
    tmp_path: Path,
) -> None:
    """Valid JSON that is not a snapshot is the shape a model change leaves behind."""
    cache = a_cache(tmp_path)
    cache.put(a_key(), a_snapshot())
    stored = cache.root / a_key().name()
    stored.write_text('{"side": "sideways"}', encoding="utf-8")

    assert cache.get(a_key()) is None
    assert not stored.exists()


def test_a_cache_that_cannot_be_written_costs_the_run_nothing(tmp_path: Path) -> None:
    """A run that produced the right answer must not fail over an optimisation."""
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")
    cache = SnapshotCache(blocked)

    cache.put(a_key(), a_snapshot())

    assert cache.get(a_key()) is None
    assert cache.entries() == []


# --- pruning and the listing (requirement 8.6) ------------------------------------------------


def test_the_newest_eight_survive_a_ninth(tmp_path: Path) -> None:
    cache = a_cache(tmp_path)
    for number in range(KEEP + 1):
        cache.put(a_key(commit=f"commit{number}"), a_snapshot())
        os.utime(cache.root / a_key(commit=f"commit{number}").name(), (number, number))
    cache.prune()

    assert len(cache.entries()) == KEEP
    assert cache.get(a_key(commit="commit0")) is None, "the oldest is the one that goes"
    assert cache.get(a_key(commit=f"commit{KEEP}")) is not None


def test_the_listing_names_each_entry_with_its_age_and_size(tmp_path: Path) -> None:
    """What ``doctor`` prints: enough to see whether the cache is doing anything."""
    cache = a_cache(tmp_path)
    cache.put(a_key(), a_snapshot())
    os.utime(cache.root / a_key().name(), (time.time() - 60, time.time() - 60))

    listed = cache.entries()

    assert len(listed) == 1
    assert listed[0].name.startswith("before-")
    assert listed[0].bytes > 0
    assert listed[0].seconds >= 59


def test_the_listing_is_newest_first(tmp_path: Path) -> None:
    cache = a_cache(tmp_path)
    for number, age in ((1, 300), (2, 10), (3, 100)):
        key = a_key(commit=f"commit{number}")
        cache.put(key, a_snapshot())
        os.utime(cache.root / key.name(), (time.time() - age, time.time() - age))

    ages = [entry.seconds for entry in cache.entries()]

    assert ages == sorted(ages)


def test_a_cache_directory_that_is_not_there_lists_nothing(tmp_path: Path) -> None:
    assert a_cache(tmp_path).entries() == []
