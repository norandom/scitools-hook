"""The before database built from the base commit rather than from a shadow tree (3.1, 3.5).

Understand 8.0 can create a database whose file *contents* come from a git commit
(``und create -gitrepo R -gitcommit C``), and, given ``-refdb``, whose file *set* is copied
from a reference database and rescanned against that commit. The before side of a check is
exactly that shape: the same files as the after side, seen one commit earlier. So the route
here builds ``before.und`` from the base commit with ``after.und`` as its reference, which
also registers the two as a comparison pair inside Understand.

**What makes a database reusable is a key, not a timestamp.** Four things decide what a
commit-built before database holds -- the commit, the language set, the analysis settings
(:func:`~scitools_hook.config.fingerprint.analysis_fingerprint`) and the Understand build --
and a run whose key matches the recorded one reuses the database *without analysing it at
all*, which is where requirement 3.5's saving comes from. Anything else is a rebuild, because
a database built for a different key is not this one with a detail wrong; it is a different
database.

Nothing here decides *whether* to take this route: that is requirement 3.3's question and it
lives in the database manager, which also owns the fallback to the shadow route. This module
answers only "make the before database be that commit, or tell me it already is".
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Self

from scitools_hook.config.fingerprint import analysis_fingerprint
from scitools_hook.config.models import Settings
from scitools_hook.errors import AnalysisFailedError, LicenseError
from scitools_hook.models.cache import CachePaths, SyncState
from scitools_hook.models.git import SyncTarget
from scitools_hook.models.progress import NullProgress, Progress
from scitools_hook.models.snapshot import Side
from scitools_hook.models.understand import AnalyzeResult, Feature
from scitools_hook.understand.cache_files import discard, present
from scitools_hook.understand.features import load_features
from scitools_hook.understand.und_cli import (
    ALL,
    GitSource,
    UndCli,
    create_from_commit,
    set_git_repository,
    und_exclusions,
)

COMMIT_ROUTE: Final = "commit"
"""What :attr:`~scitools_hook.models.cache.SyncState.before_route` records for this route."""

BEFORE: Final = "before"
"""The side this module builds; the after side is never commit-built (requirement 3.1)."""


@dataclass(frozen=True, slots=True)
class BeforeKey:
    """Everything a commit-built before database is a function of (req 3.1, 3.5).

    The commit alone does not decide what a database holds. The language set decides which
    files are enrolled, the analysis fingerprint covers every other setting that reaches the
    worker's request -- include and exclude patterns, the architecture, the ignore lists --
    and the Understand build decides how all of it was parsed. A key missing any of the four
    would reuse a database built under a different question.
    """

    commit: str
    languages: tuple[str, ...]
    settings: str
    build: str

    @classmethod
    def of(cls, commit: str, languages: list[str], settings: str, build: str) -> Self:
        """The key of one run, with the language set put in its canonical order."""
        return cls(
            commit=commit, languages=tuple(sorted(set(languages))), settings=settings, build=build
        )

    def recorded_by(self, state: SyncState) -> bool:
        """Whether ``state`` describes a database built from exactly this key.

        The route is part of the answer: a shadow-built ``before.und`` can sit at the same
        commit with the same languages and still hold a *different* file set, because the
        shadow is the working tree filtered by the include and exclude patterns while the
        commit route copies its file set from the after database.
        """
        return (
            state.before_route == COMMIT_ROUTE
            and state.before_commit == self.commit
            and tuple(sorted(set(state.languages))) == self.languages
            and state.analysis_settings == self.settings
            and state.created_with == self.build
        )


@dataclass(frozen=True, slots=True)
class CommitBuild:
    """One request to make the before database be a commit.

    A value rather than five parameters because ``und`` needs all of it at once and because
    the two reports are switches the manager decides per run: ``accuracy`` when the build
    offers it (requirement 7.1), ``sarif`` only when the companion key is also on (2.1).
    """

    paths: CachePaths
    repo: Path
    key: BeforeKey
    exclude: tuple[str, ...] = ()
    accuracy: bool = False
    sarif: Path | None = None


def ensure(
    cli: UndCli, request: CommitBuild, state: SyncState, progress: Progress | None = None
) -> AnalyzeResult | None:
    """Make the before database be ``request.key``, and say whether that cost an analysis.

    ``None`` means the recorded database was reused and **nothing ran** -- no ``create``, no
    ``analyze``, no process at all. That is the whole point of the key, and it is why the
    caller must answer such a run from the state rather than from a fresh result: there is no
    fresh result. A reuse announces nothing either, having done nothing to announce.

    A rebuild removes what is there first. ``und create`` over an existing database rewrites
    its settings and keeps its file list (measured), so re-creating in place would carry the
    previous key's files into a database built for this one.
    """
    if request.key.recorded_by(state) and present(request.paths.before_db):
        return None
    reporter = progress if progress is not None else NullProgress()
    reporter.start(BUILDING)
    started = time.monotonic()
    result = build(cli, request)
    record(state, request.key, result)
    reporter.finish(BUILDING, time.monotonic() - started)
    return result


def build(cli: UndCli, request: CommitBuild) -> AnalyzeResult:
    """Create the database from the commit and analyse it once (requirements 3.1, 7.1).

    Four commands, and a failure in any of them raises ``AnalysisFailedError`` carrying
    ``und``'s own output, because every one goes through the wrapper's own refusal. They are
    not interchangeable and the order matters:

    #. ``create`` with ``-gitrepo`` and ``-gitcommit``, **rooted at the repository**.
    #. ``add <repo>``, with the configured excludes translated into the form ``und -exclude``
       honours. This is what decides the file set.
    #. ``settings -GitRepositoryDirectory``. ``-gitrepo`` decides where contents are read
       from; this is what the git-derived architectures run ``git log`` in (requirement 4.3).
    #. ``analyze -all``, once. The whole database is new, so there is nothing selective to do.

    **There is no ``-refdb``, and that is the measurement this route turns on.** The design
    used it -- it copies the reference's settings and file set, which is exactly the parity a
    before/after comparison wants. Measured on Build 1262, it cannot be used here: ``-refdb``
    copies the reference's file *paths* as well, the Gate's after database names its files
    under a shadow tree in the user's cache, and **``-gitcommit`` pins the contents only of
    files that are inside the ``-gitrepo`` directory**. A file outside it is read from disk,
    silently. The before database then held the *working tree's* code -- identical to the
    after database, byte for byte in every metric -- and a range check that reported eight
    ratchet findings through the shadow route reported one. A gate comparing a side against
    itself is the exact silent green this tool exists to refuse.

    So the database is rooted at the repository, where ``-gitcommit`` does pin contents
    (measured: ``core.add`` is ``CountLineCode 2`` at the base commit and ``7`` at head), and
    the file set comes from ``und add`` under the configured excludes rather than from the
    reference. The consequence is recorded in ``research.md``: the before side's file set is
    the repository's, not the shadow's, and the two can differ where a glob means different
    things to Understand and to the synchroniser.
    """
    discard(request.paths.before_db)
    create_from_commit(
        cli,
        request.paths.before_db,
        list(request.key.languages),
        GitSource(repo=request.repo, commit=request.key.commit),
    )
    cli.add(request.paths.before_db, request.repo, und_exclusions(request.exclude))
    set_git_repository(cli, request.paths.before_db, request.repo)
    return cli.analyze(request.paths.before_db, ALL, accuracy=request.accuracy, sarif=request.sarif)


def record(state: SyncState, key: BeforeKey, result: AnalyzeResult) -> None:
    """Write the key and what the build reported into the state (requirements 3.5, 3.6, 7.2).

    The accuracy is recorded rather than recomputed for the same reason the parse errors are:
    the next warm run analyses nothing, so there is no ``und analyze`` to report a figure, and
    the last one is still true of the database that is still there. A build that was not asked
    for the figure records nothing rather than a zero, which is a measurement.
    """
    state.before_route = COMMIT_ROUTE
    state.before_commit = key.commit
    state.languages = list(key.languages)
    state.analysis_settings = key.settings
    state.created_with = key.build
    if result.accuracy is not None:
        state.accuracy[BEFORE] = result.accuracy


BUILDING: Final = "building the before database from the base commit"
"""The phase name a commit build announces; a reuse announces nothing, having done nothing."""

SHADOW_ROUTE: Final = "shadow"
"""What the exported-tree route records; the manager sets it whenever it builds that way."""


@dataclass(frozen=True, slots=True)
class Attempt:
    """Everything the route decision needs, gathered by the manager that owns it.

    A value rather than five parameters, and the reason is the gate: ``DatabaseManager`` is
    eight methods past its ``CountDeclMethod`` limit, so the decision cannot become a method
    on it, and it is built by :func:`attempt_for` rather than named in the manager, because
    naming a class there is what its ``CountClassCoupled`` counts.

    The Understand build is **not** here: the manager already looks it up once per run and
    caches it, and a second lookup through this value would be a second ``und`` process on
    every run for a string that is already in hand. It is passed to :func:`serve` instead.
    """

    cli: UndCli
    paths: CachePaths
    repo: Path
    settings: Settings
    progress: Progress


def attempt_for(
    cli: UndCli, paths: CachePaths, repo: Path, settings: Settings, progress: Progress
) -> Attempt:
    """Gather what the route decision needs, so the caller need not name :class:`Attempt`.

    A function rather than the constructor because the caller is ``DatabaseManager``, whose
    ``CountClassCoupled`` counts every class its methods name and which is fifteen over that
    limit already.
    """
    return Attempt(cli=cli, paths=paths, repo=repo, settings=settings, progress=progress)


def serve(
    attempt: Attempt, side: Side, target: SyncTarget, state: SyncState, build: str
) -> AnalyzeResult | None:
    """The before side through the commit route, or ``None`` when the shadow route must (3.3).

    ``None`` is not a failure. It is the answer for every run this route does not apply to --
    the after side, a before side that is not a commit, a build that cannot do it, a
    configuration that asked for the shadow tree -- and for a commit build that *failed*,
    which requirement 3.4 says must fall back and say so rather than stop the run.

    A run this route serves never exports a shadow tree for the before side (requirement 3.1),
    which is where its saving comes from: the tree is the expensive half.

    **The database is named under the repository, not under a shadow tree**, which is why the
    result carries ``analysis_root`` and why the parse errors are made relative to the same
    directory: an entity has to have one long name whichever route built its side, and the
    after side's is a cache path. :func:`build` records why the reference database cannot be
    used to get that parity for free.
    """
    if side != BEFORE or target.kind != "commit":
        return None
    if not wanted(attempt.settings, offers(attempt.paths, build, Feature.COMMIT_BEFORE)):
        return None
    request = _request(attempt, target.commit, state, build)
    try:
        fresh = ensure(attempt.cli, request, state, attempt.progress)
    except LicenseError:
        # Requirement 1.4 wants this exit code out of here unaltered, and falling back would
        # not help: the shadow route needs the same licence this one was refused.
        raise
    except AnalysisFailedError as refused:
        attempt.progress.note(
            "the commit-built before database failed, so this run used the shadow tree: "
            f"{_said(refused)}"
        )
        return None
    root = attempt.repo
    if fresh is None:
        return _reused(state).model_copy(update={"analysis_root": root})
    errors = state.record_parse_errors(BEFORE, root, fresh.parse_errors, None)
    return fresh.model_copy(update={"parse_errors": errors, "analysis_root": root})


def _said(refused: AnalysisFailedError) -> str:
    """What ``und`` said, or the wrapper's own sentence when it said nothing.

    Understand's words first, for the reason task 2.1 recorded one refusal over: the wrapper's
    message leads with the whole command line, which on a cache path fills the line on its own
    and pushes the useful sentence off the end of it.
    """
    said = " ".join(refused.stderr.split())
    return said or str(refused)


def wanted(settings: Settings, offered: bool) -> bool:
    """Whether this run should take the commit route at all (requirement 3.3).

    ``auto`` is the interesting value and the reason the setting is three-valued rather than a
    flag: it asks for the route *if the build has it* and falls back silently otherwise, so a
    6.5 install keeps today's behaviour with no configuration change and no refusal.
    """
    chosen = settings.understand.before_side
    return chosen == COMMIT_ROUTE or (chosen == "auto" and offered)


def offers(paths: CachePaths, build: str, feature: Feature) -> bool:
    """Whether the stored measurement says *this* build offers ``feature`` (req 1.2, 1.4).

    Read from the record ``doctor`` wrote beside these very databases, rather than probed: a
    check measures nothing about the installation, and a record from another build is not an
    answer about this one.
    """
    report = load_features(paths)
    return report is not None and report.build == build and report.offers(feature)


def _request(attempt: Attempt, commit: str, state: SyncState, build: str) -> CommitBuild:
    """One run's request, with the two optional reports decided from what the build offers.

    The languages come from configuration when it names any and from the record otherwise --
    the same rule the shadow route follows, and it is safe here because the after side is
    always ensured first and writes the set it detected before this runs.
    """
    return CommitBuild(
        paths=attempt.paths,
        repo=attempt.repo,
        exclude=tuple(attempt.settings.project.exclude),
        key=BeforeKey.of(
            commit=commit,
            languages=attempt.settings.project.languages or state.languages,
            settings=analysis_fingerprint(attempt.settings),
            build=build,
        ),
        accuracy=offers(attempt.paths, build, Feature.ACCURACY),
        sarif=attempt.paths.before_db.with_suffix(".sarif")
        if attempt.settings.understand.sarif
        else None,
    )


def _reused(state: SyncState) -> AnalyzeResult:
    """What a run that analysed nothing answers with: the record of the database that is there.

    The figures are read rather than measured for the reason both were recorded in the first
    place: measured on Build 1262, ``-accuracy`` and ``-sarif`` describe **the pass**, and a
    pass that did not happen describes nothing. ``0 of 0 parsed files had no errors or
    warnings (100%)`` is what a ``-changed`` run with nothing to do prints for a database
    holding three parse errors.
    """
    return AnalyzeResult(
        seconds=0.0,
        parse_errors=list(state.parse_errors.get(BEFORE, [])),
        accuracy=state.accuracy.get(BEFORE),
    )
