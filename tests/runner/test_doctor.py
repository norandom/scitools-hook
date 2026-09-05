"""``doctor``: the report that must survive everything it diagnoses (task 8.2; req 1.5, 12.5).

Requirement 1.5 asks for the installation directory, the version, the API status, the license
status, the git status, the analysis database's location and state, and the effective
configuration with the file each setting came from. Requirement 12.5 adds that it must run
outside a repository. Between them they fix the one property every test below is really
about: **doctor reports, it never raises.** An operator runs it precisely when something is
broken, so every probe that can fail turns into a ``problems`` entry naming what failed.

"Does not raise" is not, on its own, an assertion worth writing -- a function that swallowed
everything and returned an empty report would satisfy it. So every test here asserts on the
*content* of the report: which problem was recorded, what each API probe answered, which mode
was chosen, what the git and cache sections say.

The installations these tests diagnose are shell scripts. That is deliberate: it exercises
:class:`RealProbes` running real subprocesses -- the wiring between the locator's injected
probes and the processes that answer them -- on a machine with no Understand at all.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
from conftest import FakeCommandLog, MakeGitRepo
from doctor_stubs import (
    API_VERSION,
    IN_PROCESS_VERSION,
    executable,
    install,
    isolated_env,
    options,
    problem_about,
    seam,
    time_limit,
)

from scitools_hook.config.defaults import DEFAULT_THRESHOLDS
from scitools_hook.errors import AnalysisFailedError
from scitools_hook.git.repo import GitRepo
from scitools_hook.models.cache import CachePaths, SyncState
from scitools_hook.runner import doctor as doctor_module
from scitools_hook.runner.context import ContextOptions, build_context
from scitools_hook.runner.doctor import DoctorReport, run_doctor
from scitools_hook.understand.catalogue import kind_string
from scitools_hook.understand.fake import FAKE_VAR, FIXTURE_VERSION
from scitools_hook.understand.locator import platform_bin

pytestmark = pytest.mark.skipif(
    sys.platform.startswith("win32"), reason="the stub installations are POSIX shell scripts"
)

# --- a healthy installation: both probes and the chosen mode (req 1.5) -----------


def _too_deep(*_args: object, **_kwargs: object) -> object:
    """Stand in for a document the parser cannot descend, so the threshold is not asserted."""
    raise RecursionError("maximum recursion depth exceeded")


def test_a_healthy_installation_reports_its_directory_version_and_license(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """The first four things requirement 1.5 asks for, from a real subprocess each."""
    repo = git_repo()
    home = install(tmp_path / "scitools")
    env = isolated_env(tmp_path, SCITOOLS_HOME=str(home))
    report = run_doctor(options(repo.path, env, command_log))
    assert report.understand.env is not None
    assert report.understand.env.home == home
    assert report.understand.und_version == "(Build 1204)"
    assert report.understand.license is not None
    assert report.understand.license.ok


def test_both_api_probes_are_reported_and_upython_is_the_chosen_mode(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """Task 8.2 asks for *both* probes: the operator has to see which one is broken."""
    repo = git_repo()
    home = install(tmp_path / "scitools")
    env = isolated_env(tmp_path, SCITOOLS_HOME=str(home))
    report = run_doctor(options(repo.path, env, command_log))
    probes = {probe.mode: probe for probe in report.understand.probes}
    assert [probe.mode for probe in report.understand.probes] == ["upython", "inprocess"]
    assert probes["upython"].ok
    assert probes["upython"].version == API_VERSION
    assert not probes["inprocess"].ok
    assert probes["inprocess"].detail
    assert report.understand.api_mode == "upython"
    assert report.understand.verified


def test_the_in_process_probe_imports_the_api_in_a_child_interpreter(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """The probe must reach the installation's own Python directory, and only that.

    With an importable module there and no ``upython`` at all, in-process is the mode that
    works and the one ``auto`` falls back to. This is also what pins the probe's environment:
    the module is importable *only* because the API directory reaches the child interpreter.
    """
    repo = git_repo()
    home = install(tmp_path / "scitools", upython=False, api="stub")
    env = isolated_env(tmp_path, SCITOOLS_HOME=str(home))
    report = run_doctor(options(repo.path, env, command_log))
    probes = {probe.mode: probe for probe in report.understand.probes}
    assert probes["inprocess"].ok
    assert probes["inprocess"].version == IN_PROCESS_VERSION
    assert not probes["upython"].ok
    assert report.understand.api_mode == "inprocess"
    assert report.understand.verified


def test_a_upython_that_cannot_run_is_reported_and_the_other_mode_takes_over(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """A bundled interpreter that exits non-zero has answered "no", and the report says so."""
    repo = git_repo()
    home = install(tmp_path / "scitools", api="stub", mode="broken")
    env = isolated_env(tmp_path, SCITOOLS_HOME=str(home))
    report = run_doctor(options(repo.path, env, command_log))
    probes = {probe.mode: probe for probe in report.understand.probes}
    assert not probes["upython"].ok
    assert probes["upython"].detail
    assert probes["inprocess"].ok
    assert report.understand.api_mode == "inprocess"


def test_an_api_that_answers_with_a_refusal_is_not_a_working_mode(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """A refusal envelope decides, even when the same document also carries a version."""
    repo = git_repo()
    home = install(tmp_path / "scitools", mode="refusing")
    env = isolated_env(tmp_path, SCITOOLS_HOME=str(home))
    report = run_doctor(options(repo.path, env, command_log))
    probes = {probe.mode: probe for probe in report.understand.probes}
    assert not probes["upython"].ok
    assert probes["upython"].version == ""
    assert report.understand.api_mode is None


def test_an_installation_whose_api_loads_in_no_mode_is_a_problem_not_a_crash(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """``und`` is there and runs; nothing can load the API. That is the report, not an exception."""
    repo = git_repo()
    home = install(tmp_path / "scitools", upython=False)
    env = isolated_env(tmp_path, SCITOOLS_HOME=str(home))
    report = run_doctor(options(repo.path, env, command_log))
    assert report.understand.env is not None
    assert report.understand.env.home == home
    assert report.understand.api_mode is None
    assert not report.understand.verified
    assert [probe.ok for probe in report.understand.probes] == [False, False]
    assert problem_about(report, "API")


def test_no_installation_anywhere_names_the_locations_that_were_tried(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """Requirement 1.3's list survives into the report instead of ending the command."""
    repo = git_repo()
    report = run_doctor(options(repo.path, isolated_env(tmp_path), command_log))
    assert report.understand.env is None
    assert report.understand.probes == []
    reported = problem_about(report, "SCITOOLS_HOME")
    assert "wellknown:" in reported
    assert report.git.inside_repository


# --- git and the cache (req 1.5, 12.5) -------------------------------------------


def test_inside_a_repository_the_git_section_names_the_working_tree(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """ "the git repository status" is where the operator confirms the Gate found the right one."""
    repo = git_repo()
    repo.write("a.py", "x = 1\n")
    repo.stage()
    head = repo.commit("first")
    _, env = seam(tmp_path)
    report = run_doctor(options(repo.path, env, command_log))
    assert report.git.inside_repository
    assert report.git.root == repo.path.resolve()
    assert report.git.head == head


def test_outside_a_repository_doctor_still_produces_a_report(
    tmp_path: Path, command_log: FakeCommandLog
) -> None:
    """Requirement 12.5 names ``doctor`` explicitly; this is that requirement."""
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    _, env = seam(tmp_path)
    report = run_doctor(options(outside, env, command_log))
    assert not report.git.inside_repository
    assert report.git.root is None
    assert report.cache is None
    assert report.state is None
    assert report.understand.env is not None
    # Git's own words, not a paraphrase: a corrupt HEAD and a bare repository fail this call
    # identically, and only the message git printed tells them apart.
    assert "fatal" in report.git.detail
    assert str(outside) in report.git.detail


def test_an_unborn_branch_is_a_repository_with_no_head(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """The state a pre-commit hook meets on the very first commit."""
    repo = git_repo()
    _, env = seam(tmp_path)
    report = run_doctor(options(repo.path, env, command_log))
    assert report.git.inside_repository
    assert report.git.head is None


def test_the_cache_location_and_sync_state_are_read_back(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """ "the location and state of the repository's analysis database" (req 1.5, 2.8)."""
    repo = git_repo()
    (repo.path / "scitools-hook.toml").write_text(
        '[understand]\ndb_location = "gitdir"\n', encoding="utf-8"
    )
    paths = CachePaths.for_repo(repo.path.resolve() / ".git", "gitdir")
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.state.write_text(
        json.dumps({"after_target": "index", "after_tree_id": "abc", "created_with": "6.5"}),
        encoding="utf-8",
    )
    _, env = seam(tmp_path)
    report = run_doctor(options(repo.path, env, command_log))
    assert report.cache is not None
    assert report.cache.root == paths.root
    assert report.state is not None
    assert report.state.after_target == "index"
    assert report.state.created_with == "6.5"
    # A cache that exists, is a directory and is readable is the healthy path, so the checks
    # guarding the unhealthy ones must stay silent here or every analysed repository is told
    # its cache is broken on every run.
    assert not [problem for problem in report.problems if "cache root" in problem]


def test_an_unreadable_sync_state_is_a_problem_and_the_paths_are_still_reported(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """A corrupt ``state.json`` is exactly what an operator runs ``doctor`` to find."""
    repo = git_repo()
    (repo.path / "scitools-hook.toml").write_text(
        '[understand]\ndb_location = "gitdir"\n', encoding="utf-8"
    )
    paths = CachePaths.for_repo(repo.path.resolve() / ".git", "gitdir")
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.state.write_text("{ truncated", encoding="utf-8")
    _, env = seam(tmp_path)
    report = run_doctor(options(repo.path, env, command_log))
    assert report.state is None
    assert report.cache is not None
    assert problem_about(report, "state.json")


# --- the effective configuration and its sources (req 1.5) ----------------------


def test_the_effective_configuration_is_reported_with_the_file_each_value_came_from(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """The last clause of requirement 1.5, and the reason a ``Provenance`` exists at all."""
    repo = git_repo()
    (repo.path / "scitools-hook.toml").write_text(
        "[thresholds.routine]\nMaxNesting = 2\n", encoding="utf-8"
    )
    _, env = seam(tmp_path)
    report = run_doctor(options(repo.path, env, command_log))
    nesting = next(spec for spec in report.settings.thresholds if spec.rule == "routine.MaxNesting")
    assert nesting.limit.max == 2
    source = report.settings_provenance.values["thresholds.routine.MaxNesting"]
    assert source == f"repo:{repo.path.resolve() / 'scitools-hook.toml'}"


def test_a_broken_configuration_is_a_problem_and_the_rest_of_the_report_survives(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """A configuration error is what stops every other command; doctor is where it is explained."""
    repo = git_repo()
    (repo.path / "scitools-hook.toml").write_text("[nonsense]\nwhat = 1\n", encoding="utf-8")
    _, env = seam(tmp_path)
    report = run_doctor(options(repo.path, env, command_log))
    assert problem_about(report, "scitools-hook.toml")
    assert report.git.inside_repository
    assert report.understand.env is not None


def test_a_language_understand_computes_nothing_for_is_a_problem_not_an_exception(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """Task 2.4 made this a ``ConfigError``; doctor is the one command that must survive it."""
    repo = git_repo()
    (repo.path / "scitools-hook.toml").write_text(
        '[project]\nlanguages = ["Pyhton"]\n', encoding="utf-8"
    )
    fixtures, env = seam(tmp_path)
    kinds: dict[str, list[str]] = {kind_string("Pyhton", scope): [] for scope in DEFAULT_THRESHOLDS}
    (fixtures / "catalogue.json").write_text(json.dumps({"metrics": kinds}), encoding="utf-8")
    report = run_doctor(options(repo.path, env, command_log))
    assert problem_about(report, "project.languages")


def test_a_threshold_this_repository_cannot_evaluate_is_reported(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """Requirement 5.5: a dropped default is legitimate, and it must not be dropped silently."""
    repo = git_repo()
    (repo.path / "scitools-hook.toml").write_text(
        '[project]\nlanguages = ["Python"]\n', encoding="utf-8"
    )
    fixtures, env = seam(tmp_path)
    kinds = {
        kind_string("Python", scope): sorted(set(table) - {"PercentLackOfCohesion"})
        for scope, table in DEFAULT_THRESHOLDS.items()
    }
    (fixtures / "catalogue.json").write_text(json.dumps({"metrics": kinds}), encoding="utf-8")
    report = run_doctor(options(repo.path, env, command_log))
    reported = problem_about(report, "class.PercentLackOfCohesion")
    assert "Python" in reported


def test_the_test_seam_is_reported_so_no_one_mistakes_fixtures_for_analysis(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """A doctor report that looked healthy while reading fixtures would be a trap."""
    repo = git_repo()
    fixtures, env = seam(tmp_path)
    report = run_doctor(options(repo.path, env, command_log))
    assert report.understand.env is not None
    assert report.understand.und_version == FIXTURE_VERSION
    assert str(fixtures) in problem_about(report, FAKE_VAR)


def test_the_interpreter_running_the_gate_is_reported(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """Which Python is running matters: in-process mode loads the API into this one."""
    repo = git_repo()
    _, env = seam(tmp_path)
    report = run_doctor(options(repo.path, env, command_log))
    assert report.python.startswith(".".join(str(part) for part in sys.version_info[:2]))


# --- the python und would analyse with (req 1.5, tasks 11.10/11.12) --------------

# `und` decides the Python dialect by EXECUTING a bare `python` off `PATH` and analyses a
# Python 2 model when it finds none, which drops every routine after the first Python 3
# construct in a file. That is the one field of this report whose failure mode is a green
# run, so it is reported on every run and is a problem whenever it is not healthy.

NOT_PYTHON_THREE = "#!/bin/sh\necho '2.7.18'\n"
"""An executable that answers like a Python 2. Its *path* looks like any other."""


def test_the_python_und_would_analyse_with_is_reported(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """Named on a healthy machine too, because the defect it guards is invisible otherwise."""
    repo = git_repo()
    home = install(tmp_path / "scitools", api="stub")
    env = isolated_env(tmp_path, SCITOOLS_HOME=str(home))

    report = run_doctor(options(repo.path, env, command_log))

    pin = report.understand.python
    assert pin is not None
    assert pin.ok, pin.detail
    assert pin.interpreter == Path(sys.executable)
    assert pin.version.startswith("3.")
    assert not [problem for problem in report.problems if "analyse with" in problem]


def test_an_interpreter_that_answers_python_two_is_a_problem(
    tmp_path: Path,
    git_repo: MakeGitRepo,
    command_log: FakeCommandLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The check is a *measurement*, not a path inspection, and this is what proves it.

    Nothing about the path of this executable says it is not a Python 3; only running it
    does. A frozen build of the Gate itself is the case that matters -- there
    ``sys.executable`` is not an interpreter at all -- and it is caught the same way.
    """
    repo = git_repo()
    home = install(tmp_path / "scitools", api="stub")
    pretender = executable(tmp_path / "pretender" / "python", NOT_PYTHON_THREE)
    monkeypatch.setattr(sys, "executable", str(pretender))
    env = isolated_env(tmp_path, SCITOOLS_HOME=str(home))

    report = run_doctor(options(repo.path, env, command_log))

    pin = report.understand.python
    assert pin is not None and not pin.ok
    assert pin.version == "2.7.18"
    reported = problem_about(report, "2.7.18")
    assert "und would analyse Python 2" in reported


def test_a_python_that_cannot_be_pinned_at_all_is_a_problem(
    tmp_path: Path,
    git_repo: MakeGitRepo,
    command_log: FakeCommandLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No interpreter to pin is the worst case and must still produce a report, not a raise."""
    repo = git_repo()
    home = install(tmp_path / "scitools", api="stub")
    monkeypatch.setattr(sys, "executable", "")
    env = isolated_env(tmp_path, SCITOOLS_HOME=str(home))

    report = run_doctor(options(repo.path, env, command_log))

    pin = report.understand.python
    assert pin is not None and not pin.ok and pin.interpreter is None
    assert "sys.executable is empty" in problem_about(report, "unusable")


def test_an_interpreter_that_refuses_to_run_is_a_problem(
    tmp_path: Path,
    git_repo: MakeGitRepo,
    command_log: FakeCommandLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not every unusable interpreter answers wrongly; some do not answer at all."""
    repo = git_repo()
    home = install(tmp_path / "scitools", api="stub")
    refuses = executable(tmp_path / "refuses" / "python", "#!/bin/sh\nexit 3\n")
    monkeypatch.setattr(sys, "executable", str(refuses))
    env = isolated_env(tmp_path, SCITOOLS_HOME=str(home))

    report = run_doctor(options(repo.path, env, command_log))

    pin = report.understand.python
    assert pin is not None and not pin.ok and pin.version == ""
    assert "exited 3" in pin.detail
    assert "exited 3" in problem_about(report, "unusable")


def test_a_pin_that_cannot_be_built_is_reported_rather_than_raised(
    tmp_path: Path,
    git_repo: MakeGitRepo,
    command_log: FakeCommandLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The interpreter is fine and the machine is not: no temporary directory to build in.

    ``doctor`` is the command an operator runs when things are already broken, so this has to
    arrive as a problem entry. It is provoked by a real cause -- a ``tempfile.tempdir`` that
    does not exist -- rather than by an injected exception.
    """
    repo = git_repo()
    home = install(tmp_path / "scitools", api="stub")
    env = isolated_env(tmp_path, SCITOOLS_HOME=str(home))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "no-such-tmp"))

    report = run_doctor(options(repo.path, env, command_log))

    pin = report.understand.python
    assert pin is not None and not pin.ok
    assert pin.interpreter == Path(sys.executable), "the choice is known; building it failed"
    assert "could not be created" in pin.detail


def test_an_installation_that_was_not_found_still_reports_the_interpreter(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """It is a fact about this process, not about the install, so it survives a missing one."""
    repo = git_repo()
    env = isolated_env(tmp_path, SCITOOLS_HOME=str(tmp_path / "nowhere"))

    report = run_doctor(options(repo.path, env, command_log))

    assert report.understand.env is None
    pin = report.understand.python
    assert pin is not None and pin.ok
    assert pin.interpreter == Path(sys.executable)


def test_the_fixture_seam_reports_no_interpreter_because_it_runs_nothing(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """The seam's promise is that no process starts; a probe here would break it."""
    repo = git_repo()
    _, env = seam(tmp_path)

    report = run_doctor(options(repo.path, env, command_log))

    assert report.understand.python is None


# --- the forced mode, and what the probes are allowed to assume ------------------


def test_forcing_a_mode_still_reports_both_probes(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """A diagnosis says what works, not only what was used.

    ``locator.verify`` deliberately runs a forced mode's probe *only* -- an operator who
    forced ``upython`` must not have the in-process import run behind their back during a run.
    Doctor is the documented exception: both are reported, and it is safe because the
    in-process probe loads the API in a child process.
    """
    repo = git_repo()
    (repo.path / "scitools-hook.toml").write_text(
        '[understand]\napi_mode = "upython"\n', encoding="utf-8"
    )
    home = install(tmp_path / "scitools", api="stub")
    env = isolated_env(tmp_path, SCITOOLS_HOME=str(home))
    report = run_doctor(options(repo.path, env, command_log))
    probes = {probe.mode: probe for probe in report.understand.probes}
    assert probes["upython"].ok
    assert probes["inprocess"].ok
    assert probes["inprocess"].version == IN_PROCESS_VERSION
    assert report.understand.api_mode == "upython"


def test_forcing_a_mode_that_does_not_work_leaves_the_run_unverified(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """The chosen mode is the operator's choice, not the best available one.

    ``upython`` answers here and in-process does not, but in-process is what was forced, so
    verification fails -- and the report still shows that ``upython`` would have worked, which
    is the whole reason an operator runs this command.
    """
    repo = git_repo()
    (repo.path / "scitools-hook.toml").write_text(
        '[understand]\napi_mode = "inprocess"\n', encoding="utf-8"
    )
    home = install(tmp_path / "scitools")
    env = isolated_env(tmp_path, SCITOOLS_HOME=str(home))
    report = run_doctor(options(repo.path, env, command_log))
    probes = {probe.mode: probe for probe in report.understand.probes}
    assert probes["upython"].ok
    assert not probes["inprocess"].ok
    assert report.understand.api_mode is None
    assert not report.understand.verified
    assert problem_about(report, "API")


def test_the_in_process_probe_adds_the_api_directory_and_nothing_else(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """The probe must build the environment ``ApiRunner`` builds, and no friendlier one.

    In-process mode appends the API directory to ``sys.path`` and does nothing else, so a
    probe that also exported a library path would certify an environment no run reproduces --
    the probe would pass and the first real operation would fail. The stub module answers with
    the variables it was given, which is the only way to assert on a child's environment.
    """
    repo = git_repo()
    home = install(tmp_path / "scitools", upython=False, api="env")
    env = isolated_env(tmp_path, SCITOOLS_HOME=str(home), PYTHONPATH="/sentinel")
    report = run_doctor(options(repo.path, env, command_log))
    probes = {probe.mode: probe for probe in report.understand.probes}
    assert probes["inprocess"].ok, probes["inprocess"].detail
    named, _, path = probes["inprocess"].version.partition("|")
    assert named == "PYTHONPATH"
    api_dir = str(home / "bin" / platform_bin(sys.platform) / "Python")
    assert path == f"{api_dir}{os.pathsep}/sentinel"


# --- failures that must still produce a report ----------------------------------


def test_a_configuration_file_that_is_not_utf8_is_reported_not_raised(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """A ``UnicodeDecodeError`` is a ``ValueError``, so it slips past every typed guard.

    Any Latin-1 editor produces this file. Left unmapped it escaped ``doctor`` entirely -- no
    report at all, on the one step whose failure this command exists to explain.
    """
    repo = git_repo()
    (repo.path / "scitools-hook.toml").write_bytes(b'[project]\nlanguages = ["\xff\xfe"]\n')
    _, env = seam(tmp_path)
    report = run_doctor(options(repo.path, env, command_log))
    assert problem_about(report, "scitools-hook.toml")
    assert "UTF-8" in problem_about(report, "scitools-hook.toml")
    assert report.git.inside_repository
    assert report.understand.env is not None


def test_git_failing_to_run_at_all_is_a_problem_of_its_own(
    tmp_path: Path,
    git_repo: MakeGitRepo,
    command_log: FakeCommandLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ "git is broken" and "you are not in a repository" are different faults, reported apart."""
    repo = git_repo()

    def unrunnable(*args: object, **kwargs: object) -> GitRepo:
        raise AnalysisFailedError("git could not be started", command=["git"])

    monkeypatch.setattr(GitRepo, "discover", unrunnable)
    _, env = seam(tmp_path)
    report = run_doctor(options(repo.path, env, command_log))
    assert not report.git.inside_repository
    assert problem_about(report, "git failed")


def test_a_working_directory_that_does_not_exist_says_so(
    tmp_path: Path, command_log: FakeCommandLog
) -> None:
    """Otherwise it reads as "not inside a git working tree", which sends the operator away."""
    _, env = seam(tmp_path)
    report = run_doctor(options(tmp_path / "gone", env, command_log))
    assert not report.git.inside_repository
    assert problem_about(report, "does not exist")


def test_a_seam_pointing_at_something_that_is_not_a_directory_is_reported(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """A mistyped fixture path must never present as a healthy installation.

    It would otherwise be the quietest failure the seam has: a directory that does not exist
    holds no ``analyze.json``, and a missing ``analyze.json`` is read as "this project parsed
    cleanly".
    """
    repo = git_repo()
    plain = tmp_path / "not-a-directory"
    plain.write_text("", encoding="utf-8")
    env = isolated_env(tmp_path, **{FAKE_VAR: str(plain)})
    report = run_doctor(options(repo.path, env, command_log))
    assert not report.understand.verified
    assert report.understand.api_mode is None
    assert report.understand.probes == []
    assert problem_about(report, "not a directory")


def test_a_repository_that_has_never_been_analysed_reports_no_state_and_no_problem(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """The state of every repository before its first run; reporting it would be noise."""
    repo = git_repo()
    _, env = seam(tmp_path)
    report = run_doctor(options(repo.path, env, command_log))
    assert report.cache is not None
    assert report.state is None
    assert not [problem for problem in report.problems if "state.json" in problem]


def test_a_cache_root_that_is_a_file_is_reported(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """``Path.exists()`` answers False under a file, so this fault is invisible without a check."""
    repo = git_repo()
    (repo.path / "scitools-hook.toml").write_text(
        '[understand]\ndb_location = "gitdir"\n', encoding="utf-8"
    )
    paths = CachePaths.for_repo(repo.path.resolve() / ".git", "gitdir")
    paths.root.write_text("not a directory", encoding="utf-8")
    _, env = seam(tmp_path)
    report = run_doctor(options(repo.path, env, command_log))
    assert report.state is None
    assert problem_about(report, "is not a directory")


# --- the seam diagnosis itself, the path the done-criterion names ----------------


@pytest.mark.parametrize("inside", [True, False])
def test_the_seam_reports_a_complete_and_usable_diagnosis(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog, inside: bool
) -> None:
    """ "doctor runs with fakes inside and outside a repository" is this task's done criterion.

    Requirement 1.5's API and licence answers come from ``_fixture_diagnosis`` whenever the
    seam is on, and 8.3, 8.5, 9.3 and 10.2 all read them. Asserting only that an environment
    exists left every field of that answer free to be anything.
    """
    fixtures, env = seam(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    cwd = git_repo().path if inside else elsewhere
    report = run_doctor(options(cwd, env, command_log))
    understand = report.understand
    assert understand.env is not None
    assert understand.env.home == fixtures
    assert understand.und_version == FIXTURE_VERSION
    assert understand.verified
    assert understand.api_mode == "inprocess"
    assert understand.license is not None and understand.license.ok
    assert [(probe.mode, probe.ok) for probe in understand.probes] == [("inprocess", True)]
    assert understand.probes[0].version
    assert report.git.inside_repository is inside


def test_a_never_analysed_repository_reports_nothing_but_the_seam(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """The ordinary first run: no cache, no state, and therefore nothing to complain about.

    Asserted as the whole list rather than as an absent substring, because that is the only
    form that catches a *spurious* problem -- and both guards this pins would produce one on
    every single run of every repository that has not been analysed yet.
    """
    repo = git_repo()
    fixtures, env = seam(tmp_path)
    report = run_doctor(options(repo.path, env, command_log))
    assert report.cache is not None
    # Inside the sandbox, not the developer's real cache directory: the environment mapping
    # now decides the cache root, so this assertion is about a path the test controls.
    assert str(tmp_path) in str(report.cache.root)
    assert not report.cache.root.exists()
    assert report.state is None
    assert report.problems == [
        f"{FAKE_VAR}={fixtures} is set: the Gate is reading fixtures, not analysing code"
    ]


def test_outside_a_repository_is_a_status_and_never_a_problem(
    tmp_path: Path, command_log: FakeCommandLog
) -> None:
    """Requirement 12.5 makes running here legitimate, so nothing about it is a fault."""
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    _, env = seam(tmp_path)
    report = run_doctor(options(outside, env, command_log))
    assert not report.git.inside_repository
    assert report.git.detail
    assert not [problem for problem in report.problems if "git" in problem.lower()]


# --- the failures that used to escape (all reproduced before being fixed) --------


def test_a_configuration_nested_too_deeply_is_reported_not_raised(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """Measured: ``tomllib`` raises ``RecursionError`` from ~497 levels, and 450 parses.

    It is neither an ``OSError`` nor a ``ValueError``, so it escaped every typed guard and
    ``run_doctor`` produced no report at all.
    """
    repo = git_repo()
    depth = 600
    (repo.path / "scitools-hook.toml").write_text(f"value = {'[' * depth}{']' * depth}\n")
    _, env = seam(tmp_path)
    report = run_doctor(options(repo.path, env, command_log))
    assert "too deeply" in problem_about(report, "scitools-hook.toml")
    assert report.git.inside_repository


def test_a_configuration_that_is_a_fifo_does_not_withhold_the_report(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """A FIFO never raises -- ``read_text`` simply blocks forever with no writer.

    That destroys the report as completely as an exception does, so the kind is checked with
    ``stat`` (which does not block) before anything opens the path. The alarm makes a
    regression fail in ten seconds instead of hanging the suite.
    """
    repo = git_repo()
    os.mkfifo(repo.path / "scitools-hook.toml")
    _, env = seam(tmp_path)
    with time_limit(10):
        report = run_doctor(options(repo.path, env, command_log))
    assert "not a regular file" in problem_about(report, "scitools-hook.toml")


def test_a_cache_root_this_user_cannot_read_is_reported(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """Measured: under ``chmod 000`` the root still answers ``exists()`` and ``is_dir()``,
    while ``state.json``'s ``exists()`` answers ``False`` because ``EACCES`` is swallowed --
    so an unreadable cache is indistinguishable from one that was never built."""
    repo = git_repo()
    (repo.path / "scitools-hook.toml").write_text(
        '[understand]\ndb_location = "gitdir"\n', encoding="utf-8"
    )
    paths = CachePaths.for_repo(repo.path.resolve() / ".git", "gitdir")
    paths.root.mkdir(parents=True)
    paths.state.write_text("{}", encoding="utf-8")
    paths.root.chmod(0o000)
    try:
        _, env = seam(tmp_path)
        report = run_doctor(options(repo.path, env, command_log))
    finally:
        paths.root.chmod(0o755)
    assert problem_about(report, "cannot be read")


def test_an_unexpected_failure_is_reported_as_one_rather_than_disguised(
    tmp_path: Path,
    git_repo: MakeGitRepo,
    command_log: FakeCommandLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard is broad so the report always arrives; it must still name a defect a defect.

    An environment fault reads as the operator's to fix. Anything else is labelled with its
    exception type, which is what the narrow guard used to buy and what must not be lost by
    widening it.
    """
    repo = git_repo()

    def broken(self: object) -> str:
        raise TypeError("head() got an unexpected keyword")

    monkeypatch.setattr(GitRepo, "head", broken)
    _, env = seam(tmp_path)
    report = run_doctor(options(repo.path, env, command_log))
    reported = problem_about(report, "unexpectedly")
    assert "TypeError" in reported
    assert report.git.inside_repository


def test_a_report_arrives_whatever_loading_the_configuration_does(
    tmp_path: Path,
    git_repo: MakeGitRepo,
    command_log: FakeCommandLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Doctor must not depend on another module's failure taxonomy being complete.

    This is the finding that rejected the previous round, pinned as behaviour instead of as
    prose. The four escapes then known -- a Latin-1 file, 497-level nesting in a file or in a
    ``SCITOOLS_HOOK_*`` variable, and a FIFO that blocks rather than raises -- are each mapped
    at their source now and each has its own test. What this asserts is the property those
    four were only ever examples of: *whatever* ``load_settings`` raises, the operator still
    gets their report. Written with an injected failure precisely so it cannot decay into a
    claim about which exceptions ``config.loader`` happens to raise today.
    """
    repo = git_repo()

    def refuses(*args: object, **kwargs: object) -> tuple[object, object]:
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(doctor_module, "load_settings", refuses)
    _, env = seam(tmp_path)
    report = run_doctor(options(repo.path, env, command_log))
    reported = problem_about(report, "configuration")
    assert "RecursionError" in reported
    assert report.git.inside_repository
    assert report.understand.env is not None


def test_a_sync_state_that_is_a_fifo_does_not_withhold_the_report(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """The third reader in this boundary to need the same guard, and the one that was missed.

    ``config.loader._read_toml`` and ``BaselineStore.load`` both settle the file kind before
    opening; ``_sync_state`` gated on ``exists()`` alone, so a FIFO here blocked ``run_doctor``
    forever -- the same failure of the same property, in the module whose whole contract is
    that a report always arrives.
    """
    repo = git_repo()
    (repo.path / "scitools-hook.toml").write_text(
        '[understand]\ndb_location = "gitdir"\n', encoding="utf-8"
    )
    paths = CachePaths.for_repo(repo.path.resolve() / ".git", "gitdir")
    paths.root.mkdir(parents=True)
    os.mkfifo(paths.state)
    _, env = seam(tmp_path)
    with time_limit(10):
        report = run_doctor(options(repo.path, env, command_log))
    assert report.state is None
    assert report.cache is not None
    assert "not a regular file" in problem_about(report, "state.json")


def test_a_fixture_directory_this_user_cannot_enter_is_reported(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """The sibling of the cache-root check, which documents this exact trap and applies it.

    Without it the seam answers ``verified=True`` and licensed for a directory whose every
    fixture read is going to fail.
    """
    repo = git_repo()
    fixtures = tmp_path / "sealed"
    fixtures.mkdir()
    fixtures.chmod(0o000)
    try:
        env = isolated_env(tmp_path, **{FAKE_VAR: str(fixtures)})
        report = run_doctor(options(repo.path, env, command_log))
    finally:
        fixtures.chmod(0o755)
    assert not report.understand.verified
    assert report.understand.api_mode is None
    assert problem_about(report, "cannot be read")


def test_a_value_nested_too_deeply_in_an_environment_variable_is_reported(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """The escape route that reaches the Gate without any file being involved."""
    repo = git_repo()
    _, env = seam(tmp_path)
    depth = 600
    env["SCITOOLS_HOOK_THRESHOLDS__ROUTINE__CyclomaticStrict"] = "[" * depth + "]" * depth
    report = run_doctor(options(repo.path, env, command_log))
    assert "too deeply" in problem_about(report, "configuration")
    assert report.git.inside_repository


def test_a_working_directory_that_is_a_file_says_so_rather_than_saying_it_is_missing(
    tmp_path: Path, command_log: FakeCommandLog
) -> None:
    """ "does not exist" would send the operator looking for something that is right there."""
    plain = tmp_path / "a-file"
    plain.write_text("", encoding="utf-8")
    _, env = seam(tmp_path)
    report = run_doctor(options(plain, env, command_log))
    assert not report.git.inside_repository
    assert "is not a directory" in problem_about(report, str(plain))


def test_a_cache_root_that_is_a_file_produces_exactly_one_problem(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """One fault, one problem. A file root fails the readability test too, so the branches
    must exclude one another or the operator is told the same thing twice in two wordings."""
    repo = git_repo()
    (repo.path / "scitools-hook.toml").write_text(
        '[understand]\ndb_location = "gitdir"\n', encoding="utf-8"
    )
    paths = CachePaths.for_repo(repo.path.resolve() / ".git", "gitdir")
    paths.root.parent.mkdir(parents=True, exist_ok=True)
    paths.root.write_text("not a directory", encoding="utf-8")
    _, env = seam(tmp_path)
    report = run_doctor(options(repo.path, env, command_log))
    about_cache = [problem for problem in report.problems if "cache root" in problem]
    assert len(about_cache) == 1
    assert "is not a directory" in about_cache[0]


@pytest.mark.parametrize(("mode", "bit"), [(0o444, "search"), (0o111, "read")])
def test_a_cache_root_missing_either_permission_bit_is_reported(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog, mode: int, bit: str
) -> None:
    """Both bits are the question being posed, and ``chmod 000`` cannot tell them apart.

    Measured: ``0o444`` is readable but not searchable, ``0o111`` searchable but not readable,
    and either alone makes the cache unusable. Every earlier test used ``0o000``.
    """
    repo = git_repo()
    (repo.path / "scitools-hook.toml").write_text(
        '[understand]\ndb_location = "gitdir"\n', encoding="utf-8"
    )
    paths = CachePaths.for_repo(repo.path.resolve() / ".git", "gitdir")
    paths.root.mkdir(parents=True)
    paths.root.chmod(mode)
    try:
        _, env = seam(tmp_path)
        report = run_doctor(options(repo.path, env, command_log))
    finally:
        paths.root.chmod(0o755)
    assert "cannot be read" in problem_about(report, "cache root"), bit


def test_a_sync_state_nested_too_deeply_is_reported(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """This reader survives the depth class only because pydantic maps its own limit onto a
    ``ValidationError``. Relying on a dependency's internal choice is not a guarantee, so the
    class is named explicitly here and this is what would notice if that choice changed."""
    repo = git_repo()
    (repo.path / "scitools-hook.toml").write_text(
        '[understand]\ndb_location = "gitdir"\n', encoding="utf-8"
    )
    paths = CachePaths.for_repo(repo.path.resolve() / ".git", "gitdir")
    paths.root.mkdir(parents=True)
    paths.state.write_text("[" * 100_000 + "]" * 100_000, encoding="utf-8")
    _, env = seam(tmp_path)
    report = run_doctor(options(repo.path, env, command_log))
    assert report.state is None
    assert problem_about(report, "state.json")


def test_a_fixture_nested_too_deeply_is_an_analysis_failure_not_an_internal_defect(
    tmp_path: Path,
    git_repo: MakeGitRepo,
    command_log: FakeCommandLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The seam's own ``RecursionError`` used to reach the report as a Gate defect.

    ``configuration failed unexpectedly (RecursionError)`` is exit 70 for what is plainly a
    broken fixture, and the label matters: it tells the operator to file a bug rather than to
    fix their file.


    The document used to be 100,000 nested brackets, trusting CPython to refuse it. In 3.14 the
    json C scanner is bounded by the C stack rather than by ``sys.getrecursionlimit()``, so the
    depth that trips it moves with the environment: measured, the release container parsed the
    very document this machine refuses, and the test then failed on an unrelated message. The
    promise here is that a ``RecursionError`` from the parser is reported rather than escaping
    as a Gate defect, so the error is injected and CPython's threshold is left alone.
    """
    repo = git_repo()
    (repo.path / "scitools-hook.toml").write_text(
        '[project]\nlanguages = ["Python"]\n', encoding="utf-8"
    )
    fixtures, env = seam(tmp_path)
    (fixtures / "catalogue.json").write_text("[[[]]]", encoding="utf-8")
    monkeypatch.setattr(json, "loads", _too_deep)
    report = run_doctor(options(repo.path, env, command_log))
    assert "too deeply" in problem_about(report, "too deeply")
    assert not [p for p in report.problems if "unexpectedly" in p]


def test_a_probe_answering_an_unparseable_document_is_a_failed_probe_not_a_crash(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """The parser-depth class reaches the probes too, by way of the answer they read.

    ``_version_of`` calls ``json.loads`` on whatever the interpreter printed, and a deeply
    nested document raises ``RecursionError`` there -- not a ``ValueError``. Guarding only
    ``ValueError`` would let a probe take down the whole run while asking whether a mode works.
    """
    repo = git_repo()
    home = install(tmp_path / "scitools", api="stub", mode="deep")
    env = isolated_env(tmp_path, SCITOOLS_HOME=str(home))
    report = run_doctor(options(repo.path, env, command_log))
    probes = {probe.mode: probe for probe in report.understand.probes}
    assert not probes["upython"].ok
    assert probes["upython"].detail
    assert report.understand.api_mode == "inprocess"


def test_a_probe_answering_an_unparseable_document_does_not_break_a_real_run(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """The same fault on the *run* path, where no broad guard is waiting to absorb it.

    ``doctor`` catches everything, so a ``RecursionError`` escaping ``_version_of`` merely
    reads as a failed probe there. ``build_context`` has no such net: ``locator.verify`` asks
    the probes through ``_ask``, which deliberately catches only ``OSError``, so the error
    would leave ``check`` as an unexpected internal failure (exit 70) instead of falling back
    to the mode that works.
    """
    repo = git_repo()
    home = install(tmp_path / "scitools", api="stub", mode="deep")
    env = isolated_env(tmp_path, SCITOOLS_HOME=str(home))
    context = build_context(ContextOptions(cwd=repo.path, env=env, log=command_log))
    assert context.understand.api_mode == "inprocess"
    assert context.understand.version == "(Build 1204)"


def test_a_sync_state_that_is_not_utf8_is_reported_never_silently_replaced(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """A lenient decoder here would rewrite ``created_with`` and ``before_commit`` with U+FFFD.

    The Gate would then accept a commit hash and an Understand version nobody wrote, and
    decide from them whether the cached databases are still valid.
    """
    repo = git_repo()
    (repo.path / "scitools-hook.toml").write_text(
        '[understand]\ndb_location = "gitdir"\n', encoding="utf-8"
    )
    paths = CachePaths.for_repo(repo.path.resolve() / ".git", "gitdir")
    paths.root.mkdir(parents=True)
    paths.state.write_bytes(b'{"created_with": "6.5.\xe9", "before_commit": null}')
    _, env = seam(tmp_path)
    report = run_doctor(options(repo.path, env, command_log))
    assert report.state is None
    assert problem_about(report, "state.json")


def test_no_phase_of_the_report_can_escape_by_a_type_nobody_listed(
    tmp_path: Path,
    git_repo: MakeGitRepo,
    command_log: FakeCommandLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every phase is guarded on its outcome, not on a list of expected exception types.

    ``MemoryError`` is the case that proved the need -- a regular file larger than available
    memory raises it, and it is neither an ``OSError``, a ``ValueError`` nor a
    ``RecursionError`` -- and three phases called their helpers directly, so it walked out of
    ``run_doctor`` entirely. Injected rather than reproduced, so the guard is what is pinned
    and not the one exception that exposed it.
    """
    repo = git_repo()

    def exhausted(*args: object, **kwargs: object) -> str:
        raise MemoryError("cannot allocate")

    monkeypatch.setattr(Path, "read_text", exhausted)
    _, env = seam(tmp_path)
    report = run_doctor(options(repo.path, env, command_log))
    assert isinstance(report, DoctorReport)
    assert any("MemoryError" in problem for problem in report.problems)


def test_a_depth_failure_from_the_state_reader_is_reported(
    tmp_path: Path,
    git_repo: MakeGitRepo,
    command_log: FakeCommandLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The depth class reaches this reader only through pydantic, which maps it to a
    ``ValidationError`` -- so a deep ``state.json`` can never exercise a ``RecursionError``
    guard here, and a test written with one asserts nothing. Injecting the error is what
    shows the outcome guard is load-bearing."""
    repo = git_repo()
    (repo.path / "scitools-hook.toml").write_text(
        '[understand]\ndb_location = "gitdir"\n', encoding="utf-8"
    )
    paths = CachePaths.for_repo(repo.path.resolve() / ".git", "gitdir")
    paths.root.mkdir(parents=True)
    paths.state.write_text("{}", encoding="utf-8")

    def too_deep(*args: object, **kwargs: object) -> SyncState:
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(SyncState, "model_validate_json", too_deep)
    _, env = seam(tmp_path)
    report = run_doctor(options(repo.path, env, command_log))
    assert report.state is None
    reported = problem_about(report, "RecursionError")
    assert "unexpectedly" in reported, "an injected defect must be labelled as one"


@pytest.mark.parametrize("shape", ["dangling", "loop", "unsearchable"])
def test_a_sync_state_that_cannot_be_reached_is_never_read_as_never_analysed(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog, shape: str
) -> None:
    """``Path.exists()`` swallows ``OSError``, so it cannot tell absent from unreachable.

    Reported as "never analysed" these are byte-identical to a fresh repository -- the exact
    silence this function's docstring denies. The class was fixed in ``BaselineStore`` first
    and missed here twice, which is why both sites now share one implementation.
    """
    repo = git_repo()
    (repo.path / "scitools-hook.toml").write_text(
        '[understand]\ndb_location = "gitdir"\n', encoding="utf-8"
    )
    paths = CachePaths.for_repo(repo.path.resolve() / ".git", "gitdir")
    paths.root.mkdir(parents=True)
    sealed = paths.root / "sealed"
    if shape == "dangling":
        paths.state.symlink_to(paths.root / "nowhere")
    elif shape == "loop":
        paths.state.symlink_to(paths.state)
    else:
        sealed.mkdir()
        (sealed / "state.json").write_text("{}", encoding="utf-8")
        paths.state.symlink_to(sealed / "state.json")
        sealed.chmod(0o000)
    _, env = seam(tmp_path)
    try:
        report = run_doctor(options(repo.path, env, command_log))
    finally:
        if sealed.exists():
            sealed.chmod(0o755)
    assert report.state is None
    assert problem_about(report, "state.json")


@pytest.mark.parametrize("shape", ["dangling", "loop"])
def test_a_cache_root_that_cannot_be_reached_is_reported(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog, shape: str
) -> None:
    """The identical hole at the sibling site: ``exists()`` gated both later checks."""
    repo = git_repo()
    (repo.path / "scitools-hook.toml").write_text(
        '[understand]\ndb_location = "gitdir"\n', encoding="utf-8"
    )
    paths = CachePaths.for_repo(repo.path.resolve() / ".git", "gitdir")
    paths.root.parent.mkdir(parents=True, exist_ok=True)
    paths.root.symlink_to(paths.root.parent / "nowhere" if shape == "dangling" else paths.root)
    _, env = seam(tmp_path)
    report = run_doctor(options(repo.path, env, command_log))
    assert report.cache is not None
    assert problem_about(report, "cache root")


def test_a_working_directory_that_cannot_be_reached_is_not_called_missing(
    tmp_path: Path, command_log: FakeCommandLog
) -> None:
    """ "does not exist" about a symlink loop sends the operator looking for the wrong fault."""
    loop = tmp_path / "loop"
    loop.symlink_to(loop)
    _, env = seam(tmp_path)
    report = run_doctor(options(loop, env, command_log))
    assert not report.git.inside_repository
    assert "does not exist" not in problem_about(report, str(loop))


@pytest.mark.parametrize(
    ("failure", "expected"),
    [(OSError(13, "Permission denied"), "failed:"), (TypeError("bug"), "unexpectedly")],
)
def test_an_environment_failure_and_a_defect_are_reported_differently(
    tmp_path: Path,
    git_repo: MakeGitRepo,
    command_log: FakeCommandLog,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    expected: str,
) -> None:
    """The whole reason the guard classifies instead of catching everything alike.

    An ``OSError`` is the operator's environment; a ``TypeError`` is a bug in the Gate. Only
    the ``GateError`` member of that tuple was pinned, so dropping either of the other two
    changed nothing any test could see.
    """
    repo = git_repo()

    def broken(self: object) -> str:
        raise failure

    monkeypatch.setattr(GitRepo, "head", broken)
    _, env = seam(tmp_path)
    report = run_doctor(options(repo.path, env, command_log))
    assert any(expected in problem for problem in report.problems)


def test_a_subprocess_failure_is_reported_as_the_environment_not_as_a_defect(
    tmp_path: Path,
    git_repo: MakeGitRepo,
    command_log: FakeCommandLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The third member of the tuple: a command that timed out is not a Gate defect."""
    repo = git_repo()

    def timed_out(self: object) -> str:
        raise subprocess.TimeoutExpired(cmd=["git"], timeout=1)

    monkeypatch.setattr(GitRepo, "head", timed_out)
    _, env = seam(tmp_path)
    report = run_doctor(options(repo.path, env, command_log))
    assert any("failed:" in problem for problem in report.problems)
    assert not [problem for problem in report.problems if "unexpectedly" in problem]


def test_a_wedged_und_does_not_delay_the_report_by_the_wrapper_s_own_ceiling(
    tmp_path: Path,
    git_repo: MakeGitRepo,
    command_log: FakeCommandLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``doctor`` is the command an operator runs when things are already broken.

    ``UndCli``'s own ceiling is 900 s and two calls are made here, so a wedged ``und`` would
    hold the report for up to half an hour. The budget is the probes' instead. The ceiling is
    lowered for the test rather than waiting on the real one, and ``/bin/sleep`` is used by
    absolute path because the probe environment has an empty ``PATH``.
    """
    repo = git_repo()
    home = install(tmp_path / "scitools", mode="deep")
    bin_dir = home / "bin" / platform_bin(sys.platform)
    executable(bin_dir / "und", "#!/bin/sh\nexec /bin/sleep 5\n")
    monkeypatch.setattr(doctor_module, "PROBE_TIMEOUT_S", 1)
    env = isolated_env(tmp_path, SCITOOLS_HOME=str(home))
    started = time.monotonic()
    report = run_doctor(options(repo.path, env, command_log))
    assert time.monotonic() - started < 5, "the wrapper's own ceiling was used, not the probes'"
    assert problem_about(report, "und version")
