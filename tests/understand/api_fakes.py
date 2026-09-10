"""A fake ``understand`` module, injectable into ``sys.modules``, for the worker's unit tests.

The worker is the only module that may touch the ``understand`` API, and its unit tests
drive :func:`scitools_hook.understand.worker.dispatch` against these stand-ins so that the
mapping, the request validation and the ``try/finally`` around the database are covered on
a machine with no licence. Each fake models the slice of the API the worker reads -- kinds,
references, entities, architectures, metrics, lexemes -- and nothing more, so a test that
needs a member the fake lacks fails loudly instead of passing on ``None``.

Two generations of ``understand.Metric`` are here because the worker serves both:
:class:`FakeMetrics` answers as 7.x did (id strings, a class-level ``description``) and
:class:`FakeMetrics8` as 8.0 does (``Metric`` objects, ``lookup``).
"""

from __future__ import annotations

import io
import itertools
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

# --- a fake `understand` module -------------------------------------------------


class FakeUnderstandError(Exception):
    """Stand-in for ``understand.UnderstandError``; the worker maps its text to a type."""


@dataclass(eq=False)
class FakeKind:
    """``Ent.kind()``: only its long name reaches the answer (``python Class``)."""

    path: str

    def longname(self) -> str:
        """The fully qualified kind name."""
        return self.path

    def check(self, kindstring: str) -> bool:
        """Whether this kind matches a filter string, as ``Kind.check`` does.

        A comma separates alternatives and the words of one alternative are an AND, which is
        the part of Understand's filter grammar the worker uses; verified against the real
        API for the strings it passes (``'definein'`` matches ``'Python Definein'`` and not
        ``'Python Callby Possible'``).
        """
        words = self.path.lower().split()
        return any(
            all(word in words for word in alternative.lower().split())
            for alternative in kindstring.split(",")
        )


@dataclass(eq=False)
class FakeRef:
    """A reference: its direction, its kind, the entity at the other end and where it is.

    ``forward`` is Understand's ``isforward``: false for the second half of every pair
    (``use`` versus ``useby``), which is how the impact walk tells a referencer from a
    reference this entity makes.
    """

    target: FakeEnt
    line_no: int | None = None
    kind_path: str = "python Definein"
    forward: bool = False

    def file(self) -> FakeEnt:
        """The file entity this reference names."""
        return self.target

    def line(self) -> int | None:
        """The line the reference sits on."""
        return self.line_no

    def ent(self) -> FakeEnt:
        """The entity at the other end of the reference."""
        return self.target

    def kind(self) -> FakeKind:
        """The reference kind."""
        return FakeKind(self.kind_path)

    def isforward(self) -> bool:
        """Whether this is the first half of the pair, i.e. a reference this entity makes."""
        return self.forward


@dataclass(eq=False)
class FakeLexeme:
    """One token of a file's lexical stream: its class, its text and the line it starts on.

    Three members of the real ``understand.Lexeme`` and no others. The API also offers
    ``line_end``, ``column_begin``, ``column_end``, ``next``, ``previous``, ``inactive``,
    ``ent`` and ``ref``; none of them is read by anything here, and a fake that answered them
    anyway would invite a measurement to depend on a value nobody has checked against a real
    database. ``token()`` is Understand's own vocabulary -- ``Comment``, ``Identifier``,
    ``Keyword``, ``Literal``, ``Newline``, ``Operator``, ``Preprocessor``, ``Punctuation``,
    ``String``, ``Whitespace``, ``Indent``, ``Dedent`` and the rest -- so a test names the
    class the code will branch on rather than a spelling of its own.
    """

    token_class: str
    content: str
    line: int

    def token(self) -> str:
        """The token's kind, in Understand's spelling."""
        return self.token_class

    def text(self) -> str:
        """The source text of the token, which may legitimately be empty."""
        return self.content

    def line_begin(self) -> int:
        """The 1-based line the token starts on."""
        return self.line


class FakeLexer:
    """A file's lexical stream, answering the line-range form of ``Lexer.lexemes``.

    Built from ``(token class, text, line)`` triples rather than from ready-made lexemes, so
    that a caller -- a test, or :meth:`FakeEnt.lexer` -- describes a file's tokens without
    having to know what a lexeme object is. Making lexemes is the lexer's own work, and
    leaving it here is what keeps :class:`FakeEnt` from depending on both classes at once.

    **The range is inclusive at both ends and counted from 1**, which is what the API
    documents and what a hand-written stand-in most often gets wrong; a fake that treated
    ``end_line`` as exclusive would shift every answer by a line and the tests written
    against it would agree with it. Both bounds are optional and absent means unbounded, so
    ``lexemes()`` is the whole file.

    Deliberately not iterable, though the real ``Lexer`` is: nothing here walks a lexer that
    way, and a fake that supported both forms would let a caller pick the one the worker
    does not use without anything noticing.
    """

    def __init__(self, tokens: Sequence[tuple[str, str, int]] = ()) -> None:
        self.stream = [FakeLexeme(*token) for token in tokens]

    def lexemes(
        self, start_line: int | None = None, end_line: int | None = None
    ) -> list[FakeLexeme]:
        """The tokens between the two lines, both ends included."""
        return [
            lexeme
            for lexeme in self.stream
            if (start_line is None or lexeme.line_begin() >= start_line)
            and (end_line is None or lexeme.line_begin() <= end_line)
        ]


_ENTITY_IDS = itertools.count(1)
"""Hands every fake entity the database-unique numeric id ``Ent.id()`` answers with."""


@dataclass(eq=False)
class FakeEnt:
    """An entity of any scope; ``relname`` is ``None`` for everything but a file (verified).

    Identity, not value, decides equality, because the API's ``depends()`` maps are keyed by
    entity and the worker must be able to look a target up.
    """

    path: str | None = None
    qualified: str = ""
    kind_path: str = "File"
    simple: str = ""
    lang: str = "Python"
    params: str | None = None
    values: dict[str, object] = field(default_factory=dict)
    lib: str = ""
    container: FakeEnt | None = None
    line_no: int | None = None
    declared_params: int = 0
    deps: dict[FakeEnt, list[object]] = field(default_factory=dict)
    deps_by: dict[FakeEnt, list[object]] = field(default_factory=dict)
    refs_by: list[FakeEnt] = field(default_factory=list)
    refs_by_kind: str = "python Callby"
    refs_to: list[FakeEnt] = field(default_factory=list)
    members: list[FakeEnt] = field(default_factory=list)
    source: str | None = None
    tokens: Sequence[tuple[str, str, int]] | None = None
    """``(token class, text, line)`` per token of the file, or ``None`` when it cannot be lexed.

    ``None`` and ``[]`` are different answers on purpose, exactly as they are for
    :attr:`source`: an empty list is a readable file with nothing in it, ``None`` is the file
    requirement 5.8 is about -- deleted, or changed since the parse -- and :meth:`lexer`
    raises for it the way the API does.

    Triples rather than :class:`FakeLexeme` objects because building lexemes is the lexer's
    work rather than the entity's: a test describes a file's tokens without having to know
    what a lexeme object is, and :class:`FakeLexer` is the one place that turns them into
    lexemes.
    """
    refs_error: str | None = None
    drawable: tuple[str, ...] = ("Butterfly", "Calls", "Called By")
    drawn: list[tuple[str, str]] = field(default_factory=list)
    ident: int = field(default_factory=lambda: next(_ENTITY_IDS))
    asked: list[tuple[str, ...]] = field(default_factory=list)
    """Every ``metric()`` call this entity received, in order, as the names it was given.

    Recorded because *which* metrics are asked for, and of which entities, is itself a
    contract: a plugin metric is computed on demand rather than read out of the database, so
    asking for one during the whole-project population walk costs a plugin run per entity of
    the scope (requirement 5.1). A test can only see that by watching the calls."""

    def relname(self) -> str | None:
        """The project-relative path of a file entity, or ``None`` for other kinds."""
        return self.path

    def longname(self) -> str:
        """Understand's qualified name; a file reports its absolute path (verified)."""
        return self.qualified

    def kind(self) -> FakeKind:
        """The entity's kind object."""
        return FakeKind(self.kind_path)

    def name(self) -> str:
        """The short name."""
        return self.simple

    def language(self) -> str:
        """The language the entity is written in."""
        return self.lang

    def parameters(self) -> str | None:
        """The declared parameters of a routine, ``None`` for every other kind."""
        return self.params

    def library(self) -> str:
        """``'Standard'`` for the stubs Understand injects, empty for project code."""
        return self.lib

    def metric(self, names: Sequence[str]) -> dict[str, object]:
        """The requested metrics; an absent one answers ``None``, as the API does."""
        self.asked.append(tuple(names))
        return {name: self.values.get(name) for name in names}

    def contents(self) -> str:
        """The file's text, as ``Ent.contents()`` hands it over.

        Raises when this entity has none, which is what the API does for an entity that is not
        a readable file -- and is the case the import-time measurement has to degrade through
        rather than crash on. A fake that answered an empty string instead would make "no
        source" look like "a file with nothing deferred in it", which is the opposite claim.
        """
        if self.source is None:
            raise FakeUnderstandError(f"no contents for {self.qualified}")
        return self.source

    def lexer(
        self,
        lookup_ents: bool = True,
        show_inactive: bool = False,
        expand_macros: bool = False,
    ) -> FakeLexer:
        """The file's lexical stream, raising when the source cannot be read.

        The three parameters are the API's own, accepted and ignored: ``lookup_ents`` is a
        construction-speed switch over entity and reference lookup, which is not modelled
        here at all, so there is nothing for the fake to vary. They are declared rather than
        dropped because ``lexer(False)`` is the spelling the callers use, and they are
        annotated ``bool`` because ``bool`` is what the documented API takes. Task 1.5 had
        annotated them ``object`` to keep this class under a class-coupling limit that counts
        annotation types; task 1.9 recorded that limit as a scope deviation instead, and the
        first thing it bought back was these three annotations.

        The raise is the part with behaviour behind it. ``Ent.lexer`` documents
        ``UnderstandError`` "if unable to construct the lexer", and says the source file must
        still exist and be unchanged since the last parse -- a condition a gate that measures
        a worktree between two commits meets often. Requirement 5.8 turns on that error being
        distinguishable from an empty file, so :attr:`tokens` being ``None`` raises here and
        an empty list does not.
        """
        if self.tokens is None:
            raise FakeUnderstandError(f"unable to lex {self.path or self.qualified}")
        return FakeLexer(self.tokens)

    def ref(self, refkinds: str) -> FakeRef | None:
        """The first reference of ``refkinds``; the worker asks for the container file."""
        return None if self.container is None else FakeRef(self.container, self.line_no)

    def ents(self, refkinds: str, entkinds: str) -> list[FakeEnt]:
        """The entities reached by ``refkinds``; only the count of parameters is read."""
        return [FakeEnt() for _ in range(self.declared_params)]

    def refs(self, refkinds: str = "") -> list[FakeRef]:
        """The references of ``refkinds``, both directions, as ``Ent.refs()`` returns them.

        The containment reference to the file the entity is written in is always present,
        because Understand always records one and the impact walk has to leave it out.

        **The filter is applied here rather than ignored**, even though the impact walk asks
        for everything. A fake that answered every reference whatever it was asked would let a
        caller that forgot to name a kind pass its tests and then read a containment reference
        as a call against the real API -- the "a fake that answers more than it was asked"
        failure. An empty string means every reference, which is what the API does.
        """
        if self.refs_error is not None:
            raise FakeUnderstandError(self.refs_error)
        found: list[FakeRef] = []
        if self.container is not None:
            found.append(FakeRef(self.container, self.line_no, "python Definein", False))
        found.extend(FakeRef(ent, None, "python Define", True) for ent in self.members)
        found.extend(FakeRef(ent, None, "python Call", True) for ent in self.refs_to)
        found.extend(FakeRef(ent, None, self.refs_by_kind, False) for ent in self.refs_by)
        return [ref for ref in found if not refkinds or ref.kind().check(refkinds)]

    def id(self) -> int:
        """The numeric identity of the entity (verified stable within one open database)."""
        return self.ident

    def draw(self, graph: str, filename: str) -> None:
        """Render ``graph`` to ``filename``; an unavailable graph raises, as the API does.

        Verified live: a routine draws ``Butterfly``/``Calls``/``Called By`` and refuses
        ``Depends On`` with ``UnderstandError('Unknown Graph')``, writing no file at all;
        files and classes draw ``Depends On``.
        """
        self.drawn.append((graph, filename))
        if graph not in self.drawable:
            raise FakeUnderstandError("Unknown Graph")
        Path(filename).write_text(f"<svg><!-- {graph} --></svg>", encoding="utf-8")

    def depends(self) -> dict[FakeEnt, list[object]]:
        """What this entity depends on, with the references that make each dependency."""
        return dict(self.deps)

    def dependsby(self) -> dict[FakeEnt, list[object]]:
        """What depends on this entity."""
        return dict(self.deps_by)


class FakeArch:
    """An architecture node: a long name, child nodes and member entities."""

    def __init__(
        self,
        longname: str,
        children: Sequence[FakeArch] = (),
        ents: Sequence[FakeEnt] = (),
    ) -> None:
        self._longname = longname
        self._children = list(children)
        self._ents = list(ents)

    def longname(self) -> str:
        """The full path of the node, e.g. ``Directory Structure/cli``."""
        return self._longname

    def children(self) -> list[FakeArch]:
        """The child nodes, in declaration order."""
        return list(self._children)

    def ents(self, recursive: bool = False) -> list[FakeEnt]:
        """Member entities, optionally including those of the child nodes."""
        found = list(self._ents)
        if recursive:
            for child in self._children:
                found.extend(child.ents(True))
        return found

    def walk(self) -> list[FakeArch]:
        """This node and every descendant, depth first; what ``lookup_arch`` searches."""
        found = [self]
        for child in self._children:
            found.extend(child.walk())
        return found


class FakeDb:
    """An opened database: entities, metrics, root architectures and a recorded ``close``."""

    def __init__(
        self,
        roots: Sequence[FakeArch] = (),
        lookup_error: str | None = None,
        entities: dict[str, list[FakeEnt]] | None = None,
        project_metrics: dict[str, object] | None = None,
    ) -> None:
        self._roots = list(roots)
        self._lookup_error = lookup_error
        self._entities = dict(entities or {})
        self._project_metrics = dict(project_metrics or {})
        self.languages: tuple[str, ...] = ("Python", "C++")
        self.closed = False

    def ents(self, kindstring: str) -> list[FakeEnt]:
        """Every entity of the kind string; an unknown kind answers with nothing."""
        return list(self._entities.get(kindstring, []))

    def metric(self, names: Sequence[str]) -> dict[str, object]:
        """The database's own metrics; an absent one answers ``None``, as the API does."""
        return {name: self._project_metrics.get(name) for name in names}

    def language(self) -> tuple[str, ...]:
        """The languages the database was analyzed with."""
        return self.languages

    def root_archs(self) -> list[FakeArch]:
        """The root architectures of the database."""
        return list(self._roots)

    def lookup_arch(self, longname: str) -> FakeArch | None:
        """The node with this long name anywhere in the tree, or ``None`` (as the API does)."""
        if self._lookup_error is not None:
            raise FakeUnderstandError(self._lookup_error)
        for root in self._roots:
            for node in root.walk():
                if node.longname() == longname:
                    return node
        return None

    def close(self) -> None:
        """Record the close; the real API crashes later if objects outlive this call."""
        self.closed = True


class FakeMetrics:
    """Stand-in for ``understand.Metric``: metric names per kind string, and descriptions."""

    def __init__(
        self,
        by_kind: dict[str, list[str]] | None = None,
        descriptions: dict[str, str] | None = None,
    ) -> None:
        self._by_kind = by_kind or {}
        self._descriptions = descriptions or {}

    def list(self, kindstring: str) -> list[str]:
        """The metrics defined for the kind string; the API returns ``[]`` for an unknown one."""
        return list(self._by_kind.get(kindstring, []))

    def description(self, metricname: str) -> str:
        """The metric's description, empty when the name is unknown (as the API does)."""
        return self._descriptions.get(metricname, "")


class FakeMetricObject:
    """One ``understand.Metric`` as 8.0 hands it out: ``id()``, ``description()``, ``tags()``.

    The tags are how a plugin metric says what it applies to. Measured on Build 1262,
    ``Metric.lookup("CountGlobalsModified").tags()`` answers ``['Category: Coupling',
    'Target: Functions', 'Language: C', 'Language: C++', 'Language: Python', ...]`` -- one
    entry per language, with the target scope beside them.
    """

    def __init__(self, metric_id: str, description: str = "", tags: Sequence[str] = ()) -> None:
        self._id = metric_id
        self._description = description
        self._tags = list(tags)

    def id(self) -> str:
        return self._id

    def description(self) -> str:
        return self._description

    def tags(self) -> list[str]:
        """What this metric applies to, in Understand's own ``Category: X`` spelling."""
        return list(self._tags)


class FakeMetrics8:
    """Stand-in for 8.0's ``understand.Metric``: ``list`` answers objects, ``lookup`` finds one.

    There is deliberately no class-level ``description(id)``: 8.0 made it an instance method,
    and a worker that still called it on the class would raise here as it did on 8.0.1262.
    """

    def __init__(
        self,
        by_kind: dict[str, list[str]] | None = None,
        descriptions: dict[str, str] | None = None,
        tags: dict[str, list[str]] | None = None,
    ) -> None:
        self._by_kind = by_kind or {}
        self._descriptions = descriptions or {}
        self._tags = tags or {}

    def list(self, kindstring: str) -> list[FakeMetricObject]:
        return [
            FakeMetricObject(name, self._descriptions.get(name, ""))
            for name in self._by_kind.get(kindstring, [])
        ]

    def lookup(self, metricid: str) -> FakeMetricObject | None:
        known = {name for names in self._by_kind.values() for name in names}
        if (
            metricid not in known
            and metricid not in self._descriptions
            and metricid not in self._tags
        ):
            return None
        return FakeMetricObject(
            metricid, self._descriptions.get(metricid, ""), self._tags.get(metricid, ())
        )


class FakeUnderstand(ModuleType):
    """A module-shaped stand-in for the API, injectable into ``sys.modules``."""

    UnderstandError: type[FakeUnderstandError]
    Metric: FakeMetrics

    def __init__(
        self,
        db: FakeDb | None = None,
        version: str = "6.5.1204",
        open_error: str | None = None,
        metrics: FakeMetrics | None = None,
    ) -> None:
        super().__init__("understand")
        self.UnderstandError = FakeUnderstandError
        self.Metric = metrics if metrics is not None else FakeMetrics()
        self.opened: list[str] = []
        self._db = db
        self._version = version
        self._open_error = open_error

    def version(self) -> str:
        """The API version, as ``understand.version()`` returns it (verified: ``6.5.1204``)."""
        return self._version

    def open(self, dbname: str) -> FakeDb:
        """Open the database, recording the path and raising the configured API error."""
        self.opened.append(dbname)
        if self._open_error is not None:
            raise FakeUnderstandError(self._open_error)
        if self._db is None:
            raise FakeUnderstandError("DBUnableOpen: unable to open database")
        return self._db


class InteractiveStdin(io.StringIO):
    """A terminal-like stdin: reading it would block the worker forever."""

    def isatty(self) -> bool:
        """Claim to be a terminal."""
        return True

    def read(self, size: int | None = -1, /) -> str:
        """Fail the test rather than block; the worker must skip an interactive stdin."""
        raise AssertionError("the worker must not read from an interactive stdin")


def install(monkeypatch: pytest.MonkeyPatch, api: ModuleType) -> None:
    """Put ``api`` in ``sys.modules`` so the worker's lazy ``import understand`` finds it."""
    monkeypatch.setitem(sys.modules, "understand", api)


def directory_structure() -> FakeArch:
    """The shape Understand builds for the sample project, with one uneven branch."""
    return FakeArch(
        "Directory Structure",
        children=[
            FakeArch(
                "Directory Structure/src",
                children=[
                    FakeArch("Directory Structure/src/cli", ents=[FakeEnt("src/cli/app.py")]),
                    FakeArch("Directory Structure/src/util", ents=[FakeEnt("src/util/text.py")]),
                ],
            ),
            FakeArch(
                "Directory Structure/native",
                ents=[FakeEnt("native/util.c"), FakeEnt(None), FakeEnt("native/util.h")],
            ),
        ],
    )


def envelope(result: dict[str, object]) -> dict[str, Any]:
    """The error object of an envelope, failing the test when the result is a success."""
    error = result.get("error")
    assert isinstance(error, dict), f"expected an error envelope, got {result!r}"
    return error
