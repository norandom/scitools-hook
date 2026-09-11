"""The similar-routine rule: families of near-identical routines (req 5.2-5.5, 5.9-5.11).

**Families, not pairs, and the fixtures are built to hold that line.** Measured on a 417-file
codebase, 69 similar *pairs* at 0.9 are 44 *families* over 98 routines; the largest is twelve
``normalize`` methods, one per data provider. Sixty-six pairwise findings about twelve
routines is noise, so every case below asks how many findings one family produces and which
members one finding names -- never whether a particular pair was found.

:mod:`similar_shapes` holds the fixture vocabulary and says what a shape is; the nesting rule
-- a routine written inside another, the edge it refuses and the family it prunes -- is a
cohesive group of its own and lives in :mod:`test_similar_nesting`.

Each fixture is built so the two answers *differ*. The transitive family differs from a
one-pair fixture by the two offsets that put ``A`` and ``C`` below the threshold while both
sit above it against ``B``; the idiom family differs from the reported one by the routines'
names alone; the same-file family differs from the cross-file one by the path and by nothing
else. A fixture where both answers agree by construction would assert nothing, and that is a
rule about the *assertions* as much as about the inputs: a case whose two candidates share
everything a test happens to assert has asserted nothing about which of them survived.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from difflib import SequenceMatcher
from typing import Final

import pytest
from similar_shapes import (
    BASE,
    DEFAULTS,
    MIN_STATEMENTS,
    THRESHOLD,
    Routine,
    at,
    family_of,
    key_of,
    run,
    snap,
    variant,
)

from scitools_hook.analysis.lean import similar
from scitools_hook.analysis.lean.similar import NAMED_MEMBERS, SIMILAR_RULE, SimilarLimits
from scitools_hook.models.snapshot import ProjectSnapshot
from scitools_hook.report.hints import SAME_FILE

# --- one finding per family --------------------------------------------------------------


def test_a_family_of_twelve_is_one_finding_naming_eleven_others() -> None:
    """Requirement 5.2: twelve routines are one task, not sixty-six pairwise findings."""
    members = family_of(12)

    findings, notes = run(snap(members), affected=[members[0]])

    assert notes == ()
    assert len(findings) == 1
    finding = findings[0]
    assert finding.rule == SIMILAR_RULE
    assert finding.path == members[0].path
    assert finding.line == members[0].start
    assert finding.details["family_size"] == 12
    assert finding.details["family"] == [at(item) for item in members[1:]]
    assert len(finding.details["family"]) == 11


def test_a_change_touching_three_members_of_one_family_is_one_finding() -> None:
    """Requirement 5.11: once per run, never once per member."""
    members = family_of(12)

    findings, _ = run(snap(members), affected=members[3:6])

    assert len(findings) == 1
    assert findings[0].path == members[3].path


def test_the_finding_is_anchored_at_the_first_affected_member_in_file_order() -> None:
    """Three affected members, one finding, and which one it points at is not the input order.

    The anchor decides the path and line a reader opens, so it may not follow the order the
    affected set happened to arrive in -- a set Python iterates differently per process.
    """
    members = family_of(12)

    findings, _ = run(snap(members), affected=[members[7], members[2], members[5]])

    assert [finding.path for finding in findings] == [members[2].path]


def test_the_anchor_is_never_named_among_the_other_members() -> None:
    """A finding may not send a reader to the routine it is already reported at."""
    members = family_of(4)

    findings, _ = run(snap(members), affected=[members[1]])

    assert at(members[1]) not in findings[0].details["family"]  # type: ignore[operator]
    assert at(members[1]) not in findings[0].message.split("the others are ", 1)[1]


def test_a_family_of_two_still_reports() -> None:
    """Requirement 5.9: the minimum ships at two, so a plain twin loses nothing."""
    members = family_of(2)

    findings, _ = run(snap(members), affected=[members[0]])

    assert len(findings) == 1
    assert findings[0].details["family_size"] == 2
    assert findings[0].value == 2.0


def test_a_routine_similar_to_nothing_is_no_finding() -> None:
    """A family of one is a routine with no twin, which is most of a project."""
    alone = Routine("src/app/a.py", "a.only", BASE)
    other = Routine("src/app/b.py", "b.other", tuple(range(500, 540)))

    findings, notes = run(snap([alone, other]), affected=[alone])

    assert findings == []
    assert notes == ()


def test_a_change_touching_no_member_of_any_family_is_told_nothing() -> None:
    """Requirement 5.3: decided over the project, reported against the change."""
    members = family_of(3)
    touched = Routine("src/app/z.py", "z.alone", tuple(range(500, 540)))

    findings, _ = run(snap([*members, touched]), affected=[touched])

    assert findings == []


def test_a_deleted_routine_cannot_be_reported() -> None:
    """The after side alone: a key the change touched that the index never saw is skipped."""
    members = family_of(2)
    gone = Routine("src/app/gone.py", "gone.normalize", BASE)

    findings, _ = run(snap(members), affected=[gone, members[0]])

    assert [finding.path for finding in findings] == [members[0].path]


def test_two_families_are_two_findings_in_file_order() -> None:
    """One order, so the same project renders byte-identically in any process.

    ``report.json_out`` re-sorts nothing on purpose, on the stated ground that this layer
    already emits a settled order. Iterated as a set, these keys come out in an order Python
    randomises per process.
    """
    first = [Routine(f"src/app/a{number}.py", f"a{number}.run", BASE) for number in range(2)]
    second = [
        Routine(f"src/app/b{number}.py", f"b{number}.run", tuple(range(500, 540)))
        for number in range(2)
    ]

    findings, _ = run(snap([*first, *second]), affected=[second[0], first[0]])

    assert [finding.path for finding in findings] == ["src/app/a0.py", "src/app/b0.py"]


def test_the_other_members_are_ordered_by_path_and_line() -> None:
    """The order a reader sees inside one finding, settled the same way."""
    anchor = Routine("src/app/a.py", "a.run", BASE)
    late = Routine("src/app/z.py", "z.run", BASE, start=4)
    early = Routine("src/app/z.py", "z.other", BASE, start=2)

    findings, _ = run(snap([late, anchor, early]), affected=[anchor])

    assert findings[0].details["family"] == [at(early), at(late)]


# --- what holds a family together ---------------------------------------------------------


def test_similarity_is_transitive_so_three_routines_are_one_family() -> None:
    """Requirement 5.2: the family is the connected component, not the pair.

    ``B`` sits at the threshold against both ``A`` and ``C``; ``A`` and ``C`` differ in eight
    of forty tokens and score 0.8, below it. Pairs would report two findings and never say
    that all three are one routine.
    """
    first = Routine("src/app/a.py", "a.run", variant())
    middle = Routine("src/app/b.py", "b.run", variant(5, 6, 7, 8))
    last = Routine("src/app/c.py", "c.run", variant(5, 6, 7, 8, 25, 26, 27, 28))

    findings, _ = run(snap([first, middle, last]), affected=[first])

    assert len(findings) == 1
    assert findings[0].details["family_size"] == 3
    assert findings[0].details["family"] == [at(middle), at(last)]


def test_the_reported_similarity_is_the_lowest_edge_and_not_the_highest() -> None:
    """Requirement 5.2: "the lowest similarity holding it together".

    Two of the three are identical, so a rule reporting the best pair would say 1.00 about a
    family a reader has to be told is joined at 0.90.
    """
    first = Routine("src/app/a.py", "a.run", variant())
    twin = Routine("src/app/b.py", "b.run", variant())
    weaker = Routine("src/app/c.py", "c.run", variant(5, 6, 7, 8))

    findings, _ = run(snap([first, twin, weaker]), affected=[first])

    assert findings[0].details["similarity"] == pytest.approx(0.9)
    assert "0.90" in findings[0].message


def test_the_weakest_edge_is_found_even_between_two_routines_already_in_the_family() -> None:
    """The edge that decides requirement 5.2's number need not be one that grew the family.

    ``test_the_reported_similarity_is_the_lowest_edge_and_not_the_highest`` cannot hold this,
    and the reason is worth stating: there the non-tree edge and the lowest tree edge are both
    0.90, so a walk that never recorded the non-tree edge would answer 0.90 anyway. The two
    candidate answers coincide, and a fixture whose answers coincide asserts nothing.

    Here they do not. ``a.run`` reaches both others at 0.95 and puts them in the family; the
    edge that actually holds the family at its weakest runs between those two, and by the time
    the walk reaches it both endpoints are already members. Recording an edge only when it
    brings in a new member reports 0.95 about a family joined at 0.90.
    """
    first = Routine("src/app/a.py", "a.run", variant())
    second = Routine("src/app/b.py", "b.run", variant(5, 6))
    third = Routine("src/app/c.py", "c.run", variant(25, 26))

    findings, _ = run(snap([first, second, third]), affected=[first])

    assert findings[0].details["family_size"] == 3
    assert findings[0].details["similarity"] == pytest.approx(0.9)
    assert "0.90" in findings[0].message


def test_a_pair_below_the_threshold_is_not_a_family() -> None:
    """Eight of forty tokens differ: 0.80, and the shipped threshold is 0.90."""
    first = Routine("src/app/a.py", "a.run", variant())
    other = Routine("src/app/b.py", "b.run", variant(5, 6, 7, 8, 25, 26, 27, 28))

    findings, _ = run(snap([first, other]), affected=[first])

    assert findings == []


def test_the_threshold_is_the_configured_one_and_not_a_constant() -> None:
    """Requirement 5.5: the same fixture flips when the operator moves the number."""
    first = Routine("src/app/a.py", "a.run", variant())
    other = Routine("src/app/b.py", "b.run", variant(5, 6, 7, 8))
    after = snap([first, other])

    assert run(after, affected=[first])[0] != []
    assert run(after, affected=[first], limits=SimilarLimits(threshold=0.95))[0] == []


def test_a_similarity_exactly_at_the_threshold_joins_the_family() -> None:
    """At or above: the pair this fixture scores 0.90 on is the shipped threshold itself."""
    first = Routine("src/app/a.py", "a.run", variant())
    other = Routine("src/app/b.py", "b.run", variant(5, 6, 7, 8))

    findings, _ = run(snap([first, other]), affected=[first])

    assert findings[0].details["similarity"] == pytest.approx(THRESHOLD)


ASYMMETRIC: Final[tuple[tuple[int, ...], tuple[int, ...]]] = (
    (0, 3, 1, 3, 3, 4, 4, 3, 1, 1, 0, 3, 2, 4),
    (1, 0, 1, 3, 4, 4, 3, 3, 1, 1, 0, 3, 2, 4),
)
"""A pair ``difflib`` scores differently depending which shape it is given first.

0.857 one way round and 0.786 the other, found by search over random shapes and pinned here
because the property is what the test below is about: ``SequenceMatcher`` builds its index on
the *second* sequence, so its ratio is not symmetric, and a threshold of 0.8 falls between
these two numbers. They share a four-token run, so the candidate index offers the pair.
"""


def test_a_pair_scores_the_same_whichever_member_the_walk_reaches_first() -> None:
    """``difflib``'s ratio is not symmetric, so the pair is ordered before it is scored.

    Scored in the order the walk happens to reach it, this pair is a family when the change
    touches one member and no family when it touches the other -- the same two routines, two
    different answers, decided by which file the commit happened to open.
    """
    first = Routine("src/app/a.py", "a.run", ASYMMETRIC[0])
    second = Routine("src/app/b.py", "b.run", ASYMMETRIC[1])
    after = snap([first, second])
    loose = SimilarLimits(threshold=0.8)

    from_first, _ = run(after, affected=[first], limits=loose)
    from_second, _ = run(after, affected=[second], limits=loose)

    assert [finding.details["similarity"] for finding in from_first] == [
        pytest.approx(0.857, abs=0.001)
    ]
    assert [finding.details["similarity"] for finding in from_second] == [
        pytest.approx(0.857, abs=0.001)
    ]


def test_a_threshold_of_one_reports_identical_shapes_and_nothing_else() -> None:
    """The top of the configurable range, and the case the length band decides exactly.

    At 1.0 the band's own test is an equality -- two shapes of the same length are the only
    ones that can reach it -- so a band written with a strict comparison would refuse the one
    pair a threshold of 1.0 exists to find.
    """
    exact = SimilarLimits(threshold=1.0)
    same = [Routine("src/app/a.py", "a.run", BASE), Routine("src/app/b.py", "b.run", BASE)]
    near = [Routine("src/app/a.py", "a.run", BASE), Routine("src/app/b.py", "b.run", variant(7))]

    assert len(run(snap(same), affected=[same[0]], limits=exact)[0]) == 1
    assert run(snap(near), affected=[near[0]], limits=exact)[0] == []


def test_a_family_split_between_two_files_names_no_construct() -> None:
    """ "Every member shares a file" is *all* of them, not *some* of them.

    Two of these three sit in the anchor's file and the third does not, so the family cannot
    be merged in place: the ``shrink:`` wording would tell an agent to fold a routine it
    cannot see into a file that does not hold it.
    """
    members = [
        Routine("src/app/a.py", "a.first", BASE, start=1),
        Routine("src/app/a.py", "a.second", BASE, start=40),
        Routine("src/app/b.py", "b.third", BASE, start=1),
    ]

    findings, _ = run(snap(members), affected=[members[0]])

    assert findings[0].details["family_size"] == 3
    assert "construct" not in findings[0].details


def test_the_line_is_the_one_the_index_recorded_and_not_the_declaration_reference() -> None:
    """One scale for the anchor and the members, so a reader compares like with like.

    Understand's declaration reference and the token index's first line are two measurements
    of the same routine and need not agree. The members can only be quoted at the line the
    index gave them, so the anchor is quoted there too -- otherwise the one location a reader
    is sent to first is on a different scale from the rest of the list.
    """
    first = Routine("src/app/a.py", "a.run", BASE, start=10, declared=7)
    other = Routine("src/app/b.py", "b.run", BASE, start=20, declared=17)

    findings, _ = run(snap([first, other]), affected=[first])

    assert findings[0].line == 10
    assert findings[0].details["family"] == ["b.run (src/app/b.py:20)"]
    assert "a.run (src/app/a.py:10)" in findings[0].message


def test_a_renamed_copy_is_still_a_copy() -> None:
    """Requirement 5.4: the index normalises identifiers, so only the shape is compared."""
    first = Routine("src/app/a.py", "a.parse_customer_row", BASE)
    other = Routine("src/app/b.py", "b.read_supplier_record", BASE)

    findings, _ = run(snap([first, other]), affected=[first])

    assert len(findings) == 1
    assert findings[0].details["similarity"] == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("length", "matched", "families"),
    [(20, 0, 0), (38, 1, 1)],
    ids=["out_of_band", "in_band"],
)
def test_a_pair_whose_lengths_cannot_reach_the_threshold_is_never_matched(
    monkeypatch: pytest.MonkeyPatch, length: int, matched: int, families: int
) -> None:
    """The length band is exact, so it changes no answer -- only whether it is paid for.

    Twenty tokens against forty can match at most twenty, which is 0.67 whatever the tokens
    are, and the shorter shape is a prefix of the longer one so the shingle index offers the
    pair. The finding is absent either way, and the absence of an output proves nothing about
    whether the work ran: a call spy on the matcher is what says the band refused the pair
    before it was scored, and the in-band row is the case where the same spy fires.
    """
    long_form = Routine("src/app/a.py", "a.run", BASE)
    short_form = Routine("src/app/b.py", "b.run", BASE[:length])
    runs: list[int] = []

    def spy(
        isjunk: None, one: Sequence[int], other: Sequence[int], autojunk: bool = True
    ) -> SequenceMatcher[int]:
        runs.append(1)
        return SequenceMatcher(isjunk, one, other, autojunk=autojunk)

    monkeypatch.setattr(similar, "SequenceMatcher", spy)
    findings, _ = run(snap([long_form, short_form]), affected=[long_form])

    assert len(runs) == matched
    assert len(findings) == families


APART_TOKENS: Final[tuple[int, ...]] = (*BASE[:4], *range(900, 936))
"""Forty tokens sharing a four-token run with :data:`BASE` and nothing else.

The candidate index offers the pair -- they share the shingle ``(0, 1, 2, 3)`` -- and the
length band admits it, two shapes of forty tokens being the only kind that can reach a
threshold of 1.0. What refuses it is the token bound: four tokens in common out of forty and
forty is ``2 * 4 / 80``, so the pair cannot reach 0.10, let alone 0.90.
"""


@pytest.mark.parametrize(
    ("other_shape", "matched", "families"),
    [(APART_TOKENS, 0, 0), (variant(0, 1), 1, 1)],
    ids=["out_of_bound", "in_bound"],
)
def test_a_pair_whose_tokens_cannot_reach_the_threshold_is_never_matched(
    monkeypatch: pytest.MonkeyPatch, other_shape: tuple[int, ...], matched: int, families: int
) -> None:
    """The token bound is exact, so it changes no answer -- only whether it is paid for.

    ``SequenceMatcher`` matches a common *subsequence*, which can use a token no more often
    than the shorter shape holds it, so the multiset overlap is an exact ceiling on the
    matched count and therefore on the ratio. Both rows are in the length band and both are
    offered by the shingle index; the finding is absent either way on the first row, and the
    absence of an output proves nothing about whether the work ran. A call spy on the matcher
    is what says the bound refused the pair before it was scored.

    **The measurement this guard exists for**, on this repository's own whole-project index
    of 2344 considered routines at the shipped threshold of 0.9: of the 2 745 996 pairs,
    837 303 pass the length band and **5191 pass this one, 0.62 per cent of them**. A pass
    over every affected routine measures **48.7 s with this bound and 900.8 s without it, for
    the same 158 findings** -- the difference between a whole-project run task 6.3 can use and
    one that did not finish in ten minutes.
    """
    long_form = Routine("src/app/a.py", "a.run", BASE)
    other = Routine("src/app/b.py", "b.run", other_shape)
    runs: list[int] = []

    def spy(
        isjunk: None, one: Sequence[int], other: Sequence[int], autojunk: bool = True
    ) -> SequenceMatcher[int]:
        runs.append(1)
        return SequenceMatcher(isjunk, one, other, autojunk=autojunk)

    monkeypatch.setattr(similar, "SequenceMatcher", spy)
    findings, _ = run(snap([long_form, other]), affected=[long_form])

    assert len(runs) == matched
    assert len(findings) == families


@pytest.mark.parametrize(
    ("length", "counted"),
    [(20, 0), (38, 1)],
    ids=["out_of_band", "in_band"],
)
def test_the_cheap_length_band_is_asked_before_the_token_bound(
    monkeypatch: pytest.MonkeyPatch, length: int, counted: int
) -> None:
    """Guard order inside ``_comparable``, counted from the code and not from the prose.

    Both halves of ``_comparable`` are exact, so **neither order changes an answer** and the
    order is a cost decision alone: the band is two integers, the bound walks the smaller
    routine's token counts. Deleting the band, or asking the two the other way round, is
    therefore invisible in the findings -- which is why this stands on a call spy. The
    out-of-band row is the one the band answers on its own; the in-band row is where the
    bound is reached and the same spy fires.
    """
    long_form = Routine("src/app/a.py", "a.run", BASE)
    short_form = Routine("src/app/b.py", "b.run", BASE[:length])
    asked: list[int] = []
    real = similar._shared

    def spy(one: Mapping[int, int], other: Mapping[int, int]) -> int:
        asked.append(1)
        return real(one, other)

    monkeypatch.setattr(similar, "_shared", spy)
    run(snap([long_form, short_form]), affected=[long_form])

    assert len(asked) == counted


def test_a_long_routine_built_from_a_small_token_alphabet_is_still_compared() -> None:
    """``difflib``'s junk heuristic is off, and a normalised shape is why it has to be.

    From 200 elements up, ``SequenceMatcher`` treats an element appearing in more than one
    per cent of the second sequence as junk and matches around it. A normalised shape is
    *made* of such elements -- one identifier class, one literal class, a handful of
    punctuation -- so a 300-token routine is almost entirely junk by that rule. These two
    differ in three tokens of three hundred: 0.96 with the heuristic off, and **0.03** with it
    on, which is the difference between a family and silence.
    """
    shape = tuple((index * 7) % 5 for index in range(300))
    nearly = tuple(99 if index in (10, 150, 290) else token for index, token in enumerate(shape))
    first = Routine("src/app/a.py", "a.run", shape)
    other = Routine("src/app/b.py", "b.run", nearly)

    findings, _ = run(snap([first, other]), affected=[first])

    assert [finding.details["similarity"] for finding in findings] == [pytest.approx(0.96)]


def test_a_candidate_sharing_many_shingles_is_offered_once() -> None:
    """The dedupe is a cost guard, so no finding can say whether it ran.

    Two identical forty-token routines share thirty-seven windows, and every one of them puts
    the same routine in the candidate list. The answer is one family either way -- the score
    memo answers the repeats -- so the only artefact that can fail if the dedupe goes is this
    one, reading the candidate stream itself.
    """
    first = Routine("src/app/a.py", "a.run", BASE)
    other = Routine("src/app/b.py", "b.run", BASE)
    after = snap([first, other])
    assert after.tokens is not None
    considered = similar._considered(after, after.tokens, DEFAULTS)

    graph = similar._Graph(considered, DEFAULTS.threshold)
    offered = list(graph._candidates(considered[key_of(first).token]))

    assert [item.longname for item in offered] == ["b.run"]


def test_a_shape_shorter_than_one_shingle_can_still_find_its_twin() -> None:
    """A routine with no four-token run is still a routine, and its twin is still its twin.

    Three tokens yield no window of four at all, so a shingle index built from windows alone
    would offer this routine no candidate and it would never be compared with anything. The
    whole shape stands as its own shingle instead, and the pair reports.
    """
    tiny = (7, 8, 9)
    first = Routine("src/app/a.py", "a.run", tiny)
    other = Routine("src/app/b.py", "b.run", tiny)

    findings, _ = run(snap([first, other]), affected=[first])

    assert len(findings) == 1
    assert findings[0].details["similarity"] == pytest.approx(1.0)


# --- the construct the hint catalogue reads -----------------------------------------------


def test_a_family_wholly_inside_one_file_names_the_same_file_construct() -> None:
    """The literal, asserted as a literal, because a misspelling raises nothing.

    ``report.hints`` keys its ``shrink:`` hint on ``structure.similar_routine/same_file``. A
    rule that wrote ``same-file`` would fall through to the rule-level ``delete:`` text and
    tell an agent to delete one member of a family that should be merged in place.
    """
    members = [
        Routine("src/app/a.py", f"a.step{number}", BASE, start=1 + number * 30)
        for number in range(3)
    ]

    findings, _ = run(snap(members), affected=[members[0]])

    assert findings[0].details["construct"] == "same_file"


def test_the_construct_is_spelled_the_way_the_hint_catalogue_reads_it() -> None:
    """The two halves of an agreement no import can hold, bound by a test that reads both.

    ``analysis`` sits below ``report`` and may not import from it, so the literal is agreed
    rather than shared. This is the only artefact that fails if the two sides ever differ.
    """
    members = [
        Routine("src/app/a.py", f"a.step{number}", BASE, start=1 + number * 30)
        for number in range(2)
    ]

    findings, _ = run(snap(members), affected=[members[0]])

    assert findings[0].details["construct"] == SAME_FILE


def test_a_family_spread_over_files_names_no_construct_at_all() -> None:
    """The cross-file family takes the rule-level ``delete:`` hint, so it names no variant."""
    members = family_of(3)

    findings, _ = run(snap(members), affected=[members[0]])

    assert "construct" not in findings[0].details


@pytest.mark.parametrize("same_file", [True, False])
def test_no_finding_ever_names_the_reserved_example_construct(same_file: bool) -> None:
    """``example`` is the hint catalogue's own variant for its worked examples.

    A rule that set it would have its hint answered with the example instead of the remedy,
    on both sides of this rule's one branch.
    """
    paths = ["src/app/a.py"] * 3 if same_file else [f"src/app/m{number}.py" for number in range(3)]
    members = [
        Routine(path, f"m{number}.run", BASE, start=1 + number * 30)
        for number, path in enumerate(paths)
    ]

    findings, _ = run(snap(members), affected=[members[0]])

    assert findings[0].details.get("construct") != "example"


# --- which routines a family may be made of ------------------------------------------------


def test_a_routine_below_the_statement_floor_is_not_in_a_family() -> None:
    """Requirement 5.2: only routines of at least ``min_statements`` statements are grouped."""
    first = Routine("src/app/a.py", "a.run", BASE, statements=float(MIN_STATEMENTS - 1))
    other = Routine("src/app/b.py", "b.run", BASE, statements=float(MIN_STATEMENTS - 1))

    findings, _ = run(snap([first, other]), affected=[first])

    assert findings == []


def test_the_statement_floor_is_the_configured_one_and_not_a_constant() -> None:
    """Requirement 5.5: the same two routines flip when the operator lowers the floor."""
    first = Routine("src/app/a.py", "a.run", BASE, statements=3.0)
    other = Routine("src/app/b.py", "b.run", BASE, statements=3.0)
    after = snap([first, other])

    assert run(after, affected=[first])[0] == []
    assert run(after, affected=[first], limits=SimilarLimits(min_statements=3))[0] != []


def test_a_routine_at_the_floor_exactly_is_in_a_family() -> None:
    """The floor is a minimum the routine may sit on, not one it has to clear."""
    first = Routine("src/app/a.py", "a.run", BASE, statements=float(MIN_STATEMENTS))
    other = Routine("src/app/b.py", "b.run", BASE, statements=float(MIN_STATEMENTS))

    findings, _ = run(snap([first, other]), affected=[first])

    assert len(findings) == 1


def test_an_unmeasured_statement_count_is_not_a_routine_of_no_statements() -> None:
    """A record the analysis never measured is not judged, rather than judged as empty.

    Read as zero it would fall below every floor and be silently dropped; read as absent it
    is dropped for the same *reason*, which is the one a reviewer can check. The difference
    shows here: with the count present the pair reports, with it absent it does not, and the
    rule says nothing about a project it could not measure.
    """
    first = Routine("src/app/a.py", "a.run", BASE, statements=None)
    other = Routine("src/app/b.py", "b.run", BASE)

    findings, _ = run(snap([first, other]), affected=[first])

    assert findings == []
    assert run(snap([Routine("src/app/a.py", "a.run", BASE), other]), affected=[first])[0] != []


def test_a_routine_the_snapshot_holds_no_record_for_is_still_a_member() -> None:
    """Requirement 5.3 at the rule's own level: the vertex set is the index's, not the table's.

    The index and the entity table are two walks with two reaches, and the one that matters is
    the check pipeline's: it hands this rule an entity table cut to the change's files plus one
    dependency step, over a whole-project index. While the rule refused a routine that table
    had no record of, every twin outside that ring was invisible. Task 5.5 measured 15
    families of 41 lost on this repository, 37 per cent, over 60 single-file commits; task
    5.8 could not retake that sample (of 240 commits, 90 touch one file and one touches a
    single ``.py``) and measured 13 of 31 over 60 sampled source files instead. Both agree on
    the direction and the magnitude, and on where the losses fall: the cross-file families,
    which are the ones worth merging.

    This is the same case the old bound was recorded on, asserted the other way round. The
    mutation it stands on is ``_routine`` going back to ``after.entities`` for the floor, and
    the routine whose record is removed here is the *member* -- the half a narrowed document
    actually loses.
    """
    first = Routine("src/app/a.py", "a.run", BASE)
    other = Routine("src/app/b.py", "b.run", BASE)
    after = snap([first, other])
    after.entities.pop(key_of(other))

    findings, _ = run(after, affected=[first])

    assert [finding.details["family"] for finding in findings] == [[at(other)]]


def test_a_family_anchored_at_a_routine_with_no_record_carries_no_entity_reference() -> None:
    """What is genuinely lost with the record, said out loud rather than fabricated.

    ``Finding.entity`` is Understand's kind, name and declaration line, which live on the
    entity record and nowhere else; the index carries the routine's own span, which is not the
    same line. So an anchor with no record answers ``None`` there rather than a guess, and the
    finding still names the routine through ``details["longname"]``, its path and its line --
    which is what ``report.human`` and ``report.sarif`` fall back to.

    On the check pipeline's own wiring this case does not arise: an anchor is an affected
    routine, or a routine holding one in the same file, and both are in the change's files.
    It is asserted because the type allows it and a fabricated reference would not be
    detectable downstream.
    """
    first = Routine("src/app/a.py", "a.run", BASE)
    other = Routine("src/app/b.py", "b.run", BASE)
    after = snap([first, other])
    after.entities.pop(key_of(first))

    findings, _ = run(after, affected=[first])

    assert [finding.entity for finding in findings] == [None]
    assert [finding.details["longname"] for finding in findings] == ["a.run"]
    assert [(finding.path, finding.line) for finding in findings] == [("src/app/a.py", 10)]


# --- the two ignore lists ------------------------------------------------------------------


def test_an_ignored_path_is_not_reported() -> None:
    """Requirement 5.5: the path ignore list silences the file the change touched."""
    members = family_of(3)

    findings, _ = run(
        snap(members),
        affected=[members[0]],
        limits=SimilarLimits(ignore=[members[0].path]),
    )

    assert [finding.path for finding in findings] == []


def test_an_ignored_path_is_not_a_member_either() -> None:
    """An ignored path contributes *neither* side, so it is not named in someone else's family."""
    members = family_of(3)

    findings, _ = run(
        snap(members), affected=[members[0]], limits=SimilarLimits(ignore=[members[2].path])
    )

    assert findings[0].details["family_size"] == 2
    assert findings[0].details["family"] == [at(members[1])]


def test_an_idiom_family_is_silent() -> None:
    """Requirement 5.10: thirty ``__post_init__`` validators are one shape per class on purpose.

    They are near-identical because a dataclass validator has one shape, and there is no
    routine to merge them into. The shipped name list is what keeps the rule from asking for
    that merge twelve members at a time.
    """
    members = [
        Routine(f"src/app/m{number}.py", f"m{number}.Order.__post_init__", BASE)
        for number in range(12)
    ]

    findings, notes = run(snap(members), affected=[members[0]])

    assert findings == []
    assert notes == ()


def test_the_name_list_is_the_configured_one_and_not_a_constant() -> None:
    """Requirement 5.10: the same family flips when the operator names it."""
    members = [
        Routine(f"src/app/m{number}.py", f"m{number}.normalize", BASE) for number in range(3)
    ]
    after = snap(members)

    assert run(after, affected=[members[0]])[0] != []
    excused = SimilarLimits(name_ignore=[r"\.normalize$"])
    assert run(after, affected=[members[0]], limits=excused)[0] == []


def test_an_excused_name_is_not_a_member_of_someone_else_s_family() -> None:
    """The name list contributes neither side, exactly as the path list does."""
    members = family_of(3)
    excused = Routine("src/app/hook.py", "hook.Order.__post_init__", BASE)

    findings, _ = run(snap([*members, excused]), affected=[members[0]])

    assert findings[0].details["family_size"] == 3
    assert at(excused) not in findings[0].details["family"]  # type: ignore[operator]


def test_an_excused_routine_does_not_join_two_families_it_sits_between() -> None:
    """The consequence of "neither side" that a member-count assertion would miss.

    The excused routine is similar to both halves and they are not similar to each other, so
    a rule that dropped it from the *report* while leaving it in the *graph* would answer one
    family of three where this answers two families of one and no finding at all.
    """
    left = Routine("src/app/a.py", "a.run", variant())
    bridge = Routine("src/app/b.py", "b.Order.__post_init__", variant(5, 6, 7, 8))
    right = Routine("src/app/c.py", "c.run", variant(5, 6, 7, 8, 25, 26, 27, 28))

    findings, _ = run(snap([left, bridge, right]), affected=[left])

    assert findings == []


# --- the family minimum ----------------------------------------------------------------------


def test_a_family_below_the_configured_minimum_is_silent() -> None:
    """Requirement 5.9: the minimum is configurable, and the same fixture flips on it."""
    members = family_of(2)
    after = snap(members)

    assert run(after, affected=[members[0]])[0] != []
    assert run(after, affected=[members[0]], limits=SimilarLimits(min_family=3))[0] == []


def test_a_family_at_the_configured_minimum_reports() -> None:
    """The minimum is a size the family may have, not one it has to exceed."""
    members = family_of(3)

    findings, _ = run(snap(members), affected=[members[0]], limits=SimilarLimits(min_family=3))

    assert len(findings) == 1


@pytest.mark.parametrize("min_family", [0, 1])
def test_a_family_minimum_below_two_is_refused(min_family: int) -> None:
    """A family of one is a routine similar to nothing, which is most of the project.

    ``similar_min_family`` ships ``ge=2``, so no operator reaches either -- but the number
    arrives here as an argument and a caller is not the settings model.
    """
    members = family_of(2)

    with pytest.raises(ValueError, match="min_family"):
        run(snap(members), affected=[members[0]], limits=SimilarLimits(min_family=min_family))


@pytest.mark.parametrize("threshold", [0.0, -0.5])
def test_a_threshold_of_nothing_is_refused(threshold: float) -> None:
    """At or below zero every pair of routines is a union and the project is one family.

    It is also what the two rejections this rule answers with ``0.0`` -- a nested pair and a
    pair neither exact bound of ``_comparable`` admits -- would silently become.
    """
    members = family_of(2)

    with pytest.raises(ValueError, match="threshold"):
        run(snap(members), affected=[members[0]], limits=SimilarLimits(threshold=threshold))


def test_a_caller_refused_by_both_guards_is_told_about_the_family_minimum() -> None:
    """The two guards are not disjoint, so the order that decides the message is asserted.

    ``min_family=1`` with ``threshold=0.0`` fails both tests and only one message is raised.
    Which one is an output, and an output left unasserted is one the next edit is free to
    swap: the docstring on the guard says the same and points here.
    """
    members = family_of(2)

    with pytest.raises(ValueError, match="min_family must be at least 2, not 1"):
        run(
            snap(members),
            affected=[members[0]],
            limits=SimilarLimits(min_family=1, threshold=0.0),
        )


@pytest.mark.parametrize(
    "limits",
    [SimilarLimits(min_family=1), SimilarLimits(threshold=0.0)],
    ids=["min_family", "threshold"],
)
def test_a_refused_number_is_refused_before_the_snapshot_is_read(limits: SimilarLimits) -> None:
    """Both answers are reachable and they differ, so the guard's position is asserted.

    A snapshot with no index answers the unavailable message, so a guard moved below that
    return would report the caller's bug as a measurement this run could not take.
    """
    with pytest.raises(ValueError):
        run(ProjectSnapshot(side="after"), limits=limits)


# --- the finding a reader sees -----------------------------------------------------------------


def test_the_message_names_the_size_the_weakest_pair_and_three_others() -> None:
    """One line: how many, how alike, and the first places to look."""
    members = family_of(12)

    findings, _ = run(snap(members), affected=[members[0]])

    message = findings[0].message
    assert at(members[0]) in message
    assert "12" in message
    assert "0.90" not in message
    assert "1.00" in message
    for item in members[1 : 1 + NAMED_MEMBERS]:
        assert at(item) in message
    assert message.endswith("and 8 more")
    assert at(members[5]) not in message


def test_a_family_whose_members_all_fit_is_not_told_there_are_more() -> None:
    """A reader shown everything there is must not be told there is more."""
    members = family_of(1 + NAMED_MEMBERS)

    findings, _ = run(snap(members), affected=[members[0]])

    assert "more" not in findings[0].message
    assert findings[0].message.endswith(at(members[-1]))


def test_severity_error_blocks_and_warning_does_not() -> None:
    """The severity the operator chose decides blocking, as on every structural rule."""
    members = family_of(2)

    blocking, _ = run(snap(members), affected=[members[0]], severity="error")
    warned, _ = run(snap(members), affected=[members[0]])

    assert (blocking[0].severity, blocking[0].blocking) == ("error", True)
    assert warned[0].blocking is False


def test_the_finding_carries_the_routine_it_is_reported_at() -> None:
    """A routine-scope finding, so the renderers have the entity rather than a path alone."""
    members = family_of(2)

    findings, _ = run(snap(members), affected=[members[0]])

    assert findings[0].scope == "routine"
    assert findings[0].entity is not None
    assert findings[0].entity.key == key_of(members[0])
    assert findings[0].details["longname"] == members[0].longname


def test_the_size_is_the_value_and_no_limit_is_claimed() -> None:
    """The family minimum is the size at which a family reports, not a limit it exceeded."""
    members = family_of(5)

    findings, _ = run(snap(members), affected=[members[0]])

    assert findings[0].value == 5.0
    assert findings[0].limit is None
    assert findings[0].limit_source == "rule"


# --- what could not be measured ----------------------------------------------------------------


def test_no_index_yields_the_unavailable_message_and_no_findings() -> None:
    """Requirement 5.8: ``tokens`` of ``None`` is 'not asked', never 'nothing is alike'."""
    findings, notes = run(ProjectSnapshot(side="after"))

    assert findings == []
    assert len(notes) == 1
    assert notes[0].startswith(f"{SIMILAR_RULE} is on, but this snapshot carries no ")
    assert "db rebuild" in notes[0]


def test_an_unreadable_file_is_noted_once_and_is_similar_to_nothing() -> None:
    """Requirement 5.8: a file that was never lexed is said, not silently absent."""
    members = family_of(2)

    findings, notes = run(snap(members, unreadable=["src/odd.py"]), affected=[members[0]])

    assert len(findings) == 1
    assert len(notes) == 1
    assert notes[0].startswith(SIMILAR_RULE)
    assert "src/odd.py" in notes[0]
    assert "more" not in notes[0]


def test_many_unreadable_files_name_three_and_count_the_rest() -> None:
    """The note stays one line however many files refused."""
    refused = [f"src/odd{number}.py" for number in range(5)]

    _, notes = run(snap(family_of(2), unreadable=refused))

    assert len(notes) == 1
    for path in refused[:NAMED_MEMBERS]:
        assert path in notes[0]
    assert "2 more" in notes[0]
    assert refused[4] not in notes[0]


def test_an_ignored_unreadable_file_is_not_noted() -> None:
    """An ignored path contributes nothing, so its lexer refusing is not the run's news."""
    _, notes = run(
        snap(family_of(2), unreadable=["vendor/lib/odd.py"]),
        limits=SimilarLimits(ignore=["vendor"]),
    )

    assert notes == ()


def test_the_unreadable_note_and_the_unavailable_message_are_not_the_same_answer() -> None:
    """One says the run could not judge; the other says one file could not be read."""
    missing = run(ProjectSnapshot(side="after"))[1]
    refused = run(snap(family_of(2), unreadable=["src/odd.py"]))[1]

    assert missing != refused


# --- cost ------------------------------------------------------------------------------------


def test_a_family_the_change_never_touched_is_never_scored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement 9.5: the families that are built are the ones the change is in.

    The answer is one finding either way -- a whole-project pass would produce this finding
    and no other -- so the absence of a second finding proves nothing about whether the work
    ran. A call spy on the scorer is what says so: the untouched pair is never compared.
    """
    touched = [Routine(f"src/app/a{number}.py", f"a{number}.run", BASE) for number in range(2)]
    untouched = [
        Routine(f"src/app/b{number}.py", f"b{number}.run", tuple(range(500, 540)))
        for number in range(2)
    ]
    compared: list[tuple[str, str]] = []
    real = similar._score

    def spy(one: similar._Routine, other: similar._Routine, threshold: float) -> float:
        compared.append((one.longname, other.longname))
        return real(one, other, threshold)

    monkeypatch.setattr(similar, "_score", spy)
    findings, _ = run(snap([*touched, *untouched]), affected=[touched[0]])

    assert len(findings) == 1
    assert compared == [("a0.run", "a1.run")]


@pytest.mark.parametrize("routines", [1000])
def test_the_whole_project_scan_is_fast_at_a_thousand_routines(routines: int) -> None:
    """Requirement 9.5: the pass follows the change through the project, not every pair.

    A thousand routines sharing a prologue and an epilogue, so every one of them is a
    shingle candidate for every other and the length band lets them all through: the work is
    decided by what the change reaches and by nothing else. All-pairs scoring is half a
    million ``SequenceMatcher`` runs here and takes minutes.
    """
    project = [
        Routine(
            f"src/pkg{number // 20}/mod{number}.py",
            f"mod{number}.run",
            (*range(8), *range(1000 + number * 30, 1030 + number * 30), *range(8, 16)),
        )
        for number in range(routines)
    ]
    twin = Routine("src/app/twin.py", "twin.run", project[0].shape)
    after = snap([*project, twin])

    started = time.perf_counter()
    findings, _ = run(after, affected=[twin])
    elapsed = time.perf_counter() - started

    assert [finding.details["family_size"] for finding in findings] == [2]
    assert elapsed < 1.0, elapsed
