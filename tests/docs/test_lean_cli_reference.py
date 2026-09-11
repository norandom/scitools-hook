"""The CLI reference and the feature list, bound to what ships (lean-code-rules req 10.3).

The net line on the CLI page is the renderer's own string, the three ``doctor`` rows are
members of ``Feature`` and every example uses the form task 7.2 found works, the group
option before the subcommand.
"""

from __future__ import annotations

import re

import pytest
from lean_docs import DOCS, LEAN_RULES, read, rule_and_tag

from scitools_hook.models.change import NetDelta
from scitools_hook.models.understand import Feature
from scitools_hook.report.human import LEAN_ALREADY_PREFIX, net_line

CLI = DOCS / "reference" / "cli.md"
FEATURES = DOCS / "reference" / "features.md"

LEAN_ROWS = ("feature lean references", "feature lean tokens", "feature duplicate metric")
"""The three rows requirement 9.2 asks ``doctor`` for, as the report labels them."""


@pytest.mark.parametrize("label", LEAN_ROWS)
def test_the_cli_page_shows_each_lean_doctor_row(label: str) -> None:
    """Each row is a ``Feature`` member's label, and the page shows it as ``doctor`` prints it."""
    assert Feature(label.removeprefix("feature ").replace(" ", "_")) in Feature
    assert re.search(rf"^\s+{re.escape(label)}:\s+\S", read(CLI), re.M), label


def test_the_cli_page_reads_the_net_line_in_the_shape_the_report_prints() -> None:
    """The example is :func:`net_line`'s output, and the zero case names its cause."""
    page = read(CLI)
    assert net_line(NetDelta(statements=12, lines=30, routines=7)) in page
    assert net_line(NetDelta(statements=0, lines=0, routines=0)) in page
    assert "held no routines" in page
    assert LEAN_ALREADY_PREFIX.strip() in page


def test_every_verbose_example_puts_the_group_option_before_the_subcommand() -> None:
    """Task 7.2: ``--verbose`` is a group option, so ``check --verbose`` is a usage error."""
    page = read(CLI)
    assert "scitools-hook --verbose check" in page
    assert not re.search(r"\bcheck\s+--verbose\b", page), "an example in the broken form"


def test_the_feature_list_has_a_row_for_every_lean_rule_and_says_it_ships_off() -> None:
    """Requirement 10.3: one row per capability; every rule of the family is off."""
    page = read(FEATURES)
    start = page.index("## What it leaves behind")
    section = page[start : page.index("\n## ", start + 1)]
    for name in LEAN_RULES:
        rule, _tag = rule_and_tag(name)
        rows = [line for line in section.splitlines() if f"`{rule}`" in line]
        assert rows, name
        assert all("**off**" in row for row in rows), (name, rows)


def test_the_feature_list_names_what_ships_on_in_the_family() -> None:
    """The floors, the two shrink metrics, the net line and the doctor rows ship on."""
    page = read(FEATURES)
    start = page.index("## What it leaves behind")
    section = page[start : page.index("\n## ", start + 1)]
    for on in ("resolution_floor", "LinesPerStatement", "net:", "doctor"):
        rows = [line for line in section.splitlines() if on in line and line.startswith("|")]
        assert rows, on
        assert any("**on**" in row or "| on" in row for row in rows), (on, rows)
    assert "lean-code.md" in section
