"""The CSV CodeCheck writes, as the parser tests replay it: ``und``'s headers, byte for byte.

The two header lines are read straight out of the 6.5 ``und`` executable (the contract test
``test_contract_the_fixture_headers_and_export_names_are_compiled_into_und`` pins them there),
so the parser is tested against what CodeCheck wrote rather than against a spelling invented
for the test. Shared by the three CodeCheck test modules.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scitools_hook.models.understand import RawViolation

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


ANCHORED = "CheckID,File,Line,Violation,Directory,Root"
"""A header with both anchor columns, so a refusal cannot be passing for want of one."""
