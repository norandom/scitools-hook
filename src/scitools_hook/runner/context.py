"""Everything one run needs, assembled once: settings, repository, Understand, adapters.

A pipeline should never have to ask *where* its inputs came from. :func:`build_context`
answers that question once -- which configuration files were merged and in what order, which
repository the working directory belongs to (if any), which Understand installation was found
and how its API will be reached, which command log the adapters report through -- and hands
back a frozen :class:`RunContext` every later stage reads.

Four decisions here are load-bearing:

* **The repository is optional.** Requirement 12.5 lets ``doctor`` and ``config`` run outside
  one, so a missing repository is a ``None``, not a failure; the pipelines that genuinely
  need git call :meth:`RunContext.require_repo` and get the typed refusal with its own exit
  code. Note the asymmetry: *git itself* failing to run is not the same thing and is not
  swallowed.
* **The availability report is produced here and carried whole.** ``validate_settings``
  answers with the thresholds that survived the metric catalogue, the shipped defaults this
  repository's languages cannot compute, and those metrics keyed *language -> metrics*
  (task 2.4). Reducing that to a settings object would let a threshold go unevaluated with
  nobody told, so :attr:`RunContext.availability` is the report itself: the check pipeline
  evaluates ``availability.thresholds`` and reports ``availability.unavailable`` in both
  ``RunResult.unavailable_metrics`` and ``evaluate_thresholds(catalogue_unavailable=...)``.
* **The installation is verified with real probes.** ``locator.verify`` decides the API mode
  from three injected probes and touches nothing itself; :class:`RealProbes` is the
  implementation that runs them. The in-process probe runs the *host* interpreter in a
  **child** process, because the mode it certifies loads a foreign C extension into whatever
  interpreter asks -- probing it in this one would risk the Gate. It also adds nothing to
  that child but ``PYTHONPATH``: ``ApiRunner`` reaches in-process mode by appending the API
  directory to ``sys.path`` and nothing else, and a probe that helped the import along with a
  library path would certify a mode the runner cannot reproduce.
* **The clock is read once.** :attr:`RunContext.started_at` is the run's timestamp, so
  ``baseline.capture`` and ``RunResult`` are stamped from the same moment and no production
  path reads the clock inside ``analysis``.

The documented test seam is ``SCITOOLS_HOOK_FAKE_UNDERSTAND=<dir>``: when it names a
directory, no installation is searched for and no process is started -- the fixture-backed
adapters of :mod:`scitools_hook.understand.fake` answer from files in that directory instead.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from scitools_hook.config.loader import attach_source, load_settings
from scitools_hook.config.models import Provenance, Settings
from scitools_hook.config.validate import AvailabilityReport, validate_settings
from scitools_hook.errors import ConfigError, NotAGitRepositoryError
from scitools_hook.exit_codes import MISSING_RC, TIMEOUT_RC
from scitools_hook.git.repo import GitRepo
from scitools_hook.models.cache import CachePaths
from scitools_hook.models.progress import CommandLog, NullCommandLog, NullProgress, Progress
from scitools_hook.models.understand import UnderstandEnv
from scitools_hook.runner.baseline_store import BaselineStore, baseline_path
from scitools_hook.understand.api_runner import ApiRunner
from scitools_hook.understand.catalogue import MetricCatalogue, as_availability
from scitools_hook.understand.fake import (
    FAKE_VAR,
    FixtureApiRunner,
    FixtureUndCli,
    fake_directory,
    fixture_env,
    fixture_problem,
)
from scitools_hook.understand.locator import WORKER_PATH, discover, verify
from scitools_hook.understand.und_cli import UndCli

PROBE_TIMEOUT_S: Final = 60
"""Ceiling for one probe: a ping that takes a minute is a broken installation, not a slow one."""

PING_OP: Final = "ping"
"""The worker operation both API probes run; it opens no database and needs no request."""

PYTHONPATH_VAR: Final = "PYTHONPATH"
"""How the in-process probe puts the API directory on the child interpreter's path."""

NO_REPOSITORY_HINT: Final = (
    "Run this command from inside a git working tree; `doctor` and `config` do not need one."
)
"""Requirement 12.5's other half: say which commands still work where this one does not."""


@dataclass(frozen=True, slots=True)
class ContextOptions:
    """What the command line knows before anything has been loaded or located.

    ``env`` is the process environment as a mapping rather than an ambient read: every
    location decision -- ``SCITOOLS_HOME``, ``XDG_CONFIG_HOME``, ``HOME``, ``USERPROFILE``,
    ``PATH`` and the test seam -- is taken from it, so a test controls them all by passing
    them here.

    That sentence was not true when it was first written, and the correction is worth keeping
    rather than quietly deleting. ``HOME`` reached two consumers by different routes:
    ``locator._expand_user`` read it from this mapping, but ``config.loader`` resolved the
    user configuration through ``Path.home()``, which reads the **ambient** ``os.environ``
    whatever mapping it is handed. Measured: ``user_config_path({"HOME": tmp})`` answered the
    developer's own ``~/.config/scitools-hook/config.toml``, and a reviewer's probe read a
    real ``MaxNesting = 99`` out of it, attributed to ``user:<ambient home>``. Tests looked
    isolated only because they also set ``XDG_CONFIG_HOME``, which short-circuits that path.
    ``config.loader.user_config_path`` now reads ``HOME``/``USERPROFILE`` from the mapping and
    falls back to ``Path.home()`` only when it names neither. Anything reached through
    ``Path.cwd()`` or through ``git``'s own inherited environment is still ambient; this
    promise covers the variables named above and nothing wider.

    One consumer is still not covered and saying so is the point of this paragraph: on macOS
    and Windows the *cache root* is built by ``platformdirs``, which expands ``~`` from the
    ambient environment. :func:`cache_dir` redirects it from ``XDG_CACHE_HOME`` everywhere and
    from ``HOME`` on Linux; elsewhere it declines to guess at the platform's layout, so a
    caller-supplied ``HOME`` does not reach it there.

    ``scitools_home`` is kept
    apart from ``cli_overrides`` on purpose: requirement 1.1 ranks the command-line option
    above the configuration file, and folding it into the settings would leave the locator
    reporting the installation's source as ``config``.
    """

    cwd: Path
    env: Mapping[str, str]
    cli_overrides: Mapping[str, object] = field(default_factory=dict)
    scitools_home: Path | None = None
    log: CommandLog = field(default_factory=NullCommandLog)
    progress: Progress = field(default_factory=NullProgress)


@dataclass(frozen=True, slots=True)
class RunContext:
    """One run's inputs, frozen: a later stage reads them and cannot rewrite them."""

    settings: Settings
    provenance: Provenance
    availability: AvailabilityReport
    understand: UnderstandEnv
    und: UndCli
    api: ApiRunner
    repo: GitRepo | None
    env: Mapping[str, str]
    log: CommandLog
    progress: Progress
    started_at: str

    @property
    def repo_root(self) -> Path | None:
        """The repository root, or ``None`` outside one.

        Worth passing explicitly wherever it is accepted: ``analysis.codecheck`` and the
        renderers take ``repo_root=None`` by default and then emit absolute paths with no
        type error to warn anyone.
        """
        return None if self.repo is None else self.repo.root

    @property
    def cache(self) -> CachePaths | None:
        """Where this repository's shadows and databases live (req 2.1, 2.2, 2.8).

        Derived rather than stored, so it cannot disagree with ``understand.db_location``
        after the fact. ``None`` outside a repository: there is nothing to key a cache on.
        """
        if self.repo is None:
            return None
        return CachePaths.for_repo(
            self.repo.common_dir, self.settings.understand.db_location, cache_dir(self.env)
        )

    def require_repo(self) -> GitRepo:
        """The repository, or the typed refusal requirement 12.5 asks for."""
        if self.repo is None:
            raise NotAGitRepositoryError(
                "this command needs a git working tree and none was found",
                hint=NO_REPOSITORY_HINT,
            )
        return self.repo

    def baseline_store(self) -> BaselineStore:
        """The store over the configured baseline file (req 8.1)."""
        return BaselineStore(baseline_path(self.settings, self.repo_root))


def build_context(options: ContextOptions) -> RunContext:
    """Assemble one run, or raise the typed error that stops it.

    Raises ``ConfigError`` for invalid configuration (with the file and key that produced it),
    ``UnderstandNotFoundError`` when no usable installation is found (listing every location
    tried, req 1.3) and ``LicenseError`` when Understand refuses one (req 1.4). Being outside
    a repository raises nothing at all.
    """
    repo = find_repository(options.cwd, options.log)
    root = None if repo is None else repo.root
    settings, provenance = load_settings(root, dict(options.cli_overrides), options.env)
    understand, und, api = build_adapters(settings, options)
    return RunContext(
        settings=settings,
        provenance=provenance,
        availability=_availability(settings, provenance, api),
        understand=understand,
        und=und,
        api=api,
        repo=repo,
        env=options.env,
        log=options.log,
        progress=options.progress,
        started_at=now(),
    )


def find_repository(cwd: Path, log: CommandLog) -> GitRepo | None:
    """The repository containing ``cwd``, or ``None`` when there is none (req 12.5).

    Only "this is not a working tree" becomes ``None``. A ``git`` that cannot be run at all,
    or one that fails for any other reason, raises: that is an environment fault, and
    reporting it as "no repository" would send the operator looking in the wrong place.
    """
    try:
        return GitRepo.discover(cwd, log)
    except NotAGitRepositoryError:
        return None


def build_adapters(
    settings: Settings, options: ContextOptions
) -> tuple[UnderstandEnv, UndCli, ApiRunner]:
    """The Understand environment and the two adapters that talk to it.

    With the test seam set this searches nothing and starts nothing; otherwise the
    installation is discovered in requirement 1.1's order and then verified by running it
    (req 1.2), which is also what fills in the version and decides the API mode.
    """
    fixtures = fake_directory(options.env)
    if fixtures is not None:
        _reject_unusable_fixtures(fixtures)
        return fixture_env(fixtures), FixtureUndCli(fixtures), FixtureApiRunner(fixtures)
    found = discover(options.env, options.scitools_home, settings.understand.home)
    probes = RealProbes(cli=UndCli(found, options.log), log=options.log, env=options.env)
    env = verify(found, settings.understand.api_mode, probes)
    return env, UndCli(env, options.log), ApiRunner(env, options.log)


def _reject_unusable_fixtures(fixtures: Path) -> None:
    """Refuse a seam variable that names no directory of fixtures.

    A ``ConfigError`` rather than an analysis failure: the operator set an environment
    variable to the wrong thing, and the exit code should say so. Refusing here also closes
    the quiet path -- a directory that does not exist has no ``analyze.json`` either, and a
    missing ``analyze.json`` is the one absence the seam reads as an answer.
    """
    unusable = fixture_problem(fixtures)
    if unusable:
        raise ConfigError(unusable, key=FAKE_VAR, hint="Point it at a directory of fixtures.")


def cache_dir(env: Mapping[str, str]) -> Path | None:
    """The base directory the user cache lives under, from ``env``; ``None`` to let it decide.

    ``platformdirs.user_cache_dir`` -- which ``CachePaths.for_repo`` falls back to -- expands
    ``~`` from the **ambient** ``os.environ``, so a caller that passed its own ``HOME`` still
    got ``/home/<real user>/.cache`` for a requirement 1.5 report field. That is the same
    class as the ``Path.home()`` leak ``ContextOptions`` documents fixing, one consumer along.

    Only what can be honoured is honoured, and the promise is scoped to match. ``XDG_CACHE_HOME``
    is the variable platformdirs itself reads and is taken on any platform. ``HOME`` is used to
    build ``~/.cache`` on Linux only, because that is the layout platformdirs uses there;
    macOS (``~/Library/Caches``) and Windows are left to it rather than guessed at, so on those
    platforms a caller-supplied ``HOME`` still does not reach the cache root.
    """
    named = env.get("XDG_CACHE_HOME", "")
    if named.strip():
        return Path(named)
    home = env.get("HOME", "")
    if home.strip() and sys.platform.startswith("linux"):
        return Path(home) / ".cache"
    return None


def now() -> str:
    """The moment a run started, as the timestamp every record of it carries."""
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class RealProbes:
    """The three questions ``locator.verify`` asks, answered by actually running things.

    ``cli`` is built from the *discovered* environment, which is the same executable the
    ``und`` path argument names -- the locator passes it for the benefit of probes that do
    not already hold one. The API probes answer ``None`` for "this mode does not work here"
    and let anything they cannot survive propagate: a timeout is a fault to report, not a
    mode that failed.
    """

    cli: UndCli
    log: CommandLog
    env: Mapping[str, str]
    timeout_s: int = PROBE_TIMEOUT_S

    def und_version(self, und: Path) -> str:
        """What ``und version`` prints; a broken or unlicensed ``und`` raises from here."""
        return self.cli.version()

    def inprocess_import(self, api_dir: Path, bin_dir: Path) -> str | None:
        """Whether *this* interpreter can load the API -- asked in a child, never here.

        ``bin_dir`` is deliberately unused. In-process mode, as ``ApiRunner`` implements it,
        appends the API directory to ``sys.path`` and does nothing else; a probe that also
        exported a library path would answer for an environment the runner never builds.
        """
        return self._ping([sys.executable, str(WORKER_PATH), PING_OP], {PYTHONPATH_VAR: api_dir})

    def upython_ping(self, upython: Path, worker: Path) -> str | None:
        """Whether Understand's bundled interpreter answers the worker ping."""
        return self._ping([str(upython), str(worker), PING_OP], {})

    def _ping(self, argv: list[str], extra: Mapping[str, Path]) -> str | None:
        """Run one ping and return the API version it reported, or ``None``.

        Every foreseeable "no" is a ``None``: a non-zero status, output that is not a JSON
        object, an error envelope (``ApiUnavailable`` is exactly this case), or an answer with
        no version in it. ``OSError`` propagates for ``locator._ask`` to turn into a reason,
        and a timeout propagates all the way, because a hung interpreter is a fault.

        **Propagating is not a reason to leave the attempt out of the log**, and this recorded
        only the call that returned until task 11.2. Measured on the pristine code: a stand-in
        ``upython`` sleeping 30 s at ``timeout_s=2`` raised ``TimeoutExpired`` and left the log
        empty, and a missing executable raised ``FileNotFoundError`` and left the log empty,
        while :meth:`~scitools_hook.understand.und_cli.UndCli.version` on that same missing
        executable recorded ``(argv, 127)``. So the two probes worth seeing under ``--verbose``
        -- the one that hung for a whole minute and the interpreter that could not start --
        were the two that were invisible, and their timing was lost with them.

        ``subprocess.TimeoutExpired`` is not an ``OSError``, so the two are caught separately;
        both re-raise the exception they recorded, because *what* happens to the caller is
        unchanged and only the log entry is new. The statuses are
        :data:`~scitools_hook.exit_codes.TIMEOUT_RC` and
        :data:`~scitools_hook.exit_codes.MISSING_RC` from the package leaf -- the same two
        numbers ``git``, ``und`` and the API worker record, so the ``--verbose`` stream reads
        with one convention rather than one per adapter.
        """
        started = time.monotonic()
        try:
            done = subprocess.run(
                argv,
                input="",
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                check=False,
                env=self._child_env(extra),
            )
        except subprocess.TimeoutExpired:
            self.log.record(argv, time.monotonic() - started, TIMEOUT_RC)
            raise
        except OSError:
            self.log.record(argv, time.monotonic() - started, MISSING_RC)
            raise
        self.log.record(argv, time.monotonic() - started, done.returncode)
        if done.returncode != 0:
            return None
        return _version_of(done.stdout)

    def _child_env(self, extra: Mapping[str, Path]) -> dict[str, str]:
        """The probe's environment: this run's, plus the path entries the probe adds."""
        child = dict(self.env)
        for name, value in extra.items():
            existing = child.get(name, "")
            child[name] = f"{value}{os.pathsep}{existing}" if existing else str(value)
        return child


def _version_of(stdout: str) -> str | None:
    """The API version a ping answered with, or ``None`` when it did not answer one."""
    try:
        answer = json.loads(stdout)
    except Exception:  # noqa: BLE001 - the outcome is the contract: a version, or "no"
        # Guarded by outcome rather than by type. A probe exists to answer "does this mode
        # work", so every way of failing to produce a version is the same answer -- and the
        # types are not enumerable: `json.loads` raises `RecursionError` (not `ValueError`)
        # on a deeply nested document, and `MemoryError` on one larger than memory. On the
        # `check` path there is no outer net: `locator._ask` catches only `OSError`, so an
        # escape here ends the run with the unexpected-error code instead of falling back to
        # the mode that works.
        return None
    if not isinstance(answer, dict) or "error" in answer:
        return None
    version = answer.get("version")
    return str(version) if isinstance(version, str) and version else None


def _availability(settings: Settings, provenance: Provenance, api: ApiRunner) -> AvailabilityReport:
    """Validate the settings against the installed metric catalogue (req 3.8, 5.5).

    The catalogue is asked only when ``project.languages`` is configured -- without a language
    there is nothing to ask about -- so the default configuration pays no subprocess for this.
    A rejection arrives naming only a dotted key, because ``config.validate`` is pure and a
    ``Settings`` no longer remembers where its values came from; ``attach_source`` puts the
    file back on it so requirement 3.8's "file and key" holds for this pass too.
    """
    try:
        return validate_settings(settings, as_availability(MetricCatalogue(api)))
    except ConfigError as invalid:
        raise attach_source(invalid, provenance) from invalid
