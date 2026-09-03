"""Where a repository's analysis cache lives and what state it keeps (req 2.1, 2.2, 2.7).

Nothing the gate writes ever lands in the working tree. The cache root is derived from the
git *common* directory, so linked worktrees of one repository share it::

    db_location = "cache"    <user cache dir>/scitools-hook/<repo_id>/
    db_location = "gitdir"   <git common dir>/scitools-hook/

``repo_id`` is the first 16 hex characters of the sha1 of the resolved common directory, so
the same repository always maps to the same cache regardless of how it was addressed.
"""

from __future__ import annotations

import hashlib
from collections.abc import Collection, Sequence
from pathlib import Path
from typing import Final, Self

from platformdirs import user_cache_dir
from pydantic import Field

from scitools_hook.config.models import DbLocation
from scitools_hook.models.git import SyncTargetKind
from scitools_hook.models.snapshot import DataModel, ParseError, Side

APP_NAME: Final = "scitools-hook"
"""Directory name used under the user cache dir and under the git directory."""

REPO_ID_LENGTH: Final = 16


def repo_id(common_dir: Path) -> str:
    """Stable identifier of a repository, from its resolved git common directory."""
    resolved = str(Path(common_dir).resolve())
    digest = hashlib.sha1(resolved.encode("utf-8"), usedforsecurity=False)
    return digest.hexdigest()[:REPO_ID_LENGTH]


def cache_root(
    common_dir: Path, db_location: DbLocation = "cache", cache_dir: Path | None = None
) -> Path:
    """Root of the analysis cache for a repository, per ``understand.db_location``.

    ``cache_dir`` overrides the platform user cache directory (tests, future ``--cache-dir``).

    **The ``APP_NAME`` segment is appended to a supplied base too, and that is a fix rather
    than a flourish.** ``user_cache_dir(APP_NAME)`` carries the name itself, so the two arms
    used to disagree: on Linux ``runner.context.cache_dir(env)`` returns ``~/.cache`` whenever
    ``HOME`` is set, so a base was *always* supplied, the ``platformdirs`` arm was effectively
    dead, and the result was ``~/.cache/<repo_id>`` -- an unlabelled hash directory sitting
    directly in the user's cache, contradicting this module's own documented layout. Measured:
    ``/home/mc/.cache/1c23f1c40aae2d9b``. Every test passed an explicit ``tmp_path`` base,
    which is exactly why nothing noticed: the defect lived only in the arm the tests replaced.
    """
    if db_location == "gitdir":
        return Path(common_dir).resolve() / APP_NAME
    base = Path(cache_dir) / APP_NAME if cache_dir is not None else Path(user_cache_dir(APP_NAME))
    return base / repo_id(common_dir)


class CachePaths(DataModel):
    """Every path the database manager owns; all of them sit directly under ``root``."""

    root: Path
    before_tree: Path
    after_tree: Path
    before_db: Path
    after_db: Path
    state: Path
    graphs: Path

    @classmethod
    def for_repo(
        cls, common_dir: Path, db_location: DbLocation = "cache", cache_dir: Path | None = None
    ) -> Self:
        """Build the cache layout of one repository (req 2.1, 2.2, 2.8)."""
        root = cache_root(common_dir, db_location, cache_dir)
        return cls(
            root=root,
            before_tree=root / "before",
            after_tree=root / "after",
            before_db=root / "before.und",
            after_db=root / "after.und",
            state=root / "state.json",
            graphs=root / "graphs",
        )


class SyncState(DataModel):
    """``state.json``: what the shadows currently hold, so a sync stays incremental (2.3).

    ``after_tree_id`` is the ``git write-tree`` id for the index, a content hash for the
    working tree and the commit hash for a commit target; ``created_with`` is the Understand
    version that built the databases, which invalidates them when it changes.

    ``parse_errors`` is the one field here that is **not** an optimisation, and the
    difference decides how a failure to read this file has to be handled. Every other field
    is a cache key: losing it costs a full re-sync and nothing else, which is why
    :meth:`~scitools_hook.understand.database.DatabaseManager._load_state` treats an
    unreadable state as an empty one. A parse error, though, is a property of the *database*
    and not of the last ``und analyze`` -- the run that did the parsing reports it, and every
    warm run afterwards analyses nothing and would report a clean parse of a file it never
    read (measured in task 10.4: a cold run named 9 unparsed files, three consecutive warm
    runs named none). Losing this field therefore under-reports rather than over-costs. It
    stays here because it is written next to the databases it describes and is discarded with
    them, and a lost state file forces the full analysis that produces the errors again --
    so the failure is self-healing in the one direction that matters.
    """

    after_target: SyncTargetKind | None = None
    after_tree_id: str | None = None
    before_commit: str | None = None
    languages: list[str] = Field(default_factory=list)
    created_with: str = ""
    parse_errors: dict[Side, list[ParseError]] = Field(default_factory=dict)
    """What each side's database could not read, carried between runs (req 2.6, task 11.13)."""

    def record_parse_errors(
        self,
        side: Side,
        tree: Path,
        found: Sequence[ParseError],
        reread: Collection[Path] | None,
    ) -> list[ParseError]:
        """Update one side's record after an analysis, and answer with all of it (task 11.13).

        ``found`` is what the ``und analyze`` that just ran reported and ``reread`` the files
        it actually opened -- ``None`` for a pass that read the whole project. Three rules,
        each of them a different way of getting this wrong:

        * A **full** pass re-read everything, so its answer is the complete new truth and the
          previous record goes entirely -- otherwise an error in a file since fixed, or since
          deleted, would be reported forever.
        * A **selective** pass re-read the files it named, so the previous entries *for those
          files* go (a fix clears them) and every other entry is carried forward untouched.
          That carry-forward is the whole of 11.13: a warm run re-parses nothing, so its
          silence is not evidence that the project now parses.
        * The two are merged **de-duplicated and in order**, carried-forward first. An error in
          both lists is one coverage loss, and within a file the first error is the cause and
          the rest are the cascade it set off, which is the order the report prints.

        Paths are made relative to ``tree`` here, and this is the only place that happens: see
        :func:`_inside` for why there is no ``realpath`` fallback.
        """
        opened = None if reread is None else {_inside(path, tree) for path in reread}
        kept = (
            []
            if opened is None
            else [error for error in self.parse_errors.get(side, []) if error.path not in opened]
        )
        fresh = [error.model_copy(update={"path": _inside(error.path, tree)}) for error in found]
        errors = _distinct([*kept, *fresh])
        self.parse_errors[side] = errors
        return errors

    def forget_parse_errors(self) -> None:
        """Drop both sides' records, for a run that discarded both databases.

        A record of a database that no longer exists would be re-reported forever -- and what
        a database built by a *different* Understand could not read is not evidence about the
        one that replaces it.
        """
        self.parse_errors.clear()


def _inside(path: Path, tree: Path) -> Path:
    """``path`` named relative to the shadow ``tree``, or unchanged when it lies outside it.

    Both answers are meaningful and the caller has to be able to tell them apart: a path under
    the shadow is a file of this repository, comparable with an ``EntityKey``'s and with a
    run's selection, while one outside it is something Understand read on its own account --
    the interpreter's standard library, where task 10.4 measured four parse errors on a clean
    run of this project, and which no commit here can fix.

    There is no ``realpath`` fallback, and that is a decision rather than an omission:
    measured, ``und`` records files under their **resolved** path, so a shadow root reached
    through a symlink already fails loudly in the snapshot extractor (``no file of <db> is
    under the analysis root``) before anything reaches here. Resolving would only move that
    failure somewhere quieter.
    """
    return path.relative_to(tree) if path.is_relative_to(tree) else path


def _distinct(errors: Sequence[ParseError]) -> list[ParseError]:
    """``errors`` with each (path, line, message) kept once, in the order first seen."""
    seen: set[tuple[str, int | None, str]] = set()
    kept: list[ParseError] = []
    for error in errors:
        token = (error.path.as_posix(), error.line, error.message)
        if token not in seen:
            seen.add(token)
            kept.append(error)
    return kept
