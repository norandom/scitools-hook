"""The rows ``doctor`` prints about the analysis cache's before side (requirement 3.6).

Its own module because ``test_cli_commands`` is twelve functions past its own
``CountDeclFunction`` limit and the gate refused three more -- correctly, since a file that
tests every command is a file nobody reads to find one.

Each test writes a sync state by hand and reads one row back through the real command, so
what is asserted is what an operator sees rather than what a formatter returns. The route is
worth a row of its own because the two routes hold **different file sets**: the shadow tree is
the working tree filtered by the include and exclude patterns, while a commit-built database
copies its file set from the after database and rescans it against the commit.
"""

from __future__ import annotations

import json
from pathlib import Path

from conftest import MakeGitRepo
from test_cli_commands import cache_paths, env_for, invoke, one_row, seeded

from scitools_hook.exit_codes import ExitCode


def test_doctor_names_the_route_the_before_database_was_built_by(
    tmp_path: Path, git_repo: MakeGitRepo
) -> None:
    """Requirement 3.6: the two routes hold different file sets, so which one ran matters."""
    builder = seeded(git_repo)
    paths = cache_paths(tmp_path, builder)
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.state.write_text(
        json.dumps(
            {
                "after_target": "index",
                "after_tree_id": "abc",
                "before_commit": "3ca0a97",
                "before_route": "commit",
                "languages": ["Python"],
            }
        ),
        encoding="utf-8",
    )
    result = invoke(["doctor"], cwd=builder.path, env=env_for(tmp_path))
    assert result.exit_code == int(ExitCode.OK), result.stderr
    assert one_row(result.stdout, "before route") == "commit (3ca0a97)"


def test_doctor_names_the_shadow_route_without_a_commit(
    tmp_path: Path, git_repo: MakeGitRepo
) -> None:
    """A shadow-built database has a before commit too; the route is the other question."""
    builder = seeded(git_repo)
    paths = cache_paths(tmp_path, builder)
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.state.write_text(
        json.dumps({"before_commit": "3ca0a97", "before_route": "shadow", "languages": []}),
        encoding="utf-8",
    )
    result = invoke(["doctor"], cwd=builder.path, env=env_for(tmp_path))
    assert one_row(result.stdout, "before route") == "shadow"


def test_doctor_says_none_before_any_before_side_has_been_built(
    tmp_path: Path, git_repo: MakeGitRepo
) -> None:
    """Every repository until its first staged or ranged check; not a problem, a fact."""
    builder = seeded(git_repo)
    paths = cache_paths(tmp_path, builder)
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.state.write_text(json.dumps({"languages": ["Python"]}), encoding="utf-8")
    result = invoke(["doctor"], cwd=builder.path, env=env_for(tmp_path))
    assert one_row(result.stdout, "before route") == "none"
