"""The lean-code family's one step in a check: the rules that are on, and the change's delta.

Six rules, two floors, one number and one place they are called from. The step exists so that
:class:`~scitools_hook.runner.check.CheckPipeline` gains one call rather than six, and so that
"which rules did this configuration ask for" is a question with a single answer rather than a
statement per rule spread through the pipeline.

**What the step is allowed to decide, and what it is not.** It decides *whether* a rule runs:
a rule whose severity is ``None`` is off, and an off rule costs a call, a snapshot walk and a
note -- none of which it makes (requirement 9.4). It decides nothing about the findings
themselves. Scope overrides, ``[ignore]``, the severity map, ``classify`` and the hint
catalogue all apply afterwards, in ``CheckPipeline._finish``, exactly as they apply to
``structure.fan_out`` or ``structure.unused_routines``; requirement 9.6 is the promise that an
operator learns nothing new to configure this family, and the way to keep it is for this module
to hand back plain :class:`~scitools_hook.models.findings.Finding` objects and stop.

**Three answers, not one, and the second is the one that gets lost.**
:class:`LeanResult` carries findings, *notes* and the delta because the family's rules have the
same three-state discipline ``structure.unused_routines`` already has: used, unused, and **not
measured**. A rule that could not measure says so once per run -- ``CheckPipeline._report``,
the diagnostics channel, the same route the unused rule's message takes -- rather than once per
entity, and never by inventing a finding. Requirements 1.6 and 2.5 both turn on that sentence.
Five of the family's rules fill that list: the three dead-code rules and the pass-through rule
refuse below either of requirement 1.8's floors and when the facts they read were never
measured, and the single-implementation rule refuses on the facts alone. ``over_export`` is the
one that cannot: it reads file metrics, the file edges and the definitions walk, all of which
the snapshot already carries, so it has no third state.

**The floors are the run's, not the operator's alone, so this step is where they are bound.**
``[lean] resolution_floor`` and ``[lean] accuracy_floor`` are the numbers, the after side's
``und analyze -accuracy`` is the figure one of them judges, and
:class:`~scitools_hook.analysis.lean.dead.Trust` is the object the rules take. Built once here
rather than at five call sites, because five rules disagreeing about what "trusted" means in
one run is the shape of defect this family keeps meeting.

**The delta is not one of the rules.** :func:`~scitools_hook.analysis.lean.net.net_delta` runs
whenever there is a before side to subtract from, whatever the ``[lean]`` section says.
Requirement 7.1 prints it "when a check has a before side" and attaches no condition about the
lean rules; requirement 7.6, the "nothing to cut" line, scopes itself explicitly with "when
lean-code rules are enabled", which is the author scoping by enablement in the one place they
meant it. It costs no extraction of its own -- both sides' ``CountStmt`` and ``CountLineCode``
are already in the snapshots the ratchet reads -- so requirement 9.4's cost rule does not reach
it either. The *maximum* on the delta is a different thing and is off by default (7.5).
"""

from __future__ import annotations

from typing import NamedTuple

from scitools_hook.analysis.lean.dead import (
    LeanOutcome,
    Trust,
    find_unused_classes,
    find_unused_parameters,
    find_unused_variables,
)
from scitools_hook.analysis.lean.layering import (
    PassThroughLimits,
    find_over_exports,
    find_pass_through,
    find_single_implementations,
)
from scitools_hook.analysis.lean.net import net_delta, net_growth_finding
from scitools_hook.config.models import LeanRules
from scitools_hook.models.change import AffectedSet, NetDelta
from scitools_hook.models.findings import Finding
from scitools_hook.models.snapshot import ProjectSnapshot


class LeanResult(NamedTuple):
    """Everything one lean step produced: what it found, what it could not measure, and by how
    much the change moved the project's length.

    ``net_delta`` is ``None`` for a run with no before side, which is not the same answer as a
    delta of zero (requirement 7.4) -- the report prints no line at all for the first and
    ``net: +0 lloc`` for the second.
    """

    findings: list[Finding]
    notes: list[str]
    net_delta: NetDelta | None


def evaluate(
    rules: LeanRules,
    after: ProjectSnapshot,
    before: ProjectSnapshot | None,
    affected: AffectedSet,
    accuracy: float | None = None,
) -> LeanResult:
    """Run every lean rule this configuration switched on, and measure the change (req 9.4).

    ``rules`` is ``settings.lean``. A rule whose severity is ``None`` is not called, so a
    configuration with the shipped defaults does exactly what a check did before this family
    existed, apart from the delta.

    ``accuracy`` is the **after side's** ``und analyze -accuracy`` figure -- the code this
    change proposes, which is the side every one of these rules reads its facts from. It is a
    parameter and not a snapshot field because the snapshot carries none: the figure lives on
    ``AnalyzeResult.accuracy``, and ``runner.check.run`` has both sides in hand from
    ``_figures``. ``None`` is what a 6.5 install reports, what a build that was not asked
    reports and what a caller that wired nothing through passes; all three **refuse**, because
    the figure is the licence to say a name is unused and a licence nobody produced is not a
    licence (requirement 1.8).

    The floors it is judged against are the operator's, from ``[lean]``, so this is the one
    place the settings become a :class:`~scitools_hook.analysis.lean.dead.Trust`. Building it
    here rather than at each rule is what keeps the five rules holding one opinion about what
    "trusted" means in a run.
    """
    trust = Trust(accuracy, rules.resolution_floor, rules.accuracy_floor)
    outcomes = [
        *_dead_rules(rules, after, affected, trust),
        *_layering_rules(rules, after, affected, trust),
    ]
    findings = [finding for outcome in outcomes for finding in outcome.findings]
    notes = [note for outcome in outcomes for note in outcome.unavailable]
    findings += _over_exports(rules, after, affected)
    delta = net_delta(after, before, affected)
    findings += _growth(rules, delta)
    return LeanResult(findings=findings, notes=notes, net_delta=delta)


def _dead_rules(
    rules: LeanRules, after: ProjectSnapshot, affected: AffectedSet, trust: Trust
) -> list[LeanOutcome]:
    """The three dead-code rules that are on, in the order ``[lean]`` writes their switches.

    One ``if`` per rule and each a statement of its own, because branch coverage records no
    arc for an ``and`` short circuit: a guard fused into a boolean can be deleted with the
    module reporting 100% and every test green, which this family has now shipped twice.
    **Three guards here and two in** :func:`_layering_rules`, and
    ``test_one_reference_rule_off_is_the_only_one_missing`` is parametrised over exactly those
    five -- one case per guard, counted from the code rather than from a sentence, which is
    what task 4.2's reviews cost three rounds. Each of the five was shown to fail with its own
    guard removed.

    The variable rule takes the change's **files** rather than its entity keys, because a
    module-level binding is not an entity of any scope: it is a
    :class:`~scitools_hook.models.snapshot.Definition` recorded against the file that binds it.
    """
    found: list[LeanOutcome] = []
    keys, files = affected.keys, affected.files
    if rules.unused_parameters is not None:
        ignore = rules.unused_parameters_ignore
        found.append(find_unused_parameters(after, keys, rules.unused_parameters, ignore, trust))
    if rules.unused_classes is not None:
        ignore = rules.unused_classes_ignore
        found.append(find_unused_classes(after, keys, rules.unused_classes, ignore, trust))
    if rules.unused_variables is not None:
        ignore = rules.unused_variables_ignore
        found.append(find_unused_variables(after, files, rules.unused_variables, ignore, trust))
    return found


def _layering_rules(
    rules: LeanRules, after: ProjectSnapshot, affected: AffectedSet, trust: Trust
) -> list[LeanOutcome]:
    """The two structure rules that read the reference walk (req 2.1, 3.1).

    **Only one of the two takes ``trust``, and that is the design's decision rather than an
    omission.** Requirement 2.6 gives the pass-through rule requirement 1.8's floors in as
    many words, because an understated caller count moves a routine *towards* that rule's
    predicate. Requirement 3 names neither floor, and of the two only the accuracy one is even
    arguable for it -- ``referrers`` counts use, type and inheritance references rather than
    call edges, so a partly resolved call graph does not bound it. So the rule ships off, takes
    no gate, and task 6.4 owns the measurement that would decide whether one belongs.
    """
    found: list[LeanOutcome] = []
    keys = affected.keys
    if rules.pass_through is not None:
        limits = PassThroughLimits(rules.pass_through_max_statements, rules.pass_through_ignore)
        found.append(find_pass_through(after, keys, rules.pass_through, limits, trust))
    if rules.single_implementation is not None:
        ignore = rules.single_implementation_ignore
        found.append(find_single_implementations(after, keys, rules.single_implementation, ignore))
    return found


def _over_exports(rules: LeanRules, after: ProjectSnapshot, affected: AffectedSet) -> list[Finding]:
    """The over-export rule, or nothing at all while it is off (req 4.1, 9.4).

    The guard is a statement of its own rather than a clause fused into a boolean, because
    branch coverage records no arc for an ``and`` short circuit: a guard written that way can
    be deleted with the module reporting 100% and every test green, which this family has now
    shipped twice.
    """
    if rules.over_export is None:
        return []
    return find_over_exports(after, affected.files, rules.over_export, rules.over_export_ignore)


def _growth(rules: LeanRules, delta: NetDelta | None) -> list[Finding]:
    """The optional project-scope finding for a change that made the project longer (7.5).

    Two ways to answer nothing and they are different facts: there was no before side to
    subtract from, or the operator configured no maximum. Both are the shipped case.
    """
    if delta is None:
        return []
    if rules.max_net_growth is None:
        return []
    found = net_growth_finding(delta, rules.max_net_growth, rules.net_growth_severity)
    return [] if found is None else [found]
