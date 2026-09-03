"""The two call-graph rules: reachable complexity and routine cycles.

The property that matters most here is **what the rules refuse to say**. Understand cannot
follow a Python call through an instance attribute, so a call graph read as complete answers
"reaches nothing" for code that reaches a great deal; the tests below therefore spend as much
effort on the silences -- a routine the graph does not hold, a reach that passes through a file
that failed to parse, a reached routine with no complexity value -- as on the arithmetic.

Snapshots are built by hand rather than taken from the shared fixture wherever a test needs a
shape the fixture does not have, because a rule that never meets a hole cannot be shown to
report one.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

import pytest
from fixtures import snapshot_fixture

from scitools_hook.analysis.structure.calls import (
    CallGraph,
    evaluate_reachable_complexity,
    find_call_cycles,
)
from scitools_hook.config.models import Limit
from scitools_hook.models.snapshot import (
    CallNode,
    CallResolution,
    DepEdge,
    EntityKey,
    EntityRecord,
    EntityRef,
    ParseError,
    ProjectSnapshot,
)

PATH = "src/pkg/module.py"
OTHER = "src/pkg/other.py"


def key(name: str, path: str = PATH) -> EntityKey:
    """A routine key of ``name``; the parameter list is empty and therefore never a tie-break."""
    return EntityKey(scope="routine", path=path, longname=name, parameters="")


def record(name: str, path: str = PATH, language: str = "Python") -> EntityRecord:
    """The entity record a snapshot holds for a routine, so findings can carry its line."""
    return EntityRecord(
        ref=EntityRef(key=key(name, path), kind="python Function", name=name, line=7),
        language=language,
    )


def edge(src: str, dst: str, refs: int = 1, path: str = PATH, to: str | None = None) -> DepEdge:
    """One call edge between two routine key tokens."""
    return DepEdge(src=key(src, path).token, dst=key(dst, to or path).token, refs=refs)


def node(name: str, complexity: float | None, unresolved: int = 0, path: str = PATH) -> CallNode:
    """One call-graph node: its endpoint, its own complexity, its unbound call sites."""
    return CallNode(node=key(name, path).token, complexity=complexity, unresolved_calls=unresolved)


def snapshot(
    nodes: Iterable[CallNode],
    edges: Iterable[DepEdge] = (),
    records: Iterable[EntityRecord] = (),
    resolution: Mapping[str, CallResolution] | None = None,
    parse_errors: Sequence[ParseError] = (),
    languages: Sequence[str] = ("Python",),
) -> ProjectSnapshot:
    """A snapshot carrying nothing but a call graph, which is all these rules read."""
    return ProjectSnapshot(
        side="after",
        languages=list(languages),
        entities={item.key: item for item in records},
        call_edges=list(edges),
        call_nodes=list(nodes),
        call_resolution=(
            {"Python": CallResolution(resolved=9, unresolved=1)}
            if resolution is None
            else dict(resolution)
        ),
        parse_errors=list(parse_errors),
    )


def chain() -> ProjectSnapshot:
    """``top`` -> ``mid`` -> ``leaf``, complexities 1, 4 and 10, all three held and measured."""
    return snapshot(
        nodes=[node("top", 1.0), node("mid", 4.0), node("leaf", 10.0)],
        edges=[edge("top", "mid"), edge("mid", "leaf")],
        records=[record("top"), record("mid"), record("leaf")],
    )


# --- reachable complexity: the arithmetic ---------------------------------------


def test_reach_sums_the_complexity_of_everything_transitively_called() -> None:
    findings = evaluate_reachable_complexity(chain(), [key("top")], Limit(max=10))
    assert [finding.value for finding in findings] == [15.0]
    assert findings[0].rule == "structure.reachable_complexity"
    assert findings[0].metric == "CyclomaticStrict"


def test_reach_includes_the_routine_itself() -> None:
    lone = snapshot(nodes=[node("only", 12.0)], records=[record("only")])
    findings = evaluate_reachable_complexity(lone, [key("only")], Limit(max=10))
    assert [finding.value for finding in findings] == [12.0]
    assert findings[0].details["reached_routines"] == 1


def test_a_routine_exactly_at_the_limit_is_not_reported() -> None:
    assert evaluate_reachable_complexity(chain(), [key("top")], Limit(max=15)) == []


def test_a_routine_under_the_limit_is_not_reported() -> None:
    assert evaluate_reachable_complexity(chain(), [key("mid")], Limit(max=14)) == []


def test_a_diamond_counts_the_shared_routine_once() -> None:
    diamond = snapshot(
        nodes=[node(name, 5.0) for name in ("top", "left", "right", "shared")],
        edges=[
            edge("top", "left"),
            edge("top", "right"),
            edge("left", "shared"),
            edge("right", "shared"),
        ],
        records=[record(name) for name in ("top", "left", "right", "shared")],
    )
    findings = evaluate_reachable_complexity(diamond, [key("top")], Limit(max=1))
    assert findings[0].value == 20.0
    assert findings[0].details["reached_routines"] == 4


def test_a_cycle_in_the_reach_does_not_hang_the_walk() -> None:
    looped = snapshot(
        nodes=[node("a", 3.0), node("b", 4.0)],
        edges=[edge("a", "b"), edge("b", "a")],
        records=[record("a"), record("b")],
    )
    findings = evaluate_reachable_complexity(looped, [key("a")], Limit(max=1))
    assert findings[0].value == 7.0


def test_the_finding_names_what_the_routine_reaches_by_qualified_name() -> None:
    findings = evaluate_reachable_complexity(chain(), [key("top")], Limit(max=1))
    assert findings[0].details["reaches"] == ["leaf", "mid"]


def test_the_finding_carries_the_entity_and_its_line() -> None:
    findings = evaluate_reachable_complexity(chain(), [key("top")], Limit(max=1))
    assert findings[0].entity is not None
    assert findings[0].entity.name == "top"
    assert findings[0].line == 7
    assert findings[0].path == PATH


def test_findings_come_back_in_key_order() -> None:
    findings = evaluate_reachable_complexity(
        chain(), [key("top"), key("mid"), key("leaf")], Limit(max=1)
    )
    assert [finding.entity.name for finding in findings if finding.entity] == [
        "leaf",
        "mid",
        "top",
    ]


# --- reachable complexity: the silences -----------------------------------------


def test_a_routine_the_graph_does_not_hold_is_not_judged_at_all() -> None:
    """The bound on ``call_edges`` must read as "not looked at", never as "reaches nothing".

    The maximum is **negative** on purpose. A routine the graph does not hold has a reach of
    zero, so against any maximum of zero or more this test would pass whether the guard was
    there or not -- the failure mode would be unreachable and the test would prove nothing.
    Below zero, an unguarded rule reports "reaches 0 routines totalling 0", which is exactly
    the fictional clean answer the guard exists to prevent.
    """
    bounded = snapshot(nodes=[node("held", 1.0)], records=[record("held"), record("outside")])
    assert evaluate_reachable_complexity(bounded, [key("outside")], Limit(max=-1.0)) == []
    assert evaluate_reachable_complexity(bounded, [key("held")], Limit(max=-1.0)) != []


def test_a_snapshot_with_no_call_graph_judges_nothing() -> None:
    """An extraction that carried no call graph -- ``include_edges = False`` -- judges nobody."""
    empty = ProjectSnapshot(side="after", entities={key("top"): record("top")})
    assert evaluate_reachable_complexity(empty, [key("top")], Limit(max=-1.0)) == []


def test_an_unmeasured_routine_is_named_rather_than_counted_as_free() -> None:
    partial = snapshot(
        nodes=[node("top", 20.0), node("mid", None)],
        edges=[edge("top", "mid")],
        records=[record("top"), record("mid")],
    )
    findings = evaluate_reachable_complexity(partial, [key("top")], Limit(max=1))
    assert findings[0].value == 20.0
    assert findings[0].details["unmeasured_routines"] == ["mid"]


def test_unresolved_call_sites_are_summed_over_the_whole_reach() -> None:
    blind = snapshot(
        nodes=[node("top", 5.0, unresolved=2), node("mid", 5.0, unresolved=3)],
        edges=[edge("top", "mid")],
        records=[record("top"), record("mid")],
    )
    findings = evaluate_reachable_complexity(blind, [key("top")], Limit(max=1))
    assert findings[0].details["unresolved_calls"] == 5


def test_the_message_says_the_number_is_a_lower_bound_and_why() -> None:
    blind = snapshot(
        nodes=[node("top", 50.0, unresolved=4)],
        records=[record("top")],
        resolution={"Python": CallResolution(resolved=30, external=10, unresolved=10)},
    )
    message = evaluate_reachable_complexity(blind, [key("top")], Limit(max=1))[0].message
    assert "lower bound" in message
    assert "4 call sites inside that reach bound to nothing callable" in message
    assert "10 of 50 Python call sites in this run bound to nothing callable (20%)" in message


def test_a_reach_with_no_blind_spot_says_so_rather_than_saying_nothing() -> None:
    clean = snapshot(nodes=[node("top", 50.0)], records=[record("top")])
    message = evaluate_reachable_complexity(clean, [key("top")], Limit(max=1))[0].message
    assert "no call site inside that reach was left unresolved" in message


def test_a_language_with_no_resolution_figure_says_so_rather_than_implying_a_clean_one() -> None:
    unmeasured = snapshot(nodes=[node("top", 50.0)], records=[record("top")], resolution={})
    finding = evaluate_reachable_complexity(unmeasured, [key("top")], Limit(max=1))[0]
    assert "no Python call-resolution figure was measured" in finding.message
    assert finding.details["call_resolution"] == (
        "no Python call-resolution figure was measured for this run"
    )


def test_the_resolution_figure_quoted_is_the_one_for_the_routines_own_language() -> None:
    mixed = snapshot(
        nodes=[node("top", 50.0)],
        records=[record("top", language="C++")],
        resolution={
            "Python": CallResolution(resolved=1, unresolved=99),
            "C++": CallResolution(resolved=95, unresolved=5),
        },
        languages=("C++", "Python"),
    )
    message = evaluate_reachable_complexity(mixed, [key("top")], Limit(max=1))[0].message
    assert "5 of 100 C++ call sites" in message
    assert "Python" not in message


def test_a_reach_through_a_file_that_failed_to_parse_names_that_file() -> None:
    truncated = snapshot(
        nodes=[node("top", 5.0), node("mid", 20.0, path=OTHER)],
        edges=[edge("top", "mid", to=OTHER)],
        records=[record("top"), record("mid", path=OTHER)],
        parse_errors=[ParseError(path=OTHER, line=3, message="unexpected token")],
    )
    findings = evaluate_reachable_complexity(truncated, [key("top")], Limit(max=1))
    assert findings[0].details["truncated_by_parse_errors"] == [OTHER]


def test_a_clean_reach_names_no_truncating_file() -> None:
    findings = evaluate_reachable_complexity(chain(), [key("top")], Limit(max=1))
    assert findings[0].details["truncated_by_parse_errors"] == []


# --- reachable complexity: configuration ----------------------------------------


@pytest.mark.parametrize("limit", [None, Limit(min=1.0)])
def test_a_rule_without_a_maximum_is_switched_off(limit: Limit | None) -> None:
    assert evaluate_reachable_complexity(chain(), [key("top")], limit) == []


@pytest.mark.parametrize(("severity", "blocking"), [("error", True), ("warning", False)])
def test_the_configured_severity_decides_whether_the_finding_blocks(
    severity: str, blocking: bool
) -> None:
    findings = evaluate_reachable_complexity(chain(), [key("top")], Limit(max=1), severity)  # type: ignore[arg-type]
    assert findings[0].severity == severity
    assert findings[0].blocking is blocking


def test_the_default_severity_is_a_warning() -> None:
    assert evaluate_reachable_complexity(chain(), [key("top")], Limit(max=1))[0].severity == (
        "warning"
    )


# --- call cycles ------------------------------------------------------------------


def mutual() -> ProjectSnapshot:
    """``a`` and ``b`` call each other; ``c`` calls ``a`` and is on no cycle."""
    return snapshot(
        nodes=[node("a", 1.0), node("b", 1.0), node("c", 1.0)],
        edges=[edge("a", "b", 2), edge("b", "a"), edge("c", "a")],
        records=[record("a"), record("b"), record("c")],
    )


def test_a_pair_of_mutually_recursive_routines_is_a_cycle() -> None:
    findings = find_call_cycles(mutual(), [key("a")])
    assert [finding.rule for finding in findings] == ["structure.call_cycle"]
    assert findings[0].details["members"] == ["a", "b"]
    assert findings[0].value == 2


def test_the_cycle_names_the_calls_that_close_it_by_qualified_name() -> None:
    findings = find_call_cycles(mutual(), [key("a")])
    assert findings[0].details["closing_calls"] == ["a -> b (2 calls)", "b -> a (1 call)"]
    assert "a -> b (2 calls)" in findings[0].message


def test_a_routine_off_the_cycle_reports_nothing() -> None:
    assert find_call_cycles(mutual(), [key("c")]) == []


def test_a_cycle_is_reported_once_however_many_of_its_members_are_judged() -> None:
    findings = find_call_cycles(mutual(), [key("a"), key("b")])
    assert len(findings) == 1


def test_a_self_call_is_not_a_cycle() -> None:
    recursive = snapshot(
        nodes=[node("solo", 3.0)],
        edges=[edge("solo", "solo", 4)],
        records=[record("solo")],
    )
    assert find_call_cycles(recursive, [key("solo")]) == []


def test_a_routine_the_graph_does_not_hold_reports_no_cycle() -> None:
    assert find_call_cycles(mutual(), [key("outside")]) == []


def test_the_node_list_and_not_the_edge_list_decides_what_the_graph_holds() -> None:
    """A routine named only by an edge is still outside the graph, and is not judged.

    ``call_nodes`` is what carries a routine's complexity and its unresolved-call count, so a
    routine present only as an edge endpoint is one nothing is known about. Reporting a cycle
    through it would name a routine the run cannot say anything else about.
    """
    edges_only = snapshot(
        nodes=[node("b", 1.0)],
        edges=[edge("a", "b"), edge("b", "a")],
        records=[record("a"), record("b")],
    )
    assert find_call_cycles(edges_only, [key("a")]) == []
    assert [f.details["members"] for f in find_call_cycles(edges_only, [key("b")])] == [["a", "b"]]


def test_the_cycle_finding_carries_the_resolution_figure_too() -> None:
    findings = find_call_cycles(mutual(), [key("a")])
    assert "call sites in this run bound to nothing callable" in str(
        findings[0].details["call_resolution"]
    )


@pytest.mark.parametrize(("severity", "blocking"), [("error", True), ("warning", False)])
def test_a_cycle_follows_the_configured_severity(severity: str, blocking: bool) -> None:
    findings = find_call_cycles(mutual(), [key("a")], severity)  # type: ignore[arg-type]
    assert findings[0].severity == severity
    assert findings[0].blocking is blocking


# --- the shared view --------------------------------------------------------------


def test_the_graph_reports_which_routines_it_holds() -> None:
    calls = CallGraph.of(chain())
    assert calls.holds(key("top").token)
    assert not calls.holds(key("absent").token)


def test_a_reach_is_sorted_and_holds_the_subject() -> None:
    reach = CallGraph.of(chain()).reach(key("top").token, frozenset())
    assert reach.routines == tuple(sorted(reach.routines))
    assert key("top").token in reach.routines


# --- against the shared project fixture -------------------------------------------


MAIN = EntityKey(scope="routine", path="src/cli/app.py", longname="app.main", parameters="argv")
APPLY_RULES = EntityKey(
    scope="routine",
    path="src/analysis/rules.py",
    longname="rules.apply_rules",
    parameters="specs,snapshot",
)


def test_the_fixture_after_side_reaches_the_whole_call_graph_from_main() -> None:
    """``app.main`` reaches every routine of the fixture, totalling their complexities."""
    after = snapshot_fixture("after")
    reach = CallGraph.of(after).reach(MAIN.token, after.unparsed_files)
    assert len(reach.routines) == 8
    assert reach.complexity == 49.0
    assert reach.unmeasured == ()
    assert reach.truncated_by == ("src/analysis/rules.py",)


def test_the_fixture_reach_breaks_a_limit_it_is_over_and_not_one_it_is_under() -> None:
    after = snapshot_fixture("after")
    assert evaluate_reachable_complexity(after, [MAIN], Limit(max=49)) == []
    over = evaluate_reachable_complexity(after, [MAIN], Limit(max=48))
    assert [finding.value for finding in over] == [49.0]


def test_the_fixture_change_closes_a_call_cycle_that_did_not_exist_before() -> None:
    """The same change that closes the file cycle closes a routine cycle inside it."""
    assert find_call_cycles(snapshot_fixture("before"), [APPLY_RULES]) == []
    findings = find_call_cycles(snapshot_fixture("after"), [APPLY_RULES])
    assert [finding.details["members"] for finding in findings] == [
        ["engine.Engine.evaluate", "rules.apply_rules"]
    ]


# --- the finding has to be actionable, not just correct ---------------------------


def weighted() -> ProjectSnapshot:
    """``top`` reaches three routines of very different weight, plus one unmeasured."""
    return snapshot(
        nodes=[
            node("top", 1.0),
            node("light", 2.0),
            node("heavy", 40.0),
            node("middling", 9.0),
            node("unknown", None),
        ],
        edges=[edge("top", name) for name in ("light", "heavy", "middling", "unknown")],
        records=[record(name) for name in ("top", "light", "heavy", "middling", "unknown")],
    )


def test_the_finding_names_the_heaviest_routines_it_reaches() -> None:
    """A total names the problem; the ranking names the work."""
    findings = evaluate_reachable_complexity(weighted(), [key("top")], Limit(max=1))
    assert findings[0].details["heaviest"] == [
        "heavy (40)",
        "middling (9)",
        "light (2)",
        "top (1)",
    ]


def test_the_message_names_the_worst_three() -> None:
    message = evaluate_reachable_complexity(weighted(), [key("top")], Limit(max=1))[0].message
    assert "heaviest: heavy (40), middling (9), light (2)" in message
    assert "top (1)" not in message


def test_an_unmeasured_routine_is_not_ranked_as_free() -> None:
    """It is already in ``unmeasured_routines``; a ``0`` here would read as "this one is free"."""
    findings = evaluate_reachable_complexity(weighted(), [key("top")], Limit(max=1))
    assert "unknown (0)" not in findings[0].details["heaviest"]
    assert findings[0].details["unmeasured_routines"] == ["unknown"]


def test_ties_break_on_the_name_so_two_runs_agree() -> None:
    tied = snapshot(
        nodes=[node("top", 1.0), node("beta", 5.0), node("alpha", 5.0)],
        edges=[edge("top", "beta"), edge("top", "alpha")],
        records=[record(n) for n in ("top", "beta", "alpha")],
    )
    findings = evaluate_reachable_complexity(tied, [key("top")], Limit(max=1))
    assert findings[0].details["heaviest"][:2] == ["alpha (5)", "beta (5)"]


def test_a_reach_of_one_still_names_itself() -> None:
    lone = snapshot(nodes=[node("only", 12.0)], records=[record("only")])
    findings = evaluate_reachable_complexity(lone, [key("only")], Limit(max=1))
    assert findings[0].details["heaviest"] == ["only (12)"]
