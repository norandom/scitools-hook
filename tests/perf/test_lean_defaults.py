"""Unit tests for the pure parts of the lean-defaults harness (lean-code-rules task 6.3).

The harness drives real ``und`` analyses and takes minutes, so its module name does not begin
with ``test_`` and the default suite never collects it. What *is* collected is everything in
it that turns text into the tables the research log carries: the generated configuration,
the "was not evaluated" lines the floors print, the per-rule count table and the first-ten
listing. A renderer that agreed only with itself would fill the research log with a
plausible-looking table that the run never produced.

:data:`REFUSAL_LINES` is verbatim diagnostics output captured on 2026-09-11 from
``check --all`` on this repository with every lean rule on, at the shipped floors; the
document below is the shape of the JSON that run wrote, cut to three findings.
"""

from __future__ import annotations

from lean_defaults import (
    LEAN_SWITCHES,
    SHRINK_RULES,
    all_on,
    count_rows,
    parse_refusals,
    render_counts,
    render_first,
)

WHY = (
    "Understand parsed 19% of this analysis without an error, below the accuracy floor of "
    "75%; a name whose use sites sit in a region the analysis errored on reads as unused "
    "while it is read"
)
"""The reason both refused rules printed, verbatim, split only to fit the column limit."""

REFUSAL_LINES = (
    "... reading the after snapshot finished in 15.6s\n"
    f"structure.unused_parameter was not evaluated: {WHY}\n"
    f"structure.pass_through was not evaluated: {WHY}\n"
    "Command exited with non-zero status 1\n"
)

DOCUMENT = {
    "accuracy": {"after": 0.1910828025477707},
    "findings": [
        {"rule": "structure.similar_routine", "path": "a.py", "line": 3, "message": "twin of b"},
        {"rule": "structure.similar_routine", "path": "b.py", "line": 9, "message": "twin of a"},
        {"rule": "routine.LinesPerStatement", "path": "c.py", "line": 1, "message": "long"},
    ],
}


def test_the_generated_configuration_switches_every_lean_rule_on_and_nothing_else() -> None:
    text = all_on("[project]\ninclude = ['**']\n", lower_floors=False)
    tail = text.split("[lean]\n", 1)[1].splitlines()
    assert tail == [f'{switch} = "warning"' for switch, _ in LEAN_SWITCHES]
    assert text.startswith("[project]\ninclude = ['**']\n")


def test_lowering_the_floors_adds_exactly_the_two_floor_keys() -> None:
    plain = all_on("", lower_floors=False)
    lowered = all_on("", lower_floors=True)
    assert lowered.replace("resolution_floor = 0.0\naccuracy_floor = 0.0\n", "") == plain


def test_a_refusal_is_keyed_by_its_rule_and_keeps_the_reason() -> None:
    found = parse_refusals(REFUSAL_LINES)
    assert set(found) == {"structure.unused_parameter", "structure.pass_through"}
    assert found["structure.pass_through"].startswith("Understand parsed 19% of this analysis")


def test_every_lean_rule_gets_a_row_with_its_count_and_its_refusal() -> None:
    rows = count_rows(DOCUMENT, parse_refusals(REFUSAL_LINES))
    assert [rule for rule, _, _ in rows] == [r for _, r in LEAN_SWITCHES] + list(SHRINK_RULES)
    by_rule = {rule: (count, note) for rule, count, note in rows}
    assert by_rule["structure.similar_routine"] == (2, "")
    assert by_rule["routine.LinesPerStatement"] == (1, "")
    assert by_rule["structure.unused_class"] == (0, "")
    assert by_rule["structure.pass_through"][0] == 0
    assert "below the accuracy floor of 75%" in by_rule["structure.pass_through"][1]


def test_the_count_table_is_markdown_with_one_row_per_rule() -> None:
    rows = count_rows(DOCUMENT, {})
    table = render_counts("on", rows).splitlines()
    assert table[0] == "| rule (on) | count | note |"
    assert table[1] == "| --- | --- | --- |"
    assert len(table) == 2 + len(rows)
    assert "| `structure.similar_routine` | 2 |  |" in table


def test_the_first_findings_are_listed_in_the_order_the_check_printed_them() -> None:
    listed = render_first(DOCUMENT, "structure.similar_routine", limit=1).splitlines()
    assert listed == [
        "**`structure.similar_routine`**: 2 findings, first 1:",
        "- `a.py:3` twin of b",
    ]
    assert render_first(DOCUMENT, "structure.over_export") == (
        "**`structure.over_export`**: 0 findings, first 0:"
    )
