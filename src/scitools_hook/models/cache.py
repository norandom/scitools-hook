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
from typing import Final, Literal, Self

from platformdirs import user_cache_dir
from pydantic import Field

from scitools_hook.config.models import DbLocation
from scitools_hook.models.git import SyncTargetKind
from scitools_hook.models.snapshot import DataModel, ParseError, Side

CACHE_SCHEMA: Final = 1
"""The layout of ``state.json``. A state that does not carry this number is discarded.

Bumped by the understand-8-features specification, which added the before route, the analysis
fingerprint, the per-side accuracy and the generated-architecture stamps. A state written
before them cannot answer any of those questions, and reading it anyway would leave a run
believing its before database was built from a commit it was not -- a stale-cache bug that
reports the wrong findings and exits 0. Absent reads as ``0``, so an old file is stale by
construction; :meth:`SyncState.stale_layout` is what asks.
"""

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


BeforeRoute = Literal["shadow", "commit"]
"""How a before database came to exist. ``auto`` is a *setting*; this is what actually happened."""


class SnapshotEntry(DataModel):
    """One stored snapshot document, as ``doctor`` lists it (requirement 8.6).

    A model rather than a dataclass in the adapter because the CLI prints it and may not
    import ``understand`` -- the layer rule in ``tests/test_import_direction.py``. It carries
    an age rather than a timestamp because the only question anyone asks of it is whether the
    cache is doing anything.
    """

    name: str
    seconds: float
    bytes: int


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
    schema_version: int = 0
    """The cache layout this state was written by; ``0`` is "before the field existed"."""

    before_route: BeforeRoute | None = None
    """How the before database was built: an exported shadow tree, or the base commit (3.6)."""

    analysis_settings: str = ""
    """The analysis fingerprint the databases were built under (req 3.5).

    Empty means "not recorded", which reads as a miss. It is the settings half of the
    before-database key: the commit alone does not decide what a database holds, because the
    language set, the file selection and the architecture all do too.
    """

    accuracy: dict[Side, float] = Field(default_factory=dict)
    """Each side's last reported accuracy (req 7.1, 7.2).

    Recorded rather than recomputed because a warm run analyses nothing on a side whose
    commit has not moved -- there is no ``und analyze`` to report a figure, and the last one
    is still true of the database that is still there.
    """

    generated_archs: dict[str, str] = Field(default_factory=dict)
    """Generated architecture name -> the repository head and after tree id it was built from.

    The skip rule of requirement 4.4: a git-derived architecture is regenerated when either
    has moved and left alone when neither has.
    """
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

        Paths are made relative to ``tree`` here, and this is the only place that happens; it is
        also where an error **outside** the shadow is dropped, for the reason :func:`_inside`
        gives. Both together are why every consumer downstream -- the run's reported parse
        errors, the blocking ``analysis.parse_error`` finding, the snapshot's
        ``unparsed_files`` -- sees one already-normalised, already-filtered list and none of
        them repeats the judgement.
        """
        opened = None if reread is None else {_inside(path, tree) for path in reread}
        kept = (
            []
            if opened is None
            else [error for error in self.parse_errors.get(side, []) if error.path not in opened]
        )
        fresh = [
            error.model_copy(update={"path": relative})
            for error in found
            if (relative := _inside(error.path, tree)) is not None
        ]
        errors = _distinct([*kept, *fresh])
        self.parse_errors[side] = errors
        return errors

    def stale_layout(self) -> bool:
        """Whether this state was written by a different cache layout and must be discarded."""
        return self.schema_version != CACHE_SCHEMA

    def forget_parse_errors(self) -> None:
        """Drop both sides' records, for a run that discarded both databases.

        A record of a database that no longer exists would be re-reported forever -- and what
        a database built by a *different* Understand could not read is not evidence about the
        one that replaces it.
        """
        self.parse_errors.clear()


def _inside(path: Path, tree: Path) -> Path | None:
    """``path`` named relative to the shadow ``tree``, or ``None`` when it lies outside it.

    A path under the shadow is a file of this repository, comparable with an
    :class:`~scitools_hook.models.snapshot.EntityKey`'s and with a run's selection. **A path
    outside it is not the operator's file and is dropped.** Measured on one real run: 63 parse
    errors under ``~/.local/share/uv/python/cpython-3.14.4/.../typing.py``, ``pdb.py`` and
    ``_pyrepl`` -- the interpreter's own standard library, enrolled by Understand's
    ``use_installed_standard`` and surviving task 11.10's interpreter pin, which removes
    ``site-packages`` and not the stdlib. Nobody can fix ``typing.py`` from this repository, so
    every line of that is noise in a report whose whole value is that its lines can be acted
    on.

    Task 11.11 already made such an error non-blocking, and non-blocking turned out not to be
    enough: it still printed. Dropping it is deliberately **not** extended one step further --
    an error for a file *inside* the shadow stays, loudly, whether or not this run selected it,
    because that is the false negative 11.11 exists to prevent: the analysis stops where the
    parse stops and every rule below it then reports success over code it never read.

    Only an **absolute** path can be outside, and only an absolute path is ever dropped. A
    relative one is already in the repository-relative form everything downstream speaks, has
    no shadow root to be measured against, and cannot be the standard library -- Understand
    names a file it read on its own account by its absolute path. Judging it would silence a
    real error over a spelling.

    There is no ``realpath`` fallback, and that is a decision rather than an omission:
    measured, ``und`` records files under their **resolved** path, so a shadow root reached
    through a symlink already fails loudly in the snapshot extractor (``no file of <db> is
    under the analysis root``) before anything reaches here. Resolving would only move that
    failure somewhere quieter.
    """
    if not path.is_absolute():
        return path
    return path.relative_to(tree) if path.is_relative_to(tree) else None


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
