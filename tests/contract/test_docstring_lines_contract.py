"""Docstring accounting on the installed build (lean-code-rules 6.5).

Understand lexes a Python docstring as a ``String`` token and not a ``Comment``, so the token
index cannot treat it as the metrics do. The first two tests measure both sides on the
fixture's Python. Per routine: what ``CountLine``, ``CountLineCode``, ``CountLineComment`` and
``CountStmt`` say about a one-line docstring, and what the index says -- whether its line is a
code line in ``tokens.files`` and how many ``LIT`` tokens it puts in the shape. Per file: the
same metrics against a line budget read off the fixture text with Python's own ``tokenize``,
which is a second reading of the source rather than the worker checking itself, because the
record carries two claims that read as contradictory -- ``research.md`` says docstring lines
are comment lines, ``analysis/lean/layering.py`` says a multi-line module docstring is charged
as code lines -- and only a measurement of files with multi-line docstrings can say which. The
third test builds a project of docstring-only initialisers for the ``layering.py`` claim
itself, under both Python grammars. Every table is printed by the test so the research log
and the run cannot disagree.

The coverage of the index is ``test_token_index_contract``'s subject and the exact finding
set ``test_token_rules_contract``'s; ``token_index_project`` holds what the three share.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from contract_project import (
    SOURCES,
    SampleProject,
    extract_with,
    run_und,
    sample_project,  # noqa: F401 -- imported so the session fixture is registered here
    write_tree,
)
from token_index_project import (
    LINE_METRICS,
    index_of,
    keys_of,
    line_budget,
    number_of,
    print_table,
    token_settings,
    token_snapshot,
)

from scitools_hook.models.snapshot import EntityKey, ProjectSnapshot, TokenIndex
from scitools_hook.understand.worker_lean import LITERAL_SHAPE

pytestmark = pytest.mark.contract

WITH_DOCSTRING = (
    "layers.canonical_name",
    "layers.display_name",
    "layers.open_channel",
    "dead.advance",
)
"""The fixture's Python routines carrying a one-line docstring and nothing else in prose."""

WITHOUT_DOCSTRING = ("layers.BaseChannel.send", "twin_left.summarise_orders")
"""Two Python routines with no docstring, the control the table needs."""

DOCSTRING_CLASSES = ("dead.ForgottenReport", "layers.OnlyChannel")
"""A class whose whole body is its docstring, and one with a docstring and a method."""


def _routine_row(snapshot: ProjectSnapshot, index: TokenIndex, key: EntityKey) -> dict[str, object]:
    """What the metrics and the index each say about one Python routine."""
    record = snapshot.entities[key]
    shape = index.routines[key.token]
    literals = sum(1 for number in shape.shape if index.vocabulary[number] == LITERAL_SHAPE)
    indexed = [line for line, _ in index.files[shape.path] if shape.start <= line <= shape.end]
    return {
        "routine": key.longname,
        **{name: record.metrics.get(name) for name in LINE_METRICS},
        "span": f"{shape.start}-{shape.end}",
        "indexed lines": len(indexed),
        "LIT tokens": literals,
        "shape.statements": shape.statements,
    }


def test_contract_a_routine_docstring_is_a_comment_to_the_metrics_and_a_line_to_the_index(
    sample_project: SampleProject,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Requirement 6.5 at routine level, re-measured on the fixture's Python.

    A one-line docstring is one comment line, no code line and no statement to Understand; to
    the index it is one code line -- every line of the span is indexed -- and exactly one
    ``LIT`` token, whatever its length. The controls without a docstring show the difference.
    """
    snapshot = token_snapshot(sample_project, ("routine",))
    index = index_of(snapshot)
    by_name = {key.longname: key for key in keys_of(snapshot, "routine")}
    rows = [_routine_row(snapshot, index, by_name[name]) for name in WITH_DOCSTRING]
    controls = [_routine_row(snapshot, index, by_name[name]) for name in WITHOUT_DOCSTRING]
    print_table(capsys, [*rows, *controls])

    for row in rows:
        assert row["CountLineComment"] == 1.0, row
        assert number_of(row, "CountLine") == number_of(row, "CountLineCode") + 1.0, row
        assert row["CountStmt"] == row["CountLineCode"], row
        assert row["indexed lines"] == row["CountLine"], row
        assert row["LIT tokens"] == 1, row
        assert row["shape.statements"] == row["CountStmt"], row
    for row in controls:
        assert row["CountLineComment"] == 0.0, row
        assert row["CountLine"] == row["CountLineCode"], row
        assert row["indexed lines"] == row["CountLine"], row


def _file_row(snapshot: ProjectSnapshot, index: TokenIndex, key: EntityKey) -> dict[str, object]:
    """What the metrics, the index and ``tokenize`` each say about one Python file."""
    budget = line_budget(SOURCES[key.path])
    return {
        "file": key.path,
        **{name: snapshot.entities[key].metrics.get(name) for name in LINE_METRICS},
        "lines": budget.total,
        "blank": budget.blank,
        "docstrings": budget.docstrings,
        "docstring prose": budget.prose,
        "docstring blank": budget.docstring_blank,
        "plain code": budget.plain,
        "indexed lines": len(index.files[key.path]),
        "first indexed": index.files[key.path][0][0],
    }


def _class_row(snapshot: ProjectSnapshot, key: EntityKey) -> dict[str, object]:
    """What the metrics say about one Python class; classes have no shape in the index."""
    return {
        "class": key.longname,
        **{name: snapshot.entities[key].metrics.get(name) for name in LINE_METRICS},
    }


def test_contract_a_module_docstring_is_comment_lines_to_the_metrics_and_one_line_to_the_index(
    sample_project: SampleProject,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Requirement 6.5 at file and class level, against a ``tokenize`` line budget.

    The claim under test is ``research.md``'s: every docstring line is a comment line and no
    docstring line is a code line, multi-line module docstrings included, so a file's
    ``CountLineCode`` is its plain code and ``CountLineComment`` its docstring prose -- a
    blank line inside a docstring is blank to Understand, not comment. The index disagrees by
    design: a docstring is one ``String`` token and so one indexed line at the line it opens
    on, which is why a file's indexed lines are its plain code plus one per docstring, and
    why a Python file is indexed from line 1 while a C++ file opening with ``//`` comments is
    indexed from its first code line. The class rows are the same accounting one scope up,
    on a class that is nothing but its docstring and on one with a method.
    """
    snapshot = token_snapshot(sample_project, ("file", "class"))
    index = index_of(snapshot)
    python_files = sorted(
        (key for key in keys_of(snapshot, "file") if key.path.endswith(".py")),
        key=lambda key: key.path,
    )
    files = [_file_row(snapshot, index, key) for key in python_files]
    print_table(capsys, files)
    by_name = {key.longname: key for key in keys_of(snapshot, "class")}
    classes = [_class_row(snapshot, by_name[name]) for name in DOCSTRING_CLASSES]
    print_table(capsys, classes)

    assert len(files) == 11, [row["file"] for row in files]
    for row in files:
        assert row["CountLine"] == row["lines"], row
        assert row["CountLineComment"] == row["docstring prose"], row
        assert row["CountLineCode"] == row["plain code"], row
        one_per_docstring = number_of(row, "plain code") + number_of(row, "docstrings")
        assert row["indexed lines"] == one_per_docstring, row
        assert row["first indexed"] == 1, row
    assert [tuple(row[name] for name in LINE_METRICS) for row in classes] == [
        (2.0, 1.0, 1.0, 1.0),
        (6.0, 4.0, 1.0, 4.0),
    ]
    assert index.files["native/lean_twin_left.cpp"][0][0] == 13
    assert index.files["native/lean_twin_right.cpp"][0][0] == 4


OLD_INITIALISER = '''"""The lean-code rules: what a change leaves behind that nothing needs.

Six rules and a delta, each a pure function over a :class:`~scitools_hook.models.snapshot.
ProjectSnapshot` and the affected set, in the shape the structural rules of
:mod:`scitools_hook.analysis.structure` already have. The package exists because six rule
modules and a delta are not one module, and because the family shares one configuration
section, one severity convention -- ``Severity | None``, where ``None`` is off -- and one
promise: it imports ``config`` and ``models`` and nothing else.

The promise has exactly one exception, named in the design and taken by :mod:`.net` alone:
``analysis.ratchet.pair_changed_signatures``, which joins a routine whose parameter list
changed to the key it had before. That join already exists, is the only identity work in this
layer that is not local to one snapshot, and a second copy of it would drift; see :mod:`.net`
for what it costs the delta to go without.
"""
'''
"""``analysis/lean/__init__.py`` as it stood at commit ``acd741c``, byte for byte.

The sample ``layering.py``'s claim was made on: fifteen lines, thirteen of prose with two
blank lines inside, Sphinx roles and double backticks, and no code. Carried verbatim so the
probe measures the claim's own sample and not a stand-in that might lack whatever triggered it.
"""

INITIALISERS: dict[str, str] = {
    "prose/__init__.py": '"""A package whose initialiser is prose.\n\nThree lines of it.\n"""\n',
    "line/__init__.py": '"""A package whose initialiser is one line of prose."""\n',
    "old/__init__.py": OLD_INITIALISER,
    "prose/module.py": '"""A module with a docstring and code."""\n\n\ndef code():\n    return 1\n',
}
"""Three docstring-only initialisers and a module with code.

``analysis/lean/layering.py`` records that Understand charged this repository's own
``analysis/lean/__init__.py`` -- a multi-line docstring and nothing else -- as code lines,
while every one-line-docstring initialiser measured zero. The contract fixture holds no
docstring-only file, so the claim needs a project of its own: a short multi-line docstring, a
one-line one, the very text the claim was made on, and ``prose/module.py`` so that ``prose``
is a package with something in it and not a directory of prose alone.
"""


GRAMMARS = ("Python2", "Python3")
"""Both values of Understand's ``PythonSetVersion``, because the grammar is a confounder.

``und`` decides the dialect by running a bare ``python`` from ``PATH`` and analyses Python 2
when it finds none (``understand/locator.py``, ``PIN_HINT``). This repository is dogfooded
with the Gate's pinned Python 3, which is where ``layering.py`` measured; the contract fixture
builds with no pin and measured ``PythonSetVersion Python2`` on the scratch database of the
first draft of this test. So the claim is measured under both, each database pinned
explicitly with ``und settings`` and the value read back rather than inferred from ``PATH``.
"""


def grammar_database(tmp_path: Path, version: str) -> tuple[Path, Path]:
    """The initialiser project analysed under one Python grammar, pinned and read back."""
    root = write_tree(tmp_path / version / "root", INITIALISERS)
    db = tmp_path / version / "root.und"
    for argv in (
        ["-quiet", "create", "-db", str(db), "-languages", "python", "-local"],
        ["settings", "-PythonSetVersion", version, str(db)],
        ["-quiet", "-db", str(db), "add", str(root)],
        ["-db", str(db), "analyze", "-all", "-errors", "-warnings"],
    ):
        done = run_und(*argv)
        assert done.returncode == 0, f"und {' '.join(argv)}: {done.stderr.strip()}"
    listing = run_und("list", "-all", "settings", str(db)).stdout
    assert re.search(rf"PythonSetVersion\s+{version}\b", listing), listing
    return db, root


def initialiser_rows(db: Path, root: Path, version: str) -> list[dict[str, object]]:
    """The file metrics, the ``tokenize`` budget and the index for one grammar's database."""
    snapshot = extract_with(db, root, tuple(sorted(INITIALISERS)), token_settings(("file",)))
    index = index_of(snapshot)
    rows: list[dict[str, object]] = []
    for key in sorted(keys_of(snapshot, "file"), key=lambda key: key.path):
        budget = line_budget(INITIALISERS[key.path])
        rows.append(
            {
                "grammar": version,
                "file": key.path,
                **{name: snapshot.entities[key].metrics.get(name) for name in LINE_METRICS},
                "docstring prose": budget.prose,
                "plain code": budget.plain,
                "indexed lines": len(index.files[key.path]),
            }
        )
    return rows


def test_contract_a_docstring_only_initialiser_is_no_code_line_under_either_python_grammar(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The ``layering.py`` claim, measured on a database built for it (requirement 6.5).

    **The claim did not reproduce.** ``analysis/lean/layering.py`` records that Understand
    charged this repository's multi-line docstring-only initialiser as code lines, inferred
    from a dependency count that fell from eight to seven when the docstring was shortened to
    one line. Measured here on that initialiser's own text, byte for byte, under both Python
    grammars: ``CountLineCode`` 0 and ``CountLineComment`` 13 either way, exactly as the short
    multi-line and the one-line initialisers beside it and as the fixture's module docstrings
    measure. The accounting is one rule at every level this feature has measured -- a
    non-blank docstring line is a comment line and never a code line -- and the eight-to-seven
    change had some other cause, which this test does not pursue.

    The assertion is written to fail if either grammar ever answers the way the paragraph
    says, so the record and the build cannot drift apart again in silence.
    """
    rows = []
    for version in GRAMMARS:
        db, root = grammar_database(tmp_path, version)
        rows.extend(initialiser_rows(db, root, version))
    print_table(capsys, rows)

    assert [str(row["file"]) for row in rows] == [*sorted(INITIALISERS)] * len(GRAMMARS)
    for row in rows:
        assert row["CountLineCode"] == row["plain code"], row
        assert row["CountLineComment"] == row["docstring prose"], row
        assert row["indexed lines"] == number_of(row, "plain code") + 1, row
    assert [row["CountLineComment"] for row in rows] == [1.0, 13.0, 3.0, 1.0] * len(GRAMMARS)
