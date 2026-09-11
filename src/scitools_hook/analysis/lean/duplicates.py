"""The duplicate-block rule: a run of lines this project already holds somewhere else.

Requirements 5.1, 5.3, 5.5 and 5.8. The finding is one affected file's line range and up to
:data:`NAMED_LOCATIONS` other places the same lines stand.

**This rule takes no trust gate, and the two floors are declined one at a time.**
:class:`~scitools_hook.analysis.lean.dead.TrustGate` bounds the dead-code rules with two
numbers, and requirement 1.8's amendment is explicit that neither covers the other's
evidence. An argument that refutes one therefore does not refute the other, so each is
answered on its own ground:

* **The call-resolution floor** bounds whether the analyser knew what calls what. It is the
  guard behind the 830 routines a naive dead-code predicate answers on a structurally typed
  codebase, where an implementation holds no reference to the interface it satisfies. This
  rule reads no reference of any kind. It compares line hashes produced by
  ``Ent.lexer(False)``, a lexical pass that resolves nothing, so there is no resolved
  quantity for a floor to bound. Declining it costs nothing because it constrains nothing
  here.
* **The accuracy floor** bounds whether a file was read at all -- the failure behind the
  sixteen module bindings this repository reports unreferenced while every one of them is
  read, their use sites sitting in regions the analysis errored on. That failure is specific
  to an **absence** claim: "nothing references this" is only as good as the run's coverage of
  everywhere a reference could have been, so a partly read project manufactures absences.
  This rule makes a **presence** claim -- these lines stand here, and also there -- and a
  presence claim is carried by the two occurrences it names. A region the semantic analysis
  could not resolve still yields lexemes; a file whose lexer refused outright is in
  :attr:`~scitools_hook.models.snapshot.TokenIndex.unreadable` and is *named* in a note
  rather than being silently read as a file with nothing in it (requirement 5.8). Low
  accuracy can therefore make this rule quieter than the code deserves. It cannot make it
  say something false, which is what the floor exists to prevent.

**What a match is allowed to mean, and why it may not be widened.** A line hash is a 64-bit
truncated SHA-256 (``worker_lean.LINE_HASH_CHARS``). At a million distinct code lines the
birthday bound on any collision at all is about 2.7e-8, and a false finding needs
``min_lines`` of them to collide *in aligned order* on top of that. That margin is the whole
reason a hash comparison may be reported as a fact about code. It is spent by loosening what
counts as a match, so this module reports no window shorter than the configured minimum and
never reports a single matching line: ``test_a_block_one_line_short_is_not_reported`` and
``test_a_single_repeated_line_is_never_a_finding`` stand on those two.

**Cost, which requirement 9.5 caps.** The pass is linear in the project's code lines and
never compares a file with a file. One key per window of ``min_lines`` consecutive code
lines is built for the affected files first, and the whole-project walk records a location
only for a key some affected file already carries -- so the map that is *kept* follows the
*change* while the walk that fills it follows the project. That is ``O(L * min_lines)``
window work for ``L`` code lines, with no term in the number of files squared, and no
whole-project window walk at all when the change carries no window long enough to match.

Measured on a synthetic index at this repository's scale -- 318 files, 111 300 code lines,
``min_lines`` 12 -- with the whole pass timed end to end:

===================  ==========  =========
affected files       time        peak heap
===================  ==========  =========
0 with a window      0.7 ms      -
1                    157 ms      0.1 MB
20                   272 ms      3.6 MB
all 318              1 695 ms    58 MB
===================  ==========  =========

Best of three on this machine, ``min_lines`` 12. A commit is the second row. The last is not
a commit and is recorded only to show the shape: the growth is in what the map *keeps*,
which is the change, and not in the walk.
``test_the_whole_project_pass_is_fast_at_this_repository_s_scale`` holds the first row under
two seconds, which is loose on purpose -- it is a guard against an accidental quadratic, not
a benchmark, and a tight bound on a shared machine is a flaky test.

**Two occurrences in one file count.** Requirement 5.1 asks about a run of lines occurring at
more than one location *in the project*, and a file that holds the same twelve lines twice
holds them at two locations. Both copies are reported when the file is affected, each naming
the other, because either one is a place the fix has to touch. The self-exclusion is by
position and not by path: a window is never its own other location, which
``test_a_file_whose_lines_stand_nowhere_else_is_silent`` is the guard for -- without it every
file long enough would match itself and the rule would report the whole project.

**A run that overlaps itself names nothing.** Twenty-four identical code lines -- a table of
``{0, 0, 0, 0},`` rows, a column of ``pass``, any run of ``2 * min_lines - 1`` or more lines
periodic with a period below ``min_lines`` -- make every window match every other, and the
positions they match are the run's own. Those are dropped: a location whose path is the
reported file and whose line falls inside the reported range is not one of requirement 5.1's
"other locations of the same lines", because it is a line the reader already has open. The
finding for ``a.py:1-24`` may not name ``a.py:7``.

Dropping them can leave nothing to name, and then there is **no finding**. The alternative --
a second message saying that this one range repeats itself -- was declined on three grounds.
Requirement 5.1 is about lines standing at more than one *location*, and a periodic run
stands in one contiguous range that a reader opens once. The rule's remediation is to delete
a copy and keep one, and a table of identical rows holds no separable copy to delete. And
nothing actionable is lost: a run that *also* stands somewhere else keeps that location and is
still reported. Only the self-references go, and they were the problem. Before the filter, the
same twenty-four lines in ``a.py`` **and** ``b.py`` reported ``also holds at a.py:1, a.py:2,
a.py:3, and 23 more`` -- thirteen of those twenty-six locations were in ``b.py``, the three
the message had room for were not, and the one place a reader had to go was never said.
``test_an_overlapping_run_names_the_other_file_and_nothing_inside_its_own_range`` is the
guard, and :func:`_message` takes its first location as a parameter of its own so that
"``also holds at``" followed by nothing is not a string this module can build.

What the silence covers is narrower than "a block that repeats itself", and the difference is
the part worth knowing: a run goes quiet only when its period is **strictly below**
``min_lines`` and it stands nowhere else. An ordinary paste of a block immediately below
itself has period ``min_lines`` exactly, its shifted windows match nothing, and it is still
reported from both ends -- ``a.py:1-12 ... also holds at a.py:13`` and its mirror. A six-line
unit repeated four times is silent because six is below the configured minimum the rule may
not report at any location, here or anywhere. So no copy-paste a reader could act on is
swallowed by this filter; what it removes is a range whose only repetitions are inside
itself.

**``min_lines`` is checked rather than trusted.** Below two the rule stops being the rule: at
zero every window is the empty tuple, equal to every other window in the project, so every
file would be reported as a copy of every other; at one it reports single matching lines,
which the collision margin above forbids. ``duplicates_min_lines`` ships ``ge=3`` so no
configured value reaches either, but the number arrives here as an argument and a caller is
not the settings model, so it is refused with ``ValueError``.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import Final, NamedTuple

from scitools_hook.analysis.lean.dead import LeanOutcome, unavailable
from scitools_hook.config.models import LeanRules, Severity, matching_pattern
from scitools_hook.models.findings import Finding, structure_rule
from scitools_hook.models.snapshot import ProjectSnapshot, TokenIndex

DUPLICATE_RULE: Final = structure_rule("duplicate_block")

NAMED_LOCATIONS: Final = 3
"""How many other places one finding names, and how many files the unreadable note names.

Requirement 5.1 asks for "up to a fixed number of locations named". Three, because the
message has to stay one line an agent reads, and the count of the rest is carried with them
so a reader is never told about two copies when there are eleven.
"""

DEFAULT_MIN_LINES: Final = LeanRules().duplicates_min_lines
"""``duplicates_min_lines`` as the settings model ships it, so a caller with no operator
configuration in hand -- a test, a probe -- uses the shipped number rather than a second
copy of it."""

_NO_INDEX: Final = "line hashes for the project's files"
_COMPARED: Final = "compared for duplication"


class _Location(NamedTuple):
    """One window's position: the file it is in and the line it starts on.

    Position and not path, because the self-exclusion below compares locations: a file's
    line numbers are unique within it, so ``(path, line)`` identifies a window exactly.
    """

    path: str
    line: int

    def __str__(self) -> str:
        """``path:line``, the form the message and ``details["also_at"]`` both carry."""
        return f"{self.path}:{self.line}"


class _Block(NamedTuple):
    """One maximal run of duplicated windows in one file, ready to become a finding."""

    start: int
    end: int
    lines: int
    elsewhere: tuple[_Location, ...]


def find_duplicate_blocks(
    after: ProjectSnapshot,
    affected_files: Collection[str],
    severity: Severity = "warning",
    min_lines: int = DEFAULT_MIN_LINES,
    ignore: Sequence[str] = (),
) -> LeanOutcome:
    """Report each affected file's runs of lines the project holds elsewhere (req 5.1).

    ``after`` is the after side alone, so a file the change deleted has no index entry and
    cannot be reported. ``affected_files`` is the change's own file set, which is the only
    query set: duplication is decided over the whole project and reported against the change
    (5.3). ``ignore`` is a list of path globs in the language ``[project] include`` speaks,
    and an ignored path contributes **neither** side -- it is neither reported nor named as
    somewhere a copy stands (5.5).

    A ``tokens`` of ``None`` is "the token pass was never asked for", never "this project has
    no duplicates", so it yields the run's one unavailable message and no findings (5.8).

    ``min_lines`` below two raises: see the module docstring. It is checked before the
    snapshot, because a nonsensical window length is the caller's bug either way and saying
    "this metric was unavailable" about it would hide it.
    """
    if min_lines < 2:
        raise ValueError(f"min_lines must be at least 2, not {min_lines}")
    index = after.tokens
    if index is None:
        return unavailable(DUPLICATE_RULE, _NO_INDEX, _COMPARED)
    considered = _considered(index, ignore)
    subjects = sorted(path for path in set(affected_files) if path in considered)
    occurrences = _occurrences(considered, subjects, min_lines)
    findings = [
        _finding(path, block, severity)
        for path in subjects
        for block in _file_blocks(considered[path], occurrences, path, min_lines)
    ]
    return LeanOutcome(findings=findings, unavailable=_unreadable_note(index, ignore))


def _considered(index: TokenIndex, ignore: Sequence[str]) -> dict[str, list[tuple[int, str]]]:
    """Every indexed file the operator did not exclude.

    One filtered map serves both sides of the rule -- the files that may be reported and the
    files that may be named as a location -- because requirement 5.5 says an ignored path
    contributes neither, and two filters would let one of them drift.

    Its iteration order is deliberately not part of its contract, and it does not sort. The
    two orders a reader sees are decided elsewhere and unconditionally: findings come out in
    the order of ``subjects``, which is sorted, and a finding's other locations in the order
    :func:`_block` sorts them into. A sort here would be a line no mutation of it could
    change, which is a line that reads like a guarantee and is not one.
    """
    return {
        path: pairs for path, pairs in index.files.items() if matching_pattern(ignore, path) is None
    }


def _window_keys(pairs: Sequence[tuple[int, str]], min_lines: int) -> list[tuple[str, ...]]:
    """One key per window of ``min_lines`` consecutive **code** lines, in file order.

    Consecutive in the index rather than in the file: a line left with nothing on it once
    whitespace and comments are gone is absent from ``pairs``, which is requirement 5.4's
    "whitespace and comments treated as absent" and the reason a copy separated by a comment
    block still compares equal. A file with fewer code lines than the minimum yields no
    window at all, which is where the configured minimum is enforced.

    The key is the tuple of the window's hashes rather than a string joining them. It
    compares element by element, so two windows are equal exactly when their lines are, with
    no join for a boundary to be ambiguous across -- and it is the cheaper of the two:
    measured over 111 300 code lines, tuples build the project's keys in 66 ms against 99 ms
    for a joined string and 223 ms for the generator form this started as.
    """
    hashes = [line_hash for _, line_hash in pairs]
    return [
        tuple(hashes[offset : offset + min_lines]) for offset in range(len(hashes) - min_lines + 1)
    ]


def _occurrences(
    considered: Mapping[str, list[tuple[int, str]]], subjects: Sequence[str], min_lines: int
) -> dict[tuple[str, ...], list[_Location]]:
    """Every location of every window some affected file carries.

    Two passes rather than one whole-project map, and the second pass is the cheap half of
    the promise in the module docstring: the keys worth remembering are the ones an affected
    file has, so the map that is *kept* is sized by the change while the walk that fills it
    is sized by the project. A whole-project map answers identically and costs the project's
    line count in memory on every check.

    A location carries ``pairs[offset][0]``, the file's own line number, and not ``offset``,
    the index of the window among this file's code lines. They are the same integer only in a
    file with no comments and no blank lines. ``_matches`` compares a recorded location
    against the position it was built from, so recording the index would break the
    self-exclusion in any file with a gap as well as naming lines that hold nothing:
    ``test_the_named_location_carries_the_named_file_s_own_line_number`` and
    ``test_a_gappy_file_holding_the_block_twice_names_lines_and_not_indices`` hold both ends.
    """
    wanted = {key for path in subjects for key in _window_keys(considered[path], min_lines)}
    if not wanted:
        return {}
    found: dict[tuple[str, ...], list[_Location]] = {}
    for path, pairs in considered.items():
        for offset, key in enumerate(_window_keys(pairs, min_lines)):
            if key in wanted:
                found.setdefault(key, []).append(_Location(path, pairs[offset][0]))
    return found


def _file_blocks(
    pairs: Sequence[tuple[int, str]],
    occurrences: Mapping[tuple[str, ...], Sequence[_Location]],
    path: str,
    min_lines: int,
) -> list[_Block]:
    """One block per maximal run of duplicated windows in this file, in line order.

    A run whose every match lies inside its own range comes back with an empty ``elsewhere``
    and is not a finding -- the module docstring argues why that, and not a second kind of
    message. This is also the only place that can drop a block, so every block reaching
    :func:`_finding` has at least one location to name.
    """
    keys = _window_keys(pairs, min_lines)
    matches = _matches(pairs, keys, occurrences, path)
    blocks = (_block(pairs, run, matches, min_lines, path) for run in _runs(matches))
    return [block for block in blocks if block.elsewhere]


def _matches(
    pairs: Sequence[tuple[int, str]],
    keys: Sequence[tuple[str, ...]],
    occurrences: Mapping[tuple[str, ...], Sequence[_Location]],
    path: str,
) -> list[tuple[_Location, ...]]:
    """Per window, the other places its lines stand; empty where they stand only here.

    The exclusion here is of the window's own position and of nothing else, so a second copy
    in this same file counts as another place -- which is what requirement 5.1's "more than
    one location in the project" says. It is per *window*, which is why it is not the whole
    answer: the other windows of a self-overlapping run survive it, and :func:`_block` is
    where they are dropped, because only a whole run knows the range being reported.
    """
    return [
        tuple(
            location
            for location in occurrences.get(key, ())
            if location != _Location(path, pairs[offset][0])
        )
        for offset, key in enumerate(keys)
    ]


def _runs(matches: Sequence[tuple[_Location, ...]]) -> list[list[int]]:
    """The maximal runs of consecutive duplicated windows, as lists of window offsets.

    A twenty-line copy overlaps into nine windows at a minimum of twelve; reporting nine
    findings would tell an agent nine times about one block. One run is one finding.
    """
    runs: list[list[int]] = []
    run: list[int] = []
    for offset, others in enumerate(matches):
        if others:
            run.append(offset)
            continue
        if run:
            runs.append(run)
        run = []
    if run:
        runs.append(run)
    return runs


def _block(
    pairs: Sequence[tuple[int, str]],
    run: Sequence[int],
    matches: Sequence[tuple[_Location, ...]],
    min_lines: int,
    path: str,
) -> _Block:
    """One run's line range, its code-line count, and every other place it stands.

    The range is the file's own line numbers -- the first line of the run's first window to
    the last line of its last -- so a reader opens what the finding names. The count is of
    **code** lines, which is what was compared, and is smaller than the range whenever
    comments or blank lines fall inside it.

    "Other" is taken literally: a location in this same file whose line falls inside
    ``start..end`` is a line the finding is already telling the reader to open, so it is not
    somewhere else and is dropped. A second copy *elsewhere in this file* is outside the
    range by construction and survives, which is the case
    ``test_a_block_repeated_inside_one_file_is_reported_from_both_ends`` holds.
    """
    last = run[-1] + min_lines - 1
    start, end = pairs[run[0]][0], pairs[last][0]
    found = {location for offset in run for location in matches[offset]}
    outside = (place for place in found if place.path != path or not start <= place.line <= end)
    return _Block(start=start, end=end, lines=last - run[0] + 1, elsewhere=tuple(sorted(outside)))


def _named(items: Sequence[object]) -> tuple[list[str], int]:
    """The first :data:`NAMED_LOCATIONS` of a list as text, and how many were left out.

    One decision in one place for the two lists this module caps -- a finding's other
    locations and the note's unreadable files -- because both make the same promise, that a
    reader is never shown three of something without being told there were more.
    """
    named = [str(item) for item in items[:NAMED_LOCATIONS]]
    return named, len(items) - len(named)


def _finding(path: str, block: _Block, severity: Severity) -> Finding:
    """One affected file's duplicated range; the pipeline attaches the ``delete:`` hint."""
    named, rest = _named(block.elsewhere)
    first, *others = named
    return Finding(
        kind="structural",
        rule=DUPLICATE_RULE,
        scope="file",
        path=path,
        line=block.start,
        # The number the rule is about: how many code lines repeat. `limit` stays None
        # because the configured minimum is not a limit this value may not exceed -- it is
        # the length at which a run becomes reportable at all.
        value=float(block.lines),
        limit=None,
        limit_source="rule",
        severity=severity,
        blocking=severity == "error",
        message=_message(path, block, first, others, rest),
        details={"also_at": named, "end_line": block.end},
    )


def _message(path: str, block: _Block, first: str, others: Sequence[str], rest: int) -> str:
    """One line: which lines repeat, how many of them, and where the other copies are.

    The first location is a parameter of its own rather than the head of a list, so this
    function cannot be called with nothing to name and cannot render "``also holds at``"
    followed by nothing. A block with no other location is dropped in :func:`_file_blocks`,
    and the unpacking in :func:`_finding` raises rather than reaching here if it ever is not.
    """
    more = f", and {rest} more" if rest else ""
    return (
        f"{path}:{block.start}-{block.end} repeats {block.lines} code lines this project "
        f"also holds at {', '.join([first, *others])}{more}"
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
        f"{DUPLICATE_RULE} read no token stream for {', '.join(named)}{more}; each of those "
        f"files contributes no duplicates and is reported as a duplicate of nothing",
    )
