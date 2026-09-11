"""What ``mkdocs build --strict`` would refuse, for a machine that cannot run the build.

The site's ``validation`` block promotes an omitted page, a nav entry with no file and a
link to a missing page or anchor from INFO to a failure. The docs workflow runs the build
with the network; these three checks are the same questions asked without it, over the
pages the lean-code family touched.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from lean_docs import AGENTS, CONFIGURATION, DOCS, GUIDE, NAV, anchors, nav_pages, read


def test_the_lean_guide_is_in_the_navigation() -> None:
    assert re.search(r"^\s+- guide/lean-code\.md$", read(NAV), re.MULTILINE), "not in nav"


def test_every_page_is_in_the_nav_and_every_nav_entry_is_a_page() -> None:
    pages = {p.relative_to(DOCS).as_posix() for p in DOCS.rglob("*.md")}
    listed = set(nav_pages())
    assert listed == pages, {"unlisted": pages - listed, "missing": listed - pages}


@pytest.mark.parametrize("page", [GUIDE, CONFIGURATION, AGENTS], ids=lambda p: p.name)
def test_every_relative_link_on_the_page_resolves(page: Path) -> None:
    text = read(page)
    for target in re.findall(r"\]\(([^)\s]+)\)", text):
        if "://" in target or target.startswith("mailto:"):
            continue
        path_part, _, anchor = target.partition("#")
        destination = page if not path_part else (page.parent / path_part).resolve()
        assert destination.is_file(), target
        if anchor:
            assert anchor in anchors(destination), target
