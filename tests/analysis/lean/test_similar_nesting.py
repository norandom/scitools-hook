"""The nesting rule: a routine written inside another is not its twin (task 5.3's finding).

A closure whose body is nearly all of its enclosing routine scores 0.95 against it, and
*neither can be deleted in favour of the other*, because one is part of the other. Reported,
the finding would name a location inside the routine it is reported at and would ask an agent
to keep one of two routines that are one routine. So the rule is asked twice -- the edge is
refused, and then the family is pruned of every member written inside another -- and this
module holds both halves and everything that follows from the prune.

Three things follow, and each has a fixture built so that the two answers *differ*:

* what the family is once a member is dropped. :func:`nested_through_a_bridge` is the pair
  that meets through a third routine anyway, which is what makes refusing the edge
  insufficient; :func:`two_surviving_components` is the case where dropping the member leaves
  the survivors in **two** components that each still hold an edge, so a second walk that
  swept every surviving edge and a walk that followed the anchor answer differently, and so
  do a weakest edge read off the component and one read off the family.
* where such a family is reported, and with which similarity. The two routines an index
  reports at one span share their path and their line by construction, so a test standing on
  those alone is satisfied by either survivor: the longname and the similarity are what the
  tie-break moves, and they are what is asserted.
* which routines still get a family of their own. A member the prune drops from one family
  can still hold one through its own surviving edges, and it is reported whatever else the
  commit touched (5.11); a routine whose only link was the dropped member gets nothing, and
  that is a property of what the project contains rather than of the change.

:mod:`similar_shapes` holds the fixture vocabulary: shapes are :data:`~similar_shapes.BASE`
with named offsets replaced, so the similarity of any pair is ``2 * (40 - differing) / 80``
and every number asserted below can be read off the fixture.
"""

from __future__ import annotations

from typing import cast

import pytest
from similar_shapes import BASE, Routine, at, last_of, run, snap, spanned, variant


@pytest.mark.parametrize("inner_name", ["a.outer.inner", "a.aninner"])
def test_a_routine_nested_inside_another_is_not_its_twin(inner_name: str) -> None:
    """Task 5.3's critical finding, in the shape this rule can take it.

    A closure whose body is almost all of its enclosing routine scores 0.95 against it -- and
    neither can be deleted in favour of the other, because one *is* part of the other.
    Reported, the finding would tell an agent to keep one of two routines that are one
    routine, and it would name a line inside the routine it is reported at.

    Both names, because the pair is ordered before it is scored: whichever of the two the
    scorer is handed first, one span contains the other, and each name puts a different half
    of the containment test on the left.
    """
    outer = Routine("src/app/a.py", "a.outer", BASE, start=10, end=40)
    inner = Routine("src/app/a.py", inner_name, variant(0, 1), start=12, end=38)

    findings, _ = run(snap([outer, inner]), affected=[outer])

    assert findings == []


def test_a_nested_routine_is_still_compared_with_a_routine_outside_it() -> None:
    """The guard is about containment *in one file*, and nothing else.

    ``elsewhere`` spans the same lines as ``inner`` in a different file, so a containment
    test that forgot to compare paths would refuse this pair too -- and the one real twin the
    change has would go unreported.
    """
    outer = Routine("src/app/a.py", "a.outer", BASE, start=10, end=40)
    inner = Routine("src/app/a.py", "a.outer.inner", variant(0, 1, 2, 3), start=12, end=38)
    elsewhere = Routine("src/app/b.py", "b.run", variant(0, 1, 2, 3, 25, 26), start=12, end=38)

    findings, _ = run(snap([outer, inner, elsewhere]), affected=[inner])

    assert len(findings) == 1
    assert findings[0].details["family"] == [at(elsewhere)]


def nested_through_a_bridge() -> tuple[Routine, Routine, Routine]:
    """Task 5.4's review fixture: a closure, its parent, and the twin that joins them.

    ``inner`` is written inside ``outer`` and the pair is refused as an edge -- but both are
    similar to ``bridge`` in another file, and a family is a connected component, so the two
    meet through it anyway. Unfixed, this reports one finding at ``src/app/a.py:10`` on a
    routine spanning 10-40 that names ``src/app/a.py:12``.
    """
    outer = Routine("src/app/a.py", "a.outer", BASE, start=10, end=40)
    inner = Routine("src/app/a.py", "a.outer.inner", variant(0, 1), start=12, end=38)
    bridge = Routine("src/app/b.py", "b.run", variant(0, 1), start=5, end=25)
    return outer, inner, bridge


def test_no_finding_names_a_location_inside_the_routine_it_is_reported_at() -> None:
    """The property the edge guard promises, asserted of the family the finding names.

    A member quoted at a line inside the anchor's own span is a finding asking an agent to
    delete one of two routines that are one routine -- and being cross-file it carries no
    ``construct``, so the hint it is answered with is the rule-level ``delete:``.
    """
    outer, inner, bridge = nested_through_a_bridge()

    findings, _ = run(snap([outer, inner, bridge]), affected=[outer])

    assert findings
    for finding in findings:
        anchor_line = finding.line
        assert anchor_line is not None
        for member in cast("list[str]", finding.details["family"]):
            path, line = spanned(member)
            assert not (path == finding.path and anchor_line <= line <= last_of(outer))


def test_a_nested_member_reached_through_a_third_routine_is_dropped_from_the_family() -> None:
    """The nesting rule is a property of the FAMILY, not only of the edge it refuses.

    The family reported is the two routines that are not written inside one another, and
    every number the finding carries is recomputed from those: the size, the other members
    and the weakest edge.
    """
    outer, inner, bridge = nested_through_a_bridge()

    findings, _ = run(snap([outer, inner, bridge]), affected=[outer])

    assert len(findings) == 1
    assert findings[0].details["family"] == [at(bridge)]
    assert findings[0].details["family_size"] == 2
    assert findings[0].value == 2.0
    assert findings[0].details["similarity"] == pytest.approx(0.95)


def test_a_family_seeded_at_a_nested_routine_is_reported_at_the_routine_holding_it() -> None:
    """The change touched the closure, so the finding is reported at the code that holds it.

    Anchoring at the container rather than reporting nothing: the seed's text IS part of the
    container's text, so a reader sent to ``a.outer`` is sent to the lines the change
    touched, and staying silent would lose a real cross-file family over nothing but which
    of two overlapping routines the change was attributed to.
    """
    outer, inner, bridge = nested_through_a_bridge()

    findings, _ = run(snap([outer, inner, bridge]), affected=[inner])

    assert len(findings) == 1
    assert findings[0].path == outer.path
    assert findings[0].line == outer.start
    assert findings[0].details["longname"] == outer.longname
    assert findings[0].details["family"] == [at(bridge)]


def test_a_member_that_joined_only_through_a_dropped_routine_is_not_named() -> None:
    """Dropping a member drops what reached the family only through it.

    ``far`` is similar to ``inner`` and to nothing else -- 0.80 against ``outer``, 0.85
    against ``near`` -- so once ``inner`` goes, the one link holding ``far`` to this family
    is a routine that is part of ``outer``. Named anyway, it would be a member no other
    member is similar to.
    """
    outer = Routine("src/app/a.py", "a.outer", variant(0, 1, 2, 3), start=1, end=100)
    inner = Routine("src/app/a.py", "a.outer.inner", BASE, start=5, end=50)
    near = Routine("src/app/c.py", "c.run", variant(0, 1), start=5, end=25)
    far = Routine("src/app/d.py", "d.run", variant(30, 31, 32, 33), start=5, end=25)

    findings, _ = run(snap([outer, inner, near, far]), affected=[outer])

    assert len(findings) == 1
    assert findings[0].details["family"] == [at(near)]
    assert findings[0].details["family_size"] == 2
    assert findings[0].details["similarity"] == pytest.approx(0.95)


def test_two_routines_an_index_reports_at_one_span_are_not_both_named() -> None:
    """A degenerate measurement is answered by keeping one, not by deleting the family.

    Two distinct routines reported at the same lines of one file contain each other. Kept
    both, the finding would name a location inside the span it is reported at -- the very
    line it is reported at; dropped both, the family they share a twin with would vanish
    with them. Exactly one survives, chosen by the file order this module already sorts in,
    so "no member is written inside another" holds of every family without a caveat.

    The two are built so the assertions can tell them apart, which the path and the line
    cannot: they share both by construction, so a test standing on those alone would be
    satisfied by either survivor and would assert nothing about the tie-break. ``a.one``
    sorts first and is 0.95 from the bridge where ``a.two`` is identical to it, so the
    longname and the similarity say which of the two the family kept -- reverse the
    tie-break and they read ``a.two`` and 1.00.
    """
    first = Routine("src/app/a.py", "a.one", BASE, start=10, end=40)
    second = Routine("src/app/a.py", "a.two", variant(0, 1), start=10, end=40)
    bridge = Routine("src/app/b.py", "b.run", variant(0, 1), start=5, end=25)

    findings, _ = run(snap([first, second, bridge]), affected=[first])

    assert len(findings) == 1
    assert findings[0].path == first.path
    assert findings[0].line == first.start
    assert findings[0].details["longname"] == first.longname
    assert findings[0].details["similarity"] == pytest.approx(0.95)
    assert findings[0].details["family_size"] == 2
    assert findings[0].details["family"] == [at(bridge)]


def test_a_seed_held_by_two_containers_is_anchored_at_the_earlier_of_them() -> None:
    """Which container a dropped seed is reported at, asserted rather than described.

    A seed can be written inside two members that do not contain each other -- one starting
    earlier and ending earlier, the other starting later and ending later -- and then the
    anchor is a choice. The docstring calls file order "only so that the answer does not
    depend on the order a set of tokens iterated in", which is true of either direction, and
    this module's own standard is that an order deciding an output and merely described is
    one the next edit is free to swap. It decides two visible things: the longname the
    finding carries and the weakest edge it quotes.
    """
    seed = Routine("src/app/a.py", "a.seed", BASE, start=12, end=38)
    earlier = Routine("src/app/a.py", "a.earlier", variant(0, 1), start=10, end=38)
    later = Routine("src/app/a.py", "a.later", variant(0, 1, 2, 3), start=12, end=40)
    bridge = Routine("src/app/b.py", "b.run", variant(0, 1), start=5, end=25)

    findings, _ = run(snap([seed, earlier, later, bridge]), affected=[seed])

    assert len(findings) == 1
    assert findings[0].details["longname"] == earlier.longname
    assert findings[0].line == earlier.start


def two_surviving_components() -> tuple[Routine, Routine, Routine, Routine, Routine]:
    """The fixture that tells the second walk apart from the edges that survived the prune.

    Every member reaches the component through ``inner``, the closure written inside
    ``outer``: ``p.run`` is 0.95 from ``outer`` and 0.90 from ``inner``, ``x.run`` is 0.90
    from ``inner`` and only 0.85 from ``outer``, and ``y.run`` is 0.90 from ``x.run`` and
    below the threshold from everything else. Drop ``inner`` and the survivors fall into
    **two** components that each still hold an edge -- ``outer`` with ``p.run`` at 0.95, and
    ``x.run`` with ``y.run`` at 0.90.

    That is what no other fixture here builds, and it is what binds two guards at once: a
    second walk that took every endpoint of every surviving edge would report all four as one
    family, and a weakest edge read off every surviving edge rather than off the reported
    family's own would answer 0.90 from a pair the finding does not name.
    """
    outer = Routine("src/app/a.py", "a.outer", BASE, start=1, end=100)
    inner = Routine("src/app/a.py", "a.outer.inner", variant(2, 3), start=5, end=95)
    near = Routine("src/app/p.py", "p.run", variant(0, 1), start=5, end=25)
    far = Routine("src/app/x.py", "x.run", variant(2, 3, 4, 5, 6, 7), start=5, end=25)
    beyond = Routine("src/app/y.py", "y.run", variant(4, 5, 6, 7, 8, 9), start=5, end=25)
    return outer, inner, near, far, beyond


def test_the_family_is_what_still_reaches_the_anchor_and_not_every_surviving_edge() -> None:
    """The second walk is a walk, not a sweep of the edges the prune left standing.

    ``x.run`` and ``y.run`` are still similar to each other once ``inner`` is dropped, and
    that is exactly why they may not be named here: nothing joins them to ``outer`` any more.
    Named anyway, the finding would tell a reader that four routines are one routine when two
    of them are 0.85 and 0.75 from the code it is reported at.
    """
    outer, inner, near, far, beyond = two_surviving_components()

    findings, _ = run(snap([outer, inner, near, far, beyond]), affected=[outer])

    assert len(findings) == 1
    assert findings[0].path == outer.path
    assert findings[0].details["family"] == [at(near)]
    assert findings[0].details["family_size"] == 2


def test_the_weakest_edge_is_read_off_the_reported_family_and_not_off_the_survivors() -> None:
    """Requirement 5.2 asks for the lowest similarity holding **this** family together.

    The 0.90 between ``x.run`` and ``y.run`` survives the prune and is the lowest number left
    in the component, and it holds a pair this finding does not name. The family it does name
    is held together by one edge, at 0.95.
    """
    outer, inner, near, far, beyond = two_surviving_components()

    findings, _ = run(snap([outer, inner, near, far, beyond]), affected=[outer])

    assert findings[0].details["similarity"] == pytest.approx(0.95)


@pytest.mark.parametrize("also", [False, True], ids=["alone", "with_the_container"])
def test_a_routine_reports_its_own_family_whatever_else_the_commit_touched(also: bool) -> None:
    """Requirement 5.11: a family against every affected routine that belongs to it.

    ``x.run`` belongs to the family ``{x.run, y.run}`` whichever routine the commit touched
    first. ``a.outer``'s walk passes through ``x.run`` on its way round the component and
    then prunes away the member that carried it there, so a run that marked the *walk*
    reports ``x.run`` its family when it is touched alone and nothing at all when an
    unrelated routine in the same file is touched with it -- the same project and the same
    routine answered two ways by what else the commit contains.
    """
    outer, inner, near, far, beyond = two_surviving_components()
    touched = [outer, far] if also else [far]

    findings, _ = run(snap([outer, inner, near, far, beyond]), affected=touched)

    assert len(findings) == (2 if also else 1)
    own = [finding for finding in findings if finding.path == far.path]
    assert len(own) == 1
    assert own[0].details["family"] == [at(beyond)]
    assert own[0].details["similarity"] == pytest.approx(0.90)


def test_two_closures_of_one_container_touched_together_are_one_finding() -> None:
    """Requirement 5.11 from the other side: once per run, and the anchor says which run.

    Both closures are written inside ``outer`` and both are dropped by the prune, so both
    walks come back anchored at the container with the same family. They are one answer
    reached twice, and reporting each seed would be two identical findings -- "once per
    member", which 5.11 refuses.
    """
    outer = Routine("src/app/a.py", "a.outer", BASE, start=1, end=100)
    first = Routine("src/app/a.py", "a.outer.one", variant(0, 1), start=5, end=40)
    second = Routine("src/app/a.py", "a.outer.two", variant(2, 3), start=50, end=90)
    bridge = Routine("src/app/b.py", "b.run", variant(0, 1), start=5, end=25)

    findings, _ = run(snap([outer, first, second, bridge]), affected=[first, second])

    assert len(findings) == 1
    assert findings[0].path == outer.path
    assert findings[0].details["longname"] == outer.longname
    assert findings[0].details["family"] == [at(bridge)]


@pytest.mark.parametrize("holding", [False, True], ids=["closure_alone", "inside_its_parent"])
def test_a_twin_of_a_closure_has_the_family_the_project_leaves_it(holding: bool) -> None:
    """The recorded residue of the nesting rule: the project decides, not the change.

    ``d.run`` is a 0.90 cross-file twin of ``a.outer.inner`` and 0.80 from ``a.outer``. Where
    the enclosing routine is not in the project, ``inner`` is a routine of its own and the
    three are one family; where it is, ``inner`` is part of a member and ``d.run``'s one link
    to this family is code it may not be asked to delete. That is a property of what the
    project contains rather than of what the commit touched, and it is the direction this
    rule accepts: quieter, never wronger.
    """
    outer = Routine("src/app/a.py", "a.outer", variant(0, 1, 2, 3), start=1, end=100)
    inner = Routine("src/app/a.py", "a.outer.inner", BASE, start=5, end=50)
    near = Routine("src/app/c.py", "c.run", variant(0, 1), start=5, end=25)
    far = Routine("src/app/d.py", "d.run", variant(30, 31, 32, 33), start=5, end=25)
    project = [inner, near, far, outer] if holding else [inner, near, far]

    findings, _ = run(snap(project), affected=[far])

    assert [finding.details["family_size"] for finding in findings] == ([] if holding else [3])
