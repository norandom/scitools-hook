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

Operations implemented here (the impact and graph operations follow in a later task):
``ping`` for the probes, ``catalogue`` for metric availability per language and kind
(requirement 5.5 and the configuration validation of 3.8), ``archs`` for the architecture
nodes structural rules work on (requirements 6.7 and 6.8), and ``snapshot``, which turns one
database into the ``ProjectSnapshot`` document every rule is evaluated against (requirements
3.5, 5.5, 6.7, 9.7).

Database discipline: a database is opened only by the operations that need one and always
closed in a ``finally``, because the API crashes the process when entities outlive their
database, and only one database may be open per process.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, TextIO

OPS: Final[tuple[str, ...]] = ("ping", "catalogue", "archs", "snapshot")
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
    """Import ``understand`` on demand, refusing the request when the interpreter has none."""
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
    """Read the architecture depth, defaulting to the immediate children of the root."""
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
        """The reversible string form ``EntityKey.token`` produces; class edges use it."""
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
    parse_errors: list[object]


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


def _require_objects(request: Mapping[str, object], key: str) -> list[object]:
    """Read an optional list of JSON objects, the shape ``ParseError`` validates from."""
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
        parse_errors=_require_objects(request, "parse_errors"),
    )


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
    ref = ent.ref(CONTAINER_REFS)
    if ref is None:
        return None
    container = ref.file()
    if container is None:
        return None
    path = _project_path(container, root)
    return None if path is None else (path, ref.line())


def _key_of(ent: Any, scope: str, path: str) -> _Key:
    """Build the identity of one entity; a file's long name is its root-relative path."""
    longname = path if scope == "file" else str(ent.longname())
    return _Key(scope=scope, path=path, longname=longname, parameters=ent.parameters())


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
        self.records: list[dict[str, object]] = []
        self.collected: dict[str, dict[str, list[float]]] = {}
        self.unavailable: dict[str, set[str]] = {}

    def build(self) -> dict[str, object]:
        """Extract everything the snapshot holds and answer with its wire form."""
        self._check_root()
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
        }
        document.update(self._edges())
        return document

    # --- architecture (req 6.7, 6.8, 9.7) -----------------------------------------

    def _check_root(self) -> None:
        """Refuse an analysis root that names no file of this database.

        A root that is not the directory ``und add`` was pointed at resolves nothing: every
        file falls back to ``relname``, every key carries a path the caller never asked for,
        the requested files match none of them, and the answer is a valid, empty, entirely
        green snapshot that gates nothing — the same silent failure a missing root would
        cause, which is why the root is required in the first place. It is a caller error and
        it is reported as one, with the long names that were actually found.
        """
        kind = self.plan.kinds.get("file")
        if kind is None:
            return
        found = [ent for ent in self.db.ents(kind) if not ent.library()]
        if not found or any(_relative_to(str(ent.longname()), self.plan.root) for ent in found):
            return
        raise _RequestError(
            "AnalysisRootMismatch",
            f"no file of {self.plan.db} is under the analysis root {self.plan.root!r}",
            found=sorted(str(ent.longname()) for ent in found)[:3],
        )

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
        """Keep the entities the dependency edges are built from, files and classes alike."""
        if key.scope == "file":
            self.file_ents[key.path] = ent
        elif key.scope == "class":
            self.class_ents[key.token] = (ent, key.path)

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
        """The three edge lists, or three empty ones when the caller wants no edges."""
        if not self.plan.include_edges:
            return {"file_edges": [], "class_edges": [], "arch_edges": []}
        return {
            "file_edges": self._file_edges(),
            "class_edges": self._class_edges(),
            "arch_edges": self._arch_edges(),
        }

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
        """File dependencies with reference counts, bounded by the affected neighbourhood."""
        scope = self._neighbourhood(self.file_ents, self._file_of)
        return self._collect_edges(scope, self._file_of, crossing=True)

    def _class_edges(self) -> list[dict[str, object]]:
        """Class dependencies; the endpoints are ``EntityKey.token`` values, as ``fan`` expects."""
        scope = self._neighbourhood(self.class_ents, self._class_of)
        return self._collect_edges(scope, self._class_of, crossing=False)

    def _collect_edges(
        self, scope: set[str], resolve: Callable[[Any], str | None], crossing: bool
    ) -> list[dict[str, object]]:
        """Every dependency inside ``scope``, merged by endpoint pair and counted."""
        counts: dict[tuple[str, str], int] = {}
        for src in sorted(scope):
            for other, refs in self._entity(src).depends().items():
                dst = resolve(other)
                if dst is not None and dst != src and dst in scope:
                    counts[(src, dst)] = counts.get((src, dst), 0) + len(refs)
        return [self._edge(pair, count, crossing) for pair, count in sorted(counts.items())]

    def _edge(self, pair: tuple[str, str], refs: int, crossing: bool) -> dict[str, object]:
        """One dependency edge; a class edge names entities, so it has no architecture to cross."""
        src, dst = pair
        crosses = self._crosses(src, dst) if crossing else self._crosses_classes(src, dst)
        return {"src": src, "dst": dst, "refs": refs, "crosses_arch": crosses}

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


_HANDLERS: Final[dict[str, Callable[[Any, Mapping[str, object]], dict[str, object]]]] = {
    "ping": _op_ping,
    "catalogue": _op_catalogue,
    "archs": _op_archs,
    "snapshot": _op_snapshot,
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


if __name__ == "__main__":
    sys.exit(main())
