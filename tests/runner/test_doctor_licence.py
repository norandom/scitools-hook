"""``doctor`` on the licence and on whether anything analyses at all (req 1.4, 1.5).

Three questions, in the order an operator meets them: is there a licence, does it carry the
option the gate reads every metric through, and does ``und`` analyse a one-file project right
now. Each answer is reported, never raised, and each failure is a ``problems`` entry that
quotes ``und``'s own words -- the line an operator can act on -- and points at the vendor's
command-line licensing page rather than at anything this tool could do about it.
"""

from __future__ import annotations

from pathlib import Path

from conftest import FakeCommandLog, MakeGitRepo
from doctor_stubs import UndAnswers, install, isolated_env, options, problem_about

from scitools_hook.runner.doctor import run_doctor
from scitools_hook.understand.und_cli import API_OPTION

WITH_API = (
    "Reply Code : 9C0A6E2B1D4F7\nReply Date : 2036-09-05\n\nLicense codes:\n"
    "  license/code (INI)             : (not set)\n\nEnabled Options\n\nGUI Access\n"
    "Perpetual License\nExport & Share Reports/Metrics\nCommand Line Access via Und\n"
    "API Access\nVS Code Plugin\nOnboard\n"
)
"""``und license`` on 8.0.1262 after the offline activation of 2026-09-05, but for the code."""


def test_the_enabled_options_are_reported_and_a_licence_with_the_api_raises_no_problem(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    repo = git_repo()
    home = install(tmp_path / "scitools", und=UndAnswers(license_text=WITH_API))
    env = isolated_env(tmp_path, SCITOOLS_HOME=str(home))
    report = run_doctor(options(repo.path, env, command_log))
    assert report.understand.license is not None
    assert report.understand.license.ok
    assert API_OPTION in report.understand.license.options
    assert not [problem for problem in report.problems if "license" in problem]


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


WITHOUT_API = (
    "Reply Code : 85DF6F5DAD6BF\nReply Date : 2036-09-05\n\nLicense codes:\n"
    "  license/code (INI)             : (not set)\n\nEnabled Options\n\nGUI Access\n"
    "Perpetual License\nCommand Line Access via Und\nOnboard\n"
)
"""``und license`` on 2026-09-05, verbatim but for the code: licensed, and not for the API."""


def test_a_licence_without_the_api_option_is_named_as_the_problem(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """The state that cost a morning: `und analyze` ran, every metric read said NoApiLicense.

    ``-isundlicensed`` says 1 for it. Only the option list says what is missing, and the
    problem must point the operator at the vendor's command-line licensing page rather than
    at anything this tool could do about it.
    """
    repo = git_repo()
    home = install(tmp_path / "scitools", und=UndAnswers(license_text=WITHOUT_API))
    env = isolated_env(tmp_path, SCITOOLS_HOME=str(home))
    report = run_doctor(options(repo.path, env, command_log))
    assert report.understand.license is not None
    assert report.understand.license.options == [
        "GUI Access",
        "Perpetual License",
        "Command Line Access via Und",
        "Onboard",
    ]
    problem = problem_about(report, "API Access")
    assert "command-line-licensing" in problem


def test_a_healthy_installation_analyses_a_one_file_project(
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
