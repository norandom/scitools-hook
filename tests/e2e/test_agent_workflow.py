"""The agent's path and the pre-commit framework's, end to end (task 10.2).

Where :mod:`test_hook_workflow` drives ``git commit``, this module drives the two ways the
Gate is invoked *without* one: an agent checking its own edits before it stages them
(requirement 10.5), and the pre-commit framework handing the Gate a file list (requirement
11.8). Both run the installed console script as a real process, so what is asserted is what a
caller actually receives -- the exit status, the bytes on standard output, and the state the
run left in the analysis cache.

Three properties are worth naming, because each is a way a green run could be a lie:

* **Which repository answered.** ``repo_root`` in the JSON document is asserted against the
  workspace, because a run whose working directory was not what the test believed once
  reported a clean pass against a different repository entirely.
* **Which fixture answered.** ``violating/analyze.json`` carries one parse error, and an
  absent ``analyze.json`` is read by the seam as "this project parsed cleanly". Asserting the
  parse error is therefore the difference between "the fixture I meant was read" and "some
  directory was read, or none".
* **Standard output carries the document and nothing else** (req 7.4). ``json.loads`` over
  the whole stream is the assertion: a diagnostic that leaked onto stdout makes it fail.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from e2e.harness import (
    DEEP,
    NESTED,
    OTHER,
    PARSE_ERROR_PATH,
    VIOLATING,
    Workspace,
    finding_paths,
    make_workspace,
    report,
    rules,
)
from scitools_hook.exit_codes import ExitCode
from scitools_hook.report.agent_rules import BEGIN_MARKER, END_MARKER
from scitools_hook.understand.fake import FAKE_VAR, FIXTURE_VERSION

HOOKS_YAML = Path(__file__).resolve().parents[2] / ".pre-commit-hooks.yaml"
"""The shipped pre-commit-framework definition (req 11.7); its ``entry`` is run verbatim."""

BASELINE_DEEP = "def walk(rows):\n    return rows\n"
"""What ``HEAD`` holds for :data:`e2e.harness.DEEP` in a fresh workspace."""

UNSTAGED = "def walk(rows):\n    return tuple(rows)  # not staged\n"
"""A working-tree edit made after staging, so the index and the tree differ."""


def framework_argv() -> list[str]:
    """The argv the pre-commit framework builds, read from the definition the Gate ships.

    Taken from the file rather than retyped: the point of requirement 11.7 is that *this*
    entry works, and a test that spelled its own would keep passing after the entry changed.
    ``pass_filenames: true`` is what makes the framework append the staged paths, so it is
    asserted here rather than assumed.
    """
    definition = yaml.safe_load(HOOKS_YAML.read_text(encoding="utf-8"))[0]
    assert definition["pass_filenames"] is True
    return list(str(definition["entry"]).split())


# --- the agent: check the edit before staging it (req 10.5) ---------------------------


def test_the_worktree_mode_sees_an_edit_the_staged_mode_cannot(workspace: Workspace) -> None:
    """Requirement 10.5's whole reason to exist, and requirement 4.9's answer beside it.

    One repository, one unstaged edit, two runs. ``--worktree`` reports the change; ``--staged``
    has nothing to analyse and says so at exit 0. The pair is the discriminator: a
    ``--worktree`` that quietly read the index would answer the same as ``--staged`` here.
    """
    workspace.write(DEEP, NESTED)

    unstaged = workspace.cli("check", "--worktree", "--format", "json")
    assert unstaged.returncode == int(ExitCode.VIOLATIONS), unstaged.stderr
    document = report(unstaged)
    assert document["selection"] == "worktree"
    assert document["repo_root"] == str(workspace.root)
    assert finding_paths(document) == {DEEP}
    assert "routine.MaxNesting" in rules(document)

    staged = workspace.cli("check", "--staged", "--format", "json")
    assert staged.returncode == int(ExitCode.OK), staged.stderr
    nothing = report(staged)
    assert nothing["selection"] == "staged"
    assert nothing["analyzed_files"] == 0
    assert nothing["findings"] == []


def test_the_agent_writes_the_rules_and_then_checks_its_own_edit(workspace: Workspace) -> None:
    """``agent-rules --write`` then ``check --worktree --format json``, as the task describes.

    ``agent-rules --write`` writes the file and confirms on standard error, leaving standard
    output empty -- the same shape ``check --output`` has, so a caller piping the command
    receives nothing it did not ask for.
    """
    written = workspace.cli("agent-rules", "--write", "AGENTS.md")
    assert written.returncode == int(ExitCode.OK), written.stderr
    assert written.stdout == ""
    assert "AGENTS.md" in written.stderr
    text = (workspace.root / "AGENTS.md").read_text(encoding="utf-8")
    assert BEGIN_MARKER in text and END_MARKER in text
    assert "`MaxNesting`: at most 3 (error)" in text
    assert "scitools-hook check --worktree" in text

    workspace.write(DEEP, NESTED)
    checked = workspace.cli("check", "--worktree", "--format", "json")

    assert checked.returncode == int(ExitCode.VIOLATIONS), checked.stderr
    document = report(checked)
    assert "routine.MaxNesting" in rules(document), (
        "the rule the snippet told the agent about is the one that fired"
    )


def test_nothing_that_understand_can_parse_is_staged(workspace: Workspace) -> None:
    """Requirement 4.9: a change of files Understand does not read is not a failure.

    The staged file is a real staged change -- ``git`` reports it -- so this is not the same
    input as the empty index above: the run has something to look at and nothing to analyse.
    """
    workspace.write("README.md", "# notes\n")
    workspace.stage("README.md")

    done = workspace.cli("check", "--staged", "--format", "json")
    assert done.returncode == int(ExitCode.OK), done.stderr
    document = report(done)
    assert document["analyzed_files"] == 0
    assert document["findings"] == []

    human = workspace.cli("check", "--staged")
    assert human.returncode == int(ExitCode.OK), human.stderr
    assert "nothing to report" in human.stdout


# --- the pre-commit framework: a file list, and HEAD (req 11.8) -----------------------


def test_the_framework_entry_evaluates_only_the_files_it_is_passed(workspace: Workspace) -> None:
    """Both files are staged and both break the limit; only the named one is reported."""
    workspace.write(DEEP, NESTED)
    workspace.write(OTHER, NESTED.replace("walk(rows)", "scan(items)"))
    workspace.stage(DEEP, OTHER)

    everything = workspace.cli("check", "--staged", "--format", "json")
    assert everything.returncode == int(ExitCode.VIOLATIONS), everything.stderr
    assert finding_paths(report(everything)) == {DEEP, OTHER}

    entry = framework_argv()
    one = workspace.cli(*entry[1:], OTHER, "--format", "json")
    assert one.returncode == int(ExitCode.VIOLATIONS), one.stderr
    single = report(one)
    assert finding_paths(single) == {OTHER}
    assert single["selection"] == f"files: {OTHER}"

    both = workspace.cli(*entry[1:], DEEP, OTHER, "--format", "json")
    assert both.returncode == int(ExitCode.VIOLATIONS), both.stderr
    assert finding_paths(report(both)) == {DEEP, OTHER}


def test_the_framework_entry_is_the_console_script_this_suite_runs(workspace: Workspace) -> None:
    """The entry names the installed command, and the harness's PATH is where it is found."""
    entry = framework_argv()
    assert entry[0] == "scitools-hook"
    assert shutil.which(entry[0], path=workspace.env["PATH"]) is not None


def test_the_files_selection_reads_the_before_state_from_head(workspace: Workspace) -> None:
    """Requirement 11.8's second half, measured rather than inferred.

    Three separate observations, because "it used HEAD" is not something a finding says out
    loud: the cache records the commit the before side was synced from, the before shadow
    holds that commit's content, and every finding carries a ``before`` value that could only
    have come from a second side. A second commit is made first so that ``HEAD`` is not simply
    the only commit the repository has.
    """
    workspace.write("notes.txt", "a second commit, so HEAD is a choice\n")
    workspace.stage("notes.txt")
    workspace.git_ok("commit", "--quiet", "-m", "second")
    head = workspace.head()

    workspace.write(DEEP, NESTED)
    workspace.stage(DEEP)
    workspace.write(DEEP, UNSTAGED)

    done = workspace.cli("check", "--files", DEEP, "--format", "json")
    assert done.returncode == int(ExitCode.VIOLATIONS), done.stderr
    document = report(done)
    findings = document["findings"]
    assert isinstance(findings, list) and findings
    assert all(finding["before"] is not None for finding in findings)

    assert workspace.sync_state()["before_commit"] == head
    assert workspace.shadow("before", DEEP) == BASELINE_DEEP
    assert workspace.shadow("after", DEEP) == NESTED, (
        "the after side is the index, not the working tree (req 4.1)"
    )


def test_the_framework_entry_with_no_paths_still_means_the_staged_change(
    workspace: Workspace,
) -> None:
    """Why ``.pre-commit-hooks.yaml`` ships the bare ``check`` and not ``check --files``.

    ``--files`` with nothing after it is a usage error, so a framework run that passed no
    paths would fail before anything was analysed. The bare entry has no such edge: with no
    paths it falls back to requirement 12.3's hook-aware default, which the framework
    satisfies because git runs *it* as the pre-commit hook and ``GIT_INDEX_FILE`` is in the
    environment its children inherit. Outside a hook the same argv means the whole project,
    and the two answers are asserted against each other so neither can be the default by
    accident.
    """
    empty = workspace.cli("check", "--files")
    assert empty.returncode == int(ExitCode.CONFIG_ERROR)

    workspace.write(DEEP, NESTED)
    workspace.stage(DEEP)
    entry = framework_argv()[1:]

    outside = workspace.cli(*entry, "--format", "json")
    assert report(outside)["selection"] == "all"

    inside = workspace.cli(
        *entry,
        "--format",
        "json",
        env=workspace.with_env(GIT_INDEX_FILE=str(workspace.root / ".git" / "index")),
    )
    assert report(inside)["selection"] == "staged"
    assert finding_paths(report(inside)) == {DEEP}


# --- what proves the run was the run this test meant ----------------------------------


def test_the_report_names_the_repository_and_the_fixture_directory_it_read(
    tmp_path: Path,
) -> None:
    """A green run against the wrong repository, or against no fixtures, cannot pass as this one.

    Two workspaces exist at once and the second is the one run, so ``repo_root`` distinguishes
    them; the parse error comes from ``violating/analyze.json``, and an absent ``analyze.json``
    is read by the seam as "parsed cleanly", so its presence names the directory that answered.
    """
    decoy = make_workspace(tmp_path, "decoy", **{FAKE_VAR: str(VIOLATING)})
    space = make_workspace(tmp_path, "real", **{FAKE_VAR: str(VIOLATING)})
    space.write(DEEP, NESTED)
    space.stage(DEEP)

    done = space.cli("check", "--staged", "--format", "json")
    assert done.returncode == int(ExitCode.VIOLATIONS), done.stderr
    document = report(done)

    assert document["repo_root"] == str(space.root)
    assert document["repo_root"] != str(decoy.root)
    assert document["understand_version"] == FIXTURE_VERSION
    assert [error["path"] for error in document["parse_errors"]] == [PARSE_ERROR_PATH]  # type: ignore[index]
