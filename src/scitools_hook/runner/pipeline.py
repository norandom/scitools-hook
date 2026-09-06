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

import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final, Literal, TypeVar

from pydantic import Field

from scitools_hook.analysis.affected import resolve
from scitools_hook.analysis.narrow import narrow
from scitools_hook.errors import ConfigError
from scitools_hook.git.repo import GitRepo
from scitools_hook.models.change import AffectedSet
from scitools_hook.models.git import (
    RANGE_HINT,
    RANGE_KEY,
    CommitRange,
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


# --- a range of commits (req 9.1), planned like a selection ------------------------

OBJECT_ID: Final = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
"""A full git object id, sha-1 or sha-256; the same shape ``ShadowSync`` accepts as a key."""


def merge_base(repo: GitRepo, left: str, right: str) -> str:
    """The commit ``left`` and ``right`` diverged from, for ``--range A...B`` (req 9.1).

    Resolved through ``git merge-base`` and then through :func:`resolve_commit`, so the answer
    is checked to be a full object id by the same test every other end passes, and an
    unrelated pair -- two histories with no common ancestor -- is refused with the range the
    operator typed rather than failing later inside the shadow export.
    """
    result = repo._run(["merge-base", "--end-of-options", left, right])
    answer = result.stdout.decode("utf-8", "replace").strip()
    if result.rc != 0 or not answer:
        raise ConfigError(
            f"{left!r} and {right!r} have no common ancestor, so there is no merge base",
            key=RANGE_KEY,
            hint=RANGE_HINT,
        )
    return resolve_commit(repo, answer)


def resolve_commit(repo: GitRepo, revision: str) -> str:
    """The object id ``revision`` names, or the typed refusal naming what could not resolve.

    ``^{commit}`` peels a tag or a tree to the commit the shadow will be exported from, so an
    end that names something other than a commit is refused here rather than half-way through
    a ``read-tree``. ``--end-of-options`` closes the argv against a revision that begins with a
    dash (see the module docstring), and the *answer* is checked as well as the status: only a
    full object id is accepted, because that is the only form ``ShadowSync`` will reuse as a
    cache key, and because an option that slipped through would answer with something else.

    A revision that does not resolve is a ``ConfigError`` rather than an analysis failure: the
    operator typed a range that does not name two commits, and the exit code should say "fix
    what you asked for", exactly as a ``--files`` entry outside the repository does.

    This reaches through :class:`~scitools_hook.git.repo.GitRepo`'s own runner rather than
    running git itself, so the call is still recorded for ``--verbose`` (req 12.8), still
    bounded by the repository's timeout, and still made from the repository root. It belongs
    on ``GitRepo`` as a method; task 8.4's boundary excludes ``git/repo.py`` while task 11.1
    holds it, so it is written here and flagged for promotion.
    """
    result = repo._run(["rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}"])
    answer = result.stdout.decode("utf-8", "replace").strip()
    if result.rc != 0 or not OBJECT_ID.fullmatch(answer):
        raise ConfigError(
            f"{revision!r} does not name a commit in {repo.root}: "
            f"{result.stderr.strip() or 'git answered ' + (answer or 'nothing')}",
            key=RANGE_KEY,
            hint=RANGE_HINT,
        )
    return answer


def plan_range(span: CommitRange, repo: GitRepo, languages: Sequence[str] | None) -> AnalysisPlan:
    """What a run over ``span`` covers: both ends resolved, and the change between them.

    Beside :func:`plan_selection` rather than in ``runner.explain``, because two commands ask
    this question now. ``explain --range`` describes what a range did; ``check --range`` judges
    it, which is what a pre-push hook needs -- at push time nothing is staged and the working
    tree is irrelevant, so the only honest question is what the commits being pushed did.

    Both ends are resolved in the order they were typed, so a typo is refused by name; the
    merge base is then taken from the two object ids, which leaves ``merge-base`` able to fail
    for one reason only -- histories that never met.

    Both shadows are commit targets, which is what makes ``SyncState`` record
    ``after_target = "commit"`` for this run. That is deliberate and it has a price worth
    knowing: the next ``check`` finds the after shadow synced from a commit rather than from
    the index, and a changed target kind forces a full re-sync of it.
    """
    named = resolve_commit(repo, span.base)
    head = resolve_commit(repo, span.head)
    base = merge_base(repo, named, head) if span.from_merge_base else named
    changes = tuple(repo.diff_names(base, head))
    return AnalysisPlan(
        mode="range",
        changes=changes,
        files=analysable(touched(changes), languages),
        target=CommitTarget(commit=head),
        before=base,
    )


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


RINGS: Final = 2
"""How many dependency steps a check's single extraction records (requirement 8.3).

Two, because that is what the two passes together used to reach: the affected set is one step
from the change and the rules that look past it read one more. Whole-project mode asks for
zero -- it records everything already, and widening a request that names every file would
widen nothing.
"""

SERVED_FROM_CACHE: Final = (
    "the before snapshot was served from the cache, so this run extracted it once fewer"
)
"""Requirement 8.6: an optimisation that cannot be seen is one nobody can check."""

BEFORE_UNCOVERED: Final = (
    "the before side does not reach {files} within two dependency steps of the change, so it "
    "was extracted again for them; this run is as fast as it was before the cache existed"
)
"""The uncovered case of :meth:`Engine._before_for`, said out loud rather than absorbed."""


BeforeSource = tuple[ProjectSnapshot | None, Callable[[ProjectSnapshot], None] | None]
"""A before-side document the caller already has, and where to put one that is extracted.

One argument rather than two because ``observe`` is at its own ``CountParams`` limit, and one
pair rather than an object because naming a class of its own would cost the engine a coupling
it has no room for. Both halves are ``None`` for a run with no before side, and the second is
``None`` when the first is a hit.
"""


def _wanted(affected: AffectedSet, include_deleted: bool) -> frozenset[str]:
    """The files the rules read: the affected set, its neighbourhood, and deletions on request.

    A module function so the engine names no container type of its own -- the class is three
    past its ``CountClassCoupled`` limit, and the gate counts a ``frozenset(...)`` in a method
    exactly as it counts a field (recorded in task 9.2's notes).
    """
    gone = affected.deleted_files if include_deleted else set()
    return frozenset(affected.files | affected.neighbourhood | gone)


def _uncovered(wide: ProjectSnapshot, wanted: frozenset[str]) -> frozenset[str]:
    """The wanted files the wide document holds no entity for, for the same reason."""
    return wanted - {key.path for key in wide.entities}


class Engine:
    """Understand, driven for one run: analyse the sides, then read them back as snapshots."""

    def __init__(
        self,
        dbm: DatabaseManager,
        extractor: SnapshotExtractor,
        progress: Progress,
    ):
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
        before: BeforeSource = (None, None),
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
        wide_after = self.extract("after", plan.files, analyses, wide=True)
        wide_before = self._wide_before(plan, analyses, before)
        affected = resolve(
            plan.changes,
            narrow(wide_after, plan.files),
            None if wide_before is None else narrow(wide_before, plan.files),
        )
        wanted = _wanted(affected, include_deleted)
        read = None if wide_before is None else self._before_for(wide_before, wanted, analyses)
        return narrow(wide_after, wanted), read, affected

    def _wide_before(
        self,
        plan: AnalysisPlan,
        analyses: Mapping[Side, AnalyzeResult],
        source: BeforeSource,
    ) -> ProjectSnapshot | None:
        """The before side's wide document: the caller's, or one extracted and handed back.

        ``source`` is how the check pipeline reads and writes the snapshot cache without
        this class ever naming it: a document it already had, and where to put one this
        extracts. The cache needs the analysis settings and the
        Understand build, neither of which the engine knows, and naming it here would cost a
        coupling this class has no room for.

        The extraction stays **after** the after side's, which is the order the phases have
        always been announced in and the order a reader of the progress stream expects.
        """
        given, keep = source
        if plan.before is None:
            return None
        if given is not None:
            return given
        document = self.extract("before", plan.files, analyses, wide=True)
        if keep is not None:
            keep(document)
        return document

    def _before_for(
        self,
        wide: ProjectSnapshot,
        wanted: frozenset[str],
        analyses: Mapping[Side, AnalyzeResult],
    ) -> ProjectSnapshot:
        """The before document the rules read, narrowed, or extracted again if it cannot be.

        The affected set is computed from **both** graphs, so a file can be two dependency
        steps from the change on the after side and further than that on the before side --
        the change may be the very edit that created the dependency. Such a file is in
        ``wanted`` and absent from the wide before document, and narrowing would silently give
        it no before-side entities, which reads as "new" and takes it out of the ratchet.

        So coverage is checked rather than assumed, and the uncovered case falls back to
        exactly what this pipeline did before: one bounded extraction for ``wanted``. It costs
        a second pass in a case that does not arise on an ordinary edit, and it keeps
        requirement 8.7's promise that none of this changes a finding.
        """
        missing = _uncovered(wide, wanted)
        if not missing:
            return narrow(wide, wanted)
        self._progress.note(BEFORE_UNCOVERED.format(files=", ".join(sorted(missing))))
        return self.extract("before", wanted, analyses)

    def extract(
        self,
        side: Side,
        files: frozenset[str],
        analyses: Mapping[Side, AnalyzeResult],
        wide: bool = False,
    ) -> ProjectSnapshot:
        """One side's snapshot, rooted at the directory its database names its files under.

        ``wide`` asks the worker for :data:`RINGS` dependency steps of neighbourhood
        beyond ``files``, which is what lets one extraction answer both of the questions a
        check used to ask. A boolean rather than a count because the engine has exactly two
        callers and exactly two answers, and because an ``int`` here would be one more
        coupled type on a class already three past its limit.

        Not always that side's own tree. A commit-built before database is named under the
        **after** tree, because ``-refdb`` copies the reference's file set with its paths,
        so the analysis says where it is rooted and this reads it rather than deducing it
        from the side. The fallback covers a result assembled by hand, which every fake does.
        """
        paths = self._dbm.paths()
        own = paths.before_tree if side == "before" else paths.after_tree
        target = SnapshotTarget(
            db=paths.before_db if side == "before" else paths.after_db,
            root=analyses[side].analysis_root or own,
            side=side,
            files=files,
            parse_errors=tuple(analyses[side].parse_errors),
            rings=RINGS if wide else 0,
        )
        return self.phase(f"reading the {side} snapshot", lambda: self._extractor.extract(target))

    def phase(self, name: str, work: Callable[[], T]) -> T:
        """Run one phase, announced and timed, so a slow one can be named (req 4.11)."""
        self._progress.start(name)
        started = time.monotonic()
        answer = work()
        self._progress.finish(name, time.monotonic() - started)
        return answer
