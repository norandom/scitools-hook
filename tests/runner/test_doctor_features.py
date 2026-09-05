"""What `doctor` measures about the installed build, and what it stores (req 1.1, 1.4).

Availability is measured, never inferred from a version number: 6.5 and 8.0 already differ in
three of these, and a build number says nothing about a feature backported into one. So the
installations below are shell scripts that answer like each build does, and the probe reads
what came back.

The third state is the one worth testing hardest. ``unverified`` is a probe that could not
run, and it is not ``not on this build`` -- a configuration asking for something unverified
fails closed rather than being ignored (task 2.3).
"""

from __future__ import annotations

import json
from pathlib import Path

from conftest import FakeCommandLog, MakeGitRepo
from doctor_stubs import UndAnswers, install, isolated_env, options

from scitools_hook.models.understand import Feature, FeatureReport
from scitools_hook.runner.doctor import run_doctor
from scitools_hook.understand.features import FEATURES_FILE

ARCH_LISTING = (
    "Directory Structure   active\nGit Stability         available\nGit Owner             available"
)
"""Three of the 21 rows Build 1262 prints; enough to prove the listing is read and carried."""

ACCURACY_LINE = "25 of 92 parsed files had no errors or warnings (27%)"
"""What ``-accuracy`` adds after the summary, verbatim from Build 1262."""


def understand_8() -> UndAnswers:
    """An installation that answers every 8.0 command the probe asks."""
    return UndAnswers(
        arch_listing=ARCH_LISTING,
        accuracy_line=ACCURACY_LINE,
        writes_sarif=True,
        commit_create=True,
    )


def offered(tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog, **kwargs: object):
    """Run `doctor` against a stubbed installation and answer its feature report."""
    home = install(tmp_path / "scitools", **kwargs)  # type: ignore[arg-type]
    env = isolated_env(tmp_path, SCITOOLS_HOME=str(home))
    report = run_doctor(options(git_repo().path, env, command_log))
    assert report.understand.features is not None, f"no probe ran; problems: {report.problems}"
    return report.understand.features


# --- a build that has everything ----------------------------------------------------


def test_a_build_that_answers_every_command_offers_every_feature(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """The 8.0 shape: six features, each measured by running the thing that needs it."""
    features = offered(
        tmp_path, git_repo, command_log, und=understand_8(), mode="understand8", api="stub"
    )

    assert {feature: found.state for feature, found in features.features.items()} == {
        Feature.UNDERSTAND_SARIF: "available",
        Feature.ACCURACY: "available",
        Feature.GENERATED_ARCHS: "available",
        Feature.COMMIT_BEFORE: "available",
        Feature.PLUGIN_METRICS: "available",
        Feature.UNUSED_RULE: "available",
    }


def test_the_generated_architectures_are_carried_by_name(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """Requirement 4.2 refuses a configured name with the names the build does offer."""
    features = offered(
        tmp_path, git_repo, command_log, und=understand_8(), mode="understand8", api="stub"
    )

    assert features.features[Feature.GENERATED_ARCHS].generated == [
        "Directory Structure",
        "Git Stability",
        "Git Owner",
    ]


def test_the_report_records_the_build_it_was_measured_on(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """A report from another build must read as stale rather than as an answer."""
    features = offered(
        tmp_path, git_repo, command_log, und=understand_8(), mode="understand8", api="stub"
    )

    assert features.build == "(Build 1204)"
    assert features.offers(Feature.ACCURACY) is True


# --- a build that has none of it ------------------------------------------------------


def test_a_build_that_refuses_the_new_commands_offers_none_of_them(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """The 6.5 shape: every new switch is an unrecognised argument, and says so."""
    features = offered(tmp_path, git_repo, command_log)

    assert features.features[Feature.GENERATED_ARCHS].state == "not on this build"
    assert features.features[Feature.ACCURACY].state == "not on this build"
    assert features.features[Feature.UNDERSTAND_SARIF].state == "not on this build"
    assert features.features[Feature.COMMIT_BEFORE].state == "not on this build"
    assert features.offers(Feature.ACCURACY) is False


def test_a_refusal_is_reported_in_the_builds_own_words(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """An operator asking why gets what `und` said, not this tool's paraphrase of it."""
    features = offered(tmp_path, git_repo, command_log)

    assert "Unrecognized arguments" in features.features[Feature.COMMIT_BEFORE].detail


def test_a_build_that_writes_no_sarif_does_not_offer_it_however_it_exited(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """The file is the answer: a build that ignored the switch would otherwise look able."""
    answers = UndAnswers(accuracy_line=ACCURACY_LINE, writes_sarif=False)
    features = offered(tmp_path, git_repo, command_log, und=answers)

    assert features.features[Feature.ACCURACY].state == "available"
    assert features.features[Feature.UNDERSTAND_SARIF].state == "not on this build"


def test_the_unused_rule_needs_no_build_support_at_all(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """It is computed from references every build reports, so it is always available."""
    features = offered(tmp_path, git_repo, command_log)

    assert features.features[Feature.UNUSED_RULE].state == "available"


def test_a_catalogue_that_cannot_be_asked_is_unverified_not_missing(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """No working API mode is this machine's problem, not a statement about the build."""
    features = offered(tmp_path, git_repo, command_log, und=understand_8(), mode="refusing")

    plugin = features.features[Feature.PLUGIN_METRICS]
    assert plugin.state in {"unverified", "not on this build"}
    assert plugin.detail


# --- the stored report ----------------------------------------------------------------


def test_the_report_is_stored_beside_the_analysis_databases(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """A check reads it to validate its configuration without paying for a probe (task 2.3)."""
    home = install(tmp_path / "scitools", und=understand_8(), mode="understand8", api="stub")
    env = isolated_env(tmp_path, SCITOOLS_HOME=str(home), XDG_CACHE_HOME=str(tmp_path / "cache"))

    report = run_doctor(options(git_repo().path, env, command_log))

    assert report.cache is not None
    stored = report.cache.root / FEATURES_FILE
    assert stored.exists(), f"no feature report stored; problems: {report.problems}"
    again = FeatureReport.model_validate(json.loads(stored.read_text(encoding="utf-8")))
    assert again.offers(Feature.GENERATED_ARCHS) is True
    assert again.build == report.understand.und_version


def test_a_probe_that_never_ran_stores_nothing_and_says_so(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """An installation that cannot analyse has nothing to report about its features."""
    no_analysis = UndAnswers(analysis_rc=2, analysis_text="No Server Response")
    home = install(tmp_path / "scitools", und=no_analysis)
    env = isolated_env(tmp_path, SCITOOLS_HOME=str(home), XDG_CACHE_HOME=str(tmp_path / "cache"))

    report = run_doctor(options(git_repo().path, env, command_log))

    assert report.understand.features is None
    assert report.cache is not None
    assert not (report.cache.root / FEATURES_FILE).exists()
