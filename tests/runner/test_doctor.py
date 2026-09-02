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

import contextlib
import json
import os
import signal
import stat
import subprocess
import sys
import time
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest
from conftest import FakeCommandLog, MakeGitRepo

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

UND_SCRIPT = """#!/bin/sh
case "$1" in
  version) echo "{version}" ;;
  -isundlicensed) echo "{licensed}" ;;
  license) echo "{license_text}" ;;
  *) echo "Error: No valid command found." >&2; exit 1 ;;
esac
"""
"""A stand-in ``und`` answering the two commands ``doctor`` runs, and refusing the rest."""

UPYTHON_SCRIPT = """#!/bin/sh
echo '{{"version": "{version}", "python": "3.12.0"}}'
"""
"""A stand-in ``upython`` answering the worker ping the way a healthy installation does."""

BROKEN_UPYTHON = """#!/bin/sh
echo '{{"version": "{version}", "python": "3.12.0"}}'
echo "the interpreter died after answering" >&2
exit 1
"""
"""A bundled interpreter that prints a perfectly good answer and then dies.

Deliberately this shape rather than a silent failure: it is the one that would slip through
a probe reading only standard output. The worker goes to some length (``worker._leave``) to
exit 0 after ``Ent.draw`` leaves a subinterpreter behind, so a non-zero status means the
interpreter is broken whatever it managed to print first.
"""

REFUSING_UPYTHON = """#!/bin/sh
echo '{{"error": {{"type": "NoApiLicense", "message": "no license"}}, "version": "{version}"}}'
"""
"""A bundled interpreter that runs, exits 0, and answers with a refusal.

The worker's contract is that a foreseeable failure is *data*: an ``{{"error": ...}}``
envelope on standard output with exit status 0. A probe that read the document for a version
and ignored the envelope would certify a mode that cannot open a database, so the envelope --
not the absence of a version -- is what decides. The version is present here on purpose: it
is the input that tells the two rules apart.
"""

STUB_API = '''"""A stand-in for Understand's own ``understand`` module, importable by any Python."""


class UnderstandError(Exception):
    """The exception type the worker catches by attribute, so the stub must define it."""


def version() -> str:
    """The API version the worker's ``ping`` operation reports."""
    return "{version}"
'''
"""Put in ``bin/<platform>/Python`` so the in-process probe has something real to import."""

ENV_REPORTING_API = '''"""A stand-in that answers with the environment the probe gave its child."""

import os

_WATCHED = ("PYTHONPATH", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "PATH")


class UnderstandError(Exception):
    """The exception type the worker catches by attribute."""


def version() -> str:
    """The variables the probe set, then the PYTHONPATH it built, separated by a bar."""
    named = ",".join(name for name in _WATCHED if os.environ.get(name))
    return named + "|" + os.environ.get("PYTHONPATH", "")
'''
"""Reports its own environment, so the probe's promise about it can be asserted on."""

API_STUBS = {"stub": STUB_API, "env": ENV_REPORTING_API}
"""Which stand-in module ``install(api=...)`` writes into the API directory."""

DEEP_UPYTHON = """#!/bin/sh
exec /bin/cat '{deep}'
"""
"""A bundled interpreter whose answer is a document no parser will accept.

``json.loads`` answers this with ``RecursionError``, not ``ValueError`` -- the parser-depth
fault class -- so a probe guarding only ``ValueError`` would take the Gate down while merely
asking whether a mode works.

``/bin/cat`` by absolute path, reading a file written at install time: the probe runs the stub
with the isolated environment, whose ``PATH`` is empty, so anything resolved through ``PATH``
exits 127 and the probe answers "no" on the *status* without ever parsing the output. An
earlier version of this stub invoked ``python3`` and did exactly that -- the test passed while
proving nothing, which the surviving mutant is what exposed.
"""

UPYTHON_SCRIPTS = {
    "ok": UPYTHON_SCRIPT,
    "broken": BROKEN_UPYTHON,
    "refusing": REFUSING_UPYTHON,
    "deep": DEEP_UPYTHON,
}
"""The three answers a bundled interpreter can give, selected by ``install(mode=...)``."""

API_VERSION = "6.5.1204"
"""What the stub ``upython`` reports; the version the API returns, not the ``und`` build."""

IN_PROCESS_VERSION = "9.9.9-stub"
"""What the importable stub module reports, so the two probes cannot be confused."""


@contextlib.contextmanager
def time_limit(seconds: int) -> Iterator[None]:
    """Fail rather than hang: a blocking read must not take the whole suite down with it."""

    def ring(signum: int, frame: object) -> None:
        raise AssertionError(f"the call blocked for more than {seconds}s")

    previous = signal.signal(signal.SIGALRM, ring)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def executable(path: Path, body: str) -> Path:
    """Write ``body`` to ``path`` and make it runnable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    return path


def install(
    root: Path, upython: bool = True, licensed: bool = True, api: str = "", mode: str = "ok"
) -> Path:
    """A directory laid out like an Understand installation, answering like a healthy one.

    ``api`` writes an importable stand-in module into the directory the in-process mode adds
    to ``sys.path``, so that probe has something to succeed at; without it the probe answers
    the ``ApiUnavailable`` envelope a real interpreter without Understand answers.
    """
    bin_dir = root / "bin" / platform_bin(sys.platform)
    executable(
        bin_dir / "und",
        UND_SCRIPT.format(
            version="(Build 1204)",
            licensed="1" if licensed else "0",
            license_text="ok" if licensed else "No license available",
        ),
    )
    if upython:
        deep = bin_dir / "deep.json"
        deep.parent.mkdir(parents=True, exist_ok=True)
        deep.write_text("[" * 100_000 + "]" * 100_000, encoding="utf-8")
        script = UPYTHON_SCRIPTS[mode].format(version=API_VERSION, deep=deep)
        executable(bin_dir / "upython", script)
    if api:
        (bin_dir / "Python").mkdir(parents=True, exist_ok=True)
        (bin_dir / "Python" / "understand.py").write_text(
            API_STUBS[api].format(version=IN_PROCESS_VERSION), encoding="utf-8"
        )
    return root


def isolated_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    """An environment that cannot reach this machine's real Understand or user config."""
    return {
        "HOME": str(tmp_path / "home"),
        "PATH": "",
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
        **extra,
    }


def options(cwd: Path, env: Mapping[str, str], log: FakeCommandLog) -> ContextOptions:
    """The inputs the ``doctor`` command hands the pipeline."""
    return ContextOptions(cwd=cwd, env=dict(env), log=log)


def seam(tmp_path: Path, **extra: str) -> tuple[Path, dict[str, str]]:
    """A fixture directory and an environment pointing the test seam at it."""
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir(parents=True, exist_ok=True)
    return fixtures, isolated_env(tmp_path, **{FAKE_VAR: str(fixtures), **extra})


def problem_about(report: DoctorReport, needle: str) -> str:
    """The one problem mentioning ``needle``; fails the test when there is none."""
    found = [problem for problem in report.problems if needle in problem]
    assert found, f"no problem mentions {needle!r}; problems were {report.problems}"
    return found[0]


# --- a healthy installation: both probes and the chosen mode (req 1.5) -----------


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


def test_an_unlicensed_installation_is_reported_rather_than_stopping_the_report(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """Requirement 1.4 stops a *check*; requirement 1.5 still wants the whole diagnosis."""
    repo = git_repo()
    home = install(tmp_path / "scitools", licensed=False)
    env = isolated_env(tmp_path, SCITOOLS_HOME=str(home))
    report = run_doctor(options(repo.path, env, command_log))
    assert report.understand.license is not None
    assert not report.understand.license.ok
    assert report.understand.license.text
    assert problem_about(report, "license")


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
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """The seam's own ``RecursionError`` used to reach the report as a Gate defect.

    ``configuration failed unexpectedly (RecursionError)`` is exit 70 for what is plainly a
    broken fixture, and the label matters: it tells the operator to file a bug rather than to
    fix their file.
    """
    repo = git_repo()
    (repo.path / "scitools-hook.toml").write_text(
        '[project]\nlanguages = ["Python"]\n', encoding="utf-8"
    )
    fixtures, env = seam(tmp_path)
    (fixtures / "catalogue.json").write_text("[" * 100_000 + "]" * 100_000, encoding="utf-8")
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
