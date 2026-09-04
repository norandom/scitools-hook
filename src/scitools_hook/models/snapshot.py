"""Snapshot vocabulary: entity identity, records, dependency edges, architecture nodes, calls.

An entity is identified by ``(scope, path, longname, parameters)`` plus an
:attr:`~EntityKey.ordinal` that is ``0`` for all but the rare entities the first four cannot
separate. A live experiment (research.md, "Entity identity across two databases") confirmed
that ``relname``, ``longname`` and ``parameters`` are identical in the before and after
databases while ``uniquename`` embeds the analysis root and is therefore unusable across the
two shadows. Architecture membership comes from the container file, because ``Db.archs()`` is
empty for routines and classes.

``ProjectSnapshot.entities`` is a mapping keyed by :class:`EntityKey` because every rule
joins the two sides by that key. JSON object keys must be strings, so the mapping is
written to the wire as a **list of records**, the key being ``record.ref.key``; validation
rebuilds the mapping and checks that both agree. Where a key must be an object key after
all (``ChangeSummary.impact``), :attr:`EntityKey.token` provides a reversible string form.

**A mapping that discards duplicates is itself a false negative** (task 11.6). The first four
fields are *not* unique in ordinary Python: ``@typing.overload`` puts a stub-plus-stub-plus-
implementation triple of the same name and the same ``parameters`` in one module, and a bad
merge or a ``try/except ImportError`` pair writes ``def same(x)`` twice. Understand reports
each of them; ``{record.key: record}`` kept the last and dropped the rest, silently, on both
sides -- so the dropped entity was never measured against a threshold, never ratcheted and
never counted, and no rule could notice. :func:`_index_by_key` is the fix: a key that names
more than one record has its members numbered in file order, so every record reaches the
mapping and every consumer of ``entities`` judges all of them without knowing this happened.

**The call graph is the one view here that cannot be read as complete.** ``file_edges``,
``class_edges`` and ``arch_edges`` come from ``Ent.depends()``, which Understand builds from
resolved references; ``call_edges`` comes from call references, and on Python a call through
an instance attribute resolves to the attribute rather than to the routine behind it -- 31.4%
of the 44 783 call sites of a measured 770-file project bound to nothing callable at all.
So ``call_edges`` never travels alone: :class:`CallResolution` says per language how much of
it resolved, :class:`CallNode` says per routine how many of its own call sites did not, and
:attr:`ProjectSnapshot.call_graph_holds` says which routines the graph covers in the first
place. A rule may report that a routine reaches *at least* what the graph shows. It may never
report that a routine reaches nothing.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FieldSerializationInfo,
    SerializerFunctionWrapHandler,
    field_serializer,
    field_validator,
    model_serializer,
    model_validator,
)

from scitools_hook.config.metric_names import Scope

Side = Literal["before", "after"]
"""Which side of the change a snapshot describes."""


class DataModel(BaseModel):
    """Base of every model in this layer: unknown keys are a validation error.

    Snapshots and worker results cross a process boundary as JSON; forbidding extras turns
    a drifting contract into a validation failure instead of silently dropped data.
    """

    model_config = ConfigDict(extra="forbid")


class EntityKey(DataModel):
    """Identity of one entity, stable across the before and after databases.

    ``path`` is repo-relative with forward slashes, ``longname`` is Understand's qualified
    name and ``parameters`` distinguishes overloads (``None`` for entities that have none).
    Frozen, so it can key dictionaries and sets.

    ``parameters`` is what tells a real C++ overload pair apart: task 10.1 measured
    ``Shape::area(int) const`` and ``Shape::area(int, int) const`` agreeing on scope, path,
    long name, Understand's kind string and the short name, and differing in nothing but the
    parameter list and the definition line -- and a line moves whenever code above it does.
    Dropping ``parameters`` would merge that pair into one entity, so it stays. The price is
    that a routine whose signature changed is a *different* key on the two sides; paying it
    here rather than by weakening the key is what ``analysis.ratchet`` exists to do, by
    pairing a removed key with an added one that shares this key's :attr:`family`.

    :attr:`ordinal` is the tie-break for entities the other four fields cannot separate at
    all; it is ``0`` for every key the worker emits and is assigned by :func:`_index_by_key`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: Scope
    path: str
    longname: str
    parameters: str | None = None
    ordinal: int = Field(default=0, ge=0)

    @property
    def family(self) -> tuple[Scope, str, str]:
        """The same entity under any signature: ``(scope, path, longname)``.

        This is what a changed parameter list leaves untouched, and therefore the only thing
        the two sides of such a change still agree on. ``analysis.ratchet`` joins on it when
        -- and only when -- exactly one key of the family was removed and exactly one added.
        """
        return (self.scope, self.path, self.longname)

    @property
    def token(self) -> str:
        """Reversible string form, for use wherever a key has to be a string.

        A zero ``ordinal`` is left out, so the token of an ordinary key is exactly the four
        element form every stored baseline, class edge endpoint and impact key already holds.
        """
        parts: list[object] = [self.scope, self.path, self.longname, self.parameters]
        if self.ordinal:
            parts.append(self.ordinal)
        return json.dumps(parts, separators=(",", ":"))

    @model_serializer(mode="wrap")
    def _leave_out_a_zero_ordinal(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """Write the ordinal only where it carries information, as :attr:`token` does.

        ``understand.worker`` may not import this package, so it writes key documents of the
        four fields it knows about and nothing else; ``tests/understand/test_impact.py`` and
        ``tests/understand/test_worker.py`` hold the two forms to each other. Emitting a
        ``0`` no worker can produce would break that agreement -- and would rewrite every
        cached snapshot, stored baseline and report payload for a field that says "this
        entity was never ambiguous". A duplicate's ordinal is written, because there it is
        the only thing separating two records.
        """
        document = dict(handler(self))
        if not self.ordinal:
            document.pop("ordinal", None)
        return document

    @classmethod
    def from_token(cls, token: str) -> Self:
        """Rebuild a key from :attr:`token`; raise ``ValueError`` for anything else."""
        try:
            parts = json.loads(token)
        except json.JSONDecodeError as err:
            raise ValueError(f"not an entity key token: {token!r}") from err
        if not isinstance(parts, list) or len(parts) not in (4, 5):
            raise ValueError(f"an entity key token has four or five elements, got {token!r}")
        scope, path, longname, parameters = parts[:4]
        ordinal = parts[4] if len(parts) == 5 else 0
        return cls(
            scope=scope, path=path, longname=longname, parameters=parameters, ordinal=ordinal
        )


class EntityRef(DataModel):
    """An entity as it is shown to a user: its key plus Understand's kind, name and line."""

    key: EntityKey
    kind: str
    name: str
    line: int | None = None


class EntityRecord(DataModel):
    """One entity with everything the rules need: metrics, architectures, newness."""

    ref: EntityRef
    language: str
    metrics: dict[str, float] = Field(default_factory=dict)
    archs: list[str] = Field(default_factory=list)
    is_new: bool = False

    @property
    def key(self) -> EntityKey:
        """The identity of the recorded entity."""
        return self.ref.key


def _index_by_key(records: Iterable[EntityRecord]) -> dict[EntityKey, EntityRecord]:
    """Index ``records`` by key, numbering the records a single key names more than once.

    The plain ``{record.key: record}`` this replaces dropped every record but the last of a
    colliding key, which is the false negative the module docstring records: an entity that
    never reaches the mapping is never judged by any rule, on either side, without a word.

    Numbering is by **file order** -- line, then kind, then short name -- and not by the order
    the records arrive in, because the ordinal has to mean the same thing in the before and
    the after snapshot for the join to hold. Understand's own walk order is not promised to be
    either. So the ``@typing.overload`` triple ``stub, stub, implementation`` is numbered 0, 1,
    2 on both sides and the implementation is compared with the implementation.

    Two honest limits, both smaller than the drop they replace.

    * When a change *removes* one of a duplicated pair, the survivor is renumbered, so the
      second ``def same(x)`` of the before side is compared with the first of the after side.
      There is nothing in the database that could tell them apart -- that is what "duplicate
      key" means -- and a wrong comparison between two entities that both exist is a smaller
      failure than dropping one of them unmeasured.
    * A numbered key's :attr:`~EntityKey.token` carries its ordinal, and the class edge
      endpoints ``understand.worker`` writes never do, so a *duplicated class* -- two classes
      of one name in one file -- matches no edge past the first and ``analysis.structure.fan``
      reads no dependencies for it. Its metrics are still measured, which is what the drop
      took away; before this it had no record at all.
    """
    grouped: dict[EntityKey, list[EntityRecord]] = {}
    for record in records:
        grouped.setdefault(record.key, []).append(record)
    indexed: dict[EntityKey, EntityRecord] = {}
    for key, group in grouped.items():
        if len(group) == 1:
            indexed[key] = group[0]
            continue
        for ordinal, record in enumerate(sorted(group, key=_in_file_order)):
            numbered = _renumbered(record, ordinal)
            indexed[numbered.key] = numbered
    return indexed


def _in_file_order(record: EntityRecord) -> tuple[bool, int, str, str]:
    """Order duplicates the way a reader meets them; a record without a line sorts last."""
    line = record.ref.line
    return (line is None, line or 0, record.ref.kind, record.ref.name)


def _renumbered(record: EntityRecord, ordinal: int) -> EntityRecord:
    """``record`` under an ordinal of its own, ref included, so key and record still agree."""
    key = record.key.model_copy(update={"ordinal": ordinal})
    return record.model_copy(update={"ref": record.ref.model_copy(update={"key": key})})


class DepEdge(DataModel):
    """A dependency with its reference count, and how much of it happens at import time.

    ``src`` and ``dst`` are file paths, architecture node paths, or :attr:`EntityKey.token`
    values for class edges, depending on which edge list holds the edge.

    **``refs`` and :attr:`import_time` answer two different questions, and conflating them
    reported a cycle that does not exist.** Measured on a 770-file Python project: nine files
    across ``shells/config`` and ``shells/pods`` were reported as a dependency cycle, and the
    module that closes it imports ``pods`` in exactly two ways -- four imports inside
    ``if TYPE_CHECKING:``, which the interpreter erases, and two inside a function body, with
    a comment saying they are there to keep the module importable. Neither runs when the module
    is loaded, so there is no cycle to break; the deferred import *is* the fix, and reporting it
    as the defect punishes it. Understand's ``depends()`` counts every reference regardless of
    guard or scope, so the count alone cannot tell the two apart.

    The coupling is nonetheless real -- that module does use those types -- so the reference
    count is left exactly as it was and the distinction is *published* rather than subtracted:
    ``refs`` stays every reference, and :attr:`import_time` says how many of them the import of
    ``src`` actually executes. Cycle detection reads :attr:`import_time`; fan, coupling, layers
    and the new-dependency limit read ``refs``, because a deferred import still couples two
    files even though it cannot deadlock their loading.
    """

    src: str
    dst: str
    refs: int = Field(ge=0)
    crosses_arch: bool = False
    import_time: int | None = Field(default=None, ge=0)
    """How many of ``refs`` run when ``src`` is imported; ``None`` when that was not measured.

    ``None`` is not zero and must never be read as one. It means "this language, or this file,
    was not analysed for import-time-ness", and every consumer treats it exactly as it treated
    an edge before this field existed. Only **Python file** edges are ever measured: the
    construct being detected is Python's, and the reference kinds a C++ ``#include`` produces
    (measured: ``Include``, ``Type``, ``Use``, ``Init``, ``Return``) are not import references
    at all, so a language-blind rule would have scored every C++ edge ``0`` and silently
    switched off C++ cycle detection entirely. A Python file whose source the worker cannot
    read or cannot parse is ``None`` for the same reason: an unmeasured edge keeps the older,
    louder behaviour.
    """

    @model_serializer(mode="wrap")
    def _leave_out_an_unmeasured_import_time(
        self, handler: SerializerFunctionWrapHandler
    ) -> dict[str, Any]:
        """Write ``import_time`` only where it was measured, as the ordinal is written.

        Every stored baseline, cached snapshot and test fixture written before this field
        existed holds four keys per edge, and ``None`` is exactly what their absence means. A
        ``null`` emitted for each of them would rewrite all of it to say nothing.
        """
        document = dict(handler(self))
        if self.import_time is None:
            document.pop("import_time", None)
        return document


class CallResolution(DataModel):
    """How many of one language's call sites Understand could bind, and to what.

    **A call-graph finding without this figure asserts a completeness the database does not
    have.** Understand resolves a Python call through an instance attribute to the *attribute*
    and not to the routine behind it, so a call graph read as if it were complete answers
    "nothing calls this" for code that is called on every run. The three counters are the
    honest denominator, and every call-graph finding carries the one for its language.

    A call site is counted exactly once, in exactly one bucket, from the routine that makes it:

    * :attr:`resolved` -- it landed on a routine the call graph holds, which is a project
      routine, or a project class whose constructor stands in for a call on the class.
    * :attr:`external` -- it landed on something callable that the graph does not hold: a
      library routine, a class outside the analysis root, a project class with no constructor
      of its own, or a C++ implicit member the routine kind filter excludes. Not a defect and
      not a gap; simply outside the graph.
    * :attr:`unresolved` -- **it landed on nothing callable at all**: an unknown attribute, a
      variable, a parameter. This is the false-negative source, and it is the number a reader
      has to see beside any claim about what a routine reaches.

    Measured on Understand 6.5.1204 (build 1204), counting every call site of every project
    routine. The first two rows were taken outside this repository and are quoted from that
    measurement; the third is reproduced on every contract run by
    ``tests/contract/test_call_graph_contract.py``, which prints it.

    ==========================================  ===========  ==========  ============
    corpus                                      call sites   resolved    unresolved
    ==========================================  ===========  ==========  ============
    Python, a 770-file project, out of tree          44 783      31.0%        31.4%
    C++, two purpose-built fixtures, out of tree         29      55.2%         6.9%
    the in-tree contract fixture, Python                  7      71.4%        28.6%
    the in-tree contract fixture, C++                     7      57.1%        14.3%
    ==========================================  ===========  ==========  ============

    **The substrate is therefore markedly better for C++ than for Python**, and the report is
    per language for the same reason :attr:`ProjectSnapshot.unavailable` reports a missing
    metric per language: the two are not the same substrate and one averaged figure would let
    the weaker one hide behind the better. Only the Python row is a large sample; every C++
    figure here is a fixture of a few dozen call sites and is labelled as such wherever it is
    quoted. What the contract test asserts is the *ordering* -- Python resolves a smaller share
    than C++ on the same build -- and not any one of these rates.
    """

    resolved: int = Field(default=0, ge=0)
    """Call sites that landed on a routine this call graph holds."""

    external: int = Field(default=0, ge=0)
    """Call sites that landed on something callable outside the graph."""

    unresolved: int = Field(default=0, ge=0)
    """Call sites that landed on nothing callable -- the false-negative source."""

    @property
    def total(self) -> int:
        """Every call site counted for this language."""
        return self.resolved + self.external + self.unresolved

    @property
    def bound(self) -> float | None:
        """The share of call sites that bound to *something* callable; ``None`` for no data.

        This is the figure that describes the parser rather than the project: a language whose
        calls Understand cannot follow scores low here however the code is written.
        """
        return None if not self.total else (self.total - self.unresolved) / self.total

    @property
    def internal(self) -> float | None:
        """The share of call sites that became an edge of this graph; ``None`` for no data."""
        return None if not self.total else self.resolved / self.total


class CallNode(DataModel):
    """One routine of the call graph: its endpoint, its own complexity, its blind spots.

    :attr:`unresolved_calls` is the **per-entity** half of the confidence report that
    :class:`CallResolution` gives per run. A routine that reaches nothing because it is a leaf
    and one that reaches nothing because six of its call sites bound to an attribute are the
    same shape in the graph and must not read the same in a finding, so the count travels with
    the node and every rule that sums over a reached set sums these too.

    :attr:`complexity` is ``CyclomaticStrict`` and is ``None`` -- never ``0.0`` -- when the
    database has no value for it, because a zero is a claim Understand never made. A rule
    summing complexity over a reached set therefore reports how many of its members were
    unmeasured rather than quietly counting them as free.
    """

    node: str
    """The routine's endpoint in the graph: its :attr:`EntityKey.token`.

    Always the **four element** form, as a class edge endpoint is: the worker writes it from a
    walk that sees one entity at a time and cannot assign the ordinal :func:`_index_by_key`
    gives a duplicated key. Two routines the four fields cannot separate therefore share one
    node -- measured at four routines over two nodes in a 770-file project -- and the reach
    they are summed into counts their complexity once. It is the same trade recorded for class
    edges, and it costs a measurement rather than making one up.
    """

    complexity: float | None = None
    """``CyclomaticStrict`` of this routine, or ``None`` where the database has no value."""

    unresolved_calls: int = Field(default=0, ge=0)
    """Call sites of this routine that bound to nothing callable."""


class ArchNode(DataModel):
    """A node of an Understand architecture at the configured depth, plus its member files."""

    path: str
    members: list[str] = Field(default_factory=list)

    @property
    def depth(self) -> int:
        """Number of path components below the architecture root."""
        return self.path.count("/")


class ParseError(DataModel):
    """One parse error reported by ``und analyze`` (req 2.6).

    ``path`` is **repository-relative** for a file inside the analysed shadow, and absolute
    for anything else Understand parsed on the way -- the interpreter's own standard library
    above all, which task 10.4 measured four errors in and which no commit can be blamed for.
    :meth:`~scitools_hook.understand.database.DatabaseManager.ensure_side` is what makes it
    so, and that one form is what lets the path be compared against an
    :class:`EntityKey`'s and against the run's selection without any path arithmetic at the
    other end.
    """

    path: Path
    line: int | None = None
    message: str


class Definition(DataModel):
    """One module-level name bound to a value, and where that binding is written.

    ``value`` is the initialiser as the analyser's own lexer read it, with comments and
    whitespace removed so that two spellings of one constant compare equal. It is ``None``
    when the binding has no initialiser the lexer could recover -- an augmented assignment, a
    tuple unpacking, a bare annotation -- and a definition without a value is never compared
    against another, because "both unknown" is not "both the same".

    ``path`` is repository-relative and ``line`` is where the name is bound, so a finding can
    point at the copy the commit touched rather than at the concept in the abstract.
    """

    name: str
    path: str
    line: int
    value: str | None = None


class ProjectSnapshot(DataModel):
    """Immutable view of one database side; every rule works on these, never on live objects."""

    side: Side
    languages: list[str] = Field(default_factory=list)
    entities: dict[EntityKey, EntityRecord] = Field(default_factory=dict)
    file_edges: list[DepEdge] = Field(default_factory=list)
    class_edges: list[DepEdge] = Field(default_factory=list)
    call_edges: list[DepEdge] = Field(default_factory=list)
    call_nodes: list[CallNode] = Field(default_factory=list)
    call_resolution: dict[str, CallResolution] = Field(default_factory=dict)
    arch_nodes: list[ArchNode] = Field(default_factory=list)
    arch_edges: list[DepEdge] = Field(default_factory=list)
    populations: dict[Scope, dict[str, list[float]]] = Field(default_factory=dict)
    unavailable: dict[str, list[str]] = Field(default_factory=dict)
    parse_errors: list[ParseError] = Field(default_factory=list)
    definitions: list[Definition] = Field(default_factory=list)

    @property
    def call_graph_holds(self) -> frozenset[str]:
        """Every routine endpoint the call graph knows, as :attr:`EntityKey.token` values.

        **Absence from this set is not "this routine calls nothing".** ``call_edges`` is
        bounded to the routines reachable from the ones the run asked about, exactly as
        ``file_edges`` is bounded to the affected neighbourhood (req 4.11), so a routine
        outside the bound has no node here and nothing may be concluded about it. A rule that
        read a missing node as an empty reach would report a clean answer over code it never
        looked at, which is the failure this project keeps meeting; every consumer therefore
        asks this question before it asks any other.
        """
        return frozenset(node.node for node in self.call_nodes)

    @property
    def unparsed_files(self) -> frozenset[str]:
        """The repository-relative paths this side could not read in full (req 2.6).

        **This is the fact a comparison between two sides cannot do without, and it is
        published here so the ratchet can consume it rather than re-derive it.** An entity
        after a parse error is absent from the database, so the *same file* measured on a
        side that failed to parse and on a side that did not is not two measurements of the
        same thing: fixing a parse error reads as ``file.CountDeclClass rose from 3 to 15``,
        which is the analysis getting better and not the code getting worse. A rule that
        compares a before value with an after one has to be able to ask whether the before
        side was read at all, and ``key.path in before.unparsed_files`` is that question.

        Every path here is repository-relative, because
        :meth:`~scitools_hook.models.cache.SyncState.record_parse_errors` normalises them
        before they reach a snapshot -- and drops the ones that are not, which is what makes
        the whole set comparable with :attr:`EntityKey.path` without any path arithmetic. An
        error Understand found outside the shadow, in the interpreter's own standard library,
        is no entity of this project and no commit here can fix it, so it never arrives.
        """
        return frozenset(error.path.as_posix() for error in self.parse_errors)

    @field_validator("entities", mode="before")
    @classmethod
    def _accept_the_record_list_wire_form(cls, value: object) -> object:
        if isinstance(value, list):
            return _index_by_key(EntityRecord.model_validate(item) for item in value)
        return value

    @model_validator(mode="after")
    def _keys_match_their_records(self) -> Self:
        for key, record in self.entities.items():
            if record.ref.key != key:
                raise ValueError(
                    f"entity is stored under {key.token} but identifies as {record.ref.key.token}"
                )
        return self

    @field_serializer("entities")
    def _dump_entities(
        self, entities: dict[EntityKey, EntityRecord], info: FieldSerializationInfo
    ) -> list[dict[str, Any]]:
        mode = "json" if info.mode_is_json() else "python"
        ordered = sorted(entities.values(), key=lambda record: record.ref.key.token)
        return [record.model_dump(mode=mode) for record in ordered]
