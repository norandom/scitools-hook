"""Test doubles the runner pipelines need: a database-creating ``und``, a scripted extractor.

Imported as ``fakes.runner`` rather than through ``fakes/__init__.py``, for the reason
:mod:`fakes.api` states: a module only ever imported by its own path cannot collide with
another task's edit of the package header.

Each double subclasses the class it stands in for, so a signature that drifts from the real
adapter is a type error rather than a test that quietly stops exercising anything.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from fakes.und_cli import FakeUndCli
from scitools_hook.config.defaults import default_settings
from scitools_hook.models.snapshot import ProjectSnapshot, Side
from scitools_hook.understand.fake import FixtureApiRunner
from scitools_hook.understand.snapshot import SnapshotExtractor, SnapshotTarget


@dataclass
class UndStub(FakeUndCli):
    """``FakeUndCli`` that also creates the database directory, as ``und create`` does.

    Without it the database manager cannot tell a first run from a second one, because it
    decides that by looking for the database exactly as an operator who cleared the cache
    would.
    """

    def create(self, db: Path, languages: list[str], local: bool = True) -> None:
        """Record the creation and make the ``.und`` directory the real command makes."""
        super().create(db, languages, local)
        db.mkdir(parents=True, exist_ok=True)

    def analyzed_sides(self, before_db: Path, after_db: Path) -> list[str]:
        """Which databases were analysed, read from the ``und`` commands that actually ran."""
        seen: list[str] = []
        for call in self.calls:
            if call.command != "analyze":
                continue
            db = call.arguments["db"]
            seen.append("before" if db == before_db else "after" if db == after_db else str(db))
        return seen


@dataclass
class ScriptedExtractor(SnapshotExtractor):
    """Answers each extraction from a queue per side and records the target it was given.

    The queue is exact. An extraction the design does not prescribe raises rather than
    repeating the last answer, so a pipeline that reads a side more often than it should fails
    here instead of quietly costing a worker run per commit -- and one that never reaches the
    extractor at all cannot pass by reusing a stale snapshot.
    """

    answers: dict[Side, list[ProjectSnapshot]] = field(default_factory=dict)
    targets: list[SnapshotTarget] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Initialise the base, so an unoverridden member answers rather than raises."""
        SnapshotExtractor.__init__(self, FixtureApiRunner(Path("unused")), default_settings())

    def extract(self, target: SnapshotTarget) -> ProjectSnapshot:
        """The next scripted snapshot for ``target``'s side, narrowed to the requested files.

        The narrowing is what makes the double honest rather than convenient. The real worker
        records an entity **only** when its path is in the requested set (``worker.py``: ``if
        key.path in self.plan.files``), with no "empty means everything" escape -- so a
        pipeline that forgets to ask for a path gets a snapshot without it, and a stub that
        answered with the whole scripted document would hide exactly that mistake. Edges,
        architecture nodes and populations are left whole: the worker derives them from the
        entities it walked rather than from the ones it recorded.
        """
        self.targets.append(target)
        queue = self.answers.get(target.side, [])
        if not queue:
            raise AssertionError(f"the pipeline extracted the {target.side} side once too often")
        whole = queue.pop(0)
        kept = {key: record for key, record in whole.entities.items() if key.path in target.files}
        return whole.model_copy(update={"entities": kept})

    @property
    def sides(self) -> list[Side]:
        """Which sides were extracted, in order."""
        return [target.side for target in self.targets]

    def requested(self, side: Side, pass_: int) -> set[str]:
        """The file set of one side's ``pass_``-th extraction (0-based)."""
        matching = [target for target in self.targets if target.side == side]
        return set(matching[pass_].files)


def scripted(answers: Mapping[Side, Sequence[ProjectSnapshot]]) -> ScriptedExtractor:
    """A :class:`ScriptedExtractor` over copies of ``answers``, so tests cannot share queues."""
    return ScriptedExtractor(answers={side: list(queue) for side, queue in answers.items()})
