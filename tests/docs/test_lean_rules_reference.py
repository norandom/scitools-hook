"""The rules reference's lean section, bound to what ships (lean-code-rules req 10.1, 10.5).

Task 8.1's done-when is that every lean rule name in the code appears in the rules reference,
and the defect this feature was rejected for most often is a reference page quoting a stale
number. So nothing here is typed twice: the nine names are read off ``StructureRuleName``
through the family tuple that is pinned to its tail, every default is read off ``LeanRules()``
and the two shrink limits off the shipped thresholds, and the page has to carry each one.
"""

from __future__ import annotations

import re

import pytest
from lean_docs import DOCS, GUIDE, LEAN_RULES, anchors, read

from scitools_hook.config.defaults import default_settings
from scitools_hook.config.models import LeanRules
from scitools_hook.models.findings import STRUCTURE_RULES, structure_rule

RULES = DOCS / "reference" / "rules.md"
LEAN_ANCHOR = "the-lean-code-rules"
"""The heading the lean-code guide deep-links to; the section the whole family sits under."""

SHIPPED_LEAN = LeanRules()


def _lean_section() -> str:
    page = read(RULES)
    start = page.index("## The lean-code rules")
    return page[start:]


def test_the_family_is_the_nine_names_at_the_tail_of_the_structure_rules() -> None:
    """The names the page is held to are ``StructureRuleName``'s last nine, not a local list."""
    assert len(LEAN_RULES) == 9, LEAN_RULES
    assert STRUCTURE_RULES[-len(LEAN_RULES) :] == LEAN_RULES


@pytest.mark.parametrize("name", LEAN_RULES)
def test_every_lean_rule_has_its_own_entry_in_the_reference(name: str) -> None:
    """One heading per rule, so a finding's rule name is an anchor on the page (req 10.1)."""
    rule = structure_rule(name)
    lines = _lean_section().splitlines()
    assert any(line.startswith(f"### `{rule}`") for line in lines), rule


@pytest.mark.parametrize("name", LEAN_RULES)
def test_every_entry_says_what_it_reports_and_what_it_does_not(name: str) -> None:
    """Requirement 10.1's four parts, as the labelled paragraphs each entry opens with."""
    section = _lean_section()
    start = section.index(f"### `{structure_rule(name)}`")
    stop = section.find("\n### ", start + 1)
    entry = section[start : stop if stop > 0 else None]
    for label in ("**Reports.**", "**Does not report.**", "**Default.**", "**Measurement.**"):
        assert label in entry, (name, label)


def test_every_lean_default_on_the_page_is_the_shipped_one() -> None:
    """Every ``[lean]`` key is named, and every number or severity is the model's own."""
    section = _lean_section()
    for key, value in SHIPPED_LEAN.model_dump().items():
        if isinstance(value, bool | list):
            assert f"`{key}`" in section, key
        elif value is None:
            assert re.search(rf"`{key}`[^\n]*\boff\b", section), key
        elif isinstance(value, str):
            assert f'`{key} = "{value}"`' in section, key
        else:
            assert f"`{key} = {value}`" in section, (key, value)


def test_the_two_shrink_limits_are_in_the_routine_table_at_their_shipped_values() -> None:
    """The two metrics the family ships on are threshold rows, so they sit in that table."""
    page = read(RULES)
    for spec in default_settings().thresholds:
        if spec.scope != "routine" or spec.metric not in ("LinesPerStatement", "CountLineComment"):
            continue
        limit = spec.limit.max
        text = f"{limit:g}" if isinstance(limit, float) else str(limit)
        assert re.search(rf"^\| `{spec.metric}` \| {text} \| \*?\*?{spec.severity}", page, re.M), (
            spec.metric
        )


def test_the_floors_are_stated_with_what_the_rules_say_below_them() -> None:
    """Both floors, that both repositories are below them, and the two rules that are noise."""
    section = _lean_section()
    assert f"{SHIPPED_LEAN.accuracy_floor:.0%}" in section
    assert "was not evaluated" in section
    for measured in ("19.1%", "25.9%", "45.9%", "32.0%"):
        assert measured in section, measured
    for shape in ("pytest fixture", "`@overload`", "comprehension"):
        assert shape in section, shape
    assert "`[analysis] accuracy_floor`" in section


def test_the_blind_spots_are_per_language_beside_the_routine_rules() -> None:
    """Requirement 10.5: the kind table's languages, and the four shapes ``unused_ignore`` names."""
    section = _lean_section()
    start = section.index("### What reference-based detection cannot see")
    blind = section[start:]
    for language in ("Python", "C++", "Ada", "C#", "Fortran", "Java", "Pascal", "TypeScript"):
        assert re.search(rf"^\| {re.escape(language)} ", blind, re.M), language
    for shape in ("packaging metadata", "decorator", "dunder", "pytest"):
        assert shape in blind, shape
    assert "`structure.unused_routine`" in blind


def test_the_net_zero_of_a_routineless_deleted_file_is_on_the_page() -> None:
    """Task 2.2's review: a deleted file with no routines is a genuine zero, not a broken number."""
    section = _lean_section()
    assert "held no routines" in section
    assert "over 0 routines" in section


def test_the_guide_deep_links_to_the_lean_section() -> None:
    """Task 8.2 linked the whole page because the section did not exist yet; now it does."""
    assert LEAN_ANCHOR in anchors(RULES)
    assert f"../reference/rules.md#{LEAN_ANCHOR}" in read(GUIDE)
    assert "(../reference/rules.md)" not in read(GUIDE)
