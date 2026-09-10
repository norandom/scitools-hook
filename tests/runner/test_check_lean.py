"""The lean step inside the check pipeline: rules, notes, the delta and the worked example.

The family's rules are pure functions with unit tests of their own; what this module pins is
the *wiring*, which is where a rule family stops being real. Four properties, each of which
has a way of being green and inert:

* **A rule that is off costs nothing** (requirement 9.4). The shipped configuration must run
  the same rules it ran before this family existed, ask the worker for nothing on its behalf,
  and say nothing on the diagnostics channel.
* **The delta does not wait for a rule to be enabled.** Requirement 7.1 prints it whenever a
  check has a before side and attaches no condition about the lean rules; requirement 7.6
  scopes itself explicitly with "when lean-code rules are enabled", which is the author
  scoping by enablement where they meant it.
* **A lean finding is an ordinary structural finding** (requirement 9.6): it goes through
  ``classify``, the severity map, the hint catalogue and, new here, the worked example.
* **The net-growth finding has an EMPTY path**, because a project-scope finding has no file.
  ``matching_pattern(["*"], "")`` answers ``"*"`` -- an empty path is matched by any glob that
  does not require a separator -- so the day somebody runs a structural finding's path through
  ``[ignore]`` or a ``[scope.*]`` matcher, this rule either disappears or matches everything.
  The test below configures both against a running change and pins that it survives.

The harness, the snapshot builders and the sample repository come from
:mod:`test_check_pipeline`; this module holds only the lean scenarios, which is the split
task 2.5's review asked for when a single test module reached for twelve collaborators.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import MakeGitRepo
from test_check_pipeline import (
    Harness,
    built,
    edge,
    fixture_snapshot,
    make_harness,
    routine,
    sample_repository,
    source_file,
)

from scitools_hook.config.defaults import default_settings
from scitools_hook.config.models import (
    IgnoreRules,
    LeanRules,
    PathScope,
    Settings,
    matching_pattern,
)
from scitools_hook.runner.lean import LeanResult

OVER_EXPORT = "structure.over_export"
"""The one lean rule that can run today; the other five need extraction groups 3 and 5."""

NET_GROWTH = "structure.net_growth"
"""The optional maximum on the delta, and the only project-scope finding this family makes."""


def a_lean_repository(
    git_repo: MakeGitRepo, tmp_path: Path, settings: Settings, name: str = "repo"
) -> Harness:
    """``src/one.py`` restaged, with ``src/user.py`` the only file that depends on it.

    The after side is the over-exporting shape: one declaration, no module-level binding, one
    dependant. The routine inside it grows from four statements to nine, so the change has a
    net delta of ``+5`` over one routine whichever rules are on.
    """
    builder = git_repo(name)
    for path in ("src/one.py", "src/user.py"):
        builder.write(path, "# x\n")
    builder.stage("src/one.py", "src/user.py")
    builder.commit("initial")
    builder.write("src/one.py", "# changed\n")
    builder.stage("src/one.py")
    files = [
        source_file("src/one.py", CountDeclFunction=1, CountDeclClass=0, CountLineCode=12),
        source_file("src/user.py", CountDeclFunction=2, CountDeclClass=0, CountLineCode=30),
    ]
    edges = [edge("src/user.py", "src/one.py")]
    after = built(
        "after",
        [*files, routine("src/one.py", "one.only", CountStmt=9, CountLineCode=11)],
        edges,
    )
    before = built(
        "before",
        [*files, routine("src/one.py", "one.only", CountStmt=4, CountLineCode=5)],
        edges,
    )
    return make_harness(
        builder,
        tmp_path / name,
        settings,
        answers={"after": [after, after], "before": [before, before]},
    )


def lean_settings(**rules: object) -> Settings:
    """The shipped settings with one ``[lean]`` section replaced."""
    settings = default_settings()
    settings.lean = LeanRules(**rules)
    return settings


# --- a rule that is on ------------------------------------------------------------


def test_the_over_export_rule_reaches_the_run_result_when_it_is_enabled(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    harness = a_lean_repository(git_repo, tmp_path, lean_settings(over_export="warning"))

    result = harness.run()

    reported = [f for f in result.findings if f.rule == OVER_EXPORT]
    assert [(f.path, f.severity, f.blocking) for f in reported] == [
        ("src/one.py", "warning", False)
    ]
    assert reported[0].details["dependant"] == "src/user.py"


def test_a_lean_finding_carries_both_its_hint_and_its_worked_example(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """Requirement 8.2: the example travels in ``details`` beside the hint, for every format."""
    harness = a_lean_repository(git_repo, tmp_path, lean_settings(over_export="warning"))

    found = next(f for f in harness.run().findings if f.rule == OVER_EXPORT)

    assert found.hint.startswith("yagni:")
    assert "src/formatCurrency.ts" in str(found.details["example"])


def test_a_finding_outside_the_lean_family_is_given_no_example(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """An example is only ever right for the rule that ships one; a generic one teaches wrong.

    Without the guard in ``check._guided`` every finding in the run would carry an ``example``
    key, empty or otherwise, in its JSON and in its SARIF property bag.
    """
    harness = a_lean_repository(git_repo, tmp_path, lean_settings(over_export="warning"))

    outside = [f for f in harness.run().findings if f.rule != OVER_EXPORT]

    assert outside
    assert not [f for f in outside if "example" in f.details]


def test_the_rule_is_silent_on_a_file_its_ignore_list_names(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """The rule's own ignore list reaches it from the pipeline, not only from a unit test."""
    settings = lean_settings(over_export="warning", over_export_ignore=["src/one.py"])

    result = a_lean_repository(git_repo, tmp_path, settings).run()

    assert [f for f in result.findings if f.rule == OVER_EXPORT] == []


def test_the_configured_severity_is_what_the_finding_carries(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """``error`` blocks, which is the whole difference between a report and a gate."""
    harness = a_lean_repository(git_repo, tmp_path, lean_settings(over_export="error"))

    found = next(f for f in harness.run().findings if f.rule == OVER_EXPORT)

    assert (found.severity, found.blocking) == ("error", True)


# --- a rule that is off -----------------------------------------------------------


def test_switching_every_lean_rule_off_leaves_the_run_as_it_was_but_for_the_delta(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """Requirement 9.4, stated as a difference rather than as a count.

    The same change is run twice over two copies of one repository, once with the family's
    one runnable rule on and once with the shipped configuration. Everything the two answers
    have that the other does not is the over-export finding; the delta is the same number in
    both, because requirement 7.1 does not wait for a rule to be enabled.
    """
    on = a_lean_repository(git_repo, tmp_path, lean_settings(over_export="warning"), "on").run()
    off = a_lean_repository(git_repo, tmp_path, default_settings(), "off").run()

    subjects = sorted((f.kind, f.rule, f.path) for f in off.findings)
    assert sorted((f.kind, f.rule, f.path) for f in on.findings) == sorted(
        [*subjects, ("structural", OVER_EXPORT, "src/one.py")]
    )
    assert off.net_delta is not None
    assert off.net_delta == on.net_delta


def test_an_all_off_run_says_nothing_on_the_diagnostics_channel_about_the_family(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """A note per run is cheap; a note per run for a rule nobody enabled is noise (9.4)."""
    harness = a_lean_repository(git_repo, tmp_path, default_settings())

    harness.run()

    assert not [note for note in harness.notes if "lean" in note or "export" in note]


def test_the_delta_is_reported_even_though_every_lean_rule_is_off(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """Requirement 7.1 attaches no condition about the lean rules to the delta.

    Requirement 7.6, the "nothing to cut" line, scopes itself explicitly with "when lean-code
    rules are enabled", which is the author scoping by enablement where they meant it. So an
    all-off run is today's run plus this one number.
    """
    result = a_lean_repository(git_repo, tmp_path, default_settings()).run()

    assert result.net_delta is not None
    assert (result.net_delta.statements, result.net_delta.lines) == (5, 6)
    assert result.net_delta.routines == 1


def test_a_whole_project_run_has_no_delta_to_report(git_repo: MakeGitRepo, tmp_path: Path) -> None:
    """Requirement 7.4: ``--all`` has no before side, and absence is not a zero."""
    builder = sample_repository(git_repo)
    harness = make_harness(builder, tmp_path, answers={"after": [fixture_snapshot("after")]})

    assert harness.run(mode="all").net_delta is None


# --- the project-scope finding, whose path is empty --------------------------------


def test_an_empty_path_is_what_every_glob_matches() -> None:
    """Why the test below exists, stated as the measurement rather than as a worry.

    ``path_prefixes("")`` is ``[""]`` and ``*`` compiles to ``[^/]*``, which full-matches the
    empty string. So a project-scope finding run through a path matcher is matched by any
    glob that does not require a separator, and refused by every one that does.
    """
    assert matching_pattern(["*"], "") == "*"
    assert matching_pattern(["src/**"], "") is None


def test_the_net_growth_finding_survives_the_ignore_patterns_and_the_scope_overrides(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """A project-scope finding carries an empty path, and the finishing step must not lose it.

    Both configurations below would swallow the finding if its path were ever handed to a path
    matcher: ``[ignore] files`` names every entity and ``[scope.everything]`` covers every
    path, including -- by the measurement above -- the empty one.
    """
    settings = lean_settings(max_net_growth=0, net_growth_severity="error")
    settings.ignore = IgnoreRules(files=[r".*"], classes=[r".*"], routines=[r".*"])
    settings.scope = {"everything": PathScope(paths=["*", "**"])}

    result = a_lean_repository(git_repo, tmp_path, settings).run()

    growth = [f for f in result.findings if f.rule == NET_GROWTH]
    assert [(f.path, f.scope, f.severity, f.blocking) for f in growth] == [
        ("", "project", "error", True)
    ]
    assert growth[0].details["net_routines"] == 1


def test_a_run_with_no_before_side_cannot_break_a_configured_maximum(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """A maximum is judged against a delta, and ``--all`` has none to judge (req 7.4, 7.5).

    Without the absence guard the step would hand ``None`` to the finding builder, which reads
    ``delta.statements``: the one configuration that reaches it is a maximum set on a
    whole-project run, so that is the configuration this test uses.
    """
    settings = lean_settings(max_net_growth=0)
    builder = sample_repository(git_repo)
    harness = make_harness(
        builder, tmp_path, settings, answers={"after": [fixture_snapshot("after")]}
    )

    result = harness.run(mode="all")

    assert result.net_delta is None
    assert [f for f in result.findings if f.rule == NET_GROWTH] == []


def test_no_maximum_configured_raises_no_growth_finding(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """Requirement 7.5: the delta never blocks unless an operator asks it to."""
    result = a_lean_repository(git_repo, tmp_path, lean_settings()).run()

    assert result.net_delta is not None
    assert [f for f in result.findings if f.rule == NET_GROWTH] == []


# --- what the rules say they could not measure --------------------------------------


def test_the_unavailable_messages_are_said_once_per_run(
    git_repo: MakeGitRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requirements 1.6 and 2.5: a rule that could not measure says so once, not per entity.

    No rule in the family can answer "unavailable" yet -- over-export reads only what the
    snapshot already carries -- so the seam is driven from the step's own return value. That
    is the whole point of pinning it now: groups 4 and 5 add five rules that report this way,
    and a step whose notes nobody printed would swallow every one of them silently.
    """
    said = LeanResult(findings=[], notes=["lean: the references were not recorded"], net_delta=None)
    monkeypatch.setattr("scitools_hook.runner.check.evaluate_lean", lambda *args: said)

    harness = a_lean_repository(git_repo, tmp_path, lean_settings(over_export="warning"))
    result = harness.run()

    assert harness.notes.count("lean: the references were not recorded") == 1
    assert result.net_delta is None
