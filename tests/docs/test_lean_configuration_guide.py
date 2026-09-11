"""The configuration guide's ``[lean]`` section, bound to what ``init`` writes (req 10.2).

Nothing here is typed twice: the excerpt the page shows is the renderer's output and every
key the model declares has to be named on the page, so a default or a key added to
``LeanRules`` reaches the guide or fails a test. ``tests/skills/test_packaged_skills.py``
does the same for the skills.
"""

from __future__ import annotations

import pytest
from lean_docs import CONFIGURATION, read

from scitools_hook.config.defaults import default_settings
from scitools_hook.config.models import LeanRules
from scitools_hook.config.template import render_template

SHIPPED_LEAN = LeanRules()
"""The family's defaults as they ship; the guide quotes these and nothing typed."""


def _rendered_lean_block() -> str:
    """The ``[lean]`` table exactly as ``scitools-hook init`` writes it, up to the next table."""
    rendered = render_template(default_settings())
    start = rendered.index("[lean]\n")
    stop = rendered.index("\n\n# ", start)
    return rendered[start:stop]


@pytest.mark.parametrize("key", list(LeanRules.model_fields))
def test_every_lean_key_is_in_the_configuration_guide(key: str) -> None:
    """A key the model declares and the guide does not name is a key an operator cannot find."""
    assert f"`{key}`" in read(CONFIGURATION), key


def test_the_configuration_guide_pastes_the_block_init_renders() -> None:
    """The excerpt is the renderer's output, so a default change reaches the page or fails."""
    block = _rendered_lean_block()
    assert block.startswith("[lean]\n")
    assert f"resolution_floor = {SHIPPED_LEAN.resolution_floor}" in block
    assert block in read(CONFIGURATION)
