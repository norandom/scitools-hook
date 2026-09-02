"""One ``explain`` run: a change described rather than judged (requirement 9).

``check`` answers "may this commit happen"; ``explain`` answers "what shape is it". Both walk
the same spine -- :mod:`scitools_hook.runner.pipeline` plans what to analyse, synchronises the
shadows, analyses the databases and reads the two sides back -- and then this module stops
where the evaluators would start and builds the reviewer's document instead: the entity and
dependency deltas of :func:`~scitools_hook.analysis.change_summary.build_summary`, optionally
the exported graphs of requirement 9.4 and the impact sets of requirement 9.5.

Four decisions here are load-bearing.

**A commit range resolves both ends to object ids before anything is synced.** Requirement 9.1
lets the operator explain a range as well as a staged change, and a range is the one input
whose ends are *symbolic*: ``HEAD~1`` names a different commit tomorrow. Two things break if
the symbol is carried through. ``ShadowSync`` only accepts a full object id as an incremental
cache key, so every run would re-export both shadows from scratch; and ``state.json`` would
record the string ``HEAD~1`` as what the before shadow holds, which is a false record the next
run and ``doctor`` both read. :func:`resolve_commit` therefore asks git, once per end, and
checks the *answer* as well as the status -- an object id, or a typed refusal naming the
revision.

**The revision is operator input, so the git argv is closed against options.** ``git`` reads
options out of a *revision* position and a trailing ``--`` does not stop it -- that separates
revisions from paths, not from options -- which the implementation notes record as the most
serious defect found on this project, ``diff_names`` having written a file into the working
tree through it. Measured here on git 2.43.0: ``rev-parse --verify --git-path hooks^{commit}``,
as two arguments, prints ``.git/hooks^{commit}``; with ``--end-of-options`` in front it prints
nothing and exits 128. The exposure through *this* call is narrower than that measurement
suggests, and saying so is better than implying a guard does more than it does: the revision is
concatenated with ``^{commit}`` before it is passed, so a single hostile token stops looking
like an option anyway (``--output=pwned.txt^{commit}`` exits 128 either way). The guard stays
because it is the project's rule for every argv that interpolates a user-supplied ref, and
because the next edit to this argv should not have to rediscover the reason.

**The graph directory is settled before any analysis starts.** ``--out DIR`` is an
operator-named destination, the same class as ``--output``, where task 9.1 found a branch that
hung forever on a FIFO and truncated an existing report before failing. Its kind is classified
with :func:`scitools_hook.paths.classify_directory` -- never ``Path.is_dir()``, which answers
``False`` for a directory it merely cannot reach -- and it is created *first*, so an operator
who typed the wrong path learns it in a second rather than after a full Understand run.

**A selection with nothing analysable produces an empty summary, not a failure.** This is
requirement 4.9's short circuit answered in ``explain``'s own terms: ``ensure_side`` raises
``AnalysisFailedError`` (exit 5) for a repository holding no file Understand can parse, so a
README-only range -- or a range whose two ends are the same commit -- would report a broken
tool instead of an honest "nothing to describe". The summary still carries the database path
and the GUI command of requirement 9.8, because both are true whether or not anything moved.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from scitools_hook.analysis.change_summary import ReviewAids, build_summary, open_command
from scitools_hook.errors import ConfigError
from scitools_hook.git.repo import GitRepo
from scitools_hook.models.change import (
    AffectedSet,
    ChangeSummary,
    GraphFile,
    GraphTarget,
    ImpactSet,
)
from scitools_hook.models.git import CommitTarget
from scitools_hook.models.snapshot import DataModel, EntityKey
from scitools_hook.paths import classify_directory
from scitools_hook.runner.context import RunContext
from scitools_hook.runner.pipeline import AnalysisPlan, Engine, Selection, analysable, touched
from scitools_hook.runner.pipeline import plan_selection as _plan_selection
from scitools_hook.understand.database import DatabaseManager
from scitools_hook.understand.graphs import GraphExporter
from scitools_hook.understand.impact import ImpactExpander
from scitools_hook.understand.snapshot import SnapshotExtractor

RANGE_SEPARATOR: Final = ".."
"""How requirement 9.1's ``--range A..B`` separates the two ends."""

RANGE_KEY: Final = "range"
"""The option a refusal about a range names, so the operator knows which one to fix."""

RANGE_HINT: Final = (
    "Write the range as BASE..HEAD, where each end names one commit "
    "(a hash, a tag, a branch or a revision like HEAD~1)."
)
"""What an operator does about a range this module cannot read."""

OUT_DIR_KEY: Final = "out"
"""The option a refusal about the graph destination names."""

OUT_DIR_HINT: Final = "Point --out at a writable directory, or leave it out to use the cache."
"""What an operator does about a graph destination the Gate will not write into."""

OBJECT_ID: Final = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
"""A full git object id, sha-1 or sha-256; the same shape ``ShadowSync`` accepts as a key."""

GRAPH_SCOPES: Final = frozenset({"routine", "class"})
"""What requirement 9.4 draws a butterfly graph of, and 9.5 expands the impact of."""


class CommitRange(DataModel):
    """The two commits requirement 9.1's ``--range BASE..HEAD`` names.

    The ends are held as the operator typed them; :meth:`ExplainPipeline.run` resolves each to
    an object id. Keeping the typed form is what lets a refusal quote it back.
    """

    base: str
    head: str

    @classmethod
    def parse(cls, text: str) -> CommitRange:
        """Read ``BASE..HEAD``, or refuse with the form that was expected (req 9.1).

        Only the two-dot form is accepted. ``A...B`` is git's *symmetric* difference, which
        answers a different question from the one requirement 9.1 asks -- what one side did to
        the other, from their merge base -- so it is refused by name rather than silently read
        as ``A..`` and a head beginning with a dot.
        """
        base, separator, head = text.partition(RANGE_SEPARATOR)
        if not separator or not base or not head or RANGE_SEPARATOR in head or head.startswith("."):
            raise ConfigError(f"{text!r} is not a commit range", key=RANGE_KEY, hint=RANGE_HINT)
        return cls(base=base, head=head)


@dataclass(frozen=True, slots=True)
class ExplainOptions:
    """The three review aids ``explain`` can be asked for (req 9.4, 9.5).

    The design writes these as three parameters of ``ExplainPipeline.run``; grouped here
    because this project caps a routine at five parameters and ``run`` would be at six --
    the same reason :class:`~scitools_hook.analysis.change_summary.ReviewAids` exists.
    """

    graphs: bool = False
    impact: bool = False
    out_dir: Path | None = None


class ExplainPipeline:
    """Describes one change: entity and dependency deltas, rankings, graphs, impact."""

    def __init__(self, ctx: RunContext, dbm: DatabaseManager, extractor: SnapshotExtractor) -> None:
        self.ctx = ctx
        self._dbm = dbm
        self._engine = Engine(dbm, extractor, ctx.progress)
        self._exporter = GraphExporter(ctx.api)
        self._expander = ImpactExpander(ctx.api)

    def run(
        self, selection: Selection | CommitRange, options: ExplainOptions | None = None
    ) -> ChangeSummary:
        """Describe what ``selection`` changed, with the aids ``options`` asks for (req 9)."""
        asked = ExplainOptions() if options is None else options
        repo = self.ctx.require_repo()
        out_dir = self._out_dir(asked) if asked.graphs else None
        plan = self._plan(selection, repo)
        if not plan.files:
            return self._nothing_analyzed()
        analyses = self._engine.analyse(plan)
        after, before, affected = self._engine.observe(plan, analyses, include_deleted=True)
        aids = ReviewAids(
            impact=self._impact(affected) if asked.impact else {},
            graphs=() if out_dir is None else self._graphs(affected, out_dir),
        )
        return build_summary(before, after, affected, self._dbm.paths(), aids)

    # --- deciding what to analyse ---------------------------------------------------

    def _plan(self, selection: Selection | CommitRange, repo: GitRepo) -> AnalysisPlan:
        """What this run covers, whether it was pointed at a selection or at a range."""
        languages = self.ctx.settings.project.languages
        if isinstance(selection, Selection):
            return _plan_selection(selection, repo, languages)
        return self._plan_range(selection, repo, languages)

    def _plan_range(
        self, span: CommitRange, repo: GitRepo, languages: Sequence[str] | None
    ) -> AnalysisPlan:
        """Both ends resolved to object ids, and the change between them (req 9.1).

        Both shadows are commit targets here, which is what makes ``SyncState`` record
        ``after_target = "commit"`` for this run. That is deliberate and it has a price worth
        knowing: the next ``check`` finds the after shadow synced from a commit rather than
        from the index, and a changed target kind forces a full re-sync of it.
        """
        base = resolve_commit(repo, span.base)
        head = resolve_commit(repo, span.head)
        changes = tuple(repo.diff_names(base, head))
        return AnalysisPlan(
            mode="range",
            changes=changes,
            files=analysable(touched(changes), languages),
            target=CommitTarget(commit=head),
            before=base,
        )

    # --- the review aids ------------------------------------------------------------

    def _out_dir(self, asked: ExplainOptions) -> Path:
        """The directory the graphs are written into, settled before any analysis runs.

        ``--out`` is operator input, so the destination is classified rather than opened
        hopefully: a FIFO, a regular file, a symlink leading nowhere and a directory this user
        cannot enter are each named as what they are. A symlink to a real directory is *not*
        refused -- pointing the graphs at a share is a working configuration, the same reading
        ``BaselineStore`` takes of a symlinked baseline.

        It is created here, before the plan is made, so that a mistyped path costs a second
        rather than a full Understand run. The price is that a run which then turns out to
        have nothing to analyse leaves an empty directory behind, which is the cheaper of the
        two surprises: the operator asked for the directory.
        """
        wanted = self._dbm.paths().graphs if asked.out_dir is None else asked.out_dir
        verdict = classify_directory(wanted)
        if not verdict.absent and not verdict.usable:
            raise ConfigError(
                f"the graph output directory {wanted} {verdict.reason}",
                key=OUT_DIR_KEY,
                hint=OUT_DIR_HINT,
            )
        try:
            wanted.mkdir(parents=True, exist_ok=True)
        except OSError as unusable:
            raise ConfigError(
                f"the graph output directory {wanted} could not be created: {unusable}",
                key=OUT_DIR_KEY,
                hint=OUT_DIR_HINT,
            ) from unusable
        return wanted

    def _graphs(self, affected: AffectedSet, out_dir: Path) -> list[GraphFile]:
        """One butterfly graph per affected routine or class, one depends-on per file (9.4).

        ``output.graphs_max`` caps each group on its own: requirement 9.4 puts the count on
        the routines and classes, and leaving the files unbounded would let a whole-project
        run draw one graph per file in the repository. Zero draws nothing and opens no
        database.

        The keys come from the affected set, so every one of them is an entity the *after*
        snapshot defines -- which is the database the graphs are drawn from. A routine the
        change deleted is therefore not asked for, rather than asked for and warned about.
        """
        paths = self._dbm.paths()
        limit = self.ctx.settings.output.graphs_max
        targets = [
            GraphTarget(key=key, graph="Butterfly")
            for key in _ranked(affected.keys, GRAPH_SCOPES, limit)
        ]
        targets += [
            GraphTarget(key=key, graph="Depends On")
            for key in _ranked(affected.keys, frozenset({"file"}), limit)
        ]
        written = self._engine.phase(
            "exporting graphs",
            lambda: self._exporter.export(paths.after_db, paths.after_tree, targets, out_dir),
        )
        self._report(self._exporter.warnings)
        return written

    def _impact(self, affected: AffectedSet) -> dict[EntityKey, ImpactSet]:
        """What references each affected routine and class, to the configured depth (9.5)."""
        paths = self._dbm.paths()
        keys = _ranked(affected.keys, GRAPH_SCOPES, None)
        depth = self.ctx.settings.output.impact_depth
        found = self._engine.phase(
            "expanding change impact",
            lambda: self._expander.expand(paths.after_db, paths.after_tree, keys, depth),
        )
        self._report(self._expander.warnings)
        return found

    # --- small services -------------------------------------------------------------

    def _nothing_analyzed(self) -> ChangeSummary:
        """The summary of a change holding nothing Understand can parse (req 4.9, 9.8)."""
        paths = self._dbm.paths()
        self.ctx.progress.note("nothing in this change can be analyzed; the summary is empty")
        return ChangeSummary(db_path=str(paths.after_db), open_command=open_command(paths))

    def _report(self, messages: Iterable[str]) -> None:
        """Say something on the diagnostics channel; the summary never travels this way (7.7)."""
        for message in messages:
            self.ctx.progress.note(message)


# --- helpers ------------------------------------------------------------------------


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


def _ranked(
    keys: Iterable[EntityKey], scopes: frozenset[str], limit: int | None
) -> list[EntityKey]:
    """The keys of ``scopes``, in a stable order, capped at ``limit`` when there is one.

    Sorting by token rather than by risk is a deliberate simplification: requirement 9.4 asks
    only for "up to a configurable count", the ranking of requirement 9.3 is computed from the
    deltas *after* this point, and a deterministic cap is what makes two runs over the same
    change name the same files.
    """
    ordered = sorted((key for key in keys if key.scope in scopes), key=lambda key: key.token)
    return ordered if limit is None else ordered[: max(limit, 0)]
