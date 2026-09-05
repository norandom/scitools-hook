"""Own the analysis cache: the shadows, the two databases and the state between runs (2.1-2.8).

Nothing the Gate analyses lives in the working tree. A shadow synchroniser -- reached only
through :class:`~scitools_hook.models.ports.ShadowPort`, because ``understand`` and ``git``
are siblings and neither may import the other -- materialises the index, the working tree or
a commit into a shadow under :class:`~scitools_hook.models.cache.CachePaths`, and this module
turns what that sync moved into the smallest set of ``und`` commands that leaves the database
equal to the shadow -- which is what makes a second run cost a fraction of the first
(requirement 2.3; measured, a selective pass was about 2.5x cheaper than a full one even on
60 files, and the gap widens).

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

**A parse error belongs to the database, not to the run that happened to do the parsing**
(requirement 2.6, task 11.13). ``und analyze`` is incremental, so a warm run re-parses nothing
and reports nothing: measured on this repository, a cold staged run named 9 unparsed files and
three consecutive warm runs over the same two databases -- still holding the same unparseable
files -- named none, which is the anti-silent-green report of 2.6 surviving exactly one run,
and a git hook is always warm. :class:`~scitools_hook.models.cache.SyncState` therefore carries
each side's errors between runs, and every analysis rewrites only the part of that record it
actually re-read: a full pass replaces the side's whole set, a ``-files`` pass replaces the
entries of the files it named (so fixing a file clears its errors) and carries the rest
forward untouched.

**The paths those errors carry are made repository-relative here**, against the shadow tree the
side's database was built from, and nowhere else. ``und`` reports the absolute path inside the
shadow (measured: ``…/<repo id>/after/pkg/generic.py``), which is a cache location no operator
can act on and which no ``EntityKey`` or selection entry can be compared with. A path Understand
parsed from *outside* the shadow -- the interpreter's own standard library, where task 10.4
measured four errors -- is left absolute, and that is the distinction the check pipeline blocks
on: a file in the selection failed to read, versus something the interpreter drags in.
Relativising is a plain ``is_relative_to``/``relative_to`` with no realpath fallback, and that
is deliberate: measured, ``und`` records files under their **resolved** path, so a shadow root
reached through a symlink already fails loudly in the snapshot extractor (``no file of <db> is
under the analysis root``) rather than reaching here. A fallback would only make that
configuration fail more quietly.

**The cache root is created ``0700`` before anything is written into it** (requirement 2.2's
neighbour: the shadows and the databases are copies of the repository's source).
``mkdir(parents=True)`` gives a directory the process umask and ``exist_ok=True`` does not
touch an existing one, so the mode is set explicitly, and set *first* -- a database created
into a world-readable directory is readable for as long as it takes to fix.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Final, NamedTuple, TypeVar

from scitools_hook.config.models import Settings
from scitools_hook.errors import AnalysisFailedError, LicenseError
from scitools_hook.models.cache import CACHE_SCHEMA, CachePaths, SyncState
from scitools_hook.models.git import SyncDelta, SyncTarget
from scitools_hook.models.ports import ShadowPort
from scitools_hook.models.progress import NullProgress, Progress
from scitools_hook.models.snapshot import Side
from scitools_hook.models.understand import AnalyzeResult, Feature
from scitools_hook.paths import classify_file

# The list file `analyze -files` and `remove -file` read is the same format `codecheck
# -files` reads, and 6.7 measured every way `und` can misread a line in it. One predicate
# for one format: a second copy here would be a second thing to keep in step with the
# binary.
from scitools_hook.understand.cache_files import CACHE_HINT, discard, present
from scitools_hook.understand.codecheck import unusable_list_file_name
from scitools_hook.understand.commit_before import (
    SHADOW_ROUTE,
    attempt_for,
    offers,
    serve,
)
from scitools_hook.understand.generated_arch import (
    architecture_for,
    generated_names,
    site_for,
)
from scitools_hook.understand.und_arch import (
    ARCH_HINT,
    DIRECTORY_STRUCTURE,
    ArchNode,
    read_architecture,
    write_architecture,
)
from scitools_hook.understand.und_cli import (
    ALL,
    UndCli,
    und_exclusions,
)

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

ARCH_FILE: Final = "scitools-hook.arch.xml"
"""The repository file that *declares* an architecture, beside ``scitools-hook.toml``.

A convention rather than a setting, and the choice is argued rather than assumed:

* It is **configuration**, so it is read from the working tree on both sides of a change,
  exactly as ``scitools-hook.toml`` is. The rule a run is judged by is the rule as it stands
  now, not the one that happened to be committed at the before commit.
* It sits at the repository root beside ``scitools-hook.toml`` and the default
  ``scitools-hook.baseline.json``, which is where this project already keeps the files an
  operator commits.
* It keeps the ``.xml`` extension because it *is* the document ``und import -arch`` reads
  and ``und export -arch`` writes. The workflow this feature exists for -- export the
  ``Directory Structure``, edit it into the architecture you mean, commit it -- would need a
  format conversion in the middle if it were anything else.
* Absent, it costs nothing: no file, no ``und`` call, and ``structure.architecture`` keeps
  meaning the directory layout it has always meant.

**The paths inside it are repository-relative, and they have to be rewritten before ``und``
sees them.** Measured: ``src/main.py`` written literally into an architecture document
resolves to *nothing* -- import status 0, ``Architecture imported.``, and an empty node --
while a bare ``main.py`` resolves by short name (so two files of that name are a coin toss)
and an absolute path resolves exactly. So every member is rewritten to an absolute path under
the side's shadow tree on the way in, and back to a repository-relative one on the way out.
"""


class _Pass(NamedTuple):
    """One finished ``und analyze``, and which files it actually re-read.

    ``reanalysed`` is ``None`` for a pass that read the whole project, and the absolute shadow
    paths that were named otherwise. It exists so the recorded parse errors can be updated by
    *what was re-read* rather than by what came back: a selective pass that reports no error
    says nothing at all about the files it never opened, and treating its silence as "the
    project parses cleanly now" is exactly the warm-run false green (task 11.13).
    """

    result: AnalyzeResult
    reanalysed: frozenset[Path] | None


def _reports(
    paths: CachePaths, build: str, db: Path, settings: Settings
) -> tuple[bool, Path | None]:
    """The two optional reports a **whole-project** pass may ask for (req 2.1, 7.1).

    Measured on Build 1262, twice over, and it is the same finding both times:
    ``-accuracy`` and ``-sarif`` describe **the pass**, not the database. A selective pass
    over one clean file prints ``1 of 1 parsed files had no errors or warnings (100%)`` and
    writes a SARIF whose ``results`` array is empty, while the database still holds three
    parse errors; a ``-changed`` pass with nothing to do prints ``0 of 0 ... (100%)``.
    Either published is a clean bill of health for code that has none.

    So only the two full passes ask, a selective pass asks for neither, and a run that did
    not do a full pass answers from what the last one recorded -- which is exactly why
    ``SyncState`` carries the figure at all.

    A module function, not a method: ``DatabaseManager`` is eight methods past its own
    limit, and naming ``Feature`` inside it is what its coupling count counts.

    The accuracy switch is asked for on what the *installed build* was measured to offer
    (requirement 1.4); the SARIF switch also needs the companion key, because it writes a
    file and requirement 1.3 ships every feature off.
    """
    accuracy = offers(paths, build, Feature.ACCURACY)
    sarif = db.with_suffix(".sarif") if settings.understand.sarif else None
    return accuracy, sarif


def _remember_accuracy(state: SyncState, side: Side, result: AnalyzeResult) -> float | None:
    """Record what a full pass measured, and answer with the figure that describes the database.

    A partial pass asks for no figure, so ``result.accuracy`` is ``None`` and the record stands
    -- which is the point: the database has not changed in a way the last figure does not
    describe. ``None`` is never written, because a run that did not ask has not measured a
    perfect resolution, and a zero here would be read as one.
    """
    if result.accuracy is not None:
        state.accuracy[side] = result.accuracy
    return state.accuracy.get(side)


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
        shadow: ShadowPort,
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
        # Not a phase: the route decides for itself whether it applies, and announcing
        # "building the after database from its commit" before every after side -- or before a
        # 6.5 install that has no such route -- would be a phase that mostly does not happen.
        # `serve` announces what it actually did, including the fallback of requirement 3.4.
        built = serve(
            attempt_for(
                self._und, self._paths, self._shadow.repo.root, self._settings, self._progress
            ),
            side,
            target,
            state,
            self._understand_version(),
        )
        if built is not None:
            self._save_state(state)
            return built
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
            discard(path)
        self._progress.note(f"discarded the analysis databases under {self._paths.root}")

    def build_worktree_project(self, root: Path, tracked: Sequence[str], target: Path) -> Path:
        """Build an Understand project over ``root`` itself, for a human to open (req 9.8).

        Every other database this class builds analyses a shadow tree, because the Gate judges
        the staged change rather than the files on disk. That makes those databases wrong for
        reading: their paths point into a cache directory, so following a finding to source in
        the GUI lands on a copy nobody can edit usefully.

        This is the read-only counterpart, and read-only is a design decision rather than a
        missing feature. Nothing synchronises out of it, the Gate never opens it, and an edit
        made inside Understand reaches the repository only when someone makes the same edit
        themselves. This tool exists to keep a codebase inside what a coding agent can reason
        about, and an agent edits files.

        It is rebuilt from scratch every time. A project that were updated incrementally could
        disagree with the tree without saying so, and a stale picture of the code is worse than
        no picture when the whole point is to see the code as it is.
        """
        # `tracked` comes from the caller rather than through `self._shadow.repo`: the shadow
        # port deliberately exposes only `root` (task 11.7 replaced a cross-adapter import with
        # it), and widening a port to save one argument is how that edge came back last time.
        languages = self.detect_languages(root / name for name in tracked)
        if not languages:
            raise AnalysisFailedError(
                f"no file under {root} is in a language this Understand can analyse",
                hint=NO_LANGUAGE_HINT,
            )
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        self._prepare_root()
        self._und.create(target, languages, local=False)
        if not target.exists():
            # `und create` reports success and writes nothing when the name is not `.und`:
            # `-db proj.uhd` exits 0 and creates no database, and `-db proj` exits 0 and
            # creates `proj.und` beside the path that was asked for. Both measured against
            # 6.5.1204. `cli.db.project_target` refuses those names before anything runs;
            # this is the post-condition for every other caller, because a zero exit that
            # produced nothing is the silent success this project refuses elsewhere.
            raise AnalysisFailedError(
                f"Understand reported success but created no database at {target}",
                hint=(
                    "Understand only creates a database whose name ends in .und. "
                    f"Try {target.with_suffix('.und')}."
                ),
            )
        self._und.add(target, root, und_exclusions(self._settings.project.exclude))
        self._und.analyze(target, ALL)
        return target

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
        """Turn one sync's delta into the ``und`` commands that answer it.

        What comes back names every file this side's **database** cannot read, not only what
        this run happened to re-parse: ``SyncState.record_parse_errors`` merges the pass's own
        answer into the record and hands the whole of it back, which is what makes a warm run
        report what the cold one found (requirement 2.6, task 11.13). The pass says which
        files it opened -- ``None`` for a whole-project one -- because that, and not the
        errors it returned, is what the record may be updated from.
        """
        db = self._paths.before_db if side == "before" else self._paths.after_db
        tree = self._paths.before_tree if side == "before" else self._paths.after_tree
        languages = self._languages(state, delta)
        self._invalidate(state, languages)
        if delta.full or not present(db):
            done = self._build(side, db, tree, state.languages)
        else:
            done = self._update(db, tree, delta, state.languages)
        self._declare_architecture(side, db, tree, state)
        errors = state.record_parse_errors(side, tree, done.result.parse_errors, done.reanalysed)
        state.before_route = SHADOW_ROUTE if side == "before" else state.before_route
        return done.result.model_copy(
            update={
                "parse_errors": errors,
                "accuracy": _remember_accuracy(state, side, done.result),
                "analysis_root": tree,
            }
        )

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
        discard(self._paths.before_db)
        discard(self._paths.after_db)
        state.languages = languages
        state.created_with = version
        state.forget_parse_errors()

    # --- the declared architecture (req 6.3, 6.7) --------------------------------

    def declared_architecture(self) -> ArchNode | None:
        """The architecture this repository declares, or ``None`` when it declares none.

        Read from the working tree rather than from either shadow: it is configuration, and a
        run is judged by the rules as they stand. Absence is the ordinary case and costs
        nothing; anything at that path that is not a readable file is a failure, because an
        operator who committed a declaration and then cannot have it read must be told rather
        than silently gated on the directory layout instead.
        """
        declaration = self._shadow.repo.root / ARCH_FILE
        verdict = classify_file(declaration)
        if verdict.absent:
            return None
        if not verdict.usable:
            raise AnalysisFailedError(
                f"the architecture declaration {declaration} {verdict.reason}", hint=ARCH_HINT
            )
        try:
            document = declaration.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as unreadable:
            raise AnalysisFailedError(
                f"the architecture declaration {declaration} could not be read: {unreadable}",
                hint=ARCH_HINT,
            ) from unreadable
        declared = read_architecture(document, str(declaration))
        if declared.name == DIRECTORY_STRUCTURE:
            raise AnalysisFailedError(
                f"{declaration} declares an architecture called {DIRECTORY_STRUCTURE!r}, "
                f"which is the one Understand derives from the directory layout",
                hint="Give the declaration a name of its own and point structure.architecture "
                "at it. Measured: importing under the built-in name is not refused the way "
                "every other duplicate is -- the declared nodes are MERGED into the "
                "folder-derived architecture, and the layout and the declaration stop being "
                "two things a rule can choose between.",
            )
        return declared

    def export_architecture(self, side: Side, name: str | None = None) -> str:
        """One architecture of a built database, as a document this repository could commit.

        This is the other half of the feature and it is not a convenience: nobody can write
        the document ``und import -arch`` reads from nothing, so the workflow is to export
        ``Directory Structure`` from a database that exists, edit it into the architecture
        that was meant, and commit it. The paths come back repository-relative -- a member
        Understand reported from outside the shadow, if a build ever produces one, is dropped
        rather than written as an absolute path no other machine could resolve.
        """
        wanted = name or DIRECTORY_STRUCTURE
        db = self._paths.before_db if side == "before" else self._paths.after_db
        tree = self._paths.before_tree if side == "before" else self._paths.after_tree
        if not present(db):
            raise AnalysisFailedError(
                f"there is no {side} analysis database at {db} to export an architecture from",
                hint="Run `scitools-hook db analyze` first: an architecture is read out of a "
                "database, so there has to be one.",
            )
        with tempfile.TemporaryDirectory(prefix="scitools-hook-arch-") as scratch:
            exported = self._und.export_arch(db, wanted, Path(scratch) / "architecture.xml")
        committable = exported.rebase(lambda member: _repository_relative(tree, member))
        return write_architecture(committable)

    def _declare_architecture(self, side: Side, db: Path, tree: Path, state: SyncState) -> None:
        """Put this run's architecture into one side's database (req 6.3, 4.1).

        Runs **after** the analysis and on every run, both of which are measured decisions:

        * an import into a database that has been ``add``-ed but not analysed produces empty
          nodes, and the analysis that follows does not fill them in -- so the architecture
          would exist, be listed, and hold nothing, which every layer rule reads as "no
          finding";
        * an imported architecture *survives* ``analyze -changed`` and ``analyze -all``
          (also measured), so a warm run would otherwise keep whatever was declared when the
          database was last built, and an edited declaration would not take effect until the
          next ``db rebuild``.

        **Which** architecture that is comes from ``generated_arch.architecture_for``:
        the repository's declaration where it supplies the configured name, the built-in
        directory structure where that is what is configured, and otherwise one generated from
        the after side's commit. All three arrive here as the same node and are placed the same
        way, which is what keeps the rules, ``explain`` and the review aids from ever learning
        where an architecture came from.

        Three outcomes per declared member, and each is a different thing:

        * **not under the shadow tree at all** -- an absolute path, a ``../`` escape -- is a
          defect in the declaration whichever side is being analysed, and is refused;
        * **under the tree but not on disk** is the ordinary state of the *before* side of a
          change that adds a file, so it is dropped before ``und`` is asked;
        * **on disk and still not resolved** is the silent failure this whole method exists
          for: ``und import -arch`` answers ``Architecture imported.`` with status 0 for a
          document whose every path is wrong, so what came back is compared with what was
          asked for and the difference is named.
        """
        declared = architecture_for(
            site_for(
                self._und, self._paths, self._shadow.repo.root, self._settings, self._progress
            ),
            self.declared_architecture(),
            state,
            generated_names(self._paths, self._understand_version()),
        )
        if declared is None:
            return
        inside = {member: _in_shadow(tree, member) for member in declared.paths()}
        self._refuse_outside(inside)
        present = {
            member: absolute
            for member, absolute in inside.items()
            if absolute is not None and classify_file(Path(absolute)).usable
        }
        resolved = self._und.declare_architecture(db, declared.rebase(present.get))
        self._refuse_unresolved(declared.name, present, resolved)
        self._progress.note(
            f"declared the {declared.name!r} architecture over the {side} database: "
            f"{len(resolved)} of {len(inside)} declared members"
        )
        self._note_absent(side, sorted(set(inside) - set(present)))

    def _note_absent(self, side: Side, absent: Sequence[str]) -> None:
        """Name the declared members this side's shadow does not hold, without failing.

        It cannot be an error: a file the change under review *adds* is not in the before
        shadow and a file it deletes is not in the after one, and both are ordinary. But it
        cannot be silence either -- a typo in a declared path is indistinguishable from those,
        and would quietly take a file out of its layer -- so the names are put on the progress
        stream where ``--verbose`` shows them.
        """
        if absent:
            self._progress.note(
                f"the {side} tree holds none of {', '.join(absent)}, so they are not in "
                f"that side's architecture"
            )

    def _refuse_outside(self, inside: Mapping[str, str | None]) -> None:
        """Refuse a declaration naming anything that is not inside the shadow tree."""
        outside = sorted(member for member, absolute in inside.items() if absolute is None)
        if outside:
            raise AnalysisFailedError(
                f"{self._shadow.repo.root / ARCH_FILE} declares {', '.join(outside)}, which "
                f"is not a path inside this repository",
                hint=ARCH_HINT,
            )

    def _refuse_unresolved(
        self, name: str, present: Mapping[str, str], resolved: frozenset[str]
    ) -> None:
        """Refuse an import that dropped a member whose file was there to be found."""
        missing = sorted(member for member, absolute in present.items() if absolute not in resolved)
        if not missing:
            return
        raise AnalysisFailedError(
            f"{self._shadow.repo.root / ARCH_FILE} declares {', '.join(missing)} in "
            f"{name!r}, and und import -arch resolved "
            f"{'none of them' if len(missing) == len(present) else 'the rest but not those'}",
            hint="Understand holds a file only if it enrolled it: a directory, a file of a "
            "language the project does not analyse, and a path that does not exist all "
            "import with status 0 and contribute nothing. " + ARCH_HINT,
        )

    # --- the two ways to bring a database up to date -----------------------------

    def _build(self, side: Side, db: Path, tree: Path, languages: list[str]) -> _Pass:
        """Create the database from nothing and analyse the whole shadow (req 2.1, 2.4).

        Whatever was at ``db`` is discarded first. ``und create`` over an existing database
        rewrites its settings and keeps its file list (measured), so re-creating in place
        would carry the previous shadow's files into a database built for a shadow that no
        longer holds them.

        ``und add`` is given no ``-exclude`` argument, and that is a decision rather than an
        omission: the shadow synchroniser has already applied ``project.include`` and
        ``project.exclude`` to the tree being added and re-applies them on every sync, so the
        shadow *is* the include/exclude decision (requirement 2.5). Handing the same patterns
        to ``und`` in a different pattern language would make a second, disagreeing filter --
        measured, ``und -exclude 'build/**'`` excludes nothing while ``-exclude build`` drops
        the tree -- and the disagreement that matters is the one where ``und`` holds *fewer*
        files than the shadow, because those files are then named in a ``-files`` list they
        cannot be in.
        """
        discard(db)
        self._und.create(db, languages, local=True)
        self._progress.note(
            f"created the {side} analysis database with {', '.join(languages)} enabled"
        )
        self._und.add(db, tree, [])
        return _Pass(
            self._und.analyze(
                db, ALL, *_reports(self._paths, self._understand_version(), db, self._settings)
            ),
            None,
        )

    def _update(self, db: Path, tree: Path, delta: SyncDelta, languages: list[str]) -> _Pass:
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
    ) -> _Pass:
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
        # A removal is a re-read too, in the only sense that matters here: the file has left
        # the database, so whatever it could not parse is no longer this project's problem.
        return _Pass(self._und.analyze(db, changed), frozenset({*removals, *changed}))

    def _analyse_everything(self, db: Path, reason: str) -> _Pass:
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
        return _Pass(
            self._und.analyze(
                db, ALL, *_reports(self._paths, self._understand_version(), db, self._settings)
            ),
            None,
        )

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
            state = SyncState.model_validate_json(self._paths.state.read_text(encoding="utf-8"))
        except Exception as unreadable:  # noqa: BLE001 - the outcome is the contract
            # Guarded by outcome: a state is either usable or it is not, and the ways it can
            # fail to be are not enumerable -- `read_text` raises `ValueError` on bytes that
            # are not UTF-8 and `MemoryError` on a file larger than memory, while pydantic
            # answers a 100k-deep document with a validation error rather than a
            # `RecursionError`.
            return self._forget_state(
                f"could not be read ({type(unreadable).__name__}): {unreadable}"
            )
        if state.stale_layout():
            # Every field added by a later layout reads as "nothing recorded", and some of
            # them -- the before route, the analysis fingerprint -- would otherwise be read
            # as an answer. Discarding costs one full analysis, once.
            return self._forget_state(
                f"was written by cache layout {state.schema_version}, not {CACHE_SCHEMA}"
            )
        return state

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
        state.schema_version = CACHE_SCHEMA
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


def _in_shadow(tree: Path, member: str) -> str | None:
    """The absolute path of one declared member inside ``tree``, or ``None`` when it escapes it.

    ``tree / member`` on an *absolute* member answers the member itself (pathlib), and a
    ``../`` member walks out of the shadow, so containment is checked after resolution rather
    than assumed from the text. The tree is resolved too: the cache root can sit behind a
    symlink, and comparing a resolved path with an unresolved prefix would put every member
    outside a tree they are all inside.
    """
    root = os.path.realpath(tree)
    candidate = os.path.realpath(tree / member)
    return candidate if candidate.startswith(f"{root}{os.sep}") else None


def _repository_relative(tree: Path, member: str) -> str | None:
    """One absolute shadow path as a repository-relative one, or ``None`` when it is outside.

    The inverse of :func:`_in_shadow`, and the reason the exported document is committable:
    every path in it names a file of the repository rather than a location in this machine's
    cache.
    """
    prefix = f"{os.path.realpath(tree)}{os.sep}"
    return member[len(prefix) :] if member.startswith(prefix) else None


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
