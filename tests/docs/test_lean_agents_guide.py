"""The agents guide's generated block and the lean section in it (lean-code-rules req 8.4).

The block the page shows is what ``agent-rules`` prints from the shipped defaults, so it is
compared with the renderer's output rather than transcribed; the ladder the lean-code guide
walks is checked here too, rung for rung, because this is the file that renders the snippet
the rungs come from.
"""

from __future__ import annotations

import re

from lean_docs import AGENTS, GUIDE, example_block, read

from scitools_hook.config.defaults import default_settings
from scitools_hook.report.agent_rules import render_rules
from scitools_hook.skills import NAMES as SHIPPED_SKILLS


def _lean_section(settings=None) -> str:
    """The snippet's lean section, from the renderer the ``agent-rules`` command calls."""
    text = render_rules(settings or default_settings(), effective=[])
    start = text.index("## Lean code")
    stop = text.index("## The ratchet")
    return text[start:stop].rstrip()


def test_the_guide_walks_the_ladder_in_the_snippets_words() -> None:
    """The seven rungs, one line each, are the lines ``agent-rules`` prints (req 8.4)."""
    rungs = re.findall(r"^\d\. .+$", _lean_section(), re.MULTILINE)
    assert len(rungs) == 7, rungs
    guide = read(GUIDE)
    for rung in rungs:
        assert rung in guide, rung


def test_the_agents_guide_shows_the_lean_section_of_the_snippet() -> None:
    """The example block carries the lean section byte for byte as the renderer prints it."""
    block = example_block(read(AGENTS))
    assert _lean_section() in block


def test_the_agents_guide_shows_the_two_shrink_limits_the_snippet_lists() -> None:
    """The two shrink metrics ship on, so the example block lists them at their defaults."""
    specs = default_settings().thresholds
    block = example_block(read(AGENTS))
    for metric in ("LinesPerStatement", "CountLineComment"):
        spec = next(s for s in specs if s.scope == "routine" and s.metric == metric)
        limit = spec.limit.max
        limit_text = f"{limit:g}" if isinstance(limit, float) else str(limit)
        assert f"- `{metric}`: at most {limit_text} ({spec.severity})" in block, metric


def test_the_agents_guide_counts_the_skills_the_package_ships() -> None:
    """Four ship; the page said three and showed a three-line transcript (task 8.1)."""
    page = read(AGENTS)
    assert len(SHIPPED_SKILLS) == 4, SHIPPED_SKILLS
    assert "three skills" not in page
    assert "four skills" in page
    installed = re.findall(r"^installed: (scitools-\w+) at ", page, re.MULTILINE)
    assert tuple(installed) == SHIPPED_SKILLS, installed


def test_the_agents_guide_shows_what_an_enabled_rule_line_looks_like() -> None:
    """The shipped block has every rule off; the guide also shows the lines two enabled ones add."""
    settings = default_settings().model_copy(deep=True)
    settings.lean.similar_routines = "warning"
    settings.lean.duplicates = "warning"
    section = _lean_section(settings)
    start = section.index("### The rules in force")
    stop = section.index("### The net line")
    rules = section[start:stop].strip()
    assert "`structure.similar_routine` (warning), `delete:`" in rules
    assert rules in read(AGENTS)
