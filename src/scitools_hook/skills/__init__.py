"""The agent skills this tool ships, and the packaged files they are read from.

The skills are data, not code: two Markdown documents under this package, copied into a
repository by ``scitools-hook install-skills``. They live here rather than in ``.claude/``
for one reason -- ``.claude`` is excluded from the sdist (see ``pyproject.toml``), so a
skill kept only there reaches nobody who installs the tool, and enabling a new repository
would mean copying a file by hand out of a checkout the operator may not have.

Reading them through :mod:`importlib.resources` rather than ``Path(__file__).parent`` is
what lets that work from a wheel, a zipapp or a ``uvx`` cache, none of which guarantee a
real directory on disk.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from typing import Final

FILENAME: Final = "SKILL.md"
"""The name every skill's document has, fixed by the skill format rather than by us."""

NAMES: Final = ("scitools-gate", "scitools-improve")
"""The shipped skills, in the order an operator meets them: gate a commit, then improve.

Enumerated rather than discovered by listing the directory. A wheel built without one of
them would otherwise install "the skills" successfully and silently ship one, which is the
shape of silent success this project keeps refusing elsewhere.
"""


@dataclass(frozen=True)
class PackagedSkill:
    """One shipped skill: the directory name it installs under, and its document."""

    name: str
    text: str

    @property
    def relative_path(self) -> str:
        """Where it goes beneath the skills directory, as the skill format requires."""
        return f"{self.name}/{FILENAME}"


def read(name: str) -> PackagedSkill:
    """The packaged skill called ``name``.

    Raises ``KeyError`` for a name this version does not ship, so a caller cannot install a
    typo as an empty skill.
    """
    if name not in NAMES:
        raise KeyError(name)
    document = files(__package__).joinpath(name, FILENAME)
    return PackagedSkill(name=name, text=document.read_text(encoding="utf-8"))


def shipped() -> list[PackagedSkill]:
    """Every packaged skill, in :data:`NAMES` order."""
    return [read(name) for name in NAMES]
