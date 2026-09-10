"""The three dead-code rules, and the two guards that keep them from deleting working code.

Requirements 1.1 through 1.9. Most of what is asserted here is silence, and the reason is a
measurement rather than a preference:

* **facdrone, 2026-09-10**, 417 files, ``und analyze -accuracy`` 26%: the naive predicate
  answers 830 routines and about 6160 lines, and is wrong nearly every time. That codebase
  satisfies its interfaces structurally, so an implementation holds no reference to the
  interface it satisfies, and exactly **one** of the 830 carried an override reference.
* **this repository, 2026-09-10**, accuracy 19%: every one of the sixteen module bindings in
  ``src/`` that the snapshot answers ``referenced: false`` for is in fact read. Understand
  recorded no use reference because the use sites sit in regions its analysis errored on. A
  **hundred per cent** false-positive rate, on the tool's own source.

So the tests below are written twice over: once for the finding, and once for the case where
the rule must say nothing and explain itself.

**The shares below are constructed, not measured.** ``BELOW_FLOOR`` and ``WELL_BELOW`` are
chosen to sit under the placeholder floors and ``RESOLVED`` to sit above them; neither corpus
above had its call resolution measured at all, and the accuracy figures they did produce are
quoted in prose, never asserted here as though a unit test could check them. The one thing
these tests can prove about the floors is what the module does on either side of them, and
that is all they claim.
"""

from __future__ import annotations

from typing import Final

from scitools_hook.analysis.lean.dead import (
    CLASS_RULE,
    DEFAULT_ACCURACY_FLOOR,
    DEFAULT_RESOLUTION_FLOOR,
    INTERFACE_DECLARERS,
    PARAMETER_RULE,
    VARIABLE_RULE,
    Trust,
    find_unused_classes,
    find_unused_parameters,
    find_unused_variables,
)
from scitools_hook.config.models import (
    DEFAULT_LEAN_CLASS_IGNORE,
    DEFAULT_LEAN_PARAMETER_IGNORE,
    DEFAULT_LEAN_VARIABLE_IGNORE,
    LeanRules,
)
from scitools_hook.models.snapshot import (
    CallResolution,
    Definition,
    EntityKey,
    EntityRecord,
    EntityRef,
    LeanFacts,
    ProjectSnapshot,
)

PATH: Final = "src/app/service.py"
OTHER: Final = "src/app/report.py"

RESOLVED: Final = CallResolution(resolved=80, external=15, unresolved=5)
"""A constructed call resolution above the placeholder floor: 80% became a project edge."""

BELOW_FLOOR: Final = CallResolution(resolved=19, external=50, unresolved=31)
"""A constructed call resolution below the placeholder floor. Not any corpus's measurement."""

WELL_BELOW: Final = CallResolution(resolved=26, external=44, unresolved=30)
"""A second constructed share below the floor, for the two-language case."""

TRUSTED: Final = Trust(accuracy=0.95)
"""A run whose analysis read 95% of its files cleanly: above the placeholder accuracy floor.

Passed by every test that is about something other than the floors, because the default
``Trust()`` measures no accuracy and refuses -- so a test that left it out would pass for a
reason it was not written to check.
"""

UNREAD: Final = Trust(accuracy=0.19)
"""A run below the placeholder accuracy floor: the shape all sixteen false positives had."""


def facts(unused: tuple[str, ...] = (), overrides: bool = False) -> LeanFacts:
    """The facts the parameter rule reads about one routine, both measured."""
    return LeanFacts(callers=1, callees=0, overrides=overrides, unused_parameters=list(unused))


def routine(longname: str, lean: LeanFacts | None, language: str = "Python") -> EntityRecord:
    """One recorded routine carrying the facts under test, or none at all."""
    key = EntityKey(scope="routine", path=PATH, longname=longname, parameters="")
    return EntityRecord(
        ref=EntityRef(key=key, kind="Method", name=longname.rpartition(".")[2], line=12),
        language=language,
        lean=lean,
    )


def elsewhere(longname: str, unused: tuple[str, ...], line: int = 3) -> EntityRecord:
    """One recorded routine in the other file, for the ordering tests."""
    key = EntityKey(scope="routine", path=OTHER, longname=longname, parameters="")
    return EntityRecord(
        ref=EntityRef(key=key, kind="Method", name=longname.rpartition(".")[2], line=line),
        language="Python",
        lean=facts(unused=unused),
    )


def klass(longname: str, lean: LeanFacts | None, path: str = PATH) -> EntityRecord:
    """One recorded class carrying the facts the unused-class rule reads."""
    key = EntityKey(scope="class", path=path, longname=longname, parameters=None)
    return EntityRecord(
        ref=EntityRef(key=key, kind="Class", name=longname.rpartition(".")[2], line=4),
        language="Python",
        lean=lean,
    )


def file_record(path: str = PATH, language: str = "Python") -> EntityRecord:
    """The record that tells the variable rule which language a module binding is written in."""
    key = EntityKey(scope="file", path=path, longname=path)
    return EntityRecord(
        ref=EntityRef(key=key, kind="File", name=path.rpartition("/")[2], line=1),
        language=language,
    )


def snapshot(
    records: tuple[EntityRecord, ...] = (),
    resolution: CallResolution = RESOLVED,
    declarations: dict[str, int] | None = None,
    definitions: tuple[Definition, ...] = (),
) -> ProjectSnapshot:
    """An after snapshot carrying only what the three rules read, Python resolving well."""
    return ProjectSnapshot(
        side="after",
        languages=["Python"],
        entities={record.key: record for record in records},
        call_resolution={"Python": resolution},
        method_declarations=declarations if declarations is not None else {},
        definitions=list(definitions),
    )


def binding(name: str, referenced: bool | None, path: str = PATH, line: int = 9) -> Definition:
    """One module-level binding and what the reference walk said about it."""
    return Definition(name=name, path=path, line=line, value="1", referenced=referenced)


# --- the parameter finding --------------------------------------------------------


def test_a_parameter_the_routine_never_reads_is_reported_at_the_routine() -> None:
    """Requirement 1.2: located at the routine, naming the parameter."""
    record = routine("app.Service.run", facts(unused=("verbose",)))

    (finding,) = find_unused_parameters(snapshot((record,)), {record.key}, trust=TRUSTED).findings

    assert finding.rule == PARAMETER_RULE
    assert finding.scope == "routine"
    assert finding.path == PATH
    assert finding.line == 12
    assert finding.details["parameter"] == "verbose"
    assert "verbose" in finding.message
    assert finding.severity == "warning"
    assert finding.blocking is False


def test_every_unused_parameter_of_one_routine_is_its_own_finding() -> None:
    """Requirement 1.2 is per parameter: two names are two things to delete."""
    record = routine("app.Service.run", facts(unused=("verbose", "retries")))

    found = find_unused_parameters(snapshot((record,)), {record.key}, trust=TRUSTED).findings

    assert [finding.details["parameter"] for finding in found] == ["verbose", "retries"]


def test_parameter_findings_are_ordered_by_path_before_name() -> None:
    """Both halves of the sort key, arranged so each one changes the answer.

    The name order and the path order are deliberately opposite: ``app.Zulu`` lives in the
    file that sorts first and ``app.Alpha`` in the file that sorts second, so a sort that lost
    the path would put Alpha first.

    The affected set is passed as a **list, in reverse of the answer**, which is what makes
    the name half of the key testable: a sort keyed on the path alone is stable, so it would
    leave ``app.Beta`` ahead of ``app.Alpha`` inside ``service.py``. With a set the two would
    come out in hash order and the assertion would pass or fail by luck -- ``Collection`` is
    what the signature asks for, and a list is one.
    """
    zulu = elsewhere("app.Zulu.emit", ("count",))
    alpha = routine("app.Alpha.run", facts(unused=("verbose",)))
    beta = EntityRecord(
        ref=EntityRef(
            key=EntityKey(scope="routine", path=PATH, longname="app.Beta.run", parameters=""),
            kind="Method",
            name="run",
            line=30,
        ),
        language="Python",
        lean=facts(unused=("retries",)),
    )
    after = snapshot((zulu, alpha, beta))

    found = find_unused_parameters(after, [beta.key, alpha.key, zulu.key], trust=TRUSTED).findings

    assert [(finding.path, finding.details["longname"]) for finding in found] == [
        (OTHER, "app.Zulu.emit"),
        (PATH, "app.Alpha.run"),
        (PATH, "app.Beta.run"),
    ]


def test_a_routine_whose_parameters_are_all_used_is_not_reported() -> None:
    """The empty list is a measurement: every parameter is read."""
    record = routine("app.Service.run", facts())

    assert find_unused_parameters(snapshot((record,)), {record.key}, trust=TRUSTED).findings == []


def test_an_error_severity_makes_the_parameter_finding_blocking() -> None:
    """The family's shared contract: only an error blocks."""
    record = routine("app.Service.run", facts(unused=("verbose",)))

    outcome = find_unused_parameters(snapshot((record,)), {record.key}, "error", trust=TRUSTED)

    assert outcome.findings[0].blocking is True


# --- what a parameter finding must never be ---------------------------------------


def test_an_overriding_routine_contributes_no_parameter_finding() -> None:
    """Requirement 1.4: a parameter a signature forces is conformance, not dead code."""
    record = routine("app.Service.run", facts(unused=("verbose",), overrides=True))

    assert find_unused_parameters(snapshot((record,)), {record.key}, trust=TRUSTED).findings == []


def test_the_receiver_is_excused_by_the_shipped_ignore_list() -> None:
    """Requirement 1.4's second half. The worker counts ``self`` unused; the rule excuses it."""
    record = routine("app.Service.run", facts(unused=("self", "verbose")))

    found = find_unused_parameters(
        snapshot((record,)), {record.key}, ignore=DEFAULT_LEAN_PARAMETER_IGNORE, trust=TRUSTED
    ).findings

    assert [finding.details["parameter"] for finding in found] == ["verbose"]


def test_the_shipped_list_excuses_the_three_shapes_it_was_written_for() -> None:
    """``self``/``cls``/``this``, a leading underscore, and the forwarding pair (req 1.5)."""
    unused = ("self", "cls", "this", "_unused", "args", "kwargs")
    record = routine("app.Service.run", facts(unused=unused))

    found = find_unused_parameters(
        snapshot((record,)), {record.key}, ignore=DEFAULT_LEAN_PARAMETER_IGNORE, trust=TRUSTED
    ).findings

    assert found == []


def test_an_operators_own_pattern_excuses_a_parameter() -> None:
    """Requirement 1.5: the ignore list is a list of name patterns, applied by search."""
    record = routine("app.Service.run", facts(unused=("verbose",)))

    outcome = find_unused_parameters(
        snapshot((record,)), {record.key}, ignore=["^verb"], trust=TRUSTED
    )

    assert outcome.findings == []


def test_a_method_two_classes_declare_is_an_interface_method() -> None:
    """Requirement 1.9: structural typing leaves no edge, so the tally is the only witness."""
    record = routine("app.Service.run", facts(unused=("verbose",)))
    after = snapshot((record,), declarations={"run": INTERFACE_DECLARERS})

    assert find_unused_parameters(after, {record.key}, trust=TRUSTED).findings == []


def test_a_method_one_class_declares_is_still_reported() -> None:
    """The other side of the threshold: one declaring class is not an interface."""
    record = routine("app.Service.run", facts(unused=("verbose",)))
    after = snapshot((record,), declarations={"run": INTERFACE_DECLARERS - 1})

    assert len(find_unused_parameters(after, {record.key}, trust=TRUSTED).findings) == 1


def test_a_deleted_routine_cannot_be_reported() -> None:
    """Requirement 1.7: the rules read the after side, where a deleted routine is absent."""
    gone = EntityKey(scope="routine", path=PATH, longname="app.Service.old", parameters="")

    assert find_unused_parameters(snapshot(), {gone}, trust=TRUSTED) == ([], ())


def test_a_class_key_is_not_judged_by_the_parameter_rule() -> None:
    """Each rule walks one scope; a class in the affected set is the next rule's business.

    The class here carries facts a routine would be reported for, so a rule that dropped the
    scope filter would answer a finding rather than a silence.
    """
    record = klass("app.Service", facts(unused=("verbose",)))

    assert find_unused_parameters(snapshot((record,)), {record.key}, trust=TRUSTED) == ([], ())


def test_a_routine_key_is_not_judged_by_the_class_rule() -> None:
    """The other half of the same filter: the routine here would be reported if it were read."""
    record = routine("app.Service.run", LeanFacts(referenced=False))

    assert find_unused_classes(snapshot((record,)), {record.key}, trust=TRUSTED) == ([], ())


def test_nothing_affected_is_nothing_said_at_all() -> None:
    """A change touching no routine gets no finding AND no unavailable message.

    Both facts matter, and the second is the one a naive guard order loses: this snapshot
    carries no declaring-class tally, a resolution far below the floor and a run that measured
    no accuracy, any of which would produce a message if the rule reasoned about them before
    asking whether it had anything to judge.
    """
    after = ProjectSnapshot(side="after", call_resolution={"Python": BELOW_FLOOR})

    assert find_unused_parameters(after, set()) == ([], ())


# --- the sentence all three rules share -------------------------------------------


def test_every_dead_rule_says_what_it_did_not_judge() -> None:
    """Requirement 1.6: the verdict in the message is "judged **unused**", for all three.

    ``dead.unavailable`` writes this sentence once for the whole lean family, and the verb
    phrase is the only part that differs between its callers -- these three judge a name
    unused, the layering rules judge a shape and have no adjective for it. That makes the
    word a parameter, and a parameter is exactly what nothing else here stands on: with the
    argument dropped at all four call sites below, or its constant reworded, every one of
    these rules would tell its user "so nothing was judged" instead, and the rest of the
    suite would not notice. This test is the thing that notices.
    """
    unmeasured_routine = routine("app.Service.run", None)
    unmeasured_class = klass("app.Report", None)
    unmeasured_binding = snapshot((file_record(),), definitions=(binding("TIMEOUT", None),))

    messages = [
        find_unused_parameters(
            snapshot((unmeasured_routine,)), {unmeasured_routine.key}, trust=TRUSTED
        ).unavailable[0],
        find_unused_classes(
            snapshot((unmeasured_class,)), {unmeasured_class.key}, trust=TRUSTED
        ).unavailable[0],
        find_unused_variables(unmeasured_binding, {PATH}, trust=TRUSTED).unavailable[0],
    ]

    assert len(messages) == 3
    for message in messages:
        assert "so nothing was judged unused;" in message


# --- the parameter rule's three unmeasured facts ----------------------------------


def test_a_routine_with_no_facts_at_all_makes_the_rule_unavailable() -> None:
    """Requirement 1.6: ``lean is None`` is "not asked", never "nothing found"."""
    record = routine("app.Service.run", None)

    outcome = find_unused_parameters(snapshot((record,)), {record.key}, trust=TRUSTED)

    assert outcome.findings == []
    assert len(outcome.unavailable) == 1
    assert PARAMETER_RULE in outcome.unavailable[0]


def test_an_unmeasured_override_flag_makes_the_rule_unavailable() -> None:
    """``overrides is None`` read as False would report the parameter an interface forces."""
    record = routine("app.Service.run", LeanFacts(unused_parameters=["verbose"]))

    outcome = find_unused_parameters(snapshot((record,)), {record.key}, trust=TRUSTED)

    assert outcome.findings == []
    assert outcome.unavailable != ()


def test_an_unmeasured_parameter_list_makes_the_rule_unavailable() -> None:
    """``unused_parameters is None`` is not an empty list, and not a clean bill either."""
    record = routine("app.Service.run", LeanFacts(overrides=False))

    outcome = find_unused_parameters(snapshot((record,)), {record.key}, trust=TRUSTED)

    assert outcome.findings == []
    assert outcome.unavailable != ()


def test_one_unmeasured_routine_silences_the_whole_rule() -> None:
    """Any affected record, not all of them: one unmeasured routine unmeasures the run.

    The measured routine here carries a genuine finding, so a rule that reported what it
    could would emit it. Requirement 1.6 asks for the message "and nothing else".
    """
    measured = routine("app.Service.run", facts(unused=("verbose",)))
    unmeasured = routine("app.Service.other", None)
    after = snapshot((measured, unmeasured))

    outcome = find_unused_parameters(after, {measured.key, unmeasured.key}, trust=TRUSTED)

    assert outcome.findings == []
    assert len(outcome.unavailable) == 1


def test_a_snapshot_without_the_declaring_class_tally_is_unavailable() -> None:
    """Requirement 1.9's fact has the same three states as every other one.

    Without the tally there is no way to see an interface method under structural typing, and
    the measurement says that is where the false positives are: 830 candidates, one edge.
    """
    record = routine("app.Service.run", facts(unused=("verbose",)))
    after = ProjectSnapshot(
        side="after",
        languages=["Python"],
        entities={record.key: record},
        call_resolution={"Python": RESOLVED},
    )

    outcome = find_unused_parameters(after, {record.key}, trust=TRUSTED)

    assert after.method_declarations is None
    assert outcome.findings == []
    assert len(outcome.unavailable) == 1


# --- requirement 1.8, the call-resolution floor -----------------------------------


def test_a_language_below_the_resolution_floor_reports_nothing_and_says_why() -> None:
    """The floor that bounds what the Gate knows about who calls whom."""
    record = routine("app.Service.run", facts(unused=("verbose",)))
    after = snapshot((record,), resolution=BELOW_FLOOR)

    outcome = find_unused_parameters(after, {record.key}, trust=TRUSTED)

    assert outcome.findings == []
    assert len(outcome.unavailable) == 1
    assert "Python" in outcome.unavailable[0]
    assert "19%" in outcome.unavailable[0]
    assert "call-resolution floor of 75%" in outcome.unavailable[0]


def test_the_message_names_the_floor_that_stopped_the_rule() -> None:
    """Two floors, so a run that reports nothing has to say which one refused it."""
    record = routine("app.Service.run", facts(unused=("verbose",)))
    after = snapshot((record,), resolution=BELOW_FLOOR)

    resolution = find_unused_parameters(after, {record.key}, trust=TRUSTED).unavailable[0]
    accuracy = find_unused_parameters(snapshot((record,)), {record.key}, trust=UNREAD).unavailable[
        0
    ]

    assert "call-resolution floor" in resolution
    assert "accuracy floor" not in resolution
    assert "accuracy floor" in accuracy
    assert "call-resolution floor" not in accuracy


def test_an_operator_can_lower_the_resolution_floor_onto_their_own_measurement() -> None:
    """Requirement 1.8 asks for *configurable* floors; a constant would leave it unsatisfied."""
    record = routine("app.Service.run", facts(unused=("verbose",)))
    after = snapshot((record,), resolution=BELOW_FLOOR)

    outcome = find_unused_parameters(
        after, {record.key}, trust=Trust(accuracy=0.95, resolution_floor=0.1)
    )

    assert len(outcome.findings) == 1
    assert outcome.unavailable == ()


def test_a_language_with_no_resolution_figure_at_all_is_below_the_floor() -> None:
    """An absent measurement and a good one must not read the same (the C++ record here)."""
    record = routine("app.Service.run", facts(unused=("verbose",)), language="C++")

    outcome = find_unused_parameters(snapshot((record,)), {record.key}, trust=TRUSTED)

    assert outcome.findings == []
    assert "C++" in outcome.unavailable[0]


def test_a_language_whose_call_sites_were_never_counted_is_below_the_floor() -> None:
    """A resolution record of three zeros has no share to compare, which is not a pass."""
    record = routine("app.Service.run", facts(unused=("verbose",)))
    after = snapshot((record,), resolution=CallResolution())

    outcome = find_unused_parameters(after, {record.key}, trust=TRUSTED)

    assert outcome.findings == []
    assert outcome.unavailable != ()


def test_the_resolution_floor_is_per_language_and_the_measured_one_still_reports() -> None:
    """Requirement 1.8 gates a language, not a run: two substrates, one answer each."""
    python = routine("app.Service.run", facts(unused=("verbose",)))
    native = routine("app.Native.run", facts(unused=("count",)), language="C++")
    after = ProjectSnapshot(
        side="after",
        languages=["Python", "C++"],
        entities={python.key: python, native.key: native},
        call_resolution={"Python": RESOLVED, "C++": WELL_BELOW},
        method_declarations={},
    )

    outcome = find_unused_parameters(after, {python.key, native.key}, trust=TRUSTED)

    assert [finding.details["parameter"] for finding in outcome.findings] == ["verbose"]
    assert len(outcome.unavailable) == 1
    assert "C++" in outcome.unavailable[0]


def test_one_message_per_language_however_many_routines_it_holds() -> None:
    """Requirement 1.8 says once per run, not once per entity."""
    first = routine("app.Service.run", facts(unused=("verbose",)))
    second = routine("app.Service.other", facts(unused=("count",)))
    after = snapshot((first, second), resolution=BELOW_FLOOR)

    outcome = find_unused_parameters(after, {first.key, second.key}, trust=TRUSTED)

    assert outcome.unavailable == (outcome.unavailable[0],)


# --- requirement 1.8, the accuracy floor ------------------------------------------


def test_a_run_below_the_accuracy_floor_reports_nothing_and_says_why() -> None:
    """The floor that bounds whether a file was read at all, which is the sixteen-in-sixteen
    failure: a use site inside a region the analysis errored on is invisible however well the
    call graph resolved."""
    record = routine("app.Service.run", facts(unused=("verbose",)))

    outcome = find_unused_parameters(snapshot((record,)), {record.key}, trust=UNREAD)

    assert outcome.findings == []
    assert len(outcome.unavailable) == 1
    assert "19%" in outcome.unavailable[0]
    assert "accuracy floor of 75%" in outcome.unavailable[0]


def test_a_run_that_measured_no_accuracy_reports_nothing_and_says_so() -> None:
    """An absent figure is not a good one, and it is the default a caller gets."""
    record = routine("app.Service.run", facts(unused=("verbose",)))

    outcome = find_unused_parameters(snapshot((record,)), {record.key})

    assert Trust().accuracy is None
    assert outcome.findings == []
    assert "measured no analysis accuracy" in outcome.unavailable[0]


def test_an_operator_can_lower_the_accuracy_floor_onto_their_own_measurement() -> None:
    """The second half of requirement 1.8's *configurable*."""
    record = routine("app.Service.run", facts(unused=("verbose",)))

    outcome = find_unused_parameters(
        snapshot((record,)), {record.key}, trust=Trust(accuracy=0.19, accuracy_floor=0.1)
    )

    assert len(outcome.findings) == 1
    assert outcome.unavailable == ()


def test_the_accuracy_floor_speaks_once_for_the_whole_run() -> None:
    """It is measured per side, not per language, so two languages get one sentence."""
    python = routine("app.Service.run", facts(unused=("verbose",)))
    native = routine("app.Native.run", facts(unused=("count",)), language="C++")
    after = ProjectSnapshot(
        side="after",
        languages=["Python", "C++"],
        entities={python.key: python, native.key: native},
        call_resolution={"Python": RESOLVED, "C++": RESOLVED},
        method_declarations={},
    )

    outcome = find_unused_parameters(after, {python.key, native.key}, trust=UNREAD)

    assert outcome.findings == []
    assert len(outcome.unavailable) == 1


def test_the_class_rule_takes_the_accuracy_floor_too() -> None:
    """Requirement 1.8 is about dead-code findings, not about one scope."""
    record = klass("app.Report", LeanFacts(referenced=False))

    outcome = find_unused_classes(snapshot((record,)), {record.key}, trust=UNREAD)

    assert outcome.findings == []
    assert CLASS_RULE in outcome.unavailable[0]
    assert "accuracy floor" in outcome.unavailable[0]


def test_the_variable_rule_takes_the_accuracy_floor_too() -> None:
    """The rule whose measured false-positive rate is the reason this floor exists."""
    after = snapshot((file_record(),), definitions=(binding("TIMEOUT", False),))

    outcome = find_unused_variables(after, {PATH}, trust=UNREAD)

    assert outcome.findings == []
    assert VARIABLE_RULE in outcome.unavailable[0]
    assert "accuracy floor" in outcome.unavailable[0]


def test_the_floors_default_to_the_documented_placeholders() -> None:
    """What this suite can prove about the defaults: which numbers the rules actually use.

    No test here asserts that either placeholder is calibrated, because no measurement
    calibrates it -- requirement 1.10's two-repository count is what will.
    """
    assert Trust().resolution_floor == DEFAULT_RESOLUTION_FLOOR
    assert Trust().accuracy_floor == DEFAULT_ACCURACY_FLOOR


# --- the floors are asked before the facts are believed ---------------------------


def test_a_below_floor_run_says_so_even_when_every_parameter_is_used() -> None:
    """Requirement 1.8's message is unconditional, and this is where a guard order shows.

    Every affected routine here is alive: no unused parameter, nothing to report whatever the
    floors said. A rule that consulted the facts before the gate would fall silent about an
    analysis it never judged, and this is the commit on which that happens.
    """
    record = routine("app.Service.run", facts())
    after = snapshot((record,), resolution=BELOW_FLOOR)

    outcome = find_unused_parameters(after, {record.key}, trust=TRUSTED)

    assert outcome.findings == []
    assert len(outcome.unavailable) == 1
    assert "Python" in outcome.unavailable[0]


def test_a_below_floor_run_says_so_even_when_every_class_is_referenced() -> None:
    """The same order, in the rule whose fact is a single flag."""
    record = klass("app.Report", LeanFacts(referenced=True))
    after = snapshot((record,), resolution=BELOW_FLOOR)

    outcome = find_unused_classes(after, {record.key}, trust=TRUSTED)

    assert outcome.findings == []
    assert len(outcome.unavailable) == 1
    assert "Python" in outcome.unavailable[0]


def test_a_below_floor_run_says_so_even_when_every_binding_is_read() -> None:
    """And in the rule that reads its facts off the definitions walk."""
    after = snapshot(
        (file_record(),), resolution=BELOW_FLOOR, definitions=(binding("TIMEOUT", True),)
    )

    outcome = find_unused_variables(after, {PATH}, trust=TRUSTED)

    assert outcome.findings == []
    assert len(outcome.unavailable) == 1
    assert "Python" in outcome.unavailable[0]


# --- unused classes ---------------------------------------------------------------


def test_a_class_nothing_references_is_reported() -> None:
    """Requirement 1.1, the class half."""
    record = klass("app.Report", LeanFacts(referenced=False, derived=[], referrers=0))

    (finding,) = find_unused_classes(snapshot((record,)), {record.key}, trust=TRUSTED).findings

    assert finding.rule == CLASS_RULE
    assert finding.scope == "class"
    assert finding.path == PATH
    assert finding.line == 4
    assert finding.details["longname"] == "app.Report"
    assert "app.Report" in finding.message


def test_a_referenced_class_is_not_reported() -> None:
    """The measurement the rule exists to read."""
    record = klass("app.Report", LeanFacts(referenced=True))

    assert find_unused_classes(snapshot((record,)), {record.key}, trust=TRUSTED).findings == []


def test_an_unreferenced_class_the_change_did_not_touch_is_not_reported() -> None:
    """Requirement 1.1 reports against the change: this is a gate, not an audit."""
    touched = klass("app.Service", LeanFacts(referenced=True))
    unaffected = klass("app.Report", LeanFacts(referenced=False), path=OTHER)
    after = snapshot((touched, unaffected))

    assert find_unused_classes(after, {touched.key}, trust=TRUSTED).findings == []


def test_the_shipped_class_ignore_list_excuses_an_exception() -> None:
    """Requirement 1.5: an exception class is raised and caught, never referenced."""
    record = klass("app.ConfigError", LeanFacts(referenced=False))
    outcome = find_unused_classes(
        snapshot((record,)), {record.key}, ignore=DEFAULT_LEAN_CLASS_IGNORE, trust=TRUSTED
    )

    assert outcome.findings == []


def test_the_shipped_class_ignore_list_excuses_a_collected_test_class() -> None:
    """pytest collects ``Test``-prefixed classes by name; there is no reference to find."""
    record = klass("tests.app.TestService", LeanFacts(referenced=False))
    outcome = find_unused_classes(
        snapshot((record,)), {record.key}, ignore=DEFAULT_LEAN_CLASS_IGNORE, trust=TRUSTED
    )

    assert outcome.findings == []


def test_a_class_with_no_facts_makes_the_class_rule_unavailable() -> None:
    """Requirement 1.6, per rule."""
    record = klass("app.Report", None)

    outcome = find_unused_classes(snapshot((record,)), {record.key}, trust=TRUSTED)

    assert outcome.findings == []
    assert CLASS_RULE in outcome.unavailable[0]


def test_an_unmeasured_reference_flag_makes_the_class_rule_unavailable() -> None:
    """``referenced is None`` is the state a project full of dead code comes from."""
    record = klass("app.Report", LeanFacts(derived=[], referrers=0))

    outcome = find_unused_classes(snapshot((record,)), {record.key}, trust=TRUSTED)

    assert outcome.findings == []
    assert outcome.unavailable != ()


def test_the_class_rule_takes_the_resolution_floor_too() -> None:
    """Requirement 1.8 is about dead-code findings, not about routines."""
    record = klass("app.Report", LeanFacts(referenced=False))
    after = snapshot((record,), resolution=BELOW_FLOOR)

    outcome = find_unused_classes(after, {record.key}, trust=TRUSTED)

    assert outcome.findings == []
    assert CLASS_RULE in outcome.unavailable[0]
    assert "19%" in outcome.unavailable[0]


def test_a_deleted_class_cannot_be_reported() -> None:
    """Requirement 1.7 again, for the scope that has its own walk."""
    gone = EntityKey(scope="class", path=PATH, longname="app.Gone", parameters=None)

    assert find_unused_classes(snapshot(), {gone}, trust=TRUSTED) == ([], ())


def test_an_error_severity_makes_the_class_finding_blocking() -> None:
    """The family's shared contract."""
    record = klass("app.Report", LeanFacts(referenced=False))

    outcome = find_unused_classes(snapshot((record,)), {record.key}, "error", trust=TRUSTED)

    assert outcome.findings[0].blocking is True


# --- unused module variables ------------------------------------------------------


def test_a_module_binding_nothing_reads_is_reported_where_it_is_bound() -> None:
    """Requirement 1.1, the module-variable half."""
    after = snapshot((file_record(),), definitions=(binding("TIMEOUT", False),))

    (finding,) = find_unused_variables(after, {PATH}, trust=TRUSTED).findings

    assert finding.rule == VARIABLE_RULE
    assert finding.scope == "file"
    assert finding.path == PATH
    assert finding.line == 9
    assert finding.details["definition"] == "TIMEOUT"
    assert "TIMEOUT" in finding.message


def test_a_module_binding_something_reads_is_not_reported() -> None:
    """The measurement the rule exists to read, on its own worst case."""
    after = snapshot((file_record(),), definitions=(binding("TIMEOUT", True),))

    assert find_unused_variables(after, {PATH}, trust=TRUSTED).findings == []


def test_a_binding_in_a_file_the_change_did_not_touch_is_not_reported() -> None:
    """The affected set is the file set for this rule."""
    after = snapshot(
        (file_record(), file_record(OTHER)),
        definitions=(binding("TIMEOUT", False, path=OTHER),),
    )

    assert find_unused_variables(after, {PATH}, trust=TRUSTED).findings == []


def test_the_shipped_variable_ignore_list_excuses_the_runtime_reads() -> None:
    """Requirement 1.5: ``__all__`` and a module logger are read by machinery, not by code."""
    after = snapshot(
        (file_record(),),
        definitions=(binding("__all__", False), binding("log", False)),
    )

    found = find_unused_variables(
        after, {PATH}, ignore=DEFAULT_LEAN_VARIABLE_IGNORE, trust=TRUSTED
    ).findings

    assert found == []


def test_an_unmeasured_binding_makes_the_variable_rule_unavailable() -> None:
    """Requirement 1.6, and the rule with the measured 100% false-positive rate."""
    after = snapshot((file_record(),), definitions=(binding("TIMEOUT", None),))

    outcome = find_unused_variables(after, {PATH}, trust=TRUSTED)

    assert outcome.findings == []
    assert VARIABLE_RULE in outcome.unavailable[0]


def test_one_unmeasured_binding_silences_the_whole_variable_rule() -> None:
    """Any binding, not all of them, with a genuine finding beside it to show the difference."""
    after = snapshot(
        (file_record(),),
        definitions=(binding("TIMEOUT", False), binding("RETRIES", None)),
    )

    outcome = find_unused_variables(after, {PATH}, trust=TRUSTED)

    assert outcome.findings == []
    assert len(outcome.unavailable) == 1


def test_the_variable_rule_takes_the_resolution_floor_from_its_file() -> None:
    """Requirement 1.8. The binding has no language of its own; its file has one."""
    after = snapshot(
        (file_record(),), resolution=BELOW_FLOOR, definitions=(binding("TIMEOUT", False),)
    )

    outcome = find_unused_variables(after, {PATH}, trust=TRUSTED)

    assert outcome.findings == []
    assert "Python" in outcome.unavailable[0]
    assert "19%" in outcome.unavailable[0]


def test_a_binding_whose_file_was_analysed_as_another_language_is_gated_by_that_one() -> None:
    """The file record decides, so a mixed project cannot borrow the good substrate."""
    after = ProjectSnapshot(
        side="after",
        languages=["Python", "C++"],
        entities={file_record(PATH, "C++").key: file_record(PATH, "C++")},
        call_resolution={"Python": RESOLVED, "C++": BELOW_FLOOR},
        definitions=[binding("TIMEOUT", False)],
    )

    outcome = find_unused_variables(after, {PATH}, trust=TRUSTED)

    assert outcome.findings == []
    assert "C++" in outcome.unavailable[0]


def test_the_file_record_and_not_a_routine_in_it_decides_the_bindings_language() -> None:
    """A file holding entities of a second language is gated by the language it was read as.

    The file record is the one answer that covers the whole file, bindings included; a routine
    inside it answers for itself alone, and letting it decide would gate a Python constant on
    the C++ figure because a header happened to be included.
    """
    native = routine("app.Service.run", facts(), language="C++")
    after = ProjectSnapshot(
        side="after",
        languages=["Python", "C++"],
        entities={file_record().key: file_record(), native.key: native},
        call_resolution={"Python": RESOLVED, "C++": BELOW_FLOOR},
        definitions=[binding("TIMEOUT", False)],
    )

    outcome = find_unused_variables(after, {PATH}, trust=TRUSTED)

    assert len(outcome.findings) == 1
    assert outcome.unavailable == ()


def test_a_binding_in_an_unrecorded_file_falls_back_to_the_one_language_analysed() -> None:
    """One analysed language is not a guess; it is the only answer the snapshot holds."""
    after = snapshot(definitions=(binding("TIMEOUT", False),))

    assert len(find_unused_variables(after, {PATH}, trust=TRUSTED).findings) == 1


def test_a_binding_in_an_unrecorded_file_of_a_mixed_project_is_not_judged() -> None:
    """With two languages analysed and no record, the substrate is unknown and so is the rule."""
    after = ProjectSnapshot(
        side="after",
        languages=["Python", "C++"],
        call_resolution={"Python": RESOLVED, "C++": RESOLVED},
        definitions=[binding("TIMEOUT", False)],
    )

    outcome = find_unused_variables(after, {PATH}, trust=TRUSTED)

    assert outcome.findings == []
    assert outcome.unavailable != ()


def test_a_file_with_no_binding_left_is_nothing_said_at_all() -> None:
    """A deleted binding is absent from the after side, and absence is not a finding (1.7)."""
    after = ProjectSnapshot(side="after", languages=["Python"])

    assert find_unused_variables(after, {PATH}, trust=UNREAD) == ([], ())


def test_an_error_severity_makes_the_variable_finding_blocking() -> None:
    """The family's shared contract."""
    after = snapshot((file_record(),), definitions=(binding("TIMEOUT", False),))

    outcome = find_unused_variables(after, {PATH}, "error", trust=TRUSTED)

    assert outcome.findings[0].blocking is True


def test_variable_findings_are_ordered_by_path_then_line() -> None:
    """Both halves of the sort key, arranged so each one changes the answer.

    ``report.py`` sorts first by path but holds the *highest* line number, and inside
    ``service.py`` the line order and the name order are opposite -- so a sort that lost the
    path would lead with a ``service.py`` binding, and one that lost the line would put ALPHA
    before BETA.
    """
    after = snapshot(
        (file_record(), file_record(OTHER)),
        definitions=(
            binding("ALPHA", False, line=20),
            binding("ZETA", False, path=OTHER, line=40),
            binding("BETA", False, line=5),
        ),
    )

    found = find_unused_variables(after, {PATH, OTHER}, trust=TRUSTED).findings

    assert [(finding.path, finding.line) for finding in found] == [
        (OTHER, 40),
        (PATH, 5),
        (PATH, 20),
    ]


# --- the shipped stance -----------------------------------------------------------


def test_the_three_rules_ship_off() -> None:
    """Requirement 1.5: the family is silent until an operator names a severity."""
    rules = LeanRules()

    assert (rules.unused_parameters, rules.unused_classes, rules.unused_variables) == (
        None,
        None,
        None,
    )


def test_each_rule_warns_rather_than_blocks_when_it_is_enabled() -> None:
    """Requirement 1.5's second half, read off the three functions' own defaults."""
    record = routine("app.Service.run", facts(unused=("verbose",)))
    a_class = klass("app.Report", LeanFacts(referenced=False))
    after = snapshot((record, a_class, file_record()), definitions=(binding("T", False),))

    severities = {
        find_unused_parameters(after, {record.key}, trust=TRUSTED).findings[0].severity,
        find_unused_classes(after, {a_class.key}, trust=TRUSTED).findings[0].severity,
        find_unused_variables(after, {PATH}, trust=TRUSTED).findings[0].severity,
    }

    assert severities == {"warning"}
