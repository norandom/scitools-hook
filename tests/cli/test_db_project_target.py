"""Where `db project --out` writes, and the names Understand will not build under.

`und` does not report a bad database name: `und create -db proj.uhd` exits 0 and writes
nothing, and the *next* command fails with "An open database is required for this action",
which names neither the file nor the reason. With no suffix at all it exits 0 and writes
`proj.und`, leaving the path the operator asked for empty and a differently named database
beside it. Both were measured against Understand 6.5.1204.

So the name is decided here, before anything runs, and the one case that cannot be decided
here -- a `create` that reports success and produces nothing anyway -- is caught by a
post-condition in `understand.database`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scitools_hook.cli.db import PROJECT_SUFFIX, project_target
from scitools_hook.errors import ConfigError

ROOT = Path("/repo")


def test_no_out_writes_beside_the_repository() -> None:
    assert project_target(None, ROOT) == ROOT / f"scitools-hook.worktree{PROJECT_SUFFIX}"


def test_a_und_name_is_taken_as_typed(tmp_path: Path) -> None:
    target = tmp_path / "facdrone.und"
    assert project_target(target, ROOT) == target.resolve()


def test_a_name_with_no_suffix_gains_one(tmp_path: Path) -> None:
    """Understand does this silently, to a path nobody sees; doing it here is visible."""
    assert project_target(tmp_path / "facdrone", ROOT) == (tmp_path / "facdrone.und").resolve()


@pytest.mark.parametrize("name", ["facdrone.uhd", "facdrone.udb", "report.json", "db.und.bak"])
def test_another_suffix_is_refused_with_the_corrected_path(name: str) -> None:
    """Refused rather than corrected: `--out report.json` is a mistake about what this makes.

    `.uhd` and `.udb` are the two near-misses that produced the original report -- `.udb` is
    Understand's own older extension, so it is exactly the name someone would reasonably try.
    """
    with pytest.raises(ConfigError) as refused:
        project_target(Path(name), ROOT)

    assert refused.value.key == "--out"
    assert ".und" in str(refused.value.hint)
    assert name in str(refused.value)
