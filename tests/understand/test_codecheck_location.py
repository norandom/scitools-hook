"""Where a CodeCheck violation is: the file, its directory, the root, and what is refused.

The parser has to place a violation on a file the gate can name, from up to three path
columns that ``und`` fills in with a consistency it does not promise (task 6.7). Every test
here is one shape of that -- a relative name composing with its directory, a directory that
carries its own separator, an absolute directory beside a root, a drive letter without its
separator -- and every refusal is tested by its outcome rather than by the rule that produced
it. The headers and rows come from ``codecheck_csv``.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest
from codecheck_csv import (
    ANCHORED,
    SMALL_HEADER,
    VIOLATION_HEADER,
    only,
    words_of,
    write_csv,
)

from scitools_hook.errors import AnalysisFailedError
from scitools_hook.understand import codecheck
from scitools_hook.understand.codecheck import read_violations

# --- where the violation is ------------------------------------------------------


def test_a_leaf_file_name_is_anchored_under_its_directory(tmp_path: Path) -> None:
    """``File=util.c`` with ``Directory=/proj/native`` is one path, not a repo-relative one."""
    row = ",Magic number,util.c,/proj/native,,R_01,Magic,42,7,,snip,0,,/proj,,Warning"
    found = only(read_violations(write_csv(tmp_path, f"{VIOLATION_HEADER}\n{row}\n")))
    assert found.path == "/proj/native/util.c"


def test_a_relative_directory_composes_with_the_root_instead_of_being_dropped(
    tmp_path: Path,
) -> None:
    """A named root is exactly this shape: ``Root`` absolute, ``Directory`` relative to it.

    Trying the anchors one at a time discards ``Directory`` the moment its own join is
    still relative and reports ``/proj/util.c`` — a real file, the wrong one, no error.
    """
    header = "CheckID,File,Line,Violation,Directory,Root"
    row = "R_01,util.c,42,message,native,/proj"
    found = only(read_violations(write_csv(tmp_path, f"{header}\n{row}\n")))
    assert found.path == "/proj/native/util.c"


def test_a_multi_segment_relative_directory_composes_whole(tmp_path: Path) -> None:
    """Every segment contributes; dropping the column loses three directories at once."""
    header = "CheckID,File,Line,Violation,Directory,Root"
    row = "R_01,util.c,42,message,src/native/deep,/proj"
    found = only(read_violations(write_csv(tmp_path, f"{header}\n{row}\n")))
    assert found.path == "/proj/src/native/deep/util.c"


def test_an_absolute_directory_wins_over_the_root_beside_it(tmp_path: Path) -> None:
    header = "CheckID,File,Line,Violation,Directory,Root"
    row = "R_01,util.c,42,message,/proj/native,/somewhere/else"
    found = only(read_violations(write_csv(tmp_path, f"{header}\n{row}\n")))
    assert found.path == "/proj/native/util.c"


def test_a_directory_written_with_a_trailing_separator_does_not_double_it(
    tmp_path: Path,
) -> None:
    """Pasting the parts together with a separator would give ``/proj/native//util.c``."""
    header = "CheckID,File,Line,Violation,Directory"
    row = "R_01,util.c,42,message,/proj/native/"
    found = only(read_violations(write_csv(tmp_path, f"{header}\n{row}\n")))
    assert found.path == "/proj/native/util.c"


@pytest.mark.parametrize(
    ("row", "reason"),
    [
        ("R_01,C:util.c,42,m,,C:\\proj", "':' (U+003A) in path segment 'C:util.c'"),
        ("R_01,C:util.c,42,m,C:\\proj\\native,", "':' (U+003A) in path segment 'C:util.c'"),
        ("R_01,util.c,42,m,C:native,C:\\proj", "':' (U+003A) in path segment 'C:native'"),
        ("R_01,C:util.c,42,m,/proj,", "':' (U+003A) in path segment 'C:util.c'"),
    ],
    ids=["file-with-root", "file-with-directory", "directory-drive-relative", "onto-posix-root"],
)
def test_a_drive_relative_value_is_refused_even_with_an_anchor_beside_it(
    tmp_path: Path, row: str, reason: str
) -> None:
    """``C:util.c`` is relative to drive C's working directory, so it anchors nothing.

    Every row here supplies a real anchor, so none of them can be refused merely for being
    relative — the refusal has to come from the drive-relative value itself. Without that,
    ``File=C:util.c`` beside ``Root=C:\\proj`` composes into ``C:/proj/C:util.c``.
    """
    csv_path = write_csv(tmp_path, f"{ANCHORED}\n{row}\n")
    with pytest.raises(AnalysisFailedError) as caught:
        read_violations(csv_path)
    assert reason in words_of(caught, csv_path)


@pytest.mark.parametrize(
    ("row", "reason"),
    [
        ("R_01,./util.c,42,m,,/proj", "has a '.' path segment"),
        ("R_01,util.c,42,m,/proj/.,", "has a '.' path segment"),
        ("R_01,util.c,42,m,.,/proj", "has a '.' path segment"),
        ("R_01,../shared/util.c,42,m,,/proj", "has a '..' path segment"),
        ("R_01,..,42,m,/proj,", "has a '..' path segment"),
        ("R_01,../..,42,m,/proj,", "has a '..' path segment"),
        ("R_01,/proj/../../x.c,42,m,,", "has a '..' path segment"),
        ("R_01,util.c,42,m,/proj/..,", "has a '..' path segment"),
        ("R_01,util.c,42,m,,/proj/..", "has a '..' path segment"),
        ("R_01,util.c,42,m,,/proj/.", "has a '.' path segment"),
        ("R_01,/,42,m,/proj,", "names no file, only a root"),
        ("R_01,//server/share/x.c,42,m,,", "has an empty path segment"),
        ("R_01,util.c,42,m,,", "does not start at a posix root"),
        ("R_01,util.c,42,m,native,", "does not start at a posix root"),
        ("R_01,C:util.c,42,m,,", "':' (U+003A) in path segment"),
    ],
    ids=[
        "here-in-file",
        "here-in-directory",
        "directory-is-here",
        "up-in-file",
        "bare-dotdot",
        "dotdot-twice",
        "absolute-escapes",
        "up-in-directory",
        "up-in-root",
        "here-in-root",
        "file-is-the-root",
        "unc",
        "nothing-anchors-it",
        "relative-directory",
        "drive-relative-alone",
    ],
)
def test_only_the_accepted_form_of_path_is_reported(tmp_path: Path, row: str, reason: str) -> None:
    """The whitelist, case by case, each refused for the reason the predicate names.

    ``.`` is here for a reason worth stating: ``PurePosixPath`` drops it while composing, so
    ``./util.c`` under a root arrives as a perfectly ordinary path. It is refused because
    the *value* is checked, not only the result — otherwise ``File=./util.c`` and
    ``File=/proj/./util.c`` would get different answers from the same module.
    """
    csv_path = write_csv(tmp_path, f"{ANCHORED}\n{row}\n")
    with pytest.raises(AnalysisFailedError) as caught:
        read_violations(csv_path)
    assert reason in words_of(caught, csv_path)


@pytest.mark.parametrize(
    ("bad", "seen"),
    [
        ("\t", "\t"),
        ("\n", "\n"),
        ("\x0b", "\x0b"),
        ("\x0c", "\x0c"),
        ("\r", "\n"),
        ("\x1c", "\x1c"),
        ("\x1d", "\x1d"),
        ("\x1e", "\x1e"),
        ("\x1f", "\x1f"),
        ("\x85", "\x85"),
    ],
    ids=["tab", "lf", "vt", "ff", "cr", "fs", "gs", "rs", "us", "nel"],
)
@pytest.mark.parametrize("where", ["lead", "trail"], ids=["leading", "trailing"])
def test_a_control_character_at_the_edge_of_a_file_is_refused_not_deleted(
    tmp_path: Path, bad: str, seen: str, where: str
) -> None:
    """``str.strip()`` with no argument removes all ten of these, and used to run first.

    ``File="\\ta.c"`` reached the path predicate as ``a.c`` and was reported against a file
    of that name — a file the CSV never named. A sanitiser ahead of a validator makes the
    validator judge a value the input never held. Nothing is taken off any more — not even
    the ASCII space, whose removal was an inference about a human-edited CSV — so every
    character reaches the predicate and is refused on its own merits.

    ``seen`` differs from ``bad`` only for CR: ``Path.read_text`` translates universal
    newlines, so a carriage return arrives at the predicate as a line feed and is refused as
    one. It is refused either way; the codepoint named is the one that actually arrived.
    """
    name = f"{bad}a.c" if where == "lead" else f"a.c{bad}"
    row = f'R_01,"{name}",42,m,,/proj'
    csv_path = write_csv(tmp_path, f"{ANCHORED}\n{row}\n")
    with pytest.raises(AnalysisFailedError) as caught:
        read_violations(csv_path)
    assert f"(U+{ord(seen):04X})" in words_of(caught, csv_path)


@pytest.mark.parametrize(
    ("character", "category"),
    [
        ("\x00", "Cc"),
        ("\x1f", "Cc"),
        ("\x7f", "Cc"),
        ("\x85", "Cc"),
        ("\x9f", "Cc"),
        ("\u200b", "Cf"),
        ("\u202e", "Cf"),
        ("\u2028", "Zl"),
        ("\u2029", "Zp"),
    ],
    ids=["nul", "us", "del", "nel", "apc", "zwsp", "rlo", "line-sep", "para-sep"],
)
def test_every_banned_category_is_refused_including_at_its_edges(
    tmp_path: Path, character: str, category: str
) -> None:
    """A category test has no range to shrink; these pin both ends of each old range.

    ``\\x1f`` and ``\\x9f`` are the edges a hex class kept getting wrong — narrowing
    ``\\x00-\\x1f`` by one let ``a\\x1fb.c`` through as a reported path. ``Cs`` is absent on
    purpose: a lone surrogate cannot survive ``errors="replace"`` to reach here.
    """
    assert unicodedata.category(character) == category, "the fixture must exercise what it names"
    row = f'R_01,"a{character}b.c",42,m,,/proj'
    csv_path = write_csv(tmp_path, f"{ANCHORED}\n{row}\n")
    with pytest.raises(AnalysisFailedError) as caught:
        read_violations(csv_path)
    assert f"(U+{ord(character):04X})" in words_of(caught, csv_path)


@pytest.mark.parametrize(
    "character", [" ", "\xa0", "\u3000"], ids=["space", "no-break-space", "ideographic-space"]
)
def test_a_space_inside_a_name_is_part_of_the_name(tmp_path: Path, character: str) -> None:
    """Space separators stay legal: a file may genuinely be named with one.

    The ASCII space is the case this test used to get backwards, asserting ``/proj/a.c``
    under a name that says the opposite. ``" a.c"`` is a legal posix file name and rather
    more likely than ``"\\xa0a.c"``; nothing measured says the CSV pads its fields, and
    ``und`` writes it, not a person. So all three are reported as the names they are.
    """
    row = f'R_01,"{character}a.c",42,m,,/proj'
    found = only(read_violations(write_csv(tmp_path, f"{ANCHORED}\n{row}\n")))
    assert found.path == f"/proj/{character}a.c"


@pytest.mark.parametrize(
    ("raw", "encoding"),
    [(b"caf\xe9.c", "latin-1"), (b"a\xed\xa0\x80b.c", "wtf-8 surrogate")],
    ids=["latin-1-name", "lone-surrogate"],
)
def test_a_byte_the_decoder_could_not_read_never_becomes_a_path(
    tmp_path: Path, raw: bytes, encoding: str
) -> None:
    """``errors="replace"`` edits path columns too, and U+FFFD is category ``So``.

    Nothing else in the predicate stops an ordinary symbol, so ``caf\xe9.c`` from a latin-1
    name arrived as ``/proj/caf\ufffd.c`` and reached ``Finding.path`` as a file that does
    not exist, reported as fact. U+FFFD is the decoder saying it does not know what the byte
    was, which makes it the one character provably not in the real name — and it is what a
    lone surrogate becomes, so accepting it also let the ``Cs`` ban be walked around.
    """
    target = tmp_path / "violations.csv"
    target.write_bytes(f"{ANCHORED}\nR_01,".encode() + raw + b",42,m,,/proj\n")
    with pytest.raises(AnalysisFailedError) as caught:
        read_violations(target)
    assert "(U+FFFD)" in words_of(caught, target), encoding


def test_a_byte_the_decoder_could_not_read_is_still_tolerated_in_a_message(
    tmp_path: Path,
) -> None:
    """The asymmetry is the point: a mangled message misreads, a mangled path misdirects."""
    target = tmp_path / "violations.csv"
    target.write_bytes(f"{SMALL_HEADER}\nR_01,Rule,/src/a.c,42,7,main,caf\xe9\n".encode("latin-1"))
    assert only(read_violations(target)).message == "caf\ufffd"


@pytest.mark.parametrize("drive", ["A", "Z", "a", "z"], ids=["A", "Z", "a", "z"])
def test_every_drive_letter_anchors_a_path(tmp_path: Path, drive: str) -> None:
    """``Z:`` is the conventional mapped network drive, and ``[A-Ya-z]`` would refuse it.

    Every other drive fixture writes ``C:``, which leaves all four boundaries of the anchor
    character class free to move without a test noticing.
    """
    row = f"R_01,util.c,42,message,,{drive}:/proj"
    found = only(read_violations(write_csv(tmp_path, f"{ANCHORED}\n{row}\n")))
    assert found.path == f"{drive}:/proj/util.c"


@pytest.mark.parametrize(
    ("character", "category"),
    [("\u0378", "Cn"), ("\ufdd0", "Cn"), ("\ue000", "Co")],
    ids=["unassigned", "noncharacter", "private-use"],
)
def test_the_remaining_banned_categories_are_refused(
    tmp_path: Path, character: str, category: str
) -> None:
    """``Cn`` and ``Co`` were in the set but in no test, so removing either survived."""
    assert unicodedata.category(character) == category, "the fixture must exercise what it names"
    row = f'R_01,"a{character}b.c",42,m,,/proj'
    csv_path = write_csv(tmp_path, f"{ANCHORED}\n{row}\n")
    with pytest.raises(AnalysisFailedError) as caught:
        read_violations(csv_path)
    assert f"(U+{ord(character):04X})" in words_of(caught, csv_path)


def test_a_lone_surrogate_segment_is_refused() -> None:
    """``Cs``, checked against the predicate directly because no file can deliver one.

    ``_read`` decodes with ``errors="replace"``, so a surrogate in a CSV becomes U+FFFD
    before the predicate ever sees it — which is why U+FFFD is banned by name. The ``Cs``
    entry still has to hold for any caller that reaches the predicate without a decoder in
    front of it, and nothing but a direct call can show that it does.
    """
    assert unicodedata.category("\ud800") == "Cs"
    problem = codecheck._unusable(["a\ud800b.c"])
    assert problem is not None and "(U+D800)" in problem


def test_a_directory_of_nothing_but_separators_is_refused(tmp_path: Path) -> None:
    """``//`` used to collapse to a bare ``/`` and be read as the filesystem root.

    On the platform that writes backslashes, ``\\\\`` introduces a UNC path, so a value of
    exactly two separators is one this runner cannot claim to understand.
    """
    row = "R_01,util.c,42,message,//,/proj"
    csv_path = write_csv(tmp_path, f"{ANCHORED}\n{row}\n")
    with pytest.raises(AnalysisFailedError) as caught:
        read_violations(csv_path)
    assert "empty path segment" in words_of(caught, csv_path)


def test_a_segment_of_nothing_but_whitespace_names_nothing(tmp_path: Path) -> None:
    row = 'R_01,"/proj/ /a.c",42,m,,'
    csv_path = write_csv(tmp_path, f"{ANCHORED}\n{row}\n")
    with pytest.raises(AnalysisFailedError) as caught:
        read_violations(csv_path)
    assert "whitespace-only path segment" in words_of(caught, csv_path)


@pytest.mark.parametrize(
    ("bad", "shown"),
    [("\n", "'\\n'"), ("\t", "'\\t'"), ("\x00", "'\\x00'")],
    ids=["newline", "tab", "nul"],
)
def test_a_control_character_never_reaches_a_reported_path(
    tmp_path: Path, bad: str, shown: str
) -> None:
    """A path carrying one reaches a human report and a SARIF ``physicalLocation`` as it is.

    Measured: ``csv.reader`` passes a NUL straight through into a field, so nothing upstream
    of this stops one.
    """
    row = f'R_01,"src/{bad}long/name.c",42,m,,/proj'
    csv_path = write_csv(tmp_path, f"{ANCHORED}\n{row}\n")
    with pytest.raises(AnalysisFailedError) as caught:
        read_violations(csv_path)
    assert shown in words_of(caught, csv_path)


@pytest.mark.parametrize(
    "row",
    [
        "R_01,..\\shared\\util.c,42,m,C:\\proj,",
        "R_01,util.c,42,m,C:\\proj\\..\\other,",
        "R_01,util.c,42,m,C:\\proj\\..,",
    ],
    ids=["up-in-file", "up-inside-directory", "up-at-end-of-directory"],
)
def test_a_traversal_written_with_windows_separators_is_refused_too(
    tmp_path: Path, row: str
) -> None:
    """Understand reports native separators, so the check must run after the rewrite.

    Every other fixture writes ``/``; on those, a check reading the raw value would pass
    just as well. Here the segments are only visible once backslashes have become slashes —
    and a backslash surviving into a segment is itself refused, so the ordering cannot
    quietly reverse.
    """
    csv_path = write_csv(tmp_path, f"{ANCHORED}\n{row}\n")
    with pytest.raises(AnalysisFailedError) as caught:
        read_violations(csv_path)
    assert "has a '..' path segment" in words_of(caught, csv_path)


def test_the_ambiguous_traversal_is_refused_and_not_quietly_resolved(tmp_path: Path) -> None:
    """The reviewer's case: neither reading reaches a ``Finding``.

    The assertions avoid the message's trailing echo of the row, which quotes the raw value
    and would satisfy a check for it whatever the code said.
    """
    row = "R_01,../vendor/x.c,42,message,/proj/native,/proj"
    csv_path = write_csv(tmp_path, f"{ANCHORED}\n{row}\n")
    with pytest.raises(AnalysisFailedError) as caught:
        read_violations(csv_path)
    said = words_of(caught, csv_path).split("; the row was")[0]
    assert "has a '..' path segment" in said
    assert "'../vendor/x.c'" in said, "the refusal must quote what it saw"
    assert "/proj/vendor/x.c" not in said, "the Directory-relative reading is not an answer"
    # The Root-relative reading, "/vendor/x.c", cannot be asserted absent: it is a substring
    # of the raw value the message quotes. Its absence is covered by the refusal itself.


def test_a_dot_inside_a_name_is_not_a_path_segment(tmp_path: Path) -> None:
    """``util.c`` and ``a.b`` are ordinary names; only a whole ``.`` segment is refused."""
    row = "R_01,util.c,42,message,/proj/a.b,"
    found = only(read_violations(write_csv(tmp_path, f"{ANCHORED}\n{row}\n")))
    assert found.path == "/proj/a.b/util.c"


def test_a_trailing_separator_hides_no_segment_before_it(tmp_path: Path) -> None:
    """The boundary of the trailing-separator tolerance: it drops one character, not two.

    An anchored ``File`` means ``Directory`` is checked as a column and never appears in the
    result, so a tolerance that trimmed one character too many would drop the whole last
    segment out of the only check that value ever gets.
    """
    row = "R_01,/proj/util.c,42,message,/proj/bad:/,"
    csv_path = write_csv(tmp_path, f"{ANCHORED}\n{row}\n")
    with pytest.raises(AnalysisFailedError) as caught:
        read_violations(csv_path)
    assert "path segment 'bad:'" in words_of(caught, csv_path)


@pytest.mark.parametrize(
    ("row", "quoted"),
    [
        ("R_01,/proj/util.c,42,message,/proj/bad:/,", "Directory"),
        ("R_01,/proj/util.c,42,message,,/proj/bad:", "Root"),
        ("R_01,util.c,42,message,/proj/native,/proj/bad:", "Root"),
    ],
    ids=["directory-behind-anchored-file", "root-behind-anchored-file", "root-behind-anchored-dir"],
)
def test_a_column_that_never_reaches_the_result_is_still_judged(
    tmp_path: Path, row: str, quoted: str
) -> None:
    """An anchored column discards the ones before it, which are then checked nowhere else.

    That is the whole reason each column is judged on its own and not only through the
    composed path: an anchored ``File`` means ``Directory`` and ``Root`` never appear in the
    result, and an anchored ``Directory`` means ``Root`` does not. A value nobody looks at is
    still a value this runner cannot read.
    """
    csv_path = write_csv(tmp_path, f"{ANCHORED}\n{row}\n")
    with pytest.raises(AnalysisFailedError) as caught:
        read_violations(csv_path)
    said = words_of(caught, csv_path).split("; the row was")[0]
    assert f"reports {quoted} " in said
    assert "(U+003A)" in said


def test_a_file_written_with_a_trailing_separator_still_names_that_file(
    tmp_path: Path,
) -> None:
    """A trailing separator shows no disagreement between the columns, and loses nothing."""
    row = "R_01,util.c/,42,message,/proj,"
    found = only(read_violations(write_csv(tmp_path, f"{ANCHORED}\n{row}\n")))
    assert found.path == "/proj/util.c"


def test_a_file_carrying_its_own_directory_beside_a_directory_column_is_refused(
    tmp_path: Path,
) -> None:
    """Composing gives ``/proj/native/native/util.c``; ignoring ``Directory`` gives another
    answer. Both are defensible, so neither is reported as fact."""
    header = "CheckID,File,Line,Violation,Directory"
    row = "R_01,native/util.c,42,message,/proj/native"
    csv_path = write_csv(tmp_path, f"{header}\n{row}\n")
    with pytest.raises(AnalysisFailedError) as caught:
        read_violations(csv_path)
    said = words_of(caught, csv_path).split("; the row was")[0]
    assert "disagree about which holds the directory" in said
    assert "'native/util.c'" in said
    assert "'/proj/native'" in said
    assert "/proj/native/native/util.c" not in said


def test_a_file_carrying_its_own_directory_composes_when_nothing_else_holds_one(
    tmp_path: Path,
) -> None:
    """With ``Directory`` empty there is no disagreement, so ``Root`` anchors the lot."""
    header = "CheckID,File,Line,Violation,Directory,Root"
    row = "R_01,native/util.c,42,message,,/proj"
    found = only(read_violations(write_csv(tmp_path, f"{header}\n{row}\n")))
    assert found.path == "/proj/native/util.c"


def test_an_absolute_file_wins_over_the_directory_beside_it(tmp_path: Path) -> None:
    """When ``File`` is already rooted, joining it under ``Directory`` would double the path."""
    row = ",Magic number,/proj/native/util.c,/proj/native,,R_01,Magic,42,7,,snip,0,,/proj,,W"
    found = only(read_violations(write_csv(tmp_path, f"{VIOLATION_HEADER}\n{row}\n")))
    assert found.path == "/proj/native/util.c"


def test_an_absolute_windows_file_is_not_doubled_under_its_directory(tmp_path: Path) -> None:
    """A drive letter is not a posix root, so joining would build ``C:/p/native/C:/p/…``.

    Joining an absolute posix path onto a base discards the base, which hides this; a
    Windows path is where letting an absolute ``File`` fall through to the join goes wrong.
    """
    header = "CheckID,File,Line,Violation,Directory"
    row = "R_01,C:\\proj\\native\\util.c,42,message,C:\\proj\\native"
    found = only(read_violations(write_csv(tmp_path, f"{header}\n{row}\n")))
    assert found.path == "C:/proj/native/util.c"


def test_root_anchors_the_file_when_there_is_no_directory(tmp_path: Path) -> None:
    header = "CheckID,File,Line,Violation,Root"
    row = "R_01,util.c,42,message,/proj"
    assert only(read_violations(write_csv(tmp_path, f"{header}\n{row}\n"))).path == "/proj/util.c"


@pytest.mark.parametrize(
    ("directory", "root", "expected"),
    [
        ("/proj/native", "/proj", "/proj/native/util.c"),
        ("C:/proj/native", "C:/proj", "C:/proj/native/util.c"),
        ("C:\\proj\\native", "C:\\proj", "C:/proj/native/util.c"),
        ("C:/proj/native", "/somewhere/else", "C:/proj/native/util.c"),
        ("/proj/native", "C:/somewhere", "/proj/native/util.c"),
    ],
    ids=["posix", "drive", "drive-native-separators", "drive-over-posix", "posix-over-drive"],
)
def test_an_anchored_directory_wins_over_the_root_on_either_anchor(
    tmp_path: Path, directory: str, root: str, expected: str
) -> None:
    """Both anchor columns are filled in every case, which is the configuration that failed.

    ``PurePosixPath`` resets composition on ``/`` and never on ``<letter>:/``, so a drive
    anchor used to compose as ``C:/proj/C:/proj/native/util.c`` and then be refused for a
    colon in a segment — on the one platform the drive grammar exists to serve, and with a
    message blaming the value rather than the composition that produced it. The accepting
    tests that existed supplied one anchor column each, the only shape that cannot hit it.
    """
    row = f"R_01,util.c,42,message,{directory},{root}"
    found = only(read_violations(write_csv(tmp_path, f"{ANCHORED}\n{row}\n")))
    assert found.path == expected


def test_a_root_that_is_a_filesystem_or_drive_root_still_places_a_file(
    tmp_path: Path,
) -> None:
    """A project checked out at ``/`` is unusual, not malformed."""
    for root, expected in (("/", "/util.c"), ("C:/", "C:/util.c")):
        row = f"R_01,util.c,42,message,,{root}"
        found = only(read_violations(write_csv(tmp_path, f"{ANCHORED}\n{row}\n")))
        assert found.path == expected


def test_a_windows_root_joins_with_forward_slashes(tmp_path: Path) -> None:
    """``Root`` gets the same rewrite as the other two, and its own fixture to prove it.

    Every other ``Root`` fixture writes ``/``, on which a missing rewrite is invisible: a
    raw ``C:\\proj`` would be refused for the colon in its only segment, and nothing would
    have noticed which of the three columns was left unrewritten.
    """
    header = "CheckID,File,Line,Violation,Root"
    row = "R_01,util.c,42,message,C:\\proj"
    found = only(read_violations(write_csv(tmp_path, f"{header}\n{row}\n")))
    assert found.path == "C:/proj/util.c"


def test_a_windows_directory_joins_with_forward_slashes(tmp_path: Path) -> None:
    """Understand reports native paths; every other path in a snapshot is posix."""
    header = "CheckID,File,Line,Violation,Directory"
    row = "R_01,util.c,42,message,C:\\proj\\native"
    found = only(read_violations(write_csv(tmp_path, f"{header}\n{row}\n")))
    assert found.path == "C:/proj/native/util.c"


def test_a_violation_with_no_file_at_all_fails(tmp_path: Path) -> None:
    """An empty ``File`` beside a directory would otherwise report the directory itself."""
    csv_path = write_csv(tmp_path, "CheckID,File,Line,Violation,Directory\nR_01,,42,m,/proj/n\n")
    with pytest.raises(AnalysisFailedError) as caught:
        read_violations(csv_path)
    assert "no file" in words_of(caught, csv_path).split("; the row was")[0]
