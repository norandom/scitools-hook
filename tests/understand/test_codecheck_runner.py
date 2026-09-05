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
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

import pytest
from codecheck_csv import (
    ANCHORED,
    EXPECTED,
    FILES_TREE_HEADER,
    FULL_ROW,
    GROUP_ROW,
    SMALL_HEADER,
    TREE_ROW,
    VIOLATION_HEADER,
    only,
    words_of,
    write_csv,
)
from fakes import FakeCall, FakeUndCli

from scitools_hook.analysis.codecheck import map_violations
from scitools_hook.errors import AnalysisFailedError, LicenseError
from scitools_hook.models.progress import CommandLog
from scitools_hook.models.understand import RawViolation, UnderstandEnv
from scitools_hook.understand import codecheck
from scitools_hook.understand.codecheck import (
    NO_LINE,
    CodeCheckRunner,
    read_report,
    read_violations,
)
from scitools_hook.understand.codecheck_sarif import RESULTS_SARIF
from scitools_hook.understand.und_cli import (
    VIOLATIONS_EXPORT,
    UndCli,
)

INSPECTION: dict[str, object] = {
    "version": "2.1.0",
    "runs": [
        {
            "tool": {
                "driver": {
                    "name": "CodeCheck",
                    "rules": [{"id": "CPP_F022", "name": "Cyclomatic Complexity"}],
                }
            },
            "artifacts": [{"location": {"uri": "src/app.py"}}],
            "results": [
                {
                    "ruleId": "CPP_F022",
                    "message": {"text": "Function main is too complex"},
                    "locations": [
                        {
                            "physicalLocation": {
                                "artifactLocation": {"index": 0},
                                "region": {"startLine": 42, "startColumn": 7},
                            }
                        }
                    ],
                }
            ],
        }
    ],
}
"""One violation, as ``und codecheck`` on 8.0 is documented to report it."""


def write_sarif(base: Path) -> Path:
    """One synthetic ``results.sarif``, in the shape 8.0 is documented to write it.

    Synthetic and not measured, for the reason ``understand/codecheck_sarif.py`` records: the
    licence here excludes CodeCheck. The mapping itself is covered by
    ``tests/understand/test_codecheck_sarif.py``; this one only has to be readable, so that
    the runner's *choice* of reader is what the assertion is about.
    """
    out_dir = base / "sarif"
    out_dir.mkdir(parents=True, exist_ok=True)
    written = out_dir / RESULTS_SARIF
    written.write_text(json.dumps(INSPECTION), encoding="utf-8")
    return written


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


CODECHECK6_COMPAT_HEADER = (
    "File,Violation,Line,Column,Entity,Kind,CheckID,Check Name,Check Short Description,Severity"
)
"""8.0's default ``CodeCheckResultsByTable.csv``, read off its ``codecheck6Compatability`` plugin.

The build writes it from a plugin rather than from ``und`` itself, and the plugin leaves
``Check Name``, ``Check Short Description`` and ``Severity`` empty (it tests its columns under
other names). The run needs a CodeCheck licence this machine lacks, so the header is from the
plugin source and the row is what its code would print, not a transcript.
"""


def test_the_header_understand_8_writes_by_default_is_read(tmp_path: Path) -> None:
    """The four required columns are there under their 6.5 names; the rest may be empty.

    An empty ``Check Name`` falls back to the id, as it does for any row; the finding then
    names the check by its id, which is what 8.0's report leaves the reader with anyway.
    """
    row = "/src/app.py,Function is too complex,42,7,main,Function,RECOMMENDED_04,,,"
    found = only(read_violations(write_csv(tmp_path, f"{CODECHECK6_COMPAT_HEADER}\n{row}\n")))
    assert found == RawViolation(
        check_id="RECOMMENDED_04",
        check_name="RECOMMENDED_04",
        path="/src/app.py",
        line=42,
        column=7,
        message="Function is too complex",
        entity="main",
    )


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


def test_run_returns_the_violations_from_the_sarif_when_that_is_what_und_wrote(
    tmp_path: Path,
) -> None:
    """Requirement 2.3: 8.0 writes ``results.sarif`` and no per-violation CSV.

    The wrapper hands back whichever report the build wrote and the runner reads it. Nothing
    downstream is told which one it was, which is the point -- ``map_violations`` sees the
    same records either way.
    """
    fake = FakeUndCli(violations_csv=write_sarif(tmp_path))
    found = CodeCheckRunner(fake).run(tmp_path / "a.und", "Recommended", ["/src/app.py"], tmp_path)
    assert only(found).check_id == "CPP_F022"
    assert only(found).path == "src/app.py"


def test_the_report_is_chosen_by_the_name_und_writes_and_not_by_a_suffix(
    tmp_path: Path,
) -> None:
    """``results.sarif`` is measured; a ``.sarif`` suffix on anything else is a convention."""
    row = "R_01,Rule,/src/app.py,42,7,main,too complex"
    assert only(read_report(write_csv(tmp_path, f"{SMALL_HEADER}\n{row}\n"))).check_id == "R_01"
    assert only(read_report(write_sarif(tmp_path))).check_id == "CPP_F022"


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
"""Why the CodeCheck contract cannot be checked on 8.0 yet.

An expected failure rather than a skip, so the suite keeps saying, run after run, that the
contract is open on this build; a skip would read as "nothing to check here". The condition
is the one fact both sites can read without a CodeCheck licence: whether ``und`` still
carries the name of the export this package looks for.
"""


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
    if VIOLATIONS_EXPORT.encode() not in sample_databases.und.read_bytes():
        pytest.xfail(NO_CSV_EXPORTS)
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
    executable = sample_databases.und.read_bytes()
    if VIOLATIONS_EXPORT.encode() not in executable:
        pytest.xfail(NO_CSV_EXPORTS)
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
