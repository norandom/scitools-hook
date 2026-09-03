"""The same two workflows against a real, licensed Understand (task 10.2, licensed half).

Nothing is faked here: no ``SCITOOLS_HOOK_FAKE_UNDERSTAND``, no fixture snapshot, no stand-in
for ``und``. The repository holds Python that a reader can see is over the nesting limit, a
real ``git commit`` runs the shim, the shim runs the installed console script, and the script
builds and analyses two real Understand databases before answering. That is the only way to
show that the fixture-backed suite beside this one is testing the same machine.

The three source versions below were **measured**, not guessed. Against the shipped defaults
and a fresh repository whose ``HEAD`` holds :data:`BASE`:

* :data:`NESTED` reports 11 blocking findings, ``routine.MaxNesting`` at 6 against a limit of
  3 among them.
* :data:`FLATTENED` reports **0** blocking findings -- which took measuring, because the
  ratchet also judges the file: an earlier "fix" that extracted a helper passed the routine
  limits and was still refused, for ``file.CountDeclFunction`` rising from 1 to 2 and
  ``file.CountLineCode`` from 2 to 4. A fix has to be no worse on *every* metric the change
  touches, and that is a property of the tool worth seeing rather than configuring away.

Skipping is deliberate and precise. The ``contract`` marker skips the module when the
developer's own environment has no licence; :func:`e2e.harness.license_problem` then asks the
same question again *in the isolated environment these tests actually use*, because pointing
``XDG_CONFIG_HOME`` at a sandbox is enough to hide the licence from ``und``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from e2e.harness import (
    Workspace,
    isolated_env,
    license_problem,
    licensed_env,
)
from scitools_hook.exit_codes import ExitCode
from scitools_hook.understand.fake import FAKE_VAR, FIXTURE_VERSION

pytestmark = pytest.mark.contract

DEEP = "pkg/deep.py"

BASE = '''"""Row helpers."""


def walk(rows):
    """Collect the truthy cells of every truthy row."""
    out = []
    for row in rows:
        if row:
            out.append(row)
    return out
'''

NESTED = '''"""Row helpers."""


def walk(rows):
    """Collect the truthy items of every truthy cell of every truthy row."""
    out = []
    for row in rows:
        if row:
            for cell in row:
                if cell:
                    for item in cell:
                        if item:
                            out.append(item)
    return out
'''

FLATTENED = '''"""Row helpers."""


def walk(rows):
    """Collect the truthy items of every truthy cell of every truthy row."""
    return [item for row in rows if row for cell in row if cell for item in cell if item]
'''


@pytest.fixture
def licensed(tmp_path: Path) -> Workspace:
    """A repository holding :data:`BASE`, in an environment a real Understand answers from."""
    env = licensed_env(tmp_path)
    problem = license_problem(env)
    if problem:
        pytest.skip(f"the isolated environment has no usable Understand licence: {problem}")
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    space = Workspace(root=root, sandbox=tmp_path, env=env)
    space.git_ok("-c", "init.defaultBranch=main", "init", "--quiet")
    space.git_ok("config", "user.name", "Gate End To End")
    space.git_ok("config", "user.email", "gate@example.invalid")
    space.git_ok("config", "commit.gpgsign", "false")
    space.write(DEEP, BASE)
    space.stage(DEEP)
    space.git_ok("commit", "--quiet", "-m", "baseline")
    return space


def both_streams(stdout: str, stderr: str) -> str:
    """A commit's output as the developer sees it; git folds a hook's stdout into stderr."""
    return f"{stdout}\n{stderr}"


def test_the_isolated_licensed_environment_is_still_sealed(tmp_path: Path) -> None:
    """The licence is reached by one symlink, not by inheriting the developer's environment.

    Everything :func:`isolated_env` guards is still guarded; the only addition is a link to
    the ``SciTools`` configuration directory, which is where the licence lives. The Gate's own
    user configuration -- ``scitools-hook/config.toml`` under the same root -- is *not* linked,
    so a developer's own thresholds cannot reach one of these runs.
    """
    env = licensed_env(tmp_path)
    plain = isolated_env(tmp_path)
    for name in ("HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "GIT_CONFIG_GLOBAL"):
        assert env[name] == plain[name]
        assert Path(env[name]).is_relative_to(tmp_path)
    assert FAKE_VAR not in env
    assert not (Path(env["XDG_CONFIG_HOME"]) / "scitools-hook").exists()


def test_a_real_commit_is_blocked_and_then_passes_with_the_real_adapters(
    licensed: Workspace,
) -> None:
    """The deliverable again, with nothing faked: real Understand, real hook, real commits."""
    installed = licensed.cli("install-hook")
    assert installed.returncode == int(ExitCode.OK), installed.stderr

    licensed.write(DEEP, NESTED)
    licensed.stage(DEEP)
    blocked = licensed.commit("nest the walk six deep")

    assert blocked.returncode != int(ExitCode.OK)
    assert licensed.log() == ["baseline"], "the blocked commit must not have happened"
    printed = both_streams(blocked.stdout, blocked.stderr)
    assert "routine.MaxNesting" in printed
    assert "extract the inner block into its own routine" in printed

    licensed.write(DEEP, FLATTENED)
    licensed.stage(DEEP)
    passed = licensed.commit("flatten the walk into one comprehension")

    assert passed.returncode == int(ExitCode.OK), both_streams(passed.stdout, passed.stderr)
    assert licensed.log() == ["flatten the walk into one comprehension", "baseline"]


def test_the_agent_checks_the_worktree_before_staging_with_the_real_adapters(
    licensed: Workspace,
) -> None:
    """Requirement 10.5 against the real adapters, with the run naming the build that answered.

    ``understand_version`` is the marker: it is the installed build's own string, so a run
    that had quietly fallen back to the fixture seam would report
    ``(Build 0000 fixture)`` and fail here instead of passing as a licensed run.
    """
    licensed.write(DEEP, NESTED)

    unstaged = licensed.cli("check", "--worktree", "--format", "json")
    assert unstaged.returncode == int(ExitCode.VIOLATIONS), unstaged.stderr
    document = dict(json.loads(unstaged.stdout))
    assert document["selection"] == "worktree"
    assert document["repo_root"] == str(licensed.root)
    assert document["understand_version"] not in ("", FIXTURE_VERSION)
    rules = {str(finding["rule"]) for finding in document["findings"]}  # type: ignore[index]
    assert "routine.MaxNesting" in rules

    staged = licensed.cli("check", "--staged", "--format", "json")
    assert staged.returncode == int(ExitCode.OK), staged.stderr
    assert dict(json.loads(staged.stdout))["analyzed_files"] == 0
