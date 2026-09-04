"""The pre-push shim as a shell program: the ref loop, the two zero-oid cases, the handover.

The pre-commit shim's tests are in ``test_hook_shim.py`` and the harness is shared from
there. What is different here, and what these tests are about, is standard input: git sends
the refs being pushed on it, it can only be read once, and a chained pre-push hook expects
the same lines. A shim that consumed them would leave that hook believing nothing was
pushed -- invisible until somebody chains one.

An all-zero object id means one of two things and they are not the same: a zero *local* id
is a ref being deleted, and a zero *remote* id is a branch the remote does not have yet. The
first has nothing to check; the second has nothing to check it against.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from test_hook_shim import SHELLS, Shim, errors, run

from scitools_hook.git import hooks

ZERO = "0" * 40
LOCAL = "a" * 40
REMOTE = "b" * 40

PUSH = f"refs/heads/main {LOCAL} refs/heads/main {REMOTE}\n".encode()
"""One ordinary ref being pushed: git's `<local ref> <local oid> <remote ref> <remote oid>`."""


@pytest.fixture
def shim(tmp_path: Path) -> Shim:
    """A rendered pre-push shim with a PATH of its own and nowhere else to find a command."""
    bindir = tmp_path / "bin"
    records = tmp_path / "records"
    bindir.mkdir()
    records.mkdir()
    path = tmp_path / "hooks" / "pre-push"
    path.parent.mkdir()
    path.write_text(hooks.render(hooks.RESOLVED_DIRECT, hooks.PRE_PUSH_NAME), encoding="utf-8")
    path.chmod(0o755)
    return Shim(path=path, bindir=bindir, records=records)


def called(shim: Shim) -> str:
    """The arguments the stubbed Gate was called with."""
    return shim.record("scitools-hook").read_text(encoding="utf-8")


# --- the shell it is written in --------------------------------------------------


@pytest.mark.parametrize("shell", SHELLS)
def test_the_shim_is_accepted_by_every_shell_on_this_machine(shim: Shim, shell: str) -> None:
    """A here-document feeding a `while read` loop is the part most likely to be a bashism."""
    finished = subprocess.run([shell, "-n", str(shim.path)], capture_output=True, check=False)
    assert finished.returncode == 0, finished.stderr.decode()


# --- what it asks the Gate -------------------------------------------------------


def test_it_checks_the_range_between_the_remote_and_the_local_commit(shim: Shim) -> None:
    """Not `--staged`: at push time nothing is staged and the working tree is beside the point."""
    shim.stub("scitools-hook", status=0)

    finished = run(shim, stdin=PUSH)

    assert finished.returncode == 0
    assert called(shim).strip() == f"check --range {REMOTE}..{LOCAL}"


def test_findings_on_any_ref_refuse_the_push(shim: Shim) -> None:
    shim.stub("scitools-hook", status=1)

    assert run(shim, stdin=PUSH).returncode == 1


def test_every_pushed_ref_is_checked(shim: Shim) -> None:
    """A push can carry several refs, and one clean ref does not vouch for another."""
    shim.stub("scitools-hook", status=0)
    second = "c" * 40
    two = PUSH + f"refs/heads/topic {second} refs/heads/topic {REMOTE}\n".encode()

    run(shim, stdin=two)

    assert called(shim).splitlines() == [
        f"check --range {REMOTE}..{LOCAL}",
        f"check --range {REMOTE}..{second}",
    ]


# --- the two zero-oid cases, which are not the same ------------------------------


def test_a_ref_being_deleted_is_not_checked(shim: Shim) -> None:
    """A zero LOCAL id: the branch is going away, so there is nothing to judge."""
    shim.stub("scitools-hook", status=1)

    finished = run(shim, stdin=f"(delete) {ZERO} refs/heads/gone {REMOTE}\n".encode())

    assert finished.returncode == 0
    assert not shim.record("scitools-hook").exists()


def test_a_branch_the_remote_does_not_have_is_reported_rather_than_guessed_at(
    shim: Shim,
) -> None:
    """A zero REMOTE id: there is no before side, and inventing one would judge other commits."""
    shim.stub("scitools-hook", status=1)

    finished = run(shim, stdin=f"refs/heads/new {LOCAL} refs/heads/new {ZERO}\n".encode())

    assert finished.returncode == 0
    assert "new on the remote" in errors(finished)
    assert not shim.record("scitools-hook").exists()


# --- standard input belongs to the chained hook too ------------------------------


def test_the_chained_hook_is_given_the_refs_the_shim_already_read(shim: Shim) -> None:
    """The whole reason this shim reads stdin into a variable instead of streaming it."""
    shim.stub("scitools-hook", status=0)
    shim.chain(status=0)

    finished = run(shim, stdin=PUSH)

    assert finished.returncode == 0
    assert shim.stdin_of("chained") == PUSH.decode()


def test_the_chained_hook_decides_the_push_when_the_gate_is_happy(shim: Shim) -> None:
    shim.stub("scitools-hook", status=0)
    shim.chain(status=7)

    assert run(shim, stdin=PUSH).returncode == 7


def test_a_refusal_stops_before_the_chained_hook_runs(shim: Shim) -> None:
    """Blocked is blocked; running the next hook would only obscure which one refused."""
    shim.stub("scitools-hook", status=1)
    shim.chain(status=0)

    assert run(shim, stdin=PUSH).returncode == 1
    assert not shim.record("chained").exists()


# --- the two environment variables -----------------------------------------------


def test_skip_turns_off_the_gate_and_not_the_chained_hook(shim: Shim) -> None:
    shim.stub("scitools-hook", status=1)
    shim.chain(status=0)

    finished = run(shim, stdin=PUSH, SCITOOLS_HOOK_SKIP="1")

    assert finished.returncode == 0
    assert "skipped" in errors(finished)
    assert not shim.record("scitools-hook").exists()
    assert shim.record("chained").exists()


def test_an_infrastructure_failure_blocks_unless_soft_fail_is_set(shim: Shim) -> None:
    shim.stub("scitools-hook", status=4)

    assert run(shim, stdin=PUSH).returncode == 4
    assert run(shim, stdin=PUSH, SCITOOLS_HOOK_SOFT_FAIL="1").returncode == 0


def test_soft_fail_does_not_forgive_findings(shim: Shim) -> None:
    """Exit 1 is the answer the Gate exists to give, whatever the variable says."""
    shim.stub("scitools-hook", status=1)

    assert run(shim, stdin=PUSH, SCITOOLS_HOOK_SOFT_FAIL="1").returncode == 1


def test_findings_on_one_ref_are_not_masked_by_a_broken_run_on_another(shim: Shim) -> None:
    """Why the shim keeps `blocked` and `broken` apart instead of reducing to a worst status.

    A single number plus soft-fail would let one ref's missing licence excuse another ref's
    real findings.
    """
    stub = shim.bindir / "scitools-hook"
    stub.write_text(
        f'#!/bin/sh\ncase "$*" in *{LOCAL}*) exit 1 ;; *) exit 4 ;; esac\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    second = "c" * 40
    two = PUSH + f"refs/heads/topic {second} refs/heads/topic {REMOTE}\n".encode()

    assert run(shim, stdin=two, SCITOOLS_HOOK_SOFT_FAIL="1").returncode == 1
