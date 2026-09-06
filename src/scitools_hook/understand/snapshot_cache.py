"""The extracted snapshot of a side, kept so the next run need not extract it (8.2, 8.6).

Measured on 2026-09-05 and again after task 4.2: **the four snapshot extractions are 88% of a
warm one-line check** on this repository, 24.5 s of 27.8 s. Nothing else in a warm run is
worth optimising until they are, and the cheapest of the four to remove is the *before* side:
its database has not changed, its selection has not changed, and the document it produced last
time is therefore still exactly right.

**What makes that safe is the key, and the key is the whole design.** A cached document is
served only when every input that could change it is identical:

* the **side**, because before and after are different databases;
* the **commit** the side represents, because that is what the database holds;
* the **selection**, because a snapshot is bounded to the files it was asked about;
* the **analysis settings** (:func:`~scitools_hook.config.fingerprint.analysis_fingerprint`),
  because languages, include and exclude patterns, the architecture and the ignore lists all
  change the document;
* the **worker's own source**, because the code that builds the document decides its shape,
  and a developer editing ``worker.py`` must not be served yesterday's answer;
* the **Understand build**, because a different analyser produces different entities;
* the **schema**, because a model that gained a field cannot read a document that lacks it.

Miss any one and the run is answered from a document about a different question, which is
worse than any amount of time saved. Requirement 8.7 is explicit that the cache must change no
finding, and the only way to keep that promise is to be certain the two questions were the
same one.

**A corrupt entry is a miss.** It is deleted and the extraction runs, because a cache is an
optimisation and an unreadable file is not an error worth stopping a commit for.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from scitools_hook.models.snapshot import ProjectSnapshot
from scitools_hook.understand import worker

CACHE_DIR: Final = "snapshots"
"""Where the documents live, under the analysis cache root, beside the databases."""

KEEP: Final = 8
"""How many documents survive a prune, newest first.

Eight is two sides times four recent commits, which is a working day of rebasing and amending
on one branch. It is a size, not a guarantee: the point of the cache is the *last* run, and
anything older is a bonus.
"""

SUFFIX: Final = ".json"


@dataclass(frozen=True, slots=True)
class SnapshotKey:
    """Everything that decides what a snapshot document contains (requirement 8.7)."""

    side: str
    commit: str
    selection: str
    settings: str
    build: str
    worker: str
    schema: str

    def name(self) -> str:
        """The file this key is stored under: the side, readable, and the digest of the rest."""
        parts = (self.commit, self.selection, self.settings, self.build, self.worker, self.schema)
        return f"{self.side}-{_digest(chr(10).join(parts))}{SUFFIX}"


def key_for(
    side: str, commit: str, files: frozenset[str], settings: str, build: str
) -> SnapshotKey:
    """One run's key, with the selection reduced to a digest of the file set.

    A module function rather than a classmethod because ``cls`` counts towards
    ``CountParams`` and the key genuinely has six components; the worker digest and the schema
    digest are read here rather than passed, since no caller could know them.

    The *set* and not the list: two runs that name the same files in a different order ask the
    same question, and a key that disagreed would miss every time.
    """
    return SnapshotKey(
        side=side,
        commit=commit,
        selection=_digest("\n".join(sorted(files))),
        settings=settings,
        build=build,
        worker=worker_digest(),
        schema=_schema(),
    )


@dataclass(frozen=True, slots=True)
class Entry:
    """One stored document, as ``doctor`` lists it (requirement 8.6)."""

    name: str
    seconds: float
    bytes: int


class SnapshotCache:
    """Stored snapshot documents, keyed by everything that could change one."""

    def __init__(self, root: Path) -> None:
        self.root = root / CACHE_DIR

    def get(self, key: SnapshotKey) -> ProjectSnapshot | None:
        """The stored document for ``key``, or ``None`` when there is none to trust.

        A file that cannot be read, cannot be parsed, or does not validate against the model
        is **removed** and answered as a miss. Keeping it would make every later run pay the
        same failed read, and reporting it would stop a commit over an optimisation.
        """
        found = self.root / key.name()
        try:
            return ProjectSnapshot.model_validate_json(found.read_text(encoding="utf-8"))
        except OSError:
            return None
        except ValueError:
            _discard(found)
            return None

    def put(self, key: SnapshotKey, snapshot: ProjectSnapshot) -> None:
        """Store one document and prune to the newest :data:`KEEP`.

        A failure to write is silence, for the reason the module docstring gives: this is an
        optimisation, and a run that produced the right answer must not fail because it could
        not remember it.
        """
        target = self.root / key.name()
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            target.write_text(snapshot.model_dump_json(), encoding="utf-8")
        except OSError:
            return
        self.prune()

    def prune(self, keep: int = KEEP) -> None:
        """Delete all but the ``keep`` most recently written documents."""
        stored = sorted(self._stored(), key=lambda pair: pair[1], reverse=True)
        for path, _ in stored[keep:]:
            _discard(path)

    def entries(self) -> list[Entry]:
        """What is stored, newest first, for the operator to read (requirement 8.6)."""
        now = time.time()
        return [
            Entry(name=path.name, seconds=max(0.0, now - written), bytes=_size(path))
            for path, written in sorted(self._stored(), key=lambda pair: pair[1], reverse=True)
        ]

    def _stored(self) -> list[tuple[Path, float]]:
        """Every stored document and when it was written; an unreadable directory has none."""
        try:
            found = list(self.root.glob(f"*{SUFFIX}"))
        except OSError:
            return []
        return [(path, written) for path in found if (written := _written(path)) is not None]


def worker_digest() -> str:
    """A digest of the worker's own source, so editing it invalidates every document.

    Read from the module rather than recorded by hand: a version constant is a thing to forget
    to bump, and the failure it produces -- yesterday's document served for today's code -- is
    invisible. A source that cannot be read answers with a value of its own, which misses
    every time and is the safe direction.
    """
    try:
        return _digest(Path(str(worker.__file__)).read_text(encoding="utf-8"))
    except OSError:
        return "unreadable"


def _schema() -> str:
    """A digest of the snapshot model's own field names, per scope of the document."""
    return _digest(json.dumps(ProjectSnapshot.model_json_schema(), sort_keys=True))


def _digest(text: str) -> str:
    """Sixteen hex characters of SHA-256, which is a file name and not a secret."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _written(path: Path) -> float | None:
    """When a stored document was last written, or ``None`` when it cannot be asked."""
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _size(path: Path) -> int:
    """How large a stored document is; an unreadable one counts as nothing."""
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _discard(path: Path) -> None:
    """Remove a stored document, ignoring a file that is already gone."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return
