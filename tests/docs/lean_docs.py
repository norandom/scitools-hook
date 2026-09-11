"""Shared setup for the tests over the lean-code guides (lean-code-rules 8.2).

Not a test module: the four ``test_lean_*`` files beside it each import from here rather
than from the package, so that no one of them depends on more first-party modules than the
gate allows a file to gain in one change. The split is by subject, the way
``tests/contract/token_index_project.py`` serves the token-index contract tests.

What lives here is the part every file needs -- where the pages are, how to read the
generated block out of the agents guide, and the hint catalogue reduced to the family -- and
nothing that only one file asks for.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from scitools_hook.models.findings import structure_rule
from scitools_hook.report.hints import DEFAULT_CATALOGUE
from scitools_hook.report.lean_examples import LEAN_RULES

__all__ = [
    "AGENTS",
    "CONFIGURATION",
    "DOCS",
    "GUIDE",
    "LEAN_RULES",
    "NAV",
    "anchors",
    "example_block",
    "lean_hints",
    "nav_pages",
    "read",
    "rule_and_tag",
    "slug",
    "tags_shipped",
]

DOCS = Path(__file__).resolve().parents[2] / "docs"
NAV = Path(__file__).resolve().parents[2] / "mkdocs.yml"
GUIDE = DOCS / "guide" / "lean-code.md"
CONFIGURATION = DOCS / "guide" / "configuration.md"
AGENTS = DOCS / "guide" / "agents.md"


def read(page: Path) -> str:
    assert page.is_file(), page
    return page.read_text(encoding="utf-8")


def lean_hints() -> dict[str, str]:
    """Every shipped hint of the family, variants included, keyed as the catalogue keys them."""
    names = {structure_rule(name) for name in LEAN_RULES}
    return {
        rule: hint
        for rule, hint in DEFAULT_CATALOGUE.items()
        if rule.split("/")[0] in names and not rule.endswith("/example")
    }


def tags_shipped() -> set[str]:
    """The tag each lean hint opens with, read off the catalogue (req 8.1)."""
    return {hint.split(":", 1)[0] + ":" for hint in lean_hints().values()}


def rule_and_tag(name: str) -> tuple[str, str]:
    """A lean rule's finding name and the tag its shipped hint opens with."""
    rule = structure_rule(name)
    return rule, DEFAULT_CATALOGUE[rule].split(":", 1)[0] + ":"


def example_block(page: str) -> str:
    """The generated block the agents guide shows, dedented out of its collapsed example."""
    lines = page.splitlines()
    head = next(i for i, line in enumerate(lines) if 'example "The full block' in line)
    start = next(i for i in range(head, len(lines)) if lines[i] == "    ```markdown") + 1
    body: list[str] = []
    for line in lines[start:]:
        if line and not line.startswith("    "):
            break
        body.append(line[4:])
    while body and body[-1] in ("", "```"):
        body.pop()
    return "\n".join(body)


def nav_pages() -> list[str]:
    nav = read(NAV)
    body = nav[nav.index("\nnav:\n") :]
    return re.findall(r"^\s+- (?:[^:\n]+: )?([\w/.-]+\.md)$", body, re.MULTILINE)


def slug(heading: str) -> str:
    """mkdocs' default heading slug: ASCII, lower case, punctuation gone, spaces to hyphens."""
    text = unicodedata.normalize("NFKD", heading).encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s-]", "", text.strip().lower())
    return re.sub(r"[-\s]+", "-", text)


def anchors(page: Path) -> set[str]:
    headings = re.findall(r"^#{1,6}\s+(.+?)\s*(?:\{#.*\})?$", read(page), re.MULTILINE)
    return {slug(h) for h in headings}
