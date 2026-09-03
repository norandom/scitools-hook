"""The spine every runner pipeline stands on: what a run covers, and how it is observed.

``check`` and ``explain`` ask different questions of the same machinery. Both start from a
selection (or, for ``explain``, a commit range), reduce it to the paths Understand can parse,
sync and analyse one or two shadows, and read the two sides back as snapshots joined by
:class:`~scitools_hook.models.snapshot.EntityKey`. Only what happens *after* that differs:
one evaluates rules, the other builds a review document.

This module exists because task 8.3 recorded, in the implementation notes, that
``runner/check`` deliberately exported none of it and that a second pipeline needing the same
steps must **promote** them rather than copy them -- the 8.2 lesson about a helper that ends
up in five places and is then fixed in four. Three of the pieces below are exactly the ones
8.3 named as handoffs, and each is load-bearing for a reason a copy would eventually lose:

* :func:`analysable` decides whether a run happens at all. A selection holding nothing
  Understand can parse must stop *before* any shadow is synced, because
  ``DatabaseManager.ensure_side`` raises ``AnalysisFailedError`` (exit 5) for a repository
  with no analysable file -- so a README-only commit, or a README-only commit range, would
  become a hard failure of the tool rather than "nothing was analyzed". The predicate is
  shared; the *answer* is not, because a check run answers with an empty ``RunResult`` and an
  explain run with an empty ``ChangeSummary``.
* :func:`touched` keeps the old path of a rename and the path of a deletion in the file set.
  Requirement 4.10 wants the structural rules to run on what a deletion leaves behind, and
  requirement 9.1 wants the deleted file listed in the summary; both need the deleted path to
  count towards "there is something to analyse".
* :meth:`Engine.observe` extracts twice per side. The first pass asks only about the selected
  files, which is all the affected-set resolver needs; the second asks about the affected
  files *and their neighbourhood*, which is what the rules and the dependency deltas have to
  see one step past the change. Bounding the second pass is what keeps the cost proportional
  to the change rather than to the repository (req 4.11).

:meth:`Engine.extract` roots every extraction at the very object ``und add`` was given,
unresolved: the worker makes each entity's long name relative to it, and a root the database
never saw answers with a valid, empty, entirely green document (live finding, 6.2).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final, Literal, TypeVar

from pydantic import Field

from scitools_hook.analysis.affected import resolve
from scitools_hook.errors import ConfigError
from scitools_hook.git.repo import GitRepo
from scitools_hook.models.change import AffectedSet
from scitools_hook.models.git import (
    CommitTarget,
    IndexTarget,
    StagedChange,
    SyncTarget,
    WorktreeTarget,
)
from scitools_hook.models.progress import Progress
from scitools_hook.models.snapshot import DataModel, ProjectSnapshot, Side
from scitools_hook.models.understand import AnalyzeResult
from scitools_hook.understand.database import LANGUAGE_BY_SUFFIX, DatabaseManager
from scitools_hook.understand.snapshot import SnapshotExtractor, SnapshotTarget

# Written as an explicit ``TypeVar`` rather than PEP 695 ``[T]`` syntax: Understand 6.5
# cannot parse a type-parameter list, and one such declaration costs the rest of the file
# from the analysis (measured in task 10.4).
T = TypeVar("T")
"""Whatever the timed phase returns; the timing wrapper is agnostic to it."""

SelectionMode = Literal["staged", "worktree", "all", "files"]
"""The four mutually exclusive things a run can be pointed at (req 12.3)."""

PlanMode = SelectionMode | Literal["range"]
"""What a plan covers. ``explain --range`` is a fifth shape and deliberately **not** a
selection mode: requirement 12.3's four flags are mutually exclusive and a commit range is
none of them, so it stays out of ``Selection`` and out of the CLI's option group."""

OUTSIDE_HINT: Final = "Name files by their path inside the repository, as git reports them."
"""What an operator does about a ``--files`` entry that names nothing in the working tree."""


class Selection(DataModel):
    """What one run covers: a mode, and the file list the ``files`` mode carries (req 12.3)."""

    mode: SelectionMode
    files: list[str] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class AnalysisPlan:
    """What a run will analyse, decided from git alone before any Understand work starts."""

    mode: PlanMode
    changes: tuple[StagedChange, ...]
    files: frozenset[str]
    target: SyncTarget
    before: str | None
    """The resolved commit the before side is synced from, or ``None`` when there is none."""


def touched(changes: Iterable[StagedChange]) -> list[str]:
    """Every path a change names, the old side of a rename and the deletions included."""
    paths: list[str] = []
    for change in changes:
        paths.append(change.path)
        if change.old_path is not None:
            paths.append(change.old_path)
    return paths


def analysable(paths: Iterable[str], languages: Sequence[str] | None) -> frozenset[str]:
    """The paths whose extension Understand enrols under an enabled language (req 2.4).

    Configured languages narrow the set, matched case-insensitively: the database manager
    compares the configured names exactly, and answering "analysable" for a spelling it
    would reject only costs a run that finds nothing, while the opposite would skip a
    check the operator asked for.
    """
    enabled = {name.casefold() for name in (languages or ())}
    found = {
        path
        for path in paths
        if (language := LANGUAGE_BY_SUFFIX.get(PurePosixPath(path).suffix)) is not None
        and (not enabled or language.casefold() in enabled)
    }
    return frozenset(found)


def inside(name: str, root: Path) -> str:
    """``name`` as a repository-relative path, or the typed refusal it deserves.

    A ``--files`` entry that names nothing inside the working tree matches no entity key, so
    the run would evaluate nothing and report success -- the exact silent green this project
    keeps meeting. It is refused instead, naming the path and the repository.
    """
    candidate = Path(name)
    if not candidate.is_absolute():
        return PurePosixPath(name).as_posix()
    try:
        return candidate.relative_to(root).as_posix()
    except ValueError as outside:
        raise ConfigError(
            f"--files names {name}, which is outside the repository {root}",
            key="files",
            hint=OUTSIDE_HINT,
        ) from outside


def changes_of(selection: Selection, repo: GitRepo) -> list[StagedChange]:
    """The change one selection judges, read from git (req 4.1, 10.5, 11.8)."""
    if selection.mode == "worktree":
        return repo.worktree_changes()
    if selection.mode == "files":
        return [StagedChange(status="M", path=inside(name, repo.root)) for name in selection.files]
    return repo.staged_changes()


def plan_selection(
    selection: Selection, repo: GitRepo, languages: Sequence[str] | None
) -> AnalysisPlan:
    """What ``selection`` covers, in git's terms, before Understand is asked anything.

    The file set is the selection filtered to what Understand can parse, and it decides
    whether the run happens at all. A deleted path stays in it: requirement 4.10 asks for the
    structural rules to run on what a deletion leaves behind, requirement 9.1 asks the change
    summary to list it, and the before database still holds it, so it is the seed that finds
    the former dependents.

    Whole-project mode never builds a before side (req 4.8) and neither does an unborn
    branch, which makes every entity new (req 4.5) and skips the ratchet rather than crashing
    on it. When a before side is built it is synced from the **resolved commit hash**, never
    from the word ``HEAD``: a symbolic revision names a different commit tomorrow, and the
    recorded sync state would then force a full re-sync every run.
    """
    if selection.mode == "all":
        return AnalysisPlan(
            mode="all",
            changes=(),
            files=analysable(repo.tracked_files(), languages),
            target=IndexTarget(),
            before=None,
        )
    changes = tuple(changes_of(selection, repo))
    target: SyncTarget = WorktreeTarget() if selection.mode == "worktree" else IndexTarget()
    return AnalysisPlan(
        mode=selection.mode,
        changes=changes,
        files=analysable(touched(changes), languages),
        target=target,
        before=repo.head(),
    )


class Engine:
    """Understand, driven for one run: analyse the sides, then read them back as snapshots."""

    def __init__(self, dbm: DatabaseManager, extractor: SnapshotExtractor, progress: Progress):
        self._dbm = dbm
        self._extractor = extractor
        self._progress = progress

    def analyse(self, plan: AnalysisPlan) -> dict[Side, AnalyzeResult]:
        """Bring the databases this run compares up to date (req 2.1, 2.3, 2.6, 4.3)."""
        results: dict[Side, AnalyzeResult] = {"after": self._dbm.ensure_side("after", plan.target)}
        if plan.before is not None:
            results["before"] = self._dbm.ensure_side("before", CommitTarget(commit=plan.before))
        return results

    def observe(
        self,
        plan: AnalysisPlan,
        analyses: Mapping[Side, AnalyzeResult],
        *,
        include_deleted: bool = False,
    ) -> tuple[ProjectSnapshot, ProjectSnapshot | None, AffectedSet]:
        """The two snapshots this run reads, and what the change affected (req 4.2, 4.8).

        Whole-project mode has one pass and no comparison: every entity is affected, which is
        what makes the same evaluators -- and the same summary builder -- answer requirement
        4.8 without a second code path.

        ``include_deleted`` widens the second pass by ``affected.deleted_files``, and the two
        pipelines want opposite answers for a measured reason. The worker records an entity
        **only** when its path is in the requested set (``worker.py``: ``if key.path in
        self.plan.files``), so a path left out of the second pass has no entities in the
        snapshot at all. ``check`` wants them left out -- requirement 4.10 asks it to evaluate
        what a deletion leaves *behind*, and a deleted entity can break no rule -- while
        ``explain`` cannot leave them out, because requirement 9.1 asks for the routines and
        classes the change *removed*, with their before-side metrics. Defaulting to ``False``
        keeps ``check``'s bounded second pass exactly as task 8.3 measured and pinned it.
        """
        if plan.mode == "all":
            whole = self.extract("after", plan.files, analyses)
            keys = set(whole.entities)
            return whole, None, AffectedSet(files={key.path for key in keys}, keys=keys)
        first_after = self.extract("after", plan.files, analyses)
        first_before = None if plan.before is None else self.extract("before", plan.files, analyses)
        affected = resolve(plan.changes, first_after, first_before)
        gone = affected.deleted_files if include_deleted else set()
        wanted = frozenset(affected.files | affected.neighbourhood | gone)
        after = self.extract("after", wanted, analyses)
        before = None if plan.before is None else self.extract("before", wanted, analyses)
        return after, before, affected

    def extract(
        self, side: Side, files: frozenset[str], analyses: Mapping[Side, AnalyzeResult]
    ) -> ProjectSnapshot:
        """One side's snapshot, rooted at the shadow tree its database was built from."""
        paths = self._dbm.paths()
        target = SnapshotTarget(
            db=paths.before_db if side == "before" else paths.after_db,
            root=paths.before_tree if side == "before" else paths.after_tree,
            side=side,
            files=files,
            parse_errors=tuple(analyses[side].parse_errors),
        )
        return self.phase(f"reading the {side} snapshot", lambda: self._extractor.extract(target))

    def phase(self, name: str, work: Callable[[], T]) -> T:
        """Run one phase, announced and timed, so a slow one can be named (req 4.11)."""
        self._progress.start(name)
        started = time.monotonic()
        answer = work()
        self._progress.finish(name, time.monotonic() - started)
        return answer
