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
* :data:`GREW` and :data:`GREW_WITH_PARAMETERS` are the same growth twice, the second with
  three parameters added and nothing else changed. They used to report a full set of
  routine-scope ratchet findings and **none at all**: ``EntityKey`` carries ``parameters``,
  so the second run's routine was a different entity on the two sides and requirement 4.4 had
  nothing to compare. They now report the same rules, ``routine.CountParams`` excepted
  (task 11.6).

Skipping is deliberate and precise. The ``contract`` marker skips the module when the
developer's own environment has no licence; :func:`e2e.harness.license_problem` then asks the
same question again *in the isolated environment these tests actually use*, because pointing
``XDG_CONFIG_HOME`` at a sandbox is enough to hide the licence from ``und``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from e2e.harness import (
    Workspace,
    finding_paths,
    isolated_env,
    license_problem,
    licensed_env,
    rules,
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

RATCHET_BACK_ON = """[ratchet]
below_limit_severity = "error"

[thresholds.file]
CountDeclFunction = { max = 25, ratchet = true }
CountLineCode = { max = 500, ratchet = true }
"""
"""The shipped limits with the ratchet switched on again, and with growth inside a limit made
a refusal again.

Two keys, because two tasks turned this refusal off and either one alone now suppresses it.
Task 11.9 stopped ratcheting the two file counts a decomposition raises; task 11.15 stopped a
ratchet finding blocking while the entity is inside its limit, and three functions in a file
is inside a maximum of 25. Switching both back on is what restores the exact refusal the
defect was reported as, which is what makes
:func:`test_the_extraction_the_hint_recommends_is_no_longer_refused` able to fail."""


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


def test_a_routine_grown_more_complex_in_place_is_still_reported(tmp_path: Path) -> None:
    """The other half of task 11.9: nothing was traded away to let the extraction through.

    ``HEAD`` here is the extracted version, so the comparison starts from the improved code.
    ``walk`` then grows two branches and four lines in place, breaking no absolute limit --
    CyclomaticStrict 5 against a maximum of 10, MaxNesting still 2 against 3 -- so every
    finding this draws is the ratchet. ``routine.CountLineCode`` and ``routine.CountStmt``
    are asserted by name because those two are the counts the decomposition exemption *can*
    forgive: they are reported here, which is what says the exemption reads the entity's own
    complexity rather than waving every count through.

    **What changed in task 11.15 is the exit code, not the findings.** These three lines used
    to refuse the commit; a ten-line routine against a maximum of sixty is exactly the freeze
    that task removed, so they are warnings now and the run exits 0. The distinction task 11.9
    is about survives whole, because it was always a distinction between *reporting* and
    staying silent: :func:`test_the_extraction_the_hint_recommends_is_no_longer_refused` gets
    no such finding at all for the same three rules.
    """
    space = decomposition_repo(tmp_path, EXTRACTED)

    reported = staged(space, REGRESSED)

    assert reported.returncode == int(ExitCode.OK), both_streams(reported.stdout, reported.stderr)
    assert "routine deep.walk CyclomaticStrict rose from 3 to 5" in reported.stdout
    assert "routine deep.walk CountLineCode rose from 6 to 10" in reported.stdout
    assert "routine deep.walk CountStmt rose from 6 to 10" in reported.stdout
    assert "0 blocking" in reported.stdout


def test_a_routine_grown_more_complex_in_place_blocks_again_under_the_freeze(
    tmp_path: Path,
) -> None:
    """The same change, the same repository, one configuration key: refused word for word.

    ``below_limit_severity = "error"`` is the pre-11.15 ratchet, so this is the run the test
    above used to be. It is here because without it the run above would pass just as happily
    against a ratchet that had stopped comparing anything, and "0 blocking" would prove
    nothing about *why*.
    """
    space = decomposition_repo(tmp_path, EXTRACTED)
    space.write("scitools-hook.toml", FREEZE_BELOW_LIMIT)
    space.stage("scitools-hook.toml")

    blocked = staged(space, REGRESSED)

    assert blocked.returncode == int(ExitCode.VIOLATIONS), blocked.stderr
    assert "routine deep.walk CyclomaticStrict rose from 3 to 5" in blocked.stdout
    assert "routine deep.walk CountLineCode rose from 6 to 10" in blocked.stdout
    assert "routine deep.walk CountStmt rose from 6 to 10" in blocked.stdout


# --- a routine that gained parameters is still the same routine (task 11.6) -----

GROWN_BODY = '''    """Collect the truthy cells of every truthy row."""
    out = []
    for row in rows:
        if row is None:
            continue
        if isinstance(row, str):
            continue
        if row:
            out.append(row)
    return out
'''
"""The body both versions below share, character for character."""

GREW = '"""Row helpers."""\n\n\ndef walk(rows):\n' + GROWN_BODY
GREW_WITH_PARAMETERS = (
    '"""Row helpers."""\n\n\ndef walk(rows, skip_none, skip_str, limit):\n' + GROWN_BODY
)
"""``BASE``'s ``walk`` grown by four lines and two branches, twice: once with its signature
untouched and once with three parameters added.

The three parameters are **unused on purpose**. Everything Understand measures about the two
routines is identical except ``CountParams`` -- measured: ``CountLineCode`` 10, ``CountStmt``
10, ``CountPath`` 9 and ``CyclomaticStrict`` 5 on both -- so the parameter list is the only
thing the pair of runs differs in, which is what makes the pair a measurement of the join
rather than two unrelated runs. An agent that adds arguments while growing a routine usually
uses them; using them would move the complexity numbers as well and blunt the comparison.
"""


def routine_ratchets(space: Workspace, text: str) -> dict[str, str]:
    """Stage ``text``, run the gate, and answer its routine-scope ratchet findings by rule."""
    space.write(DEEP, text)
    space.stage(DEEP)
    done = space.cli("check", "--staged", "--format", "json")
    # Exit 0 since task 11.15: this growth breaks no limit -- CountLineCode reaches 10 of 60
    # and CyclomaticStrict 5 of 10 -- so the findings below are reported as warnings and the
    # commit is allowed. The exit code is still pinned, because a run that failed for some
    # other reason would otherwise hand this helper an empty findings list to agree with.
    assert done.returncode == int(ExitCode.OK), both_streams(done.stdout, done.stderr)
    document = dict(json.loads(done.stdout))
    findings = list(document["findings"])  # type: ignore[call-overload]
    return {
        str(finding["rule"]): str(finding["message"])
        for finding in findings
        if finding["kind"] == "ratchet" and finding["scope"] == "routine"
    }


def test_a_routine_that_gained_parameters_is_ratcheted_like_one_that_did_not(
    tmp_path: Path,
) -> None:
    """Task 11.6 end to end: two runs, one repository, one difference -- the parameter list.

    ``EntityKey`` carries ``parameters`` so that a real C++ overload pair stays two entities,
    and the price was that a routine whose signature changed was a *different* entity on the
    two sides: it read as one removed and one added, requirement 4.4 never fired, and the run
    reported **nothing at routine scope**. Measured before the fix, on this very pair: six
    routine findings for the unchanged signature and zero for the changed one.

    Both runs stage the same grown body against the same ``HEAD``. The assertion is not that
    the second run reports *something* -- it is that the two runs report the *same rules*,
    with ``routine.CountParams`` as the single difference, which is the only thing that
    actually changed. A pairing that matched the wrong entity, or one that stopped matching,
    breaks that equality in one direction or the other.
    """
    space = decomposition_repo(tmp_path, BASE)

    same_signature = routine_ratchets(space, GREW)
    new_signature = routine_ratchets(space, GREW_WITH_PARAMETERS)

    assert (
        "routine deep.walk CountLineCode rose from 6 to 10"
        in same_signature["routine.CountLineCode"]
    )
    assert (
        "routine deep.walk CountLineCode rose from 6 to 10"
        in new_signature["routine.CountLineCode"]
    )
    assert set(new_signature) - set(same_signature) == {"routine.CountParams"}
    assert set(same_signature) - set(new_signature) == set()
    assert "rose from 1 to 4" in new_signature["routine.CountParams"]


# --- a file the analysis could not read (req 2.6, tasks 11.11 and 11.13) --------

GENERIC = "pkg/generic.py"
CLEAN_FILE = "pkg/clean.py"

UNREADABLE = '''"""Identity helpers."""


def generic(x:
    """A declaration Understand cannot finish reading."""
    return x


def tail(y):
    """A routine after the one Understand stops at."""
    return y
'''
"""One declaration no build parses, and one routine after it that the database will not hold.

**Measured on Build 1262** with a Python 3 interpreter on ``PATH``: ``und analyze`` answers
``expected identifier at token return`` at the line after the declaration, then ``expected
token ':' at token EOF``, and the file holds ``generic`` and not ``tail``. The declaration is
not merely unparsed: it takes the rest of the file out of the database, which is why ``tail``
is here to be lost.

Until 8.0 this fixture was a PEP 695 declaration, ``def generic[T](x: T) -> T:``, which
Build 1204 answered with ``Errors:16`` and the same lost tail. 7.2 taught the parser type
parameters, so the file that no build reads is now one with a parenthesis missing -- the
same class of failure, without a version it stops applying to.
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
    licensed.write(GENERIC, UNREADABLE)
    licensed.stage(GENERIC)

    done = licensed.cli("check", "--staged", "--format", "json")

    assert done.returncode == int(ExitCode.VIOLATIONS), done.stderr
    unreadable = unreadable_findings(done)
    assert [(item["path"], item["blocking"]) for item in unreadable] == [(GENERIC, True)]
    assert "rewrite the construct" in str(unreadable[0]["hint"])
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
    licensed.write(GENERIC, UNREADABLE)
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
    licensed.write(GENERIC, UNREADABLE)
    licensed.stage(GENERIC)

    cold = licensed.cli("check", "--staged", "--format", "json")
    warm = licensed.cli("check", "--staged", "--format", "json")

    assert unparsed_paths(cold) == [GENERIC], cold.stderr
    assert unparsed_paths(warm) == unparsed_paths(cold)
    assert unreadable_findings(warm) == unreadable_findings(cold)
    assert warm.returncode == cold.returncode == int(ExitCode.VIOLATIONS)


# --- one tree, one answer, whatever the ambient environment says (task 11.12) --------

CYCLE_A = "src/pkg/left.py"
CYCLE_B = "src/pkg/right.py"
CYCLE_INIT = "src/pkg/__init__.py"

LEFT = '''"""One half of a two-file import cycle, which only exists if both halves are seen."""

from pkg import right


def ask_right(value):
    return right.answer(value)


def answer(value):
    return value + 1
'''

RIGHT = '''"""The other half. Understand only records the cycle when both files are in the tree."""

from pkg import left


def ask_left(value):
    return left.answer(value)


def answer(value):
    return value + 2
'''

PACKAGE_INIT = '"""The package both halves of the cycle live in."""\n'
"""Present so the decoy outside the tree is a package an interpreter would really import."""


AMBIENT_CASES = ("PYTHONPATH", "PYTHONUSERBASE")
"""The two ambient variables measured to change this tree's answer before task 11.12.

``PYTHONHOME`` is the third and is deliberately **not** here. It defeats the pin just as
completely -- measured through ``UndCli``, it kills the pinned interpreter before it prints
anything, ``und`` reads that as "no python", and the database comes back under the Python 2
model -- but it also kills the console script under test, which is itself a Python process,
so at this level it fails loudly instead of silently. It is scrubbed with the rest because a
frozen build would not be a Python process, and the unit test in ``tests/understand`` is
where it is pinned.
"""


def cycle_repo(tmp_path: Path) -> Workspace:
    """A licensed repository whose two committed modules import each other."""
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
    space.write(CYCLE_INIT, PACKAGE_INIT)
    space.write(CYCLE_A, LEFT)
    space.write(CYCLE_B, RIGHT)
    space.stage(CYCLE_INIT, CYCLE_A, CYCLE_B)
    space.git_ok("commit", "--quiet", "-m", "baseline")
    return space


def decoy_package(sandbox: Path) -> Path:
    """A second copy of ``pkg`` **outside** the repository, for an interpreter to find first.

    This is the shape every editable install has: ``uv sync`` on this project puts
    ``<repo>/src`` on the venv interpreter's ``sys.path`` through a ``.pth`` file, so
    ``import pkg.right`` resolves to a directory the analysis never enrolled. A copy is used
    rather than the repository itself because the resolution has to land somewhere *outside*
    the analysed shadow tree, which is what makes the dependency edge disappear.
    """
    outside = sandbox / "outside"
    (outside / "pkg").mkdir(parents=True, exist_ok=True)
    (outside / "pkg" / "__init__.py").write_text(PACKAGE_INIT, encoding="utf-8")
    (outside / "pkg" / "left.py").write_text(LEFT, encoding="utf-8")
    (outside / "pkg" / "right.py").write_text(RIGHT, encoding="utf-8")
    return outside


def decoy_user_site(sandbox: Path) -> Path:
    """A per-user ``site-packages`` whose ``.pth`` puts the decoy package on ``sys.path``.

    The shape a ``pip install --user -e .`` leaves behind, and the reason the pin sets
    ``PYTHONNOUSERSITE`` rather than merely clearing ``PYTHONUSERBASE``. The version segment
    is this interpreter's because the console script under test runs on this interpreter, and
    so does the ``python`` it pins.
    """
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    packages = sandbox / "userbase" / "lib" / version / "site-packages"
    packages.mkdir(parents=True, exist_ok=True)
    (packages / "decoy.pth").write_text(f"{decoy_package(sandbox)}\n", encoding="utf-8")
    return sandbox / "userbase"


def hostile_environment(space: Workspace, case: str, sandbox: Path) -> dict[str, str]:
    """The workspace's own sealed environment with one measured lever added to it."""
    if case == "PYTHONPATH":
        return space.with_env(PYTHONPATH=str(decoy_package(sandbox)))
    return space.with_env(PYTHONUSERBASE=str(decoy_user_site(sandbox)))


def checked_after_rebuild(
    space: Workspace, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """``db rebuild`` and then ``check --all``, both under ``env``.

    The rebuild is not a tidying step, it is the whole measurement. ``und analyze`` is
    incremental, so a second ``check`` over an unchanged tree re-parses nothing and answers
    out of the database the *first* environment built -- which made an earlier version of this
    test pass against the very defect it was written for. Discarding first is what makes each
    environment actually analyse the tree it is given.
    """
    rebuilt = space.cli("db", "rebuild", env=env)
    assert rebuilt.returncode == int(ExitCode.OK), both_streams(rebuilt.stdout, rebuilt.stderr)
    return space.cli("check", "--all", "--format", "json", env=env)


@pytest.mark.parametrize("case", AMBIENT_CASES)
def test_the_same_tree_gives_the_same_answer_under_a_hostile_ambient_environment(
    tmp_path: Path, case: str
) -> None:
    """Task 11.12's done-condition at the ``check`` level: two environments, one answer.

    Task 11.10 pinned ``PATH`` so that ``und`` could no longer be handed the wrong ``python``.
    It did not pin the rest of the Python environment, and both of these were then measured to
    walk straight around it on Understand 6.5.1204:

    * ``PYTHONPATH`` pointing at a second copy of the package -- which is precisely what an
      editable install of the analysed project gives its own interpreter -- resolves the
      imports **outside** the analysed tree. On this project's own 172 files that took the
      intra-tree file dependency edges from **1272 to 66**, switching off the whole of
      ``structure.new_dependencies`` and ``structure.fan``, with nothing in the report saying
      why. Here it takes the import cycle with them.
    * ``PYTHONUSERBASE`` pointing at a ``site-packages`` holding a ``.pth`` does the same
      through the per-user path, which no variable is needed to reach at all. A pinned
      ``python`` is not a virtual environment, so unlike the Gate's own interpreter it has
      per-user packages switched **on**.

    Both are false *negatives*, so the discriminator has to be a finding that disappears
    rather than an error that appears. ``structure.file_cycle`` is that finding: it exists
    only while both modules resolve inside the tree. The clean run is asserted to raise it
    first, because three identical empty answers would satisfy an equality check while
    measuring nothing at all.
    """
    space = cycle_repo(tmp_path)

    clean = checked_after_rebuild(space, space.env)
    hostile = checked_after_rebuild(space, hostile_environment(space, case, tmp_path))

    assert clean.returncode == int(ExitCode.VIOLATIONS), both_streams(clean.stdout, clean.stderr)
    assert "structure.file_cycle" in rules(document_of(clean)), (
        "the control must actually see the cycle, or the comparison below measures nothing"
    )
    assert rules(document_of(hostile)) == rules(document_of(clean))
    assert finding_paths(document_of(hostile)) == finding_paths(document_of(clean))
    assert hostile.returncode == clean.returncode


# --- the four cases of the headroom rule, through the installed CLI (task 11.15) ----

FREEZE_BELOW_LIMIT = """[ratchet]
below_limit_severity = "error"
"""
"""The one key that turns growth inside a limit back into a refusal -- the pre-11.15 ratchet.

Every case below that passes is paired with a run under this file, because "the commit went
through" is the assertion a deleted ratchet satisfies too.
"""

SHORT_LIMIT = """[thresholds.routine]
CountLineCode = 12
"""
"""``routine.CountLineCode`` at twelve, so a routine a reader can count reaches the boundary.

The shipped maximum is 60 and the two cases that need a routine *at* and *over* its limit
would need sixty-line sources to reach it, which nobody can check by eye. Twelve is a
configured limit like any other and the rule under test reads
``EffectiveThreshold.limit``, so nothing about the comparison changes with the number.
"""

REGISTRAR = "pkg/registrar.py"
STEADY_PAIR = "pkg/steady.py"

STEADY = '''"""Two routines that never change, so one file does not decide the project average."""


def first(value):
    """Echo the value."""
    return value


def second(value):
    """Echo the value again."""
    return value
'''
"""Held constant in every repository below; see :data:`test_licensed_workflow.STEADY`."""

NESTING_FLAT = '''"""Row helpers."""


def walk(rows):
    """Collect the rows."""
    out = []
    for row in rows:
        out.append(row)
    return out
'''

NESTING_DEEP = '''"""Row helpers."""


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
"""``NESTING_FLAT``'s ``walk`` nested six deep.

Measured through the installed CLI against Understand 6.5.1204: ``MaxNesting`` 1 -> 6 against
a shipped maximum of 3 and ``CyclomaticStrict`` 2 -> 7 against a maximum of 10. So the
regression crosses one limit and stays inside the other, which is what makes it a test of the
rule rather than of the sources.
"""


def registrar(routes: int) -> str:
    """A registrar that adds ``routes`` routes, one per line.

    ``routine.CountLineCode`` and ``routine.CountStmt`` are both ``routes + 2`` on this shape
    -- the ``def`` line, the ``return`` and one line per route -- measured through the
    installed CLI for 2, 3, 8, 10, 11, 12, 13, 15 and 17 routes. The tests below still assert
    the values they expect as literals; this note is why those literals are what they are.
    """
    body = "\n".join(f'    app.add("/r{index}", {index})' for index in range(routes))
    header = '"""Route table."""\n\n\ndef register(app):\n    """Register every route."""\n'
    return f"{header}{body}\n    return app\n"


def headroom_repo(
    tmp_path: Path, head: str, config: str = "", subject: str = REGISTRAR
) -> Workspace:
    """A licensed repository whose ``HEAD`` holds ``head`` for ``subject``, plus :data:`STEADY`."""
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
    space.write(STEADY_PAIR, STEADY)
    space.write(subject, head)
    staged_paths = [STEADY_PAIR, subject]
    if config:
        space.write("scitools-hook.toml", config)
        staged_paths.append("scitools-hook.toml")
    space.stage(*staged_paths)
    space.git_ok("commit", "--quiet", "-m", "baseline")
    return space


def gate(space: Workspace, text: str, subject: str = REGISTRAR) -> subprocess.CompletedProcess[str]:
    """Stage ``text`` as ``subject`` and run ``check --staged`` on it."""
    space.write(subject, text)
    space.stage(subject)
    return space.cli("check", "--staged")


def test_case_1_growth_inside_the_limit_passes(tmp_path: Path) -> None:
    """The reported defect, reproduced and fixed against the **shipped** limits.

    A 27-line registrar gains one route. ``routine.CountLineCode`` and ``routine.CountStmt``
    both go 27 -> 28 against maxima of 60 and 40. Before this task that was two blocking
    errors and ``exit 1``; the reporter's own numbers -- 29 -> 30 of 60, 2 -> 3 of 40, 5 -> 6
    of 10, 5 -> 6 of 15 -- are the same shape, and every one of them was produced while doing
    the splitting the gate had asked for. The growth is still printed, with the values and
    the bound that is still holding, and the commit is allowed.
    """
    space = headroom_repo(tmp_path, registrar(25))

    passed = gate(space, registrar(26))

    assert passed.returncode == int(ExitCode.OK), both_streams(passed.stdout, passed.stderr)
    assert "0 blocking" in passed.stdout
    assert (
        "routine registrar.register CountLineCode rose from 27 to 28, "
        "still within the maximum 60" in passed.stdout
    )
    assert (
        "routine registrar.register CountStmt rose from 27 to 28, "
        "still within the maximum 40" in passed.stdout
    )


def test_case_1_blocks_again_under_below_limit_severity_error(tmp_path: Path) -> None:
    """The same 27 -> 28 line growth, one configuration key, refused as it used to be.

    This is the measurement that says the run above passes because of this task and not
    because 28 lines happen to draw no finding at all: the finding is there in both runs, and
    only its severity moves.
    """
    space = headroom_repo(tmp_path, registrar(25), FREEZE_BELOW_LIMIT)

    refused = gate(space, registrar(26))

    assert refused.returncode == int(ExitCode.VIOLATIONS), refused.stderr
    assert "2 blocking" in refused.stdout
    assert "routine.CountLineCode" in refused.stdout
    assert "routine.CountStmt" in refused.stdout


def test_case_2_growth_while_already_over_the_limit_blocks(tmp_path: Path) -> None:
    """A routine that was over its limit and got worse: refused, and by the ratchet.

    ``HEAD`` holds a 15-line routine against a maximum of 12, so the violation is older than
    the change; growing it to 17 is the one thing requirement 4.4 exists to stop and the one
    thing this task does not soften. The ratchet's own sentence is asserted, not just the
    exit code, because the absolute threshold refuses this change too and an assertion on the
    exit code alone could not tell which rule spoke.
    """
    space = headroom_repo(tmp_path, registrar(13), SHORT_LIMIT)

    blocked = gate(space, registrar(15))

    assert blocked.returncode == int(ExitCode.VIOLATIONS), blocked.stderr
    assert (
        "routine registrar.register CountLineCode rose from 15 to 17; "
        "an affected entity may not get worse than it was" in blocked.stdout
    )


def test_case_3_growth_that_crosses_the_limit_blocks(tmp_path: Path) -> None:
    """The boundary itself: 12 is the maximum, 12 -> 13 crosses it and is refused.

    ``HEAD`` holds the routine sitting exactly *on* its limit, which is the value a comparison
    written ``<`` instead of ``<=`` mistakes for a violation, and the change takes it one line
    past. The pair with :func:`test_case_3_growth_up_to_the_limit_is_the_last_growth_allowed`
    -- same repository, same limit, one line fewer -- is what pins the boundary rather than
    the neighbourhood of it.
    """
    space = headroom_repo(tmp_path, registrar(10), SHORT_LIMIT)

    blocked = gate(space, registrar(11))

    assert blocked.returncode == int(ExitCode.VIOLATIONS), blocked.stderr
    assert (
        "routine registrar.register CountLineCode rose from 12 to 13; "
        "an affected entity may not get worse than it was" in blocked.stdout
    )


def test_case_3_growth_up_to_the_limit_is_the_last_growth_allowed(tmp_path: Path) -> None:
    """One line fewer than the case above, against the same maximum of 12: allowed.

    11 -> 12 spends the last of the headroom and stops on the limit, which is not a violation
    -- ``analysis.thresholds`` refuses on ``value > max`` -- so the growth is reported and the
    commit goes through. Written as literals beside the crossing case so that the two differ
    by exactly one line in both numbers.
    """
    space = headroom_repo(tmp_path, registrar(9), SHORT_LIMIT)

    passed = gate(space, registrar(10))

    assert passed.returncode == int(ExitCode.OK), both_streams(passed.stdout, passed.stderr)
    assert "0 blocking" in passed.stdout
    assert (
        "routine registrar.register CountLineCode rose from 11 to 12, "
        "still within the maximum 12" in passed.stdout
    )


def test_case_4_a_complexity_regression_that_crosses_its_limit_still_blocks(
    tmp_path: Path,
) -> None:
    """A different metric, a different file and a real regression: still refused.

    ``walk`` goes from one level of nesting to six against a shipped maximum of three. Nothing
    about this change is size: the routine grows, but what refuses it is ``routine.MaxNesting``
    crossing its limit, and the hint that comes with it names the remedy. This is the case the
    gate exists for, and it is untouched.
    """
    space = headroom_repo(tmp_path, NESTING_FLAT, subject=DEEP)

    blocked = gate(space, NESTING_DEEP, subject=DEEP)

    assert blocked.returncode == int(ExitCode.VIOLATIONS), blocked.stderr
    assert "routine.MaxNesting" in blocked.stdout
    assert (
        "routine deep.walk MaxNesting rose from 1 to 6; "
        "an affected entity may not get worse than it was" in blocked.stdout
    )
    assert "extract the inner block into its own routine" in blocked.stdout
