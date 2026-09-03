"""Own the analysis cache: the shadows, the two databases and the state between runs (2.1-2.8).

Nothing the Gate analyses lives in the working tree. :class:`~scitools_hook.git.shadow.ShadowSync`
materialises the index, the working tree or a commit into a shadow under
:class:`~scitools_hook.models.cache.CachePaths`, and this module turns what that sync moved
into the smallest set of ``und`` commands that leaves the database equal to the shadow --
which is what makes a second run cost a fraction of the first (requirement 2.3; measured, a
selective pass was about 2.5x cheaper than a full one even on 60 files, and the gap widens).

**Everything below was measured against the installed Understand (Build 1204), because the
command line's failure modes are what decide the shape of this module.** A discriminator
that can fail was used throughout -- a per-file marker entity read back out of ``und
metrics`` -- never an error count, which cannot tell "analysed clean" from "silently
skipped":

* **``und analyze -files`` exits 1 when the list names anything the project does not hold.**
  A ``README.md``, a file added since the last ``und add``, a path that has been deleted:
  each answers ``Error: … was not found in project. Skipping file.`` and status 1 *even when
  the valid files in the same list are analysed correctly* (verified: the marker entity of
  the valid file was in the database afterwards). So the list this module writes may hold
  nothing but enrolled source files, and everything else has to be kept out of it by
  construction rather than tolerated.
* **``und remove -file`` exits 1 the same way**, with ``-quiet`` on: ``Error: … is not in
  project. File skipped.`` Deleting a ``README.md`` from a repository would therefore break
  the commit hook outright if every deleted path were forwarded.
* **``und add <root>`` is required before a new file can be named in ``-files``.** ``add``
  records a *directory root*: a later ``-all`` or ``-changed`` pass re-scans it and enrols
  whatever appeared, but ``-files`` does not, so a brand-new file named there is "not found
  in project" and the whole command fails.
* **``analyze -all`` drops a file that has left the disk** -- its entity records and its
  entry in ``und list files`` both. That is what makes a full pass the correct answer to
  *any* change this module cannot describe precisely, rather than an admission of defeat.
* **``analyze -changed`` is not usable as the incremental primitive.** It answers from
  Understand's own record of what changed, which is a timestamp: with a file's mtime forced
  back to its recorded value and its *size changed*, ``-changed`` reported ``Errors:0`` and
  left the old entities in place, while ``-files`` on the same file re-analysed it. A
  primitive whose failure mode is a silently stale database cannot be the one a gate rests
  on, so the file list is built here and ``-changed`` is never used.

**Two rules follow, and between them they cover every change.** Either the delta can be
named exactly -- every path enrolled, every name writable into ``und``'s list file -- and the
run costs one ``analyze -files``; or it cannot, and the run costs one ``analyze -all``, which
is correct for additions, edits and deletions alike. There is no third branch and no path
that leaves the database stale.

**Which files Understand holds is decided by extension**, and :data:`LANGUAGE_BY_SUFFIX` is
that table, measured rather than assumed: one file per extension was dropped into a tree, a
database was built per language, and ``und list files`` was read back. It is deliberately
case-sensitive, because the installed table is (``a.C`` is C++, ``a.PY`` is nothing at all).
``tests/contract/test_database_contract.py`` re-measures it against the installed build, in
both directions, so a build whose table differs says so instead of quietly analysing less.

**The cache root is created ``0700`` before anything is written into it** (requirement 2.2's
neighbour: the shadows and the databases are copies of the repository's source).
``mkdir(parents=True)`` gives a directory the process umask and ``exist_ok=True`` does not
touch an existing one, so the mode is set explicitly, and set *first* -- a database created
into a world-readable directory is readable for as long as it takes to fix.
"""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Final, TypeVar

from scitools_hook.config.models import Settings
from scitools_hook.errors import AnalysisFailedError, LicenseError
from scitools_hook.git.shadow import ShadowSync
from scitools_hook.models.cache import CachePaths, SyncState
from scitools_hook.models.git import SyncDelta, SyncTarget
from scitools_hook.models.progress import NullProgress, Progress
from scitools_hook.models.snapshot import Side
from scitools_hook.models.understand import AnalyzeResult
from scitools_hook.paths import classify_directory, classify_file

# The list file `analyze -files` and `remove -file` read is the same format `codecheck
# -files` reads, and 6.7 measured every way `und` can misread a line in it. One predicate
# for one format: a second copy here would be a second thing to keep in step with the
# binary. It wants a public name and a home of its own -- recorded as a handoff rather than
# taken, because the file it lives in belongs to another task.
from scitools_hook.understand.codecheck import _unusable_name as unusable_list_file_name
from scitools_hook.understand.und_cli import UndCli

# Written as an explicit ``TypeVar`` rather than PEP 695 ``[T]`` syntax: Understand 6.5
# cannot parse a type-parameter list, and one such declaration costs the rest of the file
# from the analysis (measured in task 10.4).
T = TypeVar("T")
"""Whatever the timed phase returns; the timing wrapper is agnostic to it."""

CACHE_MODE: Final = 0o700
"""The cache holds copies of the repository's source; only its owner may read it."""

LANGUAGE_BY_SUFFIX: Final[Mapping[str, str]] = {
    ".a": "Ada",
    ".ada": "Ada",
    ".adb": "Ada",
    ".ads": "Ada",
    ".gpr": "Ada",
    ".asm": "Assembly",
    ".s": "Assembly",
    ".vb": "Basic",
    ".C": "C++",
    ".H": "C++",
    ".c": "C++",
    ".cc": "C++",
    ".cpp": "C++",
    ".cu": "C++",
    ".cuh": "C++",
    ".cxx": "C++",
    ".h": "C++",
    ".hh": "C++",
    ".hpp": "C++",
    ".hxx": "C++",
    ".inl": "C++",
    ".m": "C++",
    ".mm": "C++",
    ".cs": "C#",
    ".F": "Fortran",
    ".F90": "Fortran",
    ".f": "Fortran",
    ".f03": "Fortran",
    ".f77": "Fortran",
    ".f90": "Fortran",
    ".f95": "Fortran",
    ".for": "Fortran",
    ".ftn": "Fortran",
    ".java": "Java",
    ".cpl": "Jovial",
    ".jov": "Jovial",
    ".dfm": "Pascal",
    ".dpr": "Pascal",
    ".fmx": "Pascal",
    ".pas": "Pascal",
    ".sp": "Pascal",
    ".sql": "Pascal",
    ".py": "Python",
    ".upy": "Python",
    ".vhd": "VHDL",
    ".vhdl": "VHDL",
    ".cjs": "Web",
    ".cts": "Web",
    ".css": "Web",
    ".htm": "Web",
    ".html": "Web",
    ".js": "Web",
    ".mjs": "Web",
    ".mts": "Web",
    ".php": "Web",
    ".ts": "Web",
    ".tsx": "Web",
    ".xml": "Web",
}
"""Extension -> the project language ``und create -languages`` must be given for it (req 2.4).

Measured, not derived from the ``FileTypes`` table alone: that table names *file types*
(``Javascript``, ``Objective-C``, ``Text``), several of which enter under a differently named
language and several of which enter under none. So a database was built per language over a
tree holding one file per extension, and ``und list files`` was read back -- which is how
``.m``/``.mm`` are here as C++, why ``.txt``, ``.pl``, ``.bat`` and ``.cbl`` are absent
although the table names them, and why ``.bas`` is absent while ``.vb`` is present.

``.sql`` is here because the measurement said so and reading the table would not have: it is
``Sql`` there, no language is called that, and it enters under **Pascal** -- found by asking
each of the twelve in turn. It is the one entry no amount of care with the table would have
produced, and it is why the contract test measures both directions instead of one.

The twelve values are the twelve languages this build accepts; anything else exits 1
(``Error: JavaScript is not a valid language``).
"""

NO_LANGUAGE_HINT: Final = (
    "Set project.languages if Understand should analyse something here; the file types it "
    "recognises are listed by `und list settings <database>`."
)
"""A repository with nothing Understand can parse is told so rather than gated on nothing."""

CACHE_HINT: Final = "Point understand.db_location or the cache directory somewhere writable."
"""Every failure to create or clear the cache has the same one fix."""


class DatabaseManager:
    """The cache directory's owner: shadows, databases, ``state.json`` (requirements 2.1-2.8).

    One instance serves both sides of a run. It holds no state of its own beyond the
    Understand version it looks up once, because the state that matters is on disk where the
    next run -- and ``doctor`` -- can read it.
    """

    def __init__(
        self,
        paths: CachePaths,
        und: UndCli,
        shadow: ShadowSync,
        settings: Settings,
        progress: Progress | None = None,
    ) -> None:
        self._paths = paths
        self._und = und
        self._shadow = shadow
        self._settings = settings
        self._progress = progress if progress is not None else NullProgress()
        self._version: str | None = None

    def paths(self) -> CachePaths:
        """Where everything this manager owns lives (requirements 2.8, 9.8)."""
        return self._paths

    def ensure_side(self, side: Side, target: SyncTarget) -> AnalyzeResult:
        """Bring one side's shadow and database up to date with ``target`` (req 2.1, 2.3, 2.6).

        The order is load-bearing three times over. The cache root is secured before a
        database is written into it; the state is read before the sync, which updates it in
        place; and the state is written **only after the analysis succeeded**, because
        recording a sync whose analysis failed would drop that change out of the next run's
        delta and leave the database permanently behind the code.
        """
        self._prepare_root()
        state = self._load_state()
        delta = self._phase(
            f"synchronising the {side} tree", lambda: self._shadow.sync(side, target, state)
        )
        result = self._phase(
            f"analysing the {side} database", lambda: self._analyse(side, state, delta)
        )
        self._save_state(state)
        return result

    def rebuild(self) -> None:
        """Discard both databases and the recorded state (requirement 2.7).

        The shadows are kept: they are working material, and discarding the state is what
        makes the next sync a full one anyway -- after which ``full`` sends both sides
        through a fresh ``create``. Nothing is analysed here; the next run does that, so
        ``db rebuild`` cannot fail on a machine that has no licence at hand.
        """
        for path in (self._paths.before_db, self._paths.after_db, self._paths.state):
            _discard(path)
        self._progress.note(f"discarded the analysis databases under {self._paths.root}")

    def detect_languages(self, files: Iterable[Path]) -> list[str]:
        """The project languages ``files`` call for, sorted and unique (requirement 2.4).

        A file whose extension Understand does not enrol contributes nothing rather than a
        guess: a ``README.md`` in a Python repository must not add a language, and a
        repository of nothing but such files has no language at all, which
        :meth:`ensure_side` reports rather than papering over.
        """
        found = {
            language
            for file in files
            if (language := LANGUAGE_BY_SUFFIX.get(Path(file).suffix)) is not None
        }
        return sorted(found)

    # --- deciding what the change costs ------------------------------------------

    def _analyse(self, side: Side, state: SyncState, delta: SyncDelta) -> AnalyzeResult:
        """Turn one sync's delta into the ``und`` commands that answer it."""
        db = self._paths.before_db if side == "before" else self._paths.after_db
        tree = self._paths.before_tree if side == "before" else self._paths.after_tree
        languages = self._languages(state, delta)
        self._invalidate(state, languages)
        if delta.full or not self._present(db):
            return self._build(side, db, tree, state.languages)
        return self._update(db, tree, delta, state.languages)

    def _languages(self, state: SyncState, delta: SyncDelta) -> list[str]:
        """Which languages this repository needs, from configuration or from its files (2.4).

        Configuration is authoritative when it names anything, so a repository can be pinned
        to one language and stay there. Otherwise the recorded set only ever **grows**: a
        commit that touches one Python file must not narrow a database that also holds C++,
        and the languages a commit does not mention are not evidence of anything.
        """
        configured = self._settings.project.languages
        if configured:
            return sorted(set(configured))
        found = self.detect_languages(Path(rel) for rel in _touched(delta))
        wanted = sorted(set(state.languages) | set(found))
        if not wanted:
            raise AnalysisFailedError(
                f"no file under {self._shadow.repo.root} is one Understand can analyse, "
                f"so there is no language to create a database for",
                hint=NO_LANGUAGE_HINT,
            )
        return wanted

    def _invalidate(self, state: SyncState, languages: list[str]) -> None:
        """Discard **both** databases when what they were built from has changed.

        Both, not the side in hand, and that is the whole reason this is a method. A
        database is built for a language set with one Understand; when either changes, each
        side is stale, but only one of them is being ensured. Recording the new values while
        rebuilding a single side would leave the other side matching the record and never
        rebuilt -- stale for good, with the record saying it is current.
        """
        version = self._understand_version()
        if state.languages == languages and state.created_with == version:
            return
        _discard(self._paths.before_db)
        _discard(self._paths.after_db)
        state.languages = languages
        state.created_with = version

    def _present(self, db: Path) -> bool:
        """Whether ``db`` is there to be used; a path that is taken but unusable is raised.

        Absence is an answer -- an operator who cleared the cache gets a fresh database --
        and it is asked for through the shared classifier, because ``Path.exists()`` cannot
        tell "no database yet" from "a database this user cannot read".
        """
        verdict = classify_directory(db)
        if verdict.absent:
            return False
        if not verdict.usable:
            raise AnalysisFailedError(
                f"the analysis database {db} {verdict.reason}",
                hint="Run `scitools-hook db rebuild`, or remove what is at that path.",
            )
        return True

    # --- the two ways to bring a database up to date -----------------------------

    def _build(self, side: Side, db: Path, tree: Path, languages: list[str]) -> AnalyzeResult:
        """Create the database from nothing and analyse the whole shadow (req 2.1, 2.4).

        Whatever was at ``db`` is discarded first. ``und create`` over an existing database
        rewrites its settings and keeps its file list (measured), so re-creating in place
        would carry the previous shadow's files into a database built for a shadow that no
        longer holds them.

        ``und add`` is given no ``-exclude`` argument, and that is a decision rather than an
        omission: ``ShadowSync`` has already applied ``project.include``/``project.exclude``
        to the tree being added and re-applies them on every sync, so the shadow *is* the
        include/exclude decision (requirement 2.5). Handing the same patterns to ``und`` in a
        different pattern language would make a second, disagreeing filter -- measured, ``und
        -exclude 'build/**'`` excludes nothing while ``-exclude build`` drops the tree -- and
        the disagreement that matters is the one where ``und`` holds *fewer* files than the
        shadow, because those files are then named in a ``-files`` list they cannot be in.
        """
        _discard(db)
        self._und.create(db, languages, local=True)
        self._progress.note(
            f"created the {side} analysis database with {', '.join(languages)} enabled"
        )
        self._und.add(db, tree, [])
        return self._und.analyze(db, None, all=True)

    def _update(
        self, db: Path, tree: Path, delta: SyncDelta, languages: list[str]
    ) -> AnalyzeResult:
        """Apply one delta to a database that already holds the previous shadow (req 2.3).

        Every path is checked against two questions before it is named to ``und``: does
        Understand hold it at all, and can its name survive the list file? A ``no`` to the
        first drops the path (Understand never held a ``README.md``, so removing or
        analysing one is an error); a ``no`` to the second sends the whole run through a full
        analysis, because a name that cannot be written cannot be worked around.
        """
        removals = _selection(tree, delta.deleted, languages)
        changed = _selection(tree, sorted({*delta.added, *delta.modified}), languages)
        blocked = _unlistable([*removals, *changed])
        if blocked is not None:
            return self._analyse_everything(db, blocked)
        try:
            return self._apply(db, tree, delta, removals, changed)
        except LicenseError:
            # A retry cannot produce a licence, and requirement 1.4 wants that exit code out
            # of here unaltered. Every other failure is a candidate for the fallback.
            raise
        except Exception as refused:  # noqa: BLE001 - the outcome is the contract
            # Guarded by outcome, not by type. What is required is a database equal to the
            # shadow; a full pass delivers that whatever went wrong -- a build whose file
            # types differ from the measured table, a file Understand declined to enrol, a
            # wrapper failing in a way nobody here predicted. The alternative is a gate that
            # stops working on a machine whose Understand is not this one.
            return self._analyse_everything(db, f"{type(refused).__name__}: {refused}")

    def _apply(
        self,
        db: Path,
        tree: Path,
        delta: SyncDelta,
        removals: list[Path],
        changed: list[Path],
    ) -> AnalyzeResult:
        """Remove what left the shadow, enrol what arrived, analyse what changed.

        The order of the first two is **not** load-bearing and saying so is cheaper than
        leaving the next reader to work it out: ``add`` re-scans the tree on disk, where a
        deleted path is no longer present, so it cannot undo a removal whichever way round
        they run. Removal goes first because it is the smaller set.

        ``add`` runs whenever the shadow gained *anything*, not only when it gained a source
        file: it is what enrols a new file, and a file this module's table does not recognise
        may still be one Understand enrols -- in which case a later full pass finds it
        already in the project instead of missing from it.
        """
        if removals:
            self._und.remove_files(db, removals)
        if delta.added:
            self._und.add(db, tree, [])
        return self._und.analyze(db, changed, all=False)

    def _analyse_everything(self, db: Path, reason: str) -> AnalyzeResult:
        """The fallback: one full pass, which is correct for every kind of change.

        Measured, each with a marker entity read back out of the database afterwards:
        ``analyze -all`` re-scans the added root, so an addition is enrolled and analysed
        with no ``add`` of its own -- even one carrying a *stale* timestamp; it re-parses a
        file whose mtime was forced back to its recorded value, so an edit lands whatever
        the clock says; and it drops the files that have left the disk, entity records and
        ``list files`` entry both. It costs time and never correctness, which is the right
        way round for a gate.
        """
        self._progress.note(f"analysing the whole project rather than the change: {reason}")
        return self._und.analyze(db, None, all=True)

    # --- the cache directory and its state ---------------------------------------

    def _prepare_root(self) -> None:
        """Create the cache root and make it owner-only, before anything is written into it.

        The ``chmod`` is the load-bearing call and is pinned by a test: ``mkdir(mode=…)`` is
        subject to the process umask, and ``exist_ok=True`` leaves an existing directory's
        mode alone -- which is how 7.2's shadow sync left the root at ``0o775`` under the
        common ``0o022``.

        ``mode=CACHE_MODE`` on the ``mkdir`` is a **known equivalent mutant**, measured as
        one and kept anyway. Removing it changes no observable outcome: the ``chmod`` two
        lines down dominates it, and the window between them holds a directory that has just
        been created and so contains nothing to read. It stays because it states the mode at
        the point of creation -- the narrower window is the one worth having, and the two
        calls are not the same claim.
        """
        root = self._paths.root
        try:
            root.mkdir(mode=CACHE_MODE, parents=True, exist_ok=True)
            os.chmod(root, CACHE_MODE)
        except OSError as unusable:
            raise AnalysisFailedError(
                f"the cache directory {root} could not be prepared: {unusable}",
                hint=CACHE_HINT,
            ) from unusable

    def _load_state(self) -> SyncState:
        """What the last run recorded, or an empty state, which costs a full re-sync.

        A state that cannot be read is a *missing cache key*, never a failure: every field in
        it is an optimisation, and the sync falls back to materialising the whole target,
        which is correct however the file was lost. The operator is told, because the cost is
        real and a corrupt file that is silently rewritten every run is worth hearing about.
        """
        verdict = classify_file(self._paths.state)
        if verdict.absent:
            return SyncState()
        if not verdict.usable:
            return self._forget_state(verdict.reason)
        try:
            return SyncState.model_validate_json(self._paths.state.read_text(encoding="utf-8"))
        except Exception as unreadable:  # noqa: BLE001 - the outcome is the contract
            # Guarded by outcome: a state is either usable or it is not, and the ways it can
            # fail to be are not enumerable -- `read_text` raises `ValueError` on bytes that
            # are not UTF-8 and `MemoryError` on a file larger than memory, while pydantic
            # answers a 100k-deep document with a validation error rather than a
            # `RecursionError`.
            return self._forget_state(
                f"could not be read ({type(unreadable).__name__}): {unreadable}"
            )

    def _forget_state(self, reason: str) -> SyncState:
        """Say why the recorded state is being ignored, and hand back an empty one."""
        self._progress.note(f"the sync state {self._paths.state} {reason}; analysing from scratch")
        return SyncState()

    def _save_state(self, state: SyncState) -> None:
        """Record what the shadows and databases now hold, for the next run to compare against.

        Written beside the destination and renamed on, which is not about crash safety here
        -- a truncated state file simply costs a full re-sync -- but about never opening the
        destination: a FIFO left at ``state.json`` would make a plain write block forever,
        and a symlink would be written through. ``mkstemp`` opens a new file with ``O_EXCL``
        and ``os.replace`` is a rename, so neither shape is reachable.

        A failure to write is reported and swallowed. The state is a cache key: losing it
        costs the next run a full analysis, and failing this run over it would turn a slow
        commit into a blocked one.
        """
        try:
            self._replace_state(state.model_dump_json(indent=2) + "\n")
        except Exception as unwritable:  # noqa: BLE001 - the outcome is the contract
            self._progress.note(
                f"the sync state {self._paths.state} could not be written "
                f"({type(unwritable).__name__}): {unwritable}; the next run will be a full one"
            )

    def _replace_state(self, body: str) -> None:
        """Write ``body`` into the cache root and rename it onto ``state.json``."""
        handle, name = tempfile.mkstemp(dir=self._paths.root, prefix="state.json.")
        scratch = Path(name)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as writer:
                writer.write(body)
            os.replace(scratch, self._paths.state)
        finally:
            scratch.unlink(missing_ok=True)

    # --- small services ----------------------------------------------------------

    def _understand_version(self) -> str:
        """What built these databases; asked once per manager and remembered.

        ``created_with`` is what makes an Understand upgrade heal itself: without it the
        first run after one meets a database the new build refuses to open, and the operator
        has to work out that ``db rebuild`` is the answer.
        """
        if self._version is None:
            self._version = self._und.version()
        return self._version

    def _phase(self, name: str, work: Callable[[], T]) -> T:
        """Run one phase, announcing it and reporting how long it took (requirement 4.11).

        The five-second rule itself belongs to the reporter -- ``cli.common.ConsoleProgress``
        prints a phase that reached it -- so there is no second threshold here to fall out of
        step with it. A phase that raised is not finished, and saying it was would put a
        cheerful line under an error.
        """
        self._progress.start(name)
        started = time.monotonic()
        answer = work()
        self._progress.finish(name, time.monotonic() - started)
        return answer


# --- helpers ------------------------------------------------------------------------


def _touched(delta: SyncDelta) -> list[str]:
    """Every path a sync put into the shadow; the deleted ones are not there to look at."""
    return [*delta.added, *delta.modified]


def _selection(tree: Path, rels: Sequence[str], languages: Sequence[str]) -> list[Path]:
    """The absolute shadow paths ``und`` can be asked about, out of ``rels``.

    Two filters, in this order. Only a file whose extension maps to an **enabled** language
    is one the database holds -- naming any other exits 1 and takes the whole command with
    it -- and a language the database was not created for enrols nothing, which is why the
    enabled set is consulted rather than the whole table.
    """
    enabled = set(languages)
    return [tree / rel for rel in rels if LANGUAGE_BY_SUFFIX.get(Path(rel).suffix) in enabled]


def _unlistable(paths: Sequence[Path]) -> str | None:
    """Why one of ``paths`` cannot be written into ``und``'s list file, or ``None``.

    The names are ``und``'s own hazards, measured by 6.7 against this build: a ``#`` comments
    out the rest of the line, a comma starts a line-number range, a ``*`` is glob-expanded, a
    backslash is rewritten to ``/``, edge whitespace is stripped, a line break splits the
    entry in two, and a relative path resolves against ``und``'s directory rather than the
    shadow. Every one of them names a *different file* or none at all, silently.
    """
    for path in paths:
        problem = unusable_list_file_name(str(path))
        if problem is not None:
            return f"{path} {problem}"
    return None


def _discard(path: Path) -> None:
    """Remove a database, a state file or whatever else has taken their place.

    The kind is settled with ``lstat`` before anything is deleted, so a symlink is unlinked
    rather than followed into someone else's directory tree, and absence is not an error --
    discarding what is not there is the state this leaves behind anyway.
    """
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return
    except (OSError, ValueError) as unreachable:
        raise AnalysisFailedError(
            f"{path} could not be examined: {unreachable}", hint=CACHE_HINT
        ) from unreachable
    try:
        if stat.S_ISDIR(mode) and not stat.S_ISLNK(mode):
            shutil.rmtree(path)
        else:
            os.unlink(path)
    except OSError as undeletable:
        raise AnalysisFailedError(
            f"{path} could not be removed: {undeletable}", hint=CACHE_HINT
        ) from undeletable
