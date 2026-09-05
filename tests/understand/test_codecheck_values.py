"""Values in a CodeCheck row that are missing, awkward or quoted (task 6.7).

A line that is empty or not a number, a column that is blank, a message holding the
separator, a quoted field with a newline inside it: the parser reads the header it is given
and then has to make one ``RawViolation`` out of whatever the row holds, or refuse it with
the row's own words. The headers and rows come from ``codecheck_csv``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from codecheck_csv import (
    ANCHORED,
    FILES_TREE_HEADER,
    SMALL_HEADER,
    VIOLATION_HEADER,
    only,
    words_of,
    write_csv,
)

from scitools_hook.errors import AnalysisFailedError
from scitools_hook.understand.codecheck import NO_LINE, read_violations

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
