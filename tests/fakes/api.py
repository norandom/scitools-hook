"""``FakeApiRunner`` and ``FakeSnapshotExtractor``: the Understand API without Understand (6.6).

Both derive from the class they stand in for, so mypy compares every override against the
real signature and a drift in the adapter fails the type check rather than a test months
later. Neither calls its base ``__init__``: there is no installation, no command log and no
subprocess behind these objects.

They are imported as ``fakes.api`` rather than through ``fakes/__init__.py`` on purpose:
``tests/fakes`` is shared between the adapter tasks, and a module that is only ever imported
by its own path cannot collide with another task's edit of the package header.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from scitools_hook.models.snapshot import ProjectSnapshot, Side
from scitools_hook.understand.api_runner import ApiRunner as RealApiRunner
from scitools_hook.understand.api_runner import Operation
from scitools_hook.understand.snapshot import SnapshotExtractor as RealSnapshotExtractor
from scitools_hook.understand.snapshot import SnapshotTarget

Answer = dict[str, object] | Exception
"""What a fake runner is scripted with: the document one operation answers, or a failure."""


@dataclass(frozen=True)
class FakeRun:
    """One recorded operation: its name and the request it was given."""

    op: str
    request: Mapping[str, object]


@dataclass
class FakeApiRunner(RealApiRunner):
    """An ``ApiRunner`` that answers from a script instead of from Understand.

    ``answers`` maps an operation to the document it answers with; an ``Exception`` value is
    raised instead, which is how a test drives the typed errors the real runner maps from
    worker envelopes. An operation with no entry is a test bug and says so.
    """

    answers: dict[str, Answer] = field(default_factory=dict)
    calls: list[FakeRun] = field(default_factory=list)

    @property
    def ops(self) -> list[str]:
        """The operations that ran, in order, for readable assertions."""
        return [call.op for call in self.calls]

    def request_for(self, op: str) -> Mapping[str, object]:
        """The request of the first call to ``op``; fails the test when there was none."""
        for call in self.calls:
            if call.op == op:
                return call.request
        raise AssertionError(f"{op!r} was never run; ran {self.ops}")

    def run(self, op: Operation, request: Mapping[str, object]) -> dict[str, object]:
        """Record the call and answer with what the test scripted for ``op``."""
        self.calls.append(FakeRun(op, dict(request)))
        answer = self.answers.get(op)
        if isinstance(answer, Exception):
            raise answer
        if answer is None:
            raise AssertionError(f"no answer scripted for the {op!r} operation")
        return answer


@dataclass
class FakeSnapshotExtractor(RealSnapshotExtractor):
    """A ``SnapshotExtractor`` that answers with prepared snapshots, one per side."""

    snapshots: dict[Side, ProjectSnapshot] = field(default_factory=dict)
    targets: list[SnapshotTarget] = field(default_factory=list)

    def extract(self, target: SnapshotTarget) -> ProjectSnapshot:
        """Record the target and answer with the snapshot scripted for its side."""
        self.targets.append(target)
        snapshot = self.snapshots.get(target.side)
        if snapshot is None:
            raise AssertionError(f"no snapshot scripted for the {target.side!r} side")
        return snapshot
