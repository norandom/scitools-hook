"""New dependencies per file and reference limits between architecture nodes (task 4.4).

Two coupling rules live in one module because they answer the same question at two levels.
:func:`new_dependencies` (req 6.5) counts how many dependencies a file gained in this change
— the rule that catches a change wiring one file to many new files while every other metric
stays inside its limit. :func:`evaluate_coupling` (req 6.6) sums the references between a
configured pair of architecture nodes and reports a pair that is now over its maximum.

Both are ``>`` rules: the tests below pin that a file at exactly the limit and a node pair at
exactly its maximum stay silent, and that only the affected files and the configured
direction are counted.
"""

from __future__ import annotations

import json
from typing import Final

import pytest
from fixtures import snapshot_fixture

from scitools_hook.analysis.structure.coupling import evaluate_coupling, new_dependencies
from scitools_hook.config.models import CouplingRule, Severity
from scitools_hook.models.findings import Finding
from scitools_hook.models.snapshot import DepEdge

APP: Final = "src/cli/app.py"
ENGINE: Final = "src/analysis/engine.py"
RULES: Final = "src/analysis/rules.py"
ADAPTER: Final = "src/understand/adapter.py"
TEXT: Final = "src/util/text.py"

CLI_NODE: Final = "Directory Structure/src/cli"
ANALYSIS_NODE: Final = "Directory Structure/src/analysis"
UNDERSTAND_NODE: Final = "Directory Structure/src/understand"
UTIL_NODE: Final = "Directory Structure/src/util"


def edge(src: str, dst: str, refs: int = 1) -> DepEdge:
    """One dependency edge with a reference count."""
    return DepEdge(src=src, dst=dst, refs=refs)


def deps(src: str, count: int, prefix: str = "src/new") -> list[DepEdge]:
    """``count`` distinct dependencies of ``src``, named so that their order is obvious."""
    return [edge(src, f"{prefix}/dep{index}.py") for index in range(count)]


# --- new dependencies per file (req 6.5) ----------------------------------------


def test_a_file_that_gains_six_dependencies_is_flagged_at_a_limit_of_five() -> None:
    """The rule of requirement 6.5: too many new dependencies in one change."""
    before = [edge(APP, ENGINE)]
    after = [edge(APP, ENGINE), *deps(APP, 6)]

    (finding,) = new_dependencies(before, after, {APP}, 5)

    assert finding.kind == "structural"
    assert finding.rule == "structure.new_dependencies"
    assert finding.scope == "file"
    assert finding.metric is None
    assert finding.entity is None
    assert finding.path == APP
    assert finding.value == 6
    assert finding.before is None
    assert finding.limit == 5
    assert finding.limit_source == "rule"
    assert finding.severity == "error"
    assert finding.blocking is True
    assert finding.preexisting is False
    assert finding.hint == ""
    assert finding.details == {"new_dependencies": [f"src/new/dep{index}.py" for index in range(6)]}
    assert APP in finding.message
    assert "6" in finding.message and "5" in finding.message


def test_a_file_that_gains_exactly_the_limit_is_silent() -> None:
    """``>`` and not ``>=``: five new dependencies under a limit of five are allowed."""
    after = deps(APP, 5)

    assert new_dependencies([], after, {APP}, 5) == []


def test_dependencies_the_file_already_had_do_not_count() -> None:
    """Only what the change added is new; the rest is the file as it was (req 6.5)."""
    before = deps(APP, 6)
    after = [*deps(APP, 6), edge(APP, ENGINE)]

    assert new_dependencies(before, after, {APP}, 5) == []


def test_only_the_files_of_the_affected_set_are_counted() -> None:
    """``files`` is the affected set; another file's new dependencies are not this change."""
    after = [*deps(APP, 6), *deps(ADAPTER, 9, prefix="src/other")]

    findings = new_dependencies([], after, {APP}, 5)

    assert [finding.path for finding in findings] == [APP]


def test_removing_dependencies_is_never_a_finding() -> None:
    """A file that lost dependencies gained none, whatever the limit is."""
    before = deps(APP, 9)
    after = deps(APP, 2)

    assert new_dependencies(before, after, {APP}, 0) == []


def test_a_repeated_edge_counts_as_one_dependency() -> None:
    """Understand reports one edge per reference kind; the rule counts distinct files."""
    after = [edge(APP, ENGINE), edge(APP, ENGINE, refs=4), edge(APP, RULES)]

    assert new_dependencies([], after, {APP}, 2) == []


def test_a_new_self_dependency_is_not_a_new_dependency() -> None:
    """A file referencing its own contents is a parse artefact, as it is for cycles (6.1)."""
    after = [edge(APP, APP, refs=3)]

    assert new_dependencies([], after, {APP}, 0) == []


def test_without_a_before_side_every_dependency_is_new() -> None:
    """Whole-project mode has no before side, so the whole dependency set is the count (4.8)."""
    after = deps(APP, 3)

    (finding,) = new_dependencies(None, after, {APP}, 2)

    assert finding.value == 3
    assert finding.details["new_dependencies"] == [
        "src/new/dep0.py",
        "src/new/dep1.py",
        "src/new/dep2.py",
    ]


def test_findings_are_sorted_by_file() -> None:
    """Deterministic output whatever order the affected set iterates in."""
    after = [*deps(APP, 3), *deps(ENGINE, 3, prefix="src/other")]

    findings = new_dependencies([], after, {ENGINE, APP}, 2)

    assert [finding.path for finding in findings] == [ENGINE, APP]


@pytest.mark.parametrize(("severity", "blocking"), [("error", True), ("warning", False)])
def test_the_configured_severity_decides_whether_new_dependencies_block(
    severity: Severity, blocking: bool
) -> None:
    """``structure.new_dependencies_severity``; only an error blocks a commit (req 3.7)."""
    after = deps(APP, 3)

    (finding,) = new_dependencies([], after, {APP}, 2, severity)

    assert finding.severity == severity
    assert finding.blocking is blocking


def test_the_details_of_a_new_dependency_finding_survive_a_json_round_trip() -> None:
    """``details`` is part of the JSON output contract, so it must reload unchanged (7.4)."""
    after = [edge(APP, ENGINE), edge(APP, RULES)]

    (finding,) = new_dependencies([], after, {APP}, 1)

    reloaded = Finding.model_validate(json.loads(finding.model_dump_json()))
    assert reloaded == finding
    assert reloaded.details == {"new_dependencies": [ENGINE, RULES]}


def test_the_fixture_change_wires_the_cli_entry_point_to_two_new_files() -> None:
    """``src/cli/app.py`` gains ``rules.py`` and ``adapter.py`` in the synthetic change (6.5)."""
    before = snapshot_fixture("before")
    after = snapshot_fixture("after")

    (finding,) = new_dependencies(before.file_edges, after.file_edges, {APP}, 1)

    assert finding.path == APP
    assert finding.value == 2
    assert finding.details == {"new_dependencies": [RULES, ADAPTER]}


# --- references between architecture nodes (req 6.6) ----------------------------


def test_a_node_pair_over_its_reference_limit_is_flagged() -> None:
    """The rule of requirement 6.6: too many references between two architecture nodes."""
    rule = CouplingRule(from_node=CLI_NODE, to_node=ANALYSIS_NODE, max_refs=3)
    after = [edge(CLI_NODE, ANALYSIS_NODE, refs=4)]

    (finding,) = evaluate_coupling(after, [rule])

    assert finding.kind == "structural"
    assert finding.rule == "structure.coupling"
    assert finding.scope == "arch"
    assert finding.metric is None
    assert finding.entity is None
    assert finding.path == CLI_NODE
    assert finding.value == 4
    assert finding.before is None
    assert finding.limit == 3
    assert finding.limit_source == "rule"
    assert finding.severity == "error"
    assert finding.blocking is True
    assert finding.hint == ""
    assert finding.details == {"from_node": CLI_NODE, "to_node": ANALYSIS_NODE}
    assert CLI_NODE in finding.message
    assert ANALYSIS_NODE in finding.message


def test_a_node_pair_exactly_at_its_reference_limit_is_silent() -> None:
    """``>`` and not ``>=``: the maximum itself is allowed."""
    rule = CouplingRule(from_node=CLI_NODE, to_node=ANALYSIS_NODE, max_refs=4)
    after = [edge(CLI_NODE, ANALYSIS_NODE, refs=4)]

    assert evaluate_coupling(after, [rule]) == []


def test_references_of_a_pair_are_summed_across_edges() -> None:
    """Understand reports one edge per reference kind, so the rule adds them up (req 6.6)."""
    rule = CouplingRule(from_node=CLI_NODE, to_node=ANALYSIS_NODE, max_refs=4)
    after = [edge(CLI_NODE, ANALYSIS_NODE, refs=3), edge(CLI_NODE, ANALYSIS_NODE, refs=2)]

    (finding,) = evaluate_coupling(after, [rule])

    assert finding.value == 5


def test_the_opposite_direction_is_a_different_pair() -> None:
    """A coupling rule constrains ``from_node -> to_node``, not the traffic coming back."""
    rule = CouplingRule(from_node=CLI_NODE, to_node=ANALYSIS_NODE, max_refs=1)
    after = [edge(ANALYSIS_NODE, CLI_NODE, refs=9)]

    assert evaluate_coupling(after, [rule]) == []


def test_edges_between_other_nodes_do_not_count() -> None:
    """Only the pair the rule names is summed (req 6.6)."""
    rule = CouplingRule(from_node=CLI_NODE, to_node=ANALYSIS_NODE, max_refs=1)
    after = [edge(CLI_NODE, UTIL_NODE, refs=9), edge(UNDERSTAND_NODE, ANALYSIS_NODE, refs=9)]

    assert evaluate_coupling(after, [rule]) == []


def test_a_pair_with_no_edge_at_all_is_silent() -> None:
    """Zero references never exceed a maximum, so an unused pair says nothing."""
    rule = CouplingRule(from_node=CLI_NODE, to_node=ANALYSIS_NODE, max_refs=0)

    assert evaluate_coupling([], [rule]) == []


def test_coupling_findings_are_sorted_by_the_pair_they_report() -> None:
    """Deterministic output whatever order the rules are configured in."""
    rules = [
        CouplingRule(from_node=UNDERSTAND_NODE, to_node=UTIL_NODE, max_refs=0),
        CouplingRule(from_node=CLI_NODE, to_node=ANALYSIS_NODE, max_refs=0),
    ]
    after = [edge(UNDERSTAND_NODE, UTIL_NODE), edge(CLI_NODE, ANALYSIS_NODE)]

    findings = evaluate_coupling(after, rules)

    assert [finding.path for finding in findings] == [CLI_NODE, UNDERSTAND_NODE]


@pytest.mark.parametrize(("severity", "blocking"), [("error", True), ("warning", False)])
def test_the_severity_of_the_coupling_rule_decides_whether_it_blocks(
    severity: Severity, blocking: bool
) -> None:
    """Only an ``error`` blocks a commit (req 3.7)."""
    rule = CouplingRule(from_node=CLI_NODE, to_node=ANALYSIS_NODE, max_refs=0, severity=severity)
    after = [edge(CLI_NODE, ANALYSIS_NODE)]

    (finding,) = evaluate_coupling(after, [rule])

    assert finding.severity == severity
    assert finding.blocking is blocking


def test_the_details_of_a_coupling_finding_survive_a_json_round_trip() -> None:
    """``details`` is part of the JSON output contract, so it must reload unchanged (7.4)."""
    rule = CouplingRule(from_node=CLI_NODE, to_node=UTIL_NODE, max_refs=1)
    after = [edge(CLI_NODE, UTIL_NODE, refs=2)]

    (finding,) = evaluate_coupling(after, [rule])
    reloaded = Finding.model_validate(json.loads(finding.model_dump_json()))

    assert reloaded == finding
    assert reloaded.details == {"from_node": CLI_NODE, "to_node": UTIL_NODE}


def test_the_fixture_change_pushes_the_cli_analysis_pair_over_its_limit() -> None:
    """``cli -> analysis`` grows from three references to four in the synthetic change (6.6)."""
    after = snapshot_fixture("after")
    rule = CouplingRule(from_node=CLI_NODE, to_node=ANALYSIS_NODE, max_refs=3)

    (finding,) = evaluate_coupling(after.arch_edges, [rule])

    assert finding.value == 4
    assert finding.limit == 3
    assert finding.path == CLI_NODE


def test_the_fixture_before_side_stays_inside_the_same_limit() -> None:
    """Three references before the change is exactly the maximum, so nothing was reported."""
    before = snapshot_fixture("before")
    rule = CouplingRule(from_node=CLI_NODE, to_node=ANALYSIS_NODE, max_refs=3)

    assert evaluate_coupling(before.arch_edges, [rule]) == []
