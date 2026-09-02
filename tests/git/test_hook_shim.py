"""The pre-commit shim, executed by a real POSIX shell (task 7.3, requirements 11.4, 11.5).

This module runs the shipped shell script. Nothing here reads its text and reasons about it:
the shim is the piece that decides whether a commit is blocked, and the only honest way to
ask what it decides is to run it and look at the status it exits with.

Three properties earn most of the tests, because each fails silently in the direction that
costs the most:

* **Exit status 1 always blocks.** Soft-failing on it would let real violations through with
  a warning nobody reads. The matrix below is generated from :class:`ExitCode` itself, so an
  exit code added later arrives with its own two cases rather than being quietly uncovered.
* **Every status of 2 and above soft-fails when asked to.** Not soft-failing on 3 blocks
  every commit on a machine without Understand -- requirement 11.4 exists to prevent exactly
  that -- and the "the Gate is not installed at all" branch is part of that promise, not an
  exception to it, so it is tested with the same two states.
* **A chained hook keeps its own exit status and its own standard input.** The shim runs the
  Gate with ``</dev/null`` so a chained hook that reads standard input still finds it; that
  redirect is invisible in any test that does not have both halves read.

**Every stub leaves a marker file.** A stub that fails before the code under test is reached
is one of this project's recorded false-green shapes -- a test then passes while proving
nothing -- so "the Gate did not run" and "the Gate ran and answered 0" are told apart by a
file on disk rather than by an exit status that both would produce.

**Every run is wrapped in an external ``timeout``.** A shim that blocks -- waiting on standard
input it should not have consumed -- would otherwise stall the suite or, worse, be killed by
an in-process alarm and read as a clean refusal.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from scitools_hook.exit_codes import ExitCode
from scitools_hook.git import hooks

SHELLS = tuple(shell for shell in ("/bin/sh", "/bin/dash", "/bin/bash") if Path(shell).exists())
"""Every POSIX shell on this machine, so a bashism is caught by the one that refuses it.

``/bin/sh`` is dash here, which is the shell that matters: bash accepts ``[[``, ``==`` inside
``[`` and ``local`` without complaint, so a bash-only run would certify a script that fails
on the shell the shebang names.
"""

TIMEOUT_COMMAND = shutil.which("timeout")
"""GNU ``timeout``; the shim is measured from outside, never by an alarm inside the process."""

RUN_LIMIT_S = 20
"""Ceiling for one shim run. Anything approaching this is a hang, not a slow machine."""

TIMEOUT_STATUS = 124
"""What ``timeout`` exits with when it had to kill the command; not an ``ExitCode`` value."""

CHAINED_STATUS = 42
"""A status no Gate code uses, so "the chained hook's status won" cannot be read from ours."""


def test_at_least_one_posix_shell_is_available() -> None:
    """Guard the parametrisation: an empty shell list would make every test below vacuous."""
    assert "/bin/sh" in SHELLS


def test_an_external_timeout_is_available() -> None:
    """Guard the harness: without ``timeout`` a hang would stall the suite instead of failing."""
    assert TIMEOUT_COMMAND is not None


@dataclass(frozen=True)
class Shim:
    """One rendered shim on disk, with the PATH it sees and the markers its stubs leave."""

    path: Path
    bindir: Path
    records: Path

    @property
    def chained(self) -> Path:
        """Where the shim looks for the hook it replaced."""
        return self.path.with_name(self.path.name + hooks.CHAINED_SUFFIX)

    def record(self, name: str) -> Path:
        """The marker file a stub called ``name`` writes when it runs."""
        return self.records / name

    def ran(self, name: str) -> bool:
        """Whether the stub called ``name`` actually ran during the last invocation."""
        return self.record(name).exists()

    def arguments(self, name: str) -> str:
        """The arguments the stub called ``name`` was given, one line per invocation."""
        return self.record(name).read_text(encoding="utf-8").strip()

    def stdin_of(self, name: str) -> str:
        """Whatever the stub called ``name`` read from standard input."""
        return self.record(f"{name}.stdin").read_text(encoding="utf-8")

    def stub(self, name: str, status: int = 0, *, read_stdin: bool = True) -> Path:
        """Put an executable ``name`` on the shim's PATH that records its call and exits."""
        return self._script(self.bindir / name, name, status, read_stdin=read_stdin)

    def chain(self, status: int = 0, *, executable: bool = True) -> Path:
        """Put the hook the shim should hand over to beside the shim itself."""
        script = self._script(self.chained, "chained", status, read_stdin=True)
        if not executable:
            script.chmod(0o644)
        return script

    def _script(self, path: Path, name: str, status: int, *, read_stdin: bool) -> Path:
        """Write a stub that records its call and exits, using shell builtins only.

        ``cat`` is deliberately not used: these stubs run with a PATH holding nothing but the
        stub directory, so an external command is not found -- and a redirection creates its
        file whether or not the command runs, which made a "the Gate read nothing" assertion
        pass for the wrong reason until the chained half of the same test failed. ``printf``
        and ``read`` are builtins in dash and bash alike.
        """
        record = self.record(name)
        stdin_record = self.record(f"{name}.stdin")
        stdin = (
            f": > '{stdin_record}'\n"
            f"while IFS= read -r line; do printf '%s\\n' \"$line\" >> '{stdin_record}'; done\n"
            if read_stdin
            else ""
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{record}'\n{stdin}exit {status}\n",
            encoding="utf-8",
        )
        path.chmod(0o755)
        return path


@pytest.fixture
def shim(tmp_path: Path) -> Shim:
    """A rendered shim with an empty PATH of its own and nowhere else to find a command."""
    bindir = tmp_path / "bin"
    records = tmp_path / "records"
    bindir.mkdir()
    records.mkdir()
    path = tmp_path / "hooks" / "pre-commit"
    path.parent.mkdir()
    path.write_text(hooks.render(hooks.RESOLVED_DIRECT), encoding="utf-8")
    path.chmod(0o755)
    return Shim(path=path, bindir=bindir, records=records)


def run(
    shim: Shim,
    *,
    shell: str | None = "/bin/sh",
    arguments: tuple[str, ...] = (),
    stdin: bytes = b"",
    **environment: str,
) -> subprocess.CompletedProcess[bytes]:
    """Run the shim under ``shell`` (or through its own shebang when ``shell`` is ``None``).

    The environment is built from nothing but ``PATH`` and what the caller names, so a
    variable the developer happens to have exported cannot change the answer.
    """
    assert TIMEOUT_COMMAND is not None
    launcher = [str(shim.path)] if shell is None else [shell, str(shim.path)]
    finished = subprocess.run(
        [TIMEOUT_COMMAND, str(RUN_LIMIT_S), *launcher, *arguments],
        input=stdin,
        capture_output=True,
        env={"PATH": str(shim.bindir), **environment},
        cwd=shim.path.parent,
        timeout=RUN_LIMIT_S * 3,
        check=False,
    )
    assert finished.returncode != TIMEOUT_STATUS, (
        f"the shim did not finish within {RUN_LIMIT_S}s: {finished.stderr!r}"
    )
    return finished


def errors(finished: subprocess.CompletedProcess[bytes]) -> str:
    """What the run printed on standard error, as text."""
    return finished.stderr.decode("utf-8", errors="replace")


# --- the shell the shim is written in -------------------------------------------


@pytest.mark.parametrize("shell", SHELLS)
def test_the_shim_is_accepted_by_every_shell_on_this_machine(shim: Shim, shell: str) -> None:
    """``sh -n`` parses without running, so a bashism fails here rather than at commit time."""
    checked = subprocess.run([shell, "-n", str(shim.path)], capture_output=True, check=False)
    assert checked.returncode == 0, checked.stderr.decode("utf-8", errors="replace")


def test_the_template_carries_no_shell_constructs_that_dash_rejects(shim: Shim) -> None:
    """A second, independent reading of the same property, so a broken ``sh -n`` is visible.

    ``sh -n`` is a syntax check: ``local x`` parses fine under dash and fails only when the
    line runs, so the parse alone cannot see it. These three are the constructs the task
    names, and each is a word search rather than a parse.
    """
    text = shim.path.read_text(encoding="utf-8")
    body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "[[" not in body
    assert " local " not in body
    assert "==" not in body


# --- what a status means (requirement 11.4) -------------------------------------


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("code", list(ExitCode), ids=lambda code: code.name)
def test_a_gate_status_blocks_or_passes_by_the_documented_rule(
    shim: Shim, shell: str, code: ExitCode
) -> None:
    """Every documented exit code, in both soft-fail states, under every shell.

    The rule is requirement 11.4's, written out here independently of the shell script that
    implements it: 0 passes, 1 always blocks, and everything from 2 up is an infrastructure
    failure that blocks by default and warns when ``SCITOOLS_HOOK_SOFT_FAIL`` is set.
    Generating the cases from :class:`ExitCode` means a code added to the enum later cannot
    ship without an answer here -- code 7 arrived that way.
    """
    shim.stub("scitools-hook", status=int(code))

    strict = run(shim, shell=shell)
    lenient = run(shim, shell=shell, SCITOOLS_HOOK_SOFT_FAIL="1")

    assert shim.ran("scitools-hook"), "the stub never ran, so nothing below was measured"
    assert strict.returncode == int(code)
    assert lenient.returncode == (int(code) if code <= ExitCode.VIOLATIONS else 0)


def test_the_soft_fail_boundary_sits_between_one_and_two(shim: Shim) -> None:
    """The two statuses either side of the boundary, asserted as literals (req 11.4).

    The matrix above derives its expectation from ``code <= 1``; if that expression were
    wrong in the same direction as the shim, both would agree. These two cases name the
    numbers instead: 1 is findings and must survive soft-fail, 2 is a broken configuration
    and must not.
    """
    shim.stub("scitools-hook", status=1)
    assert run(shim, SCITOOLS_HOOK_SOFT_FAIL="1").returncode == 1

    shim.stub("scitools-hook", status=2)
    assert run(shim, SCITOOLS_HOOK_SOFT_FAIL="1").returncode == 0


def test_soft_fail_says_it_let_the_commit_through(shim: Shim) -> None:
    """A warning nobody prints is a silent pass; requirement 11.4 asks for the warning."""
    shim.stub("scitools-hook", status=int(ExitCode.UNDERSTAND_NOT_FOUND))
    finished = run(shim, SCITOOLS_HOOK_SOFT_FAIL="1")
    assert "SCITOOLS_HOOK_SOFT_FAIL" in errors(finished)
    assert "3" in errors(finished)


def test_a_blocked_commit_names_the_variable_that_would_unblock_it(shim: Shim) -> None:
    """Requirement 11.4's message: clear about what happened and about the way out."""
    shim.stub("scitools-hook", status=int(ExitCode.ANALYSIS_FAILED))
    finished = run(shim)
    assert "blocked" in errors(finished)
    assert "SCITOOLS_HOOK_SOFT_FAIL" in errors(finished)


def test_an_empty_soft_fail_variable_is_not_set(shim: Shim) -> None:
    """Blank means unset everywhere else in the Gate, and it means unset here too."""
    shim.stub("scitools-hook", status=int(ExitCode.CONFIG_ERROR))
    assert run(shim, SCITOOLS_HOOK_SOFT_FAIL="").returncode == int(ExitCode.CONFIG_ERROR)


# --- skipping one commit (requirement 11.5) -------------------------------------


def test_the_skip_variable_skips_the_check_and_says_so(shim: Shim) -> None:
    """Requirement 11.5: skip for one commit, and print a notice that it was skipped."""
    shim.stub("scitools-hook", status=int(ExitCode.VIOLATIONS))
    finished = run(shim, SCITOOLS_HOOK_SKIP="1")
    assert finished.returncode == 0
    assert not shim.ran("scitools-hook"), "the Gate ran although the check was to be skipped"
    assert "SCITOOLS_HOOK_SKIP" in errors(finished)
    assert "skip" in errors(finished).lower()


def test_an_empty_skip_variable_is_not_set(shim: Shim) -> None:
    """A distinct input from the case above, and the opposite outcome."""
    shim.stub("scitools-hook", status=int(ExitCode.VIOLATIONS))
    assert run(shim, SCITOOLS_HOOK_SKIP="").returncode == int(ExitCode.VIOLATIONS)
    assert shim.ran("scitools-hook")


def test_skipping_the_gate_does_not_skip_somebody_elses_hook(shim: Shim) -> None:
    """A deliberate reading of requirement 11.5, recorded in the shim's own header.

    ``SCITOOLS_HOOK_SKIP`` is namespaced to this tool, so it turns off this tool. Using it to
    silently disable an unrelated hook the operator had before the Gate arrived would be a
    coverage loss under our name; ``git commit --no-verify`` is what skips everything.
    """
    shim.stub("scitools-hook", status=0)
    shim.chain(status=CHAINED_STATUS)
    assert run(shim, SCITOOLS_HOOK_SKIP="1").returncode == CHAINED_STATUS
    assert shim.ran("chained")


# --- finding the Gate -----------------------------------------------------------


def test_the_gate_is_invoked_in_staged_mode(shim: Shim) -> None:
    """Requirement 11.1: the hook runs the Gate over the staged change, not the worktree."""
    shim.stub("scitools-hook", status=0)
    run(shim)
    assert shim.arguments("scitools-hook") == "check --staged"


def test_uvx_runs_the_gate_when_it_is_not_installed(shim: Shim) -> None:
    """Requirement 12.2's promise reached from a hook: no virtualenv on the developer's part."""
    shim.stub("uvx", status=0)
    finished = run(shim)
    assert finished.returncode == 0
    assert shim.arguments("uvx") == "scitools-hook check --staged"


def test_the_uvx_branch_reports_the_status_it_was_given(shim: Shim) -> None:
    """The uvx arm is a sibling of the installed one, and needs its own evidence.

    "Fixed on one branch, missed on the sibling" is this project's most repeated defect
    shape. A status captured on the installed arm and dropped on this one would make every
    failure on a machine without the tool installed read as a clean commit.
    """
    shim.stub("uvx", status=int(ExitCode.LICENSE_UNAVAILABLE))
    assert run(shim).returncode == int(ExitCode.LICENSE_UNAVAILABLE)


def test_the_uvx_branch_leaves_standard_input_for_the_chained_hook(shim: Shim) -> None:
    """The other half of the same sibling: a redirect written on one arm only.

    The shim redirects the whole construct rather than each branch, so there is no second
    place to forget -- this test is what makes that structural claim measured rather than
    asserted.
    """
    shim.stub("uvx", status=0)
    shim.chain(status=0)
    run(shim, stdin=b"payload-for-the-chained-hook\n")
    assert shim.stdin_of("uvx") == ""
    assert shim.stdin_of("chained") == "payload-for-the-chained-hook\n"


def test_an_installed_gate_is_preferred_to_uvx(shim: Shim) -> None:
    """Both available: the installed one wins, and uvx is not started at all."""
    shim.stub("scitools-hook", status=0)
    shim.stub("uvx", status=0)
    run(shim)
    assert shim.ran("scitools-hook")
    assert not shim.ran("uvx")


def test_neither_command_available_blocks_with_a_message(shim: Shim) -> None:
    """Requirement 11.4's other half: the Gate itself missing must block, loudly."""
    finished = run(shim)
    assert finished.returncode == int(ExitCode.UNDERSTAND_NOT_FOUND)
    assert "scitools-hook" in errors(finished)
    assert "uvx" in errors(finished)
    assert "PATH" in errors(finished)


def test_neither_command_available_still_honours_soft_fail(shim: Shim) -> None:
    """The tool being absent is an infrastructure failure like any other (requirement 11.4).

    Written as its own case because the branch that produces this status never runs the
    Gate, so it could easily exit before the soft-fail decision -- which would leave every
    commit on a machine without the tool blocked with no way out but ``--no-verify``.
    """
    finished = run(shim, SCITOOLS_HOOK_SOFT_FAIL="1")
    assert finished.returncode == 0
    assert "SCITOOLS_HOOK_SOFT_FAIL" in errors(finished)


# --- chaining to the hook that was here first (requirement 11.2) ----------------


def test_a_chained_hook_runs_after_a_clean_check_and_keeps_its_status(shim: Shim) -> None:
    """The operator's own hook still decides, and its verdict is the commit's verdict."""
    shim.stub("scitools-hook", status=0)
    shim.chain(status=CHAINED_STATUS)
    assert run(shim).returncode == CHAINED_STATUS
    assert shim.ran("chained")


def test_a_clean_run_without_a_chained_hook_exits_zero(shim: Shim) -> None:
    """The ordinary case: nothing was chained, so the Gate's own verdict stands."""
    shim.stub("scitools-hook", status=0)
    assert run(shim).returncode == 0
    assert not shim.chained.exists()


def test_findings_block_before_the_chained_hook_runs(shim: Shim) -> None:
    """The commit is already rejected; running the next hook could only muddy the status."""
    shim.stub("scitools-hook", status=int(ExitCode.VIOLATIONS))
    shim.chain(status=0)
    assert run(shim).returncode == int(ExitCode.VIOLATIONS)
    assert not shim.ran("chained")


def test_an_infrastructure_failure_blocks_before_the_chained_hook_runs(shim: Shim) -> None:
    """Same reasoning at the other status class, and a distinct input from the case above."""
    shim.stub("scitools-hook", status=int(ExitCode.LICENSE_UNAVAILABLE))
    shim.chain(status=0)
    assert run(shim).returncode == int(ExitCode.LICENSE_UNAVAILABLE)
    assert not shim.ran("chained")


def test_a_soft_failed_run_still_hands_over_to_the_chained_hook(shim: Shim) -> None:
    """Soft-fail means "carry on", and carrying on includes the hook we replaced."""
    shim.stub("scitools-hook", status=int(ExitCode.LICENSE_UNAVAILABLE))
    shim.chain(status=CHAINED_STATUS)
    assert run(shim, SCITOOLS_HOOK_SOFT_FAIL="1").returncode == CHAINED_STATUS
    assert shim.ran("chained")


def test_the_chained_hook_receives_standard_input_and_the_gate_does_not(shim: Shim) -> None:
    """The Gate is run with ``</dev/null`` so it cannot eat a chained hook's input.

    Both halves are asserted, because either alone is satisfied by the wrong code: with the
    redirect removed the Gate stub swallows the payload and the chained hook finds nothing,
    and a test that only looked at the chained side would pass whenever the Gate happened not
    to read.
    """
    shim.stub("scitools-hook", status=0)
    shim.chain(status=0)
    run(shim, stdin=b"payload-for-the-chained-hook\n")
    assert shim.stdin_of("scitools-hook") == ""
    assert shim.stdin_of("chained") == "payload-for-the-chained-hook\n"


def test_the_chained_hook_receives_the_arguments_the_shim_was_given(shim: Shim) -> None:
    """Git passes a pre-commit hook none (measured), so this only ever adds, never removes."""
    shim.stub("scitools-hook", status=0)
    shim.chain(status=0)
    run(shim, arguments=("--first", "second"))
    assert shim.arguments("chained") == "--first second"


def test_a_chained_hook_that_is_not_executable_is_reported_not_run(shim: Shim) -> None:
    """Git would silently skip it; the shim says so instead, and does not block over it."""
    shim.stub("scitools-hook", status=0)
    shim.chain(status=CHAINED_STATUS, executable=False)
    finished = run(shim)
    assert finished.returncode == 0
    assert not shim.ran("chained")
    assert "not an executable file" in errors(finished)


def test_the_chained_hook_is_found_beside_the_shim_whatever_the_working_directory(
    shim: Shim, tmp_path: Path
) -> None:
    """The path comes from ``$0``, so moving the repository cannot orphan the chained hook.

    Git runs a hook with the working directory set to the worktree root and ``$0`` set to the
    path it used -- ``.git/hooks/pre-commit`` relative to that root, or an absolute path under
    ``core.hooksPath`` (both measured on git 2.43.0). Deriving the chained name from ``$0``
    rather than embedding a path at install time is what makes both cases work.
    """
    shim.stub("scitools-hook", status=0)
    shim.chain(status=CHAINED_STATUS)
    elsewhere = tmp_path / "somewhere-else"
    elsewhere.mkdir()
    assert TIMEOUT_COMMAND is not None
    finished = subprocess.run(
        [TIMEOUT_COMMAND, str(RUN_LIMIT_S), "/bin/sh", str(shim.path)],
        capture_output=True,
        env={"PATH": str(shim.bindir)},
        cwd=elsewhere,
        timeout=RUN_LIMIT_S * 3,
        check=False,
    )
    assert finished.returncode == CHAINED_STATUS


# --- the file as git will run it ------------------------------------------------


def test_the_shim_runs_through_its_own_shebang(shim: Shim) -> None:
    """Git executes the file; it does not hand it to a shell, so the shebang has to work."""
    shim.stub("scitools-hook", status=int(ExitCode.VIOLATIONS))
    assert run(shim, shell=None).returncode == int(ExitCode.VIOLATIONS)
    assert shim.ran("scitools-hook")


def test_a_real_commit_is_blocked_and_then_allowed(tmp_path: Path, shim: Shim) -> None:
    """End to end through git itself: the one test that proves git runs this file at all.

    Everything else here drives the shim directly. This one installs it where git looks,
    commits, and reads git's own verdict -- with the Gate answering 1 and then 0 from the same
    stub, so a green result cannot come from the hook never having run.
    """
    repository = tmp_path / "repository"
    hooks_directory = repository / ".git" / "hooks"
    environment = {
        "PATH": f"{shim.bindir}:{os.environ.get('PATH', '')}",
        "HOME": str(tmp_path / "home"),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Gate Test",
        "GIT_AUTHOR_EMAIL": "gate@example.invalid",
        "GIT_COMMITTER_NAME": "Gate Test",
        "GIT_COMMITTER_EMAIL": "gate@example.invalid",
    }

    def git(*arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(repository), *arguments],
            capture_output=True,
            text=True,
            env=environment,
            check=False,
        )

    repository.mkdir()
    assert git("init", "--quiet", "-b", "main").returncode == 0
    (repository / "a.py").write_text("x = 1\n", encoding="utf-8")
    assert git("add", "a.py").returncode == 0
    shutil.copy(shim.path, hooks_directory / "pre-commit")
    (hooks_directory / "pre-commit").chmod(0o755)

    shim.stub("scitools-hook", status=int(ExitCode.VIOLATIONS))
    blocked = git("commit", "-m", "blocked")
    assert blocked.returncode != 0
    assert git("rev-parse", "--verify", "--quiet", "HEAD").returncode != 0

    shim.record("scitools-hook").unlink()
    shim.stub("scitools-hook", status=0)
    allowed = git("commit", "-m", "allowed")
    assert allowed.returncode == 0, allowed.stderr
    assert shim.arguments("scitools-hook") == "check --staged"
