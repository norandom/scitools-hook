"""One ``check`` run, end to end: git -> shadows -> databases -> snapshots -> rules -> result.

This is the module every other one on the project was built for. It owns no rule and no
adapter; what it owns is the *order* things happen in, and almost every decision below exists
because doing it in a different order produces a confident, green, fictional answer.

**What to analyse, and how it is read, is now :mod:`scitools_hook.runner.pipeline`.** Task 8.4
promoted the planning and observation steps there so ``explain`` could stand on the same ones
rather than grow a second, drifting copy; the reasons they are shaped the way they are --
requirement 4.9's empty-selection short circuit, the resolved-hash rule for the before side,
and the two bounded extraction passes -- are documented in that module. What stays here is
requirement 4.9's *answer*: a run with nothing to analyse returns an empty ``RunResult``
rather than raising, because ``DatabaseManager.ensure_side`` raises ``AnalysisFailedError``
(exit 5) for a repository with no analysable file and requirement 4.9 says that case is
"nothing was analyzed, exit 0". Getting it wrong turns an innocuous commit -- a README edit, a
version bump, a rename of a text file -- into a hard failure of the tool.

**The evaluator order is part of the contract**: thresholds, then ``attach_before``, then the
ratchet, then structure, then CodeCheck, then ``classify``. Without ``attach_before`` between
the first two, no threshold finding can ever be pre-existing and requirement 4.6 silently
stops working. Hints are attached last, after classification, so that every finding carries
one in every output format (req 7.2) whatever an evaluator left behind.

**Adaptive tightening is confined to whole-project runs**, and that is a reading of
requirement 8.3 rather than an omission. A staged run's snapshot holds the affected entities
only, so its maximum for a metric is a maximum over a handful of files; feeding that to
``analysis.baseline.tighten`` would lower a project-wide baseline to whatever the smallest
commit happened to touch and fail everything on the next run. Requirement 8.3 fires when a run
"finds that the current maximum for a threshold is lower than the recorded baseline", and a
bounded run finds no such thing. Applying the baseline (req 8.2) and attributing it (req 8.5)
happen on every run; only the tightening waits for a run that actually saw the project.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Final

from scitools_hook import __version__
from scitools_hook.analysis import baseline as baseline_rules
from scitools_hook.analysis.classify import classify
from scitools_hook.analysis.codecheck import map_violations
from scitools_hook.analysis.ratchet import attach_before, evaluate_ratchet
from scitools_hook.analysis.structure.coupling import evaluate_coupling, new_dependencies
from scitools_hook.analysis.structure.cycles import find_new_cycles
from scitools_hook.analysis.structure.fan import evaluate_fan
from scitools_hook.analysis.structure.layers import evaluate_layers
from scitools_hook.analysis.thresholds import ThresholdOutcome, evaluate_thresholds
from scitools_hook.config.models import SeverityMap, ThresholdSpec
from scitools_hook.models.baseline import Baseline
from scitools_hook.models.change import AffectedSet
from scitools_hook.models.findings import (
    EffectiveThreshold,
    Finding,
    RunResult,
    TightenedLimit,
    structure_rule,
)
from scitools_hook.models.snapshot import ParseError, ProjectSnapshot, Side
from scitools_hook.models.understand import AnalyzeResult
from scitools_hook.report.hints import HintCatalogue
from scitools_hook.runner.baseline_store import BaselineStore
from scitools_hook.runner.context import RunContext
from scitools_hook.runner.pipeline import (
    AnalysisPlan,
    Engine,
    PlanMode,
    Selection,
    SelectionMode,
    plan_selection,
)
from scitools_hook.understand.codecheck import CodeCheckRunner
from scitools_hook.understand.codecheck import _unusable_name as unusable_list_file_name
from scitools_hook.understand.database import DatabaseManager
from scitools_hook.understand.snapshot import SnapshotExtractor

__all__ = ["CheckPipeline", "Selection", "SelectionMode"]
"""``Selection`` is re-exported: it is the runner's entry vocabulary, and task 8.4 moved it
to :mod:`scitools_hook.runner.pipeline` so ``explain`` could share it without importing
``check``."""

Sides = tuple[ProjectSnapshot, ProjectSnapshot | None]
"""The after snapshot and the before one; whole-project mode has no before side (req 4.8)."""

PARTIAL_VIEW_NOTE: Final = (
    "adaptive mode is on, but this run looked at the change rather than the whole project, "
    "so no baseline value was tightened; run `scitools-hook check --all` to tighten limits"
)
"""Requirement 8.3 needs a project-wide maximum; a bounded run has not seen one (note 4.5)."""


class CheckPipeline:
    """Runs the gate over one selection and answers with the whole of what it found."""

    def __init__(
        self,
        ctx: RunContext,
        dbm: DatabaseManager,
        extractor: SnapshotExtractor,
        codecheck: CodeCheckRunner | None,
        baseline_store: BaselineStore,
    ) -> None:
        self.ctx = ctx
        self._dbm = dbm
        self._engine = Engine(dbm, extractor, ctx.progress)
        self._codecheck = codecheck
        self._store = baseline_store
        self._hints = HintCatalogue(ctx.settings.hints)

    def run(self, selection: Selection) -> RunResult:
        """Evaluate ``selection`` and return everything the run produced (req 4.1-4.11)."""
        started = time.monotonic()
        repo = self.ctx.require_repo()
        plan = plan_selection(selection, repo, self.ctx.settings.project.languages)
        if not plan.files:
            return self._nothing_analyzed(selection, repo.root, started)
        analyses = self._engine.analyse(plan)
        after, before, affected = self._engine.observe(plan, analyses)
        specs = list(self.ctx.availability.thresholds)
        stored, unreadable = self._store.load(specs)
        effective, issues = baseline_rules.apply(specs, stored)
        # Requirement 8.6: a baseline that cannot be read, and an entry no configured
        # threshold owns, are both reported and then stepped over -- the run continues on the
        # configured limits for exactly the thresholds those entries would have narrowed.
        self._report(
            issue.message if issue.key is None else f"{issue.key}: {issue.message}"
            for issue in (*unreadable, *issues)
        )
        findings, outcome = self._evaluate(plan, (after, before), affected, effective)
        return RunResult(
            tool_version=__version__,
            understand_version=self.ctx.understand.version,
            repo_root=str(repo.root),
            selection=_describe(selection),
            started_at=self.ctx.started_at,
            seconds=time.monotonic() - started,
            effective_thresholds=[_effective_spec(item) for item in effective],
            findings=findings,
            ignored_counts=outcome.ignored_counts,
            unavailable_metrics=_merge_metrics(
                self.ctx.availability.unavailable, outcome.unavailable
            ),
            parse_errors=_merge_parse_errors(analyses),
            tightened=self._adapt(plan.mode, after, specs, stored),
            highest=outcome.highest,
            analyzed_files=len({key.path for key in after.entities}),
            blocking_count=sum(1 for finding in findings if finding.blocking),
            warning_count=sum(1 for finding in findings if finding.severity == "warning"),
            preexisting_count=sum(1 for finding in findings if finding.preexisting),
        )

    # --- the rules ----------------------------------------------------------------

    def _evaluate(
        self,
        plan: AnalysisPlan,
        sides: Sides,
        affected: AffectedSet,
        effective: Sequence[EffectiveThreshold],
    ) -> tuple[list[Finding], ThresholdOutcome]:
        """Every evaluator, in the order the design fixes, then classification and hints.

        ``attach_before`` sits between the thresholds and the ratchet on purpose: it is what
        fills ``Finding.before``, and without it ``classify`` can never call a finding
        pre-existing (req 4.6, note 4.2).
        """
        after, before = sides
        outcome = evaluate_thresholds(
            after,
            affected.keys,
            effective,
            self.ctx.availability.unavailable,
            self.ctx.settings.ignore,
        )
        self._report(f"{rule}: {why}" for rule, why in sorted(outcome.reducer_failures.items()))
        findings = list(outcome.findings)
        if before is not None:
            findings = attach_before(findings, before)
            findings.extend(evaluate_ratchet(after, before, affected.keys, effective))
        findings.extend(self._structure(after, before, affected))
        findings.extend(self._violations(affected.files & plan.files))
        return self._finish(findings, effective), outcome

    def _structure(
        self, after: ProjectSnapshot, before: ProjectSnapshot | None, affected: AffectedSet
    ) -> list[Finding]:
        """Cycles, layers, fan and coupling (req 6.1-6.6).

        ``keys_files`` is the affected files **and their neighbourhood**: requirement 4.10's
        "remaining affected files" land in the neighbourhood after a deletion, so a fan rule
        given only ``files`` would evaluate nothing at all on a deletions-only change.
        """
        rules = self.ctx.settings.structure
        was_files = None if before is None else before.file_edges
        was_arch = None if before is None else before.arch_edges
        findings = find_new_cycles(was_files, after.file_edges, rules.file_cycles, "file")
        findings += find_new_cycles(was_arch, after.arch_edges, rules.arch_cycles, "arch")
        findings += evaluate_layers(after.file_edges, was_files, _node_of(after), rules.layers)
        findings += evaluate_fan(
            after,
            before,
            affected.files | affected.neighbourhood,
            {key for key in affected.keys if key.scope == "class"},
            rules.fan,
        )
        if rules.max_new_dependencies_per_file is not None:
            findings += new_dependencies(
                was_files,
                after.file_edges,
                affected.files,
                rules.max_new_dependencies_per_file,
                rules.new_dependencies_severity,
            )
        return findings + evaluate_coupling(after.arch_edges, rules.coupling)

    def _violations(self, checked: Collection[str]) -> list[Finding]:
        """The configured CodeCheck configuration, run over the selected files (req 6.9).

        ``checked`` is the intersection of the affected files with the ones the selection
        named, which is requirement 6.9's "the staged files" in both directions: a file the
        change never touched but that gained a dependency is affected (req 4.2) yet has no new
        CodeCheck violation to report, and a deleted file is in neither set. In whole-project
        mode both sets are the project, so the intersection is the project.

        The paths handed to ``und`` are the shadow's, absolute, and every one is held against
        the list-file predicate first: a name carrying ``#``, a comma, a ``*``, a line break or
        edge whitespace cannot be asked about at all, so it is excluded and said out loud
        rather than passed through to name a different file or none.
        """
        config = self.ctx.settings.codecheck.config
        if self._codecheck is None or config is None:
            return []
        tree = self._dbm.paths().after_tree
        listed = self._listable(sorted(checked), tree)
        if not listed:
            return []
        with TemporaryDirectory(prefix="scitools-hook-codecheck-") as scratch:
            rows = self._codecheck.run(self._dbm.paths().after_db, config, listed, Path(scratch))
        return map_violations(rows, self.ctx.settings.codecheck.severity, tree)

    def _listable(self, paths: Sequence[str], tree: Path) -> list[str]:
        """The shadow paths ``und``'s list file can carry, the rest reported and dropped."""
        listed: list[str] = []
        for path in paths:
            name = str(tree / path)
            problem = unusable_list_file_name(name)
            if problem is None:
                listed.append(name)
            else:
                self._report([f"CodeCheck cannot be asked about {path}: it {problem}"])
        return listed

    def _finish(
        self, findings: Sequence[Finding], effective: Sequence[EffectiveThreshold]
    ) -> list[Finding]:
        """Classify every finding, then give each one its remediation hint (req 4.6, 4.7, 7.2)."""
        strict = self.ctx.settings.ratchet.strict
        classified = classify(findings, strict, self._severities(effective))
        return [
            finding.model_copy(update={"hint": self._hints.hint(finding.rule, finding)})
            for finding in classified
        ]

    def _severities(self, effective: Sequence[EffectiveThreshold]) -> SeverityMap:
        """The severity of every rule ``classify`` may need to override (req 3.7).

        Only two entries are load-bearing. Every other evaluator builds its findings with the
        severity its own configuration carries -- a layer or coupling rule has one *per rule*,
        so a single ``structure.layer`` entry here would flatten them all -- but
        ``evaluate_fan`` keeps the design's five-parameter signature and therefore carries no
        severity at all. Its findings default to a warning, and an operator's
        ``structure.fan_severity`` reaches them only through this map (note 4.4).
        """
        fan = self.ctx.settings.structure.fan_severity
        severities: SeverityMap = {item.rule: item.spec.severity for item in effective}
        severities[structure_rule("fan_in")] = fan
        severities[structure_rule("fan_out")] = fan
        return severities

    # --- the baseline --------------------------------------------------------------

    def _adapt(
        self,
        mode: PlanMode,
        after: ProjectSnapshot,
        specs: Sequence[ThresholdSpec],
        stored: Baseline | None,
    ) -> list[TightenedLimit]:
        """Lower the baseline to what this run observed, when the run saw the project (8.3).

        The observed values come from a fresh ``capture`` over the snapshot, never from
        ``ThresholdOutcome.highest``: that is a maximum over the affected entities alone, so a
        commit touching one simple file would tighten a project-wide baseline to that file's
        value and fail everything on the next run (note 4.5).
        """
        if not self.ctx.settings.baseline.adaptive or stored is None:
            return []
        if mode != "all":
            self._report([PARTIAL_VIEW_NOTE])
            return []
        observed = baseline_rules.capture(after, specs, self.ctx.started_at)
        tightened, lowered = baseline_rules.tighten(stored, observed.values)
        if lowered:
            self._store.save(tightened)
        return lowered

    # --- small services -------------------------------------------------------------

    def _nothing_analyzed(self, selection: Selection, root: Path, started: float) -> RunResult:
        """The result of a run that found nothing to analyse (req 4.9).

        The configuration is still reported -- the effective thresholds and the metrics this
        repository's languages cannot compute are true whether or not anything was read -- so
        the renderers can qualify "nothing to report" honestly instead of claiming a clean run.
        """
        return RunResult(
            tool_version=__version__,
            understand_version=self.ctx.understand.version,
            repo_root=str(root),
            selection=_describe(selection),
            started_at=self.ctx.started_at,
            seconds=time.monotonic() - started,
            effective_thresholds=list(self.ctx.availability.thresholds),
            unavailable_metrics=_merge_metrics(self.ctx.availability.unavailable, {}),
        )

    def _report(self, messages: Iterable[str]) -> None:
        """Say something on the diagnostics channel; findings never travel this way (req 7.7)."""
        for message in messages:
            self.ctx.progress.note(message)


# --- helpers ------------------------------------------------------------------------


def _node_of(snapshot: ProjectSnapshot) -> Callable[[str], str | None]:
    """Maps a file to its architecture node, from the nodes the snapshot published (req 6.7)."""
    index = {member: node.path for node in snapshot.arch_nodes for member in node.members}
    return index.get


def _effective_spec(item: EffectiveThreshold) -> ThresholdSpec:
    """One threshold as it actually applied, so the report quotes the effective limit (8.2)."""
    return item.spec.model_copy(update={"limit": item.limit})


def _merge_metrics(*sources: Mapping[str, Sequence[str]]) -> dict[str, list[str]]:
    """Merge unavailable-metric maps, **language -> metrics** in every one of them (req 5.5).

    Two sources, and both are needed. The availability report names the shipped defaults this
    repository's languages cannot compute, which the evaluator filters out of its own answer
    because they are no longer among the specs it was given (note 2.4); the evaluator names
    what it discovered entity by entity, which the report cannot know.
    """
    merged: dict[str, set[str]] = {}
    for source in sources:
        for language, metrics in source.items():
            merged.setdefault(language, set()).update(metrics)
    return {language: sorted(metrics) for language, metrics in sorted(merged.items())}


def _merge_parse_errors(analyses: Mapping[Side, AnalyzeResult]) -> list[ParseError]:
    """Both sides' parse errors, after first, each distinct one once (req 2.6).

    Both, because a file the change did not touch can fail to parse on either side and either
    way entities are missing from the comparison. De-duplicated, because an error present in
    both databases is one coverage loss, not two.
    """
    merged: list[ParseError] = []
    seen: set[tuple[str, int | None, str]] = set()
    for side in ("after", "before"):
        for error in analyses.get(side, AnalyzeResult(seconds=0.0)).parse_errors:
            token = (str(error.path), error.line, error.message)
            if token not in seen:
                seen.add(token)
                merged.append(error)
    return merged


def _describe(selection: Selection) -> str:
    """One line naming what the run covered, for the run metadata (req 7.4)."""
    if selection.mode == "files":
        return f"files: {', '.join(selection.files)}"
    return selection.mode
