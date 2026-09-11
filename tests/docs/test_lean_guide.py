"""The lean-code guide, bound to what ships (lean-code-rules req 8.3, 8.4, 10.4).

A guide that quotes a stale default is the defect this feature has been rejected for more
than once, so every value the page carries is read from the shipped default, rendered by the
renderer that prints it, or read off the hint catalogue. The guide and the four skills are
read by the same agents, and where they say the same thing they must say it the same way;
``tests/skills/test_packaged_skills.py`` holds the skills to the same values.

The ladder's seven rungs are checked beside the agents guide, in
``test_lean_agents_guide.py``, because that is the file that renders the snippet.
"""

from __future__ import annotations

import pytest
from lean_docs import GUIDE, LEAN_RULES, read, rule_and_tag, tags_shipped

from scitools_hook.config.models import LeanRules
from scitools_hook.models.change import NetDelta
from scitools_hook.report.human import LEAN_ALREADY_PREFIX, net_line

SHIPPED_LEAN = LeanRules()
"""The family's defaults as they ship; the guide quotes these and nothing typed."""


def test_the_guide_names_the_three_tags_and_the_two_it_never_sees() -> None:
    """The tags listed are the ones the catalogue ships, and only those (req 8.3, 10.4)."""
    guide = read(GUIDE)
    shipped = tags_shipped()
    assert shipped == {"delete:", "yagni:", "shrink:"}, shipped
    for tag in shipped:
        assert f"`{tag}`" in guide, tag
    for never in ("stdlib:", "native:"):
        assert never not in shipped
        assert f"`{never}`" in guide, never
    assert "reference database" in guide, "the reasoning: what the database cannot see"


@pytest.mark.parametrize("name", LEAN_RULES)
def test_the_guide_names_every_lean_rule_with_its_tag(name: str) -> None:
    """Each rule appears on a line with the tag its shipped hint opens with."""
    rule, tag = rule_and_tag(name)
    lines = read(GUIDE).splitlines()
    assert any(f"`{rule}`" in line and f"`{tag}`" in line for line in lines), (rule, tag)


def test_the_guide_reads_the_net_line_in_the_shape_the_report_prints() -> None:
    """The examples are :func:`net_line`'s output; a format change reaches the page.

    The zero case is the one task 2.2's review asked for: a deleted file that held no
    routines reads as net zero over no routines, because the delta counts routines.
    """
    guide = read(GUIDE)
    assert net_line(NetDelta(statements=12, lines=30, routines=7)) in guide
    assert net_line(NetDelta(statements=0, lines=0, routines=0)) in guide
    assert LEAN_ALREADY_PREFIX.strip() in guide


def test_the_guide_quotes_the_floors_and_what_a_refused_rule_prints() -> None:
    """Both floors at the shipped value, and the once-per-run refusal wording."""
    guide = read(GUIDE)
    for key in ("resolution_floor", "accuracy_floor"):
        assert f"`{key} = {getattr(SHIPPED_LEAN, key)}`" in guide, key
    assert "was not evaluated" in guide
    assert f"{SHIPPED_LEAN.accuracy_floor:.0%}" in guide


def test_the_guide_quotes_the_duplication_defaults_from_the_shipped_values() -> None:
    for key in (
        "similar_threshold",
        "similar_min_statements",
        "similar_min_family",
        "duplicates_min_lines",
        "verbosity_min_statements",
    ):
        assert f"`{key} = {getattr(SHIPPED_LEAN, key)}`" in read(GUIDE), key


def test_the_guide_names_the_scope_proposal_and_the_lists_that_reach_tests() -> None:
    """The tests policy: a scope for the two thresholds, the shipped lists for the rules."""
    guide = read(GUIDE)
    assert "[scope.tests]" in guide
    for pattern in (r"(^|\.)test_", r"(^|\.)Test", "pytestmark"):
        assert pattern in guide, pattern
    assert r"(^|\.)test_" in SHIPPED_LEAN.pass_through_ignore
    assert r"(^|\.)Test" in SHIPPED_LEAN.unused_classes_ignore
    assert any("pytestmark" in p for p in SHIPPED_LEAN.unused_variables_ignore)
