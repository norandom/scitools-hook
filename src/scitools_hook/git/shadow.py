"""Materialise the index, the working tree or a commit into the cache shadows (task 7.2).

The Gate analyses a *copy* of the repository, never the repository itself: a shadow tree per
side under :class:`~scitools_hook.models.cache.CachePaths`, kept up to date one change at a
time so that a second run re-analyses only what moved (requirements 2.2, 2.3, 2.5, 4.1, 4.3,
10.5). Everything below is written against a **measured** git 2.43.0, because three of its
behaviours decide the shape of this module:

* **A gitlink is exported as an empty directory.** Measured in a superproject:
  ``ls-files`` names ``sm``, and ``checkout-index`` with ``sm`` on stdin exits **0** while
  creating ``sm/`` with nothing in it. A sync that decided what to do by diffing "tracked"
  against "present in the shadow" would therefore see ``sm`` missing on every single run --
  a permanent phantom -- and ``und add`` on an empty directory would do nothing about it.
* **A conflicted index exports a hole, silently.** ``checkout-index`` exits 0 and writes
  *nothing* for an unmerged path. :meth:`~scitools_hook.git.repo.GitRepo.index_tree_id` is
  therefore asked first, on every index sync: it exits 128 during a conflict, so the run
  stops instead of analysing a change with its central file missing.
* **``git gc`` can prune the tree id this module recorded.** ``write-tree`` writes an
  unreachable tree; measured, it survives ``gc`` while the index still points at it and is
  pruned once the index moves, after which ``git diff`` against it exits 128 with ``fatal:
  bad object``. Routine housekeeping must not break the hook, so an unresolvable recorded id
  falls back to a full re-sync rather than raising.

Both of the first two are handled by **one** rule rather than by two special cases: the sync
decides what it achieved by looking at what is actually in the shadow afterwards, never by
trusting what it asked for. A gitlink leaves a directory, which is not a file, so it enters
no delta list and is pruned; an unmerged path leaves nothing at all, with the same result.
That is this project's own standing rule -- guard the outcome you require, not the failure
you predicted -- applied to a filesystem instead of an exception type.

**Deciding whether a sync can be incremental.** The recorded id must resolve to a git object,
which is why a symbolic revision is never reused as a cache key: ``HEAD`` names a different
commit tomorrow, so a run that trusted it would analyse a stale shadow and report nothing.
Only a full object id (40 or 64 lowercase hex characters) is accepted, and the working tree's
own state id is deliberately prefixed ``worktree-`` so that a content digest can never be
mistaken for a sha256 commit on the before side, which records no target kind of its own.

**Include and exclude patterns** (requirement 2.5) are applied at exactly one place,
:class:`PathFilter`, consulted by every target and again by the sweep that ends every sync.
That sweep is what makes exclusion an invariant rather than a decision taken once: a pattern
added between two runs removes what it now excludes, and reports it as deleted so the
database can drop it too. The commit target exports the whole tree before the sweep, because
git offers no way to list a commit's paths through the wrapper this module is allowed to use;
excluded bytes therefore land in the cache for the duration of one full sync, never in the
working tree, and never in the database.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Final

from scitools_hook.config.models import ProjectSettings
from scitools_hook.errors import AnalysisFailedError
from scitools_hook.git.repo import GitRepo
from scitools_hook.models.cache import CachePaths, SyncState
from scitools_hook.models.git import CommitTarget, IndexTarget, SyncDelta, SyncTarget
from scitools_hook.models.snapshot import Side
from scitools_hook.paths import classify_directory

SHADOW_MODE: Final = 0o700
"""Shadow trees hold the repository's source; only their owner may read them."""

COMPARE_CHUNK: Final = 1 << 16
"""How much of two files is read at a time when deciding whether they still agree."""

OBJECT_ID: Final = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
"""A full sha1 or sha256 object id -- the only shape accepted as an incremental cache key."""

WORKTREE_PREFIX: Final = "worktree-"
"""Keeps a working-tree state id out of the object-id shape (see the module docstring)."""

DELETING_STATUSES: Final = frozenset({"D", "R"})
"""The statuses whose *old* path leaves the shadow; a rename deletes as well as adds."""


class PathFilter:
    """Which repository-relative paths belong in a shadow (requirement 2.5).

    The language is git's, narrowed to what a configuration file needs:

    * ``**`` matches any number of path segments, ``*`` and ``?`` never cross ``/``.
    * A pattern may match at **any** directory level unless it starts with ``/``, which
      anchors it to the repository root. Without that rule the shipped defaults would be a
      lie: ``node_modules/**`` would leave ``packages/web/node_modules`` in the analysis, and
      requirement 2.5 promises dependency directories are excluded, not that the ones at the
      top are.
    * A pattern that matches a directory matches everything under it, so ``build`` excludes
      ``build/out.o`` without needing a wildcard.
    * Everything else is literal. There are no character classes: ``[ab].py`` names a file
      called ``[ab].py``. An empty pattern matches nothing rather than everything, because a
      stray blank entry in a list must not switch the whole project off.

    A path is kept when it matches at least one include pattern and no exclude pattern. An
    empty include list is read literally and selects nothing; the shipped default is ``**``.
    """

    def __init__(self, include: Sequence[str], exclude: Sequence[str]) -> None:
        """Compile the two lists; ``if pattern`` is a **known equivalent mutant**, kept anyway.

        Measured rather than argued: an empty pattern compiles to ``(?:.*/)?``, which matches
        only the empty string and a path ending in ``/`` -- and no path reaching
        :meth:`allows` is either, since git never emits a trailing separator and
        :func:`_prefixes` never adds one. Dropping the guard therefore changes no answer. It
        stays because "a blank entry selects nothing" is a promise worth stating at the point
        it is kept, rather than one that happens to fall out of a regular expression.
        """
        self.include = tuple(_compile(pattern) for pattern in include if pattern)
        self.exclude = tuple(_compile(pattern) for pattern in exclude if pattern)

    @classmethod
    def from_settings(cls, project: ProjectSettings) -> PathFilter:
        """Build the filter one repository's configuration asks for."""
        return cls(project.include, project.exclude)

    def allows(self, rel: str) -> bool:
        """Whether the repository-relative POSIX path ``rel`` belongs in the shadow."""
        prefixes = _prefixes(rel)
        return _matches(self.include, prefixes) and not _matches(self.exclude, prefixes)


class ShadowSync:
    """Keep one repository's shadow trees equal to the index, the working tree or a commit."""

    def __init__(self, repo: GitRepo, paths: CachePaths, project: ProjectSettings) -> None:
        self.repo = repo
        self.paths = paths
        self.filter = PathFilter.from_settings(project)

    def sync(self, side: Side, target: SyncTarget, state: SyncState) -> SyncDelta:
        """Bring one shadow up to date with ``target`` and say what moved.

        ``state`` is updated in place with the new target kind and state id; the caller owns
        writing it back to ``state.json``. The returned :class:`SyncDelta` is what 8.1 drives
        ``und add``, ``und remove`` and ``analyze -files`` from, so every path it names was
        checked to be really there afterwards.

        The order of the first two steps is load-bearing on the index side: the state id is
        asked for *before* anything is exported, because that is the call that refuses a
        conflicted index (see the module docstring).

        ``fresh`` deliberately does **not** test the recorded id for ``None``: whether an id
        can be diffed against is :func:`_reusable`'s single decision, taken a few lines below,
        and a second copy of it here would be a second place to get it wrong. Measured as a
        mutant: adding the term back changes no test, because a missing id is not reusable.
        """
        dest = self.paths.before_tree if side == "before" else self.paths.after_tree
        recorded_kind, recorded_id = self._recorded(side, state)
        fresh = recorded_kind != target.kind or not _populated(dest)
        if target.kind == "worktree":
            delta, new_id = self._sync_worktree(dest, fresh)
        else:
            new_id = self._identify(target)
            _ensure(dest)
            if fresh or not _reusable(recorded_id) or not _reusable(new_id):
                delta = self._full(dest, target)
            else:
                delta = self._incremental(dest, target, str(recorded_id), new_id)
        self._record(side, state, target, new_id)
        return delta

    # --- deciding what to do ----------------------------------------------------

    def _recorded(self, side: Side, state: SyncState) -> tuple[str | None, str | None]:
        """What this shadow was last synced from: a target kind and a state id.

        The before shadow is always a commit and :class:`SyncState` keeps no kind for it, so
        the kind is supplied here. That is safe because the two ids are comparable -- ``git
        diff`` reads a tree id and a commit interchangeably -- and because the working tree's
        id is prefixed, so a before shadow that was once synced from a working tree falls
        back to a full re-sync instead of diffing a digest against a commit.
        """
        if side == "before":
            return "commit", state.before_commit
        return state.after_target, state.after_tree_id

    def _record(self, side: Side, state: SyncState, target: SyncTarget, new_id: str) -> None:
        """Store what this shadow now holds, for the next run to compare against."""
        if side == "before":
            state.before_commit = new_id
        else:
            state.after_target = target.kind
            state.after_tree_id = new_id

    def _identify(self, target: IndexTarget | CommitTarget) -> str:
        """The state id of a target that git can name: a tree id, or the commit itself."""
        if target.kind == "index":
            return self.repo.index_tree_id()
        return target.commit

    # --- index and commit targets -----------------------------------------------

    def _full(self, dest: Path, target: IndexTarget | CommitTarget) -> SyncDelta:
        """Materialise the whole target from scratch, discarding whatever was there."""
        _clear(dest)
        _ensure(dest)
        self._export(dest, target, None)
        _, kept = self._sweep(dest)
        return SyncDelta(added=kept, full=True)

    def _incremental(
        self, dest: Path, target: IndexTarget | CommitTarget, recorded: str, current: str
    ) -> SyncDelta:
        """Apply the difference between the recorded state and the current one.

        Deletions are applied before exports, which is what lets a path change kind without a
        special case: ``foo.py`` becoming ``foo/__init__.py`` (and the reverse) is a delete
        and an add, and the delete has already made room by the time the add runs.

        An unresolvable recorded id is not an error but a missing cache key -- ``git gc``
        prunes the tree ``write-tree`` recorded once the index moves (measured) -- so the
        answer is a full re-sync, which is correct however the diff failed.
        """
        try:
            changes = self.repo.diff_names(recorded, current)
        except AnalysisFailedError:
            return self._full(dest, target)
        gone = dict.fromkeys(
            change.old_path or change.path
            for change in changes
            if change.status in DELETING_STATUSES
        )
        wanted = dict.fromkeys(
            change.path
            for change in changes
            if change.status != "D" and self.filter.allows(change.path)
        )
        deleted = {rel for rel in gone if _remove_file(dest / rel)}
        existed = {rel for rel in wanted if _kind(dest / rel) in _FILE_KINDS}
        self._export(dest, target, sorted(wanted))
        swept, kept = self._sweep(dest)
        present = set(kept)
        return SyncDelta(
            added=sorted(rel for rel in wanted if rel in present and rel not in existed),
            modified=sorted(rel for rel in wanted if rel in present and rel in existed),
            deleted=sorted(deleted | set(swept)),
        )

    def _export(
        self, dest: Path, target: IndexTarget | CommitTarget, paths: list[str] | None
    ) -> None:
        """Hand the export to the plumbing wrapper, which owns every ``git`` call."""
        if target.kind == "index":
            self.repo.export_index(dest, paths)
        else:
            self.repo.export_commit(target.commit, dest, paths)

    # --- the worktree target (requirement 10.5) ---------------------------------

    def _sync_worktree(self, dest: Path, fresh: bool) -> tuple[SyncDelta, str]:
        """Copy the tracked and staged-new files as they are on disk, so an agent can pre-check.

        There is no ref to diff against, so "what changed" is answered by comparing bytes with
        what the shadow already holds. That costs a read of each candidate and buys an answer
        that depends on nothing else: a filesystem whose timestamps are coarse, or a copy that
        preserved an mtime, cannot make an edited file look unchanged.
        """
        if fresh:
            _clear(dest)
        _ensure(dest)
        candidates = self._worktree_candidates()
        swept, present = self._sweep(dest)
        deleted = {rel for rel in present if rel not in candidates and _remove_file(dest / rel)}
        added: list[str] = []
        modified: list[str] = []
        for rel, info in candidates.items():
            outcome = _refresh(self.repo.root / rel, dest / rel, info)
            if outcome == "added":
                added.append(rel)
            elif outcome == "modified":
                modified.append(rel)
        _prune_empty(dest)
        delta = SyncDelta(
            added=sorted(added),
            modified=sorted(modified),
            deleted=sorted(deleted | set(swept)),
            full=fresh,
        )
        return delta, _worktree_id(candidates)

    def _worktree_candidates(self) -> dict[str, os.stat_result]:
        """The tracked and staged-new paths that exist on disk as a file or a link.

        A gitlink is a real directory full of another repository's files, and an unstaged
        deletion leaves nothing to copy; both are simply not candidates. Anything else that
        cannot be reached is raised rather than dropped, because a source file the Gate
        cannot read must not look like a source file the change deleted.
        """
        found: dict[str, os.stat_result] = {}
        for rel in self.repo.tracked_files():
            if not self.filter.allows(rel):
                continue
            source = self.repo.root / rel
            try:
                info = os.lstat(source)
            except FileNotFoundError:
                continue
            except OSError as unreachable:
                raise AnalysisFailedError(
                    f"the working-tree file {rel} could not be read: {unreachable}",
                    hint="Fix the file's permissions, or exclude it in [project].exclude.",
                ) from unreachable
            if stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
                found[rel] = info
        return found

    # --- keeping the shadow honest ----------------------------------------------

    def _sweep(self, dest: Path) -> tuple[list[str], list[str]]:
        """Delete what the filter no longer allows and report what is really there.

        Two jobs in one walk, because they need the same information. The kept list is how
        every caller checks its own work: a path that was exported but is not in it was never
        materialised -- a gitlink, or an unmerged path -- and belongs in no delta list.
        """
        removed: list[str] = []
        kept: list[str] = []
        for path in _walk_files(dest):
            rel = path.relative_to(dest).as_posix()
            if self.filter.allows(rel):
                kept.append(rel)
            else:
                _remove_file(path)
                removed.append(rel)
        _prune_empty(dest)
        return sorted(removed), sorted(kept)


# --- pattern helpers ----------------------------------------------------------------


def _compile(pattern: str) -> re.Pattern[str]:
    """Turn one configured pattern into a regular expression over a relative POSIX path."""
    anchored = pattern.startswith("/")
    body = _translate(pattern.lstrip("/").rstrip("/"))
    return re.compile(body if anchored else f"(?:.*/)?{body}")


def _translate(pattern: str) -> str:
    """Expand the four metacharacters and escape everything else.

    ``**/`` and ``/**`` are handled as units so that ``**`` spans whole segments while a lone
    ``*`` stays inside one; every other character is escaped, which is what makes the language
    literal rather than a regular expression the operator did not ask for.
    """
    out: list[str] = []
    at = 0
    while at < len(pattern):
        if pattern.startswith("**/", at):
            out.append("(?:.*/)?")
            at += 3
        elif pattern.startswith("**", at):
            out.append(".*")
            at += 2
        elif pattern[at] == "*":
            out.append("[^/]*")
            at += 1
        elif pattern[at] == "?":
            out.append("[^/]")
            at += 1
        else:
            out.append(re.escape(pattern[at]))
            at += 1
    return "".join(out)


def _prefixes(rel: str) -> list[str]:
    """``a/b/c.py`` as ``["a", "a/b", "a/b/c.py"]`` -- the path and every directory above it."""
    parts = rel.split("/")
    return ["/".join(parts[: at + 1]) for at in range(len(parts))]


def _matches(patterns: Sequence[re.Pattern[str]], prefixes: Sequence[str]) -> bool:
    """Whether any pattern matches the path or one of the directories containing it."""
    return any(pattern.fullmatch(prefix) for pattern in patterns for prefix in prefixes)


# --- filesystem helpers -------------------------------------------------------------


_FILE_KINDS: Final = frozenset({"file", "link"})
"""What counts as materialised: a regular file, or the symbolic link git checked out."""


def _reusable(state_id: str | None) -> bool:
    """Whether a recorded state id can serve as a cache key (see the module docstring)."""
    return state_id is not None and OBJECT_ID.fullmatch(state_id) is not None


def _kind(path: Path) -> str:
    """What ``path`` is, without following a link: file, link, dir, other or absent."""
    try:
        info = os.lstat(path)
    except (OSError, ValueError):
        return "absent"
    if stat.S_ISLNK(info.st_mode):
        return "link"
    if stat.S_ISDIR(info.st_mode):
        return "dir"
    if stat.S_ISREG(info.st_mode):
        return "file"
    return "other"


def _populated(dest: Path) -> bool:
    """Whether a shadow exists and holds anything, so a delta can be applied to it.

    A directory that is gone, or empty because something wiped the cache, cannot be brought
    up to date one change at a time -- the result would be a tree missing everything that did
    not happen to change. A path that exists and is not a directory is a broken cache layout
    and is raised, not worked around.
    """
    verdict = classify_directory(dest)
    if verdict.absent:
        return False
    if not verdict.usable:
        raise AnalysisFailedError(
            f"the shadow tree {dest} {verdict.reason}",
            hint="Remove whatever is at that path, or point the cache somewhere else.",
        )
    with os.scandir(dest) as entries:
        return next(entries, None) is not None


def _ensure(dest: Path) -> None:
    """Create the shadow root, owner-readable only, turning a refusal into a typed error."""
    try:
        dest.mkdir(mode=SHADOW_MODE, parents=True, exist_ok=True)
    except OSError as broken:
        raise AnalysisFailedError(
            f"the shadow tree {dest} could not be created: {broken}",
            hint="Point the cache at a writable directory, or remove what is in the way.",
        ) from broken


def _clear(dest: Path) -> None:
    """Remove a shadow entirely, so a full sync cannot inherit a stale file."""
    kind = _kind(dest)
    try:
        if kind == "dir":
            shutil.rmtree(dest)
        elif kind != "absent":
            os.unlink(dest)
    except OSError as broken:
        raise AnalysisFailedError(
            f"the shadow tree {dest} could not be cleared: {broken}",
            hint="Check the cache directory's permissions, or remove it by hand.",
        ) from broken


def _remove_file(path: Path) -> bool:
    """Delete one shadow entry, answering whether there was a file or link there to delete."""
    if _kind(path) not in _FILE_KINDS:
        return False
    try:
        os.unlink(path)
    except OSError as broken:
        raise AnalysisFailedError(
            f"the shadow file {path} could not be removed: {broken}",
            hint="Check the cache directory's permissions.",
        ) from broken
    return True


def _walk_files(dest: Path) -> Iterator[Path]:
    """Every file and link under ``dest``, iteratively.

    Iterative because it costs nothing and removes the depth question rather than answering
    it. The honest measurement, so the comment is not a bigger claim than the evidence: a
    recursive ``scandir`` walk handled a 600-level tree here without complaint, and a
    1500-level one could not be created at all -- ``mkdir`` ran out of path before Python ran
    out of stack. So this is insurance, not a fix for an observed failure; task 4.3 made the
    same choice for Tarjan, where the depth was reachable.
    """
    for here in _walk_dirs(dest):
        with os.scandir(here) as entries:
            for entry in entries:
                if not entry.is_dir(follow_symlinks=False):
                    yield Path(entry.path)


def _walk_dirs(dest: Path) -> list[Path]:
    """``dest`` and every directory under it, parents before children."""
    order: list[Path] = []
    pending = [dest]
    while pending:
        here = pending.pop()
        order.append(here)
        with os.scandir(here) as entries:
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
    return order


def _prune_empty(dest: Path) -> None:
    """Remove every empty directory under ``dest``, children first.

    Git cannot track an empty directory, so one in a shadow is always residue: the directory
    a deletion emptied, or the one ``checkout-index`` leaves behind for a gitlink. Understand
    would keep listing it. A directory that refuses to go is left alone -- an empty directory
    is harmless, and failing the sync over one would not be.
    """
    for here in reversed(_walk_dirs(dest)):
        if here == dest:
            continue
        with os.scandir(here) as entries:
            if next(entries, None) is not None:
                continue
        try:
            os.rmdir(here)
        except OSError:
            continue


def _refresh(source: Path, shadow: Path, info: os.stat_result) -> str:
    """Copy one working-tree entry into the shadow if it is not already there unchanged.

    Answers ``added``, ``modified`` or ``same``; ``same`` means nothing was written, which is
    what keeps a second run from re-analysing the whole project.
    """
    existing = _kind(shadow)
    if stat.S_ISLNK(info.st_mode):
        target = os.readlink(source)
        if existing == "link" and os.readlink(shadow) == target:
            return "same"
        _replace(shadow, existing, lambda: os.symlink(target, shadow))
    else:
        if existing == "file" and _same_bytes(source, shadow):
            return "same"
        _replace(shadow, existing, lambda: shutil.copyfile(source, shadow))
    return "modified" if existing in _FILE_KINDS else "added"


def _replace(shadow: Path, existing: str, write: Callable[[], object]) -> None:
    """Put something new at ``shadow``, clearing whatever kind of thing was there first."""
    try:
        if existing == "dir":
            shutil.rmtree(shadow)
        elif existing != "absent":
            os.unlink(shadow)
        shadow.parent.mkdir(parents=True, exist_ok=True)
        write()
    except OSError as broken:
        raise AnalysisFailedError(
            f"the working-tree copy of {shadow.name} failed: {broken}",
            hint="Check the cache directory's permissions and free space.",
        ) from broken


def _same_bytes(source: Path, shadow: Path) -> bool:
    """Whether two files hold the same bytes; ``False`` whenever that cannot be established.

    Sizes are compared first because they usually settle it, and the comparison is written
    out rather than taken from :func:`filecmp.cmp`, which caches its answer against
    ``(kind, size, st_mtime)`` -- read from the installed standard library, and ``st_mtime``
    is a float of seconds, not ``st_mtime_ns``. A same-size edit landing inside one tick of
    that clock would be answered from the cache, and the file would never be re-copied.
    """
    try:
        if source.stat().st_size != shadow.stat().st_size:
            return False
        with open(source, "rb") as left, open(shadow, "rb") as right:
            while True:
                chunk = left.read(COMPARE_CHUNK)
                if chunk != right.read(COMPARE_CHUNK):
                    return False
                if not chunk:
                    return True
    except OSError:
        return False


def _worktree_id(candidates: dict[str, os.stat_result]) -> str:
    """A state id for the working tree, which has no ref to name it.

    Recorded so ``doctor`` and the database manager can see what the shadow was built from;
    the sync itself never compares it, because the per-file byte comparison is what decides
    whether anything moved. The ``worktree-`` prefix keeps it out of the object-id shape, so
    it can never be diffed against as if it were a commit.
    """
    digest = hashlib.sha256(usedforsecurity=False)
    for rel in sorted(candidates):
        info = candidates[rel]
        digest.update(f"{rel}\0{info.st_size}\0{info.st_mtime_ns}\0".encode())
    return f"{WORKTREE_PREFIX}{digest.hexdigest()}"
