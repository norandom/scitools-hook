"""The ``recommend`` command: measure the whole project, then price every configured ceiling.

Structurally this is ``baseline``'s twin and semantically it is its opposite, which is the one
thing worth stating twice. Both take a **whole-project** extraction -- a percentile over the
handful of files a change touched is not a statement about a repository, exactly as a maximum
over them is not a baseline (``runner.baseline_cmd``, which recorded that reason first) -- and
both answer with numbers. What they mean could not be further apart:

* ``baseline`` writes today's worst value per rule, so that existing debt reports as
  pre-existing and nothing gets worse. Where you are.
* ``recommend`` writes nothing at all. It reports, for each ceiling in force, how much of the
  repository is already inside it and what each candidate limit would cost, and proposes a
  change only for a limit this repository has outgrown. Where to aim.

**This command never writes.** Not a flag, a property: the operator's next step is to paste
the lines they agree with, and a command that could also apply them would need a merge into an
existing configuration -- which is precisely the judgement that must stay with the operator.
The ``--print`` path other commands offer is the only path here.

**One extraction, and the same short circuit the other pipelines use.** A repository holding
no analysable file answers with an empty recommendation rather than the ``AnalysisFailedError``
(exit 5) that ``DatabaseManager.ensure_side`` would raise, because "there is nothing here to
measure" is an answer to this question and not a fault.
"""

from __future__ import annotations

from collections.abc import Sequence

from scitools_hook.analysis.recommend import TARGET_COVERAGE, Recommendation, recommend
from scitools_hook.config.models import ThresholdSpec
from scitools_hook.runner.context import RunContext
from scitools_hook.runner.pipeline import Engine, Selection, plan_selection
from scitools_hook.understand.database import DatabaseManager
from scitools_hook.understand.snapshot import SnapshotExtractor

WHOLE_PROJECT = Selection(mode="all")
"""What a recommendation always covers; see the module docstring."""

NOTHING_NOTE = (
    "no file in this repository can be analyzed, so there is no distribution to measure and "
    "nothing is recommended"
)


class RecommendCmd:
    """Measures the project's metric distributions and prices the configured ceilings."""

    def __init__(self, ctx: RunContext, dbm: DatabaseManager, extractor: SnapshotExtractor) -> None:
        self.ctx = ctx
        self._engine = Engine(dbm, extractor, ctx.progress)

    def run(self, target: float = TARGET_COVERAGE) -> Recommendation:
        """Measure the whole project and price every ceiling in force.

        ``target`` is the share of a population a limit must contain to be reported as
        fitting. It travels as an argument rather than as a settings override because it
        describes *this question*, not the gate: nothing in a configuration file reads it, and
        a dotted key for it would put a number in the file that no ``check`` run ever uses.
        """
        repo = self.ctx.require_repo()
        specs = self._ceilings()
        plan = plan_selection(WHOLE_PROJECT, repo, self.ctx.settings.project.languages)
        if not plan.files:
            self.ctx.progress.note(NOTHING_NOTE)
            return Recommendation(counts={}, advice=(), skipped=())
        analyses = self._engine.analyse(plan)
        snapshot = self._engine.extract("after", plan.files, analyses)
        return recommend(snapshot, specs, target, tuple(self.ctx.settings.scope))

    def _ceilings(self) -> Sequence[ThresholdSpec]:
        """The thresholds actually in force, after the metric catalogue has had its say.

        ``availability.thresholds`` rather than ``settings.thresholds``: a shipped default
        whose metric this project's language cannot compute is dropped from the run (req 5.5),
        and recommending a limit for a rule that will never be evaluated would be advice about
        a gate that does not exist.

        The **configured** limits are what is priced, not the shipped defaults, so a repository
        that has already tuned its numbers is told about *its* numbers. The adaptive baseline
        is deliberately not applied: it would make the recommendation a function of the
        recorded baseline, which is the confusion this command exists to end.
        """
        return list(self.ctx.availability.thresholds)
