"""The pass-through rule: a routine that adds a name and a hop and nothing else (req 2.1-2.6).

**What this rule may say, after the amendment task 4.2 made to requirement 2.1.** The finding
names the routine and the callee it forwards to, and it does **not** name the caller. The
snapshot records ``LeanFacts.callers`` as a count, so no rule can recover a name that was
never kept; the amendment recorded in ``requirements.md`` argues that the caller is the edit
site rather than part of the remedy, and that a named caller would in any case be a claim this
rule may not make -- ``worker_lean._project_callers`` counts project *routines*, and a Python
call from module scope is recorded against the file, which is not one.

That is also the rule's **accepted error**, recorded by task 3.1 and asserted here as the
shape it takes rather than as a defect: a routine called once from another routine and once
from module scope measures one caller and satisfies the predicate. There is no test below
that can catch it, because the snapshot the rule reads carries the same one as a true
instance; what the tests do assert is that the finding never claims more than the count says.

**One caller is not the finding, and that is the test that matters most** (req 2.2). The Gate's
own hints ask for decomposition, so a routine with one caller and a body of its own is a rule
following advice this tool gives. What makes a pass-through is the *conjunction*: one caller,
one callee, and a statement count small enough to leave no room for a body.

The floors (req 2.6, and requirement 1.8 as amended) are the dead-code rules' two, asked
through the same gate, and the direction is what makes them necessary here. An undercounted
caller list moves a routine **towards** this rule's predicate -- a routine with three callers
of which two did not resolve measures one -- so a partly resolved call graph does not make
this rule quiet, it makes it wrong.

The shares below are constructed rather than measured, on the same terms as
``tests/analysis/lean/test_dead.py`` records: no corpus has had its call resolution paired
with a false-positive count for this rule.
"""

from __future__ import annotations

from typing import Final

from scitools_hook.analysis.lean.dead import (
    DEFAULT_ACCURACY_FLOOR,
    DEFAULT_RESOLUTION_FLOOR,
    Trust,
)
from scitools_hook.analysis.lean.layering import (
    DEFAULT_MAX_STATEMENTS,
    PASS_THROUGH_RULE,
    PassThroughLimits,
    find_pass_through,
)
from scitools_hook.config.models import DEFAULT_UNUSED_IGNORE, LeanRules
from scitools_hook.models.snapshot import (
    CallResolution,
    EntityKey,
    EntityRecord,
    EntityRef,
    LeanFacts,
    ProjectSnapshot,
)

PATH: Final = "src/app/service.py"
OTHER: Final = "src/app/report.py"

FORWARDER: Final = "app.service.save"
TARGET: Final = "app.store.write"

RESOLVED: Final = CallResolution(resolved=80, external=15, unresolved=5)
"""A constructed call resolution above the placeholder floor: 80% became a project edge."""

BELOW_FLOOR: Final = CallResolution(resolved=19, external=50, unresolved=31)
"""A constructed call resolution below the placeholder floor. Not any corpus's measurement."""

TRUSTED: Final = Trust(accuracy=0.95)
"""A run whose analysis read 95% of its files cleanly, above the placeholder accuracy floor.

Passed by every test that is about something other than the floors, because the default
``Trust()`` measures no accuracy and refuses -- so a test that left it out would pass for a
reason it was not written to check.
"""


def facts(
    callers: int | None = 1,
    callees: int | None = 1,
    forwards_to: str | None = TARGET,
    overrides: bool | None = False,
) -> LeanFacts:
    """The four facts the rule reads about one routine, every one of them measured."""
    return LeanFacts(callers=callers, callees=callees, forwards_to=forwards_to, overrides=overrides)


MEASURED: Final = facts()
"""The facts of a routine that forwards: one caller, one callee, a name for it, no override.

A named default rather than a call in the signature, so that ``lean=None`` keeps its own
meaning -- **the worker was not asked** -- in a helper whose ordinary answer is a full set of
facts. A helper defaulting through ``None`` cannot express the case the unavailable tests are
about, and every one of them passed against a rule that never looked.
"""


def routine(
    longname: str = FORWARDER,
    lean: LeanFacts | None = MEASURED,
    statements: float | None = 2.0,
    path: str = PATH,
    language: str = "Python",
) -> EntityRecord:
    """One recorded routine, its statement count present unless a test takes it away.

    No ``line`` parameter: no test ever set one, and a sixth parameter puts this helper over
    ``routine.CountParams`` and blocks the commit. A parameter nothing passes is what this
    very feature's requirement 1.2 reports, so it is deleted rather than defended.
    """
    key = EntityKey(scope="routine", path=path, longname=longname, parameters="")
    return EntityRecord(
        ref=EntityRef(key=key, kind="Method", name=longname.rpartition(".")[2], line=12),
        language=language,
        metrics={} if statements is None else {"CountStmt": statements},
        lean=lean,
    )


def snapshot(
    records: tuple[EntityRecord, ...] = (),
    resolution: CallResolution = RESOLVED,
) -> ProjectSnapshot:
    """An after snapshot carrying only what the pass-through rule reads."""
    return ProjectSnapshot(
        side="after",
        languages=["Python"],
        entities={record.key: record for record in records},
        call_resolution={"Python": resolution},
    )


def keys(*records: EntityRecord) -> set[EntityKey]:
    """The affected set, spelled from the records a test built."""
    return {record.key for record in records}


# --- the finding ------------------------------------------------------------------


def test_one_caller_one_callee_and_a_body_within_the_budget_is_reported() -> None:
    """Requirement 2.1: the routine is the finding and the callee is named in it."""
    record = routine()

    outcome = find_pass_through(snapshot((record,)), keys(record), trust=TRUSTED)

    (finding,) = outcome.findings
    assert outcome.unavailable == ()
    assert finding.kind == "structural"
    assert finding.rule == PASS_THROUGH_RULE
    assert finding.rule == "structure.pass_through"
    assert finding.scope == "routine"
    assert finding.metric is None
    assert finding.path == PATH
    assert finding.line == 12
    assert finding.value == 2
    assert finding.before is None
    assert finding.limit == DEFAULT_MAX_STATEMENTS
    assert finding.limit_source == "rule"
    assert finding.severity == "warning"
    assert finding.blocking is False
    assert finding.preexisting is False
    assert finding.hint == ""
    assert finding.details == {"forwards_to": TARGET, "longname": FORWARDER}
    assert FORWARDER in finding.message
    assert TARGET in finding.message


def test_the_finding_never_names_a_caller_it_did_not_measure() -> None:
    """The amendment to requirement 2.1, asserted where it can be seen: a count is not a name.

    The facts carry ``callers`` as an integer and nothing else about the caller, so a message
    or a detail naming one would be invented. This test is what stops a later change from
    quietly re-introducing the wording the requirement used to ask for.
    """
    record = routine()

    (finding,) = find_pass_through(snapshot((record,)), keys(record), trust=TRUSTED).findings

    assert "caller" not in finding.details
    assert "called_by" not in finding.details
    assert "one project routine calls" in finding.message


def test_the_budget_of_two_accepts_a_call_and_return_body() -> None:
    """Requirement 2.1's shipped budget: ``return other(x)`` is two statements, not one."""
    record = routine(statements=2.0)

    outcome = find_pass_through(
        snapshot((record,)), keys(record), limits=PassThroughLimits(max_statements=2), trust=TRUSTED
    )

    assert [finding.value for finding in outcome.findings] == [2.0]


def test_the_finding_carries_the_body_it_measured_and_not_the_budget_it_was_judged_by() -> None:
    """``value`` is the statement count; ``limit`` is the number an operator would raise.

    The two coincide in every fixture whose body exactly fills the budget -- which the shipped
    default of 2 and a two-statement helper make the easy fixture to write -- and a finding
    publishing the limit under both names would pass all of them. This one is built where the
    two differ: a one-statement body judged against a budget of two.
    """
    record = routine(statements=1.0)

    (finding,) = find_pass_through(
        snapshot((record,)),
        keys(record),
        limits=PassThroughLimits(max_statements=2),
        trust=TRUSTED,
    ).findings

    assert finding.value == 1.0
    assert finding.limit == 2.0


def test_a_budget_of_one_refuses_the_same_body() -> None:
    """The budget is the operator's number, and lowering it is what makes the rule quieter."""
    record = routine(statements=2.0)

    outcome = find_pass_through(
        snapshot((record,)), keys(record), limits=PassThroughLimits(max_statements=1), trust=TRUSTED
    )

    assert outcome.findings == []


def test_an_error_severity_makes_the_finding_blocking() -> None:
    """Severity travels from the configured rule, as every structural rule's does (req 2.4)."""
    record = routine()

    outcome = find_pass_through(snapshot((record,)), keys(record), severity="error", trust=TRUSTED)

    (finding,) = outcome.findings
    assert finding.severity == "error"
    assert finding.blocking is True


def test_findings_are_reported_in_path_order() -> None:
    """Two forwarders in one change, in the order a reader meets them."""
    here = routine()
    there = routine(longname="app.report.render", path=OTHER)

    outcome = find_pass_through(snapshot((here, there)), keys(here, there), trust=TRUSTED)

    assert [finding.path for finding in outcome.findings] == [OTHER, PATH]


# --- what the rule refuses to say -------------------------------------------------


def test_one_caller_with_two_callees_is_a_decomposition_and_not_a_finding() -> None:
    """Requirement 2.2, said explicitly: a body of its own is what the Gate's hints ask for.

    ``forwards_to`` is ``None`` here because the worker sets it only for a single callee, so
    this case also proves the rule cannot name a callee it does not have.
    """
    record = routine(lean=facts(callees=2, forwards_to=None))

    outcome = find_pass_through(snapshot((record,)), keys(record), trust=TRUSTED)

    assert outcome.findings == []
    assert outcome.unavailable == ()


def test_a_routine_nothing_calls_is_the_dead_code_rules_question() -> None:
    """Zero callers is ``structure.unused_routine``'s finding, never this one."""
    record = routine(lean=facts(callers=0))

    assert find_pass_through(snapshot((record,)), keys(record), trust=TRUSTED).findings == []


def test_two_callers_are_a_shared_helper() -> None:
    """A second caller makes the routine a helper, which is the shape this rule protects."""
    record = routine(lean=facts(callers=2))

    assert find_pass_through(snapshot((record,)), keys(record), trust=TRUSTED).findings == []


def test_an_overriding_routine_is_the_interface_doing_its_job() -> None:
    """Requirement 2.3: a forwarding override is conformance, not a layer to delete."""
    record = routine(lean=facts(overrides=True))

    assert find_pass_through(snapshot((record,)), keys(record), trust=TRUSTED).findings == []


def test_a_body_longer_than_the_budget_is_not_forwarding() -> None:
    """Three statements leave room for logic, whatever the call counts say (req 2.2)."""
    record = routine(statements=3.0)

    assert find_pass_through(snapshot((record,)), keys(record), trust=TRUSTED).findings == []


def test_a_routine_with_no_statement_count_is_not_judged() -> None:
    """An unmeasured ``CountStmt`` is not a body of zero statements.

    The discriminating record: every other condition of the rule is met and only the presence
    guard stands between this and a finding about a routine whose body was never measured. A
    rule reading the metric with a default of zero reports it, and a budget high enough to
    swallow any body would not save it.
    """
    record = routine(statements=None)

    outcome = find_pass_through(
        snapshot((record,)),
        keys(record),
        limits=PassThroughLimits(max_statements=99),
        trust=TRUSTED,
    )

    assert outcome.findings == []
    assert outcome.unavailable == ()


def test_two_callees_with_a_forwarding_name_are_still_two_callees() -> None:
    """The record that separates the two callee guards, and the reason both are here.

    The test above pairs ``callees == 2`` with ``forwards_to is None``, which is what the
    worker produces, so it cannot tell the count guard from the name guard: dropping either
    leaves it green. This record disagrees with itself -- two callees *and* a forwarding name
    -- and only the count guard refuses it. ``worker_lean.routine_facts`` keeps the invariant
    that makes it impossible today; what the guard defends is requirement 2.2's subject being
    the **count**, so that a later worker giving ``forwards_to`` a second meaning cannot turn
    a routine with a body of its own into a finding.
    """
    record = routine(lean=facts(callees=2, forwards_to=TARGET))

    assert find_pass_through(snapshot((record,)), keys(record), trust=TRUSTED).findings == []


def test_a_single_callee_the_worker_could_not_name_is_not_reported() -> None:
    """A record claiming one callee and carrying no name for it cannot state its own remedy.

    ``worker_lean.routine_facts`` sets ``forwards_to`` exactly when ``callees == 1``, so this
    record is not one that worker produces; the guard is what keeps a hand-built or a later
    worker's record from producing a finding whose remedy reads ``forwards to None``.
    """
    record = routine(lean=facts(forwards_to=None))

    assert find_pass_through(snapshot((record,)), keys(record), trust=TRUSTED).findings == []


def test_a_routine_the_change_did_not_touch_is_not_reported() -> None:
    """This is a gate on a commit: another routine's hop is not this change's finding."""
    record = routine()

    assert find_pass_through(snapshot((record,)), set(), trust=TRUSTED).findings == []


def test_a_routine_the_change_deleted_cannot_be_reported() -> None:
    """The rule reads the after side, where a deleted routine has no record at all."""
    gone = EntityKey(scope="routine", path=PATH, longname="app.service.dropped", parameters="")

    outcome = find_pass_through(snapshot((routine(),)), {gone}, trust=TRUSTED)

    assert outcome.findings == []
    assert outcome.unavailable == ()


def test_an_affected_class_does_not_wake_the_routine_rule() -> None:
    """A change touching only classes leaves this rule with nothing to judge and says so."""
    klass = EntityKey(scope="class", path=PATH, longname="app.service.Store", parameters=None)

    outcome = find_pass_through(snapshot((routine(),)), {klass}, trust=TRUSTED)

    assert outcome == find_pass_through(snapshot((routine(),)), set(), trust=TRUSTED)
    assert outcome.findings == []


# --- the ignore list --------------------------------------------------------------


def test_the_shipped_list_excuses_an_entry_point() -> None:
    """Requirement 2.3: ``main`` forwards to one routine on purpose, in every language."""
    record = routine(longname="app.cli.main")

    outcome = find_pass_through(
        snapshot((record,)),
        keys(record),
        limits=PassThroughLimits(ignore=DEFAULT_UNUSED_IGNORE),
        trust=TRUSTED,
    )

    assert outcome.findings == []


def test_the_shipped_list_excuses_a_test_function() -> None:
    """A test that calls one routine is the shape of a test, not a layer (req 2.3)."""
    record = routine(longname="tests.app.test_save")

    outcome = find_pass_through(
        snapshot((record,)),
        keys(record),
        limits=PassThroughLimits(ignore=DEFAULT_UNUSED_IGNORE),
        trust=TRUSTED,
    )

    assert outcome.findings == []


def test_an_operators_own_pattern_excuses_a_routine() -> None:
    """The list is regular expressions over long names, matched anywhere in the name."""
    record = routine()

    outcome = find_pass_through(
        snapshot((record,)),
        keys(record),
        limits=PassThroughLimits(ignore=[r"\.save$"]),
        trust=TRUSTED,
    )

    assert outcome.findings == []


# --- the three states -------------------------------------------------------------


def test_a_snapshot_without_the_reference_walk_reports_the_rule_unavailable() -> None:
    """Requirement 2.5: an analysis cache recorded before the rule was on judges nothing."""
    record = routine(lean=None)

    outcome = find_pass_through(snapshot((record,)), keys(record), trust=TRUSTED)

    assert outcome.findings == []
    (message,) = outcome.unavailable
    assert message.startswith(PASS_THROUGH_RULE)
    assert "db rebuild" in message
    # The verdict half of the sentence the dead-code rules share: this rule judges a routine,
    # and the word "unused" belongs to the three rules that judge a name.
    assert "so nothing was judged;" in message


def test_an_unmeasured_caller_count_is_not_a_count_of_none() -> None:
    """The three-state discipline, one fact at a time: ``callers`` absent stops the rule."""
    record = routine(lean=facts(callers=None))

    outcome = find_pass_through(snapshot((record,)), keys(record), trust=TRUSTED)

    assert outcome.findings == []
    assert len(outcome.unavailable) == 1


def test_an_unmeasured_callee_count_is_not_a_count_of_none() -> None:
    """The same, for the fact requirement 2.2 turns on."""
    record = routine(lean=facts(callees=None))

    outcome = find_pass_through(snapshot((record,)), keys(record), trust=TRUSTED)

    assert outcome.findings == []
    assert len(outcome.unavailable) == 1


def test_an_unmeasured_override_flag_is_not_a_routine_that_overrides_nothing() -> None:
    """Read as ``False`` it would report the forwarding override requirement 2.3 excuses."""
    record = routine(lean=facts(overrides=None))

    outcome = find_pass_through(snapshot((record,)), keys(record), trust=TRUSTED)

    assert outcome.findings == []
    assert len(outcome.unavailable) == 1


def test_one_unmeasured_routine_stops_the_rule_for_the_whole_run() -> None:
    """A partial answer over the records that were measured is not an answer (req 2.5)."""
    measured = routine()
    unmeasured = routine(longname="app.report.render", path=OTHER, lean=None)

    outcome = find_pass_through(
        snapshot((measured, unmeasured)), keys(measured, unmeasured), trust=TRUSTED
    )

    assert outcome.findings == []
    assert len(outcome.unavailable) == 1


def test_a_change_touching_no_routine_is_told_nothing_is_missing() -> None:
    """A rule with nothing to judge did not need the walk, so it must not complain about it.

    The snapshot carries a routine with no facts at all, which is exactly what the unavailable
    message is for, and the change touches nothing. This rule needs no guard to get that
    right -- an empty walk answers an empty fact list rather than ``None`` and never asks the
    gate -- and the assertion is here because the behaviour is requirement 2.5's, not because
    a guard implements it. Its two siblings do need one, for the reasons ``find_pass_through``
    records.
    """
    outcome = find_pass_through(snapshot((routine(lean=None),)), set(), trust=TRUSTED)

    assert outcome == find_pass_through(snapshot(), set(), trust=TRUSTED)
    assert outcome.unavailable == ()


# --- the two floors ---------------------------------------------------------------


def test_a_call_graph_below_the_resolution_floor_reports_nothing_and_says_why() -> None:
    """Requirement 2.6: an understated caller count is exactly what this rule reports on."""
    record = routine()

    outcome = find_pass_through(
        snapshot((record,), resolution=BELOW_FLOOR), keys(record), trust=TRUSTED
    )

    assert outcome.findings == []
    (message,) = outcome.unavailable
    assert message.startswith(PASS_THROUGH_RULE)
    assert "Python" in message
    assert f"{DEFAULT_RESOLUTION_FLOOR:.0%}" in message


def test_a_run_that_measured_no_accuracy_refuses_before_it_looks() -> None:
    """Requirement 1.8's second floor, which the default ``Trust`` refuses on."""
    record = routine()

    outcome = find_pass_through(snapshot((record,)), keys(record))

    assert outcome.findings == []
    (message,) = outcome.unavailable
    assert "analysis accuracy" in message


def test_a_run_below_the_accuracy_floor_says_so_once_for_the_run() -> None:
    """Accuracy is reported per side, so one sentence covers every language it read."""
    record = routine()

    outcome = find_pass_through(snapshot((record,)), keys(record), trust=Trust(accuracy=0.19))

    assert outcome.findings == []
    assert len(outcome.unavailable) == 1
    assert "19%" in outcome.unavailable[0]


def test_the_floor_is_asked_before_the_facts_are_believed() -> None:
    """The guard order requirement 2.6 turns on, and the case where the two answers differ.

    Every affected routine here would have been dropped by the override exclusion anyway, so
    a rule that consulted the facts first falls silent about an analysis it never judged. The
    sentence requirement 2.6 asks for is "the rule was not evaluated", and it has to be said
    on the commit where nothing happened to qualify.
    """
    record = routine(lean=facts(overrides=True))

    outcome = find_pass_through(
        snapshot((record,), resolution=BELOW_FLOOR), keys(record), trust=TRUSTED
    )

    assert outcome.findings == []
    assert len(outcome.unavailable) == 1


def test_the_floor_is_asked_before_the_shape_is_read() -> None:
    """The same order, one guard further down: the affected routine is not a forwarder at all.

    ``test_the_floor_is_asked_before_the_facts_are_believed`` above exercises only the
    ``overrides`` guard, so it pins the gate above *that* one and says nothing about the two
    below it. This routine has two callees -- a decomposition, which requirement 2.2 makes a
    non-finding on any run -- and requirement 2.6 still wants the run told the rule was not
    evaluated, because "this routine would not have been reported anyway" is a fact the
    analysis was too unresolved to be trusted about.
    """
    record = routine(lean=facts(callees=2, forwards_to=None))

    outcome = find_pass_through(
        snapshot((record,), resolution=BELOW_FLOOR), keys(record), trust=TRUSTED
    )

    assert outcome.findings == []
    assert len(outcome.unavailable) == 1


def test_the_floor_is_asked_before_the_body_is_measured() -> None:
    """The third of the four: a body too long for the budget, on a run below the floor.

    The guard this one is about is ``_within_budget``, the third of the four conditions after
    the gate -- ``name_excused`` sits below it, and the test that follows pins that one. A
    forwarder in every other respect, with three statements against a budget of two, so the
    gate is the only thing that can produce the sentence -- and it must, because the statement
    count is not what the floor casts doubt on.
    """
    record = routine(statements=3.0)

    outcome = find_pass_through(
        snapshot((record,), resolution=BELOW_FLOOR), keys(record), trust=TRUSTED
    )

    assert outcome.findings == []
    assert len(outcome.unavailable) == 1


def test_the_floor_is_asked_before_the_ignore_list_is_consulted() -> None:
    """The fourth and last position in the chain: the operator's ignore list.

    ``_reported_forwarder`` is the gate plus four post-gate conditions, so pinning the gate
    above the first three says nothing about the fourth. This routine is a perfect forwarder
    that the operator excused by name, on a run below the call-resolution floor: hoisting
    ``name_excused`` above ``gate.allows`` drops the requirement 2.6 sentence for the whole
    run while the rest of the suite stays green, because an excused routine is the one case
    where the finding is empty either way. Requirement 2.6 wants the run told the rule was
    not evaluated once per run, and being excused is not being evaluated.
    """
    record = routine()

    outcome = find_pass_through(
        snapshot((record,), resolution=BELOW_FLOOR),
        keys(record),
        limits=PassThroughLimits(ignore=[r"\.save$"]),
        trust=TRUSTED,
    )

    assert outcome.findings == []
    assert len(outcome.unavailable) == 1


def test_a_language_with_no_resolution_figure_is_refused_rather_than_assumed() -> None:
    """An absent figure is not a good one: the gate refuses what it cannot measure."""
    record = routine(language="C++")

    outcome = find_pass_through(snapshot((record,)), keys(record), trust=TRUSTED)

    assert outcome.findings == []
    (message,) = outcome.unavailable
    assert "C++" in message


def test_a_run_exactly_at_the_accuracy_floor_is_allowed_to_speak() -> None:
    """The floor is a minimum met, not one to exceed: ``below`` is ``<`` and not ``<=``."""
    record = routine()

    outcome = find_pass_through(
        snapshot((record,)), keys(record), trust=Trust(accuracy=DEFAULT_ACCURACY_FLOOR)
    )

    assert len(outcome.findings) == 1
    assert outcome.unavailable == ()


def test_a_call_graph_exactly_at_the_resolution_floor_is_allowed_to_speak() -> None:
    """The same boundary on the other floor, measured from a share of exactly 75%."""
    at_floor = CallResolution(resolved=75, external=25, unresolved=0)
    record = routine()

    outcome = find_pass_through(
        snapshot((record,), resolution=at_floor), keys(record), trust=TRUSTED
    )

    assert at_floor.internal == DEFAULT_RESOLUTION_FLOOR
    assert len(outcome.findings) == 1
    assert outcome.unavailable == ()


# --- the shipped stance -----------------------------------------------------------


def test_the_rule_ships_off() -> None:
    """Requirement 2.4: the whole family is silent until an operator names a severity."""
    assert LeanRules().pass_through is None


def test_the_rule_defaults_to_a_warning_when_it_is_called() -> None:
    """Requirement 2.4's second half: enabled, it warns rather than blocks."""
    record = routine()

    (finding,) = find_pass_through(snapshot((record,)), keys(record), trust=TRUSTED).findings

    assert finding.severity == "warning"


def test_the_budget_default_is_the_settings_models_own() -> None:
    """One number, asked of the section that owns it rather than restated here (req 2.1)."""
    assert DEFAULT_MAX_STATEMENTS == LeanRules().pass_through_max_statements
    assert PassThroughLimits().max_statements == LeanRules().pass_through_max_statements


def test_no_routine_is_excused_until_an_operator_names_a_pattern() -> None:
    """The shipped limits carry the budget and an empty ignore list, as the rule ships off."""
    assert PassThroughLimits().ignore == ()
