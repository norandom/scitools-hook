"""The CodeCheck runner: file list in, parsed ``RawViolation`` records out (task 6.7).

The unit tests drive the **parser** with CSV text written into ``tmp_path`` and the
**runner** with :class:`~fakes.FakeUndCli`, so the module is exercised without a CodeCheck
license — which this machine does not have.

:data:`VIOLATION_HEADER` and :data:`FILES_TREE_HEADER` are not invented and not a guess at
the shape: they are the two header lines ``und`` builds, copied byte for byte out of the
executable, and
:func:`test_contract_the_fixture_headers_and_export_names_are_compiled_into_und` asserts they are
still in there. Every other test then renames, reorders, drops and pads the columns, because
the parser's contract is that it reads the header it is given rather than either of these.

The module is named ``test_codecheck_runner`` rather than ``test_codecheck`` because the
test directories carry no ``__init__.py``: two test modules with the same basename collide
under pytest's default import mode, and ``tests/analysis/test_codecheck.py`` (task 4.7,
which maps these records onto findings) already owns that name.
"""

from __future__ import annotations

import csv
import os
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

import pytest
from fakes import FakeCall, FakeUndCli

from scitools_hook.analysis.codecheck import map_violations
from scitools_hook.errors import AnalysisFailedError, LicenseError
from scitools_hook.models.progress import CommandLog
from scitools_hook.models.understand import RawViolation, UnderstandEnv
from scitools_hook.understand import codecheck
from scitools_hook.understand.codecheck import NO_LINE, CodeCheckRunner, read_violations
from scitools_hook.understand.und_cli import VIOLATIONS_EXPORT, UndCli

# --- und's own headers, byte for byte --------------------------------------------

VIOLATION_HEADER = (
    "Invalid,Violation,File,Directory,Entity,CheckID,Check Name,Line,Column, ,"
    "Snippet,Ignored,Note,Root, ,Severity"
)
"""``CodeCheckResultsTreeModel``'s header: sixteen columns, two of them unnamed."""

FILES_TREE_HEADER = (
    "Invalid,Violation,File,Directory,Entity,CheckID,Check Name,Line,Column, ,"
    "Snippet,Ignored,Note,CheckID,Root,Severity"
)
"""``CodeCheckResultsFilesTreeModel``'s header: also sixteen, and ``CheckID`` twice."""

SMALL_HEADER = "CheckID,Check Name,File,Line,Column,Entity,Violation"
"""The seven columns the Gate reads, and nothing else; the workhorse of the value tests."""

GROUP_ROW = ",,util.c,/proj/native,,,,,,,,,,,,"
"""A files-tree grouping row: it names a file, no check and no violation."""

TREE_ROW = (
    ",Magic number,util.c,/proj/native,adler,RECOMMENDED_04,Magic Numbers,42,7,,"
    "snip,0,,RECOMMENDED_04,/proj,Warning"
)
"""A real violation under :data:`FILES_TREE_HEADER`, its file split from its directory."""

FULL_ROW = (
    ",Function is too complex,/src/app.py,/src,main,RECOMMENDED_04,Avoid Complex Functions,"
    "42,7,,snippet,0,,/src,,Warning"
)
"""One violation under :data:`VIOLATION_HEADER`, every named column filled in."""

EXPECTED = RawViolation(
    check_id="RECOMMENDED_04",
    check_name="Avoid Complex Functions",
    path="/src/app.py",
    line=42,
    column=7,
    message="Function is too complex",
    entity="main",
)
"""What :data:`FULL_ROW` must become, whichever header spelling delivered it."""


def write_csv(directory: Path, text: str, name: str = "violations.csv") -> Path:
    """Write ``text`` as the violations CSV CodeCheck would have left behind."""
    target = directory / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def words_of(caught: pytest.ExceptionInfo[Exception], *paths: Path) -> str:
    """The error's own words, with every path it quotes taken out of them.

    ``tmp_path`` is named after the test that asked for it, so ``"empty" in str(exc)`` can
    be satisfied by a directory called ``test_an_empty_file_is_not_read0`` while the code
    says nothing of the kind. Every assertion about what the code *says* goes through here;
    the ones that are genuinely about the path assert on the path itself.
    """
    said = str(caught.value)
    for path in paths:
        said = said.replace(str(path), "<path>")
    return said


def only(violations: list[RawViolation]) -> RawViolation:
    """The single violation the CSV under test holds, asserted to be single."""
    assert len(violations) == 1, f"expected exactly one violation, got {violations}"
    return violations[0]


# --- reading one row -------------------------------------------------------------


def test_a_row_becomes_a_violation_carrying_its_check_id_path_line_and_message(
    tmp_path: Path,
) -> None:
    """The whole point of the parser: requirement 6.9's finding needs all four."""
    found = read_violations(write_csv(tmp_path, f"{VIOLATION_HEADER}\n{FULL_ROW}\n"))
    assert only(found) == EXPECTED


def test_every_row_is_read_not_just_the_first(tmp_path: Path) -> None:
    rows = "\n".join(
        [
            SMALL_HEADER,
            "A_01,First,/src/a.py,1,1,alpha,first message",
            "B_02,Second,/src/b.py,2,2,beta,second message",
        ]
    )
    found = read_violations(write_csv(tmp_path, f"{rows}\n"))
    assert [violation.check_id for violation in found] == ["A_01", "B_02"]
    assert [violation.path for violation in found] == ["/src/a.py", "/src/b.py"]


# --- the header decides which column is which ------------------------------------


def test_both_headers_und_writes_are_read_the_same_way(tmp_path: Path) -> None:
    """The files-tree header repeats ``CheckID``; positional parsing would shift by one."""
    files_tree_row = (
        ",Function is too complex,/src/app.py,/src,main,RECOMMENDED_04,Avoid Complex Functions,"
        "42,7,,snippet,0,,RECOMMENDED_04,/src,Warning"
    )
    found = read_violations(write_csv(tmp_path, f"{FILES_TREE_HEADER}\n{files_tree_row}\n"))
    assert only(found) == EXPECTED


def test_a_repeated_column_resolves_to_its_leftmost_occurrence(tmp_path: Path) -> None:
    """``CheckID`` twice is real; so is ``File`` beside ``File Name``. Leftmost decides."""
    header = "CheckID,File,Line,Violation,CheckID,File Name"
    row = "R_01,/src/first.py,1,message,R_99,/src/second.py"
    found = only(read_violations(write_csv(tmp_path, f"{header}\n{row}\n")))
    assert (found.check_id, found.path) == ("R_01", "/src/first.py")


def test_columns_are_matched_by_name_and_not_by_position(tmp_path: Path) -> None:
    """A reordered header must still produce the same record, or the schema is guessed."""
    reordered = "Violation,Line,File,Check Name,CheckID,Entity,Column"
    row = "Function is too complex,42,/src/app.py,Avoid Complex Functions,RECOMMENDED_04,main,7"
    assert only(read_violations(write_csv(tmp_path, f"{reordered}\n{row}\n"))) == EXPECTED


@pytest.mark.parametrize(
    "header",
    [
        "check id,check name,file,line,column,entity,violation",
        "CHECK_ID,CHECK-NAME,FILE,LINE,COLUMN,ENTITY,VIOLATION",
        "  Check ID ,Check  Name,File Name,Line Number,Col,Entity,Message",
    ],
    ids=["lowercase-spaced", "uppercase-punctuated", "padded-and-aliased"],
)
def test_column_names_are_read_regardless_of_case_spacing_and_punctuation(
    tmp_path: Path, header: str
) -> None:
    row = "RECOMMENDED_04,Avoid Complex Functions,/src/app.py,42,7,main,Function is too complex"
    assert only(read_violations(write_csv(tmp_path, f"{header}\n{row}\n"))) == EXPECTED


def test_a_digit_in_a_column_name_keeps_it_distinct(tmp_path: Path) -> None:
    """``Line2`` is not ``Line``: dropping digits when normalising would let it take over.

    ``Line2`` sits to the *left* of ``Line`` on purpose. To its right the leftmost-wins rule
    would hide the mistake, and the test would pass whether or not digits survive.
    """
    header = "CheckID,File,Line2,Violation,Line"
    row = "R_01,/src/app.py,999,message,42"
    assert only(read_violations(write_csv(tmp_path, f"{header}\n{row}\n"))).line == 42


def test_columns_the_gate_does_not_use_are_ignored_rather_than_fatal(tmp_path: Path) -> None:
    """``Invalid``, ``Snippet``, ``Severity`` and the unnamed columns are all extra."""
    found = read_violations(write_csv(tmp_path, f"{VIOLATION_HEADER}\n{FULL_ROW}\n"))
    assert only(found).message == "Function is too complex"


@pytest.mark.parametrize(
    ("header", "label"),
    [
        ("Check Name,File,Line,Column,Entity,Violation", "CheckID"),
        ("CheckID,Check Name,Line,Column,Entity,Violation", "File"),
        ("CheckID,Check Name,File,Column,Entity,Violation", "Line"),
        ("CheckID,Check Name,File,Line,Column,Entity", "Violation"),
    ],
    ids=["no-check-id", "no-file", "no-line", "no-violation"],
)
def test_a_missing_required_column_names_und_s_label_and_quotes_the_header(
    tmp_path: Path, header: str, label: str
) -> None:
    """The reader is looking at a CSV header, so the complaint must speak in its words."""
    csv_path = write_csv(tmp_path, f"{header}\nvalues,do,not,matter,here\n")
    with pytest.raises(AnalysisFailedError) as caught:
        read_violations(csv_path)
    complaint = words_of(caught, csv_path)
    assert f"no {label} column" in complaint
    assert header in complaint


def test_a_column_the_gate_can_do_without_may_simply_be_absent(tmp_path: Path) -> None:
    """No ``Column``, ``Entity`` or ``Check Name`` column: the record still parses."""
    body = "CheckID,File,Line,Violation\nR_01,/a.py,3,m\n"
    found = only(read_violations(write_csv(tmp_path, body)))
    assert (found.column, found.entity) == (None, None)
    assert found.check_name == "R_01"


# --- rows that group violations rather than being one -----------------------------


def test_a_row_with_no_check_id_and_no_message_is_not_a_violation(tmp_path: Path) -> None:
    """The files-tree export heads each group with a file row that names no check.

    Passed on it becomes ``RawViolation(check_id='')``, which the analysis layer answers
    with ``ConfigError: a CodeCheck rule name needs a check id`` — the configuration exit
    code, raised far from here, naming no file.
    """
    found = read_violations(write_csv(tmp_path, f"{FILES_TREE_HEADER}\n{GROUP_ROW}\n{TREE_ROW}\n"))
    assert [violation.check_id for violation in found] == ["RECOMMENDED_04"]


def test_a_skipped_grouping_row_does_not_reach_the_finding_mapper(tmp_path: Path) -> None:
    """The end the skip exists for: the same rows now map to findings instead of raising."""
    found = read_violations(write_csv(tmp_path, f"{FILES_TREE_HEADER}\n{GROUP_ROW}\n{TREE_ROW}\n"))
    findings = map_violations(found, "warning", "/proj")
    assert [(finding.path, finding.line) for finding in findings] == [("native/util.c", 42)]


def test_a_file_of_nothing_but_grouping_rows_reports_no_violations(tmp_path: Path) -> None:
    """A tree that lists checked files and finds nothing in them is clean, not broken.

    Counting rows cannot tell this from a mis-mapped column, and a threshold turns the
    boundary — one file listed, no violations — into a gate failure on a clean project.
    """
    body = f"{FILES_TREE_HEADER}\n{GROUP_ROW}\n"
    assert read_violations(write_csv(tmp_path, body)) == []


def test_a_row_that_states_a_violation_and_names_no_check_is_a_mapping_failure(
    tmp_path: Path,
) -> None:
    """A message with no check id cannot be a grouping row, whatever the row count.

    Skipping it would report the rest of the file as findings from a file nobody could
    read; one such row is as wrong as a hundred, so the count never enters into it.
    """
    csv_path = write_csv(tmp_path, f"{SMALL_HEADER}\n,Rule,/src/app.py,42,7,main,too complex\n")
    with pytest.raises(AnalysisFailedError) as caught:
        read_violations(csv_path)
    complaint = words_of(caught, csv_path).split("; the row was")[0]
    assert "states a violation in a row that names no check" in complaint


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


ANCHORED = "CheckID,File,Line,Violation,Directory,Root"
"""A header with both anchor columns, so a refusal cannot be passing for want of one."""


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


# --- values that are missing, awkward or quoted -----------------------------------


def test_no_line_is_zero_because_that_is_the_value_the_analysis_layer_reads() -> None:
    """``analysis.codecheck`` maps ``line > 0`` to a line and 0 to "the file" (req 7.1).

    Pinned to the literal, not to the imported constant: an assertion that compares the
    module's constant with itself cannot notice the constant changing, and 1 would place
    every file-level violation on the first line of its file.
    """
    assert NO_LINE == 0


def test_a_violation_with_no_line_number_is_kept_and_says_so(tmp_path: Path) -> None:
    """Project-level checks report line 0; the violation is still a violation."""
    row = "R_01,Project Rule,/src/app.py,,,,module has no docstring"
    found = only(read_violations(write_csv(tmp_path, f"{SMALL_HEADER}\n{row}\n")))
    assert found.line == 0
    assert found.message == "module has no docstring"


@pytest.mark.parametrize(
    "value",
    ["n/a", "-5", "4.0", "1e3", "\u00b2", "\u2168"],
    ids=["word", "negative", "decimal", "exponent", "superscript", "roman-numeral"],
)
def test_a_line_that_is_not_a_whole_number_is_read_as_no_line(tmp_path: Path, value: str) -> None:
    """A sign or a point must answer "no number", not raise and not become a position.

    The last two are the cases a guess fails on: ``"\u00b2".isdigit()`` is true while
    ``int("\u00b2")`` raises, and ``"\u2168".isnumeric()`` is true while ``int`` refuses it
    too. Without them the parametrisation pins the guard against nothing but itself — every
    other value is already false for every spelling of the test.
    """
    row = f"R_01,Rule,/src/app.py,{value},,,message"
    assert only(read_violations(write_csv(tmp_path, f"{SMALL_HEADER}\n{row}\n"))).line == 0


def test_a_check_id_wrapped_across_lines_is_put_back_together(tmp_path: Path) -> None:
    """``codecheck_rule`` only refuses a *blank* id, so ``R_\n01`` becomes the rule name.

    It then corrupts the human report and never matches the severity-map key an operator
    writes, and nothing anywhere says why.
    """
    row = '"R_\n01",Rule,/src/app.py,42,7,main,too complex'
    found = only(read_violations(write_csv(tmp_path, f"{SMALL_HEADER}\n{row}\n")))
    assert found.check_id == "R_01"


def test_a_check_name_and_entity_keep_their_spaces_but_lose_their_newlines(
    tmp_path: Path,
) -> None:
    """These are prose, so runs collapse rather than vanish; a name is not an identifier."""
    row = 'R_01,"Avoid\nComplex Functions",/src/app.py,42,7,"my\nclass::run",too complex'
    found = only(read_violations(write_csv(tmp_path, f"{SMALL_HEADER}\n{row}\n")))
    assert found.check_name == "Avoid Complex Functions"
    assert found.entity == "my class::run"


def test_an_empty_optional_value_is_none_rather_than_an_empty_string(tmp_path: Path) -> None:
    row = "R_01,Rule,/src/app.py,3,,,message"
    found = only(read_violations(write_csv(tmp_path, f"{SMALL_HEADER}\n{row}\n")))
    assert (found.column, found.entity) == (None, None)


def test_a_quoted_field_containing_a_comma_stays_one_message(tmp_path: Path) -> None:
    """Splitting on commas would cut this message in half and shift every later column."""
    row = 'R_01,Rule,/src/app.py,42,7,main,"too complex, extract a routine"'
    found = only(read_violations(write_csv(tmp_path, f"{SMALL_HEADER}\n{row}\n")))
    assert found.message == "too complex, extract a routine"
    assert found.entity == "main"


def test_a_quoted_field_containing_a_newline_stays_one_row(tmp_path: Path) -> None:
    """A snippet spanning two source lines must not be read as two violations."""
    row = 'R_01,Rule,/src/app.py,42,7,main,"first line\nsecond line"'
    found = only(read_violations(write_csv(tmp_path, f"{SMALL_HEADER}\n{row}\n")))
    assert found.message == "first line\nsecond line"


def test_a_nul_in_a_message_is_dropped(tmp_path: Path) -> None:
    """Measured: ``csv.reader`` passes a NUL straight into the field, so nothing else stops it.

    The message keeps its newlines, because a snippet spanning two source lines is quoted as
    two lines. A NUL is not prose: it truncates the string in every C consumer downstream and
    renders as nothing in a terminal, so all it can do is hide the rest of the message.
    """
    row = 'R_01,Rule,/src/app.py,42,7,main,"too\x00 complex"'
    found = only(read_violations(write_csv(tmp_path, f"{SMALL_HEADER}\n{row}\n")))
    assert found.message == "too complex"


def test_a_blank_row_is_skipped(tmp_path: Path) -> None:
    """A trailing blank line, or a separator row, is not a violation with empty fields."""
    body = f"{SMALL_HEADER}\nR_01,Rule,/src/app.py,42,7,main,message\n\n,,,,,,\n"
    assert len(read_violations(write_csv(tmp_path, body))) == 1


def test_a_header_followed_only_by_blank_lines_is_still_no_violations(tmp_path: Path) -> None:
    """A report that ends in blank lines is clean, not a file of unreadable grouping rows.

    Counting those blank lines as rows with no check id would turn every clean run whose
    CSV happens to end with a spare newline into a hard failure.
    """
    assert read_violations(write_csv(tmp_path, f"{SMALL_HEADER}\n\n\n,,,,,,\n")) == []


@pytest.mark.parametrize(
    ("row", "width"),
    [
        ("R_01,Rule,/src/app.py,42", 4),
        ("R_01,Rule,/src/a,b.py,42,7,main,too complex", 8),
    ],
    ids=["lost-a-field", "gained-a-field"],
)
def test_a_row_that_lost_or_gained_a_field_is_refused(tmp_path: Path, row: str, width: int) -> None:
    """Both halves, because the guard's comparison has two sides and only one was fixtured.

    The gained half is the mirror image of the truncation below: ``/src/a,b.py`` is a legal
    posix file name written unquoted, and under a ``>=`` comparison the row is accepted as
    ``path='/src/a'``, ``line=0``, ``message='main'`` — a shorter well-formed path naming a
    different file, with the wrong line and the wrong message.
    """
    csv_path = write_csv(tmp_path, f"{SMALL_HEADER}\n{row}\n")
    with pytest.raises(AnalysisFailedError) as caught:
        read_violations(csv_path)
    said = words_of(caught, csv_path).split("; the row was")[0]
    assert f"{width} fields" in said
    assert "header has 7" in said


@pytest.mark.parametrize(
    ("header", "row"),
    [
        (SMALL_HEADER, ",Rule,/src/app.py,42"),
        (VIOLATION_HEADER, ",Magic number,util.c,/proj"),
    ],
    ids=["check-id-first", "check-id-sixth"],
)
def test_a_ragged_row_is_refused_before_any_cell_of_it_is_read(
    tmp_path: Path, header: str, row: str
) -> None:
    """The width guard's *position* is load-bearing, not just its presence.

    ``_value`` does no bounds checking, on the stated ground that the guard has already
    refused any ragged row. Move the guard below the check-id read and the row is read
    first — and ``IndexError`` comes straight out of ``read_violations``, past the envelope
    every other failure here travels in and past what ``doctor`` knows to catch.

    The second case is the one that matters, and its absence made a previous equivalence
    claim wrong. Under ``SMALL_HEADER`` the check-id column is index 0, always in range, so
    the mutation looks harmless. Under ``VIOLATION_HEADER`` — ``und``'s own header, copied
    byte for byte — ``CheckID`` is **column 5**, and a four-field row cannot reach it.
    """
    csv_path = write_csv(tmp_path, f"{header}\n{row}\n")
    with pytest.raises(AnalysisFailedError) as caught:
        read_violations(csv_path)
    assert "fields where its header has" in words_of(caught, csv_path)


def test_und_s_own_header_does_not_put_the_check_id_first(tmp_path: Path) -> None:
    """The fact the test above rests on, asserted rather than assumed."""
    assert VIOLATION_HEADER.split(",").index("CheckID") == 5
    assert FILES_TREE_HEADER.split(",").index("CheckID") == 5


def test_a_carriage_return_cannot_shorten_a_path_into_a_different_file(
    tmp_path: Path,
) -> None:
    """``read_text`` turns an unquoted CR into LF, and ``csv`` reads LF as end of record.

    ``/proj/a\rb.c`` arrived as the row ``['R_01', '/proj/a']``: a shorter path, perfectly
    well formed, naming a file that is not the one the CSV meant. Nothing refused it —
    inside a quoted field the LF is caught as a control character, but out here the field
    simply ends. The row's width is what gives it away.
    """
    target = tmp_path / "violations.csv"
    target.write_bytes(b"CheckID,File,Line,Violation\nR_01,/proj/a\rb.c,42,m\n")
    with pytest.raises(AnalysisFailedError) as caught:
        read_violations(target)
    assert "fields where its header has" in words_of(caught, target)


@pytest.mark.parametrize(
    "field",
    ['"/proj/a.c"junk', '"/proj/a.c" ', '"/proj/a.c"x"y"'],
    ids=["junk-after-quote", "space-after-quote", "reopened-quote"],
)
def test_text_after_a_closing_quote_is_an_error_not_something_to_absorb(
    tmp_path: Path, field: str
) -> None:
    """``csv``'s default recovery silently appends it, producing a path that is accepted.

    ``"/proj/a.c"junk`` became ``/proj/a.cjunk`` and was reported as fact. It also made this
    module inconsistent with itself: a plainly padded ``" /proj/a.c "`` is refused as
    unmeasurable while ``"/proj/a.c" `` — the same uncertainty, quoted — was waved through.
    ``strict=True`` turns all of it into a ``csv.Error``, which the envelope types.
    """
    csv_path = write_csv(tmp_path, f"CheckID,File,Line,Violation\nR_01,{field},42,m\n")
    with pytest.raises(AnalysisFailedError) as caught:
        read_violations(csv_path)
    assert "could not be parsed" in words_of(caught, csv_path)


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ('"/proj/a,b.c"', "/proj/a,b.c"),
        ('"/proj/a""b.c"', '/proj/a"b.c'),
        ('/proj/a"b.c', '/proj/a"b.c'),
    ],
    ids=["embedded-comma", "doubled-quote", "bare-quote-mid-field"],
)
def test_strictness_costs_none_of_the_quoting_a_csv_is_entitled_to(
    tmp_path: Path, field: str, expected: str
) -> None:
    """Strict mode rejects malformed recovery, not legitimate quoting.

    A doubled quote is how a quote is written in a CSV, so collapsing it is the encoding
    rather than a transformation of the name.
    """
    body = f"CheckID,File,Line,Violation\nR_01,{field},42,m\n"
    assert only(read_violations(write_csv(tmp_path, body))).path == expected


@pytest.mark.parametrize(
    ("row", "outcome"),
    [
        ("R_01,util.c,42,m,native ,/proj", "/proj/native /util.c"),
        ("R_01,util.c,42,m, native,/proj", "/proj/ native/util.c"),
        ("R_01,util.c,42,m,,/proj ", "/proj /util.c"),
        ("R_01,util.c,42,m,, /proj", "REFUSED"),
    ],
    ids=["directory-trailing", "directory-leading", "root-trailing", "root-leading"],
)
def test_directory_and_root_are_taken_verbatim_too(tmp_path: Path, row: str, outcome: str) -> None:
    """The verbatim guarantee covers every column that locates, not only ``File``.

    ``Directory="native "`` composes to ``/proj/native /util.c`` — an ``rstrip`` would make
    that ``/proj/native/util.c``, a different file that probably does exist. ``Root=" /proj"``
    is refused for its whitespace-only first segment; an ``lstrip`` would turn that refusal
    into a composed path. Every case here changes under a sanitiser on the column it names,
    and all of them were reachable while only ``File`` was pinned.
    """
    csv_path = write_csv(tmp_path, f"{ANCHORED}\n{row}\n")
    if outcome == "REFUSED":
        with pytest.raises(AnalysisFailedError):
            read_violations(csv_path)
        return
    assert only(read_violations(csv_path)).path == outcome


def test_a_padded_path_is_refused_rather_than_quietly_trimmed(tmp_path: Path) -> None:
    """A deliberate change: this used to report ``/src/app.py`` and now refuses the row.

    Trimming assumed the CSV is padded the way a person pads one. Nothing measured says so,
    ``und`` writes this file, and the assumption cost a silent rename of every name with an
    edge space. Where two readings disagree this module refuses, so it refuses here too —
    loudly, quoting the value, on the first licensed run that produces one.
    """
    row = "R_01,Rule, /src/app.py ,42,7,main,too complex"
    csv_path = write_csv(tmp_path, f"{SMALL_HEADER}\n{row}\n")
    with pytest.raises(AnalysisFailedError) as caught:
        read_violations(csv_path)
    said = words_of(caught, csv_path).split("; the row was")[0]
    assert "' /src/app.py '" in said, "the refusal must quote the value it was given"
    assert "whitespace-only path segment" in said


def test_each_field_still_normalises_for_its_own_reasons(tmp_path: Path) -> None:
    """The path column normalises for none; the others keep the reasons they always had.

    A check id is an identifier and holds no whitespace, a check name and an entity are
    prose whose runs collapse, and a number is a number whatever surrounds it — none of
    which can misdirect a reader to a file that does not exist.
    """
    row = " R_01 , Rule  Name ,/src/app.py, 42 , 7 , main , too complex "
    found = only(read_violations(write_csv(tmp_path, f"{SMALL_HEADER}\n{row}\n")))
    assert (found.check_id, found.check_name, found.entity) == ("R_01", "Rule Name", "main")
    assert (found.line, found.column) == (42, 7)
    assert found.message == " too complex "


def test_a_byte_order_mark_does_not_hide_the_first_column(tmp_path: Path) -> None:
    """A CSV written on Windows may open with a BOM, which lands on the first header label."""
    body = f"\ufeff{SMALL_HEADER}\nR_01,Rule,/src/app.py,42,7,main,too complex\n"
    assert only(read_violations(write_csv(tmp_path, body))).check_id == "R_01"


def test_a_byte_that_is_not_utf8_does_not_stop_the_parse(tmp_path: Path) -> None:
    """A snippet quoted out of a latin-1 source must not cost every other violation."""
    target = tmp_path / "violations.csv"
    target.write_bytes(
        f"{SMALL_HEADER}\nR_01,Rule,/src/app.py,42,7,main,caf\xe9\n".encode("latin-1")
    )
    found = only(read_violations(target))
    assert (found.check_id, found.line) == ("R_01", 42)


# --- csv files that hold no violations, or nothing at all -------------------------


def test_a_header_only_csv_means_no_violations(tmp_path: Path) -> None:
    assert read_violations(write_csv(tmp_path, f"{SMALL_HEADER}\n")) == []


def test_a_csv_that_was_never_written_says_why_it_could_not_be_read(tmp_path: Path) -> None:
    """CodeCheck producing nothing is a failed run, and must not read as an empty report."""
    with pytest.raises(AnalysisFailedError) as caught:
        read_violations(tmp_path / "absent" / "violations.csv")
    complaint = str(caught.value)
    assert "violations.csv" in complaint
    assert os.strerror(2) in complaint


@pytest.mark.parametrize(
    "kind", ["directory", "unreadable"], ids=["is-a-directory", "permission-denied"]
)
def test_a_results_file_that_cannot_be_opened_at_all_is_still_typed(
    tmp_path: Path, kind: str
) -> None:
    """``except OSError`` is deliberately broad, and only its narrowest case was tested.

    A path that is a directory raises ``IsADirectoryError`` and a mode-000 file raises
    ``PermissionError`` — neither is a ``FileNotFoundError``, so narrowing the clause leaves
    both untyped and out of every caught-error tuple in the package.
    """
    target = tmp_path / "violations.csv"
    if kind == "directory":
        target.mkdir()
    else:
        target.write_text("x", encoding="utf-8")
        target.chmod(0o000)
    try:
        with pytest.raises(AnalysisFailedError) as caught:
            read_violations(target)
    finally:
        if kind != "directory":
            target.chmod(0o644)
    assert "could not be read" in words_of(caught, target)


def test_an_anchored_file_keeps_its_trailing_separator_and_the_mapper_drops_it(
    tmp_path: Path,
) -> None:
    """The one documented cross-boundary claim, asserted instead of assumed.

    An anchored ``File`` is returned as the value that was checked, so a trailing separator
    survives into ``RawViolation.path`` — the accepted form tolerates it and says
    ``map_violations`` drops it. The other trailing-separator test uses a *relative* ``File``,
    which is composed and so never exercises the survival.
    """
    row = "R_01,/proj/util.c/,42,message,,"
    found = only(read_violations(write_csv(tmp_path, f"{ANCHORED}\n{row}\n")))
    assert found.path == "/proj/util.c/", "the anchored value is returned as checked"
    assert map_violations([found], "warning", "/proj")[0].path == "util.c"


def test_an_empty_file_is_not_read_as_no_violations(tmp_path: Path) -> None:
    """Zero bytes means the header was never written, so nothing can be mapped."""
    csv_path = write_csv(tmp_path, "")
    with pytest.raises(AnalysisFailedError) as caught:
        read_violations(csv_path)
    assert "empty" in words_of(caught, csv_path).lower()


def test_a_csv_the_reader_itself_cannot_take_becomes_an_analysis_failure(
    tmp_path: Path,
) -> None:
    """``csv`` refuses a field past its own size limit with an error of its own type.

    A quoted ``Snippet`` is the column that could plausibly grow that far. ``runner`` and
    ``doctor`` catch the package's typed errors; a ``csv.Error`` is not one of them, so it
    would escape the envelope and surface as an unhandled crash with the wrong exit code.
    """
    oversized = "x" * (csv.field_size_limit() + 1)
    row = f'R_01,Rule,/src/a.py,42,7,main,"{oversized}"'
    csv_path = write_csv(tmp_path, f"{SMALL_HEADER}\n{row}\n")
    with pytest.raises(AnalysisFailedError) as caught:
        read_violations(csv_path)
    assert "could not be parsed" in words_of(caught, csv_path)


def test_anything_the_parse_throws_leaves_as_an_analysis_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The envelope is structural, not a list of the failures anyone thought of.

    ``int`` and pydantic's validation both raise ``ValueError``; no input reaching the
    module today produces one, which is exactly why the guarantee is asserted at the
    boundary instead of through whichever input happens to trip it.
    """
    row = "R_01,Rule,/src/app.py,42,7,main,too complex"
    csv_path = write_csv(tmp_path, f"{SMALL_HEADER}\n{row}\n")

    def explode(*_: object, **__: object) -> RawViolation:
        raise ValueError("something inside the parse went wrong")

    monkeypatch.setattr(codecheck, "_read_row", explode)
    with pytest.raises(AnalysisFailedError) as caught:
        read_violations(csv_path)
    assert "something inside the parse went wrong" in words_of(caught, csv_path)


# --- the runner ------------------------------------------------------------------


def test_run_asks_und_for_the_configuration_the_files_and_the_output_directory(
    tmp_path: Path,
) -> None:
    """The design hands paths around as strings here; ``UndCli`` wants real paths."""
    fake = FakeUndCli(violations_csv=write_csv(tmp_path, f"{SMALL_HEADER}\n"))
    database, out_dir = tmp_path / "after.und", tmp_path / "cc"
    CodeCheckRunner(fake).run(database, "Recommended", ["/src/a.py", "/src/b.py"], out_dir)
    assert fake.calls == [
        FakeCall(
            "codecheck",
            {
                "db": database,
                "config": "Recommended",
                "files": [Path("/src/a.py"), Path("/src/b.py")],
                "out_dir": out_dir,
            },
        )
    ]


def test_run_returns_the_violations_from_the_csv_und_wrote(tmp_path: Path) -> None:
    row = "R_01,Rule,/src/app.py,42,7,main,too complex"
    fake = FakeUndCli(violations_csv=write_csv(tmp_path, f"{SMALL_HEADER}\n{row}\n"))
    found = CodeCheckRunner(fake).run(tmp_path / "a.und", "Recommended", ["/src/app.py"], tmp_path)
    assert only(found).check_id == "R_01"


def test_run_refuses_an_empty_name_in_the_file_list(tmp_path: Path) -> None:
    """``Path("")`` is ``.``, which would ask ``und`` to check the whole working directory.

    This is the inbound normalisation — on the Gate's own selection rather than on anything
    CodeCheck wrote — and it is the one entry of it that does not name the same file it was
    given. ``a//b`` and ``./x`` do, so they pass through.
    """
    fake = FakeUndCli(violations_csv=tmp_path / "never-written.csv")
    with pytest.raises(AnalysisFailedError) as caught:
        CodeCheckRunner(fake).run(tmp_path / "a.und", "R", ["/src/a.py", ""], tmp_path / "cc")
    assert "position 1" in str(caught.value)
    assert fake.commands == []


@pytest.mark.parametrize(
    "name",
    ["", ".", "./", ".//", "././", "./.", ".///.", "..", "/", "/a/..", "/src/../."],
    ids=[
        "empty",
        "dot",
        "dot-slash",
        "dot-slashes",
        "dot-slash-twice",
        "dot-slash-dot",
        "mixed",
        "dot-dot",
        "root",
        "up-out-of-a",
        "up-then-here",
    ],
)
def test_every_spelling_of_the_working_directory_is_refused(tmp_path: Path, name: str) -> None:
    """Guarding the outcome, not the spelling: ``if not name`` caught exactly one of these.

    Every one names a directory rather than a file, which is what the guard exists to stop —
    yet ``if not name`` caught exactly one of them and ``str(Path(name)) == "."`` caught only
    the first seven. Enumerating spellings is what ``_unusable`` records as having left a gap
    every time; the predicate tests the outcome, ``Path(name).name``.
    """
    fake = FakeUndCli(violations_csv=tmp_path / "never-written.csv")
    with pytest.raises(AnalysisFailedError) as caught:
        CodeCheckRunner(fake).run(tmp_path / "a.und", "R", [name], tmp_path / "cc")
    assert "names a directory rather than a file" in str(caught.value)
    assert fake.commands == []


@pytest.mark.parametrize(
    ("name", "because"),
    [
        ("/src/a\nb.c", "line break"),
        ("/src/a\rb.c", "line break"),
        ("/src/a,b.py", "comma"),
        ("/src/caf\udce9.c", "encoded"),
        ("/src/a.c ", "whitespace at an edge"),
        (" /src/a.c", "whitespace at an edge"),
        ("/src/#hashed.c", "'#'"),
        ("/src/vic.c#1.c", "'#'"),
        ("/src/st*ar.c", "'*'"),
    ],
    ids=[
        "newline",
        "carriage-return",
        "comma",
        "surrogate",
        "trailing-space",
        "leading-space",
        "hash-prefix",
        "hash-interior",
        "wildcard",
    ],
)
def test_a_name_und_cannot_be_asked_about_is_refused(
    tmp_path: Path, name: str, because: str
) -> None:
    """The list file is ``und``'s format, and four of its rules bite legal posix names.

    Measured, each: a newline splits the entry in two, because ``_list_file`` writes one
    path per line with no escaping; a comma starts a line-number range (``und help
    codecheck``: "a comma delimited list of line numbers and ranges … after the file name");
    a surrogate from ``git``'s ``surrogateescape`` decoding raises ``UnicodeEncodeError``,
    a ``ValueError`` that is neither ``GateError`` nor ``OSError``; and ``und`` strips edge
    whitespace, answering ``/src/ a.c could not be resolved`` for ``/src/ a.c ``.

    ``#`` is the one an earlier round got backwards, on an ``Errors:0`` that could not fail.
    With a marker per file the answer is unambiguous: ``analyze -all`` reports all four
    markers, a ``-files`` list holding ``/src/#hashed.c`` reports none and exits 0, and
    ``/src/vic.c#1.c`` reports ``vic.c``'s marker — a different file checked in its place.
    ``und help analyze`` says so outright: "Lines in the file starting with # will be
    ignored."
    ``*`` is glob-expanded and fails loudly, which is milder but still unaskable.

    A file that cannot be *asked* about is a file that goes unchecked, which is a clean
    report on code nobody looked at — so it is refused rather than silently altered.
    """
    fake = FakeUndCli(violations_csv=tmp_path / "never-written.csv")
    with pytest.raises(AnalysisFailedError) as caught:
        CodeCheckRunner(fake).run(tmp_path / "a.und", "R", ["/src/ok.c", name], tmp_path / "cc")
    assert because in str(caught.value)
    assert "position 1" in str(caught.value)
    assert caught.value.hint is not None
    assert "codecheck.config" in caught.value.hint, "the hint must name the operator's lever"
    assert fake.commands == []


@pytest.mark.parametrize(
    ("name", "because"),
    [
        ("/repo/back\\slash.c", "backslash"),
        ("/repo/back\\dir/a.c", "backslash"),
    ],
    ids=["leaf", "directory-component"],
)
def test_a_backslash_is_refused_because_und_rewrites_it(
    tmp_path: Path, name: str, because: str
) -> None:
    """Measured: ``und`` turns ``\\`` into ``/`` inside a list-file entry.

    With ``src/back/slash.c`` and a literal ``src/back\\slash.c`` both on disk, ``und add``
    takes only the first — and a ``-files`` entry naming the literal one answers
    ``File: …/src/back/slash.c`` with that file's marker, rc 0. A different file analysed in
    place of the one asked for, silently: the same harm as ``vic.c#1.c``.

    The outbound side already treats ``a\\b.c`` as a real posix name, and :func:`_slashes`
    documents rewriting it as a cost — the inbound side simply never mentioned it.
    """
    fake = FakeUndCli(violations_csv=tmp_path / "never-written.csv")
    with pytest.raises(AnalysisFailedError) as caught:
        CodeCheckRunner(fake).run(tmp_path / "a.und", "R", [name], tmp_path / "cc")
    assert because in str(caught.value)
    assert fake.commands == []


@pytest.mark.parametrize(
    ("name", "because"),
    [
        ("/src/wo#rk/a.c", "'#'"),
        ("/src/st*ar/a.c", "'*'"),
        ("/src/di,r/a.c", "comma"),
        ("/src/two\nlines/a.c", "line break"),
        ("/src/two\rlines/a.c", "line break"),
        ("/src/back\\dir/a.c", "backslash"),
    ],
    ids=["hash", "wildcard", "comma", "newline", "carriage-return", "backslash"],
)
def test_a_hazard_in_a_directory_component_is_refused_too(
    tmp_path: Path, name: str, because: str
) -> None:
    """Every other fixture puts the hazard in the leaf, which left the scope unpinned.

    Scoping any of these bans to ``Path(name).name`` survives the rest of the suite, and the
    harm is real: ``analyze -files`` on ``/…/wo#rk/a.c`` answers ``Error: wo could not be
    resolved. Skipping file.`` and never checks it, while ``analyze -all`` reports its
    marker. A hazard anywhere in the line is a hazard.
    """
    fake = FakeUndCli(violations_csv=tmp_path / "never-written.csv")
    with pytest.raises(AnalysisFailedError) as caught:
        CodeCheckRunner(fake).run(tmp_path / "a.und", "R", [name], tmp_path / "cc")
    assert because in str(caught.value)
    assert fake.commands == []


@pytest.mark.parametrize(
    "name",
    ["src/plain.c", "plain.c", "a/b/c.py"],
    ids=["one-level", "bare-name", "nested"],
)
def test_a_relative_name_is_refused_because_und_resolves_it_from_its_own_directory(
    tmp_path: Path, name: str
) -> None:
    """Measured: run from ``/tmp``, ``src/plain.c`` became ``/tmp/src/plain.c``.

    Usually loud — nothing is there — but wherever a same-named file *does* exist under
    ``und``'s directory it checks that one instead, with no error at all. These names all
    have a real final component, so the directory-outcome guard does not catch them; this is
    the only test that does.
    """
    fake = FakeUndCli(violations_csv=tmp_path / "never-written.csv")
    with pytest.raises(AnalysisFailedError) as caught:
        CodeCheckRunner(fake).run(tmp_path / "a.und", "R", [name], tmp_path / "cc")
    assert "is relative" in str(caught.value)
    assert fake.commands == []


@pytest.mark.parametrize(
    "name",
    ["/src/a b.c", "/src/a\tb.c", "/src/q?mark.c", "/src/br[a]ck.c"],
    ids=["interior-space", "interior-tab", "question-mark", "bracket"],
)
def test_a_name_und_was_measured_to_handle_is_passed_through(tmp_path: Path, name: str) -> None:
    """Measured *not* to be hazards, so not refused — and the ban stops where evidence does.

    ``?`` and ``[`` are the interesting pair: ``und`` calls ``*`` a "wild card", and it
    would have been easy to refuse the other two glob characters by analogy. Measured with
    a marker per file, ``q?mark.c`` and ``br[a]ck.c`` were both analysed normally.
    """
    fake = FakeUndCli(violations_csv=write_csv(tmp_path, f"{SMALL_HEADER}\n"))
    CodeCheckRunner(fake).run(tmp_path / "a.und", "R", [name], tmp_path / "cc")
    assert fake.calls[0].arguments["files"] == [Path(name)]


def test_run_passes_a_name_that_normalises_to_the_same_file(tmp_path: Path) -> None:
    """``a//b`` and an interior ``/./`` are the harmless half, and stay allowed.

    ``./x`` is no longer among them: a relative name resolves against ``und``'s own
    directory, so it is refused whatever it normalises to.
    """
    fake = FakeUndCli(violations_csv=write_csv(tmp_path, f"{SMALL_HEADER}\n"))
    CodeCheckRunner(fake).run(
        tmp_path / "a.und", "R", ["/src//a.py", "/src/./b.py"], tmp_path / "cc"
    )
    assert fake.calls[0].arguments["files"] == [Path("/src/a.py"), Path("/src/b.py")]


def test_run_without_files_starts_no_process(tmp_path: Path) -> None:
    """An empty selection has nothing to check; ``und`` would write no csv and fail."""
    fake = FakeUndCli(violations_csv=tmp_path / "never-written.csv")
    assert CodeCheckRunner(fake).run(tmp_path / "a.und", "Recommended", [], tmp_path) == []
    assert fake.commands == []


# --- contract: a bundled configuration against the real und ----------------------

CONFIGURATION_VARIABLE = "SCITOOLS_HOOK_CODECHECK_CONFIG"
"""Names the configuration the contract run uses, for a machine whose default has no checks."""

DEFAULT_CONFIGURATION = "Sandbox"
"""Understand's per-project scratch configuration, kept in ``<db>/codecheck/configs``.

**No configuration ships with Understand and none is created by ``und create``**: a freshly
built database holds only ``id.txt``, ``local/`` and ``settings.xml``, with no ``codecheck``
directory at all (measured). ``Sandbox`` is the name the GUI gives its scratch configuration
once one exists, so on the first licensed machine this run will very likely report nothing
and
``test_contract_the_configuration_reports_violations_the_gate_can_place`` will fail red,
naming :data:`CONFIGURATION_VARIABLE`. That is the intended trade — a red test naming its
own fix, rather than a green one proving nothing — and not a regression.
"""


NO_CSV_EXPORTS = (
    "Understand 8.0 writes results.sarif from `und codecheck` and, by default, one CSV report, "
    "CodeCheckResultsByTable.csv, from plugins/Solutions/codecheck6Compatability with the "
    "columns File, Violation, Line, Column, Entity, Kind, CheckID, Check Name, Check Short "
    "Description, Severity (read off the install and `und help codecheck`). The three 6.5 "
    "exports this package reads are gone from its `und`. The licence on the measuring machine "
    "excludes CodeCheck, so the 8.0 output is unmeasured and the integration is not adapted "
    "to it yet."
)
"""Why the CodeCheck contract cannot be checked on 8.0 yet -- an expected failure, not a skip."""


def require_csv_exports(und: Path) -> None:
    """Expected-fail on a build whose ``und`` no longer carries the CSV export this reads.

    An xfail rather than a skip so the suite keeps saying, run after run, that the contract is
    open on this build; a skip would read as "nothing to check here".
    """
    if VIOLATIONS_EXPORT.encode() not in und.read_bytes():
        pytest.xfail(NO_CSV_EXPORTS)


class SampleSet(Protocol):
    """The part of ``conftest.SampleDatabases`` these tests use.

    Declared rather than imported for the reason task 6.1 recorded: ``from conftest import``
    breaks under ``--import-mode=importlib``.
    """

    und: Path
    after_db: Path

    def list_files(self, side: Literal["before", "after"]) -> list[str]: ...


@dataclass(frozen=True)
class CodeCheckRun:
    """One real CodeCheck run, so the contract tests below share a single invocation."""

    violations: list[RawViolation]
    out_dir: Path
    analyzed: set[str]
    config: str


@pytest.fixture(scope="module")
def codecheck_run(
    sample_databases: SampleSet, tmp_path_factory: pytest.TempPathFactory
) -> CodeCheckRun:
    """Run CodeCheck for real, once, and skip the contract tests when it is not licensed.

    The skip is a *probe*, never a decision: the run is attempted, and only ``und`` itself
    saying the license is missing skips. On a machine that carries the CodeCheck license the
    same call runs for real and the tests below assert on its output.
    """
    require_csv_exports(sample_databases.und)
    runner = CodeCheckRunner(UndCli(understand_env(sample_databases.und), _null_log()))
    out_dir = tmp_path_factory.mktemp("codecheck")
    config = os.environ.get(CONFIGURATION_VARIABLE, DEFAULT_CONFIGURATION)
    analyzed = sample_databases.list_files("after")
    try:
        found = runner.run(sample_databases.after_db, config, analyzed, out_dir)
    except LicenseError as unlicensed:
        said = " ".join(unlicensed.und_output.split()) or str(unlicensed)
        pytest.skip(f"this Understand license excludes CodeCheck: und said {said!r}")
    return CodeCheckRun(found, out_dir, set(analyzed), config)


@pytest.mark.contract
def test_contract_the_per_violation_export_carries_a_header_und_still_writes(
    codecheck_run: CodeCheckRun,
) -> None:
    """The schema the whole parser rests on, asserted against a real run (req 6.9).

    A configuration that selects no checks cannot dodge this one: the export and its header
    are written whether or not anything was found. A header that is neither of the two
    compiled into ``und`` is a real finding — record it and widen ``COLUMN_NAMES``.
    """
    report = codecheck_run.out_dir / f"{VIOLATIONS_EXPORT}.csv"
    written = sorted(path.name for path in codecheck_run.out_dir.glob("*.csv"))
    assert report.is_file(), f"und wrote {written} but not {report.name}"
    header = report.read_text(encoding="utf-8", errors="replace").splitlines()[0]
    assert header in (VIOLATION_HEADER, FILES_TREE_HEADER), (
        f"und wrote a header this build has not seen before: {header!r}"
    )


@pytest.mark.contract
def test_contract_the_configuration_reports_violations_the_gate_can_place(
    codecheck_run: CodeCheckRun,
) -> None:
    """Every field whose meaning could not be measured, asserted against real rows.

    This is the only loud path for a configuration that names nothing: ``und codecheck`` has
    no "unknown configuration" message at all, and ``-exitstatus`` is deliberately not
    passed, so a run against a name that selects no checks succeeds and reports nothing.
    A zero-violation result therefore fails here rather than passing while proving nothing.
    """
    assert codecheck_run.violations, (
        f"{codecheck_run.config!r} reported no violations, so nothing about a real row was "
        f"checked; point {CONFIGURATION_VARIABLE} at a configuration that selects checks"
    )
    for violation in codecheck_run.violations:
        assert violation.check_id, f"a violation arrived with no check id: {violation}"
        assert violation.path in codecheck_run.analyzed, (
            f"{violation.path} is not one of the files und analyzed"
        )
        assert violation.line == NO_LINE or violation.line > 0, (
            f"a line number is either absent or a real position: {violation}"
        )


@pytest.mark.contract
def test_contract_the_fixture_headers_and_export_names_are_compiled_into_und(
    sample_databases: SampleSet,
) -> None:
    """Pin every borrowed string to its source, so a build that changes one fails here.

    The headers and the export filenames are both facts about ``und`` that this package
    hard-codes, and both are readable straight out of the executable without a CodeCheck
    license — so neither has any excuse to be asserted against itself.
    """
    require_csv_exports(sample_databases.und)
    executable = sample_databases.und.read_bytes()
    borrowed = (
        VIOLATION_HEADER,
        FILES_TREE_HEADER,
        VIOLATIONS_EXPORT,
        "CodeCheckResultByFile",
        "CodeCheckResultByTable",
    )
    for text in borrowed:
        assert text.encode() in executable, f"{text!r} is no longer in {sample_databases.und}"


def understand_env(und: Path) -> UnderstandEnv:
    """The minimal :class:`UnderstandEnv` the wrapper needs: it only ever reads ``und``."""
    home = und.parent
    return UnderstandEnv(
        home=home,
        und=und,
        upython=None,
        python_api_dir=home / "Python",
        version="(Build 1204)",
        source="test",
        api_mode="upython",
    )


class _NullLog:
    """A ``CommandLog`` for the contract tests, which assert on Understand not on logging."""

    def record(self, argv: list[str], seconds: float, rc: int) -> None:
        """Discard one finished command."""


def _null_log() -> CommandLog:
    """The discarding command log, typed as the port the wrapper expects."""
    return _NullLog()
