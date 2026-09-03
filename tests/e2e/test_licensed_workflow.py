"""The same two workflows against a real, licensed Understand (task 10.2, licensed half).

Nothing is faked here: no ``SCITOOLS_HOOK_FAKE_UNDERSTAND``, no fixture snapshot, no stand-in
for ``und``. The repository holds Python that a reader can see is over the nesting limit, a
real ``git commit`` runs the shim, the shim runs the installed console script, and the script
builds and analyses two real Understand databases before answering. That is the only way to
show that the fixture-backed suite beside this one is testing the same machine.

The source versions below were **measured**, not guessed. Against the shipped defaults and a
fresh repository whose ``HEAD`` holds :data:`BASE`:

* :data:`NESTED` reports 10 blocking findings, ``routine.MaxNesting`` at 6 against a limit of
  3 among them.
* :data:`FLATTENED` reports **0** blocking findings.
* :data:`EXTRACTED` -- the *other* remedy ``MaxNesting``'s hint offers -- reports 0 blocking
  findings as well, and that is task 11.9's fix. It did not, once: the same extraction was
  refused for ``file.CountDeclFunction rose from 1 to 3`` and ``file.CountLineCode rose from
  6 to 18``, so an agent that followed the hint was blocked and the cheapest way out was to
  put the code back. Those two counts now ship without a ratchet
  (``config.models.DECOMPOSITION_COUNTS``), and
  :func:`test_switching_the_ratchet_back_on_restores_the_refusal` re-enables them through
  configuration and watches the old refusal come back word for word -- which is what makes
  the passing run above able to fail.
* :data:`REGRESSED` reports 7 blocking findings, ``routine.CountLineCode`` and
  ``routine.CountStmt`` among them. Those two *are* still ratcheted: a routine that grew
  while its own complexity grew is a regression, not a decomposition, and only the second
  reading is forgiven.

Skipping is deliberate and precise. The ``contract`` marker skips the module when the
developer's own environment has no licence; :func:`e2e.harness.license_problem` then asks the
same question again *in the isolated environment these tests actually use*, because pointing
``XDG_CONFIG_HOME`` at a sandbox is enough to hide the licence from ``und``.
"""

from __future__ import annotations

import json
import subprocess
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


OTHER = "pkg/other.py"

STEADY = '''"""Two routines that never change, so the project population is not one routine."""


def first(value):
    """Echo the value."""
    return value


def second(value):
    """Echo the value again."""
    return value
'''
"""Held constant across every version below.

Without it a project-scope average is the metric of whichever single routine is under test,
so ``project.AVG:CyclomaticStrict`` would decide these tests instead of the rule each one is
about. Measured: with this file present the average over the five routines of
:data:`EXTRACTED` is 2.2 against a limit of 3, and over :data:`REGRESSED`'s five it is 2.6.
"""

EXTRACTED = '''"""Row helpers."""


def items(cell):
    """The truthy items of one cell."""
    out = []
    for item in cell:
        if item:
            out.append(item)
    return out


def cells(row):
    """The truthy items of every truthy cell of one row."""
    out = []
    for cell in row:
        if cell:
            out.extend(items(cell))
    return out


def walk(rows):
    """Collect the truthy items of every truthy cell of every truthy row."""
    out = []
    for row in rows:
        if row:
            out.extend(cells(row))
    return out
'''
"""``NESTED`` with its two inner blocks extracted -- exactly what the MaxNesting hint says.

Every routine here has the shape ``BASE``'s ``walk`` had: MaxNesting 2, CyclomaticStrict 3,
six lines. What the change adds is *declarations*, which is the whole of task 11.9.
"""

REGRESSED = '''"""Row helpers."""


def items(cell):
    """The truthy items of one cell."""
    out = []
    for item in cell:
        if item:
            out.append(item)
    return out


def cells(row):
    """The truthy items of every truthy cell of one row."""
    out = []
    for cell in row:
        if cell:
            out.extend(items(cell))
    return out


def walk(rows):
    """Collect the truthy items of every truthy cell of every truthy row."""
    out = []
    for row in rows:
        if row is None:
            continue
        if isinstance(row, str):
            continue
        if row:
            out.extend(cells(row))
    return out
'''
""":data:`EXTRACTED` with ``walk`` grown in place: two more branches, four more lines.

Nothing here breaks an absolute limit -- CyclomaticStrict reaches 5 against a maximum of 10
and MaxNesting stays at 2 against a maximum of 3 -- so every finding it draws is the ratchet,
which is what makes it the discriminator for the exemption.
"""

RATCHET_BACK_ON = """[thresholds.file]
CountDeclFunction = { max = 25, ratchet = true }
CountLineCode = { max = 500, ratchet = true }
"""
"""The shipped limits with the ratchet switched on again, the one key this task changed."""


def decomposition_repo(tmp_path: Path, head: str) -> Workspace:
    """A licensed repository whose ``HEAD`` holds ``head`` for ``DEEP`` and :data:`STEADY`."""
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
    space.write(OTHER, STEADY)
    space.write(DEEP, head)
    space.stage(OTHER, DEEP)
    space.git_ok("commit", "--quiet", "-m", "baseline")
    return space


def staged(space: Workspace, text: str) -> subprocess.subprocess.CompletedProcess[str]:
    """Stage ``text`` as ``DEEP`` and run ``check --staged`` on it."""
    space.write(DEEP, text)
    space.stage(DEEP)
    return space.cli("check", "--staged")


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


# --- the gate must not refuse the refactoring its own hints recommend (task 11.9) ---


def test_the_extraction_the_hint_recommends_is_no_longer_refused(tmp_path: Path) -> None:
    """The defect end to end: get the hint, do exactly what it says, be allowed to commit.

    One repository, one file, two runs. The first prints ``routine.MaxNesting`` and the hint
    that names the remedy; the second applies that remedy -- two helpers extracted, nothing
    else -- and comes back at exit 0. Before this task the second run was refused for
    ``file.CountDeclFunction rose from 1 to 3``, so the pair is the whole finding: the
    assertion that the hint fired is what makes the assertion that the fix passed mean
    something, because a run that never reached the hint could pass for any reason at all.
    """
    space = decomposition_repo(tmp_path, BASE)

    flagged = staged(space, NESTED)

    assert flagged.returncode == int(ExitCode.VIOLATIONS), flagged.stderr
    assert "routine.MaxNesting" in flagged.stdout
    assert "extract the inner block into its own routine" in flagged.stdout

    fixed = staged(space, EXTRACTED)

    assert fixed.returncode == int(ExitCode.OK), both_streams(fixed.stdout, fixed.stderr)
    assert "0 blocking" in fixed.stdout
    assert "file.CountDeclFunction" not in fixed.stdout
    assert "file.CountLineCode" not in fixed.stdout


def test_switching_the_ratchet_back_on_restores_the_refusal(tmp_path: Path) -> None:
    """The same extraction, the same repository, one configuration key: refused again.

    This is the measurement that says the run above passes *because of this task* and not
    because the sources happen to be under every limit. ``ratchet = true`` on the two counts
    task 11.9 turned off brings back the exact two findings the defect was reported as, with
    the values they were reported with.
    """
    space = decomposition_repo(tmp_path, BASE)
    space.write("scitools-hook.toml", RATCHET_BACK_ON)
    space.stage("scitools-hook.toml")

    refused = staged(space, EXTRACTED)

    assert refused.returncode == int(ExitCode.VIOLATIONS), refused.stderr
    assert "file pkg/deep.py CountDeclFunction rose from 1 to 3" in refused.stdout
    assert "file pkg/deep.py CountLineCode rose from 6 to 18" in refused.stdout


def test_a_routine_grown_more_complex_in_place_still_blocks(tmp_path: Path) -> None:
    """The other half of the deliverable: nothing was traded away to let the fix through.

    ``HEAD`` here is the extracted version, so the comparison starts from the improved code.
    ``walk`` then grows two branches and four lines in place, breaking no absolute limit --
    CyclomaticStrict 5 against a maximum of 10, MaxNesting still 2 against 3 -- so every
    finding this draws is the ratchet. ``routine.CountLineCode`` and ``routine.CountStmt``
    are asserted by name because those two are the counts the decomposition exemption *can*
    forgive: they are reported here, which is what says the exemption reads the entity's own
    complexity rather than waving every count through.
    """
    space = decomposition_repo(tmp_path, EXTRACTED)

    blocked = staged(space, REGRESSED)

    assert blocked.returncode == int(ExitCode.VIOLATIONS), blocked.stderr
    assert "routine deep.walk CyclomaticStrict rose from 3 to 5" in blocked.stdout
    assert "routine deep.walk CountLineCode rose from 6 to 10" in blocked.stdout
    assert "routine deep.walk CountStmt rose from 6 to 10" in blocked.stdout


# --- a file the analysis could not read (req 2.6, tasks 11.11 and 11.13) --------

GENERIC = "pkg/generic.py"
CLEAN_FILE = "pkg/clean.py"

PEP695 = '''"""Identity helpers."""


def generic[T](x: T) -> T:
    """Hand back what it was given."""
    return x


def tail(y):
    """A routine after the one Understand stops at."""
    return y
'''
"""One PEP 695 declaration, and one routine after it that the database will not hold.

**Measured against the installed Build 1204**, with a Python 3 interpreter on ``PATH``:
``und analyze`` answers ``Errors:16``, the first being ``expected token '(' at token [`` at
line 1, and then ``expected identifier at token dedent`` for every later line down to
``expected newline at token EOF``. The same module with ``def generic(x):`` answers
``Errors:0`` and holds both routines. The declaration is not merely unparsed: it takes the
rest of the file out of the database, which is why ``tail`` is here to be lost.
"""

CLEAN = '''"""A module holding nothing Understand cannot read."""


def scan(items):
    """Hand back what it was given."""
    return items
'''


def document_of(done: subprocess.CompletedProcess[str]) -> dict[str, object]:
    """The JSON document a ``--format json`` run printed, and nothing else (req 7.4)."""
    return dict(json.loads(done.stdout))


def unreadable_findings(done: subprocess.CompletedProcess[str]) -> list[dict[str, object]]:
    """Every finding the run raised about a file it could not read."""
    listed = document_of(done)["findings"]
    assert isinstance(listed, list)
    return [dict(item) for item in listed if dict(item)["rule"] == "analysis.parse_error"]


def unparsed_paths(done: subprocess.CompletedProcess[str]) -> list[str]:
    """The paths the run said it could not read, de-duplicated and sorted."""
    errors = document_of(done)["parse_errors"]
    assert isinstance(errors, list)
    return sorted({str(dict(error)["path"]) for error in errors})


def test_a_staged_file_that_does_not_parse_blocks_and_says_what_to_rewrite(
    licensed: Workspace,
) -> None:
    """Task 11.11 against the real analyser: the Gate must not certify what it never read.

    Before this, the same run through the installed console script exited 0 with
    ``blocking_count`` 0 while its own JSON carried six parse errors for the staged file. The
    entities after the declaration are absent from the database, so they break no threshold,
    trip no structural rule, and the commit goes through -- measured, one such declaration
    took ``config/models.py`` from 15 classes to 3 and hid 12 findings.
    """
    licensed.write(GENERIC, PEP695)
    licensed.stage(GENERIC)

    done = licensed.cli("check", "--staged", "--format", "json")

    assert done.returncode == int(ExitCode.VIOLATIONS), done.stderr
    unreadable = unreadable_findings(done)
    assert [(item["path"], item["blocking"]) for item in unreadable] == [(GENERIC, True)]
    assert "TypeVar" in str(unreadable[0]["hint"])
    assert unparsed_paths(done) == [GENERIC]


def test_a_parse_error_outside_the_selection_is_reported_and_lets_the_commit_through(
    licensed: Workspace,
) -> None:
    """The other half, and a different input: the same unreadable file, moved out of the change.

    Task 10.4 measured four parse errors on a clean run of this project, every one of them in
    the interpreter's own standard library -- files no commit here can fix. What decides is
    therefore the selection, not the error, and the cheapest way to show that is the same file
    that blocks above, committed and then left alone.
    """
    licensed.write(GENERIC, PEP695)
    licensed.stage(GENERIC)
    licensed.git_ok("commit", "--quiet", "--no-verify", "-m", "add the generic helper")
    licensed.write(CLEAN_FILE, CLEAN)
    licensed.stage(CLEAN_FILE)

    done = licensed.cli("check", "--staged", "--format", "json")

    assert unparsed_paths(done) == [GENERIC], "requirement 2.6 still reports it"
    assert unreadable_findings(done) == []
    assert done.returncode == int(ExitCode.OK), done.stdout + done.stderr


def test_a_warm_run_reports_the_same_parse_errors_as_the_cold_one(
    licensed: Workspace,
) -> None:
    """Task 11.13 through the console script: cold, then warm, over the same two databases.

    Measured before this existed, on this repository's own source: the cold staged run printed
    ``9 files failed to parse, not fully checked`` and three consecutive warm runs printed
    none, although the databases still held the same unparseable files. ``und analyze`` is
    incremental, so the second run re-parsed nothing and therefore reported nothing -- which
    left requirement 2.6's report, and 11.11's finding with it, reaching only whoever happened
    to run the gate first. A git hook is always warm.
    """
    licensed.write(GENERIC, PEP695)
    licensed.stage(GENERIC)

    cold = licensed.cli("check", "--staged", "--format", "json")
    warm = licensed.cli("check", "--staged", "--format", "json")

    assert unparsed_paths(cold) == [GENERIC], cold.stderr
    assert unparsed_paths(warm) == unparsed_paths(cold)
    assert unreadable_findings(warm) == unreadable_findings(cold)
    assert warm.returncode == cold.returncode == int(ExitCode.VIOLATIONS)
