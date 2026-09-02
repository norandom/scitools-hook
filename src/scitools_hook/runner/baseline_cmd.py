"""The ``baseline`` command: record what this project currently measures (requirement 8.1).

One run, one question -- *what is the worst value of every configured threshold today?* -- and
one answer written to the baseline file, which adaptive mode then uses as the lower of the two
limits (req 8.2) and lowers over time (req 8.3) but never raises (req 8.4).

**The capture is taken from a whole-project extraction, and that is the decision this module
turns on.** Task 8.3 confined adaptive tightening to ``check --all`` for the same reason and
recorded it as a handoff here: a bounded run's snapshot holds the affected entities only, so
its maximum for a metric is a maximum over a handful of files. Recording *that* as the
project's baseline would write limits nobody's code has to meet, and the next ordinary commit
would fail against them -- which is also how requirement 8.4 gets violated in practice, since
the operator's only way out is to edit the file back upwards. So the plan here is always the
whole-project one, whatever directory the command was run from and whatever is staged.

Only element maxima are partial in a bounded extraction; the population vectors a stats-prefix
reduces are project-wide either way. The distinction is not exploited: capturing half the
thresholds from a project view and half from a bounded one would make the file's meaning
depend on which threshold you read.

**A threshold the project gives no value for is reported, not invented.**
``analysis.baseline.capture`` writes no entry for a metric no entity carries or a population
that cannot be reduced, so the file never claims a value it did not observe. That leaves the
operator with a baseline that is silently narrower than their configuration, so every such
threshold is named in :attr:`BaselineCapture.missing` and on the diagnostics channel.

**Nothing analysable writes nothing.** A repository holding no file Understand can parse would
raise ``AnalysisFailedError`` (exit 5) from ``ensure_side``; the same short circuit ``check``
and ``explain`` use answers it here with an empty capture that is *not* written, because a
baseline recording no value at all is indistinguishable from one that was never taken and
would sit in the repository looking like a finished job.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from scitools_hook.analysis import baseline as baseline_rules
from scitools_hook.config.models import ThresholdSpec
from scitools_hook.models.baseline import Baseline
from scitools_hook.runner.baseline_store import BaselineStore, baseline_path
from scitools_hook.runner.context import RunContext
from scitools_hook.runner.pipeline import Engine, Selection, plan_selection
from scitools_hook.understand.database import DatabaseManager
from scitools_hook.understand.snapshot import SnapshotExtractor

WHOLE_PROJECT = Selection(mode="all")
"""What a capture always covers: the project, never the change (see the module docstring)."""

NOTHING_NOTE = (
    "no file in this repository can be analyzed, so no baseline was captured and nothing "
    "was written"
)
"""Requirement 8.1 has nothing to record when there is nothing to measure."""


@dataclass(frozen=True, slots=True)
class BaselineCapture:
    """What one ``baseline`` run recorded, and what it could not."""

    path: Path
    """Where the baseline was written, or would have been."""

    baseline: Baseline
    """The captured document: one entry per configured threshold the project answered for."""

    missing: tuple[str, ...]
    """Configured thresholds this project gave no value for, sorted; the file omits them."""

    written: bool
    """False only when there was nothing to analyze, so nothing reached the file."""


class BaselineCmd:
    """Captures the project's current maxima and stores them (req 8.1)."""

    def __init__(self, ctx: RunContext, dbm: DatabaseManager, extractor: SnapshotExtractor) -> None:
        self.ctx = ctx
        self._engine = Engine(dbm, extractor, ctx.progress)

    def run(self, path: Path | None = None) -> BaselineCapture:
        """Record every configured threshold's current value and save it (req 8.1).

        ``path`` overrides ``baseline.file`` for this run and is used exactly as given, while
        the configured value is resolved against the repository root. The asymmetry is
        deliberate: a *setting* has to mean the same file from a hook running at the root and
        from a CI job running elsewhere, whereas a path typed on a command line means what it
        means in the directory it was typed in.
        """
        repo = self.ctx.require_repo()
        destination = baseline_path(self.ctx.settings, repo.root) if path is None else path
        specs = list(self.ctx.availability.thresholds)
        plan = plan_selection(WHOLE_PROJECT, repo, self.ctx.settings.project.languages)
        if not plan.files:
            self.ctx.progress.note(NOTHING_NOTE)
            return self._captured(destination, self._empty(), specs, written=False)
        analyses = self._engine.analyse(plan)
        # One extraction, not two: a capture has no change to resolve and no before side to
        # compare against, so the second, neighbourhood-bounded pass `check` makes would read
        # the same database again for nothing.
        snapshot = self._engine.extract("after", plan.files, analyses)
        captured = baseline_rules.capture(snapshot, specs, self.ctx.started_at)
        BaselineStore(destination).save(captured)
        return self._captured(destination, captured, specs, written=True)

    def _captured(
        self,
        destination: Path,
        baseline: Baseline,
        specs: Sequence[ThresholdSpec],
        *,
        written: bool,
    ) -> BaselineCapture:
        """Assemble the answer and name every threshold the capture could not measure."""
        missing = tuple(sorted({spec.rule for spec in specs} - set(baseline.values)))
        for rule in missing:
            self.ctx.progress.note(
                f"{rule}: this project reports no value for it, so the baseline records none"
            )
        return BaselineCapture(
            path=destination, baseline=baseline, missing=missing, written=written
        )

    def _empty(self) -> Baseline:
        """A capture that observed nothing, stamped with the run's own instant."""
        return Baseline(captured_at=self.ctx.started_at)
