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

from dataclasses import dataclass
from pathlib import Path
from typing import Final, Self

from scitools_hook.models.cache import CachePaths, SyncState
from scitools_hook.models.understand import AnalyzeResult
from scitools_hook.understand.cache_files import discard, present
from scitools_hook.understand.und_cli import (
    ALL,
    GitSource,
    UndCli,
    create_from_commit,
    set_git_repository,
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
    accuracy: bool = False
    sarif: Path | None = None


def ensure(cli: UndCli, request: CommitBuild, state: SyncState) -> AnalyzeResult | None:
    """Make the before database be ``request.key``, and say whether that cost an analysis.

    ``None`` means the recorded database was reused and **nothing ran** -- no ``create``, no
    ``analyze``, no process at all. That is the whole point of the key, and it is why the
    caller must answer such a run from the state rather than from a fresh result: there is no
    fresh result.

    A rebuild removes what is there first. ``und create`` over an existing database rewrites
    its settings and keeps its file list (measured, task 2.x), so re-creating in place would
    carry the previous key's files into a database built for this one.
    """
    if request.key.recorded_by(state) and present(request.paths.before_db):
        return None
    result = build(cli, request)
    record(state, request.key, result)
    return result


def build(cli: UndCli, request: CommitBuild) -> AnalyzeResult:
    """Create the database from the commit and analyse it once (requirements 3.1, 7.1).

    Three commands, and a failure in any of them raises ``AnalysisFailedError`` carrying
    ``und``'s own output, because every one of them goes through the wrapper's own refusal.
    They are not interchangeable and the order matters:

    #. ``create`` with ``-gitrepo``, ``-gitcommit`` and ``-refdb <after.und>``. The reference
       supplies the file set *and* registers the comparison pair, which is what makes the two
       databases answer a before/after question about the same files.
    #. ``settings -GitRepositoryDirectory``. ``-gitrepo`` decides where contents are read
       from; this is what the git-derived architectures run ``git log`` in (requirement 4.3),
       and a database that has one but not the other generates an empty architecture.
    #. ``analyze -all``, once. The whole database is new, so there is nothing selective to do.
    """
    discard(request.paths.before_db)
    create_from_commit(
        cli,
        request.paths.before_db,
        list(request.key.languages),
        GitSource(repo=request.repo, commit=request.key.commit, refdb=request.paths.after_db),
    )
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
