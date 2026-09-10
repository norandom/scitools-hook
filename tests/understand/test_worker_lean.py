"""The reference facts ``worker_lean`` measures for one routine (requirements 1.2-1.4, 2.1).

Three rules read this document and each fails differently, which is what the tests below are
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

**The receiver is reported unused here on purpose.** ``self`` has no reference in most
bodies, and this file measures rather than judges: ``DEFAULT_LEAN_PARAMETER_IGNORE`` is what
excuses it, one layer up. A worker that hid it would put the policy in the one process that
cannot be unit-tested without a licence.
"""

from __future__ import annotations

from typing import Any

import pytest
from api_fakes import FakeEnt, FakeRef, FakeUnderstand
from worker_projects import ANALYSIS_ROOT, a_file, a_parameter, a_routine

from scitools_hook.config.metric_names import SCOPE_KINDS
from scitools_hook.understand import worker, worker_lean

# --- the fixtures ---------------------------------------------------------------------


def a_context() -> worker_lean.LeanContext:
    """The context ``worker.py`` hands over: **its own** path helper and its analysis root.

    ``worker._project_path`` rather than a stand-in written here, because the whole meaning
    of "project" in these facts is that function's answer -- it is what refuses Understand's
    injected stubs and anything outside the root -- and a second copy of that judgement in
    the tests would let the two drift apart without either failing.
    """
    return worker_lean.LeanContext(
        api=FakeUnderstand(), project_path=worker._project_path, root=ANALYSIS_ROOT
    )


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


@pytest.mark.parametrize(
    "kind",
    ["python Useby", "python Setby Init", "python Modifyby", "python Callby"],
    ids=["use", "set", "modify", "call"],
)
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
    """The third copy, and the one no behavioural test can reach.

    ``FakeEnt.ref`` answers the containment reference whatever filter it is given, exactly as
    the impact tests need it to, so a kind dropped from ``CONTAINER_KINDS`` changes no unit
    test's answer. A routine the project only declares -- a pure virtual, an ``extern`` -- has
    no ``definein`` to fall back on, so losing ``declarein`` would silently drop every such
    callee, and only on a licensed machine.
    """
    assert worker_lean.CONTAINER_KINDS == worker.CONTAINER_REFS
