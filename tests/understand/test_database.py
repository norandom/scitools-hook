"""The analysis database's lifecycle over a real repository and a fake ``und`` (task 8.1).

Every test here drives a **real** ``git`` and a **real** :class:`ShadowSync` -- the same
combination the check pipeline will use -- and stands in for Understand alone, because what
this module has to get right is *which* ``und`` commands run for a given change. The
commands are asserted against behaviour measured on the real Understand (Build 1204), and
the measurements are stated where they decide a test:

* ``und analyze -files`` **exits 1** when the list names anything the project does not hold
  -- a ``README.md``, a file added since the last ``und add``, a path that was deleted --
  *even when the valid files in the same list are analysed correctly*. So the list this
  module builds may hold nothing but enrolled source files, and the tests check that
  directly rather than checking that a run "worked".
* ``und remove -file`` exits 1 on a path the project does not hold, with ``-quiet`` on.
* ``und add <root>`` is required before a **new** file can be named in ``-files``; a
  ``-changed`` or ``-all`` pass re-scans the root by itself, ``-files`` does not.
* ``analyze -all`` drops a file that has left the disk, entity records and file entry both,
  which is why every path this module cannot name in a list file ends in an ``-all`` pass
  rather than in a failure.

``tests/contract/test_database_contract.py`` pins the language map those decisions rest on
against the installed Understand; this module pins the logic that uses it.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from conftest import FakeCommandLog, FakeProgress, GitRepoBuilder, MakeGitRepo
from fakes import FakeCall, FakeUndCli
from fixtures.constants import BUILD

from scitools_hook.config.defaults import default_settings
from scitools_hook.config.models import ProjectSettings, Settings, UnderstandSettings
from scitools_hook.errors import AnalysisFailedError, LicenseError
from scitools_hook.git.repo import GitRepo
from scitools_hook.git.shadow import ShadowSync
from scitools_hook.models.cache import APP_NAME, CachePaths, SyncState
from scitools_hook.models.git import CommitTarget, IndexTarget, SyncTarget, WorktreeTarget
from scitools_hook.models.snapshot import ParseError, Side
from scitools_hook.models.understand import AnalyzeResult
from scitools_hook.runner.context import cache_dir
from scitools_hook.understand.database import (
    ARCH_FILE,
    CACHE_MODE,
    LANGUAGE_BY_SUFFIX,
    NO_LANGUAGE_HINT,
    DatabaseManager,
)
from scitools_hook.understand.und_arch import (
    ArchNode,
    write_architecture,
)

LEAKED_GIT_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_CONFIG_COUNT",
    "GIT_CEILING_DIRECTORIES",
    "GIT_OBJECT_DIRECTORY",
    "GIT_COMMON_DIR",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)
"""Everything an outer ``git`` invocation exports that would steer these subprocesses."""


@pytest.fixture(autouse=True)
def isolated_git(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralise the developer's git configuration for the subprocesses the module starts."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for leaked in LEAKED_GIT_VARS:
        monkeypatch.delenv(leaked, raising=False)


# --- the stand-in for Understand ------------------------------------------------


@dataclass
class UndStub(FakeUndCli):
    """``FakeUndCli`` that also **creates the database directory**, as ``und create`` does.

    Without that this module could never tell a first run from a second one: it decides
    whether a database exists by looking for it, exactly as an operator who deleted the
    cache would have it decide. The failures are injected here too, one command at a time,
    so a fallback path is exercised by a command that really refused rather than by a flag.
    """

    fail: dict[str, Exception] = field(default_factory=dict)
    fail_once: bool = True
    dropped: frozenset[str] = frozenset()
    """Absolute member paths this ``und`` refuses to resolve, as a real one silently would."""

    exported: ArchNode = field(default_factory=lambda: ArchNode(name="Directory Structure"))
    """What ``export_arch`` answers, with its members already made absolute."""

    def create(self, db: Path, languages: list[str], local: bool = True) -> None:
        """Record the creation and make the ``.und`` directory the real command makes."""
        super().create(db, languages, local)
        self._maybe_fail("create")
        db.mkdir(parents=True, exist_ok=True)

    def remove_files(self, db: Path, files: list[Path]) -> None:
        """Record the removal, then fail if this test asked ``remove`` to fail."""
        super().remove_files(db, files)
        self._maybe_fail("remove_files")

    def analyze(self, db: Path, files: list[Path] | None, all: bool = False) -> AnalyzeResult:
        """Record the analysis, then fail if this test asked the selective form to fail."""
        result = super().analyze(db, files, all)
        self._maybe_fail("analyze-all" if all else "analyze")
        return result

    def declare_architecture(self, db: Path, root: ArchNode) -> frozenset[str]:
        """Record the declaration and answer what a real ``und`` would have resolved.

        ``dropped`` is what makes the anti-false-green path reachable: ``und import -arch``
        takes a document naming files it cannot resolve with status 0 and silently keeps
        nothing of them, so a stub that always echoed its input back would make the manager's
        check unfailable.
        """
        self.calls.append(
            FakeCall(
                "declare_architecture",
                {"db": db, "root": root, "members": list(root.paths())},
            )
        )
        self._maybe_fail("declare_architecture")
        return frozenset(member for member in root.paths() if member not in self.dropped)

    def export_arch(self, db: Path, name: str, out: Path) -> ArchNode:
        """Answer the architecture this test planted, as ``und export -arch`` would."""
        self.calls.append(FakeCall("export_arch", {"db": db, "name": name, "out": out}))
        return self.exported

    def _maybe_fail(self, command: str) -> None:
        """Raise what this test planned for ``command``; once, unless it asked otherwise."""
        planned = self.fail.get(command)
        if planned is None:
            return
        if self.fail_once:
            del self.fail[command]
        raise planned


# --- harness --------------------------------------------------------------------


@dataclass(frozen=True)
class Harness:
    """One repository, its cache, a real shadow sync and a fake Understand behind it."""

    builder: GitRepoBuilder
    repo: GitRepo
    paths: CachePaths
    und: UndStub
    progress: FakeProgress
    manager: DatabaseManager

    def ensure(self, side: Side = "after", target: SyncTarget | None = None) -> AnalyzeResult:
        """Bring one side up to date, defaulting to the index (the hook's own target)."""
        return self.manager.ensure_side(side, target or IndexTarget())

    def before(self) -> AnalyzeResult:
        """Bring the before side up to date from the resolved ``HEAD`` commit."""
        head = self.repo.head()
        assert head is not None
        return self.manager.ensure_side("before", CommitTarget(commit=head))

    @property
    def commands(self) -> list[str]:
        """The database commands that ran, with ``version`` dropped as bookkeeping."""
        return [name for name in self.und.commands if name != "version"]

    def calls(self, command: str) -> list[FakeCall]:
        """Every recorded call to one command, in order."""
        return [call for call in self.und.calls if call.command == command]

    def last(self, command: str) -> FakeCall:
        """The most recent call to ``command``; fails the test when there is none."""
        found = self.calls(command)
        assert found, f"{command} never ran; commands were {self.commands}"
        return found[-1]

    def analysed(self) -> list[str]:
        """The paths of the last ``analyze``, relative to the after shadow, sorted."""
        files = self.last("analyze").arguments["files"]
        assert isinstance(files, list)
        return sorted(Path(path).relative_to(self.paths.after_tree).as_posix() for path in files)

    def reset(self) -> None:
        """Forget the recorded calls, so the next assertion is about the next run only."""
        self.und.calls.clear()

    def restart(self, version_text: str = BUILD) -> Harness:
        """The next *process*: a new manager over the same cache, as a second commit gets.

        Every other test reuses one manager across two runs, which is the harsher case for
        everything kept on disk. This is the one that matters when the thing that changed is
        held in memory -- the Understand version is read once per manager, so a test that
        changed it on a live one would be asking a question production never asks.
        """
        und = UndStub(version_text=version_text)
        settings = default_settings()
        return Harness(
            builder=self.builder,
            repo=self.repo,
            paths=self.paths,
            und=und,
            progress=self.progress,
            manager=DatabaseManager(
                self.paths,
                und,
                ShadowSync(self.repo, self.paths, settings.project),
                settings,
                self.progress,
            ),
        )

    def state(self) -> SyncState:
        """The state as it was written to disk, parsed the way ``doctor`` parses it."""
        return SyncState.model_validate_json(self.paths.state.read_text(encoding="utf-8"))


def make_harness(
    builder: GitRepoBuilder,
    cache: Path,
    settings: Settings | None = None,
    analyze_results: list[AnalyzeResult] | None = None,
) -> Harness:
    """Bind a manager to ``builder``'s repository with its cache under ``cache``."""
    effective = settings if settings is not None else default_settings()
    repo = GitRepo.discover(builder.path, FakeCommandLog())
    paths = CachePaths.for_repo(repo.common_dir, effective.understand.db_location, cache)
    und = UndStub(version_text=BUILD, analyze_results=list(analyze_results or []))
    progress = FakeProgress()
    shadow = ShadowSync(repo, paths, effective.project)
    return Harness(
        builder=builder,
        repo=repo,
        paths=paths,
        und=und,
        progress=progress,
        manager=DatabaseManager(paths, und, shadow, effective, progress),
    )


@pytest.fixture
def harness(git_repo: MakeGitRepo, tmp_path: Path) -> Harness:
    """A repository holding one Python file and one file Understand does not analyse."""
    builder = git_repo()
    builder.write("src/a.py", "def a():\n    return 1\n")
    builder.write("README.md", "hello\n")
    builder.stage()
    builder.commit("first")
    return make_harness(builder, tmp_path / "cache")


# --- first run: create, add, analyse everything (req 2.1, 2.4) -------------------


def test_a_first_run_creates_the_database_and_analyses_all_of_it(harness: Harness) -> None:
    """Requirement 2.1: no database yet means create, add the shadow root, analyse it whole."""
    result = harness.ensure()

    assert harness.commands == ["create", "add", "analyze"]
    create = harness.last("create")
    assert create.arguments["db"] == harness.paths.after_db
    assert create.arguments["local"] is True
    assert harness.last("add").arguments["root"] == harness.paths.after_tree
    analyze = harness.last("analyze")
    assert analyze.arguments["all"] is True
    assert analyze.arguments["files"] is None
    assert result.parse_errors == []


def test_the_shadow_holds_the_staged_source_before_the_database_is_built(
    harness: Harness,
) -> None:
    """The database is built from the shadow, so the shadow must be there first."""
    harness.ensure()

    assert (harness.paths.after_tree / "src" / "a.py").read_text() == "def a():\n    return 1\n"
    assert harness.paths.after_db.is_dir()


def test_the_languages_come_from_the_files_present_and_are_reported(harness: Harness) -> None:
    """Requirement 2.4: detect from the file types, and say which languages were enabled."""
    harness.ensure()

    assert harness.last("create").arguments["languages"] == ["Python"]
    assert any("Python" in note for note in harness.progress.notes), harness.progress.notes


def test_configured_languages_win_over_detection(git_repo: MakeGitRepo, tmp_path: Path) -> None:
    """Requirement 2.4 puts configuration first; a Python file cannot add Python back."""
    builder = git_repo()
    builder.write("src/a.py", "def a():\n    return 1\n")
    builder.stage()
    builder.commit("first")
    settings = default_settings().model_copy(
        update={"project": ProjectSettings(languages=["Java"])}
    )
    harness = make_harness(builder, tmp_path / "cache", settings)

    harness.ensure()

    assert harness.last("create").arguments["languages"] == ["Java"]


def test_a_repository_understand_cannot_analyse_is_refused_rather_than_reported_clean(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """A database with no language would answer every rule with silence; say so instead."""
    builder = git_repo()
    builder.write("README.md", "hello\n")
    builder.write("notes.txt", "hello\n")
    builder.stage()
    builder.commit("first")
    harness = make_harness(builder, tmp_path / "cache")

    with pytest.raises(AnalysisFailedError) as refused:
        harness.ensure()

    # Matched against the constant the module exports, not against a word that a pytest
    # directory name could have supplied: the message embeds a tmp path, and this test's own
    # directory is named after it.
    assert refused.value.hint == NO_LANGUAGE_HINT
    assert "no language to create a database for" in str(refused.value)
    assert harness.calls("create") == []


def test_the_state_records_what_the_shadow_and_the_database_hold(harness: Harness) -> None:
    """``state.json`` is what makes the next run incremental, and ``doctor`` reads it."""
    harness.ensure()

    state = harness.state()
    assert state.after_target == "index"
    assert state.after_tree_id == harness.repo.index_tree_id()
    assert state.languages == ["Python"]
    assert state.created_with == BUILD


# --- second run: only what changed (req 2.3) ------------------------------------


def test_a_second_run_with_no_change_analyses_nothing(harness: Harness) -> None:
    """The whole point of the cache: an unchanged index costs no analysis at all."""
    harness.ensure()
    harness.reset()

    harness.ensure()

    assert harness.commands == ["analyze"]
    assert harness.analysed() == []


def test_a_second_run_analyses_only_the_changed_file(harness: Harness) -> None:
    """Requirement 2.3, and the ``-files`` list is built from the shadow's own paths."""
    harness.ensure()
    harness.builder.write("src/b.py", "def b():\n    return 2\n")
    harness.builder.write("src/a.py", "def a():\n    return 11\n")
    harness.builder.stage()
    harness.reset()

    harness.ensure()

    assert harness.analysed() == ["src/a.py", "src/b.py"]
    assert harness.last("analyze").arguments["all"] is False
    assert harness.calls("create") == []


def test_a_new_file_is_added_to_the_project_before_it_is_analysed(harness: Harness) -> None:
    """Measured: ``analyze -files`` on a file no ``und add`` has enrolled exits 1."""
    harness.ensure()
    harness.builder.write("src/b.py", "def b():\n    return 2\n")
    harness.builder.stage()
    harness.reset()

    harness.ensure()

    assert harness.commands == ["add", "analyze"]
    assert harness.last("add").arguments["root"] == harness.paths.after_tree


def test_a_change_that_adds_nothing_does_not_re_add_the_root(harness: Harness) -> None:
    """``add`` re-scans the whole tree, so it runs when there is something new and not else."""
    harness.ensure()
    harness.builder.write("src/a.py", "def a():\n    return 11\n")
    harness.builder.stage()
    harness.reset()

    harness.ensure()

    assert harness.commands == ["analyze"]


def test_a_deleted_source_file_is_removed_from_the_database(harness: Harness) -> None:
    """Requirement 2.3's other half: what left the change must leave the database."""
    harness.ensure()
    harness.builder.delete("src/a.py")
    harness.reset()

    harness.ensure()

    removed = harness.last("remove_files").arguments["files"]
    assert removed == [harness.paths.after_tree / "src" / "a.py"]
    assert harness.analysed() == []


def test_a_rename_removes_the_old_path_and_analyses_the_new_one(harness: Harness) -> None:
    """7.2 delivers a rename as one deletion plus one addition; both sides must be told."""
    harness.ensure()
    harness.builder.rename("src/a.py", "src/renamed.py")
    harness.reset()

    harness.ensure()

    assert harness.last("remove_files").arguments["files"] == [
        harness.paths.after_tree / "src" / "a.py"
    ]
    assert harness.analysed() == ["src/renamed.py"]


# --- what Understand does not hold (measured: rc 1) -----------------------------


def test_a_file_understand_cannot_analyse_never_reaches_the_analyse_list(
    harness: Harness,
) -> None:
    """Measured: one ``README.md`` in the list makes the whole ``analyze -files`` exit 1."""
    harness.ensure()
    harness.builder.write("README.md", "goodbye\n")
    harness.builder.write("src/a.py", "def a():\n    return 11\n")
    harness.builder.stage()
    harness.reset()

    harness.ensure()

    assert harness.analysed() == ["src/a.py"]


def test_deleting_a_file_understand_never_held_removes_nothing(harness: Harness) -> None:
    """Measured: ``und remove`` on a path the project does not hold exits 1, quiet or not."""
    harness.ensure()
    harness.builder.delete("README.md")
    harness.reset()

    harness.ensure()

    assert harness.calls("remove_files") == []
    assert harness.commands == ["analyze"]


def test_a_change_of_only_unanalysable_files_starts_no_analysis(harness: Harness) -> None:
    """An empty selection is a no-op in the wrapper; nothing here needs to make it one."""
    harness.ensure()
    harness.builder.write("README.md", "goodbye\n")
    harness.builder.stage()
    harness.reset()

    harness.ensure()

    assert harness.analysed() == []
    assert harness.last("analyze").arguments["all"] is False


# --- names that cannot be written into a list file ------------------------------


def test_a_name_und_would_misread_falls_back_to_analysing_everything(
    harness: Harness,
) -> None:
    """Measured: ``und`` ignores a list-file line from ``#`` on, so it must not be listed."""
    harness.ensure()
    harness.builder.write("src/od#d.py", "def odd():\n    return 3\n")
    harness.builder.stage()
    harness.reset()

    harness.ensure()

    analyze = harness.last("analyze")
    assert analyze.arguments["all"] is True
    assert analyze.arguments["files"] is None
    assert any("od#d.py" in note for note in harness.progress.notes), harness.progress.notes


def test_a_deleted_name_und_would_misread_falls_back_to_analysing_everything(
    harness: Harness,
) -> None:
    """``analyze -all`` drops a file that left the disk, which is what makes this correct."""
    harness.builder.write("src/od#d.py", "def odd():\n    return 3\n")
    harness.builder.stage()
    harness.builder.commit("odd")
    harness.ensure()
    harness.builder.delete("src/od#d.py")
    harness.reset()

    harness.ensure()

    assert harness.calls("remove_files") == []
    assert harness.last("analyze").arguments["all"] is True


@pytest.mark.parametrize(
    "name",
    ["src/wi,th.py", "src/st*ar.py", "src/back\\slash.py"],
    ids=["comma", "star", "backslash"],
)
def test_every_measured_list_file_hazard_falls_back_to_analysing_everything(
    harness: Harness, name: str
) -> None:
    """Each of these was measured against ``und`` to name a different file, or none."""
    harness.ensure()
    harness.builder.write(name, "def odd():\n    return 3\n")
    harness.builder.stage()
    harness.reset()

    harness.ensure()

    assert harness.last("analyze").arguments["all"] is True


def test_a_line_break_in_a_name_falls_back_to_analysing_everything(harness: Harness) -> None:
    """The list file is one path per line, so this name would arrive as two entries."""
    harness.ensure()
    harness.builder.write("src/we\nird.py", "def odd():\n    return 3\n")
    harness.builder.stage()
    harness.reset()

    harness.ensure()

    assert harness.last("analyze").arguments["all"] is True


def test_a_relative_cache_root_falls_back_to_analysing_everything(
    git_repo: MakeGitRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``XDG_CACHE_HOME`` is taken as written, and und resolves a relative entry its own way.

    This is the reachable half of the relative-path hazard. Edge whitespace, its neighbour in
    the same predicate, is **not** reachable from here and is deliberately untested: a shadow
    path is absolute, so it cannot begin with a space, and a name that *ends* with one has a
    suffix that is not in the language table and never reaches the list file at all.
    """
    monkeypatch.chdir(tmp_path)
    builder = git_repo()
    builder.write("src/a.py", "def a():\n    return 1\n")
    builder.stage()
    builder.commit("first")
    harness = make_harness(builder, Path("relative-cache"))

    harness.ensure()
    harness.builder.write("src/a.py", "def a():\n    return 11\n")
    harness.builder.stage()
    harness.reset()
    harness.ensure()

    assert harness.last("analyze").arguments["all"] is True


def test_a_hazard_in_a_directory_component_is_caught_too(harness: Harness) -> None:
    """Measured by 6.7: a ban tested only in the leaf name is unpinned for the directories."""
    harness.ensure()
    harness.builder.write("wo#rk/a.py", "def odd():\n    return 3\n")
    harness.builder.stage()
    harness.reset()

    harness.ensure()

    assert harness.last("analyze").arguments["all"] is True


# --- when und refuses anyway (a build whose file types differ) ------------------


def test_a_refused_selective_analysis_is_retried_over_the_whole_project(
    harness: Harness,
) -> None:
    """The map is measured against one build; a different one must not break the gate."""
    harness.ensure()
    harness.builder.write("src/a.py", "def a():\n    return 11\n")
    harness.builder.stage()
    harness.reset()
    harness.und.fail["analyze"] = AnalysisFailedError("und analyze failed with exit status 1")

    harness.ensure()

    assert [call.arguments["all"] for call in harness.calls("analyze")] == [False, True]
    assert any("whole project" in note for note in harness.progress.notes), harness.progress.notes


def test_a_refused_removal_is_answered_by_analysing_everything(harness: Harness) -> None:
    """``analyze -all`` drops what left the disk, so it repairs a removal that was refused."""
    harness.ensure()
    harness.builder.delete("src/a.py")
    harness.reset()
    harness.und.fail["remove_files"] = AnalysisFailedError("und remove failed with exit status 1")

    harness.ensure()

    assert harness.last("analyze").arguments["all"] is True


def test_the_retry_guards_the_outcome_and_not_one_exception_type(harness: Harness) -> None:
    """A wrapper that failed in an unforeseen way leaves the same database behind."""
    harness.ensure()
    harness.builder.write("src/a.py", "def a():\n    return 11\n")
    harness.builder.stage()
    harness.reset()
    harness.und.fail["analyze"] = RuntimeError("something nobody predicted")

    harness.ensure()

    assert [call.arguments["all"] for call in harness.calls("analyze")] == [False, True]


def test_a_licence_failure_is_never_retried(harness: Harness) -> None:
    """A retry cannot produce a licence, and requirement 1.4 wants the licence code out."""
    harness.ensure()
    harness.builder.write("src/a.py", "def a():\n    return 11\n")
    harness.builder.stage()
    harness.reset()
    harness.und.fail["analyze"] = LicenseError("no valid license")

    with pytest.raises(LicenseError):
        harness.ensure()

    assert [call.arguments["all"] for call in harness.calls("analyze")] == [False]


def test_a_whole_project_analysis_that_fails_is_not_retried_forever(harness: Harness) -> None:
    """The fallback is the last resort, so its own failure is the run's failure."""
    harness.ensure()
    harness.builder.write("src/a.py", "def a():\n    return 11\n")
    harness.builder.stage()
    harness.reset()
    harness.und.fail["analyze"] = AnalysisFailedError("selective failed")
    harness.und.fail["analyze-all"] = AnalysisFailedError("everything failed")

    with pytest.raises(AnalysisFailedError, match="everything failed"):
        harness.ensure()

    assert len(harness.calls("analyze")) == 2


# --- invalidation: a rebuilt shadow, a new language, a new Understand -----------


def test_a_shadow_rebuilt_from_scratch_rebuilds_the_database(harness: Harness) -> None:
    """``full`` means the shadow was re-exported whole, so nothing incremental is left."""
    harness.ensure()
    harness.reset()

    harness.ensure("after", WorktreeTarget())

    assert harness.commands == ["create", "add", "analyze"]
    assert harness.last("analyze").arguments["all"] is True


def test_the_old_database_is_discarded_rather_than_created_over(harness: Harness) -> None:
    """Measured: ``und create`` over an existing database keeps its **file list**.

    So re-creating in place would carry the previous shadow's files into a database built
    for a shadow that no longer holds them -- entities of files that are not there, reported
    as this change's own. A marker inside the database directory is how that is observed
    from here, because the fake cannot be asked what a real ``create`` would have kept.
    """
    harness.ensure()
    marker = harness.paths.after_db / "stale-file-list.txt"
    marker.write_text("what the previous shadow held\n", encoding="utf-8")

    harness.ensure("after", WorktreeTarget())

    assert not marker.exists()


def test_a_file_where_the_database_belongs_is_refused_and_not_read_as_absence(
    harness: Harness,
) -> None:
    """A path that is taken but unusable is a broken cache, not a repository never analysed."""
    harness.ensure()
    shutil.rmtree(harness.paths.after_db)
    harness.paths.after_db.write_text("not a database\n", encoding="utf-8")
    harness.reset()

    with pytest.raises(AnalysisFailedError) as refused:
        harness.ensure()

    assert str(harness.paths.after_db) in str(refused.value)
    assert "rebuild" in (refused.value.hint or "")


def test_a_file_whose_language_is_not_enabled_never_reaches_the_analyse_list(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """A database created for Python does not hold the Java file, whatever its extension says.

    The selection is filtered against the languages the database was **created with**, not
    against the whole table: naming a file Understand does not hold exits 1 and takes the
    analysis of everything else in the list with it.
    """
    builder = git_repo()
    builder.write("src/a.py", "def a():\n    return 1\n")
    builder.stage()
    builder.commit("first")
    settings = default_settings().model_copy(
        update={"project": ProjectSettings(languages=["Python"])}
    )
    harness = make_harness(builder, tmp_path / "cache", settings)
    harness.ensure()
    harness.builder.write("src/App.java", "class App {}\n")
    harness.builder.write("src/a.py", "def a():\n    return 11\n")
    harness.builder.stage()
    harness.reset()

    harness.ensure()

    assert harness.analysed() == ["src/a.py"]
    assert harness.calls("create") == []


def test_a_deleted_file_does_not_add_a_language(git_repo: MakeGitRepo, tmp_path: Path) -> None:
    """Detection reads what is *in* the tree, not what left it.

    Reaching that difference takes a repository whose recorded languages do not already
    cover the deleted file, which is what unpinning ``project.languages`` produces: the
    database was created for Python, a Java file has been sitting there unanalysed, and
    deleting it must not be read as a reason to build a Java database for a file that is
    gone.
    """
    builder = git_repo()
    builder.write("src/a.py", "def a():\n    return 1\n")
    builder.write("src/App.java", "class App {}\n")
    builder.stage()
    builder.commit("first")
    pinned = default_settings().model_copy(
        update={"project": ProjectSettings(languages=["Python"])}
    )
    make_harness(builder, tmp_path / "cache", pinned).ensure()
    unpinned = make_harness(builder, tmp_path / "cache")
    builder.delete("src/App.java")

    unpinned.ensure()

    assert unpinned.calls("create") == []
    assert unpinned.state().languages == ["Python"]


def test_the_understand_version_is_asked_for_once_per_run(harness: Harness) -> None:
    """One process, one answer: the version cannot change under a running Gate."""
    harness.ensure()
    harness.before()

    assert harness.und.commands.count("version") == 1


def test_a_language_that_appears_later_rebuilds_both_databases(harness: Harness) -> None:
    """A database created for Python alone would never see the Java file, and never say so."""
    harness.ensure()
    harness.before()
    assert harness.paths.before_db.is_dir()
    harness.builder.write("src/App.java", "class App {}\n")
    harness.builder.stage()
    harness.reset()

    harness.ensure()

    assert harness.last("create").arguments["languages"] == ["Java", "Python"]
    assert harness.state().languages == ["Java", "Python"]
    assert not harness.paths.before_db.exists(), "the other side is stale and must be rebuilt"


def test_a_language_already_recorded_does_not_rebuild_anything(harness: Harness) -> None:
    """The comparison is a set, not a sequence: a second Python file changes nothing."""
    harness.ensure()
    harness.builder.write("src/b.py", "def b():\n    return 2\n")
    harness.builder.stage()
    harness.reset()

    harness.ensure()

    assert harness.calls("create") == []


def test_a_database_built_by_another_understand_is_rebuilt(harness: Harness) -> None:
    """``created_with`` is what makes an upgrade heal itself instead of failing to open."""
    harness.ensure()
    harness.builder.write("src/a.py", "def a():\n    return 11\n")
    harness.builder.stage()
    upgraded = harness.restart("(Build 9999)")

    upgraded.ensure()

    assert upgraded.commands == ["create", "add", "analyze"]
    assert upgraded.state().created_with == "(Build 9999)"


def test_the_same_understand_rebuilds_nothing_across_two_processes(harness: Harness) -> None:
    """The sibling of the test above: the version is a discriminator, not a rebuild switch."""
    harness.ensure()
    harness.builder.write("src/a.py", "def a():\n    return 11\n")
    harness.builder.stage()
    again = harness.restart()

    again.ensure()

    assert again.commands == ["analyze"]


def test_a_database_deleted_by_hand_is_created_again(harness: Harness) -> None:
    """Presence is decided by looking, so an operator who cleared the cache is served."""
    harness.ensure()
    shutil.rmtree(harness.paths.after_db)
    harness.builder.write("src/a.py", "def a():\n    return 11\n")
    harness.builder.stage()
    harness.reset()

    harness.ensure()

    assert harness.commands == ["create", "add", "analyze"]


# --- rebuild (req 2.7) ----------------------------------------------------------


def test_rebuild_discards_the_databases_and_the_recorded_state(harness: Harness) -> None:
    """Requirement 2.7: the next run must start from nothing, both sides."""
    harness.ensure()
    harness.before()
    assert harness.paths.after_db.is_dir()

    harness.manager.rebuild()

    assert not harness.paths.after_db.exists()
    assert not harness.paths.before_db.exists()
    assert not harness.paths.state.exists()


def test_rebuild_keeps_the_shadows_and_the_next_run_is_a_full_analysis(
    harness: Harness,
) -> None:
    """The shadows are re-usable working material; only the databases are discarded."""
    harness.ensure()
    harness.manager.rebuild()
    harness.reset()

    harness.ensure()

    assert (harness.paths.after_tree / "src" / "a.py").exists()
    assert harness.commands == ["create", "add", "analyze"]
    assert harness.last("analyze").arguments["all"] is True


def test_rebuild_on_a_cache_that_was_never_built_is_not_an_error(harness: Harness) -> None:
    """``db rebuild`` is what an operator reaches for when things are wrong; it must work."""
    harness.manager.rebuild()

    assert not harness.paths.state.exists()


# --- the before side ------------------------------------------------------------


def test_the_before_side_uses_its_own_shadow_and_its_own_database(harness: Harness) -> None:
    """Requirement 4.3's pre-change state, built from the committed tree, not the index."""
    harness.builder.write("src/a.py", "def a():\n    return 22\n")
    harness.builder.stage()

    harness.before()

    assert harness.last("create").arguments["db"] == harness.paths.before_db
    assert harness.last("add").arguments["root"] == harness.paths.before_tree
    assert (harness.paths.before_tree / "src" / "a.py").read_text() == "def a():\n    return 1\n"
    assert harness.state().before_commit == harness.repo.head()


def test_the_before_side_stays_incremental_across_two_commits(harness: Harness) -> None:
    """The 7.2 handoff: a resolved commit hash is a cache key, so a second run is cheap."""
    harness.before()
    harness.builder.write("src/a.py", "def a():\n    return 22\n")
    harness.builder.stage()
    harness.builder.commit("second")
    harness.reset()

    harness.before()

    assert harness.commands == ["analyze"]
    files = harness.last("analyze").arguments["files"]
    assert isinstance(files, list)
    assert [Path(path).name for path in files] == ["a.py"]


def test_the_two_sides_do_not_disturb_each_other_in_the_state_file(harness: Harness) -> None:
    """One ``state.json`` holds both sides, so each write must preserve the other's fields."""
    harness.ensure()
    harness.before()

    state = harness.state()
    assert state.after_target == "index"
    assert state.after_tree_id == harness.repo.index_tree_id()
    assert state.before_commit == harness.repo.head()


# --- state that cannot be trusted ----------------------------------------------


def test_an_unreadable_state_file_costs_a_full_analysis_and_not_the_run(
    harness: Harness,
) -> None:
    """A cache key that cannot be read is a missing cache key, never a failure."""
    harness.ensure()
    harness.paths.state.write_text("{not json at all", encoding="utf-8")
    harness.reset()

    harness.ensure()

    assert harness.last("analyze").arguments["all"] is True
    # "state" alone would be satisfied by the tmp path this note embeds, which carries this
    # test's own name -- so the phrase asserted is one only the module can produce.
    assert any("analysing from scratch" in note for note in harness.progress.notes), (
        harness.progress.notes
    )


def test_a_state_file_that_cannot_be_written_leaves_the_run_standing(
    harness: Harness,
) -> None:
    """The state is a cache key: losing it costs the next run time, not correctness."""
    harness.ensure()
    harness.paths.state.unlink()
    harness.paths.state.mkdir()
    harness.reset()

    result = harness.ensure()

    assert result.parse_errors == []
    assert any("the next run will be a full one" in note for note in harness.progress.notes), (
        harness.progress.notes
    )


def test_a_failed_analysis_leaves_the_state_alone_so_the_next_run_repeats_it(
    harness: Harness,
) -> None:
    """Recording a sync whose analysis failed would drop the change out of the next delta."""
    harness.ensure()
    recorded = harness.paths.state.read_text(encoding="utf-8")
    harness.builder.write("src/a.py", "def a():\n    return 11\n")
    harness.builder.stage()
    harness.und.fail["analyze"] = AnalysisFailedError("selective failed")
    harness.und.fail["analyze-all"] = AnalysisFailedError("everything failed")

    with pytest.raises(AnalysisFailedError):
        harness.ensure()

    assert harness.paths.state.read_text(encoding="utf-8") == recorded


def test_the_repeated_run_still_analyses_the_change_that_failed(harness: Harness) -> None:
    """The consequence the test above exists for, observed instead of argued."""
    harness.ensure()
    harness.builder.write("src/a.py", "def a():\n    return 11\n")
    harness.builder.stage()
    harness.und.fail["analyze"] = AnalysisFailedError("selective failed")
    harness.und.fail["analyze-all"] = AnalysisFailedError("everything failed")
    with pytest.raises(AnalysisFailedError):
        harness.ensure()
    harness.reset()

    harness.ensure()

    assert harness.analysed() == ["src/a.py"]


# --- parse errors (req 2.6) -----------------------------------------------------


def test_the_parse_errors_of_the_analysis_are_handed_back(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """Requirement 2.6: the errors are data the run reports, never a reason to stop."""
    builder = git_repo()
    builder.write("src/a.py", "def a(:\n")
    builder.stage()
    builder.commit("first")
    broken = AnalyzeResult(
        parse_errors=[ParseError(path=Path("src/a.py"), line=1, message="expected token")],
        warnings=2,
        seconds=0.5,
    )
    harness = make_harness(builder, tmp_path / "cache", analyze_results=[broken])

    result = harness.ensure()

    assert result.parse_errors == broken.parse_errors
    assert result.warnings == 2


def unreadable(harness: Harness, rel: str, line: int = 1, side: Side = "after") -> ParseError:
    """A parse error in the shadow, spelled the way the real ``und`` spells it.

    Absolute, and under the side's shadow tree, because that is what was measured: a real run
    of this Gate over a PEP 695 declaration reported
    ``<cache>/<repo id>/after/pkg/generic.py``, not the repository-relative name. A fake that
    handed back the tidy form would test the assertion rather than the code -- the whole of
    the relativisation would be dead and nothing here would notice.
    """
    tree = harness.paths.before_tree if side == "before" else harness.paths.after_tree
    return ParseError(path=tree / rel, line=line, message="expected token '(' at token [")


def test_a_parse_error_inside_the_shadow_is_reported_repository_relative(
    harness: Harness,
) -> None:
    """The path an operator can act on, and the one an entity key can be compared with."""
    harness.und.analyze_results.append(
        AnalyzeResult(parse_errors=[unreadable(harness, "src/a.py")], seconds=0.0)
    )

    result = harness.ensure()

    assert [error.path.as_posix() for error in result.parse_errors] == ["src/a.py"]


def test_a_parse_error_outside_the_shadow_is_dropped_rather_than_reported(
    harness: Harness,
) -> None:
    """The other half of the same decision, with a different input rather than a different name.

    Understand follows an import out of the project and reports what it finds there: task 10.4
    measured four such errors on a clean run of this repository, and one real run of a 770-file
    project reported **63**, in the interpreter's own ``typing.py``, ``pdb.py`` and
    ``_pyrepl``. Those files belong to no commit, nobody can fix them from here, and task
    11.11's non-blocking treatment was not enough because they still printed. They are dropped.
    """
    stdlib = Path("/usr/lib/python3.12/inspect.py")
    harness.und.analyze_results.append(
        AnalyzeResult(
            parse_errors=[ParseError(path=stdlib, line=9, message="unknown token")], seconds=0.0
        )
    )

    result = harness.ensure()

    assert result.parse_errors == []


def test_a_parse_error_inside_the_shadow_survives_beside_one_outside_it(
    harness: Harness,
) -> None:
    """The negative control: dropping the noise must not drop the signal with it.

    A rule that silenced both would pass a test that only looked at the standard library, and
    it is the file *inside* the shadow that requirement 2.6 exists for -- the analysis stops
    where the parse stops, so every rule below reports success over code it never read.
    """
    stdlib = Path("/usr/lib/python3.12/inspect.py")
    harness.und.analyze_results.append(
        AnalyzeResult(
            parse_errors=[
                ParseError(path=stdlib, line=9, message="unknown token"),
                unreadable(harness, "src/a.py"),
            ],
            seconds=0.0,
        )
    )

    result = harness.ensure()

    assert [error.path.as_posix() for error in result.parse_errors] == ["src/a.py"]


def test_a_warm_run_still_names_what_the_database_could_not_read(harness: Harness) -> None:
    """Task 11.13: the errors belong to the database, not to the run that did the parsing.

    Measured before this existed: a cold staged run over this repository printed ``9 files
    failed to parse, not fully checked`` and three consecutive warm runs over the same two
    databases printed none. A git hook is always warm, so requirement 2.6's report -- the one
    that stops a partially-read file being taken for a clean one -- reached the operator least
    likely to need it and never reached the one most likely to.
    """
    harness.und.analyze_results.append(
        AnalyzeResult(parse_errors=[unreadable(harness, "src/a.py")], seconds=0.0)
    )
    cold = harness.ensure()
    harness.reset()

    warm = harness.ensure()

    assert harness.last("analyze").arguments["files"] == [], "nothing changed, so nothing re-read"
    assert [error.path.as_posix() for error in cold.parse_errors] == ["src/a.py"]
    assert [error.path.as_posix() for error in warm.parse_errors] == ["src/a.py"]


def test_re_analysing_a_file_that_now_parses_drops_its_recorded_error(
    harness: Harness,
) -> None:
    """The positive case the test above needs, or "still reported" would mean "never cleared".

    An assertion that an error is still there passes just as happily when the record can never
    be emptied, which would leave a repository permanently blocked on a syntax error it fixed
    three commits ago. So the same file is edited, re-analysed, and comes back clean.
    """
    harness.und.analyze_results.append(
        AnalyzeResult(parse_errors=[unreadable(harness, "src/a.py")], seconds=0.0)
    )
    harness.ensure()
    harness.builder.write("src/a.py", "def a():\n    return 2\n")
    harness.builder.stage()
    harness.reset()

    warm = harness.ensure()

    assert harness.analysed() == ["src/a.py"]
    assert warm.parse_errors == []


def test_a_file_the_run_never_re_read_keeps_its_error_while_another_is_cleared(
    harness: Harness,
) -> None:
    """One selective pass, two files, opposite answers -- which is the whole of the merge rule."""
    harness.builder.write("src/b.py", "def b():\n    return 1\n")
    harness.builder.stage()
    harness.builder.commit("second file")
    harness.und.analyze_results.append(
        AnalyzeResult(
            parse_errors=[unreadable(harness, "src/a.py"), unreadable(harness, "src/b.py", 4)],
            seconds=0.0,
        )
    )
    harness.ensure()
    harness.builder.write("src/b.py", "def b():\n    return 2\n")
    harness.builder.stage()
    harness.reset()

    warm = harness.ensure()

    assert harness.analysed() == ["src/b.py"]
    assert [error.path.as_posix() for error in warm.parse_errors] == ["src/a.py"]


def test_the_recorded_parse_errors_survive_a_new_process(harness: Harness) -> None:
    """They are written to ``state.json``, so the next *commit* reads them and not this object."""
    harness.und.analyze_results.append(
        AnalyzeResult(parse_errors=[unreadable(harness, "src/a.py")], seconds=0.0)
    )
    harness.ensure()

    recorded = harness.state()
    warm = harness.restart().ensure()

    assert [error.path.as_posix() for error in recorded.parse_errors["after"]] == ["src/a.py"]
    assert [error.path.as_posix() for error in warm.parse_errors] == ["src/a.py"]


def test_discarding_the_databases_forgets_what_they_could_not_read(harness: Harness) -> None:
    """A record of a database that no longer exists would be re-reported forever.

    The Understand version is what changes here, because that is one of the two things
    :meth:`DatabaseManager._invalidate` discards **both** databases for. The next pass is a
    full one, so whatever the new build cannot read it says for itself.
    """
    harness.und.analyze_results.append(
        AnalyzeResult(parse_errors=[unreadable(harness, "src/a.py")], seconds=0.0)
    )
    harness.ensure()

    upgraded = harness.restart(version_text="(Build 1300)")
    fresh = upgraded.ensure()

    assert upgraded.last("analyze").arguments["all"] is True
    assert fresh.parse_errors == []
    assert upgraded.state().parse_errors == {"after": []}


# --- the cache directory (req 2.1, 2.2, 2.8, and the 7.2 handoff) ---------------


def test_the_cache_root_is_readable_only_by_its_owner(harness: Harness) -> None:
    """The shadows and the databases hold the repository's source; nobody else may read."""
    harness.ensure()

    assert stat.S_IMODE(harness.paths.root.stat().st_mode) == CACHE_MODE


def test_a_cache_root_that_already_exists_is_corrected(harness: Harness) -> None:
    """``mkdir(exist_ok=True)`` does not touch the mode, and 7.2 left the root at 0o775."""
    harness.paths.root.mkdir(parents=True)
    harness.paths.root.chmod(0o755)

    harness.ensure()

    assert stat.S_IMODE(harness.paths.root.stat().st_mode) == CACHE_MODE


def test_the_root_is_owner_only_before_the_database_is_written_into_it(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """The order is the whole point: a database created first is readable while it is built."""
    builder = git_repo()
    builder.write("src/a.py", "def a():\n    return 1\n")
    builder.stage()
    builder.commit("first")
    harness = make_harness(builder, tmp_path / "cache")
    seen: list[int] = []

    class ModeWatchingUnd(UndStub):
        def create(self, db: Path, languages: list[str], local: bool = True) -> None:
            seen.append(stat.S_IMODE(harness.paths.root.stat().st_mode))
            super().create(db, languages, local)

    watching = ModeWatchingUnd(version_text=BUILD)
    manager = DatabaseManager(
        harness.paths,
        watching,
        ShadowSync(harness.repo, harness.paths, default_settings().project),
        default_settings(),
        harness.progress,
    )
    manager.ensure_side("after", IndexTarget())

    assert seen == [CACHE_MODE]


def test_the_cache_lands_under_the_git_directory_when_configured(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """Requirement 2.2 either way: ``.git/scitools-hook`` is outside the working tree."""
    builder = git_repo()
    builder.write("src/a.py", "def a():\n    return 1\n")
    builder.stage()
    builder.commit("first")
    settings = default_settings().model_copy(
        update={"understand": UnderstandSettings(db_location="gitdir")}
    )
    harness = make_harness(builder, tmp_path / "cache", settings)

    harness.ensure()

    assert harness.paths.after_db == harness.repo.common_dir / "scitools-hook" / "after.und"
    assert harness.paths.after_db.is_dir()
    assert harness.manager.paths() is harness.paths


def test_the_cache_lands_under_the_user_cache_directory_by_default(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """The convention is ``runner.context.cache_dir``; this pins the pair, not a second one."""
    builder = git_repo()
    builder.write("src/a.py", "def a():\n    return 1\n")
    builder.stage()
    builder.commit("first")
    cache = tmp_path / "xdg"
    paths = CachePaths.for_repo(
        GitRepo.discover(builder.path, FakeCommandLog()).common_dir,
        "cache",
        cache_dir({"XDG_CACHE_HOME": str(cache)}),
    )

    assert paths.root.parent == cache / APP_NAME
    assert paths.after_db.parent == paths.root


def test_nothing_is_written_inside_the_working_tree(harness: Harness) -> None:
    """Requirement 2.2, observed over the whole tree rather than at the cache."""
    before = tree_entries(harness.builder.path)

    harness.ensure()
    harness.before()

    assert tree_entries(harness.builder.path) == before


def tree_entries(root: Path) -> set[str]:
    """Every path under ``root`` except ``.git``, which git itself writes into."""
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if ".git" not in path.relative_to(root).parts
    }


# --- progress (req 4.11) --------------------------------------------------------


def test_every_phase_is_reported_so_a_slow_one_can_be_named(harness: Harness) -> None:
    """4.11's five-second rule lives in ``ConsoleProgress``; the phases have to reach it."""
    harness.ensure()

    assert [phase for phase, _ in harness.progress.finished] == harness.progress.started
    assert any("after" in phase for phase in harness.progress.started), harness.progress.started
    assert len(harness.progress.finished) >= 2


def test_a_phase_is_finished_with_the_time_it_took(harness: Harness) -> None:
    """A duration of zero would make the five-second rule unreachable."""
    harness.ensure()

    assert all(seconds >= 0.0 for _, seconds in harness.progress.finished)
    assert any(seconds > 0.0 for _, seconds in harness.progress.finished)


# --- language detection ---------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "language"),
    [
        ("a.py", "Python"),
        ("a.upy", "Python"),
        ("a.c", "C++"),
        ("a.h", "C++"),
        ("a.C", "C++"),
        ("a.cpp", "C++"),
        ("a.cs", "C#"),
        ("App.java", "Java"),
        ("a.f90", "Fortran"),
        ("a.ads", "Ada"),
        ("a.vhd", "VHDL"),
        ("a.js", "Web"),
        ("a.ts", "Web"),
        ("a.pas", "Pascal"),
        ("a.jov", "Jovial"),
        ("q.sql", "Pascal"),
        ("a.vb", "Basic"),
        ("a.asm", "Assembly"),
    ],
)
def test_each_measured_extension_maps_to_the_language_understand_enrolled_it_under(
    harness: Harness, name: str, language: str
) -> None:
    """Every pair here was measured by building a database and listing its files."""
    assert harness.manager.detect_languages([Path(name)]) == [language]


@pytest.mark.parametrize("name", ["README.md", "a.txt", "a.PY", "a.rs", "Makefile", "a.pl"])
def test_a_file_understand_does_not_enrol_contributes_no_language(
    harness: Harness, name: str
) -> None:
    """Measured: the extension table is case-sensitive, and it has no ``.md`` or ``.rs``."""
    assert harness.manager.detect_languages([Path(name)]) == []


def test_the_languages_are_sorted_and_free_of_repeats(harness: Harness) -> None:
    """The set goes into ``state.json`` and into a command line; it must be deterministic."""
    files = [Path("z.py"), Path("a.cpp"), Path("b.py"), Path("c.h")]

    assert harness.manager.detect_languages(files) == ["C++", "Python"]


def test_the_map_only_names_languages_und_create_accepts(harness: Harness) -> None:
    """Measured: ``und create -languages JavaScript`` exits 1; only these twelve are real."""
    installed = {
        "Ada",
        "Assembly",
        "Basic",
        "C++",
        "C#",
        "Fortran",
        "Java",
        "Jovial",
        "Pascal",
        "Python",
        "VHDL",
        "Web",
    }

    assert set(LANGUAGE_BY_SUFFIX.values()) <= installed


def detected(manager: DatabaseManager, names: Iterable[str]) -> list[str]:
    """``detect_languages`` over plain file names, for the tests that read better that way."""
    return manager.detect_languages(Path(name) for name in names)


def test_detection_reads_the_suffix_and_not_the_whole_name(harness: Harness) -> None:
    """A directory called ``py`` or a file called ``python`` is not a Python file."""
    assert detected(harness.manager, ["python", "py", "src.py.bak"]) == []


# --- what the JSON on disk looks like -------------------------------------------


def test_the_state_file_is_json_a_reader_outside_this_module_can_parse(
    harness: Harness,
) -> None:
    """``doctor`` reads this file with pydantic; a human reads it with their eyes."""
    harness.ensure()

    document = json.loads(harness.paths.state.read_text(encoding="utf-8"))
    assert document["after_target"] == "index"
    assert document["languages"] == ["Python"]


# --- the declared architecture (req 6.3, 6.7) -----------------------------------
#
# `scitools-hook.arch.xml` is what turns `structure.layers` from a rule about folders into a
# rule about layers: `"Directory Structure"` is derived from the directory tree, so a layer
# that spans two directories, or two layers that share one, cannot be expressed at all
# without an architecture the repository declares. The measurements these tests encode were
# taken against Understand build 1204 and are stated where they decide a case; the licensed
# evidence is in `tests/contract/test_architecture_contract.py`.


def declare(harness: Harness, *members: str, name: str = "Layers") -> None:
    """Commit an architecture declaration naming ``members``, repository-relative."""
    tree = ArchNode(name=name, children=(ArchNode(name="shells", members=members),))
    harness.builder.write(ARCH_FILE, write_architecture(tree))
    harness.builder.stage()


def shadow(harness: Harness, side: Side, *names: str) -> list[str]:
    """The absolute paths those repository-relative names have inside one shadow tree."""
    tree = harness.paths.after_tree if side == "after" else harness.paths.before_tree
    return [os.path.realpath(tree / name) for name in names]


def test_no_declaration_means_no_architecture_command_at_all(harness: Harness) -> None:
    """The default costs nothing: no file, no ``und`` call, folders as before."""
    harness.ensure()

    assert "declare_architecture" not in harness.commands


def test_a_declaration_is_imported_after_the_analysis(harness: Harness) -> None:
    """Measured, and the single worst way to get this wrong.

    An ``import -arch`` into a database that has been ``add``-ed but not analysed produces an
    architecture whose nodes are **empty**, exits 0, lists the architecture, and is not filled
    in by the analysis that follows. Every layer rule then reads an empty node set as "no
    finding", so the order here is the feature.
    """
    declare(harness, "src/a.py")

    harness.ensure()

    assert harness.commands == ["create", "add", "analyze", "declare_architecture"]


def test_the_declared_members_reach_und_as_absolute_shadow_paths(harness: Harness) -> None:
    """Measured: ``src/a.py`` written literally into the document resolves to *nothing*.

    ``und import -arch`` answers ``Architecture imported.`` with status 0 and an empty node,
    while a bare ``a.py`` resolves by short name -- a coin toss between two files of that name
    -- and an absolute path resolves exactly. So the repository-relative declaration is
    rewritten against the side's own shadow before ``und`` is asked.
    """
    declare(harness, "src/a.py")

    harness.ensure()

    assert harness.last("declare_architecture").arguments["members"] == shadow(
        harness, "after", "src/a.py"
    )


def test_the_two_sides_get_the_same_declaration_over_their_own_shadows(
    harness: Harness,
) -> None:
    """One repository file, two shadow trees; a path from the wrong side resolves to nothing."""
    declare(harness, "src/a.py")
    harness.builder.commit("declare")

    harness.ensure()
    harness.before()

    members = [call.arguments["members"] for call in harness.calls("declare_architecture")]
    assert members == [shadow(harness, "after", "src/a.py"), shadow(harness, "before", "src/a.py")]


def test_a_declaration_is_re_imported_on_a_warm_run(harness: Harness) -> None:
    """Measured: an imported architecture survives ``analyze``, and a re-import exits 1.

    So the manager cannot simply leave a warm database alone -- it would keep whatever was
    declared when the database was built, and an edited declaration would not take effect
    until the next ``db rebuild``. ``UndCli.declare_architecture`` removes before it imports
    for exactly this reason, and this test is what pins the second run happening at all.
    """
    declare(harness, "src/a.py")
    harness.ensure()
    harness.reset()

    harness.builder.write("src/a.py", "def a():\n    return 2\n")
    harness.builder.stage()
    harness.ensure()

    assert harness.commands == ["analyze", "declare_architecture"]


def test_a_member_the_side_does_not_hold_is_named_on_the_progress_stream(
    harness: Harness,
) -> None:
    """Dropped, but not silently: a typo in a declared path looks exactly like this.

    A file the change adds is legitimately missing from the before shadow, so this cannot be
    an error -- which is precisely why it may not be silence either, or a misspelt path would
    take a file out of its layer with nothing to see it by.
    """
    harness.builder.write("src/new.py", "def n():\n    return 1\n")
    declare(harness, "src/a.py", "src/new.py")

    harness.before()

    assert any("src/new.py" in note for note in harness.progress.notes)


def test_a_member_the_side_does_not_hold_is_dropped_rather_than_refused(
    harness: Harness,
) -> None:
    """The ordinary state of the before side of a change that adds a file.

    ``src/new.py`` is staged but not committed, so it is in the *after* shadow and not in the
    *before* one. A declaration that named it and then failed would make the gate unusable on
    every commit that adds a file to a declared layer.
    """
    harness.builder.write("src/new.py", "def n():\n    return 1\n")
    declare(harness, "src/a.py", "src/new.py")

    harness.before()

    assert harness.last("declare_architecture").arguments["members"] == shadow(
        harness, "before", "src/a.py"
    )


def test_a_member_that_is_there_but_und_would_not_take_is_named(harness: Harness) -> None:
    """The anti-false-green case, and the reason the import is read back at all.

    ``und import -arch`` takes a document naming a directory, a file of a language the project
    does not analyse, or a plain typo, answers ``Architecture imported.``, exits 0, and keeps
    none of them. A file that *is* in the shadow and still did not survive is therefore a
    defect in the declaration, and is named rather than quietly dropped.
    """
    harness.und.dropped = frozenset(shadow(harness, "after", "README.md"))
    declare(harness, "src/a.py", "README.md")

    with pytest.raises(AnalysisFailedError) as caught:
        harness.ensure()

    assert "README.md" in str(caught.value)
    assert ARCH_FILE in str(caught.value)


def test_a_declaration_naming_nothing_that_resolved_says_so(harness: Harness) -> None:
    """The catastrophic shape: every path wrong, an architecture of empty nodes, status 0."""
    harness.und.dropped = frozenset(shadow(harness, "after", "src/a.py", "README.md"))
    declare(harness, "src/a.py", "README.md")

    with pytest.raises(AnalysisFailedError) as caught:
        harness.ensure()

    assert "none of them" in str(caught.value)


@pytest.mark.parametrize("escape", ("/etc/passwd", "../outside.py"))
def test_a_member_outside_the_repository_is_refused(harness: Harness, escape: str) -> None:
    """An absolute path is not portable and a ``../`` walk leaves the tree being analysed."""
    declare(harness, escape)

    with pytest.raises(AnalysisFailedError) as caught:
        harness.ensure()

    assert escape in str(caught.value)


def test_a_malformed_declaration_names_the_file_rather_than_reaching_und(
    harness: Harness,
) -> None:
    harness.builder.write(ARCH_FILE, "<arch name='Layers'>\n")
    harness.builder.stage()

    with pytest.raises(AnalysisFailedError) as caught:
        harness.ensure()

    assert ARCH_FILE in str(caught.value)
    assert "declare_architecture" not in harness.commands


def test_a_declaration_named_after_the_built_in_architecture_is_refused(
    harness: Harness,
) -> None:
    """Measured: importing under that name *merges* into the folder-derived architecture.

    It does not replace it and it is not refused as a duplicate the way any other name would
    be, and ``und remove -arch "Directory Structure"`` exits 0 without removing anything -- so
    a repository that made this mistake would carry the merged nodes in every database it ever
    built until the cache was thrown away.
    """
    declare(harness, "src/a.py", name="Directory Structure")

    with pytest.raises(AnalysisFailedError) as caught:
        harness.ensure()

    assert "Directory Structure" in str(caught.value)
    assert "declare_architecture" not in harness.commands


def test_a_declaration_that_is_a_directory_is_refused(harness: Harness) -> None:
    """A name taken by something that is not a readable file is not "no declaration"."""
    (harness.builder.path / ARCH_FILE).mkdir()

    with pytest.raises(AnalysisFailedError) as caught:
        harness.ensure()

    assert ARCH_FILE in str(caught.value)


def test_export_architecture_answers_repository_relative_paths(harness: Harness) -> None:
    """The exported document has to be committable, so it may not name this machine's cache."""
    harness.ensure()
    harness.und.exported = ArchNode(
        name="Directory Structure",
        children=(ArchNode(name="src", members=tuple(shadow(harness, "after", "src/a.py"))),),
    )

    document = harness.manager.export_architecture("after")

    assert "src/a.py" in document
    assert str(harness.paths.after_tree) not in document
    assert harness.last("export_arch").arguments["name"] == "Directory Structure"


def test_export_architecture_needs_a_database_to_read_one_out_of(harness: Harness) -> None:
    with pytest.raises(AnalysisFailedError) as caught:
        harness.manager.export_architecture("after")

    assert str(harness.paths.after_db) in str(caught.value)
