"""The lexer stand-ins in ``tests/understand/api_fakes.py``, held to the documented API.

Every other fake in that module is exercised through the worker that consumes it, so a fake
that drifts is caught by the test that used it. The lexer fakes arrive before their consumer
does: `worker_lean.py` will build the token index from them (design.md, *worker_lean*), and
`worker._initialiser` already reads a lexer but has no unit test *because* there was no fake
to give it one. A stand-in with nothing driving it is a stand-in nobody has checked, so the
checks live here instead, written against `understand.Lexer` / `understand.Lexeme` as the
8.0 documentation states them rather than against what a caller happens to want.

The two properties worth spending a test on are the ones a hand-written fake gets wrong:

* **The line range is inclusive at both ends and 1-based.** ``lexemes(line, line + span)``
  is how ``_initialiser`` asks for a statement and how the token index will ask for a line.
  A fake that treated ``end_line`` as exclusive, or counted from zero, would move every
  answer by one line and every test written against it would agree.
* **Unreadable is not empty.** ``Ent.lexer`` raises ``UnderstandError`` when the file on
  disk is gone or has changed since the parse, and requirement 5.8 turns on exactly that:
  such a file contributes no duplicates and is named once per run. A fake that answered an
  empty stream for it would make "cannot read this file" indistinguishable from "this file
  has no tokens", which is the opposite claim.
"""

from __future__ import annotations

import pytest
from api_fakes import FakeEnt, FakeLexeme, FakeLexer, FakeUnderstandError


def source_file() -> FakeEnt:
    """A readable file whose stream spans four lines, with a token class on each."""
    return FakeEnt(
        "src/app.py",
        tokens=[
            ("Keyword", "def", 1),
            ("Whitespace", " ", 1),
            ("Identifier", "run", 1),
            ("Comment", "# why", 2),
            ("Identifier", "total", 3),
            ("Operator", "=", 3),
            ("Literal", "20", 3),
            ("Identifier", "done", 4),
        ],
    )


def texts(lexemes: list[FakeLexeme]) -> list[str]:
    """The text of each lexeme, which is what every consumer of the stream joins."""
    return [lexeme.text() for lexeme in lexemes]


def test_a_lexeme_answers_its_token_class_its_text_and_its_line() -> None:
    """The three members the lean measurements read; nothing else is modelled."""
    lexeme = FakeLexeme("Identifier", "total", 3)
    assert lexeme.token() == "Identifier"
    assert lexeme.text() == "total"
    assert lexeme.line_begin() == 3


def test_the_whole_stream_is_returned_when_no_range_is_given() -> None:
    """``lexemes()`` takes both bounds as optional, and both absent means the file."""
    assert texts(source_file().lexer().lexemes()) == [
        "def",
        " ",
        "run",
        "# why",
        "total",
        "=",
        "20",
        "done",
    ]


def test_the_line_range_includes_both_of_its_ends() -> None:
    """1-based and inclusive, as the API documents; the off-by-one that would pass silently."""
    assert texts(source_file().lexer().lexemes(1, 1)) == ["def", " ", "run"]
    assert texts(source_file().lexer().lexemes(3, 4)) == ["total", "=", "20", "done"]
    assert texts(source_file().lexer().lexemes(2, 2)) == ["# why"]


def test_one_bound_of_the_range_may_be_left_open() -> None:
    """``start_line`` and ``end_line`` are independently optional."""
    assert texts(source_file().lexer().lexemes(end_line=2)) == ["def", " ", "run", "# why"]
    assert texts(source_file().lexer().lexemes(3)) == ["total", "=", "20", "done"]


def test_a_range_past_the_end_of_the_file_is_empty_rather_than_an_error() -> None:
    """``_initialiser`` asks for twelve lines past a binding near the end of a file."""
    assert source_file().lexer().lexemes(40, 52) == []


def test_the_lexer_takes_the_arguments_the_api_takes() -> None:
    """``lexer(False)`` -- the spelling the design uses -- must reach ``lookup_ents``.

    Recorded as a call rather than as an effect: entity lookup is a speed switch on the real
    API, so there is nothing for the fake to do with it but accept it. A fake that declared
    no parameters would refuse the one call site that exists.
    """
    ent = source_file()
    assert texts(ent.lexer(False).lexemes(2, 2)) == ["# why"]
    assert texts(ent.lexer(lookup_ents=False, show_inactive=True).lexemes(2, 2)) == ["# why"]


def test_a_file_marked_unreadable_raises_the_api_error() -> None:
    """Requirement 5.8's entry point: the lexer refuses, it does not answer nothing.

    ``Ent.lexer`` raises ``UnderstandError`` when the source is missing or has changed since
    the parse. :class:`FakeUnderstandError` is what the fake ``understand`` module publishes
    as ``UnderstandError``, so a caller catching the API's error catches this one.
    """
    unreadable = FakeEnt("src/gone.py")
    with pytest.raises(FakeUnderstandError) as raised:
        unreadable.lexer()
    assert "src/gone.py" in str(raised.value)


def test_a_readable_file_with_no_tokens_is_not_an_unreadable_one() -> None:
    """The distinction the fake exists to keep: an empty stream is an answer, not a failure."""
    empty = FakeEnt("src/blank.py", tokens=[])
    assert empty.lexer().lexemes() == []
    assert empty.lexer().lexemes(1, 12) == []


def test_the_lexer_can_be_built_without_a_file_entity() -> None:
    """A test that needs a stream and not a file does not have to invent an entity for it."""
    lexer = FakeLexer([("Keyword", "return", 7)])
    assert texts(lexer.lexemes(7, 7)) == ["return"]
    assert lexer.lexemes(1, 6) == []
