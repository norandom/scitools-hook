"""The git plumbing wrapper: discovery, staged changes, exports, hooks path (task 7.1).

Every test here drives a **real** ``git`` against a **real** throwaway repository, because
the whole point of this module is that it agrees with git rather than with an idea of git.
The transcripts in the docstrings below were measured on git 2.43.0 and each one is pinned
by a test, so a git that changes its mind fails the suite instead of the gate.

The behaviours that cost the most to get wrong, and that therefore get the most attention:

* ``export_index`` must materialise the **index**, never the working tree (requirement 4.1).
  :func:`test_export_index_writes_the_staged_bytes_not_the_working_tree` is the single most
  important test in the file.
* ``--prefix`` needs a trailing slash or ``checkout-index`` concatenates it into sibling
  names — measured: ``--prefix=/tmp/dest`` produced ``/tmp/destmain.py``.
* ``--name-status -z`` is a stream of NUL-separated fields whose record length depends on the
  status token: a rename emits ``R100`` and then *two* paths.
* A fresh repository has no ``HEAD``, which is exactly when a pre-commit hook first runs.

**Test by value class, not by branch.** Nine defects in this module have been of one shape: a
case handled correctly on one side of a discriminator and wrongly on the other, where the
suite happened to exercise only the safe side. Branch coverage never caught any of them,
because both branches *were* covered — just not with the input that mattered. So every
**operator-controlled path input the module reads** is enumerated below, with its classes on
every branch that consumes it. An input with no row here is the next defect waiting.

======================  ==========================  ==================================
input                   consumed by                 classes covered
======================  ==========================  ==================================
``core.hooksPath``      ``hooks_dir`` (both arms    unset / absolute / relative /
                        of ``global_``)             empty / tilde -- all ten cells
``XDG_CONFIG_HOME``     ``hooks_dir(global_=True)`` unset / absolute / relative /
                        fallback                    empty / tilde / ``..``-normalised
``HOME``                the same fallback           absolute / relative (refused);
                                                    ``HOME=""`` gives ``/``, measured
``dest``                ``export_index``,           absolute / relative / missing
                        ``export_commit``           parents / an existing regular file
``cwd``                 ``discover``                absolute / relative / subdirectory /
                                                    linked worktree / outside a repo /
                                                    bare repo
``TMPDIR``              the throwaway index         absolute / relative-nonexistent
                                                    (ignored by ``tempfile``) /
                                                    ``.`` -- lands in the repo, an
                                                    accepted operator choice
``commit`` and refs     ``export_commit``,          ``HEAD`` / a sha / unknown /
                        ``diff_names``              unborn / ambiguous with a path /
                                                    **option-looking** -- executed as a
                                                    git option until ``--end-of-options``
``paths``               both exports                ``None`` / ``[]`` / ordinary /
                                                    newline / quote / non-UTF-8 /
                                                    absent from the index
``git`` executable      ``discover``, ``_run``      default / spy / stand-in / missing
======================  ==========================  ==================================

Two rows earn their keep loudly. ``core.hooksPath``'s relative/global cell was resolved
against the process working directory, and ``XDG_CONFIG_HOME``'s relative cell did the same
thing one call deeper -- both putting ``install-hook --global``'s answer inside whatever
repository the operator was standing in, against requirement 2.2. ``dest``'s relative cell was
the same defect a third time, in ``--prefix``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

import pytest
from conftest import FakeCommandLog, GitRepoBuilder, MakeGitRepo

from scitools_hook.errors import AnalysisFailedError, ConfigError, NotAGitRepositoryError
from scitools_hook.git.repo import (
    MISSING_RC,
    TIMEOUT_RC,
    GitRepo,
    parse_name_status,
)

FIVE_LINES = "one\ntwo\nthree\nfour\nfive\n"
"""Long enough that git scores a rename at 100% similarity rather than ignoring it."""

LEAKED_GIT_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_CONFIG_NOSYSTEM",
    "GIT_CONFIG_COUNT",
    "GIT_CEILING_DIRECTORIES",
    "GIT_OBJECT_DIRECTORY",
    "GIT_COMMON_DIR",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)
"""Everything an outer ``git`` invocation exports that would steer these subprocesses."""


# --- isolation ------------------------------------------------------------------


@dataclass(frozen=True)
class GitEnv:
    """The throwaway git environment the code under test sees while one test runs."""

    home: Path
    config: Path
    """``XDG_CONFIG_HOME``; deliberately *not* ``home/".config"`` so the two are separable."""

    global_config: Path
    system_config: Path

    def set_global_hooks_path(self, path: str) -> None:
        """Point the *global* ``core.hooksPath`` at ``path`` (requirement 11.9).

        Written as a file rather than through ``git config --global`` because the repository
        builder runs with ``GIT_CONFIG_GLOBAL=/dev/null`` and could not reach this file.
        """
        self.global_config.write_text(f"[core]\n\thooksPath = {path}\n", encoding="utf-8")


@pytest.fixture(autouse=True)
def git_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> GitEnv:
    """Isolate every ``git`` subprocess a test starts, directly or through the wrapper.

    The builder in ``tests/conftest.py`` neutralises the developer's configuration on its
    own calls; the code under test runs its own subprocesses, which inherit ``os.environ``,
    so the same isolation has to exist here. ``HOME`` and ``XDG_CONFIG_HOME`` matter too,
    because ``hooks_dir(global_=True)`` falls back to the XDG location — and they are pointed
    at *different* directories on purpose, so a fallback that reached for ``~/.config``
    directly could not pass by accident.
    """
    home = tmp_path / "git-home"
    config = tmp_path / "xdg-config"
    config.mkdir(parents=True)
    home.mkdir(parents=True)
    (home / ".config").mkdir()
    env = GitEnv(
        home=home,
        config=config,
        global_config=home / "gitconfig",
        system_config=home / "gitsystem",
    )
    env.global_config.write_text("", encoding="utf-8")
    env.system_config.write_text("", encoding="utf-8")
    for leaked in LEAKED_GIT_VARS:
        monkeypatch.delenv(leaked, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(env.global_config))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(env.system_config))
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Gate Test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "gate@example.invalid")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Gate Test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "gate@example.invalid")
    return env


def open_repo(builder: GitRepoBuilder, log: FakeCommandLog) -> GitRepo:
    """Discover the wrapper for a builder's repository, recording every command it runs."""
    return GitRepo.discover(builder.path, log)


def run_git(where: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run ``git`` tolerantly, for the states the builder refuses to reach (a conflict)."""
    return subprocess.run(
        ["git", "-C", str(where), *args], capture_output=True, text=True, check=False
    )


def exported_tree(root: Path) -> dict[str, tuple[str, str]]:
    """Every entry under ``root`` as ``(kind, content)``, symlinks kept as their target.

    ``read_bytes`` would follow a symlink and read whatever it points at — or raise for a
    dangling one — so the two sides could look identical while one had turned links into
    copies. This compares what git actually stored.
    """
    found: dict[str, tuple[str, str]] = {}
    for path in sorted(root.rglob("*")):
        name = path.relative_to(root).as_posix()
        if path.is_symlink():
            found[name] = ("symlink", os.readlink(path))
        elif path.is_file():
            found[name] = ("file", path.read_text(encoding="utf-8"))
    return found


def tree_contents(root: Path) -> dict[str, bytes]:
    """Every file under ``root`` except git's own metadata, keyed by relative POSIX path."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and ".git" not in path.relative_to(root).parts
    }


# --- discovery ------------------------------------------------------------------


def test_discover_reports_root_git_dir_and_common_dir(
    git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """In an ordinary repository the git directory and the common directory coincide."""
    builder = git_repo()
    repo = open_repo(builder, command_log)
    assert repo.root == builder.path.resolve()
    assert repo.git_dir == builder.path.resolve() / ".git"
    assert repo.common_dir == repo.git_dir


def test_discover_from_a_nested_directory_finds_the_same_root(
    git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """``--git-common-dir`` answers relative to the working directory (measured ``../../.git``)."""
    builder = git_repo()
    deep = builder.path / "pkg" / "deep"
    deep.mkdir(parents=True)
    repo = GitRepo.discover(deep, command_log)
    assert repo.root == builder.path.resolve()
    assert repo.common_dir == builder.path.resolve() / ".git"
    assert repo.common_dir.is_absolute()


def test_discover_outside_a_repository_raises_not_a_git_repository(
    tmp_path: Path, command_log: FakeCommandLog
) -> None:
    """Requirement 12.5: the not-a-git-repository exit code has to come from somewhere."""
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(NotAGitRepositoryError):
        GitRepo.discover(plain, command_log)


def test_discover_in_a_bare_repository_raises_not_a_git_repository(
    tmp_path: Path, command_log: FakeCommandLog
) -> None:
    """Measured: a bare repository answers ``fatal: this operation must be run in a work tree``."""
    bare = tmp_path / "bare.git"
    run_git(tmp_path, "init", "--quiet", "--bare", str(bare))
    with pytest.raises(NotAGitRepositoryError) as raised:
        GitRepo.discover(bare, command_log)
    assert "work tree" in str(raised.value)


def write_fake_git(path: Path, stdout: str, rc: int, stderr: str = "") -> str:
    """A stand-in ``git`` that prints exactly ``stdout``/``stderr`` and exits ``rc``."""
    path.write_text(
        "#!/usr/bin/env python3\nimport sys\n"
        f"sys.stdout.write({stdout!r})\nsys.stderr.write({stderr!r})\nsys.exit({rc})\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return str(path)


def test_discover_accepts_a_relative_working_directory(
    git_repo: MakeGitRepo, command_log: FakeCommandLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``cwd`` is a path input too, and a relative one must find the same repository.

    ``-C <cwd>`` and the resolution of git's answers share the process anchor, so this works —
    but nothing pinned it, and every other discovery test passes an absolute path.
    """
    builder = git_repo()
    monkeypatch.chdir(builder.path.parent)

    repo = GitRepo.discover(Path(builder.path.name), command_log)

    assert repo.root == builder.path.resolve()
    assert repo.git_dir == builder.path.resolve() / ".git"


@pytest.mark.parametrize(
    ("stdout", "rc", "why"),
    [
        ("/a\n/b\n", 0, "two paths where three were asked for"),
        ("/a\n/b\n/c\n/d\n", 0, "four paths where three were asked for"),
        ("/a\n/b\n/c\n", 1, "three paths but a failing status"),
    ],
)
def test_discover_refuses_a_git_that_answers_oddly(
    tmp_path: Path, command_log: FakeCommandLog, stdout: str, rc: int, why: str
) -> None:
    """Discovery trusts neither half of git's answer on its own.

    ``rev-parse`` is asked for three paths and its status is checked, and each guard is
    exercised alone here: with real git the two always agree, so nothing else in the suite can
    tell whether either is load-bearing. A wrong answer must not become a ``GitRepo`` whose
    root points somewhere arbitrary.

    **Too many** lines matters as much as too few: a wrapper that prints one warning line on
    stdout would make ``lines[0]`` that warning and the root something else entirely, so the
    count is checked for equality rather than for a minimum.
    """
    fake = write_fake_git(tmp_path / "fake-git", stdout, rc)
    with pytest.raises(NotAGitRepositoryError) as raised:
        GitRepo.discover(tmp_path, command_log, git=fake)
    # The stand-in writes nothing to stderr, which is the only way to reach the message's
    # fallback branch; without this assertion that branch is executed but never checked.
    assert "git reported nothing" in str(raised.value)


def test_discover_falls_back_when_git_says_only_whitespace(
    tmp_path: Path, command_log: FakeCommandLog
) -> None:
    """``stderr.strip()`` chooses the fallback message; without it a blank line wins.

    A git whose stderr is whitespace-only is the only input that separates the two forms, so
    the call was neither equivalent nor covered — it was simply undecided. Decided by test.
    """
    fake = write_fake_git(tmp_path / "blank-git", "", 128, stderr="   \n\t\n")
    with pytest.raises(NotAGitRepositoryError) as raised:
        GitRepo.discover(tmp_path, command_log, git=fake)
    assert "git reported nothing" in str(raised.value)


def test_a_failure_carries_stderr_without_surrounding_whitespace(
    git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """``AnalysisFailedError.stderr`` is trimmed, so consumers can render it verbatim.

    git terminates its diagnostics with a newline, so the untrimmed value always differs; the
    existing assertions used ``in`` and passed either way, leaving the call undecided.
    """
    builder = git_repo()
    builder.write("a.py", "a\n")
    builder.stage()
    builder.commit("init")

    with pytest.raises(AnalysisFailedError) as raised:
        open_repo(builder, command_log).diff_names("no-such-ref", "HEAD")

    stderr = raised.value.stderr
    assert stderr and stderr == stderr.strip()


def test_discover_records_the_command_it_ran(
    git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """Requirement 12.8: every external command is logged with its timing and status."""
    builder = git_repo()
    open_repo(builder, command_log)
    assert any("rev-parse" in line for line in command_log.commands)
    argv, seconds, rc = command_log.calls[-1]
    assert argv[0].endswith("git")
    assert seconds >= 0.0
    assert rc == 0


def test_discover_in_a_linked_worktree_separates_git_dir_from_common_dir(
    git_repo: MakeGitRepo, tmp_path: Path, command_log: FakeCommandLog
) -> None:
    """A linked worktree has its own git directory and shares the main common directory."""
    builder = git_repo()
    builder.write("a.py", "a\n")
    builder.stage()
    builder.commit("init")
    linked = tmp_path / "linked"
    builder.run("worktree", "add", "--quiet", "-b", "side", str(linked))
    repo = GitRepo.discover(linked, command_log)
    assert repo.root == linked.resolve()
    assert repo.git_dir == builder.path.resolve() / ".git" / "worktrees" / "linked"
    assert repo.common_dir == builder.path.resolve() / ".git"
    assert repo.git_dir != repo.common_dir


# --- HEAD -----------------------------------------------------------------------


def test_head_is_none_on_an_unborn_branch(
    git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """A fresh ``git init`` has no commit, and that is when a pre-commit hook first runs."""
    builder = git_repo()
    builder.write("new.py", "new\n")
    builder.stage()
    assert open_repo(builder, command_log).head() is None


def test_head_reports_a_broken_head_instead_of_calling_it_unborn(
    git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """A corrupt ``HEAD`` exits 128, not 1, and must not be mistaken for a fresh repository.

    Measured: git stops recognising the directory as a repository at all. Answering ``None``
    there would skip the before side, so every entity would look new and the ratchet would
    quietly stop finding anything (requirement 4.3).
    """
    builder = git_repo()
    builder.write("a.py", "a\n")
    builder.stage()
    builder.commit("init")
    repo = open_repo(builder, command_log)
    (builder.path / ".git" / "HEAD").write_text("not-a-ref-at-all\n", encoding="utf-8")

    with pytest.raises(AnalysisFailedError) as raised:
        repo.head()
    assert raised.value.stderr


def test_head_is_the_commit_hash_once_there_is_one(
    git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """The before side is read from this hash (requirement 4.3)."""
    builder = git_repo()
    builder.write("a.py", "a\n")
    builder.stage()
    committed = builder.commit("init")
    assert open_repo(builder, command_log).head() == committed


# --- staged changes -------------------------------------------------------------


def test_staged_changes_reads_adds_modifications_deletions_and_renames(
    git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """Measured stream: ``A\\0added.py\\0D\\0gone.py\\0M\\0keep.py\\0R100\\0old\\0new\\0``."""
    builder = git_repo()
    builder.write("pkg/old.py", FIVE_LINES)
    builder.write("keep.py", "keep\n")
    builder.write("gone.py", "gone\n")
    builder.stage()
    builder.commit("init")
    builder.rename("pkg/old.py", "pkg/new.py")
    builder.write("added.py", "added\n")
    builder.write("keep.py", "keep\nmore\n")
    builder.stage("added.py", "keep.py")
    builder.delete("gone.py")

    changes = open_repo(builder, command_log).staged_changes()

    by_path = {change.path: change for change in changes}
    assert len(changes) == 4
    assert by_path["added.py"].status == "A"
    assert by_path["added.py"].old_path is None
    assert by_path["keep.py"].status == "M"
    assert by_path["gone.py"].status == "D"
    assert by_path["pkg/new.py"].status == "R"
    assert by_path["pkg/new.py"].old_path == "pkg/old.py"


def test_staged_changes_on_an_unborn_branch_reports_every_file_as_added(
    git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """Measured: ``diff --cached`` compares against the empty tree when ``HEAD`` is unborn."""
    builder = git_repo()
    builder.write("pkg/first.py", "first\n")
    builder.stage()
    changes = open_repo(builder, command_log).staged_changes()
    assert [(change.status, change.path) for change in changes] == [("A", "pkg/first.py")]


def test_staged_changes_is_empty_when_nothing_is_staged(
    git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """An empty payload is a record stream with no records, not a one-record stream."""
    builder = git_repo()
    builder.write("a.py", "a\n")
    builder.stage()
    builder.commit("init")
    assert open_repo(builder, command_log).staged_changes() == []


def test_staged_changes_ignores_an_unstaged_edit(
    git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """Requirement 4.1: the gate judges the index, never the working tree."""
    builder = git_repo()
    builder.write("a.py", "a\n")
    builder.stage()
    builder.commit("init")
    builder.unstaged_edit("a.py", "edited only on disk\n")
    assert open_repo(builder, command_log).staged_changes() == []


@pytest.mark.skipif(sys.platform == "win32", reason="needs POSIX symlinks to make a typechange")
def test_staged_changes_treats_a_typechange_as_a_modification(
    git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """Measured: replacing a file with a symlink stages as ``T\\0f.txt\\0``, a one-path record."""
    builder = git_repo()
    builder.write("f.txt", "plain\n")
    builder.stage()
    builder.commit("init")
    (builder.path / "f.txt").unlink()
    (builder.path / "f.txt").symlink_to("elsewhere")
    builder.stage("f.txt")
    changes = open_repo(builder, command_log).staged_changes()
    assert [(change.status, change.path) for change in changes] == [("M", "f.txt")]


def test_staged_changes_refuses_an_unmerged_index(
    git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """Measured: a conflict stages as ``U\\0f.txt\\0``; nothing downstream can read that."""
    builder = git_repo()
    builder.write("f.txt", "base\n")
    builder.stage()
    builder.commit("init")
    builder.run("checkout", "--quiet", "-b", "side")
    builder.write("f.txt", "side\n")
    builder.stage()
    builder.commit("side")
    builder.run("checkout", "--quiet", "main")
    builder.write("f.txt", "main\n")
    builder.stage()
    builder.commit("main")
    merged = run_git(builder.path, "merge", "side")
    assert merged.returncode != 0, "the merge was supposed to conflict"

    with pytest.raises(AnalysisFailedError) as raised:
        open_repo(builder, command_log).staged_changes()
    assert "unmerged" in str(raised.value).lower()


# --- the record parser on its own ------------------------------------------------


def test_parse_name_status_reads_nothing_from_an_empty_payload() -> None:
    """git prints an empty payload, not a lone NUL, when nothing changed."""
    assert parse_name_status(b"") == []


def test_parse_name_status_keeps_a_final_path_that_is_not_nul_terminated() -> None:
    """A payload with no trailing NUL still yields its last record.

    Git terminates every record, but this function is public and documented as reading any
    ``--name-status -z`` stream. Popping the final field unconditionally would silently drop
    the last path — the guard that prevents it was annotated as an equivalent mutant purely
    because it survived, which showed only that nothing exercised this shape.
    """
    changes = parse_name_status(b"M\0f.txt")
    assert [(c.status, c.path) for c in changes] == [("M", "f.txt")]


def test_parse_name_status_consumes_two_paths_for_a_copy() -> None:
    """A ``C`` record carries two paths, and only the destination becomes the change.

    Git emits these whenever ``-C`` is in play — the fixed ``-M``-only argv this module builds
    never can — but :func:`parse_name_status` is public and reads any ``--name-status -z``
    stream, so the record still has to be consumed correctly or everything after it shifts.
    """
    changes = parse_name_status(b"C085\0src.py\0copy.py\0M\0after.py\0")
    assert [(change.status, change.path, change.old_path) for change in changes] == [
        ("A", "copy.py", None),
        ("M", "after.py", None),
    ]


def test_parse_name_status_reads_a_typechange_as_a_modification() -> None:
    """``T`` is a one-path record; reading it as two would swallow the next record."""
    changes = parse_name_status(b"T\0f.txt\0A\0next.py\0")
    read = [(change.status, change.path) for change in changes]
    assert read == [("M", "f.txt"), ("A", "next.py")]


def test_parse_name_status_refuses_a_record_missing_its_second_path() -> None:
    """A rename whose destination is missing is a truncated stream, not a one-path record."""
    with pytest.raises(AnalysisFailedError) as raised:
        parse_name_status(b"R100\0pkg/old.py\0")
    assert "R100" in str(raised.value)


def test_parse_name_status_refuses_a_record_missing_its_path() -> None:
    """A status token with nothing after it cannot be turned into a change."""
    with pytest.raises(AnalysisFailedError):
        parse_name_status(b"M\0")


def test_parse_name_status_refuses_an_unknown_status_token() -> None:
    """An unrecognised token means the stream is being read out of step; say so loudly."""
    with pytest.raises(AnalysisFailedError) as raised:
        parse_name_status(b"pkg/new.py\0M\0a.py\0")
    assert "pkg/new.py" in str(raised.value)


def test_staged_changes_detects_a_rename_even_when_the_repository_turns_renames_off(
    git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """Measured: ``diff.renames = false`` reports a rename as ``A`` plus ``D`` without ``-M``.

    Rename detection is on by default in modern git, so this switch is the only thing that
    proves the explicit ``-M`` is load-bearing: without it an operator's configuration decides
    whether the gate sees one moved file or one new file and one deleted one.
    """
    builder = git_repo()
    builder.write("pkg/old.py", FIVE_LINES)
    builder.stage()
    builder.commit("init")
    builder.run("config", "diff.renames", "false")
    builder.rename("pkg/old.py", "pkg/new.py")

    changes = open_repo(builder, command_log).staged_changes()

    assert [(c.status, c.path, c.old_path) for c in changes] == [("R", "pkg/new.py", "pkg/old.py")]


def test_diff_names_detects_a_rename_even_when_the_repository_turns_renames_off(
    git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """The commit-range comparison needs the same protection as the staged one."""
    builder = git_repo()
    builder.write("pkg/old.py", FIVE_LINES)
    builder.stage()
    builder.commit("init")
    builder.run("config", "diff.renames", "false")
    builder.rename("pkg/old.py", "pkg/new.py")
    builder.commit("rename")

    changes = open_repo(builder, command_log).diff_names("HEAD~1", "HEAD")

    assert [(c.status, c.path, c.old_path) for c in changes] == [("R", "pkg/new.py", "pkg/old.py")]


@pytest.mark.parametrize("token", [b"X", b"B"])
def test_parse_name_status_refuses_a_single_letter_status_it_does_not_know(token: bytes) -> None:
    """``X`` (unknown) and ``B`` (broken pairing) are real git statuses with no digits.

    They are the case the *membership* half of the guard exists for: the shape half accepts
    any one-character token, so without the membership check these would fall through to the
    mapping and raise ``KeyError`` instead of a typed error naming the token.
    """
    with pytest.raises(AnalysisFailedError) as raised:
        parse_name_status(token + b"\0f.txt\0")
    assert token.decode() in str(raised.value)


@pytest.mark.parametrize("token", [b"RX", b"R9X", b"RX9"])
def test_parse_name_status_refuses_a_status_whose_tail_is_not_a_score(token: bytes) -> None:
    """A valid letter followed by something that is not a similarity score.

    ``RX9`` is here because the score is read as ``token[1:]``: slicing from ``token[2:]``
    instead would accept it as a rename, and that mutant sits on the very line carrying an
    equivalence annotation — a reminder that annotating one clause of a line says nothing
    about the rest of it.
    """
    with pytest.raises(AnalysisFailedError) as raised:
        parse_name_status(token + b"\0old.py\0new.py\0")
    assert token.decode() in str(raised.value)


def test_parse_name_status_refuses_an_empty_status_token() -> None:
    """The empty token, asserted on the token it names rather than on any raise.

    This cell previously asserted only that *something* raised, which a mutant accepting the
    empty token as a modification still satisfied — it desynchronised the stream and raised one
    record later, naming ``'new.py'``. ``"" in message`` is trivially true, so the assertion
    has to look for the rendered empty token instead.
    """
    with pytest.raises(AnalysisFailedError) as raised:
        parse_name_status(b"\0old.py\0new.py\0")
    message = str(raised.value)
    assert "''" in message
    assert "new.py" not in message, "the failure must name the empty token, not a later field"


def test_parse_name_status_accepts_a_rename_with_no_similarity_score() -> None:
    """``R`` on its own is a well-formed rename token; the score is optional.

    The shape guard must not reject it, and it still consumes two paths.
    """
    changes = parse_name_status(b"R\0old.py\0new.py\0")
    assert [(c.status, c.path, c.old_path) for c in changes] == [("R", "new.py", "old.py")]


def test_parse_name_status_refuses_a_path_masquerading_as_a_status_token() -> None:
    """A desynchronised stream lands on a path, and plenty of paths start with a status letter.

    ``Makefile`` would otherwise read as a modification of whatever field came next, which is
    a silently wrong answer rather than a loud one — the failure mode a status-token-driven
    parser exists to avoid.
    """
    with pytest.raises(AnalysisFailedError) as raised:
        parse_name_status(b"Makefile\0src.py\0")
    assert "Makefile" in str(raised.value)


def test_parse_name_status_keeps_the_similarity_score_out_of_the_status() -> None:
    """The token is ``R100``/``R087``; only its first letter is the status."""
    changes = parse_name_status(b"R087\0old.py\0new.py\0")
    assert changes[0].status == "R"
    assert changes[0].path == "new.py"
    assert changes[0].old_path == "old.py"


# --- the index tree id -----------------------------------------------------------


def test_index_tree_id_matches_git_and_is_stable(
    git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """``write-tree`` is the identity of the index the after side is built from."""
    builder = git_repo()
    builder.write("a.py", "a\n")
    builder.stage()
    repo = open_repo(builder, command_log)
    assert repo.index_tree_id() == builder.run("write-tree")
    assert repo.index_tree_id() == repo.index_tree_id()


def test_index_tree_id_ignores_the_working_tree_but_follows_the_index(
    git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """Requirement 4.1 again, this time as a cache key: unstaged edits must not invalidate it."""
    builder = git_repo()
    builder.write("a.py", "a\n")
    builder.stage()
    builder.commit("init")
    repo = open_repo(builder, command_log)
    original = repo.index_tree_id()
    builder.unstaged_edit("a.py", "edited only on disk\n")
    assert repo.index_tree_id() == original
    builder.stage("a.py")
    assert repo.index_tree_id() != original


def test_index_tree_id_fails_loudly_on_an_unmerged_index(
    git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """Measured: ``write-tree`` exits 128 with ``error building trees`` while a conflict stands."""
    builder = git_repo()
    builder.write("f.txt", "base\n")
    builder.stage()
    builder.commit("init")
    builder.run("checkout", "--quiet", "-b", "side")
    builder.write("f.txt", "side\n")
    builder.stage()
    builder.commit("side")
    builder.run("checkout", "--quiet", "main")
    builder.write("f.txt", "main\n")
    builder.stage()
    builder.commit("main")
    run_git(builder.path, "merge", "side")

    with pytest.raises(AnalysisFailedError) as raised:
        open_repo(builder, command_log).index_tree_id()
    assert raised.value.command[-1] == "write-tree"


# --- name-status between two refs -------------------------------------------------


def test_diff_names_reads_a_rename_between_two_commits(
    git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """``explain --range`` compares two commits with the same record grammar."""
    builder = git_repo()
    builder.write("pkg/old.py", FIVE_LINES)
    builder.stage()
    builder.commit("init")
    builder.rename("pkg/old.py", "pkg/new.py")
    builder.commit("rename")

    changes = open_repo(builder, command_log).diff_names("HEAD~1", "HEAD")

    assert [(change.status, change.path, change.old_path) for change in changes] == [
        ("R", "pkg/new.py", "pkg/old.py")
    ]


def test_diff_names_survives_a_ref_name_that_is_also_a_tracked_path(
    git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """The trailing ``--`` is load-bearing, and only an ambiguous name shows it.

    ``diff_names`` takes revisions the *user* supplies for ``explain --range`` (req 9.1), and
    a branch called ``feature`` alongside a tracked file called ``feature`` is an ordinary
    repository. Measured without the terminator: ``fatal: ambiguous argument 'feature': both
    revision and filename``, exit 128. With it: exit 0 and ``M|feature|``.
    """
    builder = git_repo()
    builder.write("feature", "content\n")
    builder.stage()
    builder.commit("add a file named feature")
    builder.run("branch", "feature")
    builder.write("feature", "modified\n")
    builder.stage()
    builder.commit("modify it")

    changes = open_repo(builder, command_log).diff_names("feature", "HEAD")

    assert [(change.status, change.path) for change in changes] == [("M", "feature")]


OPTION_LOOKING_REFS = ["--output=pwned.txt", "--index-output=pwned.txt"]
"""Revisions that git would execute as options; requirement 9.1 lets the operator supply them."""


@pytest.mark.parametrize("ref", OPTION_LOOKING_REFS)
def test_diff_names_refuses_a_ref_that_looks_like_an_option(
    git_repo: MakeGitRepo, command_log: FakeCommandLog, ref: str
) -> None:
    """A revision must never be executed as a git option (measured, and it was).

    Without ``--end-of-options`` the shipped argv ran the operator's "revision" as a switch:
    ``diff --name-status -z -M --output=pwned.txt HEAD --`` exited 0, reported **no changes**,
    and wrote ``pwned.txt``. Every call runs ``-C <root>``, so the file landed in the working
    tree (requirement 2.2) while the caller was told nothing had changed — which would have
    made every entity look new and stopped the ratchet (requirement 4.3).

    The trailing ``--`` does not help: it separates revisions from *paths*, not from options.
    """
    builder = git_repo()
    builder.write("a.py", "one\n")
    builder.stage()
    builder.commit("first")
    builder.write("a.py", "two\n")
    builder.stage()
    builder.commit("second")
    before = tree_contents(builder.path)

    with pytest.raises(AnalysisFailedError):
        open_repo(builder, command_log).diff_names(ref, "HEAD")

    assert tree_contents(builder.path) == before, "the ref was executed as an option"


@pytest.mark.parametrize("ref", OPTION_LOOKING_REFS)
def test_export_commit_refuses_a_commit_that_looks_like_an_option(
    git_repo: MakeGitRepo, tmp_path: Path, command_log: FakeCommandLog, ref: str
) -> None:
    """``read-tree`` had no terminator at all, so the same injection worked there.

    Measured: ``read-tree --index-output=rt-pwned.txt HEAD`` exited 0 and wrote the file into
    the working tree, and the export then produced an empty before side.
    """
    builder = git_repo()
    builder.write("a.py", "one\n")
    builder.stage()
    builder.commit("first")
    before = tree_contents(builder.path)

    with pytest.raises(AnalysisFailedError):
        open_repo(builder, command_log).export_commit(ref, tmp_path / "before", None)

    assert tree_contents(builder.path) == before, "the commit was executed as an option"


def test_diff_names_fails_loudly_on_an_unknown_ref(
    git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """A typo in ``--range`` must not read as "nothing changed"."""
    builder = git_repo()
    builder.write("a.py", "a\n")
    builder.stage()
    builder.commit("init")
    with pytest.raises(AnalysisFailedError) as raised:
        open_repo(builder, command_log).diff_names("no-such-ref", "HEAD")
    assert raised.value.stderr


# --- exporting the index ----------------------------------------------------------


def test_export_index_writes_the_staged_bytes_not_the_working_tree(
    git_repo: MakeGitRepo, tmp_path: Path, command_log: FakeCommandLog
) -> None:
    """The behaviour the whole staged mode rests on (requirement 4.1).

    The file has three different contents at once — committed, staged and on disk — and the
    export must produce the middle one while leaving the working tree exactly as it was.
    """
    builder = git_repo()
    builder.write("pkg/deep/mod.py", "COMMITTED\n")
    builder.stage()
    builder.commit("init")
    builder.write("pkg/deep/mod.py", "STAGED\n")
    builder.stage("pkg/deep/mod.py")
    builder.unstaged_edit("pkg/deep/mod.py", "WORKTREE\n")
    before = tree_contents(builder.path)

    dest = tmp_path / "shadow" / "after"
    open_repo(builder, command_log).export_index(dest, None)

    assert (dest / "pkg" / "deep" / "mod.py").read_bytes() == b"STAGED\n"
    assert tree_contents(builder.path) == before


def test_export_index_puts_nested_paths_inside_the_destination(
    git_repo: MakeGitRepo, tmp_path: Path, command_log: FakeCommandLog
) -> None:
    """Measured: ``--prefix=/tmp/dest`` without the slash writes ``/tmp/destpkg/...`` instead."""
    builder = git_repo()
    builder.write("pkg/deep/mod.py", "deep\n")
    builder.write("top.py", "top\n")
    builder.stage()

    dest = tmp_path / "shadow" / "after"
    open_repo(builder, command_log).export_index(dest, None)

    assert (dest / "pkg" / "deep" / "mod.py").is_file()
    assert (dest / "top.py").is_file()
    assert [entry.name for entry in sorted((tmp_path / "shadow").iterdir())] == ["after"]


@pytest.mark.parametrize("side", ["index", "commit"])
def test_a_destination_blocked_by_a_regular_file_reports_a_typed_error(
    git_repo: MakeGitRepo, tmp_path: Path, command_log: FakeCommandLog, side: str
) -> None:
    """``mkdir`` raises a bare ``FileExistsError``; every other failure here is typed.

    This was the one path in the module where an ``OSError`` reached the caller unwrapped,
    which the CLI maps to the unexpected-error exit rather than the analysis-failure one.
    """
    builder = git_repo()
    builder.write("a.py", "a\n")
    builder.stage()
    head = builder.commit("init")
    blocked = tmp_path / "in-the-way"
    blocked.write_text("not a directory\n", encoding="utf-8")
    repo = open_repo(builder, command_log)

    with pytest.raises(AnalysisFailedError) as raised:
        if side == "index":
            repo.export_index(blocked, None)
        else:
            repo.export_commit(head, blocked, None)
    assert "in-the-way" in str(raised.value)


@pytest.mark.parametrize("side", ["index", "commit"])
def test_a_relative_destination_lands_beside_the_caller_not_inside_the_repository(
    git_repo: MakeGitRepo,
    tmp_path: Path,
    command_log: FakeCommandLog,
    monkeypatch: pytest.MonkeyPatch,
    side: str,
) -> None:
    """A relative ``dest`` is anchored to the caller's directory, never to the repository.

    Every git command here runs with ``-C <root>``, so an unresolved relative ``--prefix``
    would be read against the *repository* and the export would land inside the working tree —
    exactly what requirement 2.2 forbids. Nothing else in the suite passes a relative
    destination, so dropping the resolution was invisible until this test existed.
    """
    builder = git_repo()
    builder.write("src/s.py", "content\n")
    builder.stage()
    head = builder.commit("init")
    repo = open_repo(builder, command_log)
    elsewhere = tmp_path / "caller-cwd"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    if side == "index":
        repo.export_index(Path("shadow"), None)
    else:
        repo.export_commit(head, Path("shadow"), None)

    assert (elsewhere / "shadow" / "src" / "s.py").read_text() == "content\n"
    assert not (builder.path / "shadow").exists(), "the export landed inside the repository"


def test_export_index_with_a_path_list_exports_only_those_paths(
    git_repo: MakeGitRepo, tmp_path: Path, command_log: FakeCommandLog
) -> None:
    """The incremental sync re-exports the files that moved, not the whole index."""
    builder = git_repo()
    builder.write("pkg/wanted.py", "wanted\n")
    builder.write("pkg/other.py", "other\n")
    builder.stage()

    dest = tmp_path / "part"
    open_repo(builder, command_log).export_index(dest, ["pkg/wanted.py"])

    assert (dest / "pkg" / "wanted.py").read_bytes() == b"wanted\n"
    assert not (dest / "pkg" / "other.py").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="Windows paths cannot contain newlines")
@pytest.mark.parametrize("awkward", ["src/weird\nnewline.py", 'src/quo"te.py'])
def test_a_path_list_carries_paths_that_contain_shell_hostile_characters(
    git_repo: MakeGitRepo, tmp_path: Path, command_log: FakeCommandLog, awkward: str
) -> None:
    """The path list is NUL-separated, which is the only reason a newline can be in it.

    Measured: the same payload without ``-z`` splits ``src/weird\nnewline.py`` in half and
    exits 1 with ``src/weird is not in the cache`` / ``newline.py is not in the cache``. A
    plain path happens to survive the line-oriented reading, so an ordinary name proves
    nothing about ``-z`` — only a name that needs it does.

    Of the two cases, **only the newline has killing power**; the quoted name passes with or
    without ``-z`` and is kept as documentation that ``core.quotepath`` cannot corrupt the
    payload, not as evidence for the switch.
    """
    builder = git_repo()
    (builder.path / "src").mkdir()
    (builder.path / awkward).write_text("awkward name\n", encoding="utf-8")
    builder.stage()

    dest = tmp_path / "shadow"
    open_repo(builder, command_log).export_index(dest, [awkward])

    assert (dest / awkward).read_text(encoding="utf-8") == "awkward name\n"


def test_export_index_with_an_empty_path_list_runs_no_command(
    git_repo: MakeGitRepo, tmp_path: Path, command_log: FakeCommandLog
) -> None:
    """Nothing to export is not the same request as "export everything"."""
    builder = git_repo()
    builder.write("a.py", "a\n")
    builder.stage()
    repo = open_repo(builder, command_log)
    command_log.calls.clear()

    dest = tmp_path / "empty"
    repo.export_index(dest, [])

    assert dest.is_dir()
    assert list(dest.iterdir()) == []
    assert command_log.calls == []


def test_export_index_fails_loudly_for_a_path_that_is_not_in_the_index(
    git_repo: MakeGitRepo, tmp_path: Path, command_log: FakeCommandLog
) -> None:
    """Measured: ``git checkout-index: nosuch.py is not in the cache``, exit status 1."""
    builder = git_repo()
    builder.write("a.py", "a\n")
    builder.stage()
    with pytest.raises(AnalysisFailedError) as raised:
        open_repo(builder, command_log).export_index(tmp_path / "out", ["nosuch.py"])
    assert "checkout-index" in " ".join(raised.value.command)


# --- exporting a commit -----------------------------------------------------------


def test_export_commit_writes_the_committed_bytes(
    git_repo: MakeGitRepo, tmp_path: Path, command_log: FakeCommandLog
) -> None:
    """Requirement 4.3: the before side comes from ``HEAD``, not the index or the disk."""
    builder = git_repo()
    builder.write("pkg/deep/mod.py", "COMMITTED\n")
    builder.stage()
    head = builder.commit("init")
    builder.write("pkg/deep/mod.py", "STAGED\n")
    builder.stage("pkg/deep/mod.py")
    builder.unstaged_edit("pkg/deep/mod.py", "WORKTREE\n")
    before = tree_contents(builder.path)

    dest = tmp_path / "shadow" / "before"
    open_repo(builder, command_log).export_commit(head, dest, None)

    assert (dest / "pkg" / "deep" / "mod.py").read_bytes() == b"COMMITTED\n"
    assert tree_contents(builder.path) == before


def test_export_commit_with_a_path_list_exports_only_those_paths(
    git_repo: MakeGitRepo, tmp_path: Path, command_log: FakeCommandLog
) -> None:
    """The before side is only ever materialised for the files a change touches."""
    builder = git_repo()
    builder.write("pkg/wanted.py", "wanted\n")
    builder.write("pkg/other.py", "other\n")
    builder.stage()
    builder.commit("init")

    dest = tmp_path / "part"
    open_repo(builder, command_log).export_commit("HEAD", dest, ["pkg/wanted.py"])

    assert (dest / "pkg" / "wanted.py").read_bytes() == b"wanted\n"
    assert not (dest / "pkg" / "other.py").exists()


def test_export_commit_with_an_empty_path_list_runs_no_command(
    git_repo: MakeGitRepo, tmp_path: Path, command_log: FakeCommandLog
) -> None:
    """An empty selection must not become an export of the whole commit."""
    builder = git_repo()
    builder.write("a.py", "a\n")
    builder.stage()
    builder.commit("init")
    repo = open_repo(builder, command_log)
    command_log.calls.clear()

    dest = tmp_path / "empty"
    repo.export_commit("HEAD", dest, [])

    assert dest.is_dir()
    assert list(dest.iterdir()) == []
    assert command_log.calls == []


def test_export_commit_fails_loudly_on_an_unborn_head(
    git_repo: MakeGitRepo, tmp_path: Path, command_log: FakeCommandLog
) -> None:
    """Measured: ``read-tree`` of an unborn ``HEAD`` exits 128, ``Not a valid object name``."""
    builder = git_repo()
    builder.write("a.py", "a\n")
    builder.stage()
    with pytest.raises(AnalysisFailedError) as raised:
        open_repo(builder, command_log).export_commit("HEAD", tmp_path / "out", None)
    assert "read-tree" in " ".join(raised.value.command)


ATTRIBUTED_REPO = "tests/ export-ignore\nsubst.txt export-subst\n"
"""Two attributes ``git archive`` acts on and ``checkout-index`` ignores (both measured)."""


def build_attributed_repo(builder: GitRepoBuilder) -> str:
    """A commit holding everything that used to make the two shadows disagree."""
    builder.write(".gitattributes", ATTRIBUTED_REPO)
    builder.write("src/s.py", "code\n")
    builder.write("tests/test_a.py", "test\n")
    builder.write("subst.txt", "hash: $Format:%H$\n")
    builder.stage()
    return builder.commit("init")


def test_the_two_exports_agree_on_a_commit_the_index_matches(
    git_repo: MakeGitRepo, tmp_path: Path, command_log: FakeCommandLog
) -> None:
    """The property the ratchet rests on: with nothing staged, before and after are equal.

    Any file the before side lacks makes every entity in it look new, and any byte the before
    side rewrites is a metric difference the change did not cause (requirement 4.3).
    """
    builder = git_repo()
    head = build_attributed_repo(builder)
    repo = open_repo(builder, command_log)

    repo.export_index(tmp_path / "after", None)
    repo.export_commit(head, tmp_path / "before", None)

    assert exported_tree(tmp_path / "before") == exported_tree(tmp_path / "after")


def test_export_commit_keeps_a_file_marked_export_ignore(
    git_repo: MakeGitRepo, tmp_path: Path, command_log: FakeCommandLog
) -> None:
    """Measured: ``git archive`` drops it, ``checkout-index`` keeps it — so the gate keeps it.

    ``tests/ export-ignore`` is an ordinary release-packaging idiom; dropping those files from
    the before side alone would silently exempt them from the ratchet.
    """
    builder = git_repo()
    head = build_attributed_repo(builder)

    open_repo(builder, command_log).export_commit(head, tmp_path / "before", None)

    assert (tmp_path / "before" / "tests" / "test_a.py").read_text() == "test\n"


def test_export_commit_leaves_an_export_subst_placeholder_alone(
    git_repo: MakeGitRepo, tmp_path: Path, command_log: FakeCommandLog
) -> None:
    """Measured: ``git archive`` rewrites ``$Format:%H$`` to the commit hash; a checkout does not.

    The after side can never expand it, so expanding it on the before side would make the two
    shadows differ byte-wise with no change involved at all.
    """
    builder = git_repo()
    head = build_attributed_repo(builder)

    open_repo(builder, command_log).export_commit(head, tmp_path / "before", None)

    written = (tmp_path / "before" / "subst.txt").read_text()
    assert written == "hash: $Format:%H$\n"
    assert head not in written


@pytest.mark.skipif(sys.platform == "win32", reason="needs POSIX symlinks")
def test_export_commit_reproduces_committed_symlinks_verbatim(
    git_repo: MakeGitRepo, tmp_path: Path, command_log: FakeCommandLog
) -> None:
    """A symlink out of the tree or to an absolute path is ordinary repository content.

    Git stores all three as mode ``120000`` blobs and a checkout writes them back unchanged;
    anything that refuses them fails on the before side exactly where the after side succeeds.
    """
    builder = git_repo()
    builder.write("src/s.py", "code\n")
    (builder.path / "abslink").symlink_to("/etc/hostname")
    (builder.path / "rellink").symlink_to("../outside.txt")
    (builder.path / "oklink").symlink_to("src/s.py")
    builder.stage()
    head = builder.commit("init")

    open_repo(builder, command_log).export_commit(head, tmp_path / "before", None)

    written = exported_tree(tmp_path / "before")
    assert written["abslink"] == ("symlink", "/etc/hostname")
    assert written["rellink"] == ("symlink", "../outside.txt")
    assert written["oklink"] == ("symlink", "src/s.py")


def test_export_commit_does_not_disturb_the_repository_index(
    git_repo: MakeGitRepo, tmp_path: Path, command_log: FakeCommandLog
) -> None:
    """The throwaway index must not become the real one, in a hook or anywhere else.

    The staged content deliberately differs from the commit, so a leak shows up twice: in the
    exported bytes, and in the repository's own tree id.
    """
    builder = git_repo()
    builder.write("src/s.py", "COMMITTED\n")
    builder.stage()
    head = builder.commit("init")
    builder.write("src/s.py", "STAGED\n")
    builder.stage("src/s.py")
    repo = open_repo(builder, command_log)
    staged_tree = repo.index_tree_id()

    repo.export_commit(head, tmp_path / "before", None)

    assert (tmp_path / "before" / "src" / "s.py").read_text() == "COMMITTED\n"
    assert repo.index_tree_id() == staged_tree
    assert builder.staged_content("src/s.py") == "STAGED\n"


def test_export_index_overwrites_a_destination_that_is_already_populated(
    git_repo: MakeGitRepo, tmp_path: Path, command_log: FakeCommandLog
) -> None:
    """The incremental re-sync writes into an existing shadow on every run after the first.

    Measured: without ``-f`` the second export exits 1 with ``… already exists, no checkout``
    and leaves the stale content in place, which would freeze the after side forever.
    """
    builder = git_repo()
    builder.write("src/s.py", "FIRST\n")
    builder.stage()
    repo = open_repo(builder, command_log)
    dest = tmp_path / "after"
    repo.export_index(dest, None)

    builder.write("src/s.py", "SECOND\n")
    builder.stage("src/s.py")
    repo.export_index(dest, None)

    assert (dest / "src" / "s.py").read_text() == "SECOND\n"


def test_export_index_overwrites_a_populated_destination_for_a_path_list(
    git_repo: MakeGitRepo, tmp_path: Path, command_log: FakeCommandLog
) -> None:
    """The path-list form needs ``-f`` for the same reason, and it is the one 7.2 will use.

    Measured: the second ``checkout-index -z --stdin`` without ``-f`` exits 1 with
    ``… already exists, no checkout``, so the incremental re-sync of a changed file would
    fail while leaving the previous content in the shadow.
    """
    builder = git_repo()
    builder.write("src/s.py", "FIRST\n")
    builder.write("src/other.py", "other\n")
    builder.stage()
    repo = open_repo(builder, command_log)
    dest = tmp_path / "after"
    repo.export_index(dest, ["src/s.py"])

    builder.write("src/s.py", "SECOND\n")
    builder.stage("src/s.py")
    repo.export_index(dest, ["src/s.py"])

    assert (dest / "src" / "s.py").read_text() == "SECOND\n"


def test_export_commit_overwrites_a_populated_destination_for_a_path_list(
    git_repo: MakeGitRepo, tmp_path: Path, command_log: FakeCommandLog
) -> None:
    """The before shadow re-syncs one path at a time too, through the throwaway index."""
    builder = git_repo()
    builder.write("src/s.py", "FIRST\n")
    builder.stage()
    first = builder.commit("first")
    builder.write("src/s.py", "SECOND\n")
    builder.stage("src/s.py")
    second = builder.commit("second")
    repo = open_repo(builder, command_log)
    dest = tmp_path / "before"
    repo.export_commit(first, dest, ["src/s.py"])

    repo.export_commit(second, dest, ["src/s.py"])

    assert (dest / "src" / "s.py").read_text() == "SECOND\n"


def test_export_commit_overwrites_a_destination_that_is_already_populated(
    git_repo: MakeGitRepo, tmp_path: Path, command_log: FakeCommandLog
) -> None:
    """The before shadow is re-synced too, whenever ``HEAD`` moves."""
    builder = git_repo()
    builder.write("src/s.py", "FIRST\n")
    builder.stage()
    first = builder.commit("first")
    builder.write("src/s.py", "SECOND\n")
    builder.stage("src/s.py")
    second = builder.commit("second")
    repo = open_repo(builder, command_log)
    dest = tmp_path / "before"
    repo.export_commit(first, dest, None)

    repo.export_commit(second, dest, None)

    assert (dest / "src" / "s.py").read_text() == "SECOND\n"


def test_export_commit_writes_only_the_shadow_tree(
    git_repo: MakeGitRepo, tmp_path: Path, command_log: FakeCommandLog
) -> None:
    """Requirement 2.2: the throwaway index lives elsewhere and leaves nothing in ``dest``."""
    builder = git_repo()
    builder.write("a.py", "a\n")
    builder.stage()
    builder.commit("init")

    dest = tmp_path / "shadow"
    open_repo(builder, command_log).export_commit("HEAD", dest, None)

    assert [entry.name for entry in sorted(dest.rglob("*"))] == ["a.py"]


# --- the before side really is the commit ------------------------------------------


def build_three_way_repo(builder: GitRepoBuilder) -> tuple[str, str]:
    """A repository where the old commit, ``HEAD`` and the index all hold different bytes.

    Only this shape can tell the three mistakes apart: exporting ``HEAD`` instead of the
    commit asked for, exporting the live index instead of the commit, and exporting the
    working tree. Two of those returned a plausible-looking shadow and no error at all.
    """
    builder.write("src/s.py", "FIRST\n")
    builder.write("src/other.py", "other\n")
    builder.stage()
    first = builder.commit("first")
    builder.write("src/s.py", "SECOND\n")
    builder.stage("src/s.py")
    second = builder.commit("second")
    builder.write("src/s.py", "STAGED\n")
    builder.stage("src/s.py")
    builder.unstaged_edit("src/s.py", "WORKTREE\n")
    return first, second


@pytest.mark.parametrize("form", ["whole-tree", "path-list"])
def test_export_commit_exports_the_commit_asked_for_not_head_or_the_index(
    git_repo: MakeGitRepo, tmp_path: Path, command_log: FakeCommandLog, form: str
) -> None:
    """The before side of ``explain --range`` is an arbitrary commit, not ``HEAD`` (req 9.1).

    Both export forms are checked because they are two argv shapes through one code path, and
    the path-list form is the one the incremental before-side re-sync will use. Each wrong
    answer is a *different* string, so a failure says which mistake was made: ``SECOND`` means
    the commit argument was ignored, ``STAGED`` means the throwaway index was lost and the
    live index got materialised, ``WORKTREE`` means the working tree was read.
    """
    builder = git_repo()
    first, _second = build_three_way_repo(builder)
    paths = None if form == "whole-tree" else ["src/s.py"]

    dest = tmp_path / "before"
    open_repo(builder, command_log).export_commit(first, dest, paths)

    assert (dest / "src" / "s.py").read_text() == "FIRST\n"


def test_export_commit_fails_loudly_for_a_revision_that_does_not_exist(
    git_repo: MakeGitRepo, tmp_path: Path, command_log: FakeCommandLog
) -> None:
    """A genuinely unknown revision, as opposed to an unborn ``HEAD``.

    Measured: ``read-tree`` exits 128 with ``fatal: Not a valid object name``. The revision
    has to reach git verbatim, so the argv is asserted rather than just the failure.
    """
    builder = git_repo()
    builder.write("a.py", "a\n")
    builder.stage()
    builder.commit("init")

    with pytest.raises(AnalysisFailedError) as raised:
        open_repo(builder, command_log).export_commit("no-such-rev", tmp_path / "out", None)

    assert raised.value.command[-3:] == ["read-tree", "--end-of-options", "no-such-rev"]
    assert "fatal:" in raised.value.stderr


# --- tracked files ----------------------------------------------------------------


def test_tracked_files_lists_index_paths_relative_to_the_root(
    git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """The paths are what ``und add`` and the include/exclude filters are matched against."""
    builder = git_repo()
    builder.write("pkg/deep/mod.py", "deep\n")
    builder.write("top.py", "top\n")
    builder.stage()
    builder.commit("init")
    builder.write("staged_only.py", "new\n")
    builder.stage("staged_only.py")
    builder.write("untracked.py", "untracked\n")

    tracked = open_repo(builder, command_log).tracked_files()

    assert tracked == ["pkg/deep/mod.py", "staged_only.py", "top.py"]


def test_tracked_files_reports_a_conflicted_path_once(
    git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """Measured: ``ls-files -z`` prints an unmerged path three times, once per stage."""
    builder = git_repo()
    builder.write("f.txt", "base\n")
    builder.stage()
    builder.commit("init")
    builder.run("checkout", "--quiet", "-b", "side")
    builder.write("f.txt", "side\n")
    builder.stage()
    builder.commit("side")
    builder.run("checkout", "--quiet", "main")
    builder.write("f.txt", "main\n")
    builder.stage()
    builder.commit("main")
    run_git(builder.path, "merge", "side")

    assert open_repo(builder, command_log).tracked_files() == ["f.txt"]


# --- the hooks directory ------------------------------------------------------------
#
# ``global_`` is the one discriminator in this module that could not be collapsed away, and
# four separate defects have hidden in it. Branch coverage was not enough — each defect lived
# in a VALUE CLASS of ``core.hooksPath`` that happened to be tested on one branch only. So the
# classes are enumerated here, and every cell below has a test:
#
#   class     | hooks_dir()                        | hooks_dir(global_=True)
#   ----------+------------------------------------+---------------------------------------
#   unset     | <git dir>/hooks                    | XDG fallback, and ~/.config without XDG
#   absolute  | verbatim                           | normalised (``..`` collapses)
#   relative  | anchored to the worktree root      | REFUSED - no single global location
#   empty     | REFUSED - resolves to the root     | treated as unset -> fallback
#   tilde     | expanded by git itself             | expanded by ``--type=path``
#
# The two cells that had no test are the ones that were wrong or unknown: relative/global was
# resolved against the process working directory, and tilde/repository was never checked at
# all. Adding a value class means adding a row here, on both branches.


def test_hooks_dir_defaults_to_the_git_directory(
    git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """Requirement 11.1: with no configuration the shim goes into ``.git/hooks``."""
    builder = git_repo()
    repo = open_repo(builder, command_log)
    assert repo.hooks_dir() == builder.path.resolve() / ".git" / "hooks"


def test_hooks_dir_honours_a_relative_core_hooks_path(
    git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """Measured: a relative ``core.hooksPath`` resolves against the working tree root."""
    builder = git_repo()
    builder.run("config", "core.hooksPath", "my-hooks")
    repo = open_repo(builder, command_log)
    assert repo.hooks_dir() == builder.path.resolve() / "my-hooks"


def test_hooks_dir_honours_an_absolute_core_hooks_path(
    git_repo: MakeGitRepo, tmp_path: Path, command_log: FakeCommandLog
) -> None:
    """An absolute ``core.hooksPath`` is used verbatim, wherever it points."""
    builder = git_repo()
    elsewhere = tmp_path / "shared-hooks"
    builder.run("config", "core.hooksPath", str(elsewhere))
    repo = open_repo(builder, command_log)
    assert repo.hooks_dir() == elsewhere


def test_hooks_dir_honours_a_global_core_hooks_path(
    git_repo: MakeGitRepo, tmp_path: Path, git_env: GitEnv, command_log: FakeCommandLog
) -> None:
    """``core.hooksPath`` set only in the user's configuration still redirects this repository."""
    builder = git_repo()
    shared = tmp_path / "user-hooks"
    git_env.set_global_hooks_path(str(shared))
    repo = open_repo(builder, command_log)
    assert repo.hooks_dir() == shared


def test_hooks_dir_prefers_the_repository_over_the_global_setting(
    git_repo: MakeGitRepo, tmp_path: Path, git_env: GitEnv, command_log: FakeCommandLog
) -> None:
    """Local configuration wins, which is why the value is read through git and not a file."""
    builder = git_repo()
    git_env.set_global_hooks_path(str(tmp_path / "user-hooks"))
    local = tmp_path / "repo-hooks"
    builder.run("config", "core.hooksPath", str(local))
    repo = open_repo(builder, command_log)
    assert repo.hooks_dir() == local


def test_hooks_dir_in_a_linked_worktree_points_at_the_shared_hooks(
    git_repo: MakeGitRepo, tmp_path: Path, command_log: FakeCommandLog
) -> None:
    """Measured: hooks live in the common directory, not in the worktree's own git directory."""
    builder = git_repo()
    builder.write("a.py", "a\n")
    builder.stage()
    builder.commit("init")
    linked = tmp_path / "linked"
    builder.run("worktree", "add", "--quiet", "-b", "side", str(linked))
    repo = GitRepo.discover(linked, command_log)
    assert repo.hooks_dir() == builder.path.resolve() / ".git" / "hooks"


@pytest.mark.parametrize("scope", ["repository", "global"])
def test_hooks_dir_refuses_a_core_hooks_path_set_to_the_empty_string(
    git_repo: MakeGitRepo, git_env: GitEnv, command_log: FakeCommandLog, scope: str
) -> None:
    """Measured: an empty value answers ``./`` at **status 0**, silently meaning the root.

    Nothing about that is usable — a ``pre-commit`` in the working-tree root was measured not
    to fire — and passing it on is the one answer that would make ``install-hook`` write into
    the repository, against requirement 2.2. So it is refused rather than reported.

    **Both scopes are exercised on purpose.** The probe behind this refusal reads the
    *effective* value across every configuration level, and a version of it that asked only
    ``--global`` passed a suite that set the value globally and never at the repository level.
    Measured with the value set per-repository and global unset: ``config --get`` answers
    status 0 and empty while ``config --global --get`` answers status 1, so the global-only
    probe would have handed back the working-tree root.
    """
    builder = git_repo()
    if scope == "global":
        git_env.set_global_hooks_path("")
    else:
        builder.run("config", "core.hooksPath", "")
    repo = open_repo(builder, command_log)

    with pytest.raises(ConfigError) as raised:
        repo.hooks_dir()
    assert "core.hooksPath" in str(raised.value)


def test_hooks_dir_still_reports_an_explicit_dot(
    git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """``core.hooksPath = .`` resolves to the root too, and is a deliberate operator choice.

    Only the empty string is refused. Deciding whether to *install* into a directory inside
    the working tree belongs to the hook installer (7.3), which can compare against ``root``;
    this method's job is to report what git resolves.
    """
    builder = git_repo()
    builder.run("config", "core.hooksPath", ".")
    repo = open_repo(builder, command_log)
    assert repo.hooks_dir() == builder.path.resolve()


def test_hooks_dir_resolves_a_relative_value_per_worktree(
    git_repo: MakeGitRepo, tmp_path: Path, command_log: FakeCommandLog
) -> None:
    """The docstring claims per-worktree resolution of a relative value; this pins it.

    Measured: git anchors a relative ``core.hooksPath`` to *each* worktree's own root, so the
    linked worktree gets its own directory rather than the main repository's.
    """
    builder = git_repo()
    builder.write("a.py", "a\n")
    builder.stage()
    builder.commit("init")
    builder.run("config", "core.hooksPath", "my-hooks")
    linked = tmp_path / "linked"
    builder.run("worktree", "add", "--quiet", "-b", "side", str(linked))

    main = open_repo(builder, command_log)
    other = GitRepo.discover(linked, command_log)

    assert main.hooks_dir() == builder.path.resolve() / "my-hooks"
    assert other.hooks_dir() == linked.resolve() / "my-hooks"


def test_global_hooks_dir_reads_the_global_configuration(
    git_repo: MakeGitRepo, tmp_path: Path, git_env: GitEnv, command_log: FakeCommandLog
) -> None:
    """Requirement 11.9: ``install-hook --global`` reports the path it used."""
    builder = git_repo()
    shared = tmp_path / "user-hooks"
    git_env.set_global_hooks_path(str(shared))
    repo = open_repo(builder, command_log)
    assert repo.hooks_dir(global_=True) == shared


def test_global_hooks_dir_refuses_a_relative_value(
    git_repo: MakeGitRepo, git_env: GitEnv, command_log: FakeCommandLog
) -> None:
    """A global hooks path must name one place; a relative one names a different place each time.

    Measured: ``config --get --type=path`` expands ``~`` but returns a relative value raw, so
    resolving it here anchors it to the *process* working directory — one repository with one
    global setting gave three different answers from three directories, one of them inside an
    unrelated repository's working tree. Git never does this: it resolves a relative value per
    repository against that worktree's root, which is not a global location at all.
    """
    builder = git_repo()
    git_env.set_global_hooks_path("myhooks")
    repo = open_repo(builder, command_log)

    with pytest.raises(ConfigError) as raised:
        repo.hooks_dir(global_=True)
    assert "core.hooksPath" in str(raised.value)


def test_global_hooks_dir_normalises_an_absolute_value(
    git_repo: MakeGitRepo, tmp_path: Path, git_env: GitEnv, command_log: FakeCommandLog
) -> None:
    """The surviving absolute values are normalised, so ``..`` segments collapse.

    This is what ``.resolve()`` is for once relative values are refused, and it is the only
    thing that distinguishes it from a bare ``Path(value)``.
    """
    builder = git_repo()
    shared = tmp_path / "hooks-home"
    shared.mkdir()
    git_env.set_global_hooks_path(f"{tmp_path}/hooks-home/../hooks-home")
    repo = open_repo(builder, command_log)

    assert repo.hooks_dir(global_=True) == shared


def test_hooks_dir_expands_a_tilde_in_a_repository_level_value(
    git_repo: MakeGitRepo, git_env: GitEnv, command_log: FakeCommandLog
) -> None:
    """The repository branch's ``~`` class, which had no test on either side of ``global_``.

    Measured: git expands it itself — ``rev-parse --git-path hooks`` answered an absolute
    ``/home/me/tilde-repo`` for ``core.hooksPath = ~/tilde-repo`` — so nothing here has to,
    and the value must not be re-anchored to the worktree root the way a relative one is.
    """
    builder = git_repo()
    builder.run("config", "core.hooksPath", "~/tilde-repo")
    repo = open_repo(builder, command_log)

    assert repo.hooks_dir() == git_env.home / "tilde-repo"


def test_global_hooks_dir_expands_a_tilde(
    git_repo: MakeGitRepo, git_env: GitEnv, command_log: FakeCommandLog
) -> None:
    """Measured: ``git config --get --type=path`` is what expands ``~`` the way git does."""
    builder = git_repo()
    git_env.set_global_hooks_path("~/tilde-hooks")
    repo = open_repo(builder, command_log)
    assert repo.hooks_dir(global_=True) == git_env.home / "tilde-hooks"


@pytest.mark.parametrize("bad", ["myconf", "~/tconf", ".config"])
def test_global_hooks_dir_ignores_a_relative_xdg_config_home(
    git_repo: MakeGitRepo,
    git_env: GitEnv,
    command_log: FakeCommandLog,
    monkeypatch: pytest.MonkeyPatch,
    bad: str,
) -> None:
    """A relative ``XDG_CONFIG_HOME`` is ignored, not anchored to the current directory.

    Measured from inside a repository, the unguarded form produced ``<repo>/myconf/git/hooks``
    and ``<repo>/~/tconf/git/hooks`` — inside the working tree, which is where
    ``install-hook --global`` would then write, against requirement 2.2. The XDG Base
    Directory specification says a value that is not an absolute path must be ignored, so this
    falls back to ``$HOME`` rather than refusing.
    """
    builder = git_repo()
    monkeypatch.setenv("XDG_CONFIG_HOME", bad)
    monkeypatch.chdir(builder.path)
    repo = open_repo(builder, command_log)

    found = repo.hooks_dir(global_=True)

    assert found == git_env.home / ".config" / "git" / "hooks"
    assert builder.path not in found.parents


def test_global_hooks_dir_ignores_an_empty_xdg_config_home(
    git_repo: MakeGitRepo,
    git_env: GitEnv,
    command_log: FakeCommandLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty value is no value; it falls back like an unset one."""
    builder = git_repo()
    monkeypatch.setenv("XDG_CONFIG_HOME", "")
    repo = open_repo(builder, command_log)
    assert repo.hooks_dir(global_=True) == git_env.home / ".config" / "git" / "hooks"


def test_global_hooks_dir_normalises_an_absolute_xdg_config_home(
    git_repo: MakeGitRepo,
    tmp_path: Path,
    command_log: FakeCommandLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The surviving absolute values are normalised, which is what ``.resolve()`` is for here.

    This line carried a "known equivalent mutant" annotation claiming the base was always
    absolute so the call only normalised. Both halves were false — measured with
    ``XDG_CONFIG_HOME=.config`` the two forms differ — so the claim is gone and the call is
    pinned instead.
    """
    builder = git_repo()
    conf = tmp_path / "xdg-real"
    conf.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", f"{tmp_path}/xdg-real/../xdg-real")
    repo = open_repo(builder, command_log)
    assert repo.hooks_dir(global_=True) == conf / "git" / "hooks"


def test_global_hooks_dir_refuses_a_relative_home(
    git_repo: MakeGitRepo, command_log: FakeCommandLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``HOME`` is the other arm of the same fallback, and has no specified default.

    Measured: ``Path.home()`` returns the relative value verbatim, so the answer would again
    depend on where the process stands. Unlike ``XDG_CONFIG_HOME`` there is nothing to fall
    back to, so it is refused rather than guessed at.
    """
    builder = git_repo()
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", "relative-home")
    repo = open_repo(builder, command_log)

    with pytest.raises(ConfigError) as raised:
        repo.hooks_dir(global_=True)
    assert "HOME" in str(raised.value)


def test_global_hooks_dir_falls_back_to_home_config_without_xdg(
    git_repo: MakeGitRepo,
    git_env: GitEnv,
    command_log: FakeCommandLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half of the fallback: ``XDG_CONFIG_HOME`` unset means ``~/.config``.

    The suite otherwise always sets that variable, so this branch of ``_user_config_dir`` had
    no test at all — the same "sibling branch unverified" shape as the two blocking defects.
    """
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    builder = git_repo()
    repo = open_repo(builder, command_log)
    assert repo.hooks_dir(global_=True) == git_env.home / ".config" / "git" / "hooks"


def test_global_hooks_dir_reports_a_configuration_git_cannot_read(
    git_repo: MakeGitRepo, git_env: GitEnv, command_log: FakeCommandLog
) -> None:
    """A probe that fails outright is not "unset" (measured: a bad config line exits 128).

    Only status 1 means the key is absent. Folding 128 into the fallback would answer with a
    hooks directory invented out of a broken configuration instead of reporting it.

    The configuration is broken *after* discovery on purpose: measured, a bad config line
    makes every git command in the repository exit 128, discovery included, so corrupting it
    first would never reach the code under test.
    """
    builder = git_repo()
    repo = open_repo(builder, command_log)
    git_env.global_config.write_text("this is not valid git config\n", encoding="utf-8")

    with pytest.raises(AnalysisFailedError) as raised:
        repo.hooks_dir(global_=True)
    assert "bad config" in raised.value.stderr


def test_global_hooks_dir_falls_back_to_the_xdg_location(
    git_repo: MakeGitRepo, git_env: GitEnv, command_log: FakeCommandLog
) -> None:
    """git has no default global hooks directory, so one is chosen beside the user's config."""
    builder = git_repo()
    repo = open_repo(builder, command_log)
    assert repo.hooks_dir(global_=True) == git_env.config / "git" / "hooks"


def test_global_hooks_dir_treats_an_empty_setting_as_unset(
    git_repo: MakeGitRepo, git_env: GitEnv, command_log: FakeCommandLog
) -> None:
    """Measured: ``core.hooksPath = `` answers status **0** with empty output, not status 1.

    Reading that as a failure produced the nonsense "``config …`` failed with exit status 0";
    the user has simply named no directory, which is what "unset" means.
    """
    builder = git_repo()
    git_env.set_global_hooks_path("")
    repo = open_repo(builder, command_log)
    assert repo.hooks_dir(global_=True) == git_env.config / "git" / "hooks"


def test_global_hooks_dir_ignores_a_repository_level_setting(
    git_repo: MakeGitRepo, tmp_path: Path, git_env: GitEnv, command_log: FakeCommandLog
) -> None:
    """``--global`` means the user's hooks, whatever this repository happens to say."""
    builder = git_repo()
    builder.run("config", "core.hooksPath", str(tmp_path / "repo-hooks"))
    repo = open_repo(builder, command_log)
    assert repo.hooks_dir(global_=True) == git_env.config / "git" / "hooks"


# --- what the subprocess environment actually looks like ------------------------------


SPY_SOURCE = """#!/usr/bin/env python3
import json, os, sys
WATCHED = ("GIT_INDEX_FILE", "PATH", "HOME", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM")
with open({log!r}, "a", encoding="utf-8") as fh:
    seen = dict((key, os.environ.get(key)) for key in WATCHED)
    fh.write(json.dumps({{"argv": sys.argv[1:], "env": seen}}) + chr(10))
os.execv({git!r}, [{git!r}] + sys.argv[1:])
"""
"""A ``git`` that records its argv and environment and then becomes the real git.

Nothing about git's behaviour is stubbed — it ``execv``s the real executable — so the tests
below still exercise real plumbing while being able to say exactly which call received
``GIT_INDEX_FILE`` and what else was in its environment.
"""


@dataclass(frozen=True)
class SpiedCall:
    """One recorded ``git`` invocation: its arguments and the environment it saw."""

    argv: list[str]
    env: dict[str, str | None]

    @property
    def subcommand(self) -> str:
        """The git subcommand, which sits after the leading ``-C <root>``."""
        return self.argv[2] if len(self.argv) > 2 else ""


@dataclass(frozen=True)
class GitSpy:
    """The recording ``git`` shim and the transcript it appends to."""

    executable: str
    log: Path

    def calls(self) -> list[SpiedCall]:
        """Every recorded invocation, in order."""
        text = self.log.read_text(encoding="utf-8") if self.log.exists() else ""
        found: list[SpiedCall] = []
        for line in text.splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            found.append(
                SpiedCall(
                    argv=[str(item) for item in raw["argv"]],
                    env={str(k): None if v is None else str(v) for k, v in raw["env"].items()},
                )
            )
        return found

    def env_of(self, subcommand: str) -> list[dict[str, str | None]]:
        """The environments of every call whose git subcommand is ``subcommand``."""
        return [call.env for call in self.calls() if call.subcommand == subcommand]


@pytest.fixture
def git_spy(tmp_path: Path) -> GitSpy:
    """A ``git`` shim that transcribes each call before delegating to the real one."""
    real = shutil.which("git")
    assert real is not None, "these tests need a real git on PATH"
    log = tmp_path / "git-calls.jsonl"
    spy = tmp_path / "spy-git"
    spy.write_text(SPY_SOURCE.format(log=str(log), git=real), encoding="utf-8")
    spy.chmod(0o755)
    return GitSpy(executable=str(spy), log=log)


def test_the_throwaway_index_reaches_exactly_the_two_calls_that_need_it(
    git_repo: MakeGitRepo,
    tmp_path: Path,
    command_log: FakeCommandLog,
    git_spy: GitSpy,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``GIT_INDEX_FILE`` must be scoped to the before-side export and nothing else.

    The ambient environment already carries ``GIT_INDEX_FILE`` — a hook always does — pointed
    at the repository's real index, so this proves both directions at once: the two calls that
    materialise the commit get the throwaway index, and every other call still gets whatever
    the caller had.
    """
    builder = git_repo()
    first, _second = build_three_way_repo(builder)
    ambient = str(builder.path / ".git" / "index")
    monkeypatch.setenv("GIT_INDEX_FILE", ambient)
    repo = GitRepo.discover(builder.path, command_log, git=git_spy.executable)

    repo.export_index(tmp_path / "after", None)
    repo.export_commit(first, tmp_path / "before", ["src/s.py"])

    assert git_spy.calls(), "discover must run through the git executable it was given"
    assert git_spy.calls()[0].subcommand == "rev-parse", "the first spied call is discovery"

    read_tree = git_spy.env_of("read-tree")
    assert len(read_tree) == 1
    throwaway = read_tree[0]["GIT_INDEX_FILE"]
    assert throwaway is not None and throwaway != ambient

    checkouts = git_spy.env_of("checkout-index")
    assert len(checkouts) == 2
    assert checkouts[0]["GIT_INDEX_FILE"] == ambient, "the after side must use the real index"
    assert checkouts[1]["GIT_INDEX_FILE"] == throwaway, "the before side must use the temp one"

    for call in git_spy.calls():
        if call.subcommand not in ("read-tree", "checkout-index"):
            assert call.env["GIT_INDEX_FILE"] == ambient


def test_the_scoped_environment_keeps_everything_else_the_caller_had(
    git_repo: MakeGitRepo, tmp_path: Path, command_log: FakeCommandLog, git_spy: GitSpy
) -> None:
    """The before-side calls get one variable *added*, not a hand-built environment.

    A replacement environment holding only ``GIT_INDEX_FILE`` would strip ``PATH``, ``HOME``
    and the ``GIT_CONFIG_*`` pointers, so git would silently start reading the developer's
    real configuration — or fail to start at all — in a way no assertion on exported bytes
    would notice.
    """
    builder = git_repo()
    builder.write("a.py", "a\n")
    builder.stage()
    head = builder.commit("init")
    repo = GitRepo.discover(builder.path, command_log, git=git_spy.executable)

    repo.export_commit(head, tmp_path / "before", None)

    inherited = ("PATH", "HOME", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM")
    for env in git_spy.env_of("read-tree") + git_spy.env_of("checkout-index"):
        for key in inherited:
            assert env[key] == os.environ[key], f"{key} was dropped from the scoped environment"


def test_each_export_gets_its_own_throwaway_index_and_cleans_it_up(
    git_repo: MakeGitRepo, tmp_path: Path, command_log: FakeCommandLog, git_spy: GitSpy
) -> None:
    """One temporary index per call, removed afterwards, outside the repository.

    A fixed shared path would make two concurrent exports read-tree over each other, and a
    directory that is never removed leaks one index per run forever.
    """
    builder = git_repo()
    first, second = build_three_way_repo(builder)
    repo = GitRepo.discover(builder.path, command_log, git=git_spy.executable)

    repo.export_commit(first, tmp_path / "one", None)
    repo.export_commit(second, tmp_path / "two", None)

    used = [env["GIT_INDEX_FILE"] for env in git_spy.env_of("read-tree")]
    assert len(used) == 2
    assert used[0] != used[1], "a shared path would break two exports running at once"
    for entry in used:
        assert entry is not None
        index = Path(entry)
        assert not index.exists(), "the throwaway index outlived the call that made it"
        assert builder.path not in index.parents, "it was written inside the working tree"
        assert (builder.path / ".git") not in index.parents, "it was written inside .git"


def test_two_exports_running_at_once_do_not_collide(
    git_repo: MakeGitRepo, tmp_path: Path, command_log: FakeCommandLog
) -> None:
    """The before side is materialised per side and per run; nothing serialises the calls.

    Every worker gets its own destination and its own slot in the results, and any exception
    is re-raised in the main thread. The first version of this test keyed results by commit
    with two workers per commit, so a worker that died was masked by its twin and pytest
    downgraded the crash to a warning — the same "one branch covers for its sibling" shape
    this module keeps tripping over, this time in the test itself.
    """
    builder = git_repo()
    first, second = build_three_way_repo(builder)
    repo = open_repo(builder, command_log)
    plan = [(first, "FIRST\n"), (second, "SECOND\n"), (first, "FIRST\n"), (second, "SECOND\n")]
    seen: dict[int, str] = {}
    failures: dict[int, BaseException] = {}

    def export(slot: int, commit: str) -> None:
        try:
            dest = tmp_path / f"worker-{slot}"
            repo.export_commit(commit, dest, ["src/s.py"])
            seen[slot] = (dest / "src" / "s.py").read_text()
        except BaseException as broken:  # noqa: BLE001 - re-raised in the main thread below
            failures[slot] = broken

    threads = [
        threading.Thread(target=export, args=(slot, commit))
        for slot, (commit, _expected) in enumerate(plan)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not failures, f"workers raised: {failures}"
    assert seen == {slot: expected for slot, (_commit, expected) in enumerate(plan)}


# --- paths that are not text ----------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="Windows paths are not arbitrary bytes")
def test_a_path_that_is_not_valid_utf8_survives_the_whole_round_trip(
    git_repo: MakeGitRepo, tmp_path: Path, command_log: FakeCommandLog
) -> None:
    """Measured: ``-z`` output carries raw bytes — ``caf\xe9.py`` arrives as ``caf\351.py``.

    The name has to survive being decoded for the models and encoded again for
    ``checkout-index --stdin``. Decoding with ``replace`` would round-trip it into U+FFFD and
    hand git a path it has never heard of, so the export would fail on a file git is perfectly
    happy with.
    """
    builder = git_repo()
    latin1 = os.fsdecode(b"caf\xe9.py")
    (builder.path / latin1).write_bytes(b"latin-1 name\n")
    builder.stage()

    repo = open_repo(builder, command_log)
    assert repo.tracked_files() == [latin1]
    assert [change.path for change in repo.staged_changes()] == [latin1]

    dest = tmp_path / "shadow"
    repo.export_index(dest, [latin1])
    assert (dest / latin1).read_bytes() == b"latin-1 name\n"


# --- logging and failure mapping ------------------------------------------------------


def test_every_call_is_recorded_with_timing_and_status(
    git_repo: MakeGitRepo, tmp_path: Path, command_log: FakeCommandLog
) -> None:
    """Requirement 12.8: ``--verbose`` prints each external command with its timing."""
    builder = git_repo()
    builder.write("a.py", "a\n")
    builder.stage()
    builder.commit("init")
    repo = open_repo(builder, command_log)
    command_log.calls.clear()

    repo.head()
    repo.staged_changes()
    repo.index_tree_id()
    repo.tracked_files()
    repo.hooks_dir()
    repo.export_index(tmp_path / "idx", None)
    repo.export_commit("HEAD", tmp_path / "cmt", None)

    assert all(seconds >= 0.0 and rc == 0 for _, seconds, rc in command_log.calls)
    subcommands = {argv[3] for argv, _, _ in command_log.calls}
    assert subcommands == {
        "rev-parse",
        "diff",
        "write-tree",
        "ls-files",
        "checkout-index",
        "read-tree",
    }


def test_a_command_that_never_starts_is_recorded_and_reported(
    git_repo: MakeGitRepo, tmp_path: Path, command_log: FakeCommandLog
) -> None:
    """A missing ``git`` must name itself rather than surface as a bare ``FileNotFoundError``."""
    builder = git_repo()
    repo = open_repo(builder, command_log)
    missing = repo.__class__(
        root=repo.root,
        git_dir=repo.git_dir,
        common_dir=repo.common_dir,
        log=command_log,
        git=str(tmp_path / "no-such-git"),
    )
    command_log.calls.clear()

    with pytest.raises(AnalysisFailedError) as raised:
        missing.head()

    assert "no-such-git" in str(raised.value)
    assert [rc for _, _, rc in command_log.calls] == [MISSING_RC]


def test_a_command_that_never_returns_is_recorded_and_reported(
    git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """A hung ``git`` becomes a typed error with the limit in it, and is still logged."""
    builder = git_repo()
    repo = open_repo(builder, command_log)
    impatient = repo.__class__(
        root=repo.root,
        git_dir=repo.git_dir,
        common_dir=repo.common_dir,
        log=command_log,
        timeout_s=0,
    )
    command_log.calls.clear()

    with pytest.raises(AnalysisFailedError) as raised:
        impatient.tracked_files()

    assert "timed out" in str(raised.value)
    assert [rc for _, _, rc in command_log.calls] == [TIMEOUT_RC]
