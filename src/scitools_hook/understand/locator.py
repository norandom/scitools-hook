"""Find the installed Understand, and decide how its Python API will be reached.

Requirement 1.1 fixes the order in which an installation directory is looked for: the
command-line option, the ``SCITOOLS_HOME`` environment variable, the configuration file,
the ``und`` executable on the search path, and finally a per-OS list of well-known
directories. :func:`candidates` builds that list, recording *which* step produced each
path, and :func:`discover` answers with the first one whose layout is an installation —
an executable ``und`` under ``bin/<platform>/``. When none is, requirement 1.3 obliges the
Gate to say every location it tried and which option or variable to set, so the
:class:`~scitools_hook.errors.UnderstandNotFoundError` carries the whole list.

Requirement 1.2 then asks whether that installation is *usable*, which no directory listing
can answer. :func:`verify` asks three injected :class:`Probes` instead — the ``und``
version, an in-process import of the API, and a ``upython`` ping — and decides ``api_mode``
from what they say. Two properties of this split matter:

* **Nothing in :func:`verify` touches the filesystem or starts a process.** Everything it
  needs to know arrives through the probes, so the mode decision is testable with stubs and
  the runner (task 6.6) wires the real implementations: ``UndCli.version``, an in-process
  import performed *in a child process*, and ``upython worker.py ping``.
* **``auto`` tries ``upython`` first.** The in-process import itself is *not* broken: with a
  license active, ``import understand``, ``open()``, entity iteration, ``metric()`` and
  ``close()`` all succeed (rc 0, measured on both ``/usr/bin/python3.12`` and the project
  venv's CPython 3.14.4). ``Ent.draw`` is the exception — in-process it aborts with
  ``Perl_xs_handshake`` (rc 127) because drawing loads Understand's bundled Perl/Qt stack,
  and the same draw succeeds under ``upython``. Since the shipped ``graphs`` operation needs
  draw, ``upython`` is the preference; ``research.md`` records the corrected measurement.
  In-process is
  therefore the fallback, not the preference, and forcing a mode (``understand.api_mode``,
  ``--api-mode``) runs that mode's probe only — the in-process probe is the dangerous one,
  and an operator who forced ``upython`` must never have it run behind their back.

Discovery does touch the filesystem, because that is its whole job. It reads no ambient
state while doing so: the environment mapping it is given is the only environment it knows,
including for ``PATH`` and for expanding ``~``, which keeps both this module and its tests
independent of the machine they run on.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, Protocol

from scitools_hook.config.models import ApiMode
from scitools_hook.errors import UnderstandNotFoundError
from scitools_hook.models.understand import UnderstandEnv

HOME_VAR: Final = "SCITOOLS_HOME"
"""The environment variable requirement 1.1 places second in the precedence list."""

PLATFORM_BIN: Final[dict[str, str]] = {"linux": "linux64", "darwin": "macosx", "win32": "pc-win64"}
"""The ``bin`` subdirectory Understand installs its executables into, per ``sys.platform``."""

FALLBACK_BIN: Final = "linux64"
"""Used for a platform Understand does not ship for, so discovery answers instead of raising."""

WINDOWS_BIN: Final = "pc-win64"
"""The one layout whose executables carry ``.exe`` and have no execute bit to check."""

WELL_KNOWN: Final[dict[str, tuple[str, ...]]] = {
    "linux": ("~/scitools", "/opt/scitools", "/usr/local/scitools"),
    "darwin": ("/Applications/Understand.app/Contents/MacOS",),
    "win32": ("C:\\Program Files\\SciTools",),
}
"""Requirement 1.1's fixed list, the last resort of the precedence order."""

WORKER_PATH: Final = Path(__file__).with_name("worker.py")
"""The script ``upython`` is asked to run for the ping; computed without touching the disk."""

SET_HOME_HINT: Final = (
    f"Set the installation directory with --scitools-home, the {HOME_VAR} environment "
    "variable, or understand.home in the configuration file."
)
"""Requirement 1.3: the message must name the option or variable to set."""

SET_MODE_HINT: Final = (
    "Set understand.api_mode (or --api-mode) to the mode that works on this machine, or "
    "reinstall Understand so that its bundled upython is present."
)
"""The other half of requirement 1.3: an installation found but unusable is still actionable."""


class Probes(Protocol):
    """The three questions only a running Understand can answer (requirement 1.2).

    Injected rather than imported so that this module depends on neither ``UndCli`` nor
    ``ApiRunner``. Both API probes answer with the API version, or ``None`` for "this mode
    does not work here", logging the detail; :func:`verify` turns a ``None`` into a reason
    naming the path that failed.
    """

    def und_version(self, und: Path) -> str:
        """The version ``und`` reports; raises if ``und`` itself cannot be run."""
        ...

    def inprocess_import(self, api_dir: Path, bin_dir: Path) -> str | None:
        """Import the API in a child process; the version, or ``None`` when it failed."""
        ...

    def upython_ping(self, upython: Path, worker: Path) -> str | None:
        """Run ``upython worker.py ping``; the version, or ``None`` when it failed."""
        ...


@dataclass(frozen=True)
class InstallLayout:
    """The paths an Understand installation is recognized by (requirement 1.1).

    ``upython`` is ``None`` when the bundled interpreter is absent, which is a fact about the
    installation rather than a defect: :func:`verify` may still reach the API in-process.
    ``python_api_dir`` is named whether or not it exists — the probes decide usability.
    """

    home: Path
    und: Path
    upython: Path | None
    python_api_dir: Path


def platform_bin(platform: str = sys.platform) -> str:
    """The ``bin`` subdirectory of ``platform``; the Linux name for anything unknown."""
    for prefix, name in PLATFORM_BIN.items():
        if platform.startswith(prefix):
            return name
    return FALLBACK_BIN


def layout(home: Path, platform: str = sys.platform) -> InstallLayout | None:
    """The layout under ``home``, or ``None`` when it holds no runnable ``und``."""
    bin_dir = home / "bin" / platform_bin(platform)
    und = bin_dir / _executable_name("und", platform)
    if not _is_runnable(und, platform):
        return None
    upython = bin_dir / _executable_name("upython", platform)
    return InstallLayout(
        home=home,
        und=und,
        upython=upython if _is_runnable(upython, platform) else None,
        python_api_dir=bin_dir / "Python",
    )


def well_known_homes(env: Mapping[str, str], platform: str = sys.platform) -> list[Path]:
    """The well-known installation directories of ``platform``, with ``~`` expanded.

    An unrecognized platform has none: Understand ships no build for it, and guessing a
    directory would put a location in the "tried" list that never could have worked.
    """
    for prefix, entries in WELL_KNOWN.items():
        if platform.startswith(prefix):
            return [_expand_user(Path(entry), env) for entry in entries]
    return []


def candidates(
    env: Mapping[str, str],
    cli_home: Path | None,
    settings_home: Path | None,
    platform: str = sys.platform,
) -> list[tuple[str, Path]]:
    """The installation directories to try, in the order requirement 1.1 fixes.

    Each pair is ``(source, path)``, where ``source`` names the precedence step — ``cli``,
    ``env:SCITOOLS_HOME``, ``config``, ``path`` or ``wellknown:<path>`` — so a resolved
    environment can say where it came from and a failure can say what was tried. A directory
    named twice appears once, attributed to the strongest source that named it.
    """
    named: list[tuple[str, Path | None]] = [
        ("cli", cli_home),
        (f"env:{HOME_VAR}", _env_home(env)),
        ("config", settings_home),
        ("path", _und_on_path(env, platform)),
    ]
    found = [(source, _expand_user(path, env)) for source, path in named if path is not None]
    found += [(f"wellknown:{path}", path) for path in well_known_homes(env, platform)]
    return _deduplicated(found)


def discover(
    env: Mapping[str, str],
    cli_home: Path | None,
    settings_home: Path | None,
    platform: str = sys.platform,
) -> UnderstandEnv:
    """The first candidate that is an installation, recorded with the source that found it.

    The result is **unverified**: ``version`` is empty and ``api_mode`` is only what the
    layout suggests, because deciding either means running something. Pass it through
    :func:`verify` — as :meth:`Locator.resolve` does — before using it for anything.
    """
    tried = candidates(env, cli_home, settings_home, platform)
    for source, home in tried:
        found = layout(home, platform)
        if found is not None:
            return _unverified_env(found, source)
    raise UnderstandNotFoundError(
        f"no SciTools Understand installation was found in {len(tried)} location(s)",
        hint=SET_HOME_HINT,
        tried=[f"{source}: {path}" for source, path in tried],
    )


def verify(env: UnderstandEnv, preferred: ApiMode, probes: Probes) -> UnderstandEnv:
    """Confirm the installation runs and decide ``api_mode`` from the probes (req 1.2).

    ``und_version`` is asked first and its failures propagate: a broken or unlicensed ``und``
    is the ``und`` wrapper's diagnosis to report, not "no installation here". The API probes
    are the opposite — their job is to fail — so a probe that answers ``None`` or cannot run
    at all becomes a reason string, and only when no mode is left does this raise, carrying
    every reason it collected (requirement 1.3).
    """
    version = probes.und_version(env.und)
    mode, reasons = _choose_api_mode(env, preferred, probes)
    if mode is None:
        raise UnderstandNotFoundError(
            f"the Understand Python API at {env.home} could not be loaded in any mode",
            hint=SET_MODE_HINT,
            tried=reasons,
        )
    return UnderstandEnv(
        home=env.home,
        und=env.und,
        upython=env.upython,
        python_api_dir=env.python_api_dir,
        version=version,
        source=env.source,
        api_mode=mode,
    )


@dataclass(frozen=True)
class Locator:
    """Discovery and verification as the runner uses them: one call, one verified answer.

    ``preferred`` is ``understand.api_mode`` from the effective configuration, and
    ``platform`` exists so the per-OS tables can be exercised without the OS.
    """

    probes: Probes
    preferred: ApiMode = "auto"
    platform: str = sys.platform

    def resolve(
        self, cli_home: Path | None, env: Mapping[str, str], settings_home: Path | None
    ) -> UnderstandEnv:
        """The verified installation, or ``UnderstandNotFoundError`` saying what was tried."""
        found = discover(env, cli_home, settings_home, self.platform)
        return verify(found, self.preferred, self.probes)


# --- layout helpers -------------------------------------------------------------


def _executable_name(name: str, platform: str) -> str:
    """``und`` or ``und.exe``, depending on the platform the layout belongs to."""
    return f"{name}.exe" if platform_bin(platform) == WINDOWS_BIN else name


def _is_runnable(path: Path, platform: str) -> bool:
    """True for a file that can actually be executed; Windows has no execute bit to read."""
    if not path.is_file():
        return False
    return platform_bin(platform) == WINDOWS_BIN or os.access(path, os.X_OK)


def _unverified_env(found: InstallLayout, source: str) -> UnderstandEnv:
    """Turn a layout into the environment :func:`verify` completes."""
    return UnderstandEnv(
        home=found.home,
        und=found.und,
        upython=found.upython,
        python_api_dir=found.python_api_dir,
        version="",
        source=source,
        api_mode="upython" if found.upython is not None else "inprocess",
    )


# --- candidate helpers ----------------------------------------------------------


def _env_home(env: Mapping[str, str]) -> Path | None:
    """``SCITOOLS_HOME`` as a path; blank means unset, not the current directory."""
    value = env.get(HOME_VAR, "").strip()
    return Path(value) if value else None


def _expand_user(path: Path, env: Mapping[str, str]) -> Path:
    """Expand a leading ``~`` from the given environment, never from the real one."""
    parts = path.parts
    if not parts or parts[0] != "~":
        return path
    home = env.get("HOME") or env.get("USERPROFILE")
    return (Path(home) if home else Path.home()).joinpath(*parts[1:])


def _und_on_path(env: Mapping[str, str], platform: str) -> Path | None:
    """The installation root of the ``und`` on the environment's ``PATH``, if there is one."""
    search = env.get("PATH", "")
    if not search:
        return None
    found = shutil.which(_executable_name("und", platform), path=search)
    return None if found is None else _home_of(Path(found), platform)


def _home_of(und: Path, platform: str) -> Path:
    """The installation root holding ``und``, following the symlink that usually puts it on PATH.

    The nearest ancestor that is an installation wins, so ``<home>/bin/<platform>/und`` gives
    ``<home>``. An ``und`` in no recognizable layout still yields a location — its parent's
    parent — because requirement 1.3 promises to report every place that was tried.
    """
    real = und.resolve()
    for ancestor in real.parents:
        if layout(ancestor, platform) is not None:
            return ancestor
    return real.parent.parent


def _deduplicated(found: Sequence[tuple[str, Path]]) -> list[tuple[str, Path]]:
    """Keep the first pair naming each path, so the strongest source is the one recorded."""
    seen: set[Path] = set()
    unique: list[tuple[str, Path]] = []
    for source, path in found:
        if path not in seen:
            seen.add(path)
            unique.append((source, path))
    return unique


# --- mode decision --------------------------------------------------------------


def _choose_api_mode(
    env: UnderstandEnv, preferred: ApiMode, probes: Probes
) -> tuple[Literal["inprocess", "upython"] | None, list[str]]:
    """The usable mode and the reasons the rejected ones gave, in the order they were tried.

    ``auto`` asks ``upython`` first and falls back; a forced mode asks only its own probe, so
    the reasons list holds exactly what the operator's choice produced.
    """
    reasons: list[str] = []
    if preferred in ("auto", "upython") and _upython_works(env, probes, reasons):
        return "upython", reasons
    if preferred in ("auto", "inprocess") and _inprocess_works(env, probes, reasons):
        return "inprocess", reasons
    return None, reasons


def _upython_works(env: UnderstandEnv, probes: Probes, reasons: list[str]) -> bool:
    """Whether the bundled interpreter answered the worker ping; otherwise record why not."""
    upython = env.upython
    if upython is None:
        reasons.append(f"upython: {env.python_api_dir.parent} holds no upython executable")
        return False
    answered, detail = _ask(lambda: probes.upython_ping(upython, WORKER_PATH))
    if answered is None:
        reasons.append(f"upython: {upython} did not answer the worker ping{detail}")
        return False
    return True


def _inprocess_works(env: UnderstandEnv, probes: Probes, reasons: list[str]) -> bool:
    """Whether this interpreter can import the API; otherwise record why not."""
    answered, detail = _ask(
        lambda: probes.inprocess_import(env.python_api_dir, env.python_api_dir.parent)
    )
    if answered is None:
        reasons.append(
            f"in-process: this interpreter could not import the Understand API from "
            f"{env.python_api_dir}{detail}"
        )
        return False
    return True


def _ask(probe: Callable[[], str | None]) -> tuple[str | None, str]:
    """Run one probe: its answer, plus the detail of an ``OSError`` it could not survive.

    A probe that cannot start — no such file, not executable — has answered no as clearly as
    one that returned ``None``, and the operating system's own words are the best reason
    available. Only ``OSError`` is caught: anything else is a defect in the probe itself and
    must reach the caller. ``subprocess.TimeoutExpired`` is deliberately *not* caught — it is
    not an ``OSError``, and a hung command is a fault to report, not a mode that "does not
    work here". Pinned by ``test_a_probe_failing_in_any_other_way_reaches_the_caller``.
    """
    try:
        return probe(), ""
    except OSError as exc:
        return None, f": {exc}"
