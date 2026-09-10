"""The lean-code family's one step in a check: the rules that are on, and the change's delta.

Six rules, one number and one place they are called from. The step exists so that
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
No rule in the family can answer "unavailable" yet: over-export reads file metrics, the file
edges and the definitions walk, all of which the snapshot already carries, so it has no third
state (see :mod:`scitools_hook.analysis.lean.layering`). The list is empty until groups 4 and 5
land the five rules that fill it, and it is here now because a step that gathered notes nobody
printed would swallow all five silently.

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

from scitools_hook.analysis.lean.layering import find_over_exports
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
) -> LeanResult:
    """Run every lean rule this configuration switched on, and measure the change (req 9.4).

    ``rules`` is ``settings.lean``. A rule whose severity is ``None`` is not called, so a
    configuration with the shipped defaults does exactly what a check did before this family
    existed, apart from the delta.
    """
    findings: list[Finding] = []
    notes: list[str] = []
    findings += _over_exports(rules, after, affected)
    delta = net_delta(after, before, affected)
    findings += _growth(rules, delta)
    return LeanResult(findings=findings, notes=notes, net_delta=delta)


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
