"""Layer rules over the new dependency edges of a change (task 4.4; req 6.3).

A layer rule is about *direction*: it names one architecture node and the nodes that node is
allowed to depend on. Most cases here are synthetic — a handful of edges and a dictionary
standing in for the architecture — because the rule is nothing but a lookup of two nodes and
a membership test. The headline case runs on the synthetic project of ``tests/fixtures``,
whose change adds ``src/cli/app.py -> src/understand/adapter.py``: an edge from the ``cli``
node into the ``understand`` node, which the layer rule below does not allow.

The three ways this rule can be got wrong each have a test that fails when it is: judging an
edge that was already there before the change, ignoring ``may_depend_on`` altogether, and
treating a growing reference count on an existing edge as a new edge.
"""

from __future__ import annotations

import json
from typing import Final

import pytest
from fixtures import snapshot_fixture

from scitools_hook.analysis.structure.layers import evaluate_layers
from scitools_hook.config.models import LayerRule, Severity
from scitools_hook.models.findings import Finding
from scitools_hook.models.snapshot import DepEdge

CLI: Final = "Directory Structure/src/cli"
ANALYSIS: Final = "Directory Structure/src/analysis"
UNDERSTAND: Final = "Directory Structure/src/understand"
UTIL: Final = "Directory Structure/src/util"

APP: Final = "src/cli/app.py"
ENGINE: Final = "src/analysis/engine.py"
RULES: Final = "src/analysis/rules.py"
ADAPTER: Final = "src/understand/adapter.py"
TEXT: Final = "src/util/text.py"

NODES: Final[dict[str, str]] = {
    APP: CLI,
    "src/cli/options.py": CLI,
    ENGINE: ANALYSIS,
    RULES: ANALYSIS,
    ADAPTER: UNDERSTAND,
    TEXT: UTIL,
    "src/util/legacy.py": UTIL,
}

CLI_RULE: Final = LayerRule(name="cli-through-analysis", node=CLI, may_depend_on=[ANALYSIS, UTIL])


def node_of(path: str) -> str | None:
    """The architecture node of a file of the synthetic project; ``None`` for anything else."""
    return NODES.get(path)


def edge(src: str, dst: str, refs: int = 1) -> DepEdge:
    """One dependency edge with a reference count."""
    return DepEdge(src=src, dst=dst, refs=refs)


def test_a_forbidden_new_edge_names_both_entities_the_nodes_and_the_rule() -> None:
    """Requirement 6.3 asks for the source entity, the target entity and the rule violated."""
    after = [edge(APP, ADAPTER, refs=2)]

    (finding,) = evaluate_layers(after, [], node_of, [CLI_RULE])

    assert finding.kind == "structural"
    assert finding.rule == "structure.layer"
    assert finding.scope == "file"
    assert finding.metric is None
    assert finding.entity is None
    assert finding.path == APP
    assert finding.value is None
    assert finding.before is None
    assert finding.limit is None
    assert finding.limit_source == "rule"
    assert finding.severity == "error"
    assert finding.blocking is True
    assert finding.preexisting is False
    assert finding.hint == ""
    assert finding.details == {
        "rule_name": "cli-through-analysis",
        "from_node": CLI,
        "to_node": UNDERSTAND,
        "src": APP,
        "dst": ADAPTER,
    }
    assert APP in finding.message
    assert ADAPTER in finding.message
    assert CLI in finding.message
    assert UNDERSTAND in finding.message
    assert "cli-through-analysis" in finding.message


def test_a_new_edge_to_an_allowed_node_is_silent() -> None:
    """The target node is listed in ``may_depend_on``, so the edge is exactly what is wanted."""
    after = [edge(APP, ENGINE), edge(APP, TEXT)]

    assert evaluate_layers(after, [], node_of, [CLI_RULE]) == []


def test_a_forbidden_edge_that_already_existed_is_silent() -> None:
    """A rule judges what the change did; an edge both sides have is not this change's doing."""
    before = [edge(APP, ADAPTER)]
    after = [edge(APP, ADAPTER)]

    assert evaluate_layers(after, before, node_of, [CLI_RULE]) == []


def test_more_references_on_an_existing_forbidden_edge_are_not_a_new_edge() -> None:
    """Newness is a property of the pair, not of its reference count (req 6.3)."""
    before = [edge(APP, ADAPTER, refs=1)]
    after = [edge(APP, ADAPTER, refs=9)]

    assert evaluate_layers(after, before, node_of, [CLI_RULE]) == []


def test_a_source_whose_node_has_no_rule_is_silent() -> None:
    """Only the nodes a rule names are constrained; everything else may depend as it likes."""
    after = [edge(ENGINE, ADAPTER), edge(TEXT, ADAPTER)]

    assert evaluate_layers(after, [], node_of, [CLI_RULE]) == []


def test_an_edge_inside_the_ruled_node_is_allowed() -> None:
    """A node always depends on itself; a layer rule is about crossing into another node."""
    after = [edge(APP, "src/cli/options.py")]

    assert evaluate_layers(after, [], node_of, [CLI_RULE]) == []


def test_a_source_outside_every_architecture_node_is_silent() -> None:
    """``node_of`` returning ``None`` means the file is in no node, so no rule can name it."""
    after = [edge("scripts/tool.py", ADAPTER)]

    assert evaluate_layers(after, [], node_of, [CLI_RULE]) == []


def test_a_target_outside_every_architecture_node_is_silent() -> None:
    """A rule allows *nodes*; a target that is in no node cannot be judged against that list."""
    after = [edge(APP, "scripts/tool.py")]

    assert evaluate_layers(after, [], node_of, [CLI_RULE]) == []


def test_a_rule_with_an_empty_allow_list_forbids_every_crossing_edge() -> None:
    """An isolated node: nothing outside it may be depended on (req 6.3)."""
    isolated = LayerRule(name="cli-alone", node=CLI, may_depend_on=[])
    after = [edge(APP, ENGINE)]

    (finding,) = evaluate_layers(after, [], node_of, [isolated])

    assert finding.details["to_node"] == ANALYSIS


def test_every_rule_is_enforced_on_its_own_node() -> None:
    """Two rules, two constrained nodes, one finding each, in a stable order (req 6.3)."""
    understand_rule = LayerRule(name="understand-leaf", node=UNDERSTAND, may_depend_on=[])
    after = [edge(ADAPTER, TEXT), edge(APP, ADAPTER)]

    findings = evaluate_layers(after, [], node_of, [CLI_RULE, understand_rule])

    assert [(finding.details["src"], finding.details["dst"]) for finding in findings] == [
        (APP, ADAPTER),
        (ADAPTER, TEXT),
    ]
    assert [finding.details["rule_name"] for finding in findings] == [
        "cli-through-analysis",
        "understand-leaf",
    ]


def test_two_rules_naming_one_node_are_both_enforced() -> None:
    """A node may carry several rules; an edge that breaks both is reported once per rule."""
    strict = LayerRule(name="cli-alone", node=CLI, may_depend_on=[])
    partial = LayerRule(name="cli-through-analysis", node=CLI, may_depend_on=[ANALYSIS])
    after = [edge(APP, ADAPTER)]

    findings = evaluate_layers(after, [], node_of, [strict, partial])

    assert [finding.details["rule_name"] for finding in findings] == [
        "cli-alone",
        "cli-through-analysis",
    ]


def test_an_edge_is_reported_only_for_the_rules_it_breaks() -> None:
    """The same edge satisfies one rule of the node and breaks the other (req 6.3)."""
    strict = LayerRule(name="cli-alone", node=CLI, may_depend_on=[])
    partial = LayerRule(name="cli-through-analysis", node=CLI, may_depend_on=[ANALYSIS])
    after = [edge(APP, ENGINE)]

    findings = evaluate_layers(after, [], node_of, [strict, partial])

    assert [finding.details["rule_name"] for finding in findings] == ["cli-alone"]


def test_findings_are_sorted_by_the_edge_they_report() -> None:
    """Deterministic output whatever order the edges arrive in."""
    after = [edge(APP, "src/util/legacy.py"), edge(APP, ADAPTER)]
    strict = LayerRule(name="cli-alone", node=CLI, may_depend_on=[])

    findings = evaluate_layers(after, [], node_of, [strict])

    assert [finding.details["dst"] for finding in findings] == [ADAPTER, "src/util/legacy.py"]


def test_without_a_before_side_every_edge_is_judged() -> None:
    """Whole-project mode has no before side, so every forbidden edge is reported (req 4.8)."""
    after = [edge(APP, ADAPTER), edge(APP, ENGINE)]

    (finding,) = evaluate_layers(after, None, node_of, [CLI_RULE])

    assert finding.details["dst"] == ADAPTER


def test_no_rules_means_no_findings() -> None:
    """A repository that configures no layers gets no layer findings, whatever it depends on."""
    after = [edge(APP, ADAPTER), edge(ADAPTER, APP)]

    assert evaluate_layers(after, [], node_of, []) == []


@pytest.mark.parametrize(("severity", "blocking"), [("error", True), ("warning", False)])
def test_the_severity_of_the_rule_decides_whether_it_blocks(
    severity: Severity, blocking: bool
) -> None:
    """Only an ``error`` blocks a commit (req 3.7)."""
    rule = LayerRule(name="cli-alone", node=CLI, may_depend_on=[], severity=severity)
    after = [edge(APP, ADAPTER)]

    (finding,) = evaluate_layers(after, [], node_of, [rule])

    assert finding.severity == severity
    assert finding.blocking is blocking


def test_the_details_of_a_finding_survive_a_json_round_trip() -> None:
    """``details`` is part of the JSON output contract, so it must reload unchanged (req 7.4)."""
    after = [edge(APP, ADAPTER)]

    (finding,) = evaluate_layers(after, [], node_of, [CLI_RULE])
    reloaded = Finding.model_validate(json.loads(finding.model_dump_json()))

    assert reloaded == finding
    assert reloaded.details["from_node"] == CLI


def test_the_fixture_change_violates_the_cli_layer_rule() -> None:
    """``cli/app.py`` reaches into ``understand/adapter.py``: one new forbidden edge (req 6.3)."""
    before = snapshot_fixture("before")
    after = snapshot_fixture("after")

    findings = evaluate_layers(after.file_edges, before.file_edges, fixture_node_of, [CLI_RULE])

    (finding,) = findings
    assert finding.details["src"] == APP
    assert finding.details["dst"] == ADAPTER
    assert finding.details["from_node"] == CLI
    assert finding.details["to_node"] == UNDERSTAND
    assert finding.path == APP


def test_the_fixture_before_side_holds_no_layer_violation() -> None:
    """The violation really is new: the ``before`` graph on its own satisfies the rule."""
    before = snapshot_fixture("before")

    assert evaluate_layers(before.file_edges, None, fixture_node_of, [CLI_RULE]) == []


def fixture_node_of(path: str) -> str | None:
    """The depth-2 ``Directory Structure`` node of a fixture file, as the extractor reports it."""
    directory, _, _ = path.rpartition("/")
    return f"Directory Structure/{directory}" if directory else None
