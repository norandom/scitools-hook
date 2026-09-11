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
from doctor_stubs import UndAnswers, install, isolated_env, options, seam

from scitools_hook.cli.doctor import NO_PROBE, NOT_CHECKED, render_report
from scitools_hook.models.understand import Feature, FeatureReport
from scitools_hook.runner.doctor import run_doctor
from scitools_hook.understand.features import FEATURES_FILE, NO_CATALOGUE, NO_LEXER_ANSWER

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
    """The 8.0 shape: seven features measured by running the thing that needs them, and two
    recorded as available on every build because references are what Understand *is*."""
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
        Feature.LEAN_REFERENCES: "available",
        Feature.LEAN_TOKENS: "available",
        Feature.DUPLICATE_METRIC: "available",
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


# --- the rows an operator reads --------------------------------------------------------


def test_the_report_prints_one_row_per_feature(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """Requirement 1.1: one row per feature, named so an operator can match them to the docs."""
    home = install(tmp_path / "scitools", und=understand_8(), mode="understand8", api="stub")
    env = isolated_env(tmp_path, SCITOOLS_HOME=str(home))

    text = render_report(run_doctor(options(git_repo().path, env, command_log)))

    for label in (
        "feature understand sarif",
        "feature accuracy",
        "feature generated archs",
        "feature commit before",
        "feature plugin metrics",
        "feature unused rule",
        "feature lean references",
        "feature lean tokens",
        "feature duplicate metric",
    ):
        assert label in text, label
    assert text.count("available") >= 9


def test_a_feature_the_report_does_not_answer_says_it_was_not_measured(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """The rows follow the `Feature` enum and the answers follow a stored report.

    The two can legitimately disagree -- a report written by an older version of the Gate has
    no entry for a feature added since -- and the row used to answer "the fixture seam starts
    no processes" for that, on a build that had just been probed for every other feature.
    That is a confident wrong answer in the one command whose whole job is to say what is
    true.

    Task 5.5 wrote the last two probes, so no shipped feature is unanswered any more and the
    disagreement has to be *made* rather than found: one entry is taken out of a full report
    and the row is read back. What survives is the rule, which is the half that was always
    the point -- a feature the report does not answer says it was not measured, and never
    blames the fixture seam.
    """
    home = install(tmp_path / "scitools", und=understand_8(), mode="understand8", api="stub")
    env = isolated_env(tmp_path, SCITOOLS_HOME=str(home))
    report = run_doctor(options(git_repo().path, env, command_log))
    assert report.understand.features is not None, f"no probe ran; problems: {report.problems}"
    del report.understand.features.features[Feature.LEAN_TOKENS]

    text = render_report(report)

    assert f"feature lean tokens: {NO_PROBE}" in text
    # Narrowed to the feature row on purpose: `NOT_CHECKED` is still the right answer for the
    # interpreter row when no pin was read, and asserting its absence from the whole report
    # would fail this test for a row that has nothing to do with features.
    assert f"feature lean tokens: {NOT_CHECKED}" not in text
    assert "feature duplicate metric: available" in text


def test_a_row_for_a_missing_feature_carries_the_builds_reason(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """`not on this build` on its own would send the operator back to the terminal."""
    home = install(tmp_path / "scitools")
    env = isolated_env(tmp_path, SCITOOLS_HOME=str(home))

    text = render_report(run_doctor(options(git_repo().path, env, command_log)))

    assert "not on this build" in text
    assert "Unrecognized arguments" in text


def test_the_generated_row_says_how_many_the_build_offers(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """The count is the useful part: the names go in the error when one is misspelt."""
    home = install(tmp_path / "scitools", und=understand_8(), mode="understand8", api="stub")
    env = isolated_env(tmp_path, SCITOOLS_HOME=str(home))

    text = render_report(run_doctor(options(git_repo().path, env, command_log)))

    assert "feature generated archs" in text
    assert "available (3 offered)" in text


def test_the_test_seam_says_unverified_rather_than_guessing(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """There is no Understand behind the seam, so it can say nothing about a build's features."""
    fixtures, env = seam(tmp_path)

    text = render_report(run_doctor(options(git_repo().path, env, command_log)))

    assert text.count("unverified") >= 8
    assert "runs no Understand at all" in text


def test_an_installation_that_never_probed_prints_no_feature_rows(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """Nine `unknown` rows would say the same nothing nine times over."""
    no_analysis = UndAnswers(analysis_rc=2, analysis_text="No Server Response")
    home = install(tmp_path / "scitools", und=no_analysis)
    env = isolated_env(tmp_path, SCITOOLS_HOME=str(home))

    text = render_report(run_doctor(options(git_repo().path, env, command_log)))

    assert "feature " not in text


# --- the two probes the token rules stand on (task 5.5) -------------------------------
#
# `LEAN_TOKENS` is what the duplicate-block and similar-routine rules need and
# `DUPLICATE_METRIC` is the optional metric of requirement 5.6; neither is inferred from a
# build number. Each case below moves ONE of the two and asserts that the other did not move,
# because a stub that failed every catalogue question at once would pass either assertion.


def test_a_build_whose_lexer_refuses_does_not_offer_the_token_rules(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """`Ent.lexer` is the whole of what the two token rules read, so its refusal is theirs."""
    features = offered(
        tmp_path, git_repo, command_log, und=understand_8(), mode="no_lexer", api="stub"
    )

    tokens = features.features[Feature.LEAN_TOKENS]
    assert tokens.state == "not on this build"
    assert "unable to lex probe.py" in tokens.detail
    assert features.features[Feature.DUPLICATE_METRIC].state == "available"


def test_a_build_without_the_duplicates_solution_names_the_plugin_manager(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """Requirement 5.6: the solution ships in the install tree and is invisible until enabled.

    So "not on this build" on its own would send an operator to the vendor for something a
    checkbox fixes, which is why the detail has to name where the checkbox is.
    """
    features = offered(
        tmp_path, git_repo, command_log, und=understand_8(), mode="no_duplicates", api="stub"
    )

    metric = features.features[Feature.DUPLICATE_METRIC]
    assert metric.state == "not on this build"
    assert "Plugin Manager" in metric.detail
    assert features.features[Feature.LEAN_TOKENS].state == "available"


def test_a_build_that_answers_no_catalogue_at_all_offers_neither(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """The 6.5 shape: nothing answers the catalogue, and both probes say so rather than yes.

    The interpreter answers the ping document to every operation, so the answer comes back
    carrying neither the lookup nor the lexer report. An absent lexer report is the Gate's own
    defect rather than a missing capability, and the detail says that rather than reading the
    missing key as a lexer that returned nothing.
    """
    features = offered(tmp_path, git_repo, command_log, api="stub")

    tokens = features.features[Feature.LEAN_TOKENS]
    assert tokens.state == "not on this build"
    assert tokens.detail == NO_LEXER_ANSWER
    metric = features.features[Feature.DUPLICATE_METRIC]
    assert metric.state == "not on this build"
    assert "Plugin Manager" in metric.detail


def test_a_probe_that_could_not_be_asked_is_unverified_rather_than_missing(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """No working API mode is this machine's problem, not a statement about the build.

    A configuration asking for something unverified fails closed, so the two states must not
    be confused: `not on this build` says the build lacks the feature and is final. This
    installation decides no API mode at all, so the probes have nothing to ask and say so.
    """
    features = offered(tmp_path, git_repo, command_log, und=understand_8(), mode="refusing")

    for feature in (Feature.LEAN_TOKENS, Feature.DUPLICATE_METRIC, Feature.PLUGIN_METRICS):
        found = features.features[feature]
        assert found.state == "unverified", feature
        assert found.detail == NO_CATALOGUE, feature


def test_a_catalogue_that_died_is_reported_in_the_words_it_died_with(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """A worker that exits non-zero raises rather than answering, and every probe that asked
    it has to answer from the exception instead of from a document it never received.

    Distinct from the case above: there *is* a working API mode here, the operation simply
    died, so ``unverified`` would be the wrong word -- nothing about this machine stopped the
    question being asked. An operator gets what the interpreter printed on the way down.
    """
    features = offered(
        tmp_path, git_repo, command_log, und=understand_8(), mode="refusing_catalogue", api="stub"
    )

    for feature in (Feature.LEAN_TOKENS, Feature.DUPLICATE_METRIC, Feature.PLUGIN_METRICS):
        found = features.features[feature]
        assert found.state == "not on this build", feature
        assert "the catalogue operation died" in found.detail, feature
