"""Execute worker operations, in whichever interpreter can load the API (req 1.2, 1.4).

Every call into the Understand Python API goes through :mod:`scitools_hook.understand.worker`,
and every call into the worker goes through here. The runner decides *where* an operation
runs and turns the worker's answer into either a document or a typed error; it knows nothing
about what any operation means.

**Two modes, one implementation.** ``worker.dispatch`` is called directly when the host
interpreter can import the API, and run as ``upython worker.py <op>`` — request JSON on
standard input, answer JSON on standard output — when it cannot. Both paths execute the same
functions, so the same request must produce the same document; the contract test of task 6.6
proves it on a real database.

**``graphs`` is the exception, and it is not negotiable.** Measured on the licensed machine
(Understand 6.5.1204, 2026-08-30): in-process ``import understand``, ``open()``, entity
iteration, ``metric()`` and ``close()`` all succeed, but ``Ent.draw`` dies with ``symbol
lookup error: <home>/bin/linux64/Perl/auto/Fcntl/Fcntl.so: undefined symbol:
Perl_xs_handshake`` and status 127 — drawing loads Understand's bundled Perl/Qt stack, which
resolves only under ``upython``. In-process that abort kills the *Gate*, not one operation, so
:data:`UPYTHON_ONLY_OPS` is routed to ``upython`` whatever the mode is. The alternative the
task considered — draw inside the in-process probe — was rejected: it would demote a mode
that is perfectly good for every other operation, and it would make the probe itself risky.

**Every foreseeable failure is data.** The worker answers ``{"error": {"type": …}}`` on
standard output and exits 0, so the exit status carries no meaning beyond "the worker itself
broke". :data:`_BUILDERS` maps each envelope type to the error the operator should see, and
the mapping — not an exception type — decides the exit code. Two entries were measured rather
than designed: ``ApiUnavailable`` means *this interpreter* has no ``understand`` module,
which is an installation problem and never a license one; and ``DBEmpty: database is empty``,
raised by ``understand.open`` on a half-built ``.und``, reaches the runner classified as the
generic ``UnderstandError``, so it is recognized by its message and answered with the hint
that fixes it.

**The in-process path is a guest in the host process.** It appends the API directory to
``sys.path`` — appends, so an Understand directory can never shadow the project's own
modules — and restores ``LC_NUMERIC`` afterwards, because ``worker._import_api`` forces it to
``C`` on every call (Understand writes ``0,5`` instead of ``0.5`` into SVG attributes under a
comma-decimal locale). In a subprocess that is invisible; here it would rewrite the
operator's environment for everything that runs after the Gate's first API call.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Final, Literal

from scitools_hook.errors import (
    AnalysisFailedError,
    ArchitectureNotFoundError,
    ConfigError,
    GateError,
    LicenseError,
    UnderstandNotFoundError,
)
from scitools_hook.models.progress import CommandLog
from scitools_hook.models.understand import UnderstandEnv
from scitools_hook.understand import worker
from scitools_hook.understand.locator import WORKER_PATH

Operation = Literal["ping", "catalogue", "archs", "snapshot", "impact", "graphs"]
"""The operations the worker answers; the same names, in the same order, as ``worker.OPS``."""

OPERATIONS: Final[tuple[Operation, ...]] = (
    "ping",
    "catalogue",
    "archs",
    "snapshot",
    "impact",
    "graphs",
)
"""Every operation this runner will run, pinned against the worker's own list."""

UPYTHON_ONLY_OPS: Final[frozenset[str]] = frozenset({"graphs"})
"""Operations that must never run in the host process, because ``Ent.draw`` aborts it."""

DEFAULT_TIMEOUT_S: Final = 600
"""Ceiling for one operation: a full snapshot of a large repository still fits."""

TIMEOUT_RC: Final = 124
"""Status recorded for an operation that had to be killed; GNU ``timeout``'s convention."""

MISSING_RC: Final = 127
"""Status recorded for an interpreter that never started; the shell's "not found" status."""

WORKER_RC: Final = 1
"""Status recorded for a worker that raised: what its own script entry point would exit with."""

IN_PROCESS: Final = "in-process"
"""First word of a logged in-process call, so a trace cannot be read as a subprocess."""

LOCALE_VAR: Final = "LC_NUMERIC"
"""The environment variable the worker forces to ``C`` and this module puts back."""

REBUILD_HINT: Final = "Rebuild the analysis database with `scitools-hook db rebuild`."
DEFECT_HINT: Final = "This is a defect in the Gate: the worker refused a request it built."
_MODE_HINT: Final = (
    "Set understand.api_mode (or --api-mode), or reinstall Understand so that its bundled "
    "upython is present."
)


class ApiRunner:
    """Runs one worker operation and answers with its document, or with a typed error.

    The instance holds no state beyond its installation, its log and its timeout, so one
    runner serves every operation and every database.
    """

    def __init__(self, env: UnderstandEnv, log: CommandLog, timeout_s: int = DEFAULT_TIMEOUT_S):
        self._env = env
        self._log = log
        self._timeout_s = timeout_s

    def run(self, op: Operation, request: Mapping[str, object]) -> dict[str, object]:
        """Run ``op`` with ``request`` and return the answer, raising on a refusal."""
        if self._runs_here(op):
            argv, answer = self._in_process(op, request)
        else:
            argv, answer = self._under_upython(op, request)
        _reject_refusal(answer, argv)
        return answer

    def _runs_here(self, op: str) -> bool:
        """Whether this operation may run in the host interpreter (never ``graphs``)."""
        return self._env.api_mode == "inprocess" and op not in UPYTHON_ONLY_OPS

    def _under_upython(
        self, op: str, request: Mapping[str, object]
    ) -> tuple[list[str], dict[str, object]]:
        """Run the worker as a subprocess of the bundled interpreter."""
        upython = self._env.upython
        if upython is None:
            raise _needs_upython(op, self._env)
        argv = [str(upython), str(WORKER_PATH), op]
        started = time.monotonic()
        try:
            done = subprocess.run(
                argv,
                input=json.dumps(request),
                capture_output=True,
                text=True,
                timeout=self._timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as expired:
            self._log.record(argv, time.monotonic() - started, TIMEOUT_RC)
            raise _timed_out(argv, expired, self._timeout_s) from expired
        except OSError as broken:
            self._log.record(argv, time.monotonic() - started, MISSING_RC)
            raise _unrunnable(argv, broken) from broken
        self._log.record(argv, time.monotonic() - started, done.returncode)
        return argv, _parse_answer(done, argv)

    def _in_process(
        self, op: str, request: Mapping[str, object]
    ) -> tuple[list[str], dict[str, object]]:
        """Call ``worker.dispatch`` in this interpreter, leaving no trace in the process."""
        argv = [IN_PROCESS, sys.executable, str(WORKER_PATH), op]
        _add_api_path(str(self._env.python_api_dir))
        started = time.monotonic()
        with _kept_locale():
            try:
                answer = worker.dispatch(op, request)
            except Exception as broken:
                self._log.record(argv, time.monotonic() - started, WORKER_RC)
                raise _broken_worker(argv, broken) from broken
        self._log.record(argv, time.monotonic() - started, 0)
        return argv, answer


# --- the host process ------------------------------------------------------------


def _add_api_path(api_dir: str) -> None:
    """Make the API importable here, without letting it shadow anything already importable."""
    if api_dir not in sys.path:
        sys.path.append(api_dir)


@contextmanager
def _kept_locale() -> Iterator[None]:
    """Restore ``LC_NUMERIC`` around a call that forces it to ``C`` (``worker._import_api``)."""
    previous = os.environ.get(LOCALE_VAR)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(LOCALE_VAR, None)
        else:
            os.environ[LOCALE_VAR] = previous


# --- reading the answer ----------------------------------------------------------


def _parse_answer(done: subprocess.CompletedProcess[str], argv: Sequence[str]) -> dict[str, object]:
    """The one JSON object the worker printed, or the reason it is not one.

    A non-zero status is never a refusal: the worker answers every foreseeable failure with
    an envelope and exit status 0, so anything else is the worker itself breaking.
    """
    if done.returncode != 0:
        raise AnalysisFailedError(
            f"{_named(argv)} exited with status {done.returncode}",
            command=argv,
            stderr=done.stderr.strip(),
            hint="Run the command by hand: the worker prints one JSON document and exits 0.",
        )
    try:
        answer = json.loads(done.stdout)
    except ValueError as exc:
        raise AnalysisFailedError(
            f"{_named(argv)} did not answer with JSON: {done.stdout.strip()[:200]!r}",
            command=argv,
            stderr=done.stderr.strip(),
        ) from exc
    if not isinstance(answer, dict):
        raise AnalysisFailedError(
            f"{_named(argv)} answered with a {type(answer).__name__}, not an object",
            command=argv,
            stderr=done.stderr.strip(),
        )
    return answer


def _named(argv: Sequence[str]) -> str:
    """One command named the way an error message has to name it."""
    return " ".join(str(part) for part in argv)


# --- refusals --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Refusal:
    """One error envelope: its type, its message, its per-type extras and the command."""

    kind: str
    message: str
    details: Mapping[str, object]
    argv: list[str]

    def strings(self, key: str) -> list[str]:
        """A list-of-strings extra, or nothing when the envelope carried none."""
        value = self.details.get(key)
        if not isinstance(value, list):
            return []
        return [str(item) for item in value]

    def failed(self, hint: str | None = None) -> AnalysisFailedError:
        """This refusal as the analysis failure it is, with an optional hint."""
        return AnalysisFailedError(self.message, command=self.argv, hint=hint)


def _reject_refusal(answer: Mapping[str, object], argv: Sequence[str]) -> None:
    """Raise the typed error an answer's envelope stands for; a document passes through."""
    error = answer.get("error")
    if not isinstance(error, Mapping):
        return
    raise _typed_error(
        _Refusal(
            kind=str(error.get("type") or "UnderstandError"),
            message=str(error.get("message") or "the Understand API refused the request"),
            details=error,
            argv=[str(part) for part in argv],
        )
    )


def _no_license(refusal: _Refusal) -> GateError:
    """Requirement 1.4: a license problem is its own exit code and quotes what was said."""
    return LicenseError(
        "SciTools Understand reports that no valid API license is available",
        und_output=refusal.message,
        hint="Check the license with `und license`, or set one with `und -setlicensecode`.",
    )


def _no_api(refusal: _Refusal) -> GateError:
    """An interpreter that cannot import the module: an installation problem, not a license."""
    return UnderstandNotFoundError(
        refusal.message, hint=_MODE_HINT, tried=[f"{_named(refusal.argv)}: {refusal.message}"]
    )


def _no_architecture(refusal: _Refusal) -> GateError:
    """Requirement 6.8: name the architectures that do exist, and stop as a config error."""
    available = refusal.strings("available")
    return ArchitectureNotFoundError(
        refusal.message,
        available=available,
        key="structure.architecture",
        hint=f"Architectures in this database: {', '.join(available) or '(none)'}.",
    )


def _wrong_root(refusal: _Refusal) -> GateError:
    """A root that names no file of the database: the caller pointed at the wrong directory.

    Reported as a configuration error rather than an analysis failure because the database is
    fine — and because the alternative reading sends the operator to rebuild it. The files the
    database really holds go into the hint: that is what makes the mistake obvious. No dotted
    key is attached, because the analysis root is not a setting an operator writes — it is the
    cache shadow the database was built from.
    """
    found = refusal.strings("found")
    return ConfigError(
        refusal.message,
        hint=(
            f"Files this database holds: {', '.join(found) or '(none)'}. "
            "Rebuild the analysis database if the cache no longer matches the repository."
        ),
    )


def _empty_database(refusal: _Refusal) -> GateError:
    """A half-built ``.und``: the analysis has to be run again, nothing else will help."""
    return refusal.failed(hint=REBUILD_HINT)


_BUILDERS: Final[dict[str, Callable[[_Refusal], GateError]]] = {
    "NoApiLicense": _no_license,
    "ApiUnavailable": _no_api,
    "ArchitectureNotFound": _no_architecture,
    "AnalysisRootMismatch": _wrong_root,
    "DBEmpty": _empty_database,
}
"""Envelope type -> the error the operator should see. Everything else is an analysis failure."""

_HINTS: Final[dict[str, str]] = {
    "DBCorrupt": REBUILD_HINT,
    "DBOldVersion": REBUILD_HINT,
    "DBUnknownVersion": REBUILD_HINT,
    "DBAlreadyOpen": "Only one database may be open per process; this is a defect in the Gate.",
    "DBUnableOpen": "Check that the database exists and that this user may read it.",
    "BadRequest": DEFECT_HINT,
    "UnknownOperation": DEFECT_HINT,
}
"""Extra advice per envelope type, for the ones that map to a plain analysis failure."""

_EMPTY_DATABASE_TEXT: Final = "dbempty"
"""``understand.open`` raises ``DBEmpty: database is empty``; the worker does not classify it."""


def _typed_error(refusal: _Refusal) -> GateError:
    """The error one refusal becomes, by its type and — for ``DBEmpty`` — by its message."""
    kind = _effective_kind(refusal)
    builder = _BUILDERS.get(kind)
    if builder is not None:
        return builder(refusal)
    return refusal.failed(hint=_HINTS.get(kind))


def _effective_kind(refusal: _Refusal) -> str:
    """The envelope type, recovering the one the worker reports only in its message.

    ``understand.open`` raises ``DBEmpty: database is empty`` for a half-built database and
    the worker's classifier does not know the name, so it arrives as the catch-all
    ``UnderstandError``. Only that catch-all is reinterpreted: a type the worker did name is
    the worker's answer and is never second-guessed by reading its prose.
    """
    if refusal.kind == "UnderstandError" and _EMPTY_DATABASE_TEXT in refusal.message.lower():
        return "DBEmpty"
    return refusal.kind


# --- failures that are not answers -----------------------------------------------


def _needs_upython(op: str, env: UnderstandEnv) -> AnalysisFailedError:
    """The error an operation that needs the bundled interpreter raises when there is none."""
    reason = (
        f"the {op!r} operation must run under upython, and {env.home} holds none"
        if op in UPYTHON_ONLY_OPS
        else f"the upython mode was selected, and {env.home} holds no upython"
    )
    return AnalysisFailedError(reason, command=[str(WORKER_PATH), op], hint=_MODE_HINT)


def _timed_out(
    argv: Sequence[str], expired: subprocess.TimeoutExpired, limit: int
) -> AnalysisFailedError:
    """The error a killed operation becomes; ``TimeoutExpired`` is not an ``OSError``."""
    captured = expired.stderr
    text = captured.decode(errors="replace") if isinstance(captured, bytes) else (captured or "")
    return AnalysisFailedError(
        f"{_named(argv)} timed out after {limit}s",
        command=argv,
        stderr=text.strip(),
        hint="Raise the timeout, or rebuild the database if the analysis is stuck.",
    )


def _unrunnable(argv: Sequence[str], broken: OSError) -> AnalysisFailedError:
    """The error an interpreter that never started becomes."""
    return AnalysisFailedError(
        f"{argv[0]} could not be run: {broken}",
        command=argv,
        stderr=str(broken),
        hint="Check the Understand installation directory with `scitools-hook doctor`.",
    )


def _broken_worker(argv: Sequence[str], broken: Exception) -> AnalysisFailedError:
    """The error an in-process worker that raised becomes.

    The subprocess mode turns a traceback into an analysis failure by way of a non-zero exit
    status; catching here is what keeps the two modes from disagreeing about what a broken
    worker is, and it keeps an exception from Understand's own C extension out of handlers
    that have no idea what it means.
    """
    return AnalysisFailedError(
        f"{_named(argv)} failed: {broken}",
        command=argv,
        stderr=f"{type(broken).__name__}: {broken}",
        hint="Run the same operation under upython to see whether the mode is at fault.",
    )
