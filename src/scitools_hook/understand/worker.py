"""The Understand Python API worker: JSON in, JSON out, standard library only (req 1.2, 1.4).

Every call into the ``understand`` Python API in this project goes through this module, and
this module imports nothing but the standard library and ``understand`` itself. That rule is
not stylistic. The API module is tied to the Python minor of the installed Understand build
and ships with its own interpreter, ``<home>/bin/<platform>/upython``, which has no
third-party packages and no copy of this project; on Linux with Understand 6.5 importing the
API into system CPython aborts the interpreter outright once a license is active (Perl XS
symbol lookup). So the adapter runs operations either in-process (``dispatch``) or as
``upython worker.py <op>`` with the request on standard input, and both paths execute the
very same functions.

Two consequences shape the code:

* ``understand`` is imported inside :func:`_import_api`, never at module level, so that
  merely importing this module — which the in-process probe of requirement 1.2 does — can
  never crash the caller, and so an interpreter without the API answers with an envelope.
* Every failure a caller can act on is *data*: ``{"error": {"type": ..., "message": ...}}``
  written to standard output with exit status 0. ``ApiRunner`` maps ``NoApiLicense`` to the
  license exit code (requirement 1.4) and the database errors to an analysis failure; the
  ``ArchitectureNotFound`` envelope carries ``available``, the architectures that do exist,
  which requirement 6.8 turns into a configuration error. A non-zero exit or a traceback
  means the worker itself is broken, and the runner reports it as such.

Operations implemented here: ``ping`` for the probes, ``catalogue`` for metric availability
per language and kind (requirement 5.5 and the configuration validation of 3.8), ``archs`` for
the architecture nodes structural rules work on (requirements 6.7 and 6.8), ``snapshot``,
which turns one database into the ``ProjectSnapshot`` document every rule is evaluated against
(requirements 3.5, 5.5, 6.7, 9.7), ``impact``, the transitive reverse-reference walk behind the
blast radius of a change (requirement 9.5), and ``graphs``, which exports Understand's own
butterfly and depends-on pictures as SVG (requirement 9.4).

Database discipline: a database is opened only by the operations that need one and always
closed in a ``finally``, because the API crashes the process when entities outlive their
database, and only one database may be open per process.

File discipline: ``graphs`` is the only operation that writes anything, and it writes only
inside the output directory the request names.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, NoReturn, TextIO

OPS: Final[tuple[str, ...]] = ("ping", "catalogue", "archs", "snapshot", "impact", "graphs")
"""The operations this worker answers, in the order the usage message lists them."""

ERROR_TYPES: Final[tuple[str, ...]] = (
    "NoApiLicense",
    "DBAlreadyOpen",
    "DBUnableOpen",
    "DBOldVersion",
    "DBUnknownVersion",
    "DBCorrupt",
)
"""``UnderstandError`` texts documented for ``understand.open``, longest-lived first.

The API reports failures as one exception class whose message starts with one of these
names (verified: ``DBUnableOpen: unable to open database``), so the envelope type is the
name found in the text and ``UnderstandError`` when none is.
"""

USAGE: Final = "usage: worker.py <operation>, with the JSON request object on stdin"


class _RequestError(Exception):
    """A failure the worker answers with an envelope: a type, a message and extra fields."""

    def __init__(self, error_type: str, message: str, **details: object) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.details = details


def _envelope(error_type: str, message: str, **details: object) -> dict[str, object]:
    """The error envelope in its wire form; ``details`` carries per-type extras."""
    return {"error": {"type": error_type, "message": message, **details}}


def _refused(exc: _RequestError) -> dict[str, object]:
    """The envelope of a refused request."""
    return _envelope(exc.error_type, str(exc), **exc.details)


def _error_type(message: str) -> str:
    """Classify an ``UnderstandError`` text into one of :data:`ERROR_TYPES`."""
    lowered = message.lower()
    for name in ERROR_TYPES:
        if name.lower() in lowered:
            return name
    return "UnderstandError"


def _import_api() -> Any:
    """Import ``understand`` on demand, refusing the request when the interpreter has none.

    ``LC_NUMERIC`` is forced to ``C`` first, before the API's own initialization reads the
    environment. Measured on this machine, whose ``LC_NUMERIC`` is German: without it every
    SVG ``Ent.draw`` writes carries ``fill-opacity="0,000000"`` and
    ``stroke-opacity="0,000000"`` — well-formed XML whose attribute values are not valid
    numbers, so a renderer drops them and paints an intended-transparent stroke opaque.
    What the graph engine reads is the *environment*, not the process's C locale:
    ``locale.setlocale(LC_NUMERIC, "C")`` leaves the comma in place (verified), while setting
    the variable works. Setting it after ``import understand`` was also measured to work — the
    environment is read when the graph engine initializes, not at import — so this is the
    earliest safe point rather than the only one that functions, and it is preferred because
    it is the single choke point every operation passes through, which keeps the rule off the
    call sites. A caller's ``LC_ALL`` still wins over ``LC_NUMERIC``, which is one reason
    :func:`_as_float` goes on accepting a comma in a metric value.
    """
    os.environ["LC_NUMERIC"] = "C"
    try:
        import understand  # type: ignore[import-not-found]
    except ImportError as exc:
        raise _RequestError(
            "ApiUnavailable",
            f"the understand Python API is not importable by {sys.executable}: {exc}",
        ) from exc
    return understand


# --- request validation ----------------------------------------------------------


def _require_str(request: Mapping[str, object], key: str) -> str:
    """Read a non-empty string from the request or refuse it."""
    value = request.get(key)
    if not isinstance(value, str) or not value:
        raise _RequestError("BadRequest", f"{key!r} must be a non-empty string, got {value!r}")
    return value


def _require_str_list(request: Mapping[str, object], key: str) -> list[str]:
    """Read a list of strings from the request or refuse it."""
    value = request.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise _RequestError("BadRequest", f"{key!r} must be a list of strings, got {value!r}")
    return list(value)


def _require_depth(request: Mapping[str, object]) -> int:
    """Read the depth of a walk, defaulting to one level.

    Two operations count different things with it and both are inclusive: ``archs`` and
    ``snapshot`` count architecture levels below the root, where 0 is the architecture
    itself, and ``impact`` counts reference hops out from an entity, where 0 reports nothing.
    """
    value = request.get("depth", 1)
    if not isinstance(value, int) or value < 0:
        raise _RequestError("BadRequest", f"'depth' must be an integer >= 0, got {value!r}")
    return value


# --- operations -------------------------------------------------------------------


def _op_ping(api: Any, request: Mapping[str, object]) -> dict[str, object]:
    """Report the API version and the interpreter that loaded it (requirements 1.2, 1.5)."""
    return {
        "version": str(api.version()),
        "python": ".".join(str(part) for part in sys.version_info[:3]),
    }


def _op_catalogue(api: Any, request: Mapping[str, object]) -> dict[str, object]:
    """List the metrics available for each requested kind string, and optional descriptions.

    The caller composes the kind strings, one per language and scope (``python function
    ~unknown ~unresolved``), so the worker needs no notion of scopes; an unknown kind yields
    an empty list, which is how requirement 5.5 learns that a metric is unavailable for a
    language. No database is involved: ``understand.Metric`` is a module-level accessor.
    """
    kinds = _require_str_list(request, "kinds")
    result: dict[str, object] = {kind: sorted(api.Metric.list(kind)) for kind in kinds}
    answer: dict[str, object] = {"metrics": result}
    if "describe" in request:
        names = _require_str_list(request, "describe")
        answer["descriptions"] = {name: api.Metric.description(name) for name in names}
    return answer


def _op_archs(api: Any, request: Mapping[str, object]) -> dict[str, object]:
    """Return the root architectures and the nodes of one architecture at a depth.

    The request is validated before the database is opened, and the database is closed
    before the answer leaves this function. A missing architecture is refused with the
    architectures that do exist (requirement 6.8). Members are file entities only —
    ``relname`` is ``None`` for anything else, and the library files Understand injects into
    a Python project are not architecture members at all (verified).
    """
    db_path = _require_str(request, "db")
    longname = _require_str(request, "architecture")
    depth = _require_depth(request)
    db = api.open(db_path)
    try:
        roots = [arch.longname() for arch in db.root_archs()]
        root = db.lookup_arch(longname)
        if root is None:
            raise _RequestError(
                "ArchitectureNotFound",
                f"architecture {longname!r} does not exist in {db_path}",
                available=roots,
            )
        nodes = [
            {"path": node.longname(), "files": _member_files(node)}
            for node in _nodes_at_depth(root, depth)
        ]
        return {
            "architecture": root.longname(),
            "depth": depth,
            "root_archs": roots,
            "nodes": nodes,
        }
    finally:
        db.close()


def _nodes_at_depth(root: Any, depth: int) -> list[Any]:
    """The architecture nodes ``depth`` levels below ``root``, keeping shallower leaves.

    Depth 0 is the architecture itself. A branch that ends above the requested depth
    contributes its own leaf instead of nothing, so that no file drops out of the structural
    rules just because its directory tree is shallower than the configured depth.
    """
    level = [root]
    for _ in range(depth):
        deeper: list[Any] = []
        for node in level:
            deeper.extend(node.children() or [node])
        level = deeper
    return level


def _member_files(node: Any, root: str = "") -> list[str]:
    """The project-relative paths of the project file entities in a node and its descendants.

    Non-file members have no ``relname`` and the library files Understand injects sit outside
    the analysis root, so :func:`_project_path` answers ``None`` for both. ``root`` is the
    analysis root the paths are taken relative to, so that a node's members are named exactly
    as the entity keys are; the ``archs`` operation has no root and keeps Understand's own
    relative names.
    """
    names = (_project_path(ent, root) for ent in node.ents(True))
    return sorted(name for name in names if name is not None)


# --- the snapshot operation --------------------------------------------------------

SNAPSHOT_SCOPES: Final[tuple[str, ...]] = ("routine", "class", "file")
"""Element scopes the snapshot walks; ``project`` and ``arch`` have no entities of their own."""

SIDES: Final[tuple[str, ...]] = ("before", "after")
"""Which side of the change a snapshot describes."""

CONTAINER_REFS: Final = "definein, declarein"
"""Reference kinds leading from an entity to the file it is written in (verified)."""

PARAMETER_KIND: Final = "Parameter ~Catch"
"""Entity kind the synthetic ``CountParams`` counts, as ``srccheck`` did."""

PYTHON_LANGUAGE: Final = "Python"
"""``Ent.language()`` of a Python file; the only language import-time-ness is measured for."""

IMPORT_REFS: Final = "import"
"""Reference kinds that make one Python file's import execute another's.

Measured against build 1204: this filter matches ``Import``, ``Import From`` and
``Import Implicit`` and does not match ``Use``, ``Call`` or ``Typed``, which is exactly the
split wanted -- **in Python a name from another module is only reachable through an import**,
so if no import between two files runs at load time, no use of that name can run then either.
It matches nothing a C++ ``#include`` produces (measured: ``Include``, ``Type``, ``Use``,
``Init``, ``Return``), which is why the language guard is not optional.
"""

TYPE_CHECKING_NAME: Final = "TYPE_CHECKING"
"""The guard whose body ``typing`` promises is never executed."""

CALL_REFS: Final = "call"
"""Reference kinds leading from a routine to what it calls.

Measured on build 1204: this one filter also matches ``Deref Call``, which is what a C++ call
through a function pointer produces, so no call site of either language is missed by asking
only for ``call``. It is *not* deduplicated -- three calls to the same routine from one body
are three references (measured) -- so the count of an edge is a count of call sites, exactly
as ``refs`` means everywhere else in a snapshot.
"""

CALLABLE_KINDS: Final = (
    "function, method, procedure, routine, classmethod, class, interface, struct"
)
"""Kinds a call reference may legitimately land on without the graph holding the target.

Deliberately **without** ``~unknown ~unresolved``: the whole point of this filter is to
separate "bound to something callable that this graph does not hold" -- a library routine, a
class outside the root, a C++ implicit member the routine scope excludes -- from "bound to
nothing callable", which is the false negative. A ``python Unknown Ambiguous Attribute`` is
not callable and must not be counted as though the call had resolved.
"""

CALL_BUCKETS: Final[tuple[str, ...]] = ("resolved", "external", "unresolved")
"""The three ways a call site can end, in the order ``CallResolution`` documents them."""

CONSTRUCTOR_NAME: Final = "__init__"
"""The routine a call on a Python class enters; C++ and Java use the class's own name."""

MEMBER_REFS: Final = "define, declare"
"""Reference kinds leading from a class to the routines written inside it."""

CALL_METRIC: Final = "CyclomaticStrict"
"""The complexity a reach rule sums; read for every routine, not only the requested ones."""


def _as_float(value: object) -> float | None:
    """Coerce one metric value to a number; ``None`` when Understand has no usable value.

    Most metrics are integers, but the ratio metrics come back as **locale-formatted
    strings** (verified on this machine, whose ``LC_NUMERIC`` is German:
    ``RatioCommentToCode`` is ``'0,18'``, and ``'0.18'`` elsewhere). ``EntityRecord.metrics``
    is ``dict[str, float]`` and every threshold comparison is numeric, so a string that is
    passed through would fail validation or raise at comparison time. Both separators are
    therefore accepted; a value carrying both is read with the comma as a thousands
    separator. Anything that still will not parse is treated exactly like ``None``: the
    metric is unavailable for that entity's language (requirement 5.5), never zero.
    """
    if isinstance(value, int | float):
        return float(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    text = text.replace(",", "") if "." in text else text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _basename(path: str) -> str:
    """The last component of a POSIX path."""
    return path.rsplit("/", 1)[-1]


def _relative_to(path: str, root: str) -> str | None:
    """``path`` expressed relative to ``root`` in POSIX form, or ``None`` when it is elsewhere."""
    if not root:
        return None
    normalised = path.replace("\\", "/")
    if not normalised.startswith(f"{root}/"):
        return None
    return normalised[len(root) + 1 :]


def _project_path(ent: Any, root: str = "") -> str | None:
    """The repository-relative POSIX path of a project file entity, else ``None``.

    Taken from ``Ent.longname()``, which for a file is its absolute path, made relative to the
    analysis root. **``Ent.relname()`` cannot be used for this**: it is relative to the root
    only while no analysed file sits directly in that root. As soon as one does, Understand
    prefixes every relative name with the root directory's own name — verified live,
    ``before/main.py`` for ``<cache>/before/main.py``, while a root holding only
    subdirectories answers ``pkg/core.py``. The gate analyses two shadow trees called
    ``before`` and ``after``, so that prefix is different on the two sides of every change: a
    key built from it matches nothing across the change, every requested file misses the
    ``files`` set, and the run comes back green with no entities at all. Any repository with a
    top-level source file (``setup.py``, ``conftest.py``, ``main.go``, ``index.js``) has that
    layout. ``relname`` is kept only as the fallback for a file the caller's root does not
    cover.

    Understand injects its own Python stubs (``<home>/conf/understand/python/...``) into
    every Python project and the API — unlike ``und list files`` — reports them. They carry a
    ``library()`` of ``'Standard'`` and, sitting outside the analysis root, an absolute
    ``relname``; both are refused here, which is what keeps ~800 ``builtins.*`` entities out
    of every snapshot (tasks.md 3.2).
    """
    if ent.library():
        return None
    name = _relative_to(str(ent.longname()), root) or ent.relname()
    if not isinstance(name, str) or not name:
        return None
    path = name.replace("\\", "/")
    if path.startswith("/") or (len(path) > 1 and path[1] == ":"):
        return None
    return path


MODULE_VARIABLE_KIND: Final = "Variable ~Local ~Unknown, Object ~Local ~Unknown, Global Object"
"""Entity kinds a module-level binding can have, across the languages Understand parses.

Written as one string with negations rather than per language: ``db.ents`` takes a kind
filter and answers nothing for a language that has no such kind, so a project in any one
language pays for the others only in the filter. ``~Local`` is what keeps function bodies
out -- a local of the same name in forty routines is not a scattered definition -- and
``~Unknown`` drops the bindings Understand infers for imports it could not resolve.

Whether a binding is really at module level is decided by its ``Definein`` owner rather
than by this string, because Understand gives a class attribute the same kind as a module
variable in several languages.
"""

DEFINITION_SPAN: Final = 12
"""How many lines past the binding the lexer will look for the end of its initialiser.

A value that does not close within twelve lines is left unread rather than truncated: a
half-read initialiser would compare equal to another half-read one and report two unrelated
definitions as copies of each other.
"""


def _is_file_kind(ent: Any) -> bool:
    """Whether ``ent`` is the file that holds a binding, rather than a class or a routine.

    Understand names the containing kind differently per language -- ``Module File`` for
    Python, ``File`` elsewhere -- so the test is on the word rather than on an enumeration
    that would silently answer ``False`` for a language nobody listed.
    """
    return "File" in str(ent.kindname())


def _initialiser(file_ent: Any, line: int) -> str | None:
    """The text bound to a name at ``line``, normalised, or ``None`` when it cannot be read.

    Comments and whitespace are dropped so that two spellings of one constant compare equal;
    ``_HORIZON_DAYS = 20  # monthly re-select`` and a bare ``_HORIZON_DAYS = 20`` are the same
    definition and a rule that said otherwise would miss every copy anybody commented.

    ``None`` is returned for a binding with no readable right-hand side -- an augmented
    assignment, a tuple unpacking, a bare annotation, an initialiser that does not close
    within :data:`DEFINITION_SPAN` lines. It is deliberately *not* an empty string: the caller
    never compares two unknown values, because "both unreadable" is not "both the same".
    """
    try:
        lexemes = list(file_ent.lexer().lexemes(line, line + DEFINITION_SPAN))
    except Exception:  # noqa: BLE001 -- a file with no lexer is a file with no definitions
        return None
    rest = _after_assignment(lexemes, line)
    return None if rest is None else "".join(_value_tokens(rest)) or None


def _after_assignment(lexemes: list[Any], line: int) -> list[Any] | None:
    """What follows the ``=`` that binds the name on ``line``, or ``None`` when there is none.

    The search stops at the end of ``line``: a name with no assignment operator on its own
    line is an annotation, a loop target or a tuple unpacking, and none of those is a value
    this rule can compare.
    """
    for index, lexeme in enumerate(lexemes):
        if lexeme.line_begin() > line:
            return None
        if lexeme.token() == "Operator" and lexeme.text() == "=":
            return lexemes[index + 1 :]
    return None


def _value_tokens(lexemes: list[Any]) -> list[str]:
    """The initialiser's text, comments and whitespace dropped, up to the end of the statement.

    Bracket depth is what decides where the statement ends: a newline inside an open bracket
    continues the value, and one outside it finishes it.
    """
    parts: list[str] = []
    depth = 0
    for lexeme in lexemes:
        token, text = lexeme.token(), lexeme.text()
        if token in ("Comment", "Whitespace"):
            continue
        if token == "Newline":
            if depth <= 0:
                break
            continue
        depth += (text in ("(", "[", "{")) - (text in (")", "]", "}"))
        parts.append(text)
    return parts


def _count_params(ent: Any) -> float | None:
    """The synthetic ``CountParams`` (req 3.5): Understand's native metric is unset (verified)."""
    return float(len(ent.ents("Define", PARAMETER_KIND)))


def _count_decl_method_non_stub(ent: Any) -> float | None:
    """The synthetic ``CountDeclMethodNonStub`` (req 3.5): declared methods less two per
    automatic property, floored at zero. A language without ``CountDeclPropertyAuto`` (Python,
    verified) has no trivial accessors to subtract, so every declared method counts."""
    raw = ent.metric(["CountDeclMethod", "CountDeclPropertyAuto"])
    declared = _as_float(raw.get("CountDeclMethod"))
    if declared is None:
        return None
    auto = _as_float(raw.get("CountDeclPropertyAuto")) or 0.0
    return max(0.0, declared - 2.0 * auto)


SYNTHETICS: Final[dict[str, dict[str, Callable[[Any], float | None]]]] = {
    "routine": {"CountParams": _count_params},
    "class": {"CountDeclMethodNonStub": _count_decl_method_non_stub},
}
"""Scope -> synthetic metric id -> its computation.

The declarations live in ``config.metric_names.SYNTHETIC_METRICS``; the computations live
here, because this module may not import the package.
"""


@dataclass(frozen=True, slots=True)
class _Key:
    """The four fields identifying an entity across the before and after databases.

    A file is keyed by its **root-relative** path, never by ``Ent.longname()``: a file's long
    name is absolute and embeds the shadow root, so keying by it makes the two sides of a
    change disagree about every file and silently switches off every file-scope ratchet
    (verified, the same failure research.md records for ``uniquename``).
    """

    scope: str
    path: str
    longname: str
    parameters: str | None

    @property
    def token(self) -> str:
        """The reversible string form ``EntityKey.token`` produces; class edges use it.

        Four elements, always. ``EntityKey`` carries a fifth -- an ordinal that separates the
        entities these four cannot, ``@typing.overload``'s triple above all -- but it is
        assigned on the model's side of the boundary, where the whole record list is in hand
        and this walk sees one entity at a time. A zero ordinal is left out of both the token
        and the key document, so the forms written here are exactly the forms the model
        writes back for every entity that was never ambiguous.
        """
        return json.dumps(
            [self.scope, self.path, self.longname, self.parameters], separators=(",", ":")
        )

    def document(self) -> dict[str, object]:
        """The wire form of the key."""
        return {
            "scope": self.scope,
            "path": self.path,
            "longname": self.longname,
            "parameters": self.parameters,
        }


@dataclass(frozen=True, slots=True)
class _Plan:
    """A validated ``snapshot`` request: everything the extraction needs, already checked."""

    db: str
    side: str
    root: str
    files: frozenset[str]
    kinds: dict[str, str]
    metrics: dict[str, list[str]]
    synthetic: frozenset[str]
    populations: dict[str, tuple[tuple[str | None, str], ...]]
    ignore: dict[str, tuple[re.Pattern[str], ...]]
    architecture: str
    depth: int
    include_edges: bool
    include_definitions: bool
    parse_errors: list[dict[str, object]]


def _require_bool(request: Mapping[str, object], key: str, default: bool) -> bool:
    """Read a boolean from the request or refuse it."""
    value = request.get(key, default)
    if not isinstance(value, bool):
        raise _RequestError("BadRequest", f"{key!r} must be a boolean, got {value!r}")
    return value


def _require_side(request: Mapping[str, object]) -> str:
    """Read which side of the change this snapshot describes, defaulting to ``after``."""
    value = request.get("side", "after")
    if not isinstance(value, str) or value not in SIDES:
        raise _RequestError(
            "BadRequest", f"'side' must be one of {', '.join(SIDES)}, got {value!r}"
        )
    return value


def _optional_str_list(request: Mapping[str, object], key: str) -> list[str]:
    """Read an optional list of strings; an absent key is empty, an explicit ``null`` is not."""
    return [] if key not in request else _require_str_list(request, key)


def _require_objects(request: Mapping[str, object], key: str) -> list[dict[str, object]]:
    """Read an optional list of JSON objects: parse errors, entity keys, graph targets."""
    if key not in request:
        return []
    value = request.get(key)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise _RequestError("BadRequest", f"{key!r} must be a list of objects, got {value!r}")
    return list(value)


def _require_str_map(request: Mapping[str, object], key: str) -> dict[str, str]:
    """Read a scope -> non-empty string mapping from the request or refuse it."""
    value = request.get(key, {})
    if not isinstance(value, dict):
        raise _RequestError(
            "BadRequest", f"{key!r} must be an object keyed by scope, got {value!r}"
        )
    for scope, text in value.items():
        if not isinstance(scope, str) or not isinstance(text, str) or not text:
            raise _RequestError(
                "BadRequest",
                f"{key!r} must map a scope to a non-empty string, got {scope!r}: {text!r}",
            )
    return dict(value)


def _require_str_list_map(request: Mapping[str, object], key: str) -> dict[str, list[str]]:
    """Read a scope -> list of strings mapping from the request or refuse it."""
    value = request.get(key, {})
    if not isinstance(value, dict):
        raise _RequestError(
            "BadRequest", f"{key!r} must be an object keyed by scope, got {value!r}"
        )
    for scope, names in value.items():
        ok = isinstance(scope, str) and isinstance(names, list)
        if not ok or not all(isinstance(name, str) for name in names):
            raise _RequestError(
                "BadRequest",
                f"{key!r} must map a scope to a list of strings, got {scope!r}: {names!r}",
            )
    return {scope: list(names) for scope, names in value.items()}


def _compile_ignore(raw: Mapping[str, list[str]]) -> dict[str, tuple[re.Pattern[str], ...]]:
    """Compile the per-scope ignore regexes once (req 3.6); an invalid one refuses the request."""
    compiled: dict[str, tuple[re.Pattern[str], ...]] = {}
    for scope, patterns in raw.items():
        try:
            compiled[scope] = tuple(re.compile(pattern) for pattern in patterns)
        except re.error as exc:
            raise _RequestError(
                "BadRequest", f"'ignore' pattern for scope {scope!r} is not a regex: {exc}"
            ) from exc
    return compiled


def _split_prefix(name: str) -> tuple[str | None, str]:
    """Split ``PREFIX:Metric`` into its stats prefix and its metric; a plain name has none."""
    prefix, separator, metric = name.partition(":")
    return (prefix, metric) if separator else (None, prefix)


def _population_entries(
    raw: Mapping[str, list[str]],
) -> dict[str, tuple[tuple[str | None, str], ...]]:
    """Parse the requested population metrics, keeping whether each carried a stats prefix."""
    return {scope: tuple(_split_prefix(name) for name in names) for scope, names in raw.items()}


def _normalise_root(root: str) -> str:
    """The analysis root in the form ``Ent.longname()`` uses: POSIX, no trailing separator."""
    return root.replace("\\", "/").rstrip("/")


def _plan(request: Mapping[str, object]) -> _Plan:
    """Validate a ``snapshot`` request completely, before any database is opened."""
    return _Plan(
        db=_require_str(request, "db"),
        side=_require_side(request),
        root=_normalise_root(_require_str(request, "root")),
        files=frozenset(_require_str_list(request, "files")),
        kinds=_require_str_map(request, "kinds_by_scope"),
        metrics=_require_str_list_map(request, "metrics_by_scope"),
        synthetic=frozenset(_optional_str_list(request, "synthetic")),
        populations=_population_entries(_require_str_list_map(request, "population_metrics")),
        ignore=_compile_ignore(_require_str_list_map(request, "ignore")),
        architecture=_require_str(request, "architecture"),
        depth=_require_depth(request),
        include_edges=_require_bool(request, "include_edges", True),
        include_definitions=_require_bool(request, "include_definitions", False),
        parse_errors=_require_objects(request, "parse_errors"),
    )


def _container_of(ent: Any, root: str) -> tuple[str, int | None] | None:
    """The project path of the file an entity is written in, and the line it sits on."""
    ref = ent.ref(CONTAINER_REFS)
    if ref is None:
        return None
    container = ref.file()
    if container is None:
        return None
    path = _project_path(container, root)
    return None if path is None else (path, ref.line())


def _may_hop(ent: Any, root: str) -> bool:
    """Whether the walk may pass through this entity to the code on the other side of it.

    Two conditions, both necessary: it is one of :data:`OBJECT_KINDS`, so that a namespace or
    another grouping entity cannot lend its users to everything inside it, and it is project
    code, so that a hop cannot run through Understand's own stubs and join two unrelated parts
    of a repository.
    """
    return ent.kind().check(OBJECT_KINDS) and _is_project(ent, root)


def _is_project(ent: Any, root: str) -> bool:
    """Whether an entity of *any* kind is project code: a project file, or written in one.

    The impact walk needs this for entities no scope of the gate keys — a local, a parameter,
    a member object, a macro — because those are what a C++ type is referenced by. It is the
    same judgement :func:`_locate` makes, minus the notion of a scope, so a library entity or
    one outside the analysis root is refused by exactly the same rule.
    """
    return _project_path(ent, root) is not None or _container_of(ent, root) is not None


def _locate(ent: Any, scope: str, root: str) -> tuple[str, int | None] | None:
    """The container file's path and the entity's line, or ``None`` when it is not project code.

    A file contains itself; every other entity is placed by its ``definein``/``declarein``
    reference. Whether an entity belongs to the project is decided in exactly one place —
    :func:`_project_path`, applied to its container file — which is what keeps the ~800
    ``builtins.*`` routines the API reports for a Python project out of every answer.
    """
    if scope == "file":
        path = _project_path(ent, root)
        return None if path is None else (path, None)
    return _container_of(ent, root)


def _key_of(ent: Any, scope: str, path: str) -> _Key:
    """Build the identity of one entity; a file's long name is its root-relative path."""
    longname = path if scope == "file" else str(ent.longname())
    return _Key(scope=scope, path=path, longname=longname, parameters=ent.parameters())


def _check_root(db: Any, kind: str | None, db_path: str, root: str) -> None:
    """Refuse an analysis root that names no file of this database.

    A root that is not the directory ``und add`` was pointed at resolves nothing: every file
    falls back to ``relname``, every key carries a path the caller never asked for, the
    requested files match none of them, and the answer is a valid, empty, entirely green
    document that gates nothing — the same silent failure a missing root would cause, which is
    why the root is required in the first place. It is a caller error and it is reported as
    one, with the long names that were actually found. Every operation that resolves entities
    checks it: an ``impact`` or ``graphs`` request against the wrong root would otherwise come
    back as one warning per key, which reads like "nothing depends on this" rather than like
    the configuration mistake it is.
    """
    if kind is None:
        return
    found = [ent for ent in db.ents(kind) if not ent.library()]
    if not found or any(_relative_to(str(ent.longname()), root) for ent in found):
        return
    raise _RequestError(
        "AnalysisRootMismatch",
        f"no file of {db_path} is under the analysis root {root!r}",
        found=sorted(str(ent.longname()) for ent in found)[:3],
    )


def _is_ignored(patterns: Sequence[re.Pattern[str]], key: _Key) -> bool:
    """Whether ``key`` matches an ignore regex of its scope (req 3.6).

    Matched with ``search`` against the qualified name, as ``srccheck`` did, plus the path for
    the file scope, because a file rule is naturally written as a path fragment — the same
    two subjects ``analysis.population.IgnoreFilter`` uses.
    """
    if not patterns:
        return False
    subjects = (key.longname, key.path) if key.scope == "file" else (key.longname,)
    return any(pattern.search(subject) for pattern in patterns for subject in subjects)


def _guards_type_checking(test: ast.expr) -> bool:
    """Whether an ``if`` tests ``TYPE_CHECKING``, written bare or through its module.

    Only the plain positive form. ``if not TYPE_CHECKING:`` guards a body that *does* run, and
    reading it as erased would drop a real import-time dependency -- the direction of error
    this whole field exists to avoid.
    """
    if isinstance(test, ast.Name):
        return test.id == TYPE_CHECKING_NAME
    return isinstance(test, ast.Attribute) and test.attr == TYPE_CHECKING_NAME


def _span(nodes: Sequence[ast.stmt]) -> range:
    """The lines a run of statements occupies, first line to last, inclusive."""
    first = nodes[0].lineno
    last = max((node.end_lineno or node.lineno) for node in nodes)
    return range(first, last + 1)


def _deferred_lines(source: str) -> frozenset[int] | None:
    """The lines of a Python module that importing it does **not** execute.

    Two constructs, both parsed rather than matched: the body of an ``if TYPE_CHECKING:``,
    which the interpreter erases, and the body of any function, which does not run until the
    function is called. Parsed and not grepped because a regular expression over source has
    already produced a false positive in this repository, matching a docstring that *described*
    the construct it was searching for -- and because only a parse knows where a body ends.

    A decorator is not in a function's span: it sits above ``body[0]`` and it does run at
    import. Neither is a ``def`` line's own annotations or defaults. An ``else:`` branch of an
    ``if TYPE_CHECKING:`` is not in the span either, because it is the branch that runs.

    ``None`` -- never an empty set -- when the source will not parse, so that an unparsable
    module keeps the behaviour it had before this function existed.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return None
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.body:
            lines.update(_span(node.body))
        elif isinstance(node, ast.If) and node.body and _guards_type_checking(node.test):
            lines.update(_span(node.body))
    return frozenset(lines)


def _import_time_lines(ent: Any) -> frozenset[int] | None:
    """The deferred lines of a file entity, or ``None`` when they were not measured.

    ``None`` for every language but Python, for a file whose source the API will not hand over,
    and for one that will not parse. Each of those is "not measured", and a consumer must read
    it as the older behaviour rather than as "nothing is deferred".
    """
    if str(ent.language()) != PYTHON_LANGUAGE:
        return None
    try:
        source = ent.contents()
    except Exception:  # noqa: BLE001 - the API's own error class cannot be named here
        # `understand.UnderstandError` is not importable at this level (the module is loaded
        # lazily, by design), and a file whose text cannot be read must cost this one edge its
        # measurement rather than the whole extraction.
        return None
    return None if not isinstance(source, str) else _deferred_lines(source)


def _import_time_refs(refs: Iterable[Any], deferred: frozenset[int]) -> int:
    """How many of ``refs`` are imports on a line that importing the module executes."""
    return sum(1 for ref in refs if ref.kind().check(IMPORT_REFS) and ref.line() not in deferred)


def _call_bucket(target: Any, by_id: Mapping[int, str], constructors: Mapping[int, str]) -> str:
    """Which of :data:`CALL_BUCKETS` one call site falls into, from what it landed on.

    The order matters and is not arbitrary. A target that is a node of the graph is resolved
    whatever else it also is; a target that is callable but is not a node is external; and
    only what is neither is unresolved. Asking ``CALLABLE_KINDS`` first would call a project
    class external even when its constructor is a node, and asking it last would let a
    ``Variable Attribute Instance`` -- the shape ``self.fn(x)`` produces -- pass as resolved.
    """
    ident = target.id()
    if ident in by_id or ident in constructors:
        return "resolved"
    return "external" if target.kind().check(CALLABLE_KINDS) else "unresolved"


def _metric_values(ent: Any, names: Sequence[str]) -> dict[str, float]:
    """The metrics of ``names`` Understand can express as a number for this entity."""
    if not names:
        return {}
    raw = ent.metric(list(names))
    values: dict[str, float] = {}
    for name in names:
        number = _as_float(raw.get(name))
        if number is not None:
            values[name] = number
    return values


class _Extractor:
    """One pass over one database, producing the ``ProjectSnapshot`` document (task 6.2).

    The instance is the working state of a single extraction: it walks each element scope
    once, building the entity records of the requested files and the whole-project population
    vectors in the same pass, then derives the dependency edges and the architecture from what
    it collected.
    """

    def __init__(self, db: Any, plan: _Plan) -> None:
        self.db = db
        self.plan = plan
        self.nodes: list[Any] = []
        self.node_root: Any = None
        self.uncovered: frozenset[str] = frozenset()
        self.arch_name = ""
        self.walk_root = ""
        self.arch_of: dict[str, list[str]] = {}
        self.file_ents: dict[str, Any] = {}
        self.class_ents: dict[str, tuple[Any, str]] = {}
        self.routine_ents: dict[str, tuple[Any, str]] = {}
        self.deferred: dict[str, frozenset[int] | None] = {}
        self.records: list[dict[str, object]] = []
        self.collected: dict[str, dict[str, list[float]]] = {}
        self.unavailable: dict[str, set[str]] = {}

    def build(self) -> dict[str, object]:
        """Extract everything the snapshot holds and answer with its wire form."""
        _check_root(self.db, self.plan.kinds.get("file"), self.plan.db, self.plan.root)
        self._read_architecture()
        for scope in SNAPSHOT_SCOPES:
            self._read_scope(scope)
        document: dict[str, object] = {
            "side": self.plan.side,
            "languages": sorted(self.db.language()),
            "entities": sorted(self.records, key=_record_order),
            "arch_nodes": self._arch_documents(),
            "populations": self._populations(),
            "unavailable": {
                language: sorted(metrics) for language, metrics in sorted(self.unavailable.items())
            },
            "parse_errors": self.plan.parse_errors,
            "definitions": self._definitions(),
        }
        document.update(self._edges())
        return document

    # --- module-level definitions (the duplicate-definition rule) ------------------

    def _definitions(self) -> list[dict[str, object]]:
        """Every module-level binding in the project, with the text it is bound to.

        Whole-project, not the affected files: the rule's question is "how many files define
        this name with this value", and an answer computed over the changed files alone would
        report the second copy of a constant and stay silent about the twentieth.

        A binding is module-level when its ``Definein`` owner is a file. That is the test
        rather than the entity kind, because Understand gives a class attribute the same kind
        as a module variable in several of the languages it parses, and a field initialised
        in one class is not a scattered definition.
        """
        if not self.plan.include_definitions:
            return []
        found: list[dict[str, object]] = []
        for ent in self.db.ents(MODULE_VARIABLE_KIND):
            for ref in ent.refs("Definein"):
                path = _project_path(ref.file(), self.plan.root)
                if path is None or not _is_file_kind(ref.ent()):
                    continue
                found.append(
                    {
                        "name": ent.name(),
                        "path": path,
                        "line": ref.line(),
                        "value": _initialiser(ref.file(), ref.line()),
                    }
                )
        return sorted(found, key=lambda row: (row["path"], row["line"], row["name"]))

    # --- architecture (req 6.7, 6.8, 9.7) -----------------------------------------

    def _read_architecture(self) -> None:
        """Take the nodes of the requested architecture at the requested depth and their files."""
        root = self.db.lookup_arch(self.plan.architecture)
        if root is None:
            raise _RequestError(
                "ArchitectureNotFound",
                f"architecture {self.plan.architecture!r} does not exist in {self.plan.db}",
                available=[arch.longname() for arch in self.db.root_archs()],
            )
        self.arch_name = root.longname()
        self.node_root = self._analysis_root_node(root)
        self.walk_root = self.node_root.longname()
        self.nodes = _nodes_at_depth(self.node_root, self.plan.depth)
        for node in self.nodes:
            path = self._node_path(node.longname())
            for member in _member_files(node, self.plan.root):
                self.arch_of.setdefault(member, []).append(path)
        self.uncovered = frozenset(
            member
            for member in _member_files(self.node_root, self.plan.root)
            if member not in self.arch_of
        )

    def _analysis_root_node(self, root: Any) -> Any:
        """The architecture node that stands for the analysis root itself.

        Understand inserts a level named after the analysis root directory as soon as a file
        sits directly in it (verified), so one directory of one repository is
        ``Directory Structure/before/pkg`` on one side of a change and
        ``Directory Structure/after/pkg`` on the other. Walking from below that level, and
        naming nodes for the architecture rather than for the shadow, keeps both the node
        paths and the meaning of ``depth`` identical on the two sides.

        The level is recognised **by name**: the architecture has a single child, and that
        child is called exactly what the analysis root directory is called. Nothing weaker
        works. Understand roots ``Directory Structure`` at the parent of the deepest common
        ancestor of the *analysed* files — files it does not analyse never enter it (verified:
        adding ``README.md`` and ``pyproject.toml`` changes nothing) — so a repository whose
        sources all sit under one nested path also shows a single child holding every file
        whose name is not the first component of their repository-relative paths
        (``Directory Structure/app`` for ``src/app/entry.py``). Stripping that one would
        delete a real directory level, disagree with the ``archs`` operation, drop every file
        above it out of the node-level rules, and move the whole node set as soon as one file
        is added a level up. The second test then covers the name clash: a repository whose
        sources really do all live in a directory called like the shadow keeps that
        directory, because its own name *is* the first component of their paths.
        """
        children = root.children()
        if len(children) != 1:
            return root
        child = children[0]
        name = _basename(child.longname())
        if name != _basename(self.plan.root):
            return root
        members = _member_files(child, self.plan.root)
        if not all(member.startswith(f"{name}/") for member in members):
            return child
        return root

    def _node_path(self, longname: str) -> str:
        """An architecture node's path with the analysis root's own name taken back out."""
        if longname == self.walk_root:
            return self.arch_name
        if longname.startswith(f"{self.walk_root}/"):
            return f"{self.arch_name}/{longname[len(self.walk_root) + 1 :]}"
        return longname

    def _trim(self, longname: str) -> str | None:
        """Trim an architecture path to the requested depth, or ``None`` when it is elsewhere."""
        path = self._node_path(longname)
        if path == self.arch_name:
            return self.arch_name
        if not path.startswith(f"{self.arch_name}/"):
            return None
        parts = path[len(self.arch_name) + 1 :].split("/")[: self.plan.depth]
        return "/".join([self.arch_name, *parts]) if parts else self.arch_name

    def _nodes_of(self, path: str) -> list[str]:
        """The architecture nodes a file belongs to, in sorted order (req 9.7).

        A file that no node at the configured depth holds — one sitting directly in the
        analysis root, ``setup.py``, ``conftest.py``, ``main.go`` — still belongs to the
        architecture, and is attributed to the architecture itself rather than to nothing:
        leaving it unplaced would exempt it from every node-level structural rule and leave
        requirement 9.7 without the path it asks for. A file the architecture does not contain
        at all stays unplaced, because an unknown node is not a node.

        Understand's walk order is not the repository's, so a file that several nodes of a
        user-defined architecture hold is reported under the first in sorted order, or the two
        sides of a change could disagree about a file that never moved.
        """
        nodes = self.arch_of.get(path)
        if nodes:
            return sorted(nodes)
        return [self.arch_name] if path in self.uncovered else []

    def _node_of(self, path: str) -> str | None:
        """The architecture node a file is reported under, or ``None`` when it has none."""
        nodes = self._nodes_of(path)
        return nodes[0] if nodes else None

    def _arch_documents(self) -> list[dict[str, object]]:
        """The architecture nodes of the answer, the walk root included when it holds files.

        The walk root is listed only with the files no deeper node holds, so that every file
        appears under exactly one node and ``change_summary``'s file -> node index agrees with
        every ``EntityRecord.archs``.
        """
        documents: list[dict[str, object]] = [
            {
                "path": self._node_path(node.longname()),
                "members": _member_files(node, self.plan.root),
            }
            for node in self.nodes
        ]
        if self.uncovered:
            documents.append({"path": self.arch_name, "members": sorted(self.uncovered)})
        return sorted(documents, key=lambda node: str(node["path"]))

    def _crosses(self, src: str, dst: str) -> bool:
        """Whether two files sit in different architecture nodes, both of them known (req 9.2)."""
        src_node, dst_node = self._node_of(src), self._node_of(dst)
        return src_node is not None and dst_node is not None and src_node != dst_node

    # --- entities, metrics and populations ----------------------------------------

    def _wanted(self, scope: str) -> set[str]:
        """The population metrics to collect for ``scope``, including what ``project`` needs."""
        wanted = {metric for _, metric in self.plan.populations.get(scope, ())}
        if scope == "routine":
            wanted |= {metric for _, metric in self.plan.populations.get("project", ())}
        return wanted

    def _read_scope(self, scope: str) -> None:
        """Walk one element scope once: population values for all of it, records for the files."""
        kind = self.plan.kinds.get(scope)
        if kind is None:
            return
        wanted = self._wanted(scope)
        names = sorted(set(self.plan.metrics.get(scope, ())) | wanted)
        values: dict[str, list[float]] = {}
        patterns = self.plan.ignore.get(scope, ())
        for ent in self.db.ents(kind):
            located = _locate(ent, scope, self.plan.root)
            if located is None:
                continue
            key = _key_of(ent, scope, located[0])
            if _is_ignored(patterns, key):
                continue
            metrics = self._entity_metrics(ent, scope, names)
            self._remember(key, ent)
            _extend(values, metrics, wanted)
            if key.path in self.plan.files:
                self.records.append(self._record(ent, key, located[1], metrics))
        self.collected[scope] = {metric: sorted(vector) for metric, vector in values.items()}

    def _entity_metrics(self, ent: Any, scope: str, names: Sequence[str]) -> dict[str, float]:
        """Every requested metric of one entity, synthetics included, plus what is unavailable.

        A metric Understand has no value for is omitted from the record and reported once per
        language (req 5.5) rather than defaulted, because a zero is a claim the database never
        made. A synthetic the request asked for replaces the native metric of the same name.

        Only the metrics of ``metrics_by_scope`` count towards ``unavailable``: those are the
        ones an entity is judged by. A metric collected purely to build a population vector is
        a project-level threshold, and an empty vector is reported once by the evaluator's
        reducer failures instead of once per entity of the wrong language.
        """
        wanted = self.plan.synthetic & set(names) & set(SYNTHETICS.get(scope, {}))
        values = _metric_values(ent, [name for name in names if name not in wanted])
        for name in sorted(wanted):
            number = SYNTHETICS[scope][name](ent)
            if number is not None:
                values[name] = number
        missing = set(self.plan.metrics.get(scope, ())) - set(values)
        if missing:
            self.unavailable.setdefault(str(ent.language()), set()).update(missing)
        return values

    def _remember(self, key: _Key, ent: Any) -> None:
        """Keep the entities the edges are built from: files, classes and routines alike.

        Routines are kept for the **whole project**, not only for the requested files, because
        a reach rule follows calls out of the change and has to be able to name and measure
        what it arrives at. The walk that fills this already visits every one of them, so the
        cost is the reference and not a second query.

        Measured cost of keying by token: two routine keys of a real 770-file project name
        three records each -- ``@typing.overload``-shaped duplicates that only
        ``EntityKey.ordinal`` can separate, and the ordinal is assigned on the model's side of
        this boundary -- so four routines collapse onto two nodes, 7 417 nodes for 7 421
        routines. That is the same limitation the class edges already carry, recorded in
        ``models.snapshot._index_by_key``, and it is the smaller of the two available errors:
        the alternative is inventing an ordinal here, from a walk that sees one entity at a
        time, that would not agree with the one the model assigns.
        """
        if key.scope == "file":
            self.file_ents[key.path] = ent
        elif key.scope == "class":
            self.class_ents[key.token] = (ent, key.path)
        elif key.scope == "routine":
            self.routine_ents[key.token] = (ent, key.path)

    def _record(
        self, ent: Any, key: _Key, line: int | None, metrics: Mapping[str, float]
    ) -> dict[str, object]:
        """One entity record in its wire form; ``is_new`` is the diff layer's answer, not ours.

        The architectures are those of the container file: ``Db.archs()`` answers with nothing
        for routines and classes (verified), so a routine is located by the file it lives in
        (requirement 9.7).
        """
        return {
            "ref": {
                "key": key.document(),
                "kind": str(ent.kind().longname()),
                "name": str(ent.name()),
                "line": line,
            },
            "language": str(ent.language()),
            "metrics": dict(metrics),
            "archs": self._nodes_of(key.path),
        }

    def _populations(self) -> dict[str, dict[str, list[float]]]:
        """The population vectors the request asked for, already ignore-filtered (req 3.4)."""
        document: dict[str, dict[str, list[float]]] = {}
        for scope in SNAPSHOT_SCOPES:
            collected = self.collected.get(scope, {})
            vectors = {
                metric: collected[metric]
                for _, metric in self.plan.populations.get(scope, ())
                if collected.get(metric)
            }
            if vectors:
                document[scope] = vectors
        project = self._project_populations()
        if project:
            document["project"] = project
        return document

    def _project_populations(self) -> dict[str, list[float]]:
        """The ``project`` vectors, which have no entities of their own to be counted over.

        A **plain** project threshold is read from a single-element vector (the 4.1 contract),
        and that value is the database's own metric — ``project.MaxCyclomaticStrict`` is 7 for
        the sample project. A **stats-prefixed** one is reduced over the population of the
        scope the metric belongs to (req 5.4: ``AVG:CyclomaticStrict`` is the mean over
        routines, ``AVG:CountLineCode`` the mean routine length); ``db.metric`` has no value
        for those at all (verified: ``CyclomaticStrict`` is ``None`` on the database). A plain
        metric the database does not know falls back to the routine population, so a threshold
        never silently stops firing.
        """
        routines = self.collected.get("routine", {})
        vectors: dict[str, list[float]] = {}
        for prefix, metric in self.plan.populations.get("project", ()):
            value = None if prefix is not None else _as_float(self.db.metric([metric]).get(metric))
            vector = [value] if value is not None else list(routines.get(metric, ()))
            if vector:
                vectors[metric] = vector
        return vectors

    # --- dependency edges (req 6.1-6.6, 9.2) --------------------------------------

    def _edges(self) -> dict[str, object]:
        """The edge lists, or empty ones when the caller wants no edges."""
        if not self.plan.include_edges:
            return {
                "file_edges": [],
                "class_edges": [],
                "arch_edges": [],
                "call_edges": [],
                "call_nodes": [],
                "call_resolution": {},
            }
        document: dict[str, object] = {
            "file_edges": self._file_edges(),
            "class_edges": self._class_edges(),
            "arch_edges": self._arch_edges(),
        }
        document.update(self._call_graph())
        return document

    # --- the call graph -----------------------------------------------------------

    def _constructors(self) -> dict[int, str]:
        """Every project class's entity id -> the routine token a call on the class runs.

        ``Widget()`` is a call reference to the **class**, never to a routine, so a call graph
        that only followed routine targets would lose every construction -- 11 517 of the
        44 783 call sites of a measured 770-file Python project, a quarter of the whole graph.
        The class is therefore mapped to the routine that a call on it actually enters: its
        ``__init__`` on Python, its same-named constructor on C++ and Java.

        Measured limit, stated because it is a false negative and not a rounding error: only
        94 of 1 343 project classes in that project declare a constructor of their own, so a
        call on any of the other 1 249 -- a dataclass, a plain container, a subclass that
        inherits ``__init__`` -- maps to nothing and is counted ``external`` rather than
        resolved. An inherited constructor is deliberately **not** followed: the base may sit
        outside the analysis root, and a graph that guessed would be asserting an edge the
        database never reported.
        """
        found: dict[int, str] = {}
        by_id = {ent.id(): token for token, (ent, _) in self.routine_ents.items()}
        for ent, _ in self.class_ents.values():
            token = self._constructor_of(ent, by_id)
            if token is not None:
                found[ent.id()] = token
        return found

    @staticmethod
    def _constructor_of(cls_ent: Any, by_id: Mapping[int, str]) -> str | None:
        """The token of ``cls_ent``'s own constructor, or ``None`` when it declares none."""
        wanted = (CONSTRUCTOR_NAME, str(cls_ent.name()))
        for ref in cls_ent.refs(MEMBER_REFS):
            target = ref.ent()
            token = by_id.get(target.id())
            if token is not None and str(target.name()) in wanted:
                return token
        return None

    def _call_graph(self) -> dict[str, object]:
        """The routine call graph, its per-routine blind spots and its resolution report.

        One pass over every project routine reads its call references and sorts each one into
        the bucket :class:`CallResolution` documents; the counts are project-wide, because the
        rate describes the *substrate* and must not shrink to whatever the change happened to
        touch. The edges and nodes that are published are then bounded to the routines
        forward-reachable from the requested files, which keeps the answer proportional to the
        change (req 4.11) while still holding everything a reach or a cycle rule can need: a
        routine on a cycle through a seed is reachable from that seed by definition, so the
        induced subgraph carries the seed's strongly connected component whole.

        A self-call yields no edge, for the same reason a self-dependency is not a cycle, but
        it is still counted resolved: the call site did bind.
        """
        constructors = self._constructors()
        by_id = {ent.id(): token for token, (ent, _) in self.routine_ents.items()}
        counts: dict[tuple[str, str], int] = {}
        blind: dict[str, int] = {}
        resolution: dict[str, dict[str, int]] = {}
        for token, (ent, _) in self.routine_ents.items():
            tally = resolution.setdefault(str(ent.language()), dict.fromkeys(CALL_BUCKETS, 0))
            for ref in ent.refs(CALL_REFS):
                target = ref.ent()
                bucket = _call_bucket(target, by_id, constructors)
                tally[bucket] += 1
                if bucket == "unresolved":
                    blind[token] = blind.get(token, 0) + 1
                other = by_id.get(target.id()) or constructors.get(target.id())
                if bucket == "resolved" and other is not None and other != token:
                    counts[(token, other)] = counts.get((token, other), 0) + 1
        return self._call_documents(counts, blind, resolution)

    def _call_documents(
        self,
        counts: Mapping[tuple[str, str], int],
        blind: Mapping[str, int],
        resolution: Mapping[str, Mapping[str, int]],
    ) -> dict[str, object]:
        """The three call keys, bounded to the forward closure of the requested routines."""
        held = self._reachable(counts)
        return {
            "call_edges": [
                {"src": src, "dst": dst, "refs": refs, "crosses_arch": False}
                for (src, dst), refs in sorted(counts.items())
                if src in held
            ],
            "call_nodes": [self._call_node(name, blind.get(name, 0)) for name in sorted(held)],
            "call_resolution": {
                language: dict(tally) for language, tally in sorted(resolution.items())
            },
        }

    def _call_node(self, token: str, unresolved: int) -> dict[str, object]:
        """One graph node: its endpoint, its own complexity, its unbound call sites.

        ``complexity`` is ``None`` and never ``0.0`` where the database has no value, so that
        a rule summing over a reached set can report how much of it was unmeasured instead of
        counting an unmeasured routine as free.
        """
        ent = self.routine_ents[token][0]
        value = _as_float(ent.metric([CALL_METRIC]).get(CALL_METRIC))
        return {"node": token, "complexity": value, "unresolved_calls": unresolved}

    def _reachable(self, counts: Mapping[tuple[str, str], int]) -> set[str]:
        """The requested routines and everything they transitively call."""
        successors: dict[str, list[str]] = {}
        for src, dst in counts:
            successors.setdefault(src, []).append(dst)
        seeds = self.routine_ents.items()
        found = {token for token, (_, path) in seeds if path in self.plan.files}
        pending = list(found)
        while pending:
            for other in successors.get(pending.pop(), ()):
                if other not in found:
                    found.add(other)
                    pending.append(other)
        return found

    def _neighbourhood(
        self, names: Iterable[str], resolve: Callable[[Any], str | None]
    ) -> set[str]:
        """The requested entities of one scope together with everything directly next to them."""
        seeds = {name for name in names if name in self.plan.files or self._is_seed(name)}
        found = set(seeds)
        for name in seeds:
            found |= set(self._targets(name, resolve))
        return found

    def _is_seed(self, name: str) -> bool:
        """Whether an entity named by ``name`` is one the request asked about."""
        entry = self.class_ents.get(name)
        return entry is not None and entry[1] in self.plan.files

    def _entity(self, name: str) -> Any:
        """The live entity behind a file path or a class key token."""
        entry = self.class_ents.get(name)
        return self.file_ents[name] if entry is None else entry[0]

    def _targets(self, name: str, resolve: Callable[[Any], str | None]) -> list[str]:
        """Everything the named entity depends on or that depends on it, in this scope."""
        ent = self._entity(name)
        pairs = list(ent.depends().items()) + list(ent.dependsby().items())
        found = (resolve(other) for other, _ in pairs)
        return [other for other in found if other is not None]

    def _file_of(self, ent: Any) -> str | None:
        """The path of a file entity, when it is a project file this snapshot knows."""
        path = _project_path(ent, self.plan.root)
        return path if path is not None and path in self.file_ents else None

    def _class_of(self, ent: Any) -> str | None:
        """The key token of a class entity, when it is a project class this snapshot knows."""
        located = _locate(ent, "class", self.plan.root)
        if located is None:
            return None
        token = _key_of(ent, "class", located[0]).token
        return token if token in self.class_ents else None

    def _file_edges(self) -> list[dict[str, object]]:
        """File dependencies with reference counts, bounded by the affected neighbourhood.

        These are the only edges whose import-time share is measured, because the constructs
        that defer an import -- ``if TYPE_CHECKING:`` and a function-local ``import`` -- are
        properties of a *module*, and a class edge has no import of its own.
        """
        scope = self._neighbourhood(self.file_ents, self._file_of)
        return self._collect_edges(scope, self._file_of, crossing=True, timed=True)

    def _class_edges(self) -> list[dict[str, object]]:
        """Class dependencies; the endpoints are ``EntityKey.token`` values, as ``fan`` expects."""
        scope = self._neighbourhood(self.class_ents, self._class_of)
        return self._collect_edges(scope, self._class_of, crossing=False)

    def _deferred_of(self, path: str) -> frozenset[int] | None:
        """The deferred lines of one file, parsed at most once per extraction."""
        if path not in self.deferred:
            ent = self.file_ents.get(path)
            self.deferred[path] = None if ent is None else _import_time_lines(ent)
        return self.deferred[path]

    def _collect_edges(
        self,
        scope: set[str],
        resolve: Callable[[Any], str | None],
        crossing: bool,
        timed: bool = False,
    ) -> list[dict[str, object]]:
        """Every dependency inside ``scope``, merged by endpoint pair and counted.

        ``timed`` additionally counts, per edge, how many of its references the import of the
        source actually executes. It stays ``None`` for a source the parse could not measure,
        which is every language but Python and every Python file whose text will not parse.
        """
        counts: dict[tuple[str, str], int] = {}
        timing: dict[tuple[str, str], int | None] = {}
        for src in sorted(scope):
            deferred = self._deferred_of(src) if timed else None
            measured = timed and deferred is not None
            for other, refs in self._entity(src).depends().items():
                dst = resolve(other)
                if dst is None or dst == src or dst not in scope:
                    continue
                counts[(src, dst)] = counts.get((src, dst), 0) + len(refs)
                timing[(src, dst)] = (
                    _import_time_refs(refs, deferred) if measured and deferred is not None else None
                )
        return [
            self._edge(pair, count, crossing, timing.get(pair))
            for pair, count in sorted(counts.items())
        ]

    def _edge(
        self, pair: tuple[str, str], refs: int, crossing: bool, import_time: int | None = None
    ) -> dict[str, object]:
        """One dependency edge; a class edge names entities, so it has no architecture to cross.

        ``import_time`` is written only when it was measured, so an edge nobody could measure
        is on the wire exactly as it was before the field existed.
        """
        src, dst = pair
        crosses = self._crosses(src, dst) if crossing else self._crosses_classes(src, dst)
        edge: dict[str, object] = {"src": src, "dst": dst, "refs": refs, "crosses_arch": crosses}
        if import_time is not None:
            edge["import_time"] = import_time
        return edge

    def _crosses_classes(self, src: str, dst: str) -> bool:
        """Whether two classes are defined in files that sit in different architecture nodes."""
        return self._crosses(self.class_ents[src][1], self.class_ents[dst][1])

    def _arch_edges(self) -> list[dict[str, object]]:
        """Dependencies between the architecture nodes, trimmed to the requested depth (6.2).

        The walk root has no OUTGOING dependencies: it contains every analysed file, so there
        is nothing outside it to depend on, and ``Arch.depends()`` on it is empty (measured on
        a shadow with a top-level file and on a nested source tree). Understand does not report
        a parent -> descendant dependency, so a dependency that STARTS at a file sitting
        directly in the analysis root has no architecture edge and is visible only as a file
        edge with ``crosses_arch`` set.

        The INCOMING direction is real and must not be dropped: a deeper node that depends on
        a root-level file yields ``Directory Structure/pkg -> Directory Structure`` (measured
        at 3 refs). The walk root is therefore a valid edge endpoint exactly when the answer
        publishes it as a node, which is what :meth:`_arch_documents` decides.
        """
        counts: dict[tuple[str, str], int] = {}
        paths = {self._node_path(node.longname()) for node in self.nodes}
        if self.uncovered:
            paths.add(self.arch_name)
        for node in self.nodes:
            src = self._node_path(node.longname())
            for other, refs in node.depends().items():
                dst = self._trim(other.longname())
                if dst is not None and dst != src and dst in paths:
                    counts[(src, dst)] = counts.get((src, dst), 0) + len(refs)
        return [
            {"src": src, "dst": dst, "refs": count, "crosses_arch": True}
            for (src, dst), count in sorted(counts.items())
        ]


def _record_order(record: Mapping[str, Any]) -> str:
    """Order records the way ``ProjectSnapshot`` serializes them: by their key token."""
    key = record["ref"]["key"]
    return json.dumps(
        [key["scope"], key["path"], key["longname"], key["parameters"]], separators=(",", ":")
    )


def _extend(
    values: dict[str, list[float]], metrics: Mapping[str, float], wanted: Iterable[str]
) -> None:
    """Add one entity's contribution to the population vectors of its scope."""
    for metric in wanted:
        number = metrics.get(metric)
        if number is not None:
            values.setdefault(metric, []).append(number)


def _op_snapshot(api: Any, request: Mapping[str, object]) -> dict[str, object]:
    """Turn one database into a ``ProjectSnapshot`` document (req 3.5, 5.5, 6.7, 9.7).

    The request is entirely self-describing — the worker runs under Understand's own
    interpreter and may not import this project — so it carries the files of interest, the
    kind string of every scope, the metrics and synthetic metric ids to compute, the ignore
    regexes, the population metrics, and the architecture and depth. Everything is validated
    before the database is opened, and the database is closed before the answer leaves.
    """
    plan = _plan(request)
    db = api.open(plan.db)
    try:
        return _Extractor(db, plan).build()
    finally:
        db.close()


# --- the impact and graph operations (req 9.4, 9.5) ---------------------------------

STRUCTURAL_REFS: Final = "definein, declarein"
"""Reverse reference kinds that lead to an entity's container rather than to a user of it.

The impact walk takes *every* reverse reference Understand records — direction comes from
``Ref.isforward()``, Understand's own bit, so no reference kind can be forgotten — and then
leaves out only these two. A container holds an entity; it does not depend on it. Measured:
a member function carries ``C Declarein`` and ``C Definein`` to its class and a class carries
them to its namespace, so keeping them would make every method report the class that holds it
and then drag that class's whole blast radius one level further out.

Three more tokens were carried here for a while on the strength of their names and were
dropped once measured: ``beginby`` and ``endby`` are only ever self-references (0 non-self
occurrences across three databases) and an entity is already kept out of its own impact set,
and ``containin`` was never emitted at all — not by a nested class, a nested function, a
comprehension, a generator, a lambda or a coroutine.

An earlier version enumerated the reverse kinds to *include* (``callby, useby, setby, …``).
That was measurably wrong: a C++ exception class carries ``C Throwby Exception`` and
``C Catchby Exception``, a function pointer ``C Assignby FunctionPtr``, a class named in
another translation unit ``C Nameby`` — none of which were in the list, so the blast radius
came back empty for code that plainly had one. ``Kind.list_reference`` cannot close that gap:
it answers with both directions at once and so proves a token exists, never that a list is
complete.
"""

OBJECT_KINDS: Final = "object, parameter, macro, variable, field, property"
"""Entity kinds the walk may pass *through* on its way to the code that uses an entity.

This is a positive list, and deliberately so. In C++ the thing that refers to a type is
usually not a routine but an object: measured on real databases, ``class Box`` used as
``Box box(3); box.area();`` is referenced only by ``box``, a ``C Object Local``; a struct
passed as a parameter only by the ``C Parameter``; a class held as a member only by the
``C Private Member Object``. Those hops have to be followed or the answer is empty.

What must *not* be followed is anything that merely groups unrelated entities. A namespace is
the case that proves it: every class defined inside a ``namespace app { … }`` block carries
``C Nameby`` to the namespace, and the namespace refers back to everything that mentions it,
so walking through it gives every class in the namespace the same inflated answer — the
namespace's user count wearing each class's name. Verified that this filter matches
``C Object Local``, ``C Object Global``, ``C Parameter``, ``C Private/Public Member Object``
and ``C Macro`` while refusing ``C Namespace``, ``C Namespace Alias``, ``C Class Type``,
``C Typedef Type``, ``C Code File`` and every function kind.

Known cost, measured: a type reached only through a ``C Typedef Type`` (``typedef Payload
Alias;`` then ``Alias a;``) loses that user, because a typedef is not an object. Unlike a
namespace a typedef aliases exactly one type and so could not inflate an answer, but it is
outside what this list is justified by; recorded for 6.6 rather than guessed at.
"""

GRAPH_SUFFIX: Final = ".svg"
"""Requirement 9.4 asks for SVG, and ``Ent.draw`` picks the format from the extension."""


@dataclass(frozen=True, slots=True)
class _Located:
    """One project entity of an element scope: the live entity plus what an ``EntityRef`` shows."""

    ent: Any
    key: _Key
    line: int | None

    def document(self) -> dict[str, object]:
        """The ``EntityRef`` wire form: the identity, Understand's kind and name, the line."""
        return {
            "key": self.key.document(),
            "kind": str(self.ent.kind().longname()),
            "name": str(self.ent.name()),
            "line": self.line,
        }


class _Entities:
    """Every project entity of the element scopes, reachable by key token and by entity id.

    Both operations here start from an ``EntityKey`` the caller computed from an earlier
    snapshot, and the API offers no lookup by that identity, so the scopes are walked once
    and indexed. The index is built with :func:`_locate` and :func:`_key_of` — the very
    functions the snapshot keys entities with — so a key that named an entity there names the
    same entity here, and a database that has no such entity says so instead of guessing.

    The identity index doubles as the project filter: an entity Understand marks as a library,
    or one whose container file sits outside the analysis root, never enters it, so neither
    the impact walk nor a graph target can reach the ~800 ``builtins.*`` entities Understand
    injects into a Python project.
    """

    def __init__(self, db: Any, kinds: Mapping[str, str], root: str) -> None:
        self.root = root
        self.by_token: dict[str, _Located] = {}
        self.by_id: dict[int, str] = {}
        for scope in SNAPSHOT_SCOPES:
            self._read(db, kinds.get(scope), scope, root)

    def _read(self, db: Any, kind: str | None, scope: str, root: str) -> None:
        """Index one element scope; a scope the request named no kind string for is skipped."""
        if kind is None:
            return
        for ent in db.ents(kind):
            located = _locate(ent, scope, root)
            if located is None:
                continue
            key = _key_of(ent, scope, located[0])
            if key.token not in self.by_token:
                self.by_token[key.token] = _Located(ent, key, located[1])
            self.by_id[ent.id()] = key.token

    def resolve(self, key: _Key) -> _Located | None:
        """The entity a key names, or ``None`` when this database has no record of it."""
        return self.by_token.get(key.token)

    def referencing(self, ents: Iterable[Any], seen: set[int]) -> list[_Located]:
        """The project entities that reference any of ``ents``, seen through opaque ones.

        Only entities the gate can key are *reported*. Beyond those, exactly one class of
        entity may be walked *through*: an object — a local, a parameter, a member object, a
        macro, a variable — that is also project code, which is what :func:`_may_hop` decides
        from the positive list in :data:`OBJECT_KINDS`. In C++ an object is usually the only
        thing standing between a type and the code that uses it, so refusing the hop reports
        nothing for a class used as a local, as a parameter or as a member.

        The rule is a positive list and not "anything that is not keyable", because the
        entities that are neither keyable nor objects are the ones that *group* unrelated
        code. A namespace is the measured case: walking through it gives every class it holds
        the namespace's own user list, so two unrelated classes come back with identical
        answers. A library entity is refused for the same reason one door further out.

        The hop is free: the entity recovered beyond an object is reported at the depth of the
        reference that reached the object, so ``depth`` measures the same distance in C++ as
        in Python, where the same call produces a direct ``Callby``. ``seen`` bounds the walk
        to one visit per entity, so a cycle of objects terminates.

        Understand's walk order is not the repository's, so the answer is ordered by key
        token: the same request must produce the same document.
        """
        found: dict[str, _Located] = {}
        frontier = list(ents)
        while frontier:
            opaque: list[Any] = []
            for ent in frontier:
                self._referrers(ent, seen, found, opaque)
            frontier = opaque
        return [found[token] for token in sorted(found)]

    def _referrers(
        self, ent: Any, seen: set[int], found: dict[str, _Located], opaque: list[Any]
    ) -> None:
        """Sort one entity's referencers into those we can report and those we walk through."""
        for other in _reverse_ents(ent, seen):
            token = self.by_id.get(other.id())
            if token is not None:
                found.setdefault(token, self.by_token[token])
            elif _may_hop(other, self.root):
                opaque.append(other)


def _reverse_ents(ent: Any, seen: set[int]) -> list[Any]:
    """The entities whose references reach ``ent``, each returned once for the whole walk.

    Direction is Understand's own: ``Ref.isforward()`` is false for the second half of every
    pair (``use`` versus ``useby``), whatever the language and whatever the kind, which is why
    nothing here enumerates reference kinds. :data:`STRUCTURAL_REFS` takes out the reverse
    kinds that mean containment rather than use.
    """
    found: list[Any] = []
    for ref in ent.refs():
        if ref.isforward() or ref.kind().check(STRUCTURAL_REFS):
            continue
        other = ref.ent()
        if other.id() not in seen:
            seen.add(other.id())
            found.append(other)
    return found


def _expand(entities: _Entities, start: _Located, depth: int) -> dict[str, object]:
    """The entities that reference ``start`` transitively, level by level (req 9.5).

    ``depth`` is an inclusive cap: ``depth=2`` reports the entities one and two references
    out, and ``depth=0`` reports none. An entity is reported at the shallowest depth that
    reaches it and never again, and the entity the walk started from is never part of its own
    impact set: reference graphs are full of cycles (a routine and its caller call each other
    back in the sample project), and without that the totals a reviewer reads would count the
    same entity once per path. Two guards share that work because they answer different
    questions: ``seen`` bounds the *walk* by entity id, so a cycle terminates and an opaque
    entity that is never reported is still visited only once, while ``reported`` decides what
    reaches the answer — including the entity the walk started from, which a recursive routine
    genuinely does reference. Seeding ``seen`` with the start as well would be redundant:
    ``reported`` already refuses it, verified by mutation.
    """
    seen: set[int] = set()
    reported = {start.key.token}
    frontier: list[Any] = [start.ent]
    by_depth: dict[str, object] = {}
    total = 0
    for level in range(1, depth + 1):
        found = [
            entity
            for entity in entities.referencing(frontier, seen)
            if entity.key.token not in reported
        ]
        if not found:
            break
        reported.update(entity.key.token for entity in found)
        by_depth[str(level)] = [entity.document() for entity in found]
        total += len(found)
        frontier = [entity.ent for entity in found]
    return {"by_depth": by_depth, "total": total}


def _slug(text: str) -> str:
    """``text`` reduced to characters every filesystem accepts, never empty, never a path."""
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")
    return cleaned[:60] or "entity"


def _graph_filename(key: _Key, graph: str) -> str:
    """A deterministic, collision-free, filesystem-safe name for one exported graph (req 9.4).

    The scheme is ``<slug of the long name>-<12 hex of the key digest>-<slug of the graph>.svg``.
    The slug is what makes a directory listing readable, and it is lossy on purpose: a long
    name carries ``::``, ``<``, ``>`` and ``/`` (``ns::Widget<int>::run``), and it is truncated.
    Identity is therefore carried by the digest of :attr:`_Key.token`, which covers the scope,
    the path, the long name and the parameters — so two entities that share a name across two
    files, and two overloads inside one file, cannot be given the same file, and a rerun of the
    same request produces the same names. Two graphs of one entity differ by the graph slug.

    Neither part can contain a path separator, which is what confines the export to the output
    directory the operator chose.
    """
    digest = hashlib.sha256(key.token.encode("utf-8")).hexdigest()[:12]
    return f"{_slug(key.longname)}-{digest}-{_slug(graph)}{GRAPH_SUFFIX}"


class _Exporter:
    """Draws one graph per target into the output directory, warning instead of failing.

    Whether a graph exists depends on the entity and its language — verified live, a routine
    draws ``Butterfly``, ``Calls`` and ``Called By`` and refuses ``Depends On`` with
    ``UnderstandError('Unknown Graph')`` while writing no file, and files and classes draw
    ``Depends On``. One target the installed Understand will not render must not cost the
    reviewer every other graph, so it is recorded as a warning (design: "a failed ``draw`` for
    one target is recorded as a warning, not a failure").
    """

    def __init__(self, api: Any, entities: _Entities, out_dir: str) -> None:
        self.api = api
        self.entities = entities
        self.out_dir = out_dir
        self.files: list[dict[str, object]] = []
        self.warnings: list[str] = []

    def draw(self, key: _Key, graph: str) -> None:
        """Export one graph, recording either the file it wrote or why it wrote none."""
        located = self.entities.resolve(key)
        if located is None:
            self.warnings.append(f"{_names(key)} is not in this database: no {graph} graph")
            return
        path = os.path.join(self.out_dir, _graph_filename(key, graph))
        try:
            located.ent.draw(graph, path)
        except self.api.UnderstandError as exc:
            self.warnings.append(f"the {graph} graph of {_names(key)} could not be drawn: {exc}")
            return
        self.files.append({"key": key.document(), "graph": graph, "path": path})


def _names(key: _Key) -> str:
    """One entity named the way a warning has to name it: scope, long name and file."""
    return f"the {key.scope} {key.longname!r} of {key.path}"


def _require_kinds(request: Mapping[str, object]) -> dict[str, str]:
    """The scope -> kind string map both entity-resolving operations look entities up by.

    The worker may not import this project and must never invent a kind string, so the caller
    composes them from ``config.metric_names.SCOPE_KINDS`` exactly as the snapshot request
    does. An empty map would resolve nothing at all and is refused rather than answered with
    a warning per key.
    """
    kinds = _require_str_map(request, "kinds_by_scope")
    if not kinds:
        raise _RequestError(
            "BadRequest", "'kinds_by_scope' must give the kind string of at least one scope"
        )
    return kinds


def _key_from(document: Mapping[str, object], name: str) -> _Key:
    """Read one ``EntityKey`` document — the wire shape the snapshot answered with — or refuse."""
    fields: dict[str, str] = {}
    for label in ("scope", "path", "longname"):
        value = document.get(label)
        if not isinstance(value, str) or not value:
            raise _RequestError(
                "BadRequest",
                f"every entry of {name!r} needs a non-empty {label!r} string, got {document!r}",
            )
        fields[label] = value
    parameters = document.get("parameters")
    if parameters is not None and not isinstance(parameters, str):
        raise _RequestError(
            "BadRequest",
            f"the 'parameters' of an entry of {name!r} must be a string or null, got {document!r}",
        )
    return _Key(fields["scope"], fields["path"], fields["longname"], parameters)


def _require_keys(request: Mapping[str, object]) -> tuple[_Key, ...]:
    """The entities whose blast radius the caller wants (req 9.5)."""
    return tuple(_key_from(document, "keys") for document in _require_objects(request, "keys"))


def _require_targets(request: Mapping[str, object]) -> tuple[tuple[_Key, str], ...]:
    """The graphs to export: one entity key and one graph name per target (req 9.4)."""
    targets: list[tuple[_Key, str]] = []
    for document in _require_objects(request, "targets"):
        key = document.get("key")
        graph = document.get("graph")
        if not isinstance(key, dict):
            raise _RequestError(
                "BadRequest", f"every entry of 'targets' needs a 'key' object, got {document!r}"
            )
        if not isinstance(graph, str) or not graph:
            raise _RequestError(
                "BadRequest",
                f"every entry of 'targets' needs a non-empty 'graph' name, got {document!r}",
            )
        targets.append((_key_from(key, "targets"), graph))
    return tuple(targets)


def _make_directory(path: str) -> None:
    """Create the output directory the operator chose; a directory that cannot be is a refusal."""
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as exc:
        raise _RequestError(
            "BadRequest", f"the 'out_dir' {path!r} could not be created: {exc}"
        ) from exc


def _impact_of(
    entities: _Entities, key: _Key, depth: int, warnings: list[str]
) -> dict[str, object]:
    """One entity's impact set; an entity this database has no record of yields a warning.

    A routine the change deleted is asked about against the after database, and a routine it
    added against the before one, so a key that resolves to nothing is ordinary. It costs the
    reviewer one line of warning and an empty set, never the whole change summary.
    """
    located = entities.resolve(key)
    if located is None:
        warnings.append(f"{_names(key)} is not in this database: its impact set is empty")
        return {"by_depth": {}, "total": 0}
    return _expand(entities, located, depth)


def _op_impact(api: Any, request: Mapping[str, object]) -> dict[str, object]:
    """List what references each requested entity, transitively, up to a depth (req 9.5).

    The answer is keyed by :attr:`_Key.token`, which is what ``ChangeSummary.impact`` accepts
    as a key, and each value is an ``ImpactSet`` document: the entities found at each depth,
    plus the total across all of them.
    """
    db_path = _require_str(request, "db")
    root = _normalise_root(_require_str(request, "root"))
    kinds = _require_kinds(request)
    keys = _require_keys(request)
    depth = _require_depth(request)
    db = api.open(db_path)
    try:
        _check_root(db, kinds.get("file"), db_path, root)
        entities = _Entities(db, kinds, root)
        warnings: list[str] = []
        sets: dict[str, object] = {}
        for key in keys:
            sets[key.token] = _impact_of(entities, key, depth, warnings)
        return {"impact": sets, "warnings": warnings}
    finally:
        db.close()


def _op_graphs(api: Any, request: Mapping[str, object]) -> dict[str, object]:
    """Export one SVG per graph target into the operator's output directory (req 9.4).

    This is the one operation that writes files, and it writes them nowhere but inside
    ``out_dir``: the name of every file is built by :func:`_graph_filename`, which cannot
    produce a path separator. The directory is created before the database is opened, so a
    directory the operator cannot have is refused before the expensive work starts.
    """
    db_path = _require_str(request, "db")
    root = _normalise_root(_require_str(request, "root"))
    kinds = _require_kinds(request)
    targets = _require_targets(request)
    out_dir = _require_str(request, "out_dir")
    _make_directory(out_dir)
    db = api.open(db_path)
    try:
        _check_root(db, kinds.get("file"), db_path, root)
        exporter = _Exporter(api, _Entities(db, kinds, root), out_dir)
        for key, graph in targets:
            exporter.draw(key, graph)
        return {"graphs": exporter.files, "warnings": exporter.warnings}
    finally:
        db.close()


_HANDLERS: Final[dict[str, Callable[[Any, Mapping[str, object]], dict[str, object]]]] = {
    "ping": _op_ping,
    "catalogue": _op_catalogue,
    "archs": _op_archs,
    "snapshot": _op_snapshot,
    "impact": _op_impact,
    "graphs": _op_graphs,
}


# --- dispatch and the command line -------------------------------------------------


def dispatch(op: str, request: Mapping[str, object]) -> dict[str, object]:
    """Run one operation and return its result document or an error envelope.

    This is the single implementation both execution modes use: ``ApiRunner`` calls it
    directly when the host interpreter can import the API, and :func:`main` calls it after
    reading the request from standard input when the operation runs under ``upython``.
    Foreseeable failures come back as envelopes; anything else propagates, because a bug in
    the worker must not look like an answer.
    """
    handler = _HANDLERS.get(op)
    if handler is None:
        known = ", ".join(OPS)
        return _envelope("UnknownOperation", f"unknown operation {op!r}; known operations: {known}")
    try:
        api = _import_api()
    except _RequestError as exc:
        return _refused(exc)
    try:
        return handler(api, request)
    except _RequestError as exc:
        return _refused(exc)
    except api.UnderstandError as exc:
        return _envelope(_error_type(str(exc)), str(exc))


def _read_request(stream: TextIO | None) -> dict[str, object]:
    """Read the JSON request object from ``stream``; an interactive or empty one means ``{}``."""
    if stream is None or stream.isatty():
        return {}
    body = stream.read().strip()
    if not body:
        return {}
    try:
        request = json.loads(body)
    except ValueError as exc:
        raise _RequestError("BadRequest", f"the request body is not valid JSON: {exc}") from exc
    if not isinstance(request, dict):
        raise _RequestError(
            "BadRequest",
            f"the request body must be a JSON object, got {type(request).__name__}",
        )
    return request


def _answer(args: Sequence[str]) -> dict[str, object]:
    """The document the command line answers with, envelope included."""
    if not args:
        return _envelope("BadRequest", USAGE)
    try:
        request = _read_request(sys.stdin)
    except _RequestError as exc:
        return _refused(exc)
    return dispatch(args[0], request)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the operation named by the first argument and print one JSON document.

    The exit status is 0 whenever the answer is a document the caller can parse, envelopes
    included: the envelope *is* the answer, and a non-zero status is reserved for a crash
    the runner cannot parse.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    sys.stdout.write(json.dumps(_answer(args)) + "\n")
    return 0


def _leave(status: int) -> NoReturn:
    """Leave the process at once, without running the interpreter's finalization.

    Measured on the licensed machine (Understand 6.5.1204): after ``Ent.draw`` has rendered a
    dependency graph — ``Depends On`` or ``Depended On By``, on a file or on a class — the
    bundled interpreter aborts at shutdown with ``Fatal Python error:
    PyInterpreterState_Delete: remaining subinterpreters`` and dies of ``SIGABRT``. The
    answer is complete and correct on standard output by then, and both the SVG files and the
    JSON document are intact; only the exit status is destroyed, and a non-zero exit status is
    precisely how :class:`ApiRunner` is told that the worker itself is broken. Drawing a
    butterfly graph first happens to avoid it and drawing one afterwards does not, so there is
    no ordering a caller could rely on: Understand's graph engine leaves a subinterpreter
    behind and the process must not try to finalize.

    Flushing both streams by hand is therefore part of the contract, because ``os._exit``
    skips the flush the interpreter would otherwise do. This runs only when the file is
    executed as a script — that is, under ``upython`` — never when ``main`` is called in
    process.
    """
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(status)


if __name__ == "__main__":
    _leave(main())
