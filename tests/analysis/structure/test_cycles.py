"""New dependency cycles between files and between architecture nodes (task 4.3; req 6.1, 6.2).

Most cases build tiny synthetic edge lists, because a cycle rule is about the shape of a
graph and nothing else. Two cases run on the synthetic project of ``tests/fixtures``, where
``src/analysis/rules.py`` gains an edge back to ``src/analysis/engine.py`` and closes a file
cycle the ``before`` side does not have, while the four architecture nodes stay acyclic.
"""

from __future__ import annotations

import json

from fixtures import snapshot_fixture

from scitools_hook.analysis.structure.cycles import find_new_cycles
from scitools_hook.models.findings import Finding
from scitools_hook.models.snapshot import DepEdge

ENGINE = "src/analysis/engine.py"
RULES = "src/analysis/rules.py"


def edge(src: str, dst: str, refs: int = 1) -> DepEdge:
    """One dependency edge with a reference count."""
    return DepEdge(src=src, dst=dst, refs=refs)


def members(finding: Finding) -> list[str]:
    """The cycle members a finding lists (req 6.1)."""
    listed = finding.details["members"]
    assert isinstance(listed, list)
    return [str(item) for item in listed]


def closing_refs(finding: Finding) -> list[str]:
    """The references that close the cycle a finding reports (req 6.1)."""
    listed = finding.details["closing_refs"]
    assert isinstance(listed, list)
    return [str(item) for item in listed]


def test_a_new_three_file_cycle_is_reported_once() -> None:
    """One finding per cycle, listing every file in it and the references that close it (6.1)."""
    before = [edge("a.py", "b.py", refs=2), edge("b.py", "c.py")]
    after = [*before, edge("c.py", "a.py", refs=3)]

    findings = find_new_cycles(before, after, "error", "file")

    (finding,) = findings
    assert finding.kind == "structural"
    assert finding.rule == "structure.file_cycle"
    assert finding.scope == "file"
    assert finding.metric is None
    assert finding.entity is None
    assert finding.path == "a.py"
    assert finding.limit is None
    assert finding.limit_source == "rule"
    assert finding.severity == "error"
    assert finding.blocking is True
    assert finding.preexisting is False
    assert finding.hint == ""
    assert finding.before is None
    assert members(finding) == ["a.py", "b.py", "c.py"]
    assert closing_refs(finding) == [
        "a.py -> b.py (2 refs)",
        "b.py -> c.py (1 ref)",
        "c.py -> a.py (3 refs)",
    ]
    assert "a.py, b.py, c.py" in finding.message
    assert "did not exist before the change" in finding.message


def test_a_pre_existing_cycle_is_not_reported() -> None:
    """A cycle both sides have is not the change's doing (req 6.1)."""
    before = [edge("a.py", "b.py"), edge("b.py", "a.py")]
    after = [edge("a.py", "b.py", refs=5), edge("b.py", "a.py"), edge("a.py", "c.py")]

    assert find_new_cycles(before, after, "error", "file") == []


def test_a_pre_existing_cycle_that_shrank_is_not_reported() -> None:
    """Losing a member leaves a subset of a known cycle, which is not a new cycle (req 6.1)."""
    before = [edge("a.py", "b.py"), edge("b.py", "c.py"), edge("c.py", "a.py")]
    after = [edge("a.py", "b.py"), edge("b.py", "a.py")]

    assert find_new_cycles(before, after, "error", "file") == []


def test_a_pre_existing_cycle_that_grew_is_reported() -> None:
    """A bigger component is no subset of the old one, so the change made it worse (req 6.1)."""
    before = [edge("a.py", "b.py"), edge("b.py", "a.py")]
    after = [edge("a.py", "b.py"), edge("b.py", "c.py"), edge("c.py", "a.py")]

    (finding,) = find_new_cycles(before, after, "error", "file")

    assert members(finding) == ["a.py", "b.py", "c.py"]


def test_two_pre_existing_cycles_that_merged_are_one_new_cycle() -> None:
    """Merging two known cycles is a new, larger cycle (req 6.1).

    Each member was already inside *some* pre-change cycle, so a rule that compared the
    component against the union of the known cycles would stay silent. The rule is per
    component -- the merged component is a subset of neither -- so the change is reported.
    """
    before = [
        edge("a.py", "b.py"),
        edge("b.py", "a.py"),
        edge("c.py", "d.py"),
        edge("d.py", "c.py"),
    ]
    after = [*before, edge("b.py", "c.py"), edge("d.py", "a.py")]

    (finding,) = find_new_cycles(before, after, "error", "file")

    assert members(finding) == ["a.py", "b.py", "c.py", "d.py"]


def test_two_independent_new_cycles_are_two_findings() -> None:
    """Each component is reported on its own, in a stable order."""
    after = [
        edge("y.py", "z.py"),
        edge("z.py", "y.py"),
        edge("a.py", "b.py"),
        edge("b.py", "a.py"),
    ]

    findings = find_new_cycles([], after, "error", "file")

    assert [members(finding) for finding in findings] == [["a.py", "b.py"], ["y.py", "z.py"]]


def test_an_acyclic_change_reports_nothing() -> None:
    """A graph without a cycle produces no finding at all."""
    after = [edge("a.py", "b.py"), edge("b.py", "c.py"), edge("a.py", "c.py")]

    assert find_new_cycles([], after, "error", "file") == []


def test_a_file_that_depends_on_itself_is_not_a_cycle() -> None:
    """A self-dependency is a parse artefact, so it is deliberately not reported (req 6.1)."""
    after = [edge("a.py", "a.py", refs=3), edge("a.py", "b.py")]

    assert find_new_cycles([], after, "error", "file") == []


def test_an_architecture_cycle_names_the_nodes_and_the_closing_references() -> None:
    """The same rule at the architecture level, under its own rule name (req 6.2)."""
    cli, analysis = "Directory Structure/src/cli", "Directory Structure/src/analysis"
    before = [edge(cli, analysis, refs=3)]
    after = [*before, edge(analysis, cli, refs=2)]

    (finding,) = find_new_cycles(before, after, "error", "arch")

    assert finding.rule == "structure.arch_cycle"
    assert finding.scope == "arch"
    assert finding.path == analysis
    assert members(finding) == [analysis, cli]
    assert closing_refs(finding) == [
        f"{analysis} -> {cli} (2 refs)",
        f"{cli} -> {analysis} (3 refs)",
    ]
    assert analysis in finding.message and cli in finding.message
    assert "architecture nodes" in finding.message


def test_whole_project_mode_reports_every_cycle_as_an_inventory() -> None:
    """Without a before side every cycle is reported, and none is called new (req 4.8)."""
    after = [edge("a.py", "b.py"), edge("b.py", "a.py")]

    (finding,) = find_new_cycles(None, after, "error", "file")

    assert members(finding) == ["a.py", "b.py"]
    assert finding.preexisting is False
    assert "did not exist before the change" not in finding.message


def test_a_warning_cycle_does_not_block_the_commit() -> None:
    """Severity comes from configuration; only an error blocks (req 3.7)."""
    after = [edge("a.py", "b.py"), edge("b.py", "a.py")]

    (finding,) = find_new_cycles([], after, "warning", "file")

    assert finding.severity == "warning"
    assert finding.blocking is False


def test_closing_references_exclude_edges_that_leave_the_cycle() -> None:
    """Only the edges inside the component close it (req 6.1)."""
    after = [edge("a.py", "b.py"), edge("b.py", "a.py"), edge("a.py", "outside.py", refs=7)]

    (finding,) = find_new_cycles([], after, "error", "file")

    assert closing_refs(finding) == ["a.py -> b.py (1 ref)", "b.py -> a.py (1 ref)"]


def test_the_details_of_a_finding_survive_a_json_round_trip() -> None:
    """``details`` is part of the JSON contract, so it must reload unchanged (req 7.4)."""
    after = [edge("a.py", "b.py", refs=2), edge("b.py", "a.py")]

    (finding,) = find_new_cycles([], after, "error", "file")
    reloaded = Finding.model_validate(json.loads(finding.model_dump_json()))

    assert reloaded.details == {
        "members": ["a.py", "b.py"],
        "closing_refs": ["a.py -> b.py (2 refs)", "b.py -> a.py (1 ref)"],
    }
    assert reloaded == finding


def test_the_fixture_change_closes_a_new_file_cycle() -> None:
    """The synthetic project's change makes ``rules.py`` and ``engine.py`` mutual (req 6.1)."""
    before = snapshot_fixture("before")
    after = snapshot_fixture("after")

    (finding,) = find_new_cycles(before.file_edges, after.file_edges, "error", "file")

    assert finding.rule == "structure.file_cycle"
    assert members(finding) == [ENGINE, RULES]
    assert closing_refs(finding) == [
        f"{ENGINE} -> {RULES} (4 refs)",
        f"{RULES} -> {ENGINE} (2 refs)",
    ]
    assert finding.path == ENGINE


def test_the_fixture_before_side_holds_no_file_cycle() -> None:
    """The cycle really is new: the ``before`` graph on its own is acyclic (req 6.1)."""
    before = snapshot_fixture("before")

    assert find_new_cycles(None, before.file_edges, "error", "file") == []


def test_the_fixture_architecture_edges_stay_acyclic() -> None:
    """The change adds ``cli -> understand`` but closes no architecture cycle (req 6.2)."""
    before = snapshot_fixture("before")
    after = snapshot_fixture("after")

    assert find_new_cycles(before.arch_edges, after.arch_edges, "error", "arch") == []
