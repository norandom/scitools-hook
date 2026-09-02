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
    CACHE_MODE,
    LANGUAGE_BY_SUFFIX,
    NO_LANGUAGE_HINT,
    DatabaseManager,
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

BUILD = "(Build 1204)"
"""What ``und version`` prints on the machine these tests were measured against."""


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
