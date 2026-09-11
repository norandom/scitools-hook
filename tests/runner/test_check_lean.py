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

from collections.abc import Sequence
from pathlib import Path
from typing import Final, NamedTuple

import pytest
from conftest import MakeGitRepo
from fixtures.constants import LEAN_REFERENCE_RULES, LEAN_TOKEN_RULES
from test_check_pipeline import (
    SAFE_POPULATIONS,
    Harness,
    built,
    edge,
    fixture_snapshot,
    make_harness,
    routine,
    sample_repository,
    source_file,
)

from scitools_hook.analysis.lean.dead import LeanOutcome
from scitools_hook.analysis.narrow import narrow
from scitools_hook.config.defaults import default_settings
from scitools_hook.config.models import (
    IgnoreRules,
    LeanRules,
    PathScope,
    Settings,
    matching_pattern,
)
from scitools_hook.models.change import AffectedSet
from scitools_hook.models.findings import RunResult
from scitools_hook.models.snapshot import EntityKey, ProjectSnapshot, Side
from scitools_hook.models.understand import AnalyzeResult
from scitools_hook.runner import lean as lean_step
from scitools_hook.runner.lean import LeanResult

OVER_EXPORT = "structure.over_export"
"""The lean rule answered from metrics and edges alone, and the one this module started with."""

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


# --- the five rules that read the reference walk (task 4.3) -------------------------
#
# `a_lean_repository` above answers over-export from metrics and edges alone. The five rules
# added by groups 3 and 4 read facts a snapshot only carries when the extractor was asked for
# them, and every one of them refuses unless the run also carries an accuracy figure and a
# call-resolution share above requirement 1.8's two floors. So they need a second fixture,
# and the fixture's whole job is to be *believable*: facts on every record, a measured
# declaring-class tally, a resolution share above the floor, and an accuracy figure on the
# after side.

FACT_FILE: Final = "src/lean/thing.py"

REFERENCE_RULES: Final[tuple[str, ...]] = (
    "structure.unused_parameter",
    "structure.unused_class",
    "structure.unused_variable",
    "structure.pass_through",
    "structure.single_implementation",
)
"""The five rules this task wires in, by the id the finding carries."""

TRUSTED_RESOLUTION: Final[dict[str, object]] = {
    "Python": {"resolved": 8, "external": 1, "unresolved": 1}
}
"""80% of call sites resolved: above the shipped 75% floor, and constructed rather than
measured -- no corpus has paired a resolution share with a false-positive count."""

REFUSED_RESOLUTION: Final[dict[str, object]] = {
    "Python": {"resolved": 1, "external": 1, "unresolved": 8}
}
"""10% resolved: under any floor an operator could reasonably set."""

TRUSTED_ACCURACY: Final = 0.9
REFUSED_ACCURACY: Final = 0.1


def _lean_record(
    scope: str, longname: str, kind: str, facts: dict[str, object], **metrics: float
) -> dict[str, object]:
    """One entity record carrying the lean facts the reference walk records for its kind."""
    record = (
        routine(FACT_FILE, longname, **metrics)
        if scope == "routine"
        else _class_record(longname, kind)
    )
    return {**record, "lean": facts}


def _class_record(longname: str, kind: str) -> dict[str, object]:
    """One class record; `test_check_pipeline` builds files and routines but no classes."""
    return {
        "ref": {
            "key": {
                "scope": "class",
                "path": FACT_FILE,
                "longname": longname,
                "parameters": None,
            },
            "kind": kind,
            "name": longname.rsplit(".", 1)[-1],
            "line": 1,
        },
        "language": "Python",
        "metrics": {},
        "archs": [],
    }


def _facts_routines() -> list[dict[str, object]]:
    """The three routines: one with a spare parameter, one that forwards, one that is called.

    ``thing.forward`` is the pass-through shape and the other two are what keep the rule from
    reporting them: ``consume`` has two callers and two callees, ``target`` has four callers.
    """
    return [
        _lean_record(
            "routine",
            "thing.consume",
            "Python Function",
            {"callers": 2, "callees": 2, "overrides": False, "unused_parameters": ["spare"]},
            CountStmt=8,
            CountLineCode=10,
        ),
        _lean_record(
            "routine",
            "thing.forward",
            "Python Function",
            {
                "callers": 1,
                "callees": 1,
                "forwards_to": "thing.target",
                "overrides": False,
                "unused_parameters": [],
            },
            CountStmt=1,
            CountLineCode=2,
        ),
        _lean_record(
            "routine",
            "thing.target",
            "Python Function",
            {"callers": 4, "callees": 0, "overrides": False, "unused_parameters": []},
            CountStmt=5,
            CountLineCode=6,
        ),
    ]


def _facts_classes() -> list[dict[str, object]]:
    """The three classes, and the two rules about classes are kept apart by their facts.

    ``thing.Orphan`` is unreferenced with no derived class, so only the unused-class rule can
    reach it; ``thing.Provider`` is referenced with one derived class, so only the
    single-implementation rule can; ``thing.Sole`` is that derived class and is reported by
    neither. A finding that moved between the two rules therefore shows as a count.
    """
    return [
        _lean_record(
            "class",
            "thing.Orphan",
            "Python Class",
            {"referenced": False, "derived": [], "referrers": 0},
        ),
        _lean_record(
            "class",
            "thing.Provider",
            "Python Class",
            {"referenced": True, "derived": ["thing.Sole"], "referrers": 0},
        ),
        _lean_record(
            "class",
            "thing.Sole",
            "Python Class",
            {"referenced": True, "derived": [], "referrers": 2},
        ),
    ]


def facts_snapshot(
    side: Side, resolution: dict[str, object] = TRUSTED_RESOLUTION
) -> ProjectSnapshot:
    """One snapshot in which each of the five rules has exactly one thing to report.

    Every record carries measured facts and ``method_declarations`` is a measured empty
    tally, so no rule reports itself unavailable for want of them: what is left to decide the
    run is the pair of floors, which is what the tests below move. The populations are the
    ones ``test_check_pipeline.SAFE_POPULATIONS`` uses, because a snapshot that says nothing
    about the project breaks the shipped project thresholds instead.
    """
    return ProjectSnapshot.model_validate(
        {
            "side": side,
            "languages": ["Python"],
            "entities": [
                source_file(FACT_FILE, CountDeclFunction=3, CountDeclClass=3, CountLineCode=40),
                *_facts_routines(),
                *_facts_classes(),
            ],
            "definitions": [
                {"name": "UNREAD", "path": FACT_FILE, "line": 3, "referenced": False},
                {"name": "READ", "path": FACT_FILE, "line": 4, "referenced": True},
            ],
            "method_declarations": {},
            "call_resolution": resolution,
            "arch_nodes": [{"path": "Directory Structure/src", "members": []}],
            "populations": {scope: dict(v) for scope, v in SAFE_POPULATIONS.items()},
        }
    )


def every_reference_rule(**overrides: object) -> Settings:
    """The five reference rules on as warnings, with anything else the test wants moved."""
    return lean_settings(**{**dict.fromkeys(LEAN_REFERENCE_RULES, "warning"), **overrides})


class Measured(NamedTuple):
    """What a run measured about its own analysis, which is what the two floors judge.

    ``accuracy`` is ``(after, before)``, and the two differ by default because the pair is
    the only way to prove which side the step reads: a step wired to the before side passes
    every test in which both sides carry the same figure.

    One object rather than two parameters because this project's own gate said so: spelled
    out beside ``settings`` and ``name``, ``a_facts_repository`` took six parameters against a
    maximum of five and ``check --worktree`` exited 1 on it -- the same finding task 4.2's
    review recorded, on the same kind of helper. They are also one decision: how far this run
    may be trusted about itself.
    """

    accuracy: tuple[float | None, float | None] = (TRUSTED_ACCURACY, REFUSED_ACCURACY)
    resolution: dict[str, object] = TRUSTED_RESOLUTION


TRUSTED_RUN: Final = Measured()
"""A run above both floors on the after side and below the accuracy floor on the before."""


def a_facts_repository(
    git_repo: MakeGitRepo,
    tmp_path: Path,
    settings: Settings,
    measured: Measured = TRUSTED_RUN,
    name: str = "facts",
) -> Harness:
    """One restaged file whose snapshot carries every fact the five rules read."""
    builder = git_repo(name)
    builder.write(FACT_FILE, "# x\n")
    builder.stage(FACT_FILE)
    builder.commit("initial")
    builder.write(FACT_FILE, "# changed\n")
    builder.stage(FACT_FILE)
    sides = (facts_snapshot(side, measured.resolution) for side in ("after", "before"))
    after, before = sides
    return make_harness(
        builder,
        tmp_path / name,
        settings,
        answers={"after": [after, after], "before": [before, before]},
        analyses=[AnalyzeResult(seconds=0.0, accuracy=found) for found in measured.accuracy],
    )


def rules_of(result: RunResult) -> list[str]:
    """Every lean reference rule that produced a finding in this run, sorted."""
    return sorted(f.rule for f in result.findings if f.rule in REFERENCE_RULES)


def test_a_trusted_run_reports_all_five_reference_rules(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """The wiring itself: five rules, five findings, one per subject the fixture built."""
    result = a_facts_repository(git_repo, tmp_path, every_reference_rule()).run()

    assert rules_of(result) == sorted(REFERENCE_RULES)


def test_each_finding_names_the_subject_its_rule_is_about(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """A count of five would pass with every rule reporting the same entity."""
    result = a_facts_repository(git_repo, tmp_path, every_reference_rule()).run()
    found = {f.rule: f.details for f in result.findings if f.rule in REFERENCE_RULES}

    assert found["structure.unused_parameter"]["parameter"] == "spare"
    assert found["structure.unused_class"]["longname"] == "thing.Orphan"
    assert found["structure.unused_variable"]["definition"] == "UNREAD"
    assert found["structure.pass_through"]["forwards_to"] == "thing.target"
    assert found["structure.single_implementation"]["derived_class"] == "thing.Sole"


@pytest.mark.parametrize("rule", LEAN_REFERENCE_RULES)
def test_one_reference_rule_off_is_the_only_one_missing(
    git_repo: MakeGitRepo, tmp_path: Path, rule: str
) -> None:
    """One test per guard in the step, counted from the code and not from a sentence.

    ``runner.lean`` holds one ``if`` per reference rule, and a guard deleted there makes its
    rule run whatever the configuration says. Switching exactly one rule off and demanding
    exactly one finding fewer is what fails for each of the five separately.
    """
    settings = every_reference_rule(**{rule: None})

    result = a_facts_repository(git_repo, tmp_path, settings, name=f"off-{rule}").run()

    assert len(rules_of(result)) == len(REFERENCE_RULES) - 1


def test_every_reference_rule_off_leaves_the_run_silent_about_them(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """Requirement 9.4: an off rule costs no finding and no note."""
    harness = a_facts_repository(git_repo, tmp_path, lean_settings())

    result = harness.run()

    assert rules_of(result) == []
    assert [note for note in harness.notes if "was not evaluated" in note] == []


# --- requirement 1.8's two floors, and which side's accuracy answers them -----------


def notes_about_floors(harness: Harness) -> list[str]:
    """Every note this run made about a rule it refused to evaluate."""
    return [note for note in harness.notes if "was not evaluated" in note]


def test_a_refused_run_says_so_once_per_rule_and_reports_nothing(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """Requirements 1.6 and 2.5 through the pipeline: five notes, no findings.

    Four of the five rules take the floors; ``single_implementation`` ships with none, which
    the design argues at length, so the count below is four and stating five would be a
    docstring contradicting its own run.
    """
    harness = a_facts_repository(
        git_repo, tmp_path, every_reference_rule(), Measured((REFUSED_ACCURACY, TRUSTED_ACCURACY))
    )

    result = harness.run()

    assert rules_of(result) == ["structure.single_implementation"]
    assert len(notes_about_floors(harness)) == 4


def test_the_after_sides_accuracy_is_the_one_the_rules_are_judged_by(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """The two sides carry opposite figures, so a step reading the wrong one fails here.

    ``check.run`` has both in hand from ``_figures(analyses)``. The after side is the code
    the change proposes and the side every one of these rules reads its facts from; a rule
    licensed by the before side's accuracy would be licensed by an analysis of code that is
    no longer there.
    """
    rules, good, bad = every_reference_rule(), TRUSTED_ACCURACY, REFUSED_ACCURACY
    trusted = a_facts_repository(git_repo, tmp_path, rules, Measured((good, bad)), "after-good")
    refused = a_facts_repository(git_repo, tmp_path, rules, Measured((bad, good)), "after-bad")

    assert len(rules_of(trusted.run())) == len(REFERENCE_RULES)
    assert notes_about_floors(trusted) == []
    assert len(rules_of(refused.run())) == 1


def test_a_run_that_measured_no_accuracy_refuses_rather_than_assumes(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """An absent figure is not a good one: a 6.5 install licenses no dead-code claim (1.8)."""
    harness = a_facts_repository(
        git_repo, tmp_path, every_reference_rule(), Measured((None, TRUSTED_ACCURACY))
    )

    result = harness.run()

    assert rules_of(result) == ["structure.single_implementation"]
    assert "measured no analysis accuracy" in notes_about_floors(harness)[0]


def test_the_configured_accuracy_floor_is_the_one_the_rules_are_held_to(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """``lean.accuracy_floor`` reaches the rules, and the note quotes the operator's number."""
    settings = every_reference_rule(accuracy_floor=0.95)

    harness = a_facts_repository(git_repo, tmp_path, settings)
    result = harness.run()

    assert rules_of(result) == ["structure.single_implementation"]
    assert "accuracy floor of 95%" in notes_about_floors(harness)[0]


def test_the_configured_resolution_floor_is_the_one_the_rules_are_held_to(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """``lean.resolution_floor`` reaches the rules, on a run whose accuracy is fine."""
    settings = every_reference_rule(resolution_floor=0.9)

    harness = a_facts_repository(git_repo, tmp_path, settings)
    result = harness.run()

    assert rules_of(result) == ["structure.single_implementation"]
    assert "call-resolution floor of 90%" in notes_about_floors(harness)[0]


def test_a_run_below_the_resolution_floor_reports_nothing_and_names_the_language(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """The second floor, moved by the measurement rather than by the configuration (1.8)."""
    harness = a_facts_repository(
        git_repo, tmp_path, every_reference_rule(), Measured(resolution=REFUSED_RESOLUTION)
    )

    result = harness.run()

    assert rules_of(result) == ["structure.single_implementation"]
    assert all("Python" in note for note in notes_about_floors(harness))


# --- the two accuracy floors are two decisions -------------------------------------
#
# `analysis.accuracy_floor` and `lean.accuracy_floor` read the same measurement to opposite
# ends: the first RAISES a non-blocking finding to say the run is less trustworthy, and ships
# unset; the second SUPPRESSES rules that cannot be trusted below it, and ships at 0.75. The
# argument for two keys rather than one is written beside both fields; what these two tests
# do is make the independence fail loudly if anyone ever wires one to the other, because two
# numbers that must DIFFER need a binding artefact exactly as two that must agree do.

ACCURACY_RULE: Final = "analysis.accuracy"


def test_lowering_the_analysis_floor_does_not_license_a_dead_code_claim(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """The failure this separation exists to prevent, written as a test.

    An operator on a project whose third-party headers do not resolve lowers
    ``analysis.accuracy_floor`` to stop the warning nagging. If the lean rules read that key,
    they would be unlocked at 10% accuracy -- the configuration in which sixteen of sixteen
    module bindings this repository reports as unreferenced are in fact read.
    """
    settings = every_reference_rule()
    settings.analysis.accuracy_floor = 0.05

    harness = a_facts_repository(
        git_repo, tmp_path, settings, Measured((REFUSED_ACCURACY, REFUSED_ACCURACY))
    )
    result = harness.run()

    assert rules_of(result) == ["structure.single_implementation"]
    assert [f for f in result.findings if f.rule == ACCURACY_RULE] == []


def test_moving_the_lean_floor_raises_no_analysis_accuracy_finding(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """The other direction: ``[lean]`` configures rules, it does not report on the analysis."""
    harness = a_facts_repository(
        git_repo,
        tmp_path,
        every_reference_rule(accuracy_floor=0.99),
        Measured((REFUSED_ACCURACY, REFUSED_ACCURACY)),
    )

    result = harness.run()

    assert [f for f in result.findings if f.rule == ACCURACY_RULE] == []


# --- the two rules answered from the token index (task 5.5) -------------------------
#
# `a_facts_repository` above answers the five reference rules from per-entity facts, and
# every one of them refuses below requirement 1.8's floors. The two rules of this section
# read neither a reference nor an accuracy figure: both are decided from `Ent.lexer(False)`,
# a lexical pass that resolves nothing, and the whole of what they read arrives in one
# snapshot field. So they need a third fixture, and its job is to put exactly one duplicated
# block and exactly one family in front of the change while keeping the two APART -- a
# fixture in which one subject answered both rules would pass with the step calling one rule
# twice.

TOKEN_FILE: Final = "src/lean/twin.py"
"""The changed file: it carries the duplicated block and one member of the family."""

TOKEN_OTHER: Final = "src/lean/other.py"
"""The file the change did not touch, carrying the other copy and the other twin."""

DUPLICATE_RULE: Final = "structure.duplicate_block"
SIMILAR_RULE: Final = "structure.similar_routine"

TOKEN_RULES: Final[tuple[str, ...]] = (DUPLICATE_RULE, SIMILAR_RULE)
"""The two rules this task wires in, by the id the finding carries, in the order
``[lean]`` writes their switches -- which is the order the notes below are asserted in."""

SHIPPED_LEAN: Final = LeanRules()

COPIED: Final[list[list[object]]] = [
    [line + 1, f"copy{line:02d}"] for line in range(SHIPPED_LEAN.duplicates_min_lines)
]
"""Twelve ``(line, hash)`` pairs both files carry: the shortest block the shipped minimum
reports, so a fixture one line shorter would report nothing."""

TWIN_SHAPE: Final[list[int]] = list(range(40))
"""One routine's normalised token shape. Two routines carrying it match at 1.00, which is
above the shipped threshold; :data:`APART_SHAPE` is the same shape with half its positions
replaced, which is below it."""

APART_SHAPE: Final[list[int]] = [*range(20), *range(900, 920)]
"""A shape sharing half its positions with :data:`TWIN_SHAPE`: no family, same block."""


def token_key(path: str, longname: str) -> str:
    """The entity-key token the index keys a routine's shape by."""
    return EntityKey(scope="routine", path=path, longname=longname).token


def token_snapshot(
    side: Side, shape: Sequence[int] = TWIN_SHAPE, linked: bool = True
) -> ProjectSnapshot:
    """One snapshot whose change has one duplicated block and, by default, one twin.

    ``shape`` is the OTHER file's routine shape and is the fixture's first moving part: moved
    below the threshold it takes the family away and leaves the duplicated block exactly as
    it was, so a test can say which of the two rules answered. A fixture that moved both at
    once would assert nothing about either.

    ``linked`` is the second, and it is about what a *check* can see rather than what a rule
    can decide. ``CheckPipeline`` hands this step a snapshot whose entity table is narrowed to
    the change's files and ONE dependency step (``analysis.narrow``), so the other file's
    entity record survives only while something links the two directly. The token index does
    not narrow, and the two rules therefore answer differently on ``linked=False`` -- which is
    a bound on the family rule, recorded by a test of its own below rather than arranged away
    here.
    """
    return ProjectSnapshot.model_validate(
        {
            "side": side,
            "languages": ["Python"],
            "file_edges": [edge(TOKEN_OTHER, TOKEN_FILE)] if linked else [],
            "entities": [
                source_file(TOKEN_FILE, CountDeclFunction=1, CountDeclClass=0, CountLineCode=40),
                source_file(TOKEN_OTHER, CountDeclFunction=1, CountDeclClass=0, CountLineCode=40),
                routine(TOKEN_FILE, "twin.normalize", CountStmt=10, CountLineCode=14),
                routine(TOKEN_OTHER, "other.normalize", CountStmt=10, CountLineCode=14),
            ],
            "tokens": {
                "vocabulary": [],
                "files": {TOKEN_FILE: COPIED, TOKEN_OTHER: COPIED},
                "routines": {
                    token_key(TOKEN_FILE, "twin.normalize"): {
                        "path": TOKEN_FILE,
                        "start": 20,
                        "end": 34,
                        "shape": list(TWIN_SHAPE),
                    },
                    token_key(TOKEN_OTHER, "other.normalize"): {
                        "path": TOKEN_OTHER,
                        "start": 20,
                        "end": 34,
                        "shape": list(shape),
                    },
                },
            },
            "arch_nodes": [{"path": "Directory Structure/src", "members": []}],
            "populations": {scope: dict(v) for scope, v in SAFE_POPULATIONS.items()},
        }
    )


class Twins(NamedTuple):
    """What the token fixture varies: the other routine's shape, and whether it is linked.

    One object rather than two parameters because this project's own gate says so: spelled
    out beside ``git_repo``, ``tmp_path``, ``settings`` and ``name`` they would put
    :func:`a_token_repository` at six parameters against a maximum of five, which is the
    finding ``a_facts_repository`` above records for the same shape of helper. They are also
    one decision: what the change's own file has in common with the other one.
    """

    shape: Sequence[int] = TWIN_SHAPE
    linked: bool = True


TWO_TWINS: Final = Twins()
"""A family of two across two linked files: what the wiring is asserted on by default."""


def a_token_repository(
    git_repo: MakeGitRepo,
    tmp_path: Path,
    settings: Settings,
    twins: Twins = TWO_TWINS,
    name: str = "tokens",
) -> Harness:
    """One restaged file whose snapshot carries the token index both rules read."""
    builder = git_repo(name)
    for path in (TOKEN_FILE, TOKEN_OTHER):
        builder.write(path, "# x\n")
    builder.stage(TOKEN_FILE, TOKEN_OTHER)
    builder.commit("initial")
    builder.write(TOKEN_FILE, "# changed\n")
    builder.stage(TOKEN_FILE)
    sides = (token_snapshot(side, twins.shape, twins.linked) for side in ("after", "before"))
    after, before = sides
    return make_harness(
        builder,
        tmp_path / name,
        settings,
        answers={"after": [after, after], "before": [before, before]},
    )


def both_token_rules(**overrides: object) -> Settings:
    """The two token rules on as warnings, with anything else the test wants moved."""
    return lean_settings(**{**dict.fromkeys(LEAN_TOKEN_RULES, "warning"), **overrides})


def token_rules_of(result: RunResult) -> list[str]:
    """Every token rule that produced a finding in this run, sorted."""
    return sorted(f.rule for f in result.findings if f.rule in TOKEN_RULES)


def test_a_run_with_a_token_index_reports_both_token_rules(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """The wiring itself: two rules, two findings, one per subject the fixture built."""
    result = a_token_repository(git_repo, tmp_path, both_token_rules()).run()

    assert token_rules_of(result) == sorted(TOKEN_RULES)


def test_each_token_finding_names_the_subject_its_rule_is_about(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """A count of two would pass with one rule reporting twice."""
    result = a_token_repository(git_repo, tmp_path, both_token_rules()).run()
    found = {f.rule: f.details for f in result.findings if f.rule in TOKEN_RULES}

    assert found[DUPLICATE_RULE]["also_at"] == [f"{TOKEN_OTHER}:1"]
    assert found[SIMILAR_RULE]["family"] == [f"other.normalize ({TOKEN_OTHER}:20)"]


def test_the_family_goes_with_the_shape_while_the_duplicated_block_stays(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """The fixture's one moving part, and what makes the test above able to fail.

    Both rules read the same index, so a step that wired one of them to the other's inputs
    would still report twice on the fixture above. Moved below the threshold, the other
    file's shape takes the family away and leaves the block untouched.
    """
    harness = a_token_repository(
        git_repo, tmp_path, both_token_rules(), Twins(shape=APART_SHAPE), "apart"
    )

    assert token_rules_of(harness.run()) == [DUPLICATE_RULE]


@pytest.mark.parametrize("rule", LEAN_TOKEN_RULES)
def test_one_token_rule_off_is_the_only_one_missing(
    git_repo: MakeGitRepo, tmp_path: Path, rule: str
) -> None:
    """One case per guard in the step, counted from the code and not from a sentence."""
    settings = both_token_rules(**{rule: None})

    harness = a_token_repository(git_repo, tmp_path, settings, TWO_TWINS, f"off-{rule}")

    assert len(token_rules_of(harness.run())) == len(TOKEN_RULES) - 1


def test_both_token_rules_off_leaves_the_run_silent_about_them(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """Requirement 9.4: an off rule costs no finding and no note, index or no index."""
    harness = a_token_repository(git_repo, tmp_path, lean_settings(), TWO_TWINS, "quiet")

    result = harness.run()

    assert token_rules_of(result) == []
    assert [note for note in harness.notes if "token" in note] == []


# --- what no count of findings can show: which rules were CALLED --------------------


def spy(calls: list[str], name: str):
    """A stand-in for one rule that records its call and finds nothing.

    An off rule producing no finding is not evidence that it did not run: the fixture could
    simply have nothing for it. Requirement 9.4 is about the *call*, and this is what sees
    one.
    """

    def called(*_args: object, **_kwargs: object) -> LeanOutcome:
        calls.append(name)
        return LeanOutcome(findings=[])

    return called


def spied(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Both token rules replaced by spies; the list records which ones were called."""
    calls: list[str] = []
    monkeypatch.setattr(lean_step, "find_duplicate_blocks", spy(calls, "duplicates"))
    monkeypatch.setattr(lean_step, "find_similar_routines", spy(calls, "similar_routines"))
    return calls


@pytest.mark.parametrize("rule", LEAN_TOKEN_RULES)
def test_the_token_rule_that_is_off_is_never_called(
    monkeypatch: pytest.MonkeyPatch, rule: str
) -> None:
    """Requirement 9.4 as a cost rather than as a silence: one rule on, one call made."""
    calls = spied(monkeypatch)

    lean_step.evaluate(
        lean_settings(**{rule: "warning"}).lean, ProjectSnapshot(side="after"), None, AffectedSet()
    )

    assert calls == [rule]


def test_neither_token_rule_is_called_while_both_are_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shipped configuration asks the token rules for nothing at all."""
    calls = spied(monkeypatch)

    lean_step.evaluate(LeanRules(), ProjectSnapshot(side="after"), None, AffectedSet())

    assert calls == []


def test_the_two_token_rules_are_called_in_the_order_the_lean_section_writes_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guard order, which a set of findings cannot show and a list of calls can.

    The spies find nothing, so this run's findings and notes are both empty and the list of
    calls is the only thing left carrying the order. The test below says the same thing from
    the other end, in the notes an operator actually reads -- two different facts, because
    one is the order the step calls in and the other the order the messages come out in.
    """
    calls = spied(monkeypatch)

    result = lean_step.evaluate(
        both_token_rules().lean, ProjectSnapshot(side="after"), None, AffectedSet()
    )

    assert calls == list(LEAN_TOKEN_RULES)
    assert result.notes == []


def test_a_run_with_no_token_index_says_so_once_per_token_rule() -> None:
    """Requirement 5.8: one message per rule, in the order the step calls them.

    Both rules say the same sentence about a snapshot that carries no index, so the pair of
    notes carries nothing but which of them was asked first.
    """
    outcome = lean_step.evaluate(
        both_token_rules().lean, ProjectSnapshot(side="after"), None, AffectedSet()
    )

    assert [note.split(" is on")[0] for note in outcome.notes] == list(TOKEN_RULES)


# --- what this wiring can see, measured rather than assumed ---------------------------


def test_a_twin_outside_the_narrowed_snapshot_is_not_named_while_its_lines_still_are() -> None:
    """The bound this wiring has, recorded so nobody has to rediscover it (req 5.3).

    ``CheckPipeline`` hands this step ``narrow(wide_after, affected | neighbourhood)``, whose
    entity table is the change's files and ONE dependency step: ``affected._neighbourhood``
    walks a single step, and ``narrow`` filters entities by ``key.path in wanted`` outright,
    its ``_one_ring`` widening only the retained *edges*. The two rules read different halves
    of that document and so reach different distances. ``duplicate_block`` reads
    ``tokens.files``, which ``narrow`` never touches, so 5.3 holds for it outright.
    ``similar_routine`` reads the whole-project ``tokens.routines`` for its shapes but takes
    each routine's ``CountStmt`` and ``EntityRef`` off ``entities``, and a routine that table
    has no record of is read as "not measured" and left out of the vertex set
    (``analysis.lean.similar._routine``). So a twin in a file the change neither touched nor
    depends on is invisible to the family rule while its *lines* are still reported.

    **The cost, measured rather than described.** Over a random sample of 60 single-file
    commits at the shipped threshold of 0.9: 41 families whole-project on this repository, 26
    still reported through the narrowed table, **15 lost outright -- 37 per cent**. So
    requirement 5.3 is not met for ``similar_routine`` today and **task 5.8 owns the fix**;
    this test is the record of what is owed, and it fails the day the contract moves.
    """
    unlinked = token_snapshot("after", TWIN_SHAPE, linked=False)
    narrowed = narrow(unlinked, [TOKEN_FILE])
    affected = AffectedSet(
        files={TOKEN_FILE},
        keys={key for key in narrowed.entities if key.path == TOKEN_FILE},
    )

    outcome = lean_step.evaluate(both_token_rules().lean, narrowed, None, affected)

    assert sorted(f.rule for f in outcome.findings) == [DUPLICATE_RULE]
    assert [f.details["also_at"] for f in outcome.findings] == [[f"{TOKEN_OTHER}:1"]]
    assert outcome.notes == []


# --- each configured number and list reaching the rule it belongs to -----------------
#
# The step maps seven `[lean]` keys onto two calls, and five of the seven travel together
# inside one `SimilarLimits`. Two keys swapped there type-check, run, and quietly change what
# the rule is about; `tests/config/test_template.py` binds each key to the SWITCH that guards
# its read, which is a different question from which PARAMETER it is handed to. So each key
# is moved to a value that changes the answer, and the rule it belongs to is the one that
# goes quiet while the other rule stays exactly as it was.

TOKEN_KEY_SILENCERS: Final[tuple[tuple[str, object, str], ...]] = (
    ("duplicates_min_lines", SHIPPED_LEAN.duplicates_min_lines + 1, DUPLICATE_RULE),
    ("duplicates_ignore", [TOKEN_OTHER], DUPLICATE_RULE),
    ("similar_min_statements", 11, SIMILAR_RULE),
    ("similar_min_family", 3, SIMILAR_RULE),
    ("similar_ignore", [TOKEN_OTHER], SIMILAR_RULE),
    ("similar_name_ignore", [r"normalize$"], SIMILAR_RULE),
)
"""One key, the value that silences its rule on this fixture, and the rule it silences.

The values are chosen against the fixture rather than picked: the block is exactly
``duplicates_min_lines`` lines long, so one more reports nothing; the routines carry ten
statements, so a floor of eleven excludes them; the family has two members, so a minimum of
three refuses it; and each list names the one file or the one name that makes the pair a
pair. A value that changed nothing would leave the case asserting that the run still works.
"""


@pytest.mark.parametrize(
    ("key", "value", "silenced"),
    TOKEN_KEY_SILENCERS,
    ids=[key for key, _, _ in TOKEN_KEY_SILENCERS],
)
def test_each_token_key_reaches_the_rule_it_belongs_to(
    git_repo: MakeGitRepo, tmp_path: Path, key: str, value: object, silenced: str
) -> None:
    """The other rule staying is half the assertion: a key wired to neither would pass alone."""
    settings = both_token_rules(**{key: value})

    harness = a_token_repository(git_repo, tmp_path, settings, TWO_TWINS, f"key-{key}")

    assert token_rules_of(harness.run()) == sorted(set(TOKEN_RULES) - {silenced})


def test_the_configured_threshold_is_the_one_the_family_rule_is_held_to(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """The seventh key, moved the other way, because this one cannot silence anything.

    ``similar_threshold`` is bounded at 1.0 and the fixture's twins match at exactly 1.00, so
    no legal value takes the default fixture's family away. Moved onto the shape that is
    half a match instead, the operator's number is what decides whether those two are a
    family at all -- and the duplicated block, which no similarity decides, stays either way.
    """
    settings = both_token_rules(similar_threshold=0.4)

    harness = a_token_repository(
        git_repo, tmp_path, settings, Twins(shape=APART_SHAPE), "threshold"
    )

    assert token_rules_of(harness.run()) == sorted(TOKEN_RULES)
