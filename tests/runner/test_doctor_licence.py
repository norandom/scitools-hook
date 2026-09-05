"""``doctor`` on the licence and on whether anything analyses at all (req 1.4, 1.5).

Three questions, in the order an operator meets them: does ``und -isundlicensed`` say ``1``,
does ``und`` analyse a one-file project right now, and does the API open what it analysed.
Each answer is reported, never raised, and each failure is a ``problems`` entry that quotes
``und``'s own words -- the line an operator can act on -- and, for a licence, points at the
vendor's command-line licensing page rather than at anything this tool could do about it.
``-isundlicensed`` is the only licence switch the tool runs.
"""

from __future__ import annotations

from pathlib import Path

from conftest import FakeCommandLog, MakeGitRepo
from doctor_stubs import UndAnswers, install, isolated_env, options, problem_about

from scitools_hook.runner.doctor import run_doctor


def test_an_unlicensed_installation_is_reported_rather_than_stopping_the_report(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """Requirement 1.4 stops a *check*; requirement 1.5 still wants the whole diagnosis."""
    repo = git_repo()
    home = install(tmp_path / "scitools", und=UndAnswers(licensed=False))
    env = isolated_env(tmp_path, SCITOOLS_HOME=str(home))
    report = run_doctor(options(repo.path, env, command_log))
    assert report.understand.license is not None
    assert not report.understand.license.ok
    assert report.understand.license.text
    assert problem_about(report, "license")


def test_a_healthy_installation_analyses_and_opens_a_one_file_project(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    repo = git_repo()
    env = isolated_env(tmp_path, SCITOOLS_HOME=str(install(tmp_path / "scitools")))
    report = run_doctor(options(repo.path, env, command_log))
    assert report.understand.analysis is not None
    assert report.understand.analysis.ok
    assert not [problem for problem in report.problems if "analysis" in problem]


def test_an_installation_whose_licence_says_yes_but_cannot_analyse_is_a_problem(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """The 8.0.1262 morning: ``license: ok``, both API probes ok, every ``und analyze`` dead.

    ``-isundlicensed`` and the API probes ask questions the gate does not depend on; only an
    analysis does. The failure text is what an operator needs, so it is carried whole.
    """
    repo = git_repo()
    no_server = UndAnswers(analysis_rc=2, analysis_text="No Server Response")
    home = install(tmp_path / "scitools", und=no_server)
    env = isolated_env(tmp_path, SCITOOLS_HOME=str(home))
    report = run_doctor(options(repo.path, env, command_log))
    assert report.understand.license is not None and report.understand.license.ok
    assert report.understand.analysis is not None
    assert not report.understand.analysis.ok
    assert "No Server Response" in report.understand.analysis.text
    assert "No Server Response" in problem_about(report, "analysis")


def test_a_licence_without_the_api_is_caught_when_the_probe_database_is_opened(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """The state that cost a morning: ``1`` from ``-isundlicensed``, ``und analyze`` fine,
    ``understand.version()`` fine, and ``understand.open`` answering ``NoApiLicense``.

    The tool does not run the licence command that would list the missing option -- on 8.0
    that command rewrote the licence file -- so the probe opens what it analysed, in the API
    mode a check would use, and the problem carries the vendor's page.
    """
    repo = git_repo()
    home = install(tmp_path / "scitools", mode="api_unlicensed")
    env = isolated_env(tmp_path, SCITOOLS_HOME=str(home))
    report = run_doctor(options(repo.path, env, command_log))
    assert report.understand.license is not None and report.understand.license.ok
    assert report.understand.api_mode == "upython"
    assert report.understand.analysis is not None
    assert not report.understand.analysis.ok
    assert "NoApiLicense" in report.understand.analysis.text
    problem = problem_about(report, "NoApiLicense")
    assert "command-line-licensing" in problem
