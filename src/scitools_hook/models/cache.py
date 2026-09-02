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
from pathlib import Path
from typing import Final, Self

from platformdirs import user_cache_dir
from pydantic import Field

from scitools_hook.config.models import DbLocation
from scitools_hook.models.git import SyncTargetKind
from scitools_hook.models.snapshot import DataModel

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
    """

    after_target: SyncTargetKind | None = None
    after_tree_id: str | None = None
    before_commit: str | None = None
    languages: list[str] = Field(default_factory=list)
    created_with: str = ""
