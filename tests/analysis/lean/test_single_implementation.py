"""The single-implementation rule: a base class kept for an implementation that never came.

Requirements 3.1 through 3.4. The rule's whole value is the conjunction requirement 3.3 spells
out -- **exactly one** derived class **and** no other project referrer -- so most of what is
asserted here is silence: no derived class is the dead-code rule's question, two derived
classes are a hierarchy doing its job, and one derived class beside any other user is an
abstraction with more than one reason to exist.

**Requirement 3.2 is why this rule cannot walk the affected set alone.** The commit that adds
the first and only implementation touches the *derived* class, not the base, and that is the
commit worth telling: the base was not a mistake when it was written, it became one when the
second implementation failed to arrive. So the rule walks every class the snapshot records and
reports a base whose own key **or** whose single derived class is in the change.

**No floor, and that is a decision rather than an omission.** Requirement 2.6 gives the
pass-through rule requirement 1.8's floors in as many words, and requirement 3 names neither
floor for this rule -- the same author scoping by enablement where they meant it. The
measurement that would justify extending them here has not been taken: ``referrers`` is a
count over use, type and inheritance references rather than over the call graph, so the
call-resolution figure is not the quantity that bounds it. Task 6.4 owns that measurement, and
the concern is recorded in task 4.2's report rather than answered by a guard nothing measured.
"""

from __future__ import annotations

from typing import Final

from scitools_hook.analysis.lean.layering import (
    SINGLE_IMPLEMENTATION_RULE,
    find_single_implementations,
)
from scitools_hook.config.models import DEFAULT_LEAN_IMPLEMENTATION_IGNORE, LeanRules
from scitools_hook.models.snapshot import (
    EntityKey,
    EntityRecord,
    EntityRef,
    LeanFacts,
    ProjectSnapshot,
)

PATH: Final = "src/app/store.py"
OTHER: Final = "src/app/sql.py"

BASE: Final = "app.store.Store"
DERIVED: Final = "app.sql.SqlStore"
SECOND: Final = "app.memory.MemoryStore"


def facts(
    derived: tuple[str, ...] | None = (DERIVED,),
    referrers: int | None = 0,
    referenced: bool | None = True,
) -> LeanFacts:
    """The two facts requirement 3.1 reads about a class, both of them measured."""
    return LeanFacts(
        referenced=referenced,
        derived=None if derived is None else list(derived),
        referrers=referrers,
    )


MEASURED: Final = facts()
"""A base with one derived class and no other referrer: the shape the rule reports.

A named default rather than a call in the signature, so that ``lean=None`` keeps its own
meaning -- **the worker was not asked** -- in a helper whose ordinary answer is a full set of
facts. A helper defaulting through ``None`` cannot express the case the unavailable tests are
about, and both of them passed against a rule that never looked.
"""


def klass(
    longname: str = BASE,
    lean: LeanFacts | None = MEASURED,
    path: str = PATH,
    line: int = 4,
) -> EntityRecord:
    """One recorded class carrying the facts under test, or none at all."""
    key = EntityKey(scope="class", path=path, longname=longname, parameters=None)
    return EntityRecord(
        ref=EntityRef(key=key, kind="Class", name=longname.rpartition(".")[2], line=line),
        language="Python",
        lean=lean,
    )


def implementation(longname: str = DERIVED, path: str = OTHER) -> EntityRecord:
    """The one derived class, which carries no derived classes of its own."""
    return klass(longname=longname, lean=facts(derived=(), referrers=1), path=path)


def snapshot(*records: EntityRecord) -> ProjectSnapshot:
    """An after snapshot carrying only what the single-implementation rule reads."""
    return ProjectSnapshot(
        side="after",
        languages=["Python"],
        entities={record.key: record for record in records},
    )


def keys(*records: EntityRecord) -> set[EntityKey]:
    """The affected set, spelled from the records a test built."""
    return {record.key for record in records}


# --- the finding ------------------------------------------------------------------


def test_one_derived_class_and_no_other_referrer_is_reported_against_the_base() -> None:
    """Requirement 3.1: the base is the finding and the derived class is named in it."""
    base = klass()
    derived = implementation()

    outcome = find_single_implementations(snapshot(base, derived), keys(base))

    (finding,) = outcome.findings
    assert outcome.unavailable == ()
    assert finding.kind == "structural"
    assert finding.rule == SINGLE_IMPLEMENTATION_RULE
    assert finding.rule == "structure.single_implementation"
    assert finding.scope == "class"
    assert finding.metric is None
    assert finding.path == PATH
    assert finding.line == 4
    assert finding.value == 1
    assert finding.before is None
    assert finding.limit is None
    assert finding.limit_source == "rule"
    assert finding.severity == "warning"
    assert finding.blocking is False
    assert finding.preexisting is False
    assert finding.hint == ""
    assert finding.details == {"derived_class": DERIVED, "longname": BASE}
    assert BASE in finding.message
    assert DERIVED in finding.message


def test_the_base_is_reported_on_the_commit_that_adds_its_only_derived_class() -> None:
    """Requirement 3.2, and the reason the rule cannot walk the affected set alone.

    Only the derived class is in the change; the base sits in a file this commit never
    touched. A rule reading the affected records alone answers nothing here, and this is the
    commit on which the finding is worth having.
    """
    base = klass()
    derived = implementation()

    outcome = find_single_implementations(snapshot(base, derived), keys(derived))

    (finding,) = outcome.findings
    assert finding.path == PATH
    assert finding.details["derived_class"] == DERIVED


def test_an_error_severity_makes_the_finding_blocking() -> None:
    """Severity travels from the configured rule, as every structural rule's does (req 3.4)."""
    base = klass()

    outcome = find_single_implementations(
        snapshot(base, implementation()), keys(base), severity="error"
    )

    (finding,) = outcome.findings
    assert finding.severity == "error"
    assert finding.blocking is True


def test_findings_are_reported_in_path_order() -> None:
    """Two single-implementation bases in one change, in the order a reader meets them.

    **The two are built so that path order and long-name order disagree**, and that is the
    whole point of the fixture: ``app.alpha.Alpha`` sorts first by name and lives in the
    *later* file. The pair this test used to carry -- ``app.codec.Codec`` in
    ``src/app/codec.py`` beside ``app.store.Store`` in ``src/app/store.py`` -- agreed on both
    keys, so it passed whichever of the two the walk sorted on and proved nothing. The
    records are handed to the snapshot in the opposite order for the same reason.
    """
    early = klass(
        longname="app.zeta.Zeta",
        path="src/app/aaa.py",
        lean=facts(derived=("app.zeta.ZetaImpl",)),
    )
    late = klass(
        longname="app.alpha.Alpha",
        path="src/app/zzz.py",
        lean=facts(derived=("app.alpha.AlphaImpl",)),
    )

    outcome = find_single_implementations(snapshot(late, early), keys(early, late))

    assert [finding.path for finding in outcome.findings] == ["src/app/aaa.py", "src/app/zzz.py"]
    assert [finding.details["longname"] for finding in outcome.findings] == [
        "app.zeta.Zeta",
        "app.alpha.Alpha",
    ]


# --- what the rule refuses to say -------------------------------------------------


def test_a_class_with_no_derived_class_is_never_reported() -> None:
    """Requirement 3.3: nothing derives from it, so there is no implementation to fold in."""
    base = klass(lean=facts(derived=()))

    assert find_single_implementations(snapshot(base), keys(base)).findings == []


def test_a_class_with_two_derived_classes_is_a_hierarchy_doing_its_job() -> None:
    """Requirement 3.3: the second implementation is what the abstraction was for."""
    base = klass(lean=facts(derived=(DERIVED, SECOND)))

    assert find_single_implementations(snapshot(base), keys(base)).findings == []


def test_a_base_with_a_referrer_outside_its_implementation_is_not_reported() -> None:
    """Requirement 3.3: something else names it, so it is a type and not a leftover.

    ``referrers`` already excludes the class itself, its own members, its derived classes and
    their members, so any count above zero is a user the fold would break.
    """
    base = klass(lean=facts(referrers=1))

    assert find_single_implementations(snapshot(base, implementation()), keys(base)).findings == []


def test_a_class_the_change_did_not_touch_is_not_reported() -> None:
    """Neither the base nor its implementation is in the change, so neither is the finding."""
    base = klass()
    derived = implementation()
    elsewhere = klass(longname="app.other.Thing", path="src/app/other.py", lean=facts(derived=()))

    outcome = find_single_implementations(snapshot(base, derived, elsewhere), keys(elsewhere))

    assert outcome.findings == []


def test_a_change_touching_no_class_is_told_nothing() -> None:
    """A rule with nothing to judge says nothing, and must not complain about the walk.

    The discriminating case for the empty guard: the snapshot carries a class with no facts
    at all, which is exactly what the unavailable message is for.
    """
    outcome = find_single_implementations(snapshot(klass(lean=None)), set())

    assert outcome.findings == []
    assert outcome.unavailable == ()


def test_an_affected_routine_does_not_wake_the_class_rule() -> None:
    """A change touching only routines leaves this rule with nothing to judge -- and silent.

    **The unmeasured class beside the measured pair is what makes this test discriminating.**
    The scope filter on ``touched`` is the only thing that keeps a routine's long name out of
    the affected class set, and with every class in the fixture measured, a rule that dropped
    the filter would walk the whole snapshot, find nothing to report and answer ``[]`` too --
    the same answer for the opposite reason. ``app.other.Thing`` carries no facts, so the
    walk this rule must not start is the walk that reports itself unavailable, and the second
    assertion is the one that sees the difference.
    """
    routine = EntityKey(scope="routine", path=PATH, longname="app.store.save", parameters="")
    unmeasured = klass(longname="app.other.Thing", path="src/app/other.py", lean=None)

    outcome = find_single_implementations(
        snapshot(klass(), implementation(), unmeasured), {routine}
    )

    assert outcome.findings == []
    assert outcome.unavailable == ()


def test_a_routine_record_in_the_snapshot_is_not_a_class_this_rule_walks() -> None:
    """The walk is over the recorded **classes**, and a routine record is not one of them.

    ``_base_facts`` reads ``derived`` and ``referrers`` off every record it walks, and a
    routine carries neither -- so a walk that dropped the scope filter would answer
    "unavailable" for any snapshot holding a routine, which is every real one. Every other
    fixture in this file is classes only, so nothing else here can see that.
    """
    base = klass()
    save = EntityRecord(
        ref=EntityRef(
            key=EntityKey(scope="routine", path=PATH, longname="app.store.save", parameters=""),
            kind="Method",
            name="save",
            line=9,
        ),
        language="Python",
        lean=LeanFacts(callers=1, callees=1),
    )

    outcome = find_single_implementations(snapshot(base, implementation(), save), keys(base))

    assert len(outcome.findings) == 1
    assert outcome.unavailable == ()


def test_an_affected_class_the_snapshot_has_no_record_of_reports_nothing() -> None:
    """A class outside the analysis root has no facts to reason from and no base to name."""
    stranger = EntityKey(scope="class", path="vendor/x.py", longname="vendor.X", parameters=None)

    outcome = find_single_implementations(snapshot(klass(), implementation()), {stranger})

    assert outcome.findings == []
    assert outcome.unavailable == ()


# --- the ignore list --------------------------------------------------------------


def test_the_shipped_list_excuses_an_exception_base() -> None:
    """Requirement 3.3: ``except AppError`` is what the base is for, with one subclass or ten."""
    base = klass(longname="app.errors.AppError", lean=facts(derived=("app.errors.NotFound",)))

    outcome = find_single_implementations(
        snapshot(base), keys(base), ignore=list(DEFAULT_LEAN_IMPLEMENTATION_IGNORE)
    )

    assert outcome.findings == []


def test_the_shipped_list_excuses_the_other_spelling_of_the_same_idiom() -> None:
    """``Exception`` as well as ``Error``, because both name the same base in the wild."""
    base = klass(longname="app.errors.StoreException", lean=facts(derived=("app.errors.Gone",)))

    outcome = find_single_implementations(
        snapshot(base), keys(base), ignore=list(DEFAULT_LEAN_IMPLEMENTATION_IGNORE)
    )

    assert outcome.findings == []


def test_an_operators_own_pattern_excuses_a_class() -> None:
    """The list is regular expressions over class long names, matched anywhere in the name."""
    base = klass()

    outcome = find_single_implementations(snapshot(base), keys(base), ignore=[r"\.Store$"])

    assert outcome.findings == []


# --- the three states -------------------------------------------------------------


def test_a_snapshot_without_the_reference_walk_reports_the_rule_unavailable() -> None:
    """An analysis cache recorded before the rule was switched on judges nothing."""
    base = klass(lean=None)

    outcome = find_single_implementations(snapshot(base), keys(base))

    assert outcome.findings == []
    (message,) = outcome.unavailable
    assert message.startswith(SINGLE_IMPLEMENTATION_RULE)
    assert "db rebuild" in message
    # As in the pass-through rule's test: the shared sentence says "judged" here, never
    # "judged unused", which is the dead-code rules' verdict about a name.
    assert "so nothing was judged;" in message


def test_an_unmeasured_derived_list_is_not_a_class_with_no_subclasses() -> None:
    """The three-state discipline, one fact at a time: ``derived`` absent stops the rule."""
    base = klass(lean=facts(derived=None))

    outcome = find_single_implementations(snapshot(base), keys(base))

    assert outcome.findings == []
    assert len(outcome.unavailable) == 1


def test_an_unmeasured_referrer_count_is_not_a_count_of_none() -> None:
    """Read as zero it would report every base class with a single subclass and a user."""
    base = klass(lean=facts(referrers=None))

    outcome = find_single_implementations(snapshot(base), keys(base))

    assert outcome.findings == []
    assert len(outcome.unavailable) == 1


def test_an_unmeasured_class_anywhere_stops_the_rule_even_when_the_change_is_measured() -> None:
    """The walk is project-wide, so a class outside the change is one this rule reads.

    The discriminating record: the affected class carries every fact, and the unmeasured one
    sits in a file the change never touched. A rule checking only the affected records answers
    this run with a finding while the base it could not see went unexamined.
    """
    derived = implementation()
    unmeasured = klass(longname="app.other.Thing", path="src/app/other.py", lean=None)

    outcome = find_single_implementations(snapshot(klass(), derived, unmeasured), keys(derived))

    assert outcome.findings == []
    assert len(outcome.unavailable) == 1


# --- the shipped stance -----------------------------------------------------------


def test_the_rule_ships_off() -> None:
    """Requirement 3.4: the whole family is silent until an operator names a severity."""
    assert LeanRules().single_implementation is None


def test_the_rule_defaults_to_a_warning_when_it_is_called() -> None:
    """Requirement 3.4's second half: enabled, it warns rather than blocks."""
    base = klass()

    (finding,) = find_single_implementations(snapshot(base, implementation()), keys(base)).findings

    assert finding.severity == "warning"
