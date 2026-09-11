"""The similar-routine rule: a family of routines that are one routine written many times.

Requirements 5.2, 5.3, 5.4, 5.5, 5.9, 5.10 and 5.11. One finding per **family** an affected
routine belongs to, naming the other members, their locations, the family's size and the
lowest similarity holding it together.

**Families and not pairs, which is an amendment measurement forced.** On a 417-file,
~101 800-line codebase, the 69 similar *pairs* at a threshold of 0.9 are 44 *families* over 98
routines; at 0.8 they are 81 families over 224 routines and about 2144 lines. The families are
recognisable as families rather than as coincidences -- the largest is twelve ``normalize``
methods, one per data provider, 180 lines between them. Sixty-six pairwise findings about
those twelve is noise a reviewer scrolls past; **one** finding saying that twelve routines are
one routine with a parameter is a task. It is also the shape a model produces bulk in, one
variant at a time without ever seeing the family, so it is the rung of the ladder most worth
automating.

**This rule takes no trust gate, and the two floors are declined one at a time.**
:class:`~scitools_hook.analysis.lean.dead.TrustGate` bounds the dead-code rules with two
numbers, and requirement 1.8's amendment is explicit that neither covers the other's evidence.
An argument that refutes one therefore does not refute the other, so each is answered on its
own ground:

* **The call-resolution floor** bounds whether the analyser knew what calls what. It is the
  guard behind the 830 routines a naive dead-code predicate answers on a structurally typed
  codebase, where an implementation holds no reference to the interface it satisfies. This
  rule reads no reference of any kind. It compares token *shapes* produced by
  ``Ent.lexer(False)``, a lexical pass that resolves nothing, so there is no resolved quantity
  for a floor to bound. Declining it costs nothing because it constrains nothing here.
* **The accuracy floor** bounds whether a file was read at all -- the failure behind the
  sixteen module bindings this repository reports unreferenced while every one of them is
  read. That failure is specific to an **absence** claim: "nothing references this" is only as
  good as the run's coverage of everywhere a reference could have been. This rule makes a
  **presence** claim -- these twelve routines are the same code -- and a presence claim is
  carried by the members it names, each of which the reader opens. A region the semantic
  analysis could not resolve still yields lexemes; a file whose lexer refused outright is in
  :attr:`~scitools_hook.models.snapshot.TokenIndex.unreadable` and is *named* in a note rather
  than silently read as a file with no routines in it (requirement 5.8). Low accuracy can
  therefore make this rule quieter than the code deserves. It cannot make it say something
  false, which is what the floor exists to prevent.

  The one measured fact this rule reads is ``CountStmt``, which it takes off
  :attr:`~scitools_hook.models.snapshot.RoutineShape.statements` -- the whole-project index --
  and reads the way :mod:`~scitools_hook.analysis.lean.layering` reads it: a routine whose
  statement count was never taken is **not judged**, rather than judged as a routine of no
  statements. That is the same direction -- quieter, never wronger.

**What the pass costs, which requirement 9.5 caps.** Scoring every pair of routines is
quadratic: a thousand routines is 500 500 ``SequenceMatcher`` runs, **measured at 29.9 s** on
the shapes below. Four things keep it off that curve, and only the first is an approximation.

**The numbers below were taken on this repository's own whole-project index** -- 312 files,
6803 indexed routines, **2344 of them considered** at the shipped ``similar_min_statements``
-- read back from a real extraction, at the shipped threshold of 0.9. They replace an earlier
account of this pass that was taken on the synthetic fixture alone and was wrong about which
filter does the work.

1. **Candidates come from a 4-gram shingle index** over the whole project's shapes, built once
   and linear in the project's tokens. Two routines that share no run of four normalised
   tokens are never scored. That is an approximation in the safe direction -- it can only make
   the rule miss a family, never invent one -- and **how much it can miss is bounded rather
   than asserted**, because the tempting assertion (that a pair with no common four-token run
   is not a renamed twin) is false at the thresholds this rule ships near.

   **On real Python it filters nothing, and that is measured rather than feared.** The
   vocabulary maps every identifier to one token and every literal to another, so the index's
   alphabet on this project is 61 symbols and a four-token run is the language's own
   punctuation. The median routine is offered **2341 of the other 2343** as candidates; a
   whole-project pass *offers* every one of the 2 724 564 pairs, of which the two bounds below
   refuse all but about five thousand.

   **The index is not cheap, and it is kept only until a task owns removing it.** Measured
   back to back on this repository's whole-project index: 48.2 s with it, 25.8 s with
   ``_candidates`` scanning the considered map instead and no postings built, the same 161
   findings both ways. So it costs roughly **46 per cent of the pass to remove 0.78 per cent
   of the pairs**, and it is the rule's only approximation. Removing it makes the rule
   lossless outright and faster; **task 5.9 owns that**. Until then, **nothing in the cost of
   this pass may be argued from it** -- which an earlier draft of this docstring did in one
   direction and a later one did in the other.

   A matching block of four consecutive tokens *is* a shared shingle, so every matching block
   of a pair the index never offers is three tokens or shorter. With ``M`` matched tokens in
   ``B`` blocks that gives ``B >= ceil(M / 3)``; consecutive blocks are separated by at least
   one unmatched token on one side, so ``la + lb >= 2M + B - 1`` and

   .. code-block:: text

       ratio = 2M / (la + lb)  <=  2M / (2M + ceil(M / 3) - 1)

   The bound is **tight**: ``k`` blocks of three written back to back in one shape and one
   fresh token apart in the other reach it exactly, measured here at 0.923 for ``k`` = 2,
   0.900 for 3, 0.889 for 4 and 0.870 for 10, falling towards **6/7, about 0.857**, as the
   shapes lengthen -- and ``2M / (2M + ceil(M / 3) - 1)`` is highest at ``M`` = 6, which is
   that row. Exhausting the pairs of shapes of five to eight tokens over a three-token
   alphabet that the index drops -- every pair whose two lengths can reach 12/13 at all, so
   the admitted length pairs are (5,5), (6,6), (6,7), (7,7), (7,8) and (8,8) and the rest are
   ruled out unscored by the exact length band of point 2 below -- finds nothing above it. So
   **12/13, about 0.9230769, is the highest ratio any pair the index drops can have**, and
   the search attains it at the six-token ``(0, 0, 0, 0, 0, 0)`` against the seven-token
   ``(0, 0, 0, 1, 0, 0, 0)``.

   No pair count is quoted here on purpose. Two independent runs of this search reported
   counts differing by about threefold, because the total depends entirely on which symmetry
   reduction the run applies -- whether the first shape is taken in first-occurrence canonical
   form, whether it is required to start at zero, and whether ordered or unordered pairs are
   counted. All three runs agreed on the three things that carry the argument: the admitted
   bands above, the maximum, and the witness. A number nobody can reproduce without also
   being told the reduction is not evidence, and this paragraph's whole posture is to record
   a bound rather than to assert past one.

   So the filter is **lossless above 0.923** at any length, and what it can lose below that is
   a function of how much of the pair matches: the bound above is at most 0.9 once ``M`` is
   nine or more. At the shipped threshold of 0.9 the only families it can drop are therefore
   held together by a pair matching on **nine tokens or fewer**, which a routine of
   ``similar_min_statements`` statements does not have; at 0.8, which requirement 5.9 names as
   the useful family threshold, the whole band down to 0.857 is reachable by long shapes and
   real families are dropped silently. That is the limit, recorded rather than asserted past.
2. **A length band refuses a pair outright**, and it is exact rather than heuristic:
   ``SequenceMatcher.ratio()`` is ``2 * matched / (len(a) + len(b))`` and ``matched`` cannot
   exceed the shorter sequence, so a pair whose ``2 * min / (la + lb)`` is already below the
   threshold cannot reach it whatever its contents. No pair that could have been a family
   member is lost here. It costs two integers, and on this project it admits **837 303 of the
   2 745 996 pairs, 30.5 per cent** -- which at a measured 1.04 ms per ``SequenceMatcher`` run
   over real shapes is most of a quarter-hour, and is the whole of why a ``check --all`` with
   this rule on did not finish in ten minutes.
3. **A token bound refuses the rest**, and it is exact for the same kind of reason.
   ``SequenceMatcher``'s matching blocks form a common *subsequence*, which can use a token no
   more often than the shorter shape holds it, so ``matched`` cannot exceed the two shapes'
   multiset overlap either. It reads the smaller routine's distinct tokens -- eighteen at this
   project's median, against a shape of 87 -- so a pair it refuses costs about fifty times
   less than scoring it would.

   **Measured over the whole of this repository, every routine affected**, which is what
   ``check --all`` asks for: with the bound, **48.7 s and 5191 ``SequenceMatcher`` runs**;
   with it removed and the length band left standing, **900.8 s and 831 694 runs**. Both
   report the same **158** findings, to the family, the member and the weakest edge. That is
   the losslessness argued above, measured rather than asserted: each bound is a ceiling on
   ``matched``, so a pair either of them refuses could not have reached the threshold whatever
   its contents.

   Both live in :func:`_comparable`, cheapest first.
4. **The walk follows the change through the project, not the project.** Only families that
   an affected routine belongs to are ever reported (5.3), so the components are grown by
   breadth-first search from the affected routines rather than by unioning every pair in the
   project. The component reached is the same component a whole-project union would find --
   BFS crosses every edge incident to a discovered member -- and a project whose change
   touches no family scores one round of candidates and stops.

   **"The index follows the project, the work follows the change" is now true of both token
   rules, and it was not.** The duplicate-block rule always had it: it reads ``tokens.files``,
   which narrowing never touches. This rule took each routine's ``CountStmt`` off ``entities``
   (:func:`_routine`), and the check pipeline hands the step an entity table narrowed to the
   change's files plus ONE dependency step -- so the vertex set stopped at that boundary
   rather than at the project's, and the BFS with it.

   Measured on this repository, at the SHIPPED threshold of 0.9 and not the 0.8 the families
   amendment discusses. The first measurement, over a random sample of 60 single-file commits:
   41 families whole-project, 26 through the narrowed table, **15 lost outright, 37 per cent**;
   a second method over one real change agreed at 8 of 22. Re-measured for task 5.8 over 60
   randomly sampled indexed source files, each treated as a one-file change, against a real
   whole-project index of 2344 considered routines: **31 families whole-project, 18 through
   the narrowed table under the old rule -- 13 lost, 42 per cent -- and 31 of 31 once
   :attr:`~scitools_hook.models.snapshot.RoutineShape.statements` carries the floor.** Nothing
   is lost. The losses had concentrated in the cross-file families, which are the ones worth
   merging; this module's own family of two showed one member.

   ``tests/runner/test_check_lean.py`` holds the reach at the wiring, on the one fixture where
   the two tables disagree.

Measured on this machine over a synthetic project of **1000 routines of 46 tokens each**, all
sharing an eight-token prologue and epilogue so that every routine is a shingle candidate for
every other and the length band lets every pair through -- the **worst case** for the two
filters, and the fixture the 29.9 s figure above was taken on. One affected routine, whose
family is grown to the size in the first column:

=========================  =========  =========================  =========
family the change is in    time       ``SequenceMatcher`` runs   peak heap
=========================  =========  =========================  =========
1 (no twin)                0.11 s     999                        7.5 MB
2 (a plain twin)           0.17 s     1 997                      7.6 MB
12 (the normalize shape)   0.78 s     11 922                     9.1 MB
=========================  =========  =========================  =========

Best of three. The shape those rows show is the promise: the work is the family's size times
the *candidates*, and the project's size enters only through the candidate count. **That
fixture's candidate count is 1000 because the fixture is built so that it is -- and a real
project's is not a handful either**, which is the claim this paragraph used to make and which
point 1 above now measures at 2341 of 2343. What holds a real project's cost down is the pair
of exact bounds in points 2 and 3, not the candidate count; the fixture keeps its place as the
guard against an accidental quadratic in the *walk*. The index the pass builds is linear in
the project's tokens and is the whole of the 7.5 MB floor.

``test_the_whole_project_scan_is_fast_at_a_thousand_routines`` holds the second row under one
second, which is loose on purpose -- it is a guard against an accidental quadratic, not a
benchmark, and a tight bound on a shared machine is a flaky test.

**What the whole project costs, measured end to end on this repository.** One change's worth
of work -- a one-file change over the whole-project vertex set of 2344 routines -- is about
0.3 s. A whole-project pass, where all 6669 affected keys seed walks and every one of the
2 724 564 pairs is scored or refused, **takes 48.7 s**; the same pass with the token bound of
point 3 removed takes 900.8 s for the same 158 findings.

Through the CLI, with ``similar_routines = "warning"`` and everything else as shipped:
``scitools-hook check --all`` **finishes in 1 min 14 s** including the analysis and both
snapshot reads, and reports **161 families, 41 of them cross-file**, the largest thirteen
routines at 0.905. Before task 5.8 the same command did not finish in ten minutes. A
``check --files src/scitools_hook/analysis/lean/duplicates.py`` finishes in 31 s and names
this module's ``_unreadable_note`` as that file's twin -- a routine in a file the change
neither touched nor depends on, which is requirement 5.3 in one line.

**The lowest similarity is a fact about the whole component, not about the pairs that
happened to be walked first.** Every pair of members that shares a shingle and passes both
bounds of :func:`_comparable` is scored exactly once, whichever member reaches it, so
:attr:`_Family.weakest` is the minimum over *all* the family's edges and does not move with
the order the affected set arrived in.

**A routine nested inside another is not its twin**, and this is
``duplicate_block``'s critical finding in the shape this rule can take it. A closure whose body
is nearly all of its enclosing routine scores above any usable threshold against it -- measured
here at 0.95 for a two-token wrapper -- and *neither member can be deleted in favour of the
other*, because one is part of the other. Reported, the finding would name a location inside
the routine it is reported at and would ask an agent to keep one of two routines that are one
routine. So a pair whose spans nest in one file is never an edge.

**Refusing the edge is not the whole rule, and the rule is a property of the family.** A
family is a connected component, so a nested pair still meets through a third member: an
``outer`` spanning 10-40, the ``inner`` at 12-38 written inside it and one ``bridge`` routine
in another file similar to both come back as **one** family of three, anchored at
``src/a.py:10`` and naming ``src/a.py:12`` -- a location inside the span it reports, carrying
no ``construct`` because the family is cross-file, and so answered with the rule-level
``delete:``. That is verbatim the harm the paragraph above says the guard prevents, and it is
the normal case rather than a corner: the 0.95 measured above is what a closure scores against
its parent, so the closure joins whenever the parent has the cross-file twin this rule exists
to find. So the rule is asked twice. After the component is built, **a member whose span is
contained in another member's span in the same file is dropped from the family**, and the
size, the other members, the family list and the weakest edge are all recomputed from the
survivors. Both halves ask ``_inside``, so the edge and the family cannot come to different
answers about the same two spans.

Three consequences are decided here rather than left to fall out of the order of a walk, and
all three are asserted:

* **A family seeded at a contained routine is reported at the routine holding it**, not
  dropped. The seed's text *is* part of the container's text, so a reader sent to the
  container is sent to the lines the change touched; staying silent would lose a real
  cross-file family over nothing but which of two overlapping routines the change was
  attributed to. The container is a member in its own right -- its edge to the routine inside
  it is refused, so it was reached by some other edge.
* **A member that reached the family only through a dropped one is dropped too.** The
  component is grown a second time over the survivors' edges alone, so every routine a
  finding names is similar to something else the finding names, and a family of two or more
  always has an edge left for :attr:`_Family.weakest` to be the minimum of. The second walk
  is a walk and not a sweep of the edges the prune left standing: dropping one member can
  leave the survivors in two components that each still hold an edge, and the one the anchor
  is not in is a different family that this finding may not name. The weakest edge is read
  off the same survivors for the same reason -- requirement 5.2 asks for the lowest
  similarity holding **this** family together, not the lowest number left in the component.
* **"Once per run" is keyed on the family's anchor, not on the routines the walk stepped
  through.** The component of a routine is the component of a routine, so two seeds in one
  component reach one ``kept``, one set of surviving edges and therefore one family per
  anchor; a second seed answering with an anchor already reported answered with the family
  already reported, and that is the duplicate requirement 5.11 refuses. It is the only
  mechanism that catches two touched closures of one container, neither of which is ever a
  member of the answer it produced. Marking the walk's own member set instead -- which an
  earlier draft did -- silences a family rather than a duplicate: a routine this family's
  prune drops still belongs to the family its own surviving edges hold, and would be reported
  only when nothing else in the same commit happened to reach it first. Measured: ``x.run``
  touched alone reports ``{x.run, y.run}`` at 0.90, and with an unrelated routine in another
  file also touched it was reported nothing at all.

**What a routine's family is remains a property of the project, not of the change**, and the
nesting rule makes that visible: a cross-file twin of a closure is in a family of three where
the enclosing routine is not in the project, and in no family at all where it is -- its one
link is then to code that is part of a member, which is the second bullet above read from the
other end. That is the recorded residue of refusing the nested edge, and it is the direction
this rule accepts: quieter, never wronger. The instability the third bullet removes is the one
that mattered, because it was a property of *what else the commit touched*.

**An excused routine contributes neither side**, path list and name list alike (5.5, 5.10). It
is not reported, it is not named as a member, **and it is not a link**: an idiomatic routine
similar to two families that are not similar to each other would otherwise merge them into one
finding that names routines a reader cannot merge.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Collection, Iterator, Mapping, Sequence
from difflib import SequenceMatcher
from typing import Final, NamedTuple

from scitools_hook.analysis.lean.dead import (
    LeanOutcome,
    compiled_patterns,
    name_excused,
    unavailable,
)
from scitools_hook.config.models import LeanRules, Severity, matching_pattern
from scitools_hook.models.findings import Finding, structure_rule
from scitools_hook.models.snapshot import (
    EntityKey,
    EntityRef,
    ProjectSnapshot,
    RoutineShape,
    TokenIndex,
)

SIMILAR_RULE: Final = structure_rule("similar_routine")

NAMED_MEMBERS: Final = 3
"""How many other members one message names, and how many files the unreadable note names.

The message has to stay one line an agent reads, and the count of the rest is carried with
them so a reader is never told about two twins when there are eleven.
``details["family"]`` carries **every** other member and is not capped: requirement 5.2 asks
the finding to name the family's other members, and that is the half a tool consumes. This is
the reason the number is not shared with
:data:`~scitools_hook.analysis.lean.duplicates.NAMED_LOCATIONS`, which caps what its finding
*carries*; the two agree on three today and answer different questions.
"""

SAME_FILE: Final = "same_file"
"""``details["construct"]`` when every member of the family sits in one file.

It selects ``structure.similar_routine/same_file`` in the hint catalogue, whose ``shrink:``
text says to merge the family in place, where the rule-level hint says ``delete:`` and asks
for a routine the other files can call. ``report.hints`` spells the same literal and says so;
``analysis`` sits below ``report`` and may not import it, so the agreement is written on both
sides and bound by a test that reads both. A rule that spelled it differently would raise
nothing: the lookup would fall through to the rule level and print the cross-file wording,
which is wrong advice rather than an error.

``example`` is reserved and no rule may use it as a construct: it is the variant
``report.lean_examples`` keys its worked examples on, and a finding naming it would have its
hint answered with the example instead of the remedy.
"""

SHINGLE: Final = 4
"""The n-gram length of the candidate index: four normalised tokens.

Short enough that two routines 90% alike share many of them, long enough that a shingle is
not simply the language's punctuation. A shape shorter than this yields one shingle -- itself
-- so a routine is never left with no way of finding its own twin.
"""

_SHIPPED: Final = LeanRules()
"""The settings model's own numbers, so a caller with none in hand -- a test, a probe -- uses
the shipped values rather than a second copy of them."""

_NO_INDEX: Final = "routine token shapes for the project's routines"
_COMPARED: Final = "compared for similarity"


class SimilarLimits(NamedTuple):
    """What the operator configured about *which* routines this rule groups and reports.

    Five decisions travel as one object rather than as five parameters because this project's
    own gate says so: spelled out beside ``after``, ``affected`` and ``severity`` they would
    put :func:`find_similar_routines` at eight parameters against a maximum of five, and
    ``scitools-hook check --worktree`` exits 1 on that. The grouping is the one that reads:
    every field here is a line of ``[lean]`` deciding which routines the rule is about.
    ``severity`` stays a parameter of its own, as on every sibling rule -- it says how loud
    the answer is rather than which routines qualify.
    """

    threshold: float = _SHIPPED.similar_threshold
    min_statements: int = _SHIPPED.similar_min_statements
    min_family: int = _SHIPPED.similar_min_family
    ignore: Sequence[str] = ()
    name_ignore: Sequence[str] = tuple(_SHIPPED.similar_name_ignore)


SIMILAR_DEFAULTS: Final = SimilarLimits()
"""The shipped numbers with the shipped idiom list; a named default, since B008 refuses a
call in a signature."""


class _Routine(NamedTuple):
    """One project routine the rule may group: its identity, its span and its shape.

    ``ref`` is ``None`` where the snapshot's entity table has no record of the routine, which
    a narrowed document has for every routine outside the change's reach. Nothing about the
    *family* is decided from it -- it is what the finding carries as
    :attr:`~scitools_hook.models.findings.Finding.entity`, and only the anchor's is ever
    read. An anchor is an affected routine or a routine holding one in the same file, so on
    the check pipeline's own wiring it always has a record; where it does not, the finding
    names the routine through ``details["longname"]``, its path and its line, and carries no
    entity reference rather than a fabricated one.

    ``bag`` is the shape's token multiset, built once per routine because
    :func:`_comparable` asks for it once per *pair* and a project of ``n`` routines has
    ``n**2 / 2`` of those.
    """

    token: str
    path: str
    start: int
    end: int
    longname: str
    ref: EntityRef | None
    shape: tuple[int, ...]
    bag: Mapping[int, int]

    def __str__(self) -> str:
        """``longname (path:line)``: requirement 5.2's "members, and their locations"."""
        return f"{self.longname} ({self.path}:{self.start})"


class _Family(NamedTuple):
    """One connected component of the similarity graph, ready to become a finding."""

    anchor: _Routine
    others: tuple[_Routine, ...]
    edges: tuple[float, ...]

    @property
    def size(self) -> int:
        """How many routines are in the family, the anchor included."""
        return len(self.others) + 1

    @property
    def weakest(self) -> float:
        """The lowest similarity holding the family together (req 5.2).

        ``min`` of an empty sequence rather than a default, deliberately: a family of one has
        no edge and no similarity to report, and it never reaches a finding because
        ``min_family`` is at least two. A number invented here would be a number a reader
        could not check.
        """
        return min(self.edges)


def find_similar_routines(
    after: ProjectSnapshot,
    affected: Collection[EntityKey],
    severity: Severity = "warning",
    limits: SimilarLimits = SIMILAR_DEFAULTS,
) -> LeanOutcome:
    """One finding per family of near-identical routines the change is in (req 5.2, 5.11).

    ``after`` is the after side alone, so a routine the change deleted has no index entry and
    cannot be reported. ``affected`` is the change's own entity set, which is the only query
    set. A family is reported **once per run** at its first affected member in file order,
    never once per member, so a commit touching three of twelve twins gets one finding.

    **Requirement 5.3 -- similarity decided over the whole project -- is met, and was not.**
    The vertex set is the whole-project token index rather than the entity table the check
    pipeline narrows to the change's files plus one dependency step; task 5.8 is what moved it
    there, and the module docstring's point 4 carries the measurement on both sides.

    A ``tokens`` of ``None`` is "the token pass was never asked for", never "this project has
    no twins", so it yields the run's one unavailable message and no findings (5.8).

    ``limits.min_family`` below two and ``limits.threshold`` at or below zero raise: see
    :func:`_refuse`. They are checked before the snapshot, because a nonsensical number is
    the caller's bug either way and saying "this metric was unavailable" about it would hide
    it.
    """
    _refuse(limits)
    index = after.tokens
    if index is None:
        return unavailable(SIMILAR_RULE, _NO_INDEX, _COMPARED)
    considered = _considered(after, index, limits)
    graph = _Graph(considered, limits.threshold)
    findings = [
        _finding(family, severity)
        for family in _families(graph, considered, affected)
        if family.size >= limits.min_family
    ]
    return LeanOutcome(findings=findings, unavailable=_unreadable_note(index, limits.ignore))


def _refuse(limits: SimilarLimits) -> None:
    """Refuse the two numbers at which this rule stops being this rule.

    A family minimum of one is a routine similar to nothing, which is every routine in the
    project; the settings model ships ``ge=2`` so no operator reaches it, but the number
    arrives here as an argument and a caller is not the settings model.

    A threshold at or below zero is worse than "everything matches". It is also what the two
    rejections :func:`_score` answers with ``0.0`` -- a nested pair, and a pair neither exact
    bound of :func:`_comparable` admits -- would silently turn into: both would become edges, and a
    project would come back as one family holding every routine in it.

    The two are **not** disjoint, which the earlier draft of this docstring claimed:
    ``SimilarLimits(min_family=1, threshold=0.0)`` fails both tests, and only one message is
    raised, so their order decides what such a caller reads. ``min_family`` is answered first
    and ``test_a_caller_refused_by_both_guards_is_told_about_the_family_minimum`` stands on
    that message for that input -- an order that decides an output and is described rather
    than asserted is an order the next edit is free to swap. Their order relative to the
    snapshot read is a second, separate decision, which is why this is a call of its own at
    the top of :func:`find_similar_routines` and why a test stands on that position too.
    """
    if limits.min_family < 2:
        raise ValueError(f"min_family must be at least 2, not {limits.min_family}")
    if limits.threshold <= 0.0:
        raise ValueError(f"threshold must be above 0, not {limits.threshold}")


def _considered(
    after: ProjectSnapshot, index: TokenIndex, limits: SimilarLimits
) -> dict[str, _Routine]:
    """Every indexed routine this rule may group, keyed by its entity-key token.

    One filtered map serves both sides of the rule -- the routines that may be reported and
    the routines that may be named as members -- because requirement 5.5 says an excused
    subject contributes neither, and two filters would let one of them drift. It is also the
    graph's whole vertex set, which is the third thing "neither side" has to mean: an excused
    routine may not link two families either.

    Its iteration order is deliberately not part of its contract, and it does not sort. The
    two orders a reader sees are decided elsewhere and unconditionally: findings come out in
    the order of :func:`_seeds`, and a family's other members in the order
    :meth:`_Graph.family` sorts them into.
    """
    excused = compiled_patterns(limits.name_ignore)
    found: dict[str, _Routine] = {}
    for token, shape in index.routines.items():
        if matching_pattern(limits.ignore, shape.path) is not None:
            continue
        routine = _routine(after, token, shape, excused, limits.min_statements)
        if routine is None:
            continue
        found[token] = routine
    return found


def _routine(
    after: ProjectSnapshot,
    token: str,
    shape: RoutineShape,
    excused: Sequence[re.Pattern[str]],
    min_statements: int,
) -> _Routine | None:
    """One indexed routine with everything the rule reads, or ``None`` where it is excused.

    Two separate refusals, one per reason, because each is a different way not to be judged:
    a name the operator excused as an idiom (5.10), and a routine the index either did not
    measure or measured below the floor requirement 5.2 sets. Read as one condition either
    could be deleted with every case in the suite still green, and they say different things.

    **Both are answered off the index and neither off the entity table**, which is what
    task 5.8 moved and what makes requirement 5.3 true of this rule: the entity table is
    narrowed to the change's files plus one dependency step, the index is whole-project, and
    while the floor was read off a record every routine outside that ring was left out of the
    vertex set. The module docstring's point 4 carries the measurement.

    The record is still *looked up*, and its absence is no longer a refusal: it carries the
    :class:`~scitools_hook.models.snapshot.EntityRef` a finding quotes, which only the
    anchor's is ever read for. See :class:`_Routine`.

    The floor is asked through :func:`_long_enough` rather than inline. The gate forced that
    split while there were four refusals here and no longer does with two, so it is kept for
    the reason it would have been made for anyway: the two halves say different things, one
    being a run that measured less than it looks like it did, and ``layering._within_budget``
    is the same split of the same metric one rule over.
    """
    key = EntityKey.from_token(token)
    if name_excused(excused, key.longname):
        return None
    if not _long_enough(shape, min_statements):
        return None
    record = after.entities.get(key)
    return _Routine(
        token=token,
        path=shape.path,
        start=shape.start,
        end=shape.end,
        longname=key.longname,
        ref=None if record is None else record.ref,
        shape=tuple(shape.shape),
        bag=Counter(shape.shape),
    )


def _long_enough(shape: RoutineShape, min_statements: int) -> bool:
    """Whether the routine has statements enough to be grouped, and was measured at all.

    An unmeasured ``CountStmt`` is not a routine of no statements, which is the treatment
    :func:`~scitools_hook.analysis.lean.layering._within_budget` gives the same metric one
    rule over: a routine the analysis did not measure is not judged rather than judged as
    empty. An index written before task 5.8 carried the shape and not the count, and reads
    back as exactly that absence. Two statements and not one ``and``, because branch coverage
    records no arc for a short circuit and the halves say different things.
    """
    if shape.statements is None:
        return False
    return shape.statements >= min_statements


def _families(
    graph: _Graph, considered: Mapping[str, _Routine], affected: Collection[EntityKey]
) -> Iterator[_Family]:
    """Every family the change is in, once per run and never once per member (req 5.11).

    Deduplicated on the **anchor**, which is the whole of it. A component decides one
    ``kept``, one set of surviving edges and therefore one family per anchor whichever member
    seeded the walk, so two seeds answering with the same anchor answered with the same
    family and the second is the duplicate 5.11 refuses. Two closures of one container,
    touched by one commit, are exactly that case and no other mechanism here catches them:
    both are dropped by the prune, so neither is ever a member of the answer it produced.

    Marking every routine the *walk* passed through instead -- which an earlier draft did --
    silences a family rather than a duplicate, because the walk passes through routines the
    prune then drops out of the answer. Measured on the fixture
    ``two_surviving_components``: ``x.run`` touched alone reports its family ``{x.run,
    y.run}`` at 0.90; with the unrelated ``a.outer`` touched as well, ``a.outer``'s walk
    reaches ``x.run`` through the closure it then prunes away, and ``x.run`` is reported
    nothing at all. The same project and the same routine, answered two ways by what else the
    commit contained. Requirement 5.11 asks for a family against every affected routine that
    belongs to it, and this rule exists to be stable under exactly that kind of difference.
    """
    taken: set[str] = set()
    for seed in _seeds(considered, affected):
        family = graph.family(seed.token)
        if family.anchor.token in taken:
            continue
        taken.update(item.token for item in (family.anchor, *family.others))
        yield family


def _seeds(considered: Mapping[str, _Routine], affected: Collection[EntityKey]) -> list[_Routine]:
    """The affected routines that may seed a family, in file order.

    File order rather than the order the affected set arrived in, because a set of keys comes
    out of Python in an order that changes per process and the first seed of a family decides
    the path and line a reader opens.

    It does **not** skip a routine an earlier walk passed through, and it does not skip an
    already-reported member either. "Once per run" is a property of the answer rather than of
    the walk, and :func:`_families` holds it on the anchor, which is the one place it can be
    held without losing a family a member still belongs to.

    A second skip here would decide nothing: a seed already reported is in that family, a
    reported family's members all survived the prune, and a survivor is its own family's
    anchor -- so the anchor check refuses it anyway, on the same set. Restored as an
    experiment it leaves all 814 targeted tests green, which is what a line that reads like a
    guarantee and is not one looks like. The cost it saved is a second walk of one component
    with every pair already scored and cached, which is dictionary lookups.
    """
    return sorted(
        (considered[key.token] for key in affected if key.token in considered), key=_position
    )


def _position(routine: _Routine) -> tuple[str, int, str]:
    """The one order this rule sorts routines in: where a reader would find them."""
    return (routine.path, routine.start, routine.longname)


class _Graph:
    """The project's routines as a similarity graph, grown from the change outwards.

    It holds the shingle postings and every pair already scored, so the expensive half --
    ``SequenceMatcher`` over two shapes -- runs at most once per pair however many members
    reach it, and never at all for a pair no affected routine's family touches.
    """

    def __init__(self, considered: Mapping[str, _Routine], threshold: float) -> None:
        self._routines = considered
        self._postings = _postings(considered)
        self._threshold = threshold
        self._scored: dict[tuple[str, str], float] = {}

    def family(self, seed: str) -> _Family:
        """The family ``seed`` belongs to: its component, less the routines written inside it.

        The component is found here and reduced by :func:`_held`, which applies the nesting
        rule to the family as a whole. The two steps are separate because they answer
        different questions -- what the similarity graph joined, and what a reader may be
        asked to merge -- and the second may not be folded into the walk: a member is only
        known to be contained in another once both have been discovered.

        It takes no "already reported" set and skips nothing: the component of a routine is
        the component of a routine, and two seeds in one component get the same answer here.
        Which of those answers is *reported* is :func:`_families`' decision, taken on the
        family that comes back rather than on the routines this walk stepped through.
        """
        members, edges = self._component(seed)
        return _held(self._routines, seed, members, edges)

    def _component(self, seed: str) -> tuple[set[str], dict[tuple[str, str], float]]:
        """The connected component of ``seed``, and every edge inside it kept by pair.

        Breadth-first rather than a union over every project pair, because the only families
        that may be reported are the ones an affected routine is in (5.3). Every member is
        popped in turn and scored against its own candidates, so every intra-family pair the
        shingle index offers is scored exactly once -- which is what makes
        :attr:`_Family.weakest` a fact about the component rather than about the walk order.

        The edges are kept by pair rather than as a bare list of ratios because the family
        the walk finds is not always the family reported: :func:`_held` drops members, and
        the weakest edge has to be recomputed over the ones that are left.
        """
        members = {seed}
        queue = [seed]
        edges: dict[tuple[str, str], float] = {}
        while queue:
            current = self._routines[queue.pop()]
            for other, ratio in self._matches(current):
                edges[_pair(current.token, other.token)] = ratio
                if other.token in members:
                    continue
                members.add(other.token)
                queue.append(other.token)
        return members, edges

    def _matches(self, current: _Routine) -> Iterator[tuple[_Routine, float]]:
        """Every routine similar enough to ``current`` to share its family, with its ratio."""
        for other in self._candidates(current):
            ratio = self._ratio(current, other)
            if ratio >= self._threshold:
                yield other, ratio

    def _candidates(self, current: _Routine) -> Iterator[_Routine]:
        """Every routine sharing a four-token run with ``current``, each offered once.

        The whole of the approximation this module's docstring declares. A routine that
        shares no shingle is never scored, which can make the rule quieter and cannot make it
        louder.
        """
        offered: set[str] = set()
        for shingle in _shingles(current.shape):
            for token in self._postings.get(shingle, ()):
                if token == current.token or token in offered:
                    continue
                offered.add(token)
                yield self._routines[token]

    def _ratio(self, one: _Routine, other: _Routine) -> float:
        """The pair's similarity, scored at most once per pair however often it is asked.

        The pair is ordered by token before it is scored and before it is remembered, because
        ``SequenceMatcher`` is not symmetric in its arguments: scored one way round for one
        member and the other way round for the other, a family could be held together by a
        number neither member would report.
        """
        pair = _pair(one.token, other.token)
        found = self._scored.get(pair)
        if found is not None:
            return found
        ratio = _score(self._routines[pair[0]], self._routines[pair[1]], self._threshold)
        self._scored[pair] = ratio
        return ratio


def _pair(one: str, other: str) -> tuple[str, str]:
    """Two entity-key tokens in the one order an edge between them is ever keyed by.

    ``SequenceMatcher`` is not symmetric in its arguments, so a pair scored one way round for
    one member and the other way round for the other could hold a family together by a number
    neither member would report. One order settles that for the score cache and for the edge
    map alike, which is why it is a function rather than a line inside either.
    """
    return (one, other) if one < other else (other, one)


def _held(
    routines: Mapping[str, _Routine],
    seed: str,
    members: set[str],
    edges: Mapping[tuple[str, str], float],
) -> _Family:
    """The component as it is reported: the nesting rule applied to the family, not the edge.

    :func:`_nested` refuses the edge between a routine and the routine it is written inside,
    and that alone is not enough -- a family is a connected component, so the two still meet
    through a third member. Every member contained in another is therefore dropped here, and
    the size, the other members, the family list and the weakest edge are all read off the
    survivors. The module docstring carries the case and the reasoning.

    Two decisions are taken rather than left to fall out; both are in the helpers below and
    both are asserted: which routine a family seeded at a dropped one is reported at
    (:func:`_anchoring`), and what happens to a member that reached the family only through a
    dropped one (:func:`_reached`).
    """
    kept = _unnested(routines, members)
    anchor = routines[_anchoring(routines, seed, kept)]
    surviving = {pair: ratio for pair, ratio in edges.items() if set(pair) <= kept}
    reached = _reached(anchor.token, surviving)
    ordered = sorted((routines[token] for token in reached), key=_position)
    return _Family(
        anchor=anchor,
        others=tuple(item for item in ordered if item.token != anchor.token),
        edges=tuple(ratio for pair, ratio in surviving.items() if set(pair) <= reached),
    )


def _unnested(routines: Mapping[str, _Routine], members: set[str]) -> set[str]:
    """``members`` less every routine written inside another of them.

    :func:`_supersedes` and not :func:`_nested`: nesting is symmetric and would drop both
    halves of every pair, which would answer a closure by deleting the family it joined. Both
    are built on :func:`_inside`, so the edge and the family cannot come to different answers
    about the same two spans, which is the drift this rule cannot afford.
    """
    return {
        token
        for token in members
        if not any(_supersedes(routines[other], routines[token]) for other in members)
    }


def _anchoring(routines: Mapping[str, _Routine], seed: str, kept: set[str]) -> str:
    """Which surviving member the family is reported at: the seed, or the routine holding it.

    Anchoring at the container rather than reporting nothing, because the seed's text **is**
    part of the container's text: a reader sent to the container is sent to the lines the
    change touched, while silence would lose a real cross-file family over nothing but which
    of two overlapping routines the change was attributed to. The container is a member of
    the family in its own right -- it was reached by an edge of its own, since the edge to
    the routine inside it is refused.

    A dropped seed always has a surviving container. :func:`_supersedes` is a strict partial
    order on a finite set of members, so the members above the seed have a maximal one, and a
    maximal member is superseded by nothing and therefore kept.

    A seed can be held by two members that do not contain each other, one starting earlier
    and ending earlier and the other starting later and ending later, and then ``min`` over
    file order is not merely a determinizer: it decides the longname the finding carries and
    the weakest edge it quotes. ``test_a_seed_held_by_two_containers_is_anchored_at_the_
    earlier_of_them`` asserts which, because an order that decides an output and is only
    described is an order the next edit is free to swap.
    """
    if seed in kept:
        return seed
    holding = [token for token in kept if _supersedes(routines[token], routines[seed])]
    return min(holding, key=lambda token: _position(routines[token]))


def _reached(anchor: str, edges: Mapping[tuple[str, str], float]) -> set[str]:
    """The members that still hold together with ``anchor`` once the dropped ones are gone.

    A second breadth-first walk, over the survivors' edges alone. A member that reached the
    component only through a routine the nesting rule dropped is no longer in this family:
    its one link was to code that is part of another member, and naming it would put a
    routine in a family nothing the finding names is similar to. It also keeps
    :attr:`_Family.weakest` answerable -- a family of two or more always has an edge left.
    """
    found = {anchor}
    queue = [anchor]
    while queue:
        for other in _linked(queue.pop(), edges):
            if other in found:
                continue
            found.add(other)
            queue.append(other)
    return found


def _linked(token: str, edges: Mapping[tuple[str, str], float]) -> Iterator[str]:
    """Every member sharing an edge with ``token``, each edge read from either end."""
    for one, other in edges:
        if one == token:
            yield other
        elif other == token:
            yield one


def _postings(considered: Mapping[str, _Routine]) -> dict[tuple[int, ...], list[str]]:
    """Shingle -> the routines holding it, built once and linear in the project's tokens.

    It does not sort, and the posting order is deliberately not part of its contract. Trace
    where it could reach: a posting order decides the order :meth:`_Graph._candidates` offers
    candidates in, which decides the order :attr:`_Family.edges` is filled in -- and ``min``
    does not care -- and the order members are discovered in, which
    :meth:`_Graph.family` sorts before anyone sees it. It reaches no output. A sort here would
    be a line no mutation of it could change, which is a line that reads like a guarantee and
    is not one; the two orders a reader *does* see are settled in :func:`_seeds` and
    :meth:`_Graph.family`, unconditionally.
    """
    found: dict[tuple[int, ...], list[str]] = {}
    for token in considered:
        for shingle in set(_shingles(considered[token].shape)):
            found.setdefault(shingle, []).append(token)
    return found


def _shingles(shape: tuple[int, ...]) -> tuple[tuple[int, ...], ...]:
    """Every window of :data:`SHINGLE` consecutive tokens, or the shape itself if it is short.

    A shape at or below the window length yields **one** shingle, itself, rather than none: a
    routine with no shingle would be invisible to the candidate index and could never find
    its own identical twin, which is a silence with no argument behind it.
    """
    if len(shape) <= SHINGLE:
        return (shape,)
    return tuple(shape[at : at + SHINGLE] for at in range(len(shape) - SHINGLE + 1))


def _score(one: _Routine, other: _Routine, threshold: float) -> float:
    """How alike two routines are, or ``0.0`` for a pair this rule will not join.

    ``0.0`` and not a ``None``, because the two refusals below mean the same thing to every
    caller -- not an edge -- and a threshold above zero is guaranteed by :func:`_refuse`.

    ``autojunk=False`` matters: the heuristic it turns off treats an element appearing in more
    than one per cent of a long sequence as junk, and a normalised shape is *made* of such
    elements -- one identifier class, one literal class, a handful of punctuation -- so a
    routine would be compared on its rare tokens alone.
    """
    if _nested(one, other):
        return 0.0
    if not _comparable(one, other, threshold):
        return 0.0
    return SequenceMatcher(None, one.shape, other.shape, autojunk=False).ratio()


def _nested(one: _Routine, other: _Routine) -> bool:
    """Whether one routine is written inside the other, which makes them one routine.

    Same file and one span inside the other: a closure and the routine that holds it, a
    method and a local function of it. Neither can be deleted in favour of the other, which
    is the remedy every hint on this rule offers, so the pair is not an edge. Refusing the
    edge is only half of the rule; :func:`_unnested` is the other half, and both ask
    :func:`_inside` so that the two cannot drift apart.
    """
    return _inside(one, other) or _inside(other, one)


def _inside(inner: _Routine, outer: _Routine) -> bool:
    """Whether ``inner``'s span lies within ``outer``'s, in one file.

    True of a routine against itself and of two routines sharing a span. :func:`_nested` and
    :func:`_supersedes` differ on what either of those should mean and each says so, which is
    why this answers containment and nothing else -- and why neither can drift from the other
    about the same two spans.
    """
    if inner.path != outer.path:
        return False
    return outer.start <= inner.start and inner.end <= outer.end


def _supersedes(outer: _Routine, inner: _Routine) -> bool:
    """Whether ``inner`` is written inside ``outer``, and ``outer`` is the one a family keeps.

    Directed, unlike :func:`_nested`, because this one decides which of two routines leaves
    the family. Containment settles it wherever the spans differ, and a routine never
    supersedes itself.

    Two *distinct* routines an index reported at **one** span contain each other, and neither
    answer falls out: dropping both would answer a degenerate measurement by deleting the
    family, keeping both would report a finding naming a location inside the span it is
    reported at -- which is the whole harm this rule is written against. So the tie is broken
    by :func:`_position`, the one order this module sorts routines in, and exactly one of the
    two survives. It is a tie no Python this rule has seen produces; it is settled here so
    that "no member is written inside another" holds of every family without a caveat.
    """
    if not _inside(inner, outer):
        return False
    if not _inside(outer, inner):
        return True
    return _position(outer) < _position(inner)


def _comparable(one: _Routine, other: _Routine, threshold: float) -> bool:
    """Whether two routines can reach the threshold at all, by two exact ceilings on a match.

    ``ratio()`` is ``2 * matched / (la + lb)``, so every upper bound on ``matched`` is an
    upper bound on the ratio. Both of the two below are **exact** rather than heuristic, which
    is the whole point of them: nothing that could have been a family member is refused here
    -- unlike the shingle index, which is an approximation -- and each costs a fraction of the
    quadratic match it saves. They are asked cheapest first and each is a statement of its
    own, so that deleting either, or swapping them, is visible to
    ``test_the_cheap_length_band_is_asked_before_the_token_bound`` rather than only to a clock.

    * **The length band**: ``matched`` cannot exceed the shorter shape. Two integers.
    * **The token bound**: ``SequenceMatcher``'s matching blocks form a common *subsequence*,
      which can use a token no more often than the shorter shape holds it, so ``matched``
      cannot exceed the multiset overlap :func:`_shared` measures -- eighteen distinct tokens
      at this repository's median, against a shape of 87.

    **The second one is why this rule finishes, and the first is not enough on its own**: on
    this repository the band admits 30.5 per cent of all pairs and the bound 0.62 per cent of
    those, which is 48.7 s against 900.8 s for the same findings. The module docstring's
    points 2 and 3 carry that measurement and the one that says why the shingle index cannot
    stand in for it.
    """
    span = len(one.shape) + len(other.shape)
    if 2 * min(len(one.shape), len(other.shape)) < threshold * span:
        return False
    return 2 * _shared(one.bag, other.bag) >= threshold * span


def _shared(one: Mapping[int, int], other: Mapping[int, int]) -> int:
    """How many tokens two shapes have in common, counting repeats: their multiset overlap.

    Symmetric, and deliberately written without a "walk the smaller map" line: the overlap is
    the same either way round, so such a line could be deleted with every case in the suite
    green, and what it would save here is a handful of dictionary lookups on the median pair.
    A line that reads like a decision and can decide nothing is worse than the lookups.
    """
    return sum(min(count, other[value]) for value, count in one.items() if value in other)


def _finding(family: _Family, severity: Severity) -> Finding:
    """One family, reported at its anchor; the pipeline attaches the tag hint."""
    named, rest = _named(family.others)
    first, *others = named
    return Finding(
        kind="structural",
        rule=SIMILAR_RULE,
        scope="routine",
        entity=family.anchor.ref,
        path=family.anchor.path,
        line=family.anchor.start,
        # The number the rule is about: how many routines are the same routine. `limit` stays
        # None because `min_family` is not a limit this value may not exceed -- it is the size
        # at which a family becomes reportable at all.
        value=float(family.size),
        limit=None,
        limit_source="rule",
        severity=severity,
        blocking=severity == "error",
        message=_message(family, first, others, rest),
        details=_details(family),
    )


def _named(items: Sequence[object]) -> tuple[list[str], int]:
    """The first :data:`NAMED_MEMBERS` of a list as text, and how many were left out.

    One decision in one place for the two lists this module caps -- a message's other members
    and the note's unreadable files -- because both make the same promise, that a reader is
    never shown three of something without being told there were more.
    """
    named = [str(item) for item in items[:NAMED_MEMBERS]]
    return named, len(items) - len(named)


def _details(family: _Family) -> dict[str, object]:
    """Every member, the size, the weakest edge, and the construct when there is one.

    ``construct`` is set **only** for a family wholly inside one file. Absent, the hint lookup
    falls through to the rule level and answers ``delete:``, which is the right remedy across
    files; present, it answers ``shrink:``, which is the right remedy within one. A key set on
    both branches would be a key that decides nothing.
    """
    found: dict[str, object] = {
        "family": [str(member) for member in family.others],
        "family_size": family.size,
        "similarity": family.weakest,
        "longname": family.anchor.longname,
    }
    if all(member.path == family.anchor.path for member in family.others):
        found["construct"] = SAME_FILE
    return found


def _message(family: _Family, first: str, others: Sequence[str], rest: int) -> str:
    """One line: how many routines, how alike, and the first places to look.

    The first member is a parameter of its own rather than the head of a list, so this
    function cannot be called with nothing to name and cannot render "``the others are``"
    followed by nothing. A family of one never reaches here -- ``min_family`` is at least two
    -- and the unpacking in :func:`_finding` raises rather than reaching here if it ever does.
    """
    more = f", and {rest} more" if rest else ""
    return (
        f"{family.anchor} is 1 of {family.size} routines this project holds in near-identical "
        f"form, the weakest pair matching at {family.weakest:.2f}; the others are "
        f"{', '.join([first, *others])}{more}"
    )


def _unreadable_note(index: TokenIndex, ignore: Sequence[str]) -> tuple[str, ...]:
    """Requirement 5.8's once-per-run note, or nothing when every file was lexed.

    Ignored paths are left out: a path that contributes neither side of the rule is not news
    when its lexer refuses, and naming it would ask an operator to act on a file they have
    already excluded.
    """
    refused = sorted(path for path in index.unreadable if matching_pattern(ignore, path) is None)
    if not refused:
        return ()
    named, rest = _named(refused)
    more = f", and {rest} more" if rest else ""
    return (
        f"{SIMILAR_RULE} read no token stream for {', '.join(named)}{more}; the routines of "
        f"each of those files are in no family and are named as no routine's twin",
    )
