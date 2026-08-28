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

Operations implemented here (the snapshot, impact and graph operations follow in later
tasks): ``ping`` for the probes, ``catalogue`` for metric availability per language and
kind (requirement 5.5 and the configuration validation of 3.8), and ``archs`` for the
architecture nodes structural rules work on (requirements 6.7 and 6.8).

Database discipline: a database is opened only by the operations that need one and always
closed in a ``finally``, because the API crashes the process when entities outlive their
database, and only one database may be open per process.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Final, TextIO

OPS: Final[tuple[str, ...]] = ("ping", "catalogue", "archs")
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


def _member_files(node: Any) -> list[str]:
    """The project-relative paths of the file entities in a node and its descendants."""
    names = (ent.relname() for ent in node.ents(True))
    return sorted(name for name in names if name)


_HANDLERS: Final[dict[str, Callable[[Any, Mapping[str, object]], dict[str, object]]]] = {
    "ping": _op_ping,
    "catalogue": _op_catalogue,
    "archs": _op_archs,
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
