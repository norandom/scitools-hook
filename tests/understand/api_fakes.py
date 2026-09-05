"""A fake ``understand`` module, injectable into ``sys.modules``, for the worker's unit tests.

The worker is the only module that may touch the ``understand`` API, and its unit tests
drive :func:`scitools_hook.understand.worker.dispatch` against these stand-ins so that the
mapping, the request validation and the ``try/finally`` around the database are covered on
a machine with no licence. Each fake models the slice of the API the worker reads -- kinds,
references, entities, architectures, metrics -- and nothing more, so a test that needs a
member the fake lacks fails loudly instead of passing on ``None``.

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
    refs_error: str | None = None
    drawable: tuple[str, ...] = ("Butterfly", "Calls", "Called By")
    drawn: list[tuple[str, str]] = field(default_factory=list)
    ident: int = field(default_factory=lambda: next(_ENTITY_IDS))

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


_ARCH_ROOT: FakeArch | None = None
"""The architecture every node reports as its root; set by the test that builds a project.

``FakeArch.roots`` reads it so a node's ``depends`` can resolve its siblings by name, the
way Understand resolves them through the architecture a node was declared under.
"""


def declare_arch_root(root: FakeArch | None) -> None:
    """Make ``root`` the architecture every node reports; ``None`` returns nodes to themselves."""
    global _ARCH_ROOT
    _ARCH_ROOT = root


class FakeArch:
    """An architecture node: a long name, child nodes and member entities."""

    def __init__(
        self,
        longname: str,
        children: Sequence[FakeArch] = (),
        ents: Sequence[FakeEnt] = (),
        depends: dict[str, int] | None = None,
    ) -> None:
        self._longname = longname
        self._children = list(children)
        self._ents = list(ents)
        self._depends = dict(depends or {})

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

    def depends(self) -> dict[FakeArch, list[object]]:
        """Node -> the references that make this node depend on it (verified shape)."""
        found = {node.longname(): node for root in self.roots() for node in root.walk()}
        return {found[name]: [object()] * count for name, count in self._depends.items()}

    def roots(self) -> list[FakeArch]:
        """The architecture this node was declared under; a fake needs only itself."""
        return [_ARCH_ROOT] if _ARCH_ROOT is not None else [self]

    def walk(self) -> list[FakeArch]:
        """This node and every descendant, depth first."""
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
    """One ``understand.Metric`` as 8.0 hands it out: an object with ``id()``/``description()``."""

    def __init__(self, metric_id: str, description: str = "") -> None:
        self._id = metric_id
        self._description = description

    def id(self) -> str:
        return self._id

    def description(self) -> str:
        return self._description


class FakeMetrics8:
    """Stand-in for 8.0's ``understand.Metric``: ``list`` answers objects, ``lookup`` finds one.

    There is deliberately no class-level ``description(id)``: 8.0 made it an instance method,
    and a worker that still called it on the class would raise here as it did on 8.0.1262.
    """

    def __init__(
        self,
        by_kind: dict[str, list[str]] | None = None,
        descriptions: dict[str, str] | None = None,
    ) -> None:
        self._by_kind = by_kind or {}
        self._descriptions = descriptions or {}

    def list(self, kindstring: str) -> list[FakeMetricObject]:
        return [
            FakeMetricObject(name, self._descriptions.get(name, ""))
            for name in self._by_kind.get(kindstring, [])
        ]

    def lookup(self, metricid: str) -> FakeMetricObject | None:
        known = {name for names in self._by_kind.values() for name in names}
        if metricid not in known and metricid not in self._descriptions:
            return None
        return FakeMetricObject(metricid, self._descriptions.get(metricid, ""))


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
