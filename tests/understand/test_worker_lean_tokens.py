"""The token index ``worker_lean`` builds for the duplication rules (requirements 5.4, 5.8, 9.7).

Two rules read this one document and they read different halves of it, so the cases below are
arranged around what each half has to survive:

* **The exact-duplicate rule** reads ``files``: one hash per *code* line, with the five token
  classes that carry no code dropped, so that a re-indented, re-commented copy hashes equal to
  its original (requirement 5.4). The line numbers stay the file's own, because a finding names
  a range a reader opens; a rule given a renumbered index would point at the wrong lines.
* **The similar-routine rule** reads ``routines``: one shape per routine, with identifiers and
  literals mapped to two classes and keyword, operator and punctuation text kept, so that a
  renamed copy compares equal to its original and a copy with a different operator does not.
* **Both** read ``unreadable``. A file whose lexer refuses is not a file with nothing in it
  (requirement 5.8), and the two must not arrive as the same absence: one is reported once per
  run, the other is a file with no duplicates in it.

**Every dropped-class case carries non-empty text on purpose.** An ``Indent`` or a ``Dedent``
whose text were empty would be dropped by the empty-text rule whatever the class map said, and
the case for that member of the set could then never fail. The empty text has a case of its
own, below, written on a class nothing drops.

**The path the walk recorded is deliberately not the path any of these files has.** The index
takes a routine's file from the ``Definein`` reference it clips the shape from, not from the
second half of the extractor's ``routine_ents`` pair: that half comes from ``definein,
declarein``, and for a C++ method with a header declaration nothing measured here says which
of the two a build answers first -- so it is not known to be the file the body's lines are in.
:data:`RECORDED_ELSEWHERE` is what a shape would carry if an implementation started reading
it, and no assertion below would pass.
"""

from __future__ import annotations

from typing import Any, Final

import pytest
from api_fakes import FakeEnt, FakeRef
from worker_projects import FILE_KIND, a_context, a_file, a_routine

from scitools_hook.config.metric_names import SCOPE_KINDS
from scitools_hook.models.snapshot import TokenIndex
from scitools_hook.understand import worker_lean

RECORDED_ELSEWHERE: Final = "some/other/declaration.h"
"""The path half of the extractor's routine map, which the index must never read."""

DEF_RUN: Final = [
    ("Keyword", "def", 1),
    ("Whitespace", " ", 1),
    ("Identifier", "run", 1),
    ("Punctuation", "(", 1),
    ("Identifier", "value", 1),
    ("Punctuation", ")", 1),
    ("Punctuation", ":", 1),
    ("Newline", "\n", 1),
    ("Indent", "    ", 2),
    ("Keyword", "return", 2),
    ("Whitespace", " ", 2),
    ("Identifier", "value", 2),
    ("Operator", "+", 2),
    ("Literal", "1", 2),
]
"""``def run(value):`` and ``return value + 1``, in the token classes Understand answers with."""

DEF_RUN_SHAPE: Final = ["def", "ID", "(", "ID", ")", ":", "return", "ID", "+", "LIT"]
"""What :data:`DEF_RUN` normalises to: names and values classed, the rest kept as its text."""


# --- the fixtures ---------------------------------------------------------------------


def a_project(*files: FakeEnt) -> dict[str, FakeEnt]:
    """The extractor's ``file_ents``: project path -> the file entity, as ``_remember`` keys it."""
    return {str(ent.relname()): ent for ent in files}


def a_body(name: str, container: FakeEnt, start: int, end: int, **fields: Any) -> FakeEnt:
    """A routine whose body runs from ``start`` to ``end`` of ``container``.

    ``line_no`` is the line the fake's ``Definein`` reference sits on and ``end_ref`` is the
    ``End`` reference; both are what the index reads, and ``ends_in`` names another file for
    the one case where the two references disagree.
    """
    ends_in = fields.pop("ends_in", container)
    return a_routine(name, container, start, end_ref=FakeRef(ends_in, end), **fields)


def a_walk(*routines: FakeEnt) -> dict[str, tuple[FakeEnt, str]]:
    """The extractor's ``routine_ents``: key token -> (entity, the path the walk recorded)."""
    return {str(ent.longname()): (ent, RECORDED_ELSEWHERE) for ent in routines}


def index(files: dict[str, FakeEnt], routines: dict[str, tuple[FakeEnt, str]]) -> dict[str, Any]:
    """The document under test."""
    return worker_lean.token_index(files, routines, a_context())


def hashes(document: dict[str, Any], path: str) -> list[list[Any]]:
    """One file's ``(line, hash)`` pairs."""
    result: list[list[Any]] = document["files"][path]
    return result


def lines_of(document: dict[str, Any], path: str) -> list[int]:
    """The line numbers one file contributed, in the order the document carries them."""
    return [line for line, _ in hashes(document, path)]


def decoded(document: dict[str, Any], token: str) -> list[str]:
    """One routine's shape, read back through the vocabulary it was encoded against."""
    vocabulary: list[str] = document["vocabulary"]
    return [vocabulary[number] for number in document["routines"][token]["shape"]]


def one_routine(tokens: list[tuple[str, str, int]], path: str = "src/app.py") -> dict[str, Any]:
    """A one-file project whose whole content is one routine spanning its first two lines."""
    source = a_file(path, tokens=tokens)
    return index(a_project(source), a_walk(a_body("app.run", source, 1, 2)))


# --- the five token classes that carry no code (requirement 5.4) -----------------------


@pytest.mark.parametrize(
    ("token_class", "text"),
    [
        ("Whitespace", "  "),
        ("Comment", "# why"),
        ("Newline", "\n"),
        ("Indent", "\t"),
        ("Dedent", "    "),
    ],
)
def test_a_token_carrying_no_code_reaches_neither_half(token_class: str, text: str) -> None:
    """Each of the five classes, one case each, in both halves of the document.

    Parametrized rather than written as one file holding all five, because a set is only
    covered a member at a time: a file carrying every class at once still hashes equal with
    four of them dropped, and the case for the fifth would pass while it leaked.

    Each text is non-empty, so nothing but the class map can explain the token's absence.
    """
    plain = one_routine(DEF_RUN)
    padded = one_routine([*DEF_RUN, (token_class, text, 2)])
    assert hashes(padded, "src/app.py") == hashes(plain, "src/app.py")
    assert decoded(padded, "app.run") == DEF_RUN_SHAPE


def test_a_lexeme_with_no_text_reaches_neither_half() -> None:
    """The end-of-file artefact, on a class nothing drops (design.md, ``token_index``).

    Clipping a file's stream picks up a trailing lexeme carrying the empty string. Kept, it
    adds one free matching token to both sides of every comparison and inflates every ratio.
    It is refused on its text because the documented token classes do not include a name for
    it, so a class-based drop would be guessing at a spelling.
    """
    trailing = one_routine([*DEF_RUN, ("Identifier", "", 3)])
    assert decoded(trailing, "app.run") == DEF_RUN_SHAPE
    assert lines_of(trailing, "src/app.py") == [1, 2]


def test_a_code_line_keeps_the_number_the_file_gave_it() -> None:
    """A finding names lines a reader opens, so the index counts the file's lines, not its own.

    The comment line and the blank line contribute nothing and are *absent* rather than
    renumbered away: line 4 is still line 4.
    """
    source = a_file(
        "src/app.py",
        tokens=[
            ("Identifier", "total", 1),
            ("Comment", "# why", 2),
            ("Identifier", "done", 4),
        ],
    )
    assert lines_of(index(a_project(source), {}), "src/app.py") == [1, 4]


def test_a_reindented_recommented_copy_hashes_equal_to_its_original() -> None:
    """Requirement 5.4 for the exact-duplicate rule: layout is not part of a line.

    The discriminating half is the second assertion. Without it the first would also hold for
    an index that hashed nothing but the line's *length*, or one hash for every line alike.

    The third line of the copy is the *same tokens in the other order*, and the third
    assertion is the only thing in this suite that separates them. A hash that joined a
    line's texts without regard to order would pass every other case here, because every
    other comparison is between two lines this fixture builds the same way. It would also
    make ``a = b`` collide with ``b = a`` and ``f(x, y)`` with ``f(y, x)``, so task 5.3
    would report a run of lines that are not duplicates as an exact duplicate block.
    """
    original = a_file("src/one.py", tokens=[("Identifier", "total", 3), ("Operator", "+", 3)])
    copy = a_file(
        "src/two.py",
        tokens=[
            ("Indent", "        ", 7),
            ("Identifier", "total", 7),
            ("Whitespace", " ", 7),
            ("Operator", "+", 7),
            ("Comment", "# added later", 7),
            ("Identifier", "total", 8),
            ("Operator", "-", 8),
            ("Operator", "+", 9),
            ("Identifier", "total", 9),
        ],
    )
    document = index(a_project(original, copy), {})
    assert hashes(document, "src/two.py")[0][1] == hashes(document, "src/one.py")[0][1]
    assert hashes(document, "src/two.py")[1][1] != hashes(document, "src/one.py")[0][1]
    assert hashes(document, "src/two.py")[2][1] != hashes(document, "src/one.py")[0][1]


# --- the shape a renamed copy shares with its original (requirement 5.4) ---------------


def test_a_renamed_copy_has_the_shape_of_its_original() -> None:
    """Identifiers and literals are interchangeable; an operator is not.

    ``compute(amount) -> amount + 42`` is ``run(value) -> value + 1`` with every name and
    every value changed, and the two shapes have to be equal or a renamed twin is invisible.
    The third routine changes one *operator* and nothing else, which is what keeps the first
    assertion from being satisfied by a map that classed every token alike.
    """
    original = a_file("src/one.py", tokens=DEF_RUN)
    renamed = a_file("src/two.py", tokens=_renamed(DEF_RUN, "compute", "amount", "42"))
    changed = a_file("src/three.py", tokens=_renamed(DEF_RUN, "scale", "amount", "42", "*"))
    document = index(
        a_project(original, renamed, changed),
        a_walk(
            a_body("one.run", original, 1, 2),
            a_body("two.compute", renamed, 1, 2),
            a_body("three.scale", changed, 1, 2),
        ),
    )
    assert decoded(document, "two.compute") == decoded(document, "one.run") == DEF_RUN_SHAPE
    assert decoded(document, "three.scale") != decoded(document, "one.run")


def test_a_string_is_the_same_class_as_a_number() -> None:
    """``String`` and ``Literal`` are two spellings of one class: a value the shape must not see."""
    numeric = a_file("src/one.py", tokens=[("Identifier", "x", 1), ("Literal", "12", 1)])
    textual = a_file("src/two.py", tokens=[("Identifier", "y", 1), ("String", '"twelve"', 1)])
    document = index(
        a_project(numeric, textual),
        a_walk(a_body("one.run", numeric, 1, 1), a_body("two.run", textual, 1, 1)),
    )
    assert decoded(document, "one.run") == decoded(document, "two.run") == ["ID", "LIT"]


def test_the_vocabulary_carries_each_text_once_in_the_order_first_seen() -> None:
    """A shape is a list of indices, so the vocabulary is the only thing that can read it back.

    Asserted as the whole list rather than as a membership test: an entry repeated, or one
    ordered by anything but first sight, still decodes -- and still makes two identical
    shapes encode differently, which is the comparison both rules are built on.
    """
    source = a_file("src/app.py", tokens=DEF_RUN)
    document = index(a_project(source), a_walk(a_body("app.run", source, 1, 2)))
    assert document["vocabulary"] == ["def", "ID", "(", ")", ":", "return", "+", "LIT"]
    assert document["routines"]["app.run"]["shape"] == [0, 1, 2, 1, 3, 4, 5, 1, 6, 7]


def test_a_shape_holds_the_lines_of_its_own_routine_and_no_others() -> None:
    """The range is inclusive at both ends, and neither end may leak the line beside it.

    The file carries a token on the line above the routine and one on the line below it, so
    an off-by-one at either end changes the shape rather than passing quietly.
    """
    source = a_file(
        "src/app.py",
        tokens=[
            ("Identifier", "before", 1),
            ("Keyword", "def", 2),
            ("Identifier", "run", 2),
            ("Keyword", "return", 3),
            ("Identifier", "after", 4),
        ],
    )
    document = index(a_project(source), a_walk(a_body("app.run", source, 2, 3)))
    assert decoded(document, "app.run") == ["def", "ID", "return"]


def test_a_shape_names_the_file_its_definein_points_at() -> None:
    """The binding the fixtures carry all along, asserted once in the open.

    The extractor's map records a path from ``definein, declarein`` and the index clips from
    the file the ``Definein`` reference names. The two agree for a Python routine and can
    disagree for a C++ method with a header declaration, and the shape has to name the file
    whose lines it actually holds.
    """
    source = a_file("src/app.py", tokens=DEF_RUN)
    document = index(a_project(source), a_walk(a_body("app.run", source, 1, 2)))
    assert document["routines"]["app.run"]["path"] == "src/app.py"
    assert document["routines"]["app.run"]["start"] == 1
    assert document["routines"]["app.run"]["end"] == 2


# --- the statement count the family rule takes its floor on (requirement 5.3) ----------


def test_a_shape_carries_the_statement_count_the_family_rule_reads() -> None:
    """``CountStmt`` travels on the shape, not on the entity record (task 5.8).

    ``analysis.lean.similar`` refuses a routine below ``similar_min_statements``, and it used
    to read that number off ``ProjectSnapshot.entities`` -- a table the check pipeline narrows
    to the change's files and one dependency step, which put the whole-project reach
    requirement 5.3 asks for out of the rule's grasp. Carried here, the floor is answerable
    for every routine the index holds, wherever the change was.
    """
    source = a_file("src/app.py", tokens=DEF_RUN)
    body = a_body("app.run", source, 1, 2, values={"CountStmt": 7})

    document = index(a_project(source), a_walk(body))

    assert document["routines"]["app.run"]["statements"] == 7


def test_a_routine_whose_statement_count_was_never_taken_carries_none() -> None:
    """An absence and not a zero, which is the difference the family rule reads.

    A routine recorded with ``0`` would be a routine measured to have no statements, which
    the floor refuses for a reason it did not measure. ``None`` is "not measured", and
    ``similar._long_enough`` declines to judge it -- the treatment ``lean.layering`` gives the
    same metric one rule over.
    """
    source = a_file("src/app.py", tokens=DEF_RUN)

    document = index(a_project(source), a_walk(a_body("app.run", source, 1, 2)))

    assert document["routines"]["app.run"]["statements"] is None


def test_a_statement_count_is_asked_for_once_per_routine() -> None:
    """Requirement 9.5: the shape pass adds one metric query per routine and no more.

    The walk holds the entity, so the count is a query and not a second pass over the
    database -- but a query inside the per-file clipping loop would be one per routine per
    file. The fake records what it was asked for, which is the only way to see the difference.
    """
    source = a_file("src/app.py", tokens=DEF_RUN)
    body = a_body("app.run", source, 1, 2, values={"CountStmt": 7})

    index(a_project(source), a_walk(body))

    assert body.asked == [("CountStmt",)]


# --- the routines and files the index leaves out (requirements 5.8, 9.7) ---------------


def test_a_routine_with_no_end_reference_is_absent() -> None:
    """Absent rather than guessed at: Understand records no end for a routine it saw no end of.

    The file it is written in is still indexed, so the absence is the routine's alone and not
    a file the walk lost.
    """
    source = a_file("src/app.py", tokens=DEF_RUN)
    unfinished = a_routine("app.run", source, 1)
    document = index(a_project(source), a_walk(unfinished))
    assert document["routines"] == {}
    assert lines_of(document, "src/app.py") == [1, 2]


def test_a_routine_whose_end_is_in_another_file_is_absent() -> None:
    """One range over two files is not a range: the shape would be some other routine's tokens.

    A C++ method declared in a header and defined in a source is where this arrives, and the
    index refuses it rather than clipping lines 1 to 2 out of whichever file it asked first.
    """
    header = a_file("src/app.h", tokens=[("Identifier", "run", 1)])
    source = a_file("src/app.cpp", tokens=DEF_RUN)
    split = a_body("app.run", header, 1, 2, ends_in=source)
    document = index(a_project(header, source), a_walk(split))
    assert document["routines"] == {}


def test_a_routine_that_is_only_declared_is_absent() -> None:
    """A routine the walk placed through ``Declarein`` and the index cannot start: no shape.

    **This is a live path on every C++ and Java project**, not a guard held for the next
    caller. A pure virtual, an interface method or a prototype whose definition is outside
    the analysis passes the extractor's kind filter, is kept by ``_remember`` -- which keys a
    routine by the path ``_container_of`` reads off ``"definein, declarein"`` and so accepts
    either half -- and then answers ``None`` to the ``"definein"`` the index asks for. It has
    no body, so there is no range to clip and nothing to compare it with; the file it is
    declared in is still hashed, so the absence costs that routine its shape and nothing else.

    **The ``End`` reference is given to it deliberately**, and it is the one artificial part
    of the fixture: whether a build records an end for a declaration is not measured anywhere
    here, and the case must not turn on it either way. Without one the routine would be
    absent through the end guard whatever kinds the start query named, and this case would go
    on passing for an index that started from ``declarein`` -- which would clip a header line
    range out of a header and report two unrelated declarations as twins.
    """
    header = a_file("src/app.h", tokens=[("Keyword", "virtual", 1), ("Identifier", "run", 1)])
    declared = FakeEnt(
        qualified="app.run",
        kind_path="c Function",
        lang="C++",
        declared_in=FakeRef(header, 1),
        end_ref=FakeRef(header, 1),
    )
    document = index(a_project(header), a_walk(declared))
    assert document["routines"] == {}
    assert lines_of(document, "src/app.h") == [1]


def test_an_unreadable_file_is_listed_and_contributes_nothing() -> None:
    """Requirement 5.8: named once, hashed nowhere, and no shape clipped out of it.

    ``tokens=None`` is the fake's unreadable file -- deleted, or changed since the parse --
    and its lexer raises the API's own error the way ``Ent.lexer`` documents.
    """
    gone = a_file("src/gone.py", tokens=None)
    kept = a_file("src/kept.py", tokens=DEF_RUN)
    document = index(
        a_project(gone, kept),
        a_walk(a_body("gone.run", gone, 1, 2), a_body("kept.run", kept, 1, 2)),
    )
    assert document["unreadable"] == ["src/gone.py"]
    assert "src/gone.py" not in document["files"]
    assert list(document["routines"]) == ["kept.run"]


def test_two_unreadable_files_are_listed_in_path_order() -> None:
    """The third list in the document, which one unreadable file cannot order.

    ``unreadable`` is appended to inside the file loop, so with a single unreadable file its
    order is whatever the loop's is and no assertion can tell the two apart. Two of them,
    walked in the order that is not their path order, is the smallest project in which the
    list has an order of its own to get wrong.

    The readable file sorts *between* them, so the list is also shown to be the unreadable
    files compacted in path order rather than a slice of the walk with a hole in it.
    """
    walked_first = a_file("src/zebra.py", tokens=None)
    walked_last = a_file("src/alpha.py", tokens=None)
    readable = a_file("src/mid.py", tokens=DEF_RUN)
    document = index(a_project(walked_first, readable, walked_last), {})
    assert document["unreadable"] == ["src/alpha.py", "src/zebra.py"]
    assert list(document["files"]) == ["src/mid.py"]


def test_a_file_with_no_code_in_it_is_not_an_unreadable_one() -> None:
    """The distinction requirement 5.8 turns on, in the index rather than in the fake.

    A file of comments answers an empty list of lines and is *not* named as unreadable: it
    has nothing to duplicate, which is a measurement, while an unreadable file is the absence
    of one.
    """
    blank = a_file("src/blank.py", tokens=[("Comment", "# nothing here", 1)])
    document = index(a_project(blank), {})
    assert document["files"] == {"src/blank.py": []}
    assert document["unreadable"] == []


# --- the document itself ---------------------------------------------------------------


def test_the_document_does_not_inherit_the_order_it_was_walked_in() -> None:
    """Two sides of one change are compared document to document, so the order is the index's.

    The files arrive in reverse, the routines arrive in reverse, and one file's lines arrive
    out of order -- none of which the API promises anything about. The document is ordered by
    path, by key and by line either way.

    **The two files carry disjoint keyword text**, ``def`` against ``while``, for the reason
    the same-file case gives its two routines disjoint keywords: files are *shaped* in path
    order and ``_Vocabulary.index`` numbers a text when it is first asked, so the walk order
    would otherwise reach every shape in the project. Given identifiers alone both files answer
    the vocabulary ``["ID"]`` either way round and the fixture would agree with itself.

    **The key orders are asserted beside it because neither implies the other**: an index that
    walked in the given order and sorted ``files`` at the end keeps both and still numbers its
    vocabulary out of the walk. And **the routine in the first file is the one that sorts
    last** -- ``zeta`` in one.py, ``alpha`` in two.py -- because shapes are collected file by
    file: named after their own files they would make the collection order the sorted order,
    and the final sort behind ``routines`` could go with the suite still green.

    The last assertion is a shape rather than an order, and belongs to the file whose lines
    arrive scrambled: the range 4 to 9 is found by binary search over them, and read straight
    off the stream as it arrived it picks up line 1 as well.
    """
    second = a_file(
        "src/two.py",
        tokens=[
            ("Identifier", "b", 9),
            ("Keyword", "while", 4),
            ("Identifier", "a", 4),
            ("Identifier", "z", 1),
        ],
    )
    first = a_file("src/one.py", tokens=[("Keyword", "def", 1), ("Identifier", "x", 1)])
    document = index(
        a_project(second, first),
        a_walk(a_body("zeta.run", first, 1, 1), a_body("alpha.run", second, 4, 9)),
    )
    assert document["vocabulary"] == ["def", "ID", "while"]
    assert document["routines"]["zeta.run"]["shape"] == [0, 1]
    assert list(document["files"]) == ["src/one.py", "src/two.py"]
    assert list(document["routines"]) == ["alpha.run", "zeta.run"]
    assert lines_of(document, "src/two.py") == [1, 4, 9]
    assert decoded(document, "alpha.run") == ["while", "ID", "ID"]


def test_two_routines_in_one_file_number_their_tokens_the_same_either_way_round() -> None:
    """The vocabulary is assigned in shaping order, and shaping order must not be walk order.

    The previous case puts its two routines in two *different* files, so the file order alone
    fixes the result and it would pass whether or not the spans were ordered. Here both
    routines are written in one file, which is the only arrangement in which the order the
    walk collected them in reaches the document: the spans of a file are shaped one after the
    other, and ``_Vocabulary.index`` hands out its numbers in the order it is first asked.

    The two bodies are given **disjoint** keyword text -- ``def`` against ``while`` -- so that
    a walk order reaching the vocabulary is visible as a different number for the same token,
    rather than being hidden by two routines that happen to open with the same word. Fed
    ``alpha`` first the vocabulary opens ``def, ID, return, LIT, while``; fed ``beta`` first it
    would open ``while, ID, return, LIT, def`` and ``alpha`` would carry ``[4, 1, 2, 3]``
    instead of ``[0, 1, 2, 3]`` -- two documents that no longer compare, which is what
    :func:`worker_lean._code_lines` says the whole index must not do. The documents are
    asserted equal whole, so a third field that starts depending on the walk fails here too.
    """
    source = a_file(
        "src/app.py",
        tokens=[
            ("Keyword", "def", 1),
            ("Identifier", "alpha", 1),
            ("Keyword", "return", 2),
            ("Literal", "1", 2),
            ("Keyword", "while", 3),
            ("Identifier", "flag", 3),
            ("Keyword", "return", 4),
            ("Literal", "2", 4),
        ],
    )
    alpha = a_body("app.alpha", source, 1, 2)
    beta = a_body("app.beta", source, 3, 4)
    walked = index(a_project(source), a_walk(alpha, beta))
    reversed_walk = index(a_project(source), a_walk(beta, alpha))
    assert walked["vocabulary"] == ["def", "ID", "return", "LIT", "while"]
    assert walked["routines"]["app.alpha"]["shape"] == [0, 1, 2, 3]
    assert walked == reversed_walk


def test_a_line_hash_is_sixteen_hex_characters() -> None:
    """The width design.md specifies, pinned because nothing else in the document shows it.

    The index carries one entry per code line of a whole project, so the truncation is a size
    decision; a narrower hash costs collisions between lines that are not copies of each other
    and a wider one costs bytes in every cached snapshot. Neither shows up as a failing
    comparison, which is why the number is asserted rather than inferred.
    """
    source = a_file("src/app.py", tokens=[("Identifier", "total", 1)])
    ((_, digest),) = hashes(index(a_project(source), {}), "src/app.py")
    assert len(digest) == 16
    assert set(digest) <= set("0123456789abcdef")


def test_the_document_is_the_shape_the_model_reads_back() -> None:
    """The worker writes JSON and the snapshot parses it, so the two halves are bound here.

    ``TokenIndex`` requires all three of its measured fields, so a key renamed or dropped on
    this side fails on the model's side rather than silently arriving as a default.
    """
    source = a_file("src/app.py", tokens=DEF_RUN)
    gone = a_file("src/gone.py", tokens=None)
    document = index(a_project(source, gone), a_walk(a_body("app.run", source, 1, 2)))
    (_, first), (_, second) = hashes(document, "src/app.py")
    parsed = TokenIndex.model_validate(document)
    assert parsed.unreadable == ["src/gone.py"]
    assert parsed.files["src/app.py"] == [(1, first), (2, second)]
    assert parsed.routines["app.run"].path == "src/app.py"
    shape = parsed.routines["app.run"].shape
    assert [parsed.vocabulary[number] for number in shape] == DEF_RUN_SHAPE


def _renamed(
    tokens: list[tuple[str, str, int]],
    name: str,
    parameter: str,
    literal: str,
    operator: str = "+",
) -> list[tuple[str, str, int]]:
    """:data:`DEF_RUN` with its name, its parameter, its literal and its operator replaced."""
    swapped = {"run": name, "value": parameter, "1": literal, "+": operator}
    return [(token_class, swapped.get(text, text), line) for token_class, text, line in tokens]


def test_the_lexer_probe_reads_the_file_kind_a_snapshot_reads_files_with() -> None:
    """The claim ``LEXER_PROBE_KIND``'s docstring makes, asserted rather than stated.

    This file may import nothing of ``scitools_hook`` -- it is executed by Understand's own
    interpreter, where the package is not on ``sys.path`` -- so the kind string is written out
    here instead of read from ``config.metric_names``. Written out twice, the two agree on the
    day they were written and never again, and the failure is silent in the worst way: a kind
    string that names no entity makes the probe answer "not on this build" on every build
    there is, and every configuration enabling a token rule is then refused.
    """
    assert worker_lean.LEXER_PROBE_KIND == SCOPE_KINDS["file"]
    assert worker_lean.LEXER_PROBE_KIND == FILE_KIND
