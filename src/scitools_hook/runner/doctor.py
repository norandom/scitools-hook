"""``doctor``: diagnose the installation, the repository and the configuration (req 1.5, 12.5).

An operator runs this command precisely when something is broken, which fixes the one rule
the whole module is built around: **it reports, it never raises.** Every step that can fail --
locating Understand, running ``und``, loading either interpreter, reading the configuration,
reading the sync state -- is attempted inside a guard and its failure becomes an entry in
:attr:`DoctorReport.problems` naming what failed. A command that stopped at the first fault
would tell an operator about one problem when they have three.

The guard is deliberately not universal. ``GateError`` (the Gate's own diagnoses), ``OSError``
and ``subprocess.SubprocessError`` are caught, because those are the ways an *environment*
breaks. A ``TypeError`` from the Gate's own code is not caught: that is a defect, the CLI's
unexpected-error handler exists for it, and swallowing it into a "problem" would disguise a
bug as a broken installation.

Two things this report says that the rest of the Gate does not:

* **Both API probes, not just the one that was used.** ``locator.verify`` stops at the first
  mode that works and, for a forced mode, deliberately never runs the other probe. A
  diagnosis has the opposite job -- an operator needs to know that ``upython`` answers and
  in-process does not -- so both are run here and reported side by side. That is safe
  precisely because :class:`RealProbes` runs the in-process import in a *child* process.
  The mode decision itself is still ``locator.verify``'s: it is replayed against the answers
  already collected, so there is no second implementation of the precedence rule and no
  second round of subprocesses.
* **A discovered installation is not a verified one.** ``locator.discover`` answers with an
  ``UnderstandEnv`` whose ``version`` is empty and whose ``api_mode`` is a guess from the
  directory layout. When verification fails the report still names the installation -- an
  operator must be told *which* broken install was found -- but
  :attr:`UnderstandDiagnosis.verified` is false and :attr:`UnderstandDiagnosis.api_mode` is
  ``None``. Read the mode from the diagnosis, never from ``env.api_mode``.
"""

from __future__ import annotations

import platform
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, TypeVar

from scitools_hook.config.loader import load_settings
from scitools_hook.config.models import ApiMode, Provenance, Settings
from scitools_hook.config.validate import validate_settings
from scitools_hook.errors import (
    ConfigError,
    GateError,
    NotAGitRepositoryError,
    UnderstandNotFoundError,
)
from scitools_hook.git.repo import GitRepo
from scitools_hook.models.cache import CachePaths, SyncState
from scitools_hook.models.snapshot import DataModel
from scitools_hook.models.understand import LicenseStatus, UnderstandEnv
from scitools_hook.paths import classify_directory, classify_file
from scitools_hook.runner.context import (
    PROBE_TIMEOUT_S,
    ContextOptions,
    RealProbes,
    cache_dir,
)
from scitools_hook.understand.api_runner import ApiRunner
from scitools_hook.understand.catalogue import MetricCatalogue, as_availability
from scitools_hook.understand.fake import (
    FAKE_VAR,
    FIXTURE_API_VERSION,
    FixtureApiRunner,
    FixtureUndCli,
    fake_directory,
    fixture_env,
    fixture_problem,
)
from scitools_hook.understand.locator import WORKER_PATH, discover, verify
from scitools_hook.understand.und_cli import UndCli

# Written as an explicit ``TypeVar`` rather than PEP 695 ``[T]`` syntax: Understand 6.5
# cannot parse a type-parameter list, and one such declaration costs the rest of the file
# from the analysis (measured in task 10.4).
T = TypeVar("T")
"""Whatever the guarded step returns; the guard neither inspects nor constrains it."""

ApiModeName = Literal["inprocess", "upython"]
"""The two modes a probe can report on; ``auto`` is a preference, never an outcome."""

PROBE_ORDER: Final[tuple[ApiModeName, ...]] = ("upython", "inprocess")
"""Reported in the order ``auto`` tries them, so the report reads as the decision did."""

NO_ANSWER: Final = "the probe ran but did not report an API version"
"""What a probe returning ``None`` means: the interpreter started and the API did not load."""

NO_UPYTHON: Final = "this installation ships no upython executable"
"""Not a failure of the probe: there is nothing to probe. Reported as its own reason."""

Caught = (GateError, OSError, subprocess.SubprocessError)
"""How an environment breaks. A defect in the Gate is not in this tuple and is not caught."""


class ApiProbe(DataModel):
    """What one API mode answered when it was asked to load Understand (req 1.2, 1.5)."""

    mode: ApiModeName
    ok: bool = False
    version: str = ""
    detail: str = ""


class UnderstandDiagnosis(DataModel):
    """The Understand half of the report: where, which version, and what loads.

    Grouped rather than flattened onto :class:`DoctorReport` for two reasons. The design's
    single ``api_ok`` flag cannot express what task 8.2 asks for -- *both* probes and the
    chosen mode -- and the four Understand fields plus the ten the rest of the report needs
    would put one model well past the class-size limit the Gate applies to its own code.

    ``env`` is the installation that was found, verified or not; ``verified`` says which.
    While it is false, ``env.api_mode`` is a guess from the directory layout and
    :attr:`api_mode` -- the decided one -- is ``None``.
    """

    env: UnderstandEnv | None = None
    verified: bool = False
    und_version: str | None = None
    license: LicenseStatus | None = None
    probes: list[ApiProbe] = []
    api_mode: ApiModeName | None = None


class GitStatus(DataModel):
    """The repository the Gate found, or the reason there is none (req 1.5, 12.5).

    ``head`` is ``None`` on an unborn branch -- the state a pre-commit hook meets on a
    repository's first commit -- which is not a fault and is not reported as one.
    """

    inside_repository: bool = False
    root: Path | None = None
    git_dir: Path | None = None
    common_dir: Path | None = None
    head: str | None = None
    detail: str = ""


class DoctorReport(DataModel):
    """Everything requirement 1.5 asks the diagnosis command to report.

    Divergence from the design's flat sketch, recorded deliberately: ``understand`` is an
    :class:`UnderstandDiagnosis` rather than a bare ``UnderstandEnv | None`` plus ``api_ok``,
    and ``settings`` is carried beside ``settings_provenance`` because "the effective
    configuration with the file each setting came from" needs both halves.
    """

    understand: UnderstandDiagnosis
    python: str
    git: GitStatus
    cache: CachePaths | None = None
    state: SyncState | None = None
    settings: Settings
    settings_provenance: Provenance
    problems: list[str] = []


def run_doctor(options: ContextOptions) -> DoctorReport:
    """Diagnose this machine and answer with the report; never raises for a broken one.

    Every phase runs through :func:`_guarded`, whose net is the *outcome* rather than a list
    of expected exception types. Three phases previously called their helpers directly and so
    had no net at all -- ``MemoryError`` from a file larger than available memory is neither
    an ``OSError``, a ``ValueError`` nor a ``RecursionError``, and it walked straight out of
    ``_cache_status`` and out of this function. A phase that fails contributes its default
    (no repository, empty settings, nothing found) plus a problem naming what broke, which is
    exactly what an operator running this command on a broken machine needs.
    """
    problems: list[str] = []
    repo, git = _guarded(lambda: _git_status(options, problems), "git", problems) or (
        None,
        GitStatus(detail="the git status could not be determined"),
    )
    settings, provenance = _guarded(
        lambda: _configuration(options, repo, problems), "the configuration", problems
    ) or (Settings(), Provenance())
    understand, api = _guarded(
        lambda: _understand(settings, options, problems), "the Understand installation", problems
    ) or (UnderstandDiagnosis(), None)
    if api is not None:
        _guarded(lambda: _check_metrics(settings, api, problems), "the metric catalogue", problems)
    cache, state = _guarded(
        lambda: _cache_status(repo, settings, options.env, problems),
        "the analysis cache",
        problems,
    ) or (None, None)
    return DoctorReport(
        understand=understand,
        python=platform.python_version(),
        git=git,
        cache=cache,
        state=state,
        settings=settings,
        settings_provenance=provenance,
        problems=problems,
    )


# --- git ---------------------------------------------------------------------------


def _git_status(options: ContextOptions, problems: list[str]) -> tuple[GitRepo | None, GitStatus]:
    """The repository containing the working directory, and what to say about it.

    Being outside a repository is a *status*, not a problem: requirement 12.5 makes running
    there legitimate. Git failing to run at all is a problem, and this is the one place that
    distinction is drawn twice -- once for the report, once for the exit code every other
    command derives from the same call.
    """
    verdict = classify_directory(options.cwd)
    if verdict.absent or not verdict.usable:
        wrong = "does not exist" if verdict.absent else verdict.reason
        problems.append(f"the working directory {options.cwd} {wrong}")
        return None, GitStatus(detail=f"{options.cwd} {wrong}")
    try:
        return _found(GitRepo.discover(options.cwd, options.log), problems)
    except NotAGitRepositoryError as outside:  # noqa: BLE001 - narrower case, handled first
        # Git's own words, not a paraphrase: `discover` fails identically for a directory
        # outside any repository, a bare repository and a repository whose HEAD is corrupt
        # (measured: git stops recognising the directory at all and exits 128), and only its
        # message distinguishes them. Being outside one is a status, not a problem -- req
        # 12.5 makes running here legitimate -- so nothing is added to `problems`.
        return None, GitStatus(detail=outside.message)
    except Exception as broken:  # noqa: BLE001 - see _guarded: a report must always arrive
        problems.append(_reason("git", broken))
        return None, GitStatus(detail=str(broken))


def _found(repo: GitRepo, problems: list[str]) -> tuple[GitRepo, GitStatus]:
    """A repository that was found, paired with the status describing it."""
    return repo, _repository_status(repo, problems)


def _repository_status(repo: GitRepo, problems: list[str]) -> GitStatus:
    """The status of a repository that was found, including its ``HEAD`` if it has one."""
    head = _guarded(repo.head, "git rev-parse HEAD", problems)
    return GitStatus(
        inside_repository=True,
        root=repo.root,
        git_dir=repo.git_dir,
        common_dir=repo.common_dir,
        head=head,
    )


# --- configuration -----------------------------------------------------------------


def _configuration(
    options: ContextOptions, repo: GitRepo | None, problems: list[str]
) -> tuple[Settings, Provenance]:
    """The effective settings and where each value came from (req 1.5, 3.9).

    A configuration the Gate refuses is the fault that stops every *other* command, so doctor
    reports it and carries on with an empty configuration rather than pretending the broken
    file was loaded. ``ConfigError`` is caught first for its file and key; anything else is
    caught by :func:`_guarded`'s rule and recorded as an unexpected internal error naming its
    type, so a defect is still legible as a defect but never costs the operator their report.

    An earlier version of this function caught ``ConfigError`` alone, justified by the claim
    that "every way ``load_settings`` can fail is mapped to a ``ConfigError``". That claim was
    an inference presented as a measurement, and it was false. What is now actually measured,
    by running each case: a Latin-1 file raises ``UnicodeDecodeError``; ``value = [[[...]]]``
    at 497 levels raises ``RecursionError`` (450 levels parses); the same nesting through a
    ``SCITOOLS_HOOK_*`` variable raises it too; a path holding a NUL byte raises a plain
    ``ValueError`` from ``open``; and a FIFO does not raise at all -- it blocks in
    ``read_text`` forever, which destroys the report just as thoroughly. All five are mapped
    at their source in ``config.loader`` so that *every* command exits with the config-error
    code. What is **not** claimed is that the list is complete: that is the claim that failed
    review, and it is why the guard below no longer depends on it -- see
    ``test_a_report_arrives_whatever_loading_the_configuration_does``, which injects a failure
    rather than relying on the enumeration above being right.
    """
    root = None if repo is None else repo.root
    try:
        return load_settings(root, dict(options.cli_overrides), options.env)
    except ConfigError as invalid:
        problems.append(_config_problem(invalid))
    except Exception as broken:  # noqa: BLE001 - see _guarded: a report must always arrive
        problems.append(_reason("configuration", broken))
    return Settings(), Provenance()


def _config_problem(invalid: ConfigError) -> str:
    """One configuration failure, located the way requirement 3.8 locates it."""
    where = f" [{invalid.file}]" if invalid.file is not None else ""
    return f"configuration: {invalid.message}{where}"


def _check_metrics(settings: Settings, api: ApiRunner, problems: list[str]) -> None:
    """Ask the installed metric catalogue what this configuration can actually evaluate.

    Two answers matter to an operator and neither is visible anywhere else: a configured
    language Understand computes nothing for -- which would otherwise drop every rule and
    report a green run (task 2.4) -- and a shipped default this repository's languages cannot
    compute, which is dropped legitimately but must not be dropped silently (req 5.5).
    """
    try:
        report = validate_settings(settings, as_availability(MetricCatalogue(api)))
    except Exception as invalid:  # noqa: BLE001 - see _guarded: a report must always arrive
        problems.append(_reason("configuration", invalid))
        return
    for spec in report.dropped:
        # `unavailable` names every configured language whenever anything was dropped
        # (`_unavailable_metrics` builds it from the same list), so there is no empty case.
        languages = ", ".join(sorted(report.unavailable))
        problems.append(f"threshold {spec.rule} is not evaluated: {languages} has no such metric")


# --- Understand --------------------------------------------------------------------


def _understand(
    settings: Settings, options: ContextOptions, problems: list[str]
) -> tuple[UnderstandDiagnosis, ApiRunner | None]:
    """Locate Understand, run it, and say what each way of reaching its API answered."""
    fixtures = fake_directory(options.env)
    if fixtures is not None:
        problems.append(
            f"{FAKE_VAR}={fixtures} is set: the Gate is reading fixtures, not analysing code"
        )
        unusable = fixture_problem(fixtures)
        if unusable:
            problems.append(unusable)
            return UnderstandDiagnosis(env=fixture_env(fixtures)), None
        return _fixture_diagnosis(fixtures), FixtureApiRunner(fixtures)
    try:
        found = discover(options.env, options.scitools_home, settings.understand.home)
    except UnderstandNotFoundError as missing:
        problems.append(_missing_problem(missing))
        return UnderstandDiagnosis(), None
    return _diagnose(found, settings.understand.api_mode, options, problems)


def _fixture_diagnosis(fixtures: Path) -> UnderstandDiagnosis:
    """What the test seam presents: itself, honestly, with no probing to do."""
    return UnderstandDiagnosis(
        env=fixture_env(fixtures),
        verified=True,
        und_version=FixtureUndCli(fixtures).version(),
        license=LicenseStatus(ok=True),
        probes=[ApiProbe(mode="inprocess", ok=True, version=FIXTURE_API_VERSION, detail=FAKE_VAR)],
        api_mode="inprocess",
    )


def _missing_problem(missing: UnderstandNotFoundError) -> str:
    """Requirement 1.3's list, kept whole: every location tried and what to set."""
    tried = "; ".join(missing.tried)
    return f"{missing.message} (tried {tried}). {missing.hint or ''}".strip()


def _diagnose(
    found: UnderstandEnv, preferred: ApiMode, options: ContextOptions, problems: list[str]
) -> tuple[UnderstandDiagnosis, ApiRunner | None]:
    """Run everything a found installation can be asked, then replay the mode decision."""
    # A shorter ceiling than the wrapper's own 900 s: this is the command an operator runs
    # when things are already broken, and a wedged `und` would otherwise delay the report by
    # up to half an hour across the two calls made here. It matches the probes' own budget.
    cli = UndCli(found, options.log, timeout_s=PROBE_TIMEOUT_S)
    probes = RealProbes(cli=cli, log=options.log, env=options.env)
    version = _guarded(cli.version, "und version", problems)
    license_status = _guarded(cli.license_status, "und license", problems)
    _reject_license(license_status, problems)
    answers = [_ask_probe(found, probes, mode, problems) for mode in PROBE_ORDER]
    env = _verified(found, preferred, version, answers, problems)
    return (
        UnderstandDiagnosis(
            env=env or found,
            verified=env is not None,
            und_version=version,
            license=license_status,
            probes=answers,
            api_mode=None if env is None else env.api_mode,
        ),
        None if env is None else ApiRunner(env, options.log),
    )


def _reject_license(status: LicenseStatus | None, problems: list[str]) -> None:
    """A license Understand refuses is requirement 1.4's fault, reported not raised here."""
    if status is not None and not status.ok:
        problems.append(f"license: {status.text or 'Understand reports no valid license'}")


def _ask_probe(
    found: UnderstandEnv, probes: RealProbes, mode: ApiModeName, problems: list[str]
) -> ApiProbe:
    """Run one API probe and record what it answered, whichever mode was preferred.

    Both are asked even when the operator forced one, because the report's job is to say what
    works. The in-process probe is safe to run here for the same reason it is safe in a run:
    it loads the API in a child process, never in this one.
    """
    upython = found.upython
    if mode == "upython" and upython is None:
        return ApiProbe(mode=mode, detail=NO_UPYTHON)
    if upython is not None and mode == "upython":
        return _probe_result(mode, lambda: probes.upython_ping(upython, WORKER_PATH), problems)
    api_dir = found.python_api_dir
    return _probe_result(mode, lambda: probes.inprocess_import(api_dir, api_dir.parent), problems)


def _probe_result(
    mode: ApiModeName, ask: Callable[[], str | None], problems: list[str]
) -> ApiProbe:
    """One probe's answer as a record; a probe that could not run is a reason, not a raise."""
    try:
        version = ask()
    except Exception as broken:  # noqa: BLE001 - see _guarded: a report must always arrive
        problems.append(_reason(f"the {mode} API probe", broken))
        return ApiProbe(mode=mode, detail=str(broken))
    if version is None:
        return ApiProbe(mode=mode, detail=NO_ANSWER)
    return ApiProbe(mode=mode, ok=True, version=version)


def _verified(
    found: UnderstandEnv,
    preferred: ApiMode,
    version: str | None,
    answers: list[ApiProbe],
    problems: list[str],
) -> UnderstandEnv | None:
    """The verified environment, decided by ``locator.verify`` replaying these answers.

    Nothing is run again: :class:`_Replay` answers from ``answers``, so the precedence rule
    that picks a mode lives in exactly one place and the report cannot disagree with what a
    real run would do.
    """
    replay = _Replay(version, {probe.mode: probe.version or None for probe in answers})
    return _guarded(lambda: verify(found, preferred, replay), "the Understand API", problems)


@dataclass(frozen=True)
class _Replay:
    """The locator's ``Probes`` protocol, answering from measurements already taken."""

    version: str | None
    answers: Mapping[str, str | None]

    def und_version(self, und: Path) -> str:
        """The version already read, or the failure that stopped the whole verification."""
        if self.version is None:
            raise UnderstandNotFoundError(
                f"{und} did not report a version, so the installation cannot be verified"
            )
        return self.version

    def inprocess_import(self, api_dir: Path, bin_dir: Path) -> str | None:
        """What the in-process probe answered."""
        return self.answers.get("inprocess")

    def upython_ping(self, upython: Path, worker: Path) -> str | None:
        """What the ``upython`` probe answered."""
        return self.answers.get("upython")


# --- the cache ---------------------------------------------------------------------


def _cache_status(
    repo: GitRepo | None, settings: Settings, env: Mapping[str, str], problems: list[str]
) -> tuple[CachePaths | None, SyncState | None]:
    """Where this repository's databases live and what the shadows currently hold (req 2.8).

    The paths are reported whether or not the state can be read: an operator whose
    ``state.json`` is corrupt still needs to be told which directory to delete.
    """
    if repo is None:
        return None, None
    # `cache_dir(env)` for the same reason `RunContext.cache` uses it: `platformdirs` expands
    # `~` from the ambient environment, so without it this requirement 1.5 field names the real
    # user's cache directory however the caller's mapping was set. Two sites, one class -- the
    # second was found only because a test began asserting it stayed inside its sandbox.
    paths = CachePaths.for_repo(repo.common_dir, settings.understand.db_location, cache_dir(env))
    _reject_unusable_root(paths, problems)
    return paths, _sync_state(paths, problems)


def _reject_unusable_root(paths: CachePaths, problems: list[str]) -> None:
    """Report a cache root whose name is taken and which cannot serve as one.

    Absence is the normal state of a repository that has never been analysed and is the one
    silence here. Everything else is reported, and the distinction is made by ``os.lstat``
    (:mod:`scitools_hook.paths`) rather than by ``Path.exists()``, which swallows
    ``OSError`` -- a dangling symlink, a symlink loop and an unsearchable parent all answered
    ``False`` and were reported as "never analysed". Measured faults this now names: a root
    that is a plain file (every path beneath it then answers ``exists()`` ``False`` rather
    than raising ``NotADirectoryError``), and a root that is a directory this user cannot
    enter, where ``state.json``'s own ``exists()`` answers ``False`` because ``EACCES`` is
    swallowed -- an unreadable cache reading exactly like one that was never built.
    """
    verdict = classify_directory(paths.root)
    if not verdict.absent and not verdict.usable:
        problems.append(f"the cache root {paths.root} {verdict.reason}")


def _sync_state(paths: CachePaths, problems: list[str]) -> SyncState | None:
    """The recorded sync state, or the reason it could not be read.

    A missing file is the state of a repository that has never been analysed, which is not a
    problem. **Anything else is** -- and that sentence was false here for two rounds, because
    absence was decided by ``Path.exists()``, which swallows ``OSError``: a dangling symlink,
    a symlink loop, a symlink into an unsearchable directory, an over-long name and a NUL in
    the path were all reported as "never analysed", byte-identical to genuine absence. The
    classification now comes from :mod:`scitools_hook.paths`, which is the same
    implementation ``BaselineStore`` uses, so the two cannot drift apart again.

    A state file that exists and cannot be read means the next run will rebuild from scratch,
    and the operator should hear that here rather than discover it as a slow commit.
    """
    verdict = classify_file(paths.state)
    if verdict.absent:
        return None
    if not verdict.usable:
        problems.append(f"the sync state {paths.state} {verdict.reason}")
        return None
    try:
        return SyncState.model_validate_json(paths.state.read_text(encoding="utf-8"))
    except (OSError, ValueError) as unreadable:
        # ValueError covers pydantic's ValidationError, which is how pydantic-core reports
        # its own depth limit -- measured: a 100k-deep document yields `json_invalid`, never a
        # `RecursionError`, so a guard naming that type here could never be exercised and
        # would be a claim rather than a test. The net that holds for anything unlisted is the
        # outcome guard on this phase in `run_doctor`, pinned by an injected error.
        problems.append(f"the sync state {paths.state} could not be read: {unreadable}")
        return None


# --- the guard ---------------------------------------------------------------------


def _reason(what: str, broken: Exception) -> str:
    """One failure as a problem line, saying whether it is the environment or the Gate.

    The distinction is the whole reason this is not a silent catch-all: an environment fault
    (:data:`Caught` -- a ``GateError``, an ``OSError``, a subprocess failure) reads as the
    operator's problem to fix, while anything else is named by its exception type so a defect
    in the Gate is still legible as a defect rather than disguised as a broken installation.
    """
    if isinstance(broken, Caught):
        return f"{what} failed: {broken}"
    return f"{what} failed unexpectedly ({type(broken).__name__}): {broken}"


def _guarded(step: Callable[[], T], what: str, problems: list[str]) -> T | None:
    """Run one diagnostic step; record its failure as a problem and answer ``None``.

    ``Exception`` is caught deliberately, and the breadth is the point. Requirement 1.5 asks
    for this report *when the installation is broken*, and this task's acceptance criterion
    says problems entries rather than raising -- so a guard that enumerates the failures it
    expects is a guard that is one unlisted exception away from producing no report at all.
    Review found exactly that: a narrow guard here was justified by an enumeration that turned
    out to be incomplete. :func:`_reason` keeps what the narrow guard was protecting -- an
    unexpected type is still named as one -- without betting the report on the list being
    right. ``BaseException`` is *not* caught: ``KeyboardInterrupt`` and ``SystemExit`` must
    still end the process.
    """
    try:
        return step()
    except Exception as broken:  # noqa: BLE001 - deliberate; see the docstring above
        problems.append(_reason(what, broken))
        return None
