"""The duplicate-block rule: lines this project already has, somewhere else (req 5.1-5.8).

**This is the half of the family that needs no reference resolution**, and the tests are
built to hold that line. Every case below is a statement about *lines*, never about what
calls what: the snapshots carry a token index and nothing else, so a test that started
passing because a reference appeared would be a test measuring the wrong rule.

The line hashes are short opaque strings rather than real SHA-256 prefixes. The rule never
inverts a hash and never reads one as text -- it compares them with other hashes of the same
index -- so ``"b00"`` and a sixteen-character hex prefix exercise exactly the same code, and
a reader can see at a glance which lines a fixture means to be equal.

Each fixture is built so the two answers *differ*: the eleven-line block differs from the
twelve-line one by one line and by nothing else, the ignored-source case differs from the
reported one by one pattern, and the file with no twin is the same shape as the file with
one. A fixture where both answers agree by construction would assert nothing.

``code_lines`` takes a ``step`` for that same reason. With consecutive line numbers a file's
line numbers and its code-line *indices* are the same integers, so a test built only on
``step=1`` passes whichever of the two the rule reports. The step fixtures separate them on
both sides -- once for the file a finding is *about*
(``test_the_range_uses_the_file_s_own_line_numbers``) and once for the file a finding
*names* (``test_the_named_location_carries_the_named_file_s_own_line_number``).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

import pytest

from scitools_hook.analysis.lean import duplicates
from scitools_hook.analysis.lean.duplicates import (
    DUPLICATE_RULE,
    NAMED_LOCATIONS,
    find_duplicate_blocks,
)
from scitools_hook.config.models import LeanRules
from scitools_hook.models.findings import Finding
from scitools_hook.models.snapshot import ProjectSnapshot, TokenIndex

MIN_LINES: Final = LeanRules().duplicates_min_lines
A: Final = "src/app/a.py"
B: Final = "src/app/b.py"
C: Final = "src/app/c.py"

BLOCK: Final[tuple[str, ...]] = tuple(f"b{index:02d}" for index in range(MIN_LINES))
"""Twelve code lines that hash equal wherever they stand: the shortest reportable block."""

SHORT: Final[tuple[str, ...]] = BLOCK[:-1]
"""The same block one line short, which is the whole difference between two answers."""


def unique(marker: str, count: int) -> tuple[str, ...]:
    """``count`` line hashes no other fixture line shares."""
    return tuple(f"{marker}{index:02d}" for index in range(count))


def code_lines(texts: Sequence[str], first: int = 1, step: int = 1) -> list[tuple[int, str]]:
    """One ``(line number, hash)`` pair per code line, as the worker records them.

    ``step`` above 1 is the shape a file with comments and blank lines has: a line with
    nothing left on it is absent from the index, so consecutive *code* lines need not carry
    consecutive line numbers, and the rule's windows run over the pairs rather than over the
    numbers.
    """
    return [(first + index * step, text) for index, text in enumerate(texts)]


def index(files: dict[str, list[tuple[int, str]]], unreadable: Sequence[str] = ()) -> TokenIndex:
    """A token index carrying only what the duplicate-block rule reads."""
    return TokenIndex(vocabulary=[], files=files, routines={}, unreadable=list(unreadable))


def snap(tokens: TokenIndex | None) -> ProjectSnapshot:
    """An after snapshot whose only content is the token index."""
    return ProjectSnapshot(side="after", tokens=tokens)


def three_copies() -> ProjectSnapshot:
    """The same twelve lines in three files, each with a unique tail of its own."""
    return snap(
        index(
            {
                A: code_lines([*BLOCK, *unique("a", 4)]),
                B: code_lines([*unique("p", 3), *BLOCK]),
                C: code_lines([*unique("q", 7), *BLOCK, *unique("r", 2)]),
            }
        )
    )


def run(
    after: ProjectSnapshot,
    affected: Sequence[str] = (A,),
    min_lines: int = MIN_LINES,
    ignore: Sequence[str] = (),
) -> tuple[list[Finding], tuple[str, ...]]:
    """The rule's two halves, so a test can assert on either without unpacking noise."""
    outcome = find_duplicate_blocks(after, affected, "warning", min_lines, ignore)
    return outcome.findings, outcome.unavailable


# --- the finding ------------------------------------------------------------------------


def test_a_block_in_three_files_is_reported_against_the_affected_one() -> None:
    """Requirement 5.1: the affected file, its line range, and where the copies are."""
    findings, notes = run(three_copies())
    assert notes == ()
    assert len(findings) == 1
    finding = findings[0]
    assert finding.rule == DUPLICATE_RULE
    assert finding.path == A
    assert finding.line == 1
    assert finding.details["end_line"] == MIN_LINES
    assert finding.value == float(MIN_LINES)
    assert finding.details["also_at"] == [f"{B}:4", f"{C}:8"]


def test_the_message_names_the_range_and_the_other_locations() -> None:
    """The finding a reader sees says which lines, and which other files hold them.

    Both copies fit, so the message ends at the second one: a reader shown everything there
    is must not be told there is more, and ", and 0 more" is the shape that says it.
    """
    findings, _ = run(three_copies())
    message = findings[0].message
    assert f"{A}:1-{MIN_LINES}" in message
    assert f"{B}:4" in message
    assert f"{C}:8" in message
    assert message.endswith(f"{C}:8")
    assert "more" not in message


def test_only_the_affected_file_is_reported() -> None:
    """Requirement 5.3: the decision is project-wide, the report is about the change."""
    findings, _ = run(three_copies(), affected=(A,))
    assert [finding.path for finding in findings] == [A]


def test_a_change_touching_none_of_the_copies_is_told_nothing() -> None:
    """Requirement 5.3's other half: no affected copy, no finding."""
    after = snap(
        index({A: code_lines(unique("a", 20)), B: code_lines(BLOCK), C: code_lines(BLOCK)})
    )
    findings, notes = run(after, affected=(A,))
    assert findings == []
    assert notes == ()


def test_severity_error_blocks_and_warning_does_not() -> None:
    """The severity the operator chose decides blocking, as on every structural rule."""
    outcome = find_duplicate_blocks(three_copies(), (A,), "error", MIN_LINES, ())
    assert outcome.findings[0].severity == "error"
    assert outcome.findings[0].blocking is True
    warned, _ = run(three_copies())
    assert warned[0].blocking is False


# --- the length that makes a block ------------------------------------------------------


def test_a_block_one_line_short_is_not_reported() -> None:
    """The configured minimum is the minimum: eleven identical lines are not a finding.

    The collision margin the 64-bit line hash buys is spent by widening what a match means.
    This fixture differs from :func:`three_copies` by exactly one line.
    """
    after = snap(
        index(
            {
                A: code_lines([*SHORT, *unique("a", 4)]),
                B: code_lines([*unique("p", 3), *SHORT]),
                C: code_lines([*unique("q", 7), *SHORT]),
            }
        )
    )
    findings, notes = run(after)
    assert findings == []
    assert notes == ()


def test_a_single_repeated_line_is_never_a_finding() -> None:
    """One line standing in fifty files is a language's punctuation, not a duplicate."""
    same = "closing-brace"
    files = {path: code_lines([*unique(path[-4], 5), same]) for path in (A, B, C)}
    findings, _ = run(snap(index(files)))
    assert findings == []


def test_a_file_shorter_than_the_minimum_yields_no_window() -> None:
    """A file with fewer code lines than the minimum cannot hold a block at all."""
    after = snap(index({A: code_lines(BLOCK[:3]), B: code_lines(BLOCK[:3])}))
    findings, _ = run(after)
    assert findings == []


def test_the_minimum_is_the_configured_one_and_not_a_constant() -> None:
    """Requirement 5.5: ``duplicates_min_lines`` decides, so the same fixture flips."""
    after = snap(index({A: code_lines(SHORT), B: code_lines(SHORT)}))
    assert run(after, min_lines=MIN_LINES)[0] == []
    assert len(run(after, min_lines=len(SHORT))[0]) == 1


@pytest.mark.parametrize("min_lines", [0, 1])
def test_a_minimum_below_two_is_refused(min_lines: int) -> None:
    """A window of nothing matches the whole project; a window of one line is one line.

    ``duplicates_min_lines`` ships ``ge=3``, so no operator reaches either -- but the
    function takes the number as an argument, and at zero every window is the empty tuple and
    equal to every other, which would report every file in the project as a copy of every
    other.
    """
    after = snap(index({A: code_lines(BLOCK), B: code_lines(BLOCK)}))
    with pytest.raises(ValueError, match="min_lines"):
        run(after, min_lines=min_lines)


def test_an_ordinary_paste_below_itself_is_still_reported_from_both_ends() -> None:
    """The claim the range filter's docstring makes, which nothing else would bind.

    The filter silences a range whose only repetitions lie inside it, and a reader's first
    worry is whether that also swallows a plain copy-paste. It does not, and the reason is
    the period: a block pasted directly below itself repeats with period ``min_lines``
    exactly, so its shifted windows match nothing and each copy stands wholly outside the
    other's range. Only a period STRICTLY BELOW the minimum folds a run onto itself.
    """
    after = snap(index({A: code_lines([*BLOCK, *BLOCK])}))

    findings, _ = run(after)

    assert [(finding.line, finding.details["also_at"]) for finding in findings] == [
        (1, [f"{A}:{MIN_LINES + 1}"]),
        (MIN_LINES + 1, [f"{A}:1"]),
    ]


@pytest.mark.parametrize("min_lines", [0, 1])
def test_a_refused_minimum_is_refused_before_the_snapshot_is_read(min_lines: int) -> None:
    """The docstring's stated reason for the guard's position, which nothing else binds.

    A snapshot with no index answers the unavailable message, so a guard moved below that
    return would answer "this metric was unavailable" to a caller that passed a nonsensical
    window length -- reporting the caller's bug as a missing measurement. Both answers are
    reachable and they differ, which is why the position is asserted and not just the raise.
    """
    with pytest.raises(ValueError, match="min_lines"):
        run(snap(None), min_lines=min_lines)


def test_a_minimum_of_two_is_accepted_and_reports() -> None:
    """The floor the error text names, bound as ACCEPTED rather than only as refused.

    ``test_a_minimum_below_two_is_refused`` pins zero and one. Without this case the guard
    could read ``< 3`` -- agreeing with every refusal above it, contradicting its own message
    and the module docstring, and silently raising on a number both call them the floor.
    """
    pair = unique("t", 2)
    after = snap(index({A: code_lines(pair), B: code_lines(pair)}))

    findings, _ = run(after, min_lines=2)

    assert [finding.details["also_at"] for finding in findings] == [[f"{B}:1"]]


# --- maximal runs -----------------------------------------------------------------------


def test_a_long_block_is_one_finding_over_its_whole_range() -> None:
    """A twenty-line copy is one finding of twenty lines, not nine overlapping ones."""
    long_block = unique("z", 20)
    after = snap(index({A: code_lines(long_block), B: code_lines(long_block)}))
    findings, _ = run(after)
    assert len(findings) == 1
    assert findings[0].line == 1
    assert findings[0].details["end_line"] == 20
    assert findings[0].value == 20.0


def test_two_separated_blocks_are_two_findings() -> None:
    """Two maximal runs in one file are two findings, each with its own range."""
    first, second = unique("y", MIN_LINES), unique("z", MIN_LINES)
    both = [*first, *unique("a", 5), *second]
    files = {A: code_lines(both), B: code_lines(first), C: code_lines(second)}
    findings, _ = run(snap(index(files)))
    ranges = [(finding.line, finding.details["end_line"]) for finding in findings]
    assert ranges == [(1, MIN_LINES), (MIN_LINES + 6, MIN_LINES * 2 + 5)]


def test_the_range_uses_the_file_s_own_line_numbers() -> None:
    """Comments and blank lines are absent, so code lines carry gaps a reader can open."""
    after = snap(index({A: code_lines(BLOCK, first=10, step=2), B: code_lines(BLOCK)}))
    findings, _ = run(after)
    assert findings[0].line == 10
    assert findings[0].details["end_line"] == 10 + 2 * (MIN_LINES - 1)
    assert findings[0].value == float(MIN_LINES)


def test_the_named_location_carries_the_named_file_s_own_line_number() -> None:
    """The *other* file's gaps count too: a location is its line, never its index.

    ``B``'s copy is the fourth code line of a file whose code lines sit ten apart, so its
    line number is 31 and its index is 3. A rule recording the index would name ``b.py:4``,
    a line that in this file holds nothing, and every assertion about the *reported* file
    would still pass -- which is why this case is asserted from the named side.
    """
    after = snap(index({A: code_lines(BLOCK), B: code_lines([*unique("p", 3), *BLOCK], step=10)}))
    findings, _ = run(after)
    assert len(findings) == 1
    assert findings[0].details["also_at"] == [f"{B}:31"]
    assert f"{B}:31" in findings[0].message


# --- a copy in the affected file itself --------------------------------------------------


def test_a_block_repeated_inside_one_file_is_reported_from_both_ends() -> None:
    """More than one location in the project includes two locations in one file."""
    after = snap(index({A: code_lines([*BLOCK, *unique("a", 5), *BLOCK])}))
    findings, _ = run(after)
    assert [finding.line for finding in findings] == [1, MIN_LINES + 6]
    assert findings[0].details["also_at"] == [f"{A}:{MIN_LINES + 6}"]
    assert findings[1].details["also_at"] == [f"{A}:1"]


def test_a_gappy_file_holding_the_block_twice_names_lines_and_not_indices() -> None:
    """The same two copies in a file whose code lines sit three apart.

    The self-exclusion compares a window's recorded location against the position it was
    built from, so both sides of that comparison have to be the file's own line numbers. A
    rule recording indices would exclude ``a.py:52`` while having recorded ``a.py:18``, so
    the first copy would name a line inside its own range and be dropped, and the second
    would name itself as well as its twin.
    """
    after = snap(index({A: code_lines([*BLOCK, *unique("a", 5), *BLOCK], step=3)}))
    findings, _ = run(after)
    assert [finding.line for finding in findings] == [1, 52]
    assert findings[0].details["also_at"] == [f"{A}:52"]
    assert findings[1].details["also_at"] == [f"{A}:1"]


def test_a_run_that_repeats_only_inside_its_own_range_is_no_finding() -> None:
    """Twenty-four identical lines standing nowhere else are one range, not two locations.

    Every one of the thirteen windows matches the other twelve, and every position they
    match is inside ``a.py:1-24``. Requirement 5.1 asks for "the other locations of the same
    lines"; a line a reader already has open is not another location, and there is no second
    copy to delete. Left in, they read as thirteen copies of a file that has one run.
    """
    after = snap(index({A: code_lines(["same"] * (MIN_LINES * 2))}))
    findings, notes = run(after)
    assert findings == []
    assert notes == ()


def test_an_overlapping_run_names_the_other_file_and_nothing_inside_its_own_range() -> None:
    """The damaging half: the one actionable location must not be crowded out.

    The same twenty-four lines stand in ``a.py`` and in ``b.py``. Every window of the run in
    ``a.py`` matches twelve positions in ``a.py`` and thirteen in ``b.py``, and the message
    names three. Sorted by path, the ``a.py`` positions come first and take all three, so
    ``b.py`` -- the only place a reader has to go -- is never said at all.
    """
    same = ["same"] * (MIN_LINES * 2)
    after = snap(index({A: code_lines(same), B: code_lines(same)}))
    findings, _ = run(after, affected=(A,))
    assert len(findings) == 1
    finding = findings[0]
    assert (finding.line, finding.details["end_line"]) == (1, MIN_LINES * 2)
    also_at = finding.details["also_at"]
    assert also_at == [f"{B}:1", f"{B}:2", f"{B}:3"]
    assert f"{B}:1" in finding.message
    inside = {f"{A}:{line}" for line in range(1, MIN_LINES * 2 + 1)}
    assert not inside.intersection(also_at)
    # The message opens with the range itself, so only its tail is a list of locations.
    listed = finding.message.split("also holds at ", 1)[1]
    assert not any(named in listed for named in inside)


def test_a_copy_starting_on_the_range_s_last_line_is_not_an_other_location() -> None:
    """The far end of the filter, which a period-eleven file reaches exactly.

    Twenty-three code lines repeating every eleven give exactly two matched windows, the
    one at line 1 and the one at line 12, each the other's only match. The first is the run
    ``1-12``, and the one place it could name is line 12 -- its own last line, inside the
    range the reader was already told to open -- so it names nothing and is not reported.
    The second run, ``12-23``, reaches back to line 1, which is outside it, and that is the
    finding that carries the reader somewhere new.
    """
    period = MIN_LINES - 1
    repeating = [f"p{number % period:02d}" for number in range(2 * period + 1)]
    findings, _ = run(snap(index({A: code_lines(repeating)})))
    assert [(f.line, f.details["end_line"]) for f in findings] == [(MIN_LINES, 2 * period + 1)]
    assert findings[0].details["also_at"] == [f"{A}:1"]


def test_a_file_whose_lines_stand_nowhere_else_is_silent() -> None:
    """A window is never its own other location: a unique file reports nothing.

    Without the self-exclusion every file long enough would match itself and the rule would
    report the whole project, so this is the case that keeps the guard honest.
    """
    after = snap(index({A: code_lines(unique("a", 40)), B: code_lines(unique("b", 40))}))
    findings, notes = run(after)
    assert findings == []
    assert notes == ()


# --- the named locations -----------------------------------------------------------------


def test_at_most_three_other_locations_are_named_and_the_rest_are_counted() -> None:
    """Requirement 5.1: a fixed number of locations named, and the reader told of the rest."""
    paths = [f"src/app/copy{number}.py" for number in range(6)]
    files = {path: code_lines(BLOCK) for path in paths}
    findings, _ = run(snap(index(files)), affected=(paths[0],))
    also_at = findings[0].details["also_at"]
    assert isinstance(also_at, list)
    assert len(also_at) == NAMED_LOCATIONS
    assert also_at == [f"{path}:1" for path in paths[1:4]]
    assert "2 more" in findings[0].message


def test_the_locations_are_ordered_by_path_and_line() -> None:
    """One order, so two runs over the same project produce the same finding."""
    files = {
        A: code_lines(BLOCK),
        C: code_lines([*unique("q", 2), *BLOCK]),
        B: code_lines([*unique("p", 5), *BLOCK]),
    }
    findings, _ = run(snap(index(files)))
    assert findings[0].details["also_at"] == [f"{B}:6", f"{C}:3"]


def test_the_affected_files_are_reported_in_path_order() -> None:
    """The other order a reader sees, and the one the JSON document depends on.

    ``report.json_out`` re-sorts nothing on purpose, on the stated ground that the analysis
    layer already emits a settled order, so the same run renders byte-identically in any
    process. Six affected files each holding the block is what that promise costs: iterated
    as a set, these paths come out in an order Python randomises per process.
    """
    paths = [f"src/app/dup{number}.py" for number in range(6)]
    findings, _ = run(
        snap(index({path: code_lines(BLOCK) for path in paths})), affected=paths[::-1]
    )
    assert [finding.path for finding in findings] == paths


# --- ignored paths -----------------------------------------------------------------------


def test_an_ignored_affected_file_is_not_reported() -> None:
    """Requirement 5.5: the ignore list silences the file the change touched."""
    findings, notes = run(three_copies(), ignore=["src/app/a.py"])
    assert findings == []
    assert notes == ()


def test_an_ignored_file_is_not_a_location_either() -> None:
    """Ignored paths contribute *neither* side: the only copies are excluded, so nothing.

    An ignore list applied to the report but not to the index would leave this finding
    standing, naming no location a reader may see.
    """
    findings, _ = run(three_copies(), ignore=["src/app/b.py", "src/app/c.py"])
    assert findings == []


def test_an_ignored_directory_still_leaves_the_unignored_copies() -> None:
    """The rule loses only what the pattern covers, and reports the rest."""
    after = snap(
        index(
            {
                A: code_lines(BLOCK),
                B: code_lines(BLOCK),
                "vendor/lib/c.py": code_lines(BLOCK),
            }
        )
    )
    findings, _ = run(after, ignore=["vendor"])
    assert len(findings) == 1
    assert findings[0].details["also_at"] == [f"{B}:1"]


def test_an_affected_path_the_index_never_saw_is_skipped() -> None:
    """A changed file with no line hashes -- a binary, a fixture, an unreadable one."""
    findings, _ = run(three_copies(), affected=(A, "docs/guide.md"))
    assert [finding.path for finding in findings] == [A]


# --- what could not be measured -----------------------------------------------------------


def test_no_index_yields_the_unavailable_message_and_no_findings() -> None:
    """Requirement 5.8: ``tokens`` of ``None`` is 'not asked', never 'nothing duplicated'."""
    findings, notes = run(snap(None))
    assert findings == []
    assert len(notes) == 1
    assert notes[0].startswith(f"{DUPLICATE_RULE} is on, but this snapshot carries no ")
    assert "db rebuild" in notes[0]


def test_an_unreadable_file_is_noted_once_and_is_a_duplicate_of_nothing() -> None:
    """Requirement 5.8: a file that was never lexed is said, not silently absent."""
    after = snap(index({A: code_lines(BLOCK), B: code_lines(BLOCK)}, unreadable=["src/odd.py"]))
    findings, notes = run(after)
    assert len(findings) == 1
    assert len(notes) == 1
    assert "src/odd.py" in notes[0]
    assert notes[0].startswith(DUPLICATE_RULE)
    assert "more" not in notes[0]


def test_many_unreadable_files_name_three_and_count_the_rest() -> None:
    """The note stays one line however many files refused."""
    refused = [f"src/odd{number}.py" for number in range(5)]
    after = snap(index({A: code_lines(BLOCK)}, unreadable=refused))
    _, notes = run(after)
    assert len(notes) == 1
    for path in refused[:NAMED_LOCATIONS]:
        assert path in notes[0]
    assert "2 more" in notes[0]
    assert refused[4] not in notes[0]


def test_an_ignored_unreadable_file_is_not_noted() -> None:
    """An ignored path contributes nothing, so its lexer refusing is not the run's news."""
    after = snap(index({A: code_lines(BLOCK)}, unreadable=["vendor/lib/odd.py"]))
    _, notes = run(after, ignore=["vendor"])
    assert notes == ()


def test_the_unreadable_note_and_the_unavailable_message_are_not_the_same_answer() -> None:
    """One says the run could not judge; the other says one file could not be read."""
    missing = run(snap(None))[1]
    refused = run(snap(index({A: code_lines(BLOCK)}, unreadable=["src/odd.py"])))[1]
    assert missing != refused


def test_the_location_map_holds_only_the_keys_the_change_carries() -> None:
    """Requirement 9.5: what the pass *keeps* is sized by the change, not by the project.

    The answer is the same either way -- a project-wide map would produce these findings and
    no others -- so only the map itself says whether the promise in the module docstring is
    kept. Forty windows of an unaffected file are what a project-wide map would add here.
    """
    considered = {A: code_lines(BLOCK), B: code_lines(unique("b", 40))}
    found = duplicates._occurrences(considered, [A], MIN_LINES)
    assert set(found) == set(duplicates._window_keys(considered[A], MIN_LINES))
    assert len(found) == 1


def test_a_change_with_no_long_enough_window_walks_no_other_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No affected window can match, so the whole-project walk is not paid for.

    The answer is the empty list either way, so the absence of a finding proves nothing
    about whether the work ran. A call spy on the window builder is what says so: the
    affected file has three code lines and the other two have twelve, and only the three
    ever reach it.
    """
    after = snap(index({A: code_lines(BLOCK[:3]), B: code_lines(BLOCK), C: code_lines(BLOCK)}))
    walked: list[int] = []
    real = duplicates._window_keys

    def spy(pairs: Sequence[tuple[int, str]], min_lines: int) -> list[tuple[str, ...]]:
        walked.append(len(pairs))
        return real(pairs, min_lines)

    monkeypatch.setattr(duplicates, "_window_keys", spy)
    findings, _ = run(after, affected=(A,))
    assert findings == []
    assert walked, "the affected file's own windows are still built"
    assert all(count == 3 for count in walked), walked


# --- cost ----------------------------------------------------------------------------------


@pytest.mark.parametrize("files", [316])
def test_the_whole_project_pass_is_fast_at_this_repository_s_scale(files: int) -> None:
    """Requirement 9.5: the cost is linear in the project, not quadratic in its files.

    316 files of about 350 code lines is this repository's own scale, roughly 110 000 lines.
    The affected set is one file, as a commit's usually is. An ``O(files squared)`` compare
    would take minutes here.
    """
    import time

    project = {
        f"src/pkg{number // 20}/mod{number}.py": code_lines(unique(f"m{number}-", 350))
        for number in range(files)
    }
    project[A] = code_lines([*BLOCK, *unique("a", 338)])
    project[B] = code_lines([*unique("p", 100), *BLOCK, *unique("q", 238)])
    started = time.perf_counter()
    findings, _ = run(snap(index(project)))
    elapsed = time.perf_counter() - started
    assert len(findings) == 1
    assert elapsed < 2.0, f"{elapsed:.3f}s over {files} files"
