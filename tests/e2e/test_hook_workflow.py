"""The developer's path, end to end: install the hook, be blocked, fix, commit (task 10.2).

Every test here drives a **real** ``git commit`` against the installed console script. That
is the point of the task: a simulation of a blocked commit proves that the renderer can print
a refusal, while a real one proves that git ran the shim, the shim ran the Gate, the Gate read
the index, and the commit did not happen. So the assertions are on the commit's exit status
and on ``git log`` afterwards, not only on what was printed.

Two measured facts about the environment shape what is asserted, and both are recorded here
rather than rediscovered:

* **git 2.43.0 folds a pre-commit hook's standard output into its own standard error.**
  Measured: a blocked commit left ``stdout`` empty and the whole report -- findings, hints
  and the agent block -- on ``stderr``. Which stream carries it is git's decision, not the
  Gate's, so these tests assert on the two joined and requirement 7.7's separation is
  asserted where the Gate owns it, in :mod:`test_agent_workflow`.

* **``SCITOOLS_HOOK_SKIP`` skips the Gate's check and still runs a chained hook.** That is a
  deliberate deviation from design.md:635, already recorded in tasks.md, and the shim says so
  in its own header: the variable turns off the Gate, not somebody else's hook.
  ``git commit --no-verify`` is what skips every hook.
"""

from __future__ import annotations

import shutil
import stat
import subprocess
from pathlib import Path

from e2e.harness import (
    DEEP,
    FIXED,
    NESTED,
    SKIP_VAR,
    SOFT_FAIL_VAR,
    VIOLATING,
    Workspace,
    cli_executable,
    git_executable,
    isolated_env,
    make_workspace,
    missing_tool_path,
)
from scitools_hook.exit_codes import ExitCode
from scitools_hook.git.hooks import CHAINED_SUFFIX, HOOK_NAME, MARKER, TEMPLATE_PATH
from scitools_hook.report.human import AGENT_HEADER
from scitools_hook.understand.fake import FAKE_VAR

BLOCKED_HINT = "extract the inner block into its own routine"
"""The remediation hint requirement 7.2 attaches to ``routine.MaxNesting``."""

COULD_NOT_RUN = "the check could not run"
"""What the shim says about every status above 1 (req 11.4)."""


def both_streams(done: subprocess.CompletedProcess[str]) -> str:
    """A commit's output as the developer sees it, whichever stream git chose."""
    return f"{done.stdout}\n{done.stderr}"


def install(space: Workspace, *argv: str) -> str:
    """Install the shim and return what the command said it did."""
    done = space.cli("install-hook", *argv)
    assert done.returncode == int(ExitCode.OK), done.stderr
    return done.stdout


def chain_a_legacy_hook(space: Workspace) -> Path:
    """Put a hook the Gate did not write in place, chain to it, and return its marker path.

    The marker is created with a bare redirection and nothing else: a stub that needs an
    external command fails silently on a narrowed ``PATH`` while ``>`` still creates the file,
    which is how task 7.3's own stub passed without running. With ``: >`` the redirection *is*
    the whole action, so the file existing is the same event as the hook running.
    """
    marker = space.sandbox / "chained-ran"
    hook = space.root / ".git" / "hooks" / HOOK_NAME
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(f"#!/bin/sh\n: > {marker}\nexit 0\n", encoding="utf-8")
    hook.chmod(0o755)

    refused = space.cli("install-hook")
    assert refused.returncode == int(ExitCode.CONFIG_ERROR), refused.stderr
    assert "--force" in refused.stderr
    assert str(hook) in install(space, "--force")
    assert (space.root / ".git" / "hooks" / (HOOK_NAME + CHAINED_SUFFIX)).is_file()
    assert not marker.exists(), "the chained hook must not have run during installation"
    return marker


# --- the harness proves itself first -----------------------------------------------


def test_the_isolated_environment_sets_every_variable_that_could_reach_the_real_home(
    tmp_path: Path,
) -> None:
    """The guard in :func:`isolated_env` is the only thing between a test and a real hook."""
    env = isolated_env(tmp_path)
    for name in ("HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "GIT_CONFIG_GLOBAL"):
        assert Path(env[name]).is_relative_to(tmp_path)
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"


def test_the_default_path_reaches_the_gate_and_git(tmp_path: Path) -> None:
    """A harness that hid either one would make every commit below fail for the wrong reason.

    Which Understand a run talks to is not decided here: these tests set
    ``SCITOOLS_HOOK_FAKE_UNDERSTAND``, and ``runner.context.build_adapters`` consults it
    *before* it discovers anything, so no installation on ``PATH`` can reach one of them.
    ``test_agent_workflow`` asserts the fixture build string back out of a report to show it.
    """
    path = isolated_env(tmp_path)["PATH"]
    assert shutil.which("scitools-hook", path=path) == str(cli_executable())
    found = shutil.which("git", path=path)
    assert found is not None and Path(found).resolve() == Path(git_executable()).resolve()


def test_the_narrowed_path_reaches_git_but_not_the_gate(tmp_path: Path) -> None:
    """Requirement 11.4's "the tool is not installed", produced on purpose and not by accident.

    Emptying ``PATH`` would also hide ``git``, so the run would fail before the shim decided
    anything -- the trap this project has walked into five times.
    """
    path = missing_tool_path(tmp_path)
    found = shutil.which("git", path=path)
    assert found is not None and Path(found).resolve() == Path(git_executable()).resolve()
    assert shutil.which("scitools-hook", path=path) is None
    assert shutil.which("uvx", path=path) is None


def test_the_shipped_shim_reads_the_two_variables_this_suite_sets() -> None:
    """A rename in the template must not leave these tests exporting a variable nothing reads."""
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    assert SKIP_VAR in template
    assert SOFT_FAIL_VAR in template


# --- the deliverable: a real commit blocked, then a real commit that succeeds ---------


def test_a_real_commit_is_blocked_and_then_succeeds_once_the_change_is_fixed(
    workspace: Workspace,
) -> None:
    """Install the hook, stage a routine over the nesting limit, commit, fix, commit again.

    The "fix" is re-pointing the seam at the ``fixed`` fixture directory, which is what the
    task specifies: the fixtures, not the file's text, are what Understand would have
    measured. The file is rewritten as well so that the repository tells the same story.
    """
    installed = install(workspace)
    hook = workspace.root / ".git" / "hooks" / HOOK_NAME
    assert str(hook) in installed
    assert MARKER in hook.read_text(encoding="utf-8")
    assert hook.stat().st_mode & stat.S_IXUSR

    workspace.write(DEEP, NESTED)
    workspace.stage(DEEP)
    blocked = workspace.commit("nest the walk five deep")

    assert blocked.returncode == int(ExitCode.VIOLATIONS)
    assert workspace.log() == ["baseline"], "the blocked commit must not have happened"
    printed = both_streams(blocked)
    assert "routine.MaxNesting" in printed
    assert BLOCKED_HINT in printed

    workspace.write(DEEP, "def walk(rows):\n    return list(rows)\n")
    workspace.stage(DEEP)
    passed = workspace.commit("flatten the walk", env=workspace.with_env(**{FAKE_VAR: str(FIXED)}))

    assert passed.returncode == int(ExitCode.OK), both_streams(passed)
    assert workspace.log() == ["flatten the walk", "baseline"]


def test_a_blocked_commit_ends_with_the_instruction_block_an_agent_reads(
    workspace: Workspace,
) -> None:
    """Requirement 10.4: the last thing a blocked run says is how to re-run it."""
    install(workspace)
    workspace.write(DEEP, NESTED)
    workspace.stage(DEEP)
    blocked = workspace.commit("nest the walk five deep")

    assert blocked.returncode == int(ExitCode.VIOLATIONS)
    printed = both_streams(blocked).rstrip()
    assert AGENT_HEADER in printed
    assert printed.endswith("--format json carries the same hints.")
    assert "scitools-hook check --worktree" in printed
    assert "scitools-hook check --staged" in printed


# --- the two documented environment variables (req 11.4, 11.5) -----------------------


def test_the_skip_variable_bypasses_the_check_and_says_so(workspace: Workspace) -> None:
    """Requirement 11.5: one commit goes through, and the developer is told why."""
    install(workspace)
    workspace.write(DEEP, NESTED)
    workspace.stage(DEEP)

    skipped = workspace.commit("nest it anyway", env=workspace.with_env(**{SKIP_VAR: "1"}))

    assert skipped.returncode == int(ExitCode.OK), both_streams(skipped)
    assert workspace.log() == ["nest it anyway", "baseline"]
    assert f"skipped because {SKIP_VAR} is set" in both_streams(skipped)
    assert "routine.MaxNesting" not in both_streams(skipped), "the check must not have run"


def test_the_skip_variable_still_runs_the_hook_the_shim_replaced(workspace: Workspace) -> None:
    """The recorded deviation from design.md:635, tested as it behaves rather than as designed.

    The variable turns off the Gate, not a hook somebody else installed; ``--no-verify`` is
    what turns off every hook.
    """
    marker = chain_a_legacy_hook(workspace)
    workspace.write(DEEP, NESTED)
    workspace.stage(DEEP)

    skipped = workspace.commit("nest it anyway", env=workspace.with_env(**{SKIP_VAR: "1"}))

    assert skipped.returncode == int(ExitCode.OK), both_streams(skipped)
    assert marker.is_file(), "the chained hook did not run"


def test_a_blocking_finding_stops_the_shim_before_the_chained_hook(workspace: Workspace) -> None:
    """A chained hook's own success must not become the commit's answer.

    This is the case that makes the shim's ``1) exit 1`` arm load-bearing rather than
    decorative: with no chained hook the shim ends at ``exit "$status"`` and reaches the same
    answer either way, so only a chained hook that exits 0 can tell the two apart. Measured
    both ways -- with the arm removed and a chain installed, the violating commit went
    through.
    """
    marker = chain_a_legacy_hook(workspace)
    workspace.write(DEEP, NESTED)
    workspace.stage(DEEP)

    blocked = workspace.commit("nest the walk five deep")

    assert blocked.returncode != int(ExitCode.OK)
    assert workspace.log() == ["baseline"]
    assert "routine.MaxNesting" in both_streams(blocked)
    assert not marker.exists(), "a blocking finding must not reach the chained hook"


def test_an_infrastructure_failure_stops_before_the_chained_hook_unless_soft_fail_is_set(
    workspace: Workspace,
) -> None:
    """The same asymmetry on the ``*)`` arm, which soft-fail is allowed to turn into a warning."""
    marker = chain_a_legacy_hook(workspace)
    workspace.write("scitools-hook.toml", '[ignore]\nfiles = ["("]\n')
    workspace.write(DEEP, NESTED)
    workspace.stage(DEEP)

    blocked = workspace.commit("commit with a broken configuration")
    assert blocked.returncode != int(ExitCode.OK)
    assert not marker.exists(), "a blocked infrastructure failure must not reach the chained hook"

    allowed = workspace.commit(
        "commit with a broken configuration", env=workspace.with_env(**{SOFT_FAIL_VAR: "1"})
    )
    assert allowed.returncode == int(ExitCode.OK), both_streams(allowed)
    assert marker.is_file(), "a warned-through failure must still run the chained hook"


def test_a_missing_gate_blocks_the_commit_by_default(workspace: Workspace) -> None:
    """Requirement 11.4: the Gate could not run at all, so the commit does not happen."""
    install(workspace)
    workspace.write(DEEP, NESTED)
    workspace.stage(DEEP)

    blocked = workspace.commit(
        "commit without the tool",
        env=workspace.with_env(PATH=missing_tool_path(workspace.sandbox)),
    )

    assert blocked.returncode != int(ExitCode.OK)
    assert workspace.log() == ["baseline"]
    printed = both_streams(blocked)
    assert "neither scitools-hook nor uvx is on PATH" in printed
    blocked_note = f"{COULD_NOT_RUN} (exit {int(ExitCode.UNDERSTAND_NOT_FOUND)}), so the commit is"
    assert blocked_note in printed
    assert SOFT_FAIL_VAR in printed, "the refusal must name the way out"


def test_the_soft_fail_variable_turns_a_missing_gate_into_a_warning(
    workspace: Workspace,
) -> None:
    """The same failure as above, with the documented variable set (req 11.4)."""
    install(workspace)
    workspace.write(DEEP, NESTED)
    workspace.stage(DEEP)

    allowed = workspace.commit(
        "commit without the tool",
        env=workspace.with_env(PATH=missing_tool_path(workspace.sandbox), **{SOFT_FAIL_VAR: "1"}),
    )

    assert allowed.returncode == int(ExitCode.OK), both_streams(allowed)
    assert workspace.log() == ["commit without the tool", "baseline"]
    assert f"continuing anyway because {SOFT_FAIL_VAR} is set" in both_streams(allowed)


def test_the_soft_fail_variable_also_covers_a_configuration_the_gate_cannot_read(
    workspace: Workspace,
) -> None:
    """A second, different infrastructure failure: the Gate ran and stopped at exit 2.

    The tool is on ``PATH`` here, so the shim takes its *first* branch and the status comes
    from the Gate itself rather than from the shim's own "nothing was checked" arm. Sibling
    cases need different inputs, and these two have them.
    """
    install(workspace)
    workspace.write("scitools-hook.toml", '[ignore]\nfiles = ["("]\n')
    workspace.write(DEEP, NESTED)
    workspace.stage(DEEP)

    blocked = workspace.commit("commit with a broken configuration")
    assert blocked.returncode != int(ExitCode.OK)
    assert workspace.log() == ["baseline"]
    assert f"{COULD_NOT_RUN} (exit {int(ExitCode.CONFIG_ERROR)})" in both_streams(blocked)

    allowed = workspace.commit(
        "commit with a broken configuration", env=workspace.with_env(**{SOFT_FAIL_VAR: "1"})
    )
    assert allowed.returncode == int(ExitCode.OK), both_streams(allowed)
    assert workspace.log() == ["commit with a broken configuration", "baseline"]


def test_the_soft_fail_variable_never_rescues_a_violation(workspace: Workspace) -> None:
    """The safety-critical line, at the workflow level: findings block whatever is set.

    Exit 1 is the answer the Gate exists to give, so it is not an infrastructure failure and
    ``SCITOOLS_HOOK_SOFT_FAIL`` has nothing to say about it.
    """
    install(workspace)
    workspace.write(DEEP, NESTED)
    workspace.stage(DEEP)

    blocked = workspace.commit("nest it anyway", env=workspace.with_env(**{SOFT_FAIL_VAR: "1"}))

    assert blocked.returncode == int(ExitCode.VIOLATIONS)
    assert workspace.log() == ["baseline"]
    assert "routine.MaxNesting" in both_streams(blocked)
    assert "continuing anyway" not in both_streams(blocked)


# --- the global shim, and taking the shim away again ---------------------------------


def test_a_globally_installed_shim_gates_a_real_commit(tmp_path: Path) -> None:
    """Requirement 11.9, driven to the point where it actually blocks something.

    The sandbox's *own* global git configuration names a hooks directory under ``tmp_path``,
    which is the arm ``install-hook --global`` resolves through, and every child in this test
    carries the four variables that keep the fallback arm -- ``$XDG_CONFIG_HOME/git/hooks``,
    read from the ambient environment -- inside the sandbox as well. The autouse canary in
    ``e2e.conftest`` fails the test if either arm ever reached the developer's own configuration.
    """
    hooks = tmp_path / "user-hooks"
    space = make_workspace(tmp_path, **{FAKE_VAR: str(VIOLATING)})
    space.git_ok("config", "--global", "core.hooksPath", str(hooks))

    installed = space.cli("install-hook", "--global")
    assert installed.returncode == int(ExitCode.OK), installed.stderr
    shim = hooks / HOOK_NAME
    assert shim.is_file() and str(shim) in installed.stdout
    assert shim.is_relative_to(tmp_path)
    assert not (space.root / ".git" / "hooks" / HOOK_NAME).exists()

    space.write(DEEP, NESTED)
    space.stage(DEEP)
    blocked = space.commit("nest the walk five deep")

    assert blocked.returncode == int(ExitCode.VIOLATIONS)
    assert space.log() == ["baseline"]
    assert "routine.MaxNesting" in both_streams(blocked)


def test_uninstalling_the_shim_lets_the_same_commit_through(workspace: Workspace) -> None:
    """Requirement 11.6 from the developer's side: the gate is off again, nothing else changed."""
    install(workspace)
    workspace.write(DEEP, NESTED)
    workspace.stage(DEEP)
    assert workspace.commit("nest the walk five deep").returncode == int(ExitCode.VIOLATIONS)

    removed = workspace.cli("uninstall-hook")
    assert removed.returncode == int(ExitCode.OK), removed.stderr
    assert not (workspace.root / ".git" / "hooks" / HOOK_NAME).exists()

    passed = workspace.commit("nest the walk five deep")
    assert passed.returncode == int(ExitCode.OK), both_streams(passed)
    assert workspace.log() == ["nest the walk five deep", "baseline"]
