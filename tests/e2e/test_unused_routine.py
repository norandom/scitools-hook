"""A change that leaves a routine nothing calls, reported end to end (requirement 6).

The rule is a warning, and that is the property this module is really about: the run says
what it found, names the routine, and **exits 0**. A gate that blocked on dead code would be
turned off within a week, because reference-based detection cannot see an entry point named
in packaging metadata or a handler a decorator registers.

The seam supplies the reference measurement the way a real extraction would, so what is
exercised here is everything above it: the configuration key, the feature record the check
reads before it starts, the rule, the ignore list and the exit code.
"""

from __future__ import annotations

from pathlib import Path

from e2e.harness import FIXED, OTHER, Workspace, make_workspace, report
from scitools_hook.exit_codes import ExitCode
from scitools_hook.models.understand import Availability, Feature, FeatureReport
from scitools_hook.understand.fake import FIXTURE_VERSION
from scitools_hook.understand.features import FEATURES_FILE

RULE = "structure.unused_routine"

UNREFERENCED = "pkg.other.scan"
"""The routine the ``fixed`` snapshot marks as referenced by nothing.

That fixture rather than ``violating``: this module is about a run that reports a warning
and **exits 0**, and the violating snapshot carries blocking findings by construction.
"""

UNUSED = "def scan(items):\n    return [item for item in items if item]\n"
"""An edit to the routine the fixture snapshot marks as referenced by nothing."""

ASKS_FOR_IT = '[structure]\nunused_routines = "warning"\n'
"""The key that turns the rule on; it ships off, like every feature of this specification."""

EXCUSED = ASKS_FOR_IT + 'unused_ignore = ["\\\\.scan$"]\n'
"""The same, with a pattern that excuses the very routine the rule would otherwise report."""


def a_workspace(tmp_path: Path) -> Workspace:
    """A repository whose seam answers from the clean fixture, so nothing else blocks."""
    return make_workspace(tmp_path, **{"SCITOOLS_HOOK_FAKE_UNDERSTAND": str(FIXED)})


def enabled(workspace: Workspace, configuration: str = ASKS_FOR_IT) -> None:
    """Record what the build offers, then turn the rule on -- the operator's own order.

    The record is written here rather than by ``scitools-hook doctor`` because the fixture
    seam refuses to probe: it runs no Understand at all, and a probe answering from fixtures
    would be measuring the fixtures. On a real install ``doctor`` writes exactly this file.
    """
    cache = Path(workspace.cli("db", "path").stdout.strip()).parent
    cache.mkdir(parents=True, exist_ok=True)
    measured = FeatureReport(
        build=FIXTURE_VERSION,
        features={
            feature: Availability(state="available", detail="recorded by the test")
            for feature in Feature
        },
    )
    (cache / FEATURES_FILE).write_text(measured.model_dump_json(indent=2), encoding="utf-8")
    workspace.write("scitools-hook.toml", configuration)


def checked(workspace: Workspace):
    """One ``check --worktree`` over an edit to the routine nothing references."""
    workspace.write(OTHER, UNUSED)
    return workspace.cli("check", "--worktree", "--format", "json")


def rules_of(document: object) -> list[str]:
    """The rule names of the findings a run reported."""
    findings = document["findings"]  # type: ignore[index]
    assert isinstance(findings, list)
    return [str(finding["rule"]) for finding in findings]


def test_an_affected_routine_nothing_references_is_reported_as_a_warning(
    tmp_path: Path,
) -> None:
    """Requirements 6.1 and 6.3, and the exit code is the point of the test."""
    workspace = a_workspace(tmp_path)
    enabled(workspace)

    done = checked(workspace)

    assert done.returncode == int(ExitCode.OK), done.stdout + done.stderr
    document = report(done)
    assert RULE in rules_of(document), rules_of(document)
    found = [one for one in document["findings"] if one["rule"] == RULE]  # type: ignore[index]
    assert found[0]["severity"] == "warning"
    assert found[0]["blocking"] is False
    assert UNREFERENCED in str(found[0]["message"])


def test_the_ignore_list_excuses_it(tmp_path: Path) -> None:
    """Requirement 6.3: the operator names the shapes their project reaches another way."""
    workspace = a_workspace(tmp_path)
    enabled(workspace, EXCUSED)

    done = checked(workspace)

    assert done.returncode == int(ExitCode.OK), done.stdout + done.stderr
    assert RULE not in rules_of(report(done))


def test_the_rule_ships_off(tmp_path: Path) -> None:
    """Requirement 1.3: an untouched repository meets none of this."""
    done = checked(a_workspace(tmp_path))

    assert done.returncode == int(ExitCode.OK), done.stdout + done.stderr
    assert RULE not in rules_of(report(done))


def test_the_human_report_names_the_routine_and_says_what_to_do(
    tmp_path: Path,
) -> None:
    """The hint is the half an agent acts on; a finding without one is a complaint."""
    workspace = a_workspace(tmp_path)
    enabled(workspace)
    workspace.write(OTHER, UNUSED)

    done = workspace.cli("check", "--worktree")

    assert done.returncode == int(ExitCode.OK), done.stderr
    assert "unused_routine" in done.stdout
    assert "unused_ignore" in done.stdout, "the hint names the way out"
