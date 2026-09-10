"""The reference facts ``worker_lean`` measures (requirements 1.1-1.4, 1.9, 2.1, 3.1).

Five rules read these documents and each fails differently, which is what the tests below are
arranged around:

* **Unused parameters** (1.2) must not report a parameter anything in the project touches.
  Two of the four kinds that count as touching it were found missing by measurement rather
  than by reading -- a parameter used only as ``fn()`` carries a ``Callby`` and nothing else
  -- so every kind in ``PARAMETER_USE`` is exercised here, and a kind dropped from that set
  turns a working callback parameter into dead code an agent deletes.
* **Pass-through** (2.1) counts callers and callees, and an end that is not a project routine
  moves either count: a call Understand could not resolve still lands on an entity in a
  project file, and counting it hands a routine a callee whose deletion breaks a call that
  binds at runtime. The contract project is not what proves this -- its own candidates are
  disqualified by a statement each, on purpose, so that the fixture holds whichever way an
  implementation reads "callee" -- so the cases below are written against the shapes rather
  than against it.
* Both rules exclude an **override** (1.4, 2.3), and the worker only says whether the routine
  has one; the exclusion itself is the rule's.
* **Dead classes and dead module variables** (1.1) fail in opposite directions, and one of
  each was found by measurement rather than by reading. A base class whose only user is its
  subclass must not read as dead, or ``unused_class`` and ``single_implementation`` report one
  class twice with contradictory advice -- so the inheritance kinds of *both* fixture
  languages are in the reference set, and there is a case per member of it. A variable's own
  defining assignment is a ``Set`` reference to it, so a set that counted writes would answer
  "used" for every variable in every project and the rule would report nothing, ever.
* **Single implementation** (3.1) reads the derived classes and the referrers, which are two
  answers with two different project tests: a name a finding prints has to belong to a file
  the project owns, while "does anything use this" is decided by where the reference sits.
* **The interface-method exclusion** (1.9) is a project-wide tally rather than a fact about
  any one class, and it is the only thing that sees an interface where structural typing
  leaves no inheritance edge to detect one by.

**The receiver is reported unused here on purpose.** ``self`` has no reference in most
bodies, and this file measures rather than judges: ``DEFAULT_LEAN_PARAMETER_IGNORE`` is what
excuses it, one layer up. A worker that hid it would put the policy in the one process that
cannot be unit-tested without a licence.
"""

from __future__ import annotations

from typing import Any, Final

import pytest
from api_fakes import FakeEnt, FakeRef
from worker_projects import (
    a_class,
    a_context,
    a_file,
    a_parameter,
    a_routine,
    a_variable,
)

from scitools_hook.config.metric_names import SCOPE_KINDS
from scitools_hook.understand import worker, worker_lean

# --- the fixtures ---------------------------------------------------------------------


def facts(ent: FakeEnt) -> dict[str, Any]:
    """The document under test."""
    return worker_lean.routine_facts(ent, a_context())


def a_call(target: FakeEnt, site: FakeEnt) -> FakeRef:
    """A call reference from a body written in ``site`` to ``target``."""
    return FakeRef(target, None, "python Call", True, site)


def a_callby(caller: FakeEnt) -> FakeRef:
    """The other half of a call: ``caller`` calls this routine, from the file it lives in."""
    return FakeRef(caller, None, "python Callby", False, caller.container)


def an_unknown(name: str, kind: str, container: FakeEnt) -> FakeEnt:
    """A call target Understand could not bind to a definition, sitting in a project file."""
    return FakeEnt(qualified=name, kind_path=kind, simple=name, container=container)


# --- one case per member of every kind set this file writes out ------------------------


EVERY_REFERENCE_KIND: Final = [
    "python Callby",
    "python Useby",
    "python Setby Init",
    "python Modifyby",
    "python Typedby",
    "python Inheritby",
    "c Public Derive",
    "fortran Extendby",
    "C ObjC Implementby",
]
"""One spelling per member of ``REFERENCE_KINDS``, in a language whose kind list has it.

Every spelling is a documented one (``kinds.html`` on this install): ``Inherit (Inheritby)``
for Python and Pascal, ``Base (Derive)`` for C/C++, C# and Basic, ``Extend (Extendby)`` for
Fortran and Web, ``Implement (Implementby)`` for Basic, C#, Pascal, Rust, VHDL and Web, and
Objective-C's own ``ObjC Implement (ObjC Implementby)`` under the C section, which is where
the last spelling comes from -- there is no ``C Implementby``. A kind dropped from the set
matches nothing and answers zero rather than raising, so the only thing that can catch the
loss is a case per member.
"""

EVERY_DERIVED_KIND: Final = [
    "python Inheritby",
    "c Public Derive",
    "fortran Extendby",
    "C ObjC Implementby",
]
"""One spelling per member of ``DERIVED_KINDS``, drawn from the same documentation."""

EVERY_VARIABLE_USE: Final = ["python Useby", "python Callby", "python Typedby"]
"""One spelling per member of ``VARIABLE_USE``."""

EVERY_PARAMETER_USE: Final = [
    "python Useby",
    "python Setby Init",
    "python Modifyby",
    "python Callby",
]
"""One spelling per member of ``PARAMETER_USE``."""


def test_every_kind_of_every_set_has_a_case_above() -> None:
    """The lists above are copies of the constants, and this is what keeps them copies.

    A case per member is what catches a kind dropped from a set, and nothing catches a kind
    *added* to one -- it simply arrives with no case, and the suite stays green while the new
    member is never exercised. Counting is the cheapest binding available across a file that
    may not import: the spellings themselves are per language and cannot be derived from the
    constant, but their number can.
    """
    sizes = {
        len(worker_lean.REFERENCE_KINDS.split(", ")): len(EVERY_REFERENCE_KIND),
        len(worker_lean.DERIVED_KINDS.split(", ")): len(EVERY_DERIVED_KIND),
        len(worker_lean.VARIABLE_USE.split(", ")): len(EVERY_VARIABLE_USE),
        len(worker_lean.PARAMETER_USE.split(", ")): len(EVERY_PARAMETER_USE),
    }

    assert all(declared == covered for declared, covered in sizes.items())


# --- callers ---------------------------------------------------------------------------


def test_two_call_sites_in_one_routine_are_one_caller() -> None:
    """Callers are distinct routines: the rule asks "how many callers", not "how many calls"."""
    app = a_file("app.py")
    main = a_routine("app.main", app)
    forward = a_routine("app.forward", app)
    forward.refs_extra = [a_callby(main), a_callby(main)]

    assert facts(forward)["callers"] == 1


def test_a_caller_written_in_an_excluded_path_is_not_counted() -> None:
    """A vendored file's call is not a project caller, so it cannot save a routine from 2.1."""
    app, vendored = a_file("app.py"), a_file("vendor/six.py", lib="Standard")
    forward = a_routine("app.forward", app)
    inside, outsider = a_routine("app.main", app), a_routine("six.wraps", vendored)
    forward.refs_extra = [a_callby(inside), a_callby(outsider)]

    assert facts(forward)["callers"] == 1


def test_the_file_the_reference_is_written_in_decides_and_not_the_caller_s_own() -> None:
    """The two questions pulled apart, because every other fixture here answers them alike.

    ``Ref.file()`` is where the call site is; ``Ent.ref("definein").file()`` is where the
    calling routine was declared. They are the same file in ordinary code, and a measurement
    that asked the second while its docstring claimed the first would pass every test in this
    module. The requirements define a project reference by the file it is written in, so this
    case makes the two disagree and pins that reading: a routine declared inside the project
    whose call to ``forward`` is recorded in a vendored file is not a project caller.
    """
    app, vendored = a_file("app.py"), a_file("vendor/six.py", lib="Standard")
    forward = a_routine("app.forward", app)
    declared_inside = a_routine("app.main", app)
    forward.refs_extra = [FakeRef(declared_inside, None, "python Callby", False, vendored)]

    assert facts(forward)["callers"] == 0


def test_a_call_from_module_scope_is_not_a_caller() -> None:
    """Python records a call outside any routine against the file, and a file is not a routine.

    Safe in this shape and only in this shape: module scope is the sole call site, so the
    routine reads as having no caller at all and the pass-through rule cannot reach it. The
    test below is the shape where the same exclusion invents a finding instead.
    """
    app = a_file("app.py")
    forward = a_routine("app.forward", app)
    forward.refs_extra = [FakeRef(app, None, "python Callby", False, app)]

    assert facts(forward)["callers"] == 0


def test_a_module_scope_call_beside_a_routine_call_reads_as_a_single_caller() -> None:
    """The accepted error, pinned so that the rule that inherits it is not surprised by it.

    Two call sites, one of them at module scope: the count falls from two to one, which is
    the pass-through predicate of requirement 2.1 exactly, and a rule reading this document
    reports a routine that two places call. Nothing here can distinguish it, because the
    document carries a count and not the call sites behind it. Asserting the wrong-but-chosen
    answer is what makes a later decision to carry the callers themselves a *change* to this
    file rather than a silent improvement.
    """
    app = a_file("app.py")
    forward = a_routine("app.forward", app)
    forward.refs_extra = [
        a_callby(a_routine("app.main", app)),
        FakeRef(app, None, "python Callby", False, app),
    ]

    assert facts(forward)["callers"] == 1


def test_the_two_counts_read_opposite_directions_of_one_reference_pair() -> None:
    """A call this routine makes is not a call it receives, and the kinds must not overlap.

    ``call`` and ``callby`` are the two halves of one pair, so a selector that picked up both
    would count each end twice and hand the pass-through rule a routine with one caller and
    one callee wherever there is one of either.
    """
    app = a_file("app.py")
    forward = a_routine("app.forward", app)
    forward.refs_extra = [
        a_call(a_routine("app.canonical", app), app),
        a_callby(a_routine("app.main", app)),
    ]

    found = facts(forward)
    assert (found["callers"], found["callees"]) == (1, 1)


# --- callees and the forwarding target ---------------------------------------------------


def test_the_one_project_routine_a_body_calls_is_the_forwarding_target() -> None:
    """Two calls to the same routine are one callee, and its long name is what 2.1 reports."""
    app, target = a_file("app.py"), a_file("lean/target.py")
    canonical = a_routine("target.canonical_name", target)
    forward = a_routine("app.forward", app)
    forward.refs_extra = [a_call(canonical, app), a_call(canonical, app)]

    found = facts(forward)
    assert (found["callees"], found["forwards_to"]) == (1, "target.canonical_name")


def test_a_second_callee_leaves_no_forwarding_target() -> None:
    """A body that calls two things forwards to neither, whatever its statement count is."""
    app, target = a_file("app.py"), a_file("lean/target.py")
    forward = a_routine("app.forward", app)
    forward.refs_extra = [
        a_call(a_routine("target.canonical_name", target), app),
        a_call(a_routine("target.other_name", target), app),
    ]

    found = facts(forward)
    assert (found["callees"], found["forwards_to"]) == (2, None)


@pytest.mark.parametrize(
    "kind",
    ["python Unknown Ambiguous Attribute", "c Unresolved Function", "python Class"],
    ids=["unknown", "unresolved", "class"],
)
def test_a_call_target_that_is_not_a_project_routine_is_not_a_callee(kind: str) -> None:
    """The three shapes task 1.6 recorded a call landing on something that is not a routine.

    Understand writes an unresolved Python target as a ``python Unknown Ambiguous Attribute``
    and an unresolved C++ one as an ``Unresolved Function``, and both sit **in a project
    file** -- ``pkg/core.py``'s ``run`` calls one such target in ``pkg/inner/leaf.py`` -- so
    nothing about where the target sits refuses them. The third is a constructor call, which
    binds to the class: ``app/entry.py``'s ``entry_point`` calls ``Engine().run(...)`` and the
    sibling target is ``core.Engine``, a Class. Neither fixture routine is evidence that the
    filter is needed -- each carries a statement of its own that keeps it out of the rule
    whichever way "callee" is read -- and the reason to refuse them is the repository the
    rule will meet next, where a counted non-routine is a finding telling an agent to delete
    a call that binds.
    """
    app, leaf = a_file("app.py"), a_file("pkg/inner/leaf.py")
    forward = a_routine("app.forward", app)
    forward.refs_extra = [a_call(an_unknown("widen", kind, leaf), app)]

    found = facts(forward)
    assert (found["callees"], found["forwards_to"]) == (0, None)


def test_a_call_into_a_library_file_is_not_a_callee() -> None:
    """Understand injects the interpreter's own library into every Python project."""
    app, injected = a_file("app.py"), a_file("/opt/scitools/python/builtins.py", lib="Standard")
    forward = a_routine("app.forward", app)
    forward.refs_extra = [a_call(a_routine("builtins.abs", injected), app)]

    found = facts(forward)
    assert (found["callees"], found["forwards_to"]) == (0, None)


def test_a_call_target_with_no_definition_is_not_a_callee() -> None:
    """A target Understand records without a file -- a C++ implicit member -- has no path."""
    app = a_file("app.py")
    homeless = FakeEnt(qualified="Box::~Box", kind_path="c Function", simple="~Box")
    forward = a_routine("app.forward", app)
    forward.refs_extra = [a_call(homeless, app)]

    found = facts(forward)
    assert (found["callees"], found["forwards_to"]) == (0, None)


# --- overrides -----------------------------------------------------------------------


def test_a_routine_that_overrides_another_is_flagged() -> None:
    """Both rules must leave conformance to a signature alone (1.4, 2.3)."""
    app = a_file("app.py")
    method = a_routine("app.Runner.run", app)
    base = a_routine("base.Task.run", app)
    method.refs_extra = [FakeRef(base, None, "python Overrides", True, app)]

    assert facts(method)["overrides"] is True


def test_a_routine_that_overrides_nothing_is_not_flagged() -> None:
    """The containment reference every entity carries must not read as an override."""
    assert facts(a_routine("app.forward", a_file("app.py")))["overrides"] is False


# --- parameters ------------------------------------------------------------------------


def a_use(user: FakeEnt, kind: str) -> FakeRef:
    """One reference to a parameter, made by ``user`` in the file ``user`` is written in."""
    return FakeRef(user, None, kind, False, user.container)


def a_routine_taking(names: list[str], app: FakeEnt) -> FakeEnt:
    """A routine declaring ``names``, none of them referenced by anything."""
    routine = a_routine("app.run", app)
    routine.param_ents = [a_parameter(f"run.{name}", app) for name in names]
    return routine


@pytest.mark.parametrize("kind", EVERY_PARAMETER_USE, ids=["use", "set", "modify", "call"])
def test_any_project_reference_to_a_parameter_counts_as_use(kind: str) -> None:
    """All four kinds, because two of them were missing until something measured them.

    A parameter only ever *assigned* is used as far as this fact is concerned -- the rule is
    about a parameter nothing in the body mentions, not about one nothing reads -- and a
    parameter used only as ``fn()`` carries a ``Callby`` and no ``Useby`` at all.
    """
    app = a_file("app.py")
    routine = a_routine_taking(["total"], app)
    routine.param_ents[0].refs_extra = [a_use(routine, kind)]

    assert facts(routine)["unused_parameters"] == []


def test_a_parameter_no_project_file_mentions_is_unused() -> None:
    """The finding requirement 1.2 exists for, reported by name and in declaration order.

    Two unused names rather than one, so that the order is asserted rather than assumed: a
    finding names a parameter, and a list that came back in the order Understand happens to
    answer references in would name a different one each run.
    """
    routine = a_routine_taking(["first", "kept", "last"], a_file("app.py"))
    routine.param_ents[1].refs_extra = [a_use(routine, "python Useby")]

    assert facts(routine)["unused_parameters"] == ["first", "last"]


def test_a_parameter_used_only_from_outside_the_project_is_unused() -> None:
    """A stub's reference is not use, here for the same reason it is not for a routine."""
    app, injected = a_file("app.py"), a_file("/opt/scitools/python/builtins.py", lib="Standard")
    routine = a_routine_taking(["shadowed"], app)
    routine.param_ents[0].refs_extra = [a_use(a_routine("builtins.abs", injected), "python Useby")]

    assert facts(routine)["unused_parameters"] == ["shadowed"]


def test_the_file_the_reference_is_written_in_decides_for_a_parameter_too() -> None:
    """The first consumer of the shared helper, given the case the refactor owed it.

    Task 3.1 wrote this fact with its own walk and this reading was never pulled apart for it;
    task 3.2 folded three facts onto one helper, so a swap to the referring entity's own
    definition file would now move all three at once. A routine declared **inside** the project
    whose only reference to the parameter is written **in a vendored file** is what tells the
    two readings apart, and the parameter is unused.
    """
    app, vendored = a_file("app.py"), a_file("vendor/six.py", lib="Standard")
    routine = a_routine_taking(["shadowed"], app)
    declared_inside = a_routine("app.main", app)
    routine.param_ents[0].refs_extra = [
        FakeRef(declared_inside, None, "python Useby", False, vendored)
    ]

    assert facts(routine)["unused_parameters"] == ["shadowed"]


def test_the_receiver_is_reported_unused_by_the_worker() -> None:
    """The split the task pins: the worker measures ``self``, the rule's ignore list excuses it.

    Reported here and excluded one layer up, where the pattern list an operator can edit
    lives. The alternative is a language's receiver spelling hard-coded in the one process
    a machine without a licence cannot run.
    """
    routine = a_routine_taking(["self", "value"], a_file("app.py"))
    routine.param_ents[1].refs_extra = [a_use(routine, "python Useby")]

    assert facts(routine)["unused_parameters"] == ["self"]


def test_a_routine_with_no_parameters_answers_with_no_names() -> None:
    """The empty list is a measurement; nothing downstream may read it as "not measured"."""
    assert facts(a_routine("app.forward", a_file("app.py")))["unused_parameters"] == []


# --- the two kind strings this file must not answer differently from the gate --------------


def test_a_project_routine_is_what_the_snapshot_walk_calls_one() -> None:
    """The population these facts count over is the population the snapshot records.

    ``worker_lean`` may import nothing, so the string is written out twice and this is what
    binds the copies: a callee the walk would not have recorded is a callee the rule cannot
    resolve to a finding.
    """
    assert worker_lean.ROUTINE_KINDS == SCOPE_KINDS["routine"]


def test_a_parameter_is_what_the_worker_already_counts() -> None:
    """``CountParams`` and ``unused_parameters`` must not disagree about what a parameter is."""
    assert worker_lean.PARAMETER_KINDS == worker.PARAMETER_KIND


def test_the_file_an_entity_is_written_in_is_found_the_way_the_worker_finds_it() -> None:
    """The third copy, and the one no behavioural test here reaches.

    ``FakeEnt.ref`` honours a ``definein`` / ``declarein`` / ``end`` filter and ignores every
    other kind, so it *can* refuse a narrowed string -- but only for an entity that carries
    one half of the pair and not the other, and every entity the reference measurements are
    given below carries a definition. Narrowing ``CONTAINER_KINDS`` to either half therefore
    changes no answer any of them produces. A routine the project only declares -- a pure
    virtual, an ``extern`` -- has no ``definein`` to fall back on, so losing ``declarein``
    would silently drop every such callee, and only on a licensed machine.
    """
    assert worker_lean.CONTAINER_KINDS == worker.CONTAINER_REFS


# --- the class and variable facts (requirements 1.1, 1.3, 1.9, 3.1) ----------------------


def a_class_document(ent: FakeEnt) -> dict[str, Any]:
    """The class document under test."""
    return worker_lean.class_facts(ent, a_context())


def variable_used(ent: FakeEnt) -> bool:
    """Whether the module-level variable reads as used."""
    return worker_lean.variable_referenced(ent, a_context())


def a_reference(source: FakeEnt, kind: str, site: FakeEnt) -> FakeRef:
    """One inbound reference of ``kind``, made by ``source`` and written in ``site``."""
    return FakeRef(source, None, kind, False, site)


def a_class_map(*classes: FakeEnt) -> dict[str, tuple[FakeEnt, str]]:
    """The extractor's ``class_ents``: every project class it walked, keyed by token."""
    return {cls.longname(): (cls, "app.py") for cls in classes}


@pytest.mark.parametrize("kind", EVERY_REFERENCE_KIND)
def test_every_reference_kind_makes_a_class_referenced(kind: str) -> None:
    """Nine kinds, because a class is used in nine ways and a missing one reads as dead.

    The kinds that matter most here are the inheritance ones. A base class whose only user is
    its subclass must not read as dead, or ``unused_class`` and ``single_implementation``
    report the same class in one run with contradictory advice -- delete it, and fold it into
    its one implementation. The design records why both ``inheritby`` and ``derive`` are in the
    set rather than one of them: measured on the contract project, Python answers ``Inheritby``
    and C++ answers ``Derive``, both recorded on the base class and naming the derived one, so
    a set holding either alone calls the other language's base class dead.
    """
    app, other = a_file("app.py"), a_file("client.py")
    base = a_class("app.Base", app)
    base.refs_extra = [a_reference(a_class("client.User", other), kind, other)]

    assert a_class_document(base)["referenced"] is True


def test_a_class_used_only_in_an_annotation_is_referenced() -> None:
    """``typedby`` is the whole of some classes' use: a protocol named only in a signature.

    Called out on its own although the case above covers the kind too, because this is the
    shape requirement 1.1 fails on in real code: an annotation is not a call, not a use and
    not an instantiation, and a set built from the routine rule's ``callby, useby`` alone
    reports every type-only class in the project as dead.
    """
    app, other = a_file("app.py"), a_file("client.py")
    protocol = a_class("app.Reader", app)
    protocol.refs_extra = [a_reference(a_routine("client.load", other), "python Typedby", other)]

    assert a_class_document(protocol)["referenced"] is True


def test_a_class_nothing_in_the_project_names_is_not_referenced() -> None:
    """The finding requirement 1.1 exists for; the containment reference must not save it."""
    assert a_class_document(a_class("app.Orphan", a_file("app.py")))["referenced"] is False


def test_a_reference_written_outside_the_project_does_not_make_a_class_referenced() -> None:
    """A stub's or a vendored file's reference is not use, as it is not for a routine."""
    app, vendored = a_file("app.py"), a_file("vendor/six.py", lib="Standard")
    orphan = a_class("app.Orphan", app)
    orphan.refs_extra = [a_reference(a_routine("six.wraps", vendored), "python Useby", vendored)]

    assert a_class_document(orphan)["referenced"] is False


def test_the_file_the_reference_is_written_in_decides_for_a_class_too() -> None:
    """The reading pulled apart for a class, as ``callers`` already has it pulled apart.

    ``Ref.file()`` is where the reference is written; ``Ent.ref("definein").file()`` is where
    the referring entity was declared. They agree in ordinary code and in every other fixture
    in this section, so a helper that asked the second while its docstring claimed the first
    would pass all of them. Task 3.1's review found exactly that hole for callers and made the
    caller test; the helper it lives in now serves three facts, and each needs its own case or
    the guard stops being a guard in the thing that reuses it.

    Here the two disagree: a routine declared **inside** the project makes its only reference
    to the class **in a vendored file**. The requirements decide a project reference by the
    file it is written in, so this is not one, and the class is dead.
    """
    app, vendored = a_file("app.py"), a_file("vendor/six.py", lib="Standard")
    orphan = a_class("app.Orphan", app)
    declared_inside = a_routine("app.main", app)
    orphan.refs_extra = [a_reference(declared_inside, "python Useby", vendored)]

    assert a_class_document(orphan)["referenced"] is False


def test_every_derived_kind_is_also_a_reference_kind() -> None:
    """The invariant behind the correction, stated where a future edit has to meet it.

    ``unused_class`` reads ``referenced`` and ``single_implementation`` reads ``derived``. A
    kind that can put a class in the second answer and not the first is a kind that makes the
    two rules contradict each other on one class, which is exactly the defect task 1.6 found.
    """
    reference = set(worker_lean.REFERENCE_KINDS.split(", "))

    assert set(worker_lean.DERIVED_KINDS.split(", ")) <= reference


# --- the derived classes ----------------------------------------------------------------


@pytest.mark.parametrize(
    "kind", EVERY_DERIVED_KIND, ids=["python", "cplusplus", "extend", "implement"]
)
def test_every_derived_kind_names_the_class_that_inherits(kind: str) -> None:
    """The name requirement 3.1 asks the finding to print, one case per member of the set.

    The first two are the measured ones: Python answers ``Inheritby`` and C++ ``Derive``, both
    on the base class. The other two fire on neither fixture language and are carried for the
    languages the contract project does not build -- which is exactly why they need a case
    here. A kind dropped from the set matches nothing and answers an empty list rather than
    raising, so nothing else in this suite would notice.
    """
    app, other = a_file("app.py"), a_file("impl.py")
    base = a_class("app.Base", app)
    base.refs_extra = [a_reference(a_class("impl.Only", other), kind, other)]

    assert a_class_document(base)["derived"] == ["impl.Only"]


def test_a_base_class_with_one_derived_class_and_no_other_user_reads_as_both() -> None:
    """The two answers together, which is the pair the two rules disagreed about.

    ``referenced`` is true, so ``unused_class`` leaves it alone; ``referrers`` is zero and
    ``derived`` holds one name, so ``single_implementation`` reports it and names the
    implementation. One class, one finding, and the finding is the useful one.
    """
    app, other = a_file("app.py"), a_file("impl.py")
    base = a_class("app.Base", app)
    base.refs_extra = [a_reference(a_class("impl.Only", other), "python Inheritby", other)]

    found = a_class_document(base)
    assert (found["referenced"], found["derived"], found["referrers"]) == (True, ["impl.Only"], 0)


def test_two_derived_classes_are_named_in_a_stable_order() -> None:
    """Sorted rather than in the order Understand answers references in.

    The list reaches a snapshot document that is written to a cache and compared across the
    two sides of a change; an order the API happens to answer in would make two identical
    projects differ. Two names whose reference order is the reverse of their sorted order is
    what makes the sort observable rather than assumed.
    """
    app, other = a_file("app.py"), a_file("impl.py")
    base = a_class("app.Base", app)
    base.refs_extra = [
        a_reference(a_class("impl.Zebra", other), "python Inheritby", other),
        a_reference(a_class("impl.Antelope", other), "python Inheritby", other),
    ]

    assert a_class_document(base)["derived"] == ["impl.Antelope", "impl.Zebra"]


def test_one_pair_recorded_under_two_kinds_is_one_derived_class() -> None:
    """Four kinds are asked for at once, so a pair that answers two of them is still a pair.

    Not a shape measured on the contract project -- there each language answers exactly one of
    them -- but the same defence ``_project_callees`` makes against two call sites: the list is
    printed in a finding, and a name printed twice reads as two implementations.
    """
    app, other = a_file("app.py"), a_file("impl.py")
    base, only = a_class("app.Base", app), a_class("impl.Only", other)
    base.refs_extra = [
        a_reference(only, "python Inheritby", other),
        a_reference(only, "c Public Derive", other),
    ]

    assert a_class_document(base)["derived"] == ["impl.Only"]


def test_a_derived_class_outside_the_project_is_not_named() -> None:
    """A subclass in an injected stub is not an implementation this project can be told about.

    The **target** decides here, not the file the reference is written in, and the two
    questions are deliberately different: ``referenced`` asks whether the project uses the
    class, which is a property of where the reference sits, while ``derived`` is a name a
    finding prints and so has to belong to a file the project owns -- the same split
    ``callers`` and ``callees`` already make.

    The reference is written in a **project** file while the subclass itself is not, so the
    two readings disagree and this asserts the chosen one. The case below makes them disagree
    the other way round, and it takes both to state a reading rather than to happen to match
    it: a fixture with the subclass and the reference in one injected file agrees with either.
    """
    app, injected = a_file("app.py"), a_file("/opt/scitools/python/abc.py", lib="Standard")
    base = a_class("app.Base", app)
    base.refs_extra = [a_reference(a_class("abc.ABCMeta", injected), "python Inheritby", app)]

    assert a_class_document(base)["derived"] == []


def test_a_project_subclass_is_named_wherever_its_inheritance_reference_is_written() -> None:
    """The other half: the subclass is the project's, and the reference is not written in it.

    Understand records the inheritance reference on the base class, and where it writes it is
    not something a finding should turn on. What the finding names has to exist in a file the
    project owns, and this one does.
    """
    app, other, vendored = (
        a_file("app.py"),
        a_file("impl.py"),
        a_file("vendor/six.py", lib="Standard"),
    )
    base = a_class("app.Base", app)
    base.refs_extra = [a_reference(a_class("impl.Only", other), "python Inheritby", vendored)]

    assert a_class_document(base)["derived"] == ["impl.Only"]


def test_a_class_nothing_derives_from_names_nothing() -> None:
    """An empty list is a measurement: ``single_implementation`` must report nothing for it."""
    assert a_class_document(a_class("app.Plain", a_file("app.py")))["derived"] == []


# --- the referrers ------------------------------------------------------------------------


def test_a_routine_of_another_file_is_a_referrer() -> None:
    """The plain case: something else uses the class, so it has one implementation and a user."""
    app, other = a_file("app.py"), a_file("client.py")
    base = a_class("app.Base", app)
    base.refs_extra = [a_reference(a_routine("client.load", other), "python Useby", other)]

    assert a_class_document(base)["referrers"] == 1


def test_two_references_from_one_routine_are_one_referrer() -> None:
    """Referrers are distinct entities: the rule asks how many things use the class."""
    app, other = a_file("app.py"), a_file("client.py")
    base, user = a_class("app.Base", app), a_routine("client.load", other)
    base.refs_extra = [
        a_reference(user, "python Useby", other),
        a_reference(user, "python Callby", other),
    ]

    assert a_class_document(base)["referrers"] == 1


def test_a_referrer_written_outside_the_project_is_not_counted() -> None:
    """A vendored file's use cannot keep a class out of the single-implementation rule."""
    app, vendored = a_file("app.py"), a_file("vendor/six.py", lib="Standard")
    base = a_class("app.Base", app)
    base.refs_extra = [a_reference(a_routine("six.wraps", vendored), "python Useby", vendored)]

    assert a_class_document(base)["referrers"] == 0


def test_the_file_the_reference_is_written_in_decides_for_a_referrer() -> None:
    """The count's reading stated rather than left incidental.

    A routine declared **inside** the project whose reference to the class is written **in a
    vendored file**. Every other case in this section answers the same either way, so this is
    the one that says which question the count asks -- and it is the same question ``callers``
    asks, deliberately, because both feed rules that report "nothing in this project uses it".
    """
    app, vendored = a_file("app.py"), a_file("vendor/six.py", lib="Standard")
    base = a_class("app.Base", app)
    base.refs_extra = [a_reference(a_routine("app.main", app), "python Useby", vendored)]

    assert a_class_document(base)["referrers"] == 0


def test_a_referrer_declared_outside_the_project_still_counts_where_it_writes_inside() -> None:
    """And the other direction, which is what keeps the reading from being half-stated.

    A routine Understand declares in a stub, making its reference in a **project** file: the
    project does use the class there, and a rule that reported the class as having no user
    would be wrong about code an agent can read.
    """
    app, injected = a_file("app.py"), a_file("/opt/scitools/python/abc.py", lib="Standard")
    base = a_class("app.Base", app)
    base.refs_extra = [a_reference(a_routine("abc.abstractmethod", injected), "python Useby", app)]

    assert a_class_document(base)["referrers"] == 1


def test_the_class_itself_is_not_one_of_its_own_referrers() -> None:
    """A class that names itself -- a classmethod returning ``cls`` -- is not a user of it."""
    app = a_file("app.py")
    base = a_class("app.Base", app)
    base.refs_extra = [a_reference(base, "python Useby", app)]

    assert a_class_document(base)["referrers"] == 0


def test_a_member_of_the_class_is_not_a_referrer() -> None:
    """A method mentioning the class it is written in is the class using itself.

    Without this, every class with one method that names it -- a factory, an ``isinstance``
    check, a recursive constructor -- has a referrer and the rule reports nothing.
    """
    app = a_file("app.py")
    method = a_routine("app.Base.make", app)
    base = a_class("app.Base", app, members=[method])
    base.refs_extra = [a_reference(method, "python Useby", app)]

    assert a_class_document(base)["referrers"] == 0


def test_the_derived_class_and_its_members_are_not_referrers() -> None:
    """Requirement 3.1's own words: no project reference *other than from that derived class*.

    Both halves in one case, because both arrive together in real code: the subclass carries
    the inheritance reference, and its ``__init__`` carries the ``super().__init__()`` call.
    Counting either would take every base class with a constructor out of the rule.
    """
    app, other = a_file("app.py"), a_file("impl.py")
    method = a_routine("impl.Only.__init__", other)
    only = a_class("impl.Only", other, members=[method])
    base = a_class("app.Base", app)
    base.refs_extra = [
        a_reference(only, "python Inheritby", other),
        a_reference(method, "python Callby", other),
    ]

    found = a_class_document(base)
    assert (found["derived"], found["referrers"]) == (["impl.Only"], 0)


def test_a_module_body_that_uses_the_class_is_a_referrer() -> None:
    """Understand records a module-scope use against the file, and a file is a user.

    No kind filter stands in front of this count on purpose. ``Engine()`` at module level is
    a real use of the class, and refusing the file entity would report a class the project
    instantiates once at import time as an abstraction with one implementation.
    """
    app, other = a_file("app.py"), a_file("client.py")
    base = a_class("app.Base", app)
    base.refs_extra = [a_reference(other, "python Callby", other)]

    assert a_class_document(base)["referrers"] == 1


# --- module-level variables (requirement 1.1) ---------------------------------------------


def a_module_variable(name: str, container: FakeEnt) -> FakeEnt:
    """A module-level binding, as ``MODULE_VARIABLE_KIND`` answers with one."""
    return a_variable(name, container, kind="python Variable Global")


@pytest.mark.parametrize("kind", EVERY_VARIABLE_USE, ids=["read", "call", "type"])
def test_a_project_read_of_a_module_variable_is_a_use(kind: str) -> None:
    """The three kinds a use can arrive under, one of them a use as a type.

    ``callby`` is here for a module-level callable -- a compiled regex, a partial, a handler
    table entry -- whose only mention is the call, and ``typedby`` for a binding named in an
    annotation, which is a mention of the name and not a write to it.
    """
    app, other = a_file("app.py"), a_file("client.py")
    setting = a_module_variable("app.TIMEOUT", app)
    setting.refs_extra = [a_reference(a_routine("client.load", other), kind, other)]

    assert variable_used(setting) is True


@pytest.mark.parametrize("kind", ["python Setby Init", "python Modifyby"], ids=["set", "modify"])
def test_a_module_variable_only_written_is_not_used(kind: str) -> None:
    """The correction task 1.7's review forced, and the one that decides whether 1.1 works.

    A module-level binding's own defining assignment is a ``Set Init`` reference to it, so a
    set holding ``setby`` answers "used" for every variable in every project and the rule
    reports nothing, ever. The contract fixture cannot catch that -- its dead variables happen
    to have no read either -- which is why the case is written here.

    Writes are excluded rather than only the defining one, deliberately: a variable something
    assigns and nothing reads is dead too, which is also how Understand defines its own
    ``CountUnusedVariable``.
    """
    app, other = a_file("app.py"), a_file("client.py")
    setting = a_module_variable("app.TIMEOUT", app)
    setting.refs_extra = [a_reference(a_routine("client.load", other), kind, other)]

    assert variable_used(setting) is False


def test_a_module_variable_read_from_another_module_is_used() -> None:
    """Whole-project, not per-file: requirement 1.3 is what makes the answer worth having."""
    app, other = a_file("app.py"), a_file("client.py")
    setting = a_module_variable("app.TIMEOUT", app)
    setting.refs_extra = [a_reference(a_routine("client.load", other), "python Useby", other)]

    assert variable_used(setting) is True


def test_a_module_variable_read_only_from_outside_the_project_is_not_used() -> None:
    """A stub's reference is not use here either."""
    app, injected = a_file("app.py"), a_file("/opt/scitools/python/os.py", lib="Standard")
    setting = a_module_variable("app.TIMEOUT", app)
    setting.refs_extra = [a_reference(a_routine("os.getenv", injected), "python Useby", injected)]

    assert variable_used(setting) is False


def test_the_file_the_reference_is_written_in_decides_for_a_variable_too() -> None:
    """The third consumer of the one helper, and the third case it needs.

    A routine declared **inside** the project reading the binding **in a vendored file**: not
    a project reference, so the binding is dead. Without this case the helper could ask the
    referring entity's own definition file and every test in this module would still pass.
    """
    app, vendored = a_file("app.py"), a_file("vendor/six.py", lib="Standard")
    setting = a_module_variable("app.TIMEOUT", app)
    setting.refs_extra = [a_reference(a_routine("app.main", app), "python Useby", vendored)]

    assert variable_used(setting) is False


def test_a_module_variable_nothing_mentions_is_not_used() -> None:
    """The finding: a binding the project defines and never reads."""
    assert variable_used(a_module_variable("app.TIMEOUT", a_file("app.py"))) is False


# --- the declaring-class count per method name (requirement 1.9) ---------------------------


def test_a_name_two_classes_declare_is_counted_twice() -> None:
    """The one thing that sees an interface method where no inheritance edge exists.

    Measured on a 417-file codebase whose analysis resolves at 26%: the naive dead-code
    predicate answers 830 routines and exactly ONE of them carries an override reference,
    because that codebase satisfies its interfaces structurally -- an implementation holds no
    reference at all to the interface it satisfies. Inheritance detects nothing there; two
    classes declaring ``run`` is the whole of the evidence available.
    """
    app, other = a_file("app.py"), a_file("impl.py")
    first = a_class("app.Reader", app, members=[a_routine("app.Reader.run", app)])
    second = a_class("impl.Writer", other, members=[a_routine("impl.Writer.run", other)])

    assert worker_lean.method_declarations(a_class_map(first, second)) == {"run": 2}


def test_a_name_one_class_declares_is_counted_once() -> None:
    """The count is recorded whatever it is; the threshold of two belongs to the rule."""
    app = a_file("app.py")
    only = a_class("app.Reader", app, members=[a_routine("app.Reader.run", app)])

    assert worker_lean.method_declarations(a_class_map(only)) == {"run": 1}


def test_two_methods_of_one_class_sharing_a_name_count_that_class_once() -> None:
    """Overloads, and a C++ member that is both declared in a header and defined in a source.

    The count is of *classes*, not of declarations: a class that answers one name twice is
    still one class, and counting references would make an overloaded method look like an
    interface method on the strength of its own overloads.
    """
    app = a_file("app.py")
    only = a_class(
        "app.Box", app, members=[a_routine("app.Box.put", app), a_routine("app.Box.put", app)]
    )

    assert worker_lean.method_declarations(a_class_map(only)) == {"put": 1}


def test_a_member_that_is_not_a_routine_is_not_a_method() -> None:
    """A field named ``run`` on one class and a method named ``run`` on another are not a pair."""
    app, other = a_file("app.py"), a_file("impl.py")
    first = a_class("app.Reader", app, members=[a_variable("app.Reader.run", app)])
    second = a_class("impl.Writer", other, members=[a_routine("impl.Writer.run", other)])

    assert worker_lean.method_declarations(a_class_map(first, second)) == {"run": 1}


def test_a_project_with_no_classes_tallies_nothing() -> None:
    """An empty tally is a measurement, and a rule reading it must exclude nothing."""
    assert worker_lean.method_declarations({}) == {}


# --- the third kind string this file must not answer differently from the gate -------------


def test_a_class_member_is_found_the_way_the_worker_finds_one() -> None:
    """``worker._constructor_of`` walks the same references to find a class's constructor.

    The fourth string this file has to write out twice, and bound here for the reason the
    other three are: the fake ignores the filter it is given for ``Ent.ents``, and a kind
    dropped from a string matches nothing rather than raising, so a copy that drifted would
    quietly answer that no class declares any method -- and requirement 1.9's exclusion would
    then excuse nothing at all.
    """
    assert worker_lean.MEMBER_KINDS == worker.MEMBER_REFS
