"""The seven commands task 9.3 owns, exercised against fakes and a temporary repository.

``init``, ``config``, ``db path|rebuild|analyze``, ``doctor``, ``install-hook``,
``uninstall-hook`` and ``agent-rules``. Three properties shape most of what is asserted here:

* **Which commands need git is a decision, not an accident** (req 12.5). ``doctor``,
  ``config`` and ``agent-rules`` answer from configuration alone and must run outside a
  repository; ``init``, ``db`` and the two hook commands need a working tree and stop with
  exit 6 without one. Both halves are tested, because a command that silently invented a
  repository root would write files where nobody expects them.
* **A refusal must survive its own force flag.** ``init`` refuses to overwrite, and the
  ``--force`` path must still refuse a destination that is not a regular file -- a FIFO there
  blocks the process forever rather than failing it, so it is refused by kind before anything
  is opened. Those cases run in a **subprocess with an external timeout**: an in-process test
  of a call that may never return cannot report that it never returned.
* **The environment every test runs in is built, never inherited.** ``HOME``,
  ``XDG_CONFIG_HOME``, ``XDG_CACHE_HOME`` and git's own configuration variables are set to
  paths under ``tmp_path`` so a run cannot read the developer's configuration, find their
  Understand installation through ``~/scitools``, or -- worst -- install a *global* hook into
  their real ``~/.config/git/hooks``. ``PATH`` is narrowed to a directory holding one symlink
  to ``git``: narrow enough that no real ``und`` is discovered, and not empty, because a probe
  that strips ``PATH`` disables its own harness (this project has met that four times, so
  :func:`test_the_sanitized_path_still_reaches_git` fails loudly if it happens again).
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import stat
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest
from conftest import GitRepoBuilder, MakeGitRepo
from typer.testing import CliRunner
from typer.testing import Result as CliResult

from scitools_hook.cli import app as app_module
from scitools_hook.config.loader import load_settings, repo_config_path
from scitools_hook.config.template import CONFIG_FILENAME
from scitools_hook.exit_codes import ExitCode
from scitools_hook.git.hooks import CHAINED_SUFFIX, HOOK_NAME, MARKER
from scitools_hook.models.cache import CachePaths
from scitools_hook.report.agent_rules import BEGIN_MARKER, END_MARKER
from scitools_hook.understand.fake import FAKE_VAR, FIXTURE_VERSION

pytestmark = pytest.mark.skipif(
    sys.platform.startswith("win32"), reason="the harness builds POSIX symlinks and FIFOs"
)

GIT_EXECUTABLE = shutil.which("git")
SUBPROCESS_TIMEOUT_S = 60
"""External ceiling for the out-of-process cases; a blocking write must fail, not hang."""

CLEARED = (
    "SCITOOLS_HOME",
    FAKE_VAR,
    "GIT_INDEX_FILE",
    "GIT_DIR",
    "GIT_WORK_TREE",
    "NO_COLOR",
)
"""Ambient variables that would change an answer, removed from every test environment."""


def sanitized_path(tmp_path: Path) -> str:
    """A ``PATH`` holding git and nothing else, so no real Understand is ever discovered."""
    assert GIT_EXECUTABLE is not None, "the test harness needs git on PATH"
    bin_dir = tmp_path / "harness-bin"
    bin_dir.mkdir(exist_ok=True)
    link = bin_dir / "git"
    if not link.exists():
        link.symlink_to(GIT_EXECUTABLE)
    return str(bin_dir)


def env_for(tmp_path: Path, **extra: str) -> dict[str, str | None]:
    """The environment a command runs in: every location decision points under ``tmp_path``."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    built: dict[str, str | None] = {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "PATH": sanitized_path(tmp_path),
    }
    built.update(dict.fromkeys(CLEARED))
    built.update(extra)
    return built


def invoke(argv: list[str], *, cwd: Path, env: Mapping[str, str | None]) -> CliResult:
    """Run one command in ``cwd`` with ``env``, the way the console script would."""
    with contextlib.chdir(cwd):
        return CliRunner().invoke(app_module.app, argv, env=dict(env))


def run_out_of_process(
    argv: list[str], *, cwd: Path, env: Mapping[str, str | None]
) -> subprocess.CompletedProcess[str]:
    """Run the CLI as a real process, under an external timeout that a hang cannot survive."""
    child = {name: value for name, value in {**os.environ, **env}.items() if value is not None}
    return subprocess.run(
        [sys.executable, "-m", "scitools_hook.cli.app", *argv],
        cwd=cwd,
        env=child,
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_S,
        check=False,
    )


def seeded(git_repo: MakeGitRepo, name: str = "repo") -> GitRepoBuilder:
    """A repository with one committed Python file, so there is something to analyse."""
    builder = git_repo(name)
    builder.write("pkg/mod.py", "def one():\n    return 1\n")
    builder.stage()
    builder.commit("first")
    return builder


def cache_paths(tmp_path: Path, builder: GitRepoBuilder) -> CachePaths:
    """Where the tests expect this repository's cache to live under ``tmp_path``."""
    common = Path(builder.run("rev-parse", "--path-format=absolute", "--git-common-dir"))
    return CachePaths.for_repo(common, "cache", tmp_path / "cache")


def fifo(path: Path) -> Path:
    """A named pipe with no reader: opening it for writing never returns."""
    os.mkfifo(path)
    return path


# --- the harness itself ----------------------------------------------------------


def test_the_sanitized_path_still_reaches_git(tmp_path: Path) -> None:
    """A harness that hid git would report 'not a repository' for every repository."""
    found = shutil.which("git", path=sanitized_path(tmp_path))
    assert found is not None
    assert shutil.which("und", path=sanitized_path(tmp_path)) is None


# --- init (req 3.9) --------------------------------------------------------------


def test_init_writes_a_configuration_file_the_loader_accepts(
    tmp_path: Path, git_repo: MakeGitRepo
) -> None:
    builder = git_repo()
    result = invoke(["init"], cwd=builder.path, env=env_for(tmp_path))
    assert result.exit_code == int(ExitCode.OK), result.stderr
    written = repo_config_path(builder.path)
    assert written.is_file()
    assert str(written) in result.stdout
    settings, provenance = load_settings(builder.path, {}, {})
    assert settings.thresholds
    assert any(label.startswith("repo:") for label in provenance.values.values())


def test_init_writes_beside_the_repository_root_not_the_working_directory(
    tmp_path: Path, git_repo: MakeGitRepo
) -> None:
    """Run from a subdirectory, the file still belongs to the repository (req 3.9)."""
    builder = git_repo()
    inner = builder.path / "src" / "deep"
    inner.mkdir(parents=True)
    result = invoke(["init"], cwd=inner, env=env_for(tmp_path))
    assert result.exit_code == int(ExitCode.OK), result.stderr
    assert (builder.path / CONFIG_FILENAME).is_file()
    assert not (inner / CONFIG_FILENAME).exists()


def test_init_refuses_to_overwrite_an_existing_file(tmp_path: Path, git_repo: MakeGitRepo) -> None:
    builder = git_repo()
    existing = repo_config_path(builder.path)
    existing.write_text("# mine\n", encoding="utf-8")
    result = invoke(["init"], cwd=builder.path, env=env_for(tmp_path))
    assert result.exit_code == int(ExitCode.CONFIG_ERROR)
    assert result.stdout == ""
    assert "--force" in result.stderr
    assert existing.read_text(encoding="utf-8") == "# mine\n"


def test_init_force_replaces_an_existing_file(tmp_path: Path, git_repo: MakeGitRepo) -> None:
    builder = git_repo()
    existing = repo_config_path(builder.path)
    existing.write_text("# mine\n", encoding="utf-8")
    result = invoke(["init", "--force"], cwd=builder.path, env=env_for(tmp_path))
    assert result.exit_code == int(ExitCode.OK), result.stderr
    body = existing.read_text(encoding="utf-8")
    assert "# mine" not in body
    assert "[thresholds.routine]" in body


def test_init_force_still_refuses_a_destination_that_is_not_a_regular_file(
    tmp_path: Path, git_repo: MakeGitRepo
) -> None:
    """The force path must not be the one that meets ``IsADirectoryError`` at exit 70."""
    builder = git_repo()
    taken = repo_config_path(builder.path)
    taken.mkdir()
    result = invoke(["init", "--force"], cwd=builder.path, env=env_for(tmp_path))
    assert result.exit_code == int(ExitCode.CONFIG_ERROR)
    assert result.stdout == ""
    assert str(taken) in result.stderr
    assert taken.is_dir()


def test_init_force_does_not_block_on_a_named_pipe(tmp_path: Path, git_repo: MakeGitRepo) -> None:
    """Opening a FIFO for writing never returns; it is refused by kind, out of process."""
    builder = git_repo()
    fifo(repo_config_path(builder.path))
    done = run_out_of_process(["init", "--force"], cwd=builder.path, env=env_for(tmp_path))
    assert done.returncode == int(ExitCode.CONFIG_ERROR), done.stderr
    assert done.stdout == ""
    assert CONFIG_FILENAME in done.stderr


def test_init_outside_a_repository_stops_with_the_git_code(tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    result = invoke(["init"], cwd=outside, env=env_for(tmp_path))
    assert result.exit_code == int(ExitCode.NOT_A_GIT_REPO)
    assert result.stdout == ""
    assert not (outside / CONFIG_FILENAME).exists()


# --- config (req 3.10, 12.5) -----------------------------------------------------


def test_config_runs_outside_a_repository(tmp_path: Path) -> None:
    """Requirement 12.5: ``config`` is one of the two commands that need no working tree."""
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    result = invoke(["config"], cwd=outside, env=env_for(tmp_path))
    assert result.exit_code == int(ExitCode.OK), result.stderr
    assert "thresholds.routine.MaxNesting" in result.stdout
    assert "error:" not in result.stderr


def test_config_prints_every_setting_with_its_source(tmp_path: Path) -> None:
    """Requirement 3.10: the effective value *and* where it came from, for every leaf."""
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    result = invoke(["config"], cwd=outside, env=env_for(tmp_path))
    _settings, provenance = load_settings(None, {}, dict(env_for_values(tmp_path)))
    printed = {
        line.split(" = ", 1)[0]
        for line in result.stdout.splitlines()
        if " = " in line and not line.startswith("#")
    }
    assert printed == set(provenance.values)
    for line in result.stdout.splitlines():
        if " = " in line and not line.startswith("#"):
            assert "  # " in line, line


def test_config_names_the_repository_file_a_value_came_from(
    tmp_path: Path, git_repo: MakeGitRepo
) -> None:
    builder = git_repo()
    repo_config_path(builder.path).write_text(
        "[thresholds.routine]\nMaxNesting = 3\n", encoding="utf-8"
    )
    result = invoke(["config"], cwd=builder.path, env=env_for(tmp_path))
    assert result.exit_code == int(ExitCode.OK), result.stderr
    line = one_line(result.stdout, "thresholds.routine.MaxNesting")
    assert "3" in line
    assert f"repo:{repo_config_path(builder.path)}" in line


def test_config_names_the_environment_variable_a_value_came_from(tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    env = env_for(tmp_path, SCITOOLS_HOOK_RATCHET__STRICT="true")
    result = invoke(["config"], cwd=outside, env=env)
    assert result.exit_code == int(ExitCode.OK), result.stderr
    line = one_line(result.stdout, "ratchet.strict")
    assert "true" in line
    assert "env:SCITOOLS_HOOK_RATCHET__STRICT" in line


def test_config_reports_an_unset_scope_table_rather_than_dropping_it(
    tmp_path: Path, git_repo: MakeGitRepo
) -> None:
    """A scope table with no metrics has a source but no effective value; both are shown."""
    builder = git_repo()
    repo_config_path(builder.path).write_text("[thresholds.arch]\n", encoding="utf-8")
    result = invoke(["config"], cwd=builder.path, env=env_for(tmp_path))
    assert result.exit_code == int(ExitCode.OK), result.stderr
    line = one_line(result.stdout, "thresholds.arch")
    assert f"repo:{repo_config_path(builder.path)}" in line


def test_config_prints_a_setting_whose_own_name_contains_a_dot(
    tmp_path: Path, git_repo: MakeGitRepo
) -> None:
    """A hint is keyed by its rule, so its leaf name is ``routine.MaxNesting`` -- dots and all.

    A lookup that split the provenance key on every dot would descend into a ``hints.routine``
    table that does not exist and report the operator's own hint as unset.
    """
    builder = git_repo()
    repo_config_path(builder.path).write_text(
        '[hints]\n"routine.MaxNesting" = "extract the inner block"\n', encoding="utf-8"
    )
    result = invoke(["config"], cwd=builder.path, env=env_for(tmp_path))
    assert result.exit_code == int(ExitCode.OK), result.stderr
    line = one_line(result.stdout, "hints.routine.MaxNesting")
    assert "extract the inner block" in line
    assert f"repo:{repo_config_path(builder.path)}" in line


def test_config_honours_an_explicit_configuration_file_and_names_it(tmp_path: Path) -> None:
    """``--config`` is a global option; the report must attribute what it brought in."""
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    named = tmp_path / "named.toml"
    named.write_text("[ratchet]\nstrict = true\n", encoding="utf-8")
    result = invoke(["--config", str(named), "config"], cwd=outside, env=env_for(tmp_path))
    assert result.exit_code == int(ExitCode.OK), result.stderr
    line = one_line(result.stdout, "ratchet.strict")
    assert "true" in line
    assert f"repo:{named}" in line


def test_config_refuses_a_broken_configuration_file(tmp_path: Path, git_repo: MakeGitRepo) -> None:
    """An invalid regular expression is requirement 3.8's fault, located by file and key."""
    builder = git_repo()
    repo_config_path(builder.path).write_text('[ignore]\nfiles = ["(unclosed"]\n', encoding="utf-8")
    result = invoke(["config"], cwd=builder.path, env=env_for(tmp_path))
    assert result.exit_code == int(ExitCode.CONFIG_ERROR)
    assert result.stdout == ""
    assert str(repo_config_path(builder.path)) in result.stderr


def env_for_values(tmp_path: Path) -> dict[str, str]:
    """The same environment as :func:`env_for`, with the cleared variables dropped."""
    return {name: value for name, value in env_for(tmp_path).items() if value is not None}


def one_line(text: str, key: str) -> str:
    """The single line whose key is exactly ``key``; fails when there is not exactly one."""
    found = [line for line in text.splitlines() if line.startswith(f"{key} = ")]
    assert len(found) == 1, f"expected one line for {key!r}, got {found}"
    return found[0]


# --- doctor (req 1.5, 12.5) ------------------------------------------------------


def test_doctor_runs_outside_a_repository(tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    result = invoke(["doctor"], cwd=outside, env=env_for(tmp_path))
    assert result.exit_code == int(ExitCode.OK), result.stderr
    assert "not inside a git working tree" in result.stdout
    assert "Configuration" in result.stdout


def test_doctor_reports_a_missing_understand_as_a_problem_rather_than_raising(
    tmp_path: Path, git_repo: MakeGitRepo
) -> None:
    builder = git_repo()
    result = invoke(["doctor"], cwd=builder.path, env=env_for(tmp_path))
    assert result.exit_code == int(ExitCode.OK), result.stderr
    assert "Problems" in result.stdout
    assert "SCITOOLS_HOME" in result.stdout


def test_doctor_reports_the_repository_and_the_cache_it_would_use(
    tmp_path: Path, git_repo: MakeGitRepo
) -> None:
    builder = seeded(git_repo)
    paths = cache_paths(tmp_path, builder)
    result = invoke(["doctor"], cwd=builder.path, env=env_for(tmp_path))
    assert result.exit_code == int(ExitCode.OK), result.stderr
    assert str(builder.path) in result.stdout
    assert str(paths.after_db) in result.stdout
    assert builder.run("rev-parse", "HEAD") in result.stdout


def test_doctor_reports_the_fixture_installation_behind_the_seam(
    tmp_path: Path, git_repo: MakeGitRepo
) -> None:
    builder = git_repo()
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    env = env_for(tmp_path, **{FAKE_VAR: str(fixtures)})
    result = invoke(["doctor"], cwd=builder.path, env=env)
    assert result.exit_code == int(ExitCode.OK), result.stderr
    assert FIXTURE_VERSION in result.stdout
    assert "inprocess" in result.stdout
    assert FAKE_VAR in result.stdout


def test_doctor_does_not_claim_an_api_mode_for_an_unverified_installation(
    tmp_path: Path, git_repo: MakeGitRepo
) -> None:
    """The mode is the one that was *decided*; a guess from the directory layout is not one."""
    builder = git_repo()
    home = broken_installation(tmp_path / "scitools")
    env = env_for(tmp_path, SCITOOLS_HOME=str(home))
    result = invoke(["doctor"], cwd=builder.path, env=env)
    assert result.exit_code == int(ExitCode.OK), result.stderr
    assert str(home) in result.stdout
    assert one_row(result.stdout, "api mode") == "not verified"


def test_doctor_calls_a_commit_synced_after_shadow_ordinary(
    tmp_path: Path, git_repo: MakeGitRepo
) -> None:
    """``explain --range`` leaves this state behind; it costs a re-sync, it is not damage."""
    builder = seeded(git_repo)
    paths = cache_paths(tmp_path, builder)
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.state.write_text(
        json.dumps({"after_target": "commit", "after_tree_id": "abc", "languages": ["Python"]}),
        encoding="utf-8",
    )
    result = invoke(["doctor"], cwd=builder.path, env=env_for(tmp_path))
    assert result.exit_code == int(ExitCode.OK), result.stderr
    assert one_row(result.stdout, "after target").startswith("commit")
    assert "re-sync" in result.stdout
    assert "re-sync" not in problems_block(result.stdout)


def test_doctor_reports_an_unreadable_sync_state_as_a_problem(
    tmp_path: Path, git_repo: MakeGitRepo
) -> None:
    builder = seeded(git_repo)
    paths = cache_paths(tmp_path, builder)
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.state.write_text("{not json", encoding="utf-8")
    result = invoke(["doctor"], cwd=builder.path, env=env_for(tmp_path))
    assert result.exit_code == int(ExitCode.OK), result.stderr
    assert str(paths.state) in problems_block(result.stdout)


def test_doctor_survives_a_configuration_it_cannot_load(
    tmp_path: Path, git_repo: MakeGitRepo
) -> None:
    """The command an operator runs when things are broken must survive a broken config."""
    builder = git_repo()
    repo_config_path(builder.path).write_text('[ignore]\nfiles = ["(unclosed"]\n', encoding="utf-8")
    result = invoke(["doctor"], cwd=builder.path, env=env_for(tmp_path))
    assert result.exit_code == int(ExitCode.OK), result.stderr
    assert str(repo_config_path(builder.path)) in problems_block(result.stdout)
    assert "none was loaded" in result.stdout


def test_doctor_says_in_its_help_that_it_always_succeeds() -> None:
    """Every command's help lists nine exit codes; this one uses exactly one of them."""
    result = CliRunner().invoke(app_module.app, ["doctor", "--help"])
    assert result.exit_code == 0
    assert "Always exits 0" in result.stdout


def test_doctor_prints_the_configuration_with_its_sources(tmp_path: Path) -> None:
    """Requirement 1.5's last clause: the effective configuration and where it came from."""
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    result = invoke(["doctor"], cwd=outside, env=env_for(tmp_path))
    assert "thresholds.routine.MaxNesting = " in result.stdout
    assert "# default" in result.stdout


def broken_installation(root: Path) -> Path:
    """An installation whose ``und`` runs and refuses every command, so nothing verifies."""
    from scitools_hook.understand.locator import platform_bin

    und = root / "bin" / platform_bin(sys.platform) / "und"
    und.parent.mkdir(parents=True, exist_ok=True)
    und.write_text("#!/bin/sh\necho 'Error: broken' >&2\nexit 1\n", encoding="utf-8")
    und.chmod(und.stat().st_mode | stat.S_IXUSR)
    return root


def problems_block(text: str) -> str:
    """The ``Problems`` section alone: the report always has one, so this is never empty."""
    _, marker, rest = text.partition("Problems\n")
    assert marker, f"the report has no Problems section:\n{text}"
    block, _, _ = rest.partition("\nConfiguration\n")
    return block


def one_row(text: str, label: str) -> str:
    """The value of the single ``label:`` row of a report section."""
    found = [line.strip() for line in text.splitlines() if line.strip().startswith(f"{label}:")]
    assert len(found) == 1, f"expected one {label!r} row, got {found}"
    return found[0].split(":", 1)[1].strip()


# --- db (req 2.7, 2.8) -----------------------------------------------------------


def test_db_path_prints_one_path_outside_the_working_tree(
    tmp_path: Path, git_repo: MakeGitRepo
) -> None:
    builder = seeded(git_repo)
    paths = cache_paths(tmp_path, builder)
    result = invoke(["db", "path"], cwd=builder.path, env=env_for(tmp_path))
    assert result.exit_code == int(ExitCode.OK), result.stderr
    assert result.stdout.strip() == str(paths.after_db)
    assert not paths.after_db.is_relative_to(builder.path)


def test_db_path_and_doctor_name_the_same_database(tmp_path: Path, git_repo: MakeGitRepo) -> None:
    """Two commands derive the cache; a repository has one, so they must agree on it."""
    builder = seeded(git_repo)
    printed = invoke(["db", "path"], cwd=builder.path, env=env_for(tmp_path)).stdout.strip()
    report = invoke(["doctor"], cwd=builder.path, env=env_for(tmp_path)).stdout
    assert printed == one_row(report, "after database")


def test_db_path_answers_without_an_understand_installation(
    tmp_path: Path, git_repo: MakeGitRepo
) -> None:
    """The cache layout is decided by configuration; asking where it is needs no Understand."""
    builder = seeded(git_repo)
    result = invoke(["db", "path"], cwd=builder.path, env=env_for(tmp_path))
    assert result.exit_code == int(ExitCode.OK), result.stderr


def test_verbose_keeps_the_command_log_off_standard_output(
    tmp_path: Path, git_repo: MakeGitRepo
) -> None:
    """Requirement 7.7/12.8: the answer owns stdout, the external commands go to stderr."""
    builder = seeded(git_repo)
    result = invoke(["--verbose", "db", "path"], cwd=builder.path, env=env_for(tmp_path))
    assert result.exit_code == int(ExitCode.OK), result.stderr
    assert len(result.stdout.strip().splitlines()) == 1
    assert result.stdout.strip().endswith("after.und")
    assert "$ git" in result.stderr


def test_db_path_outside_a_repository_stops_with_the_git_code(tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    result = invoke(["db", "path"], cwd=outside, env=env_for(tmp_path))
    assert result.exit_code == int(ExitCode.NOT_A_GIT_REPO)
    assert result.stdout == ""


def test_db_analyze_builds_the_after_database(tmp_path: Path, git_repo: MakeGitRepo) -> None:
    builder = seeded(git_repo)
    paths = cache_paths(tmp_path, builder)
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    env = env_for(tmp_path, **{FAKE_VAR: str(fixtures)})
    result = invoke(["db", "analyze"], cwd=builder.path, env=env)
    assert result.exit_code == int(ExitCode.OK), result.stderr
    assert str(paths.after_db) in result.stdout
    assert paths.after_db.exists()
    assert paths.state.is_file()
    assert builder.run("status", "--porcelain") == ""
    # The index, not the working tree: a different target would leave the next
    # `check --staged` re-syncing the after shadow in full instead of reusing it.
    assert json.loads(paths.state.read_text(encoding="utf-8"))["after_target"] == "index"


def test_db_rebuild_removes_the_databases_and_says_which(
    tmp_path: Path, git_repo: MakeGitRepo
) -> None:
    builder = seeded(git_repo)
    paths = cache_paths(tmp_path, builder)
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    env = env_for(tmp_path, **{FAKE_VAR: str(fixtures)})
    assert invoke(["db", "analyze"], cwd=builder.path, env=env).exit_code == 0
    stale = paths.before_db
    stale.mkdir(parents=True, exist_ok=True)
    (stale / "marker").write_text("stale", encoding="utf-8")
    result = invoke(["db", "rebuild"], cwd=builder.path, env=env)
    assert result.exit_code == int(ExitCode.OK), result.stderr
    assert str(paths.before_db) in result.stdout
    assert str(paths.after_db) in result.stdout
    assert not (stale / "marker").exists()
    assert paths.after_db.exists()


def test_db_rebuild_says_what_it_removed_even_when_the_analysis_fails(
    tmp_path: Path, git_repo: MakeGitRepo
) -> None:
    """A destructive step must not lose its record because the step after it failed."""
    builder = git_repo()
    builder.write("README.md", "# nothing Understand can parse\n")
    builder.stage()
    builder.commit("first")
    paths = cache_paths(tmp_path, builder)
    paths.after_db.mkdir(parents=True)
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    env = env_for(tmp_path, **{FAKE_VAR: str(fixtures)})
    result = invoke(["db", "rebuild"], cwd=builder.path, env=env)
    assert result.exit_code == int(ExitCode.ANALYSIS_FAILED), result.stderr
    assert f"removed {paths.after_db}" in result.stdout
    assert not paths.after_db.exists()


def test_db_rebuild_touches_nothing_in_the_working_tree(
    tmp_path: Path, git_repo: MakeGitRepo
) -> None:
    """Requirement 2.2: the destructive command must reach only the cache it derived."""
    builder = seeded(git_repo)
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    env = env_for(tmp_path, **{FAKE_VAR: str(fixtures)})
    before = sorted(path.name for path in builder.path.iterdir())
    result = invoke(["db", "rebuild"], cwd=builder.path, env=env)
    assert result.exit_code == int(ExitCode.OK), result.stderr
    assert sorted(path.name for path in builder.path.iterdir()) == before
    assert (builder.path / "pkg" / "mod.py").is_file()
    assert builder.run("status", "--porcelain") == ""


def test_db_rebuild_reports_a_cache_that_held_nothing(
    tmp_path: Path, git_repo: MakeGitRepo
) -> None:
    builder = seeded(git_repo)
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    env = env_for(tmp_path, **{FAKE_VAR: str(fixtures)})
    result = invoke(["db", "rebuild"], cwd=builder.path, env=env)
    assert result.exit_code == int(ExitCode.OK), result.stderr
    assert "no analysis database" in result.stdout


def test_db_analyze_outside_a_repository_stops_with_the_git_code(tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    result = invoke(["db", "analyze"], cwd=outside, env=env_for(tmp_path))
    assert result.exit_code == int(ExitCode.NOT_A_GIT_REPO)
    assert result.stdout == ""


# --- install-hook and uninstall-hook (req 11.1, 11.2, 11.6, 11.9) ----------------


def hook_path(builder: GitRepoBuilder) -> Path:
    """Where the shim belongs in this repository."""
    return builder.path / ".git" / "hooks" / HOOK_NAME


def test_install_hook_writes_an_executable_shim(tmp_path: Path, git_repo: MakeGitRepo) -> None:
    builder = git_repo()
    result = invoke(["install-hook"], cwd=builder.path, env=env_for(tmp_path))
    assert result.exit_code == int(ExitCode.OK), result.stderr
    shim = hook_path(builder)
    assert shim.is_file()
    assert MARKER in shim.read_text(encoding="utf-8")
    assert shim.stat().st_mode & stat.S_IXUSR
    assert str(shim) in result.stdout


def test_install_hook_refuses_a_second_install_without_force(
    tmp_path: Path, git_repo: MakeGitRepo
) -> None:
    builder = git_repo()
    assert invoke(["install-hook"], cwd=builder.path, env=env_for(tmp_path)).exit_code == 0
    result = invoke(["install-hook"], cwd=builder.path, env=env_for(tmp_path))
    assert result.exit_code == int(ExitCode.CONFIG_ERROR)
    assert result.stdout == ""
    assert "--force" in result.stderr


def test_install_hook_refuses_a_foreign_hook_and_leaves_it_alone(
    tmp_path: Path, git_repo: MakeGitRepo
) -> None:
    builder = git_repo()
    shim = hook_path(builder)
    shim.parent.mkdir(parents=True, exist_ok=True)
    shim.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    result = invoke(["install-hook"], cwd=builder.path, env=env_for(tmp_path))
    assert result.exit_code == int(ExitCode.CONFIG_ERROR)
    assert result.stdout == ""
    assert "--force" in result.stderr
    assert shim.read_text(encoding="utf-8") == "#!/bin/sh\nexit 0\n"


def test_install_hook_force_chains_the_hook_it_replaced(
    tmp_path: Path, git_repo: MakeGitRepo
) -> None:
    builder = git_repo()
    shim = hook_path(builder)
    shim.parent.mkdir(parents=True, exist_ok=True)
    shim.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
    result = invoke(["install-hook", "--force"], cwd=builder.path, env=env_for(tmp_path))
    assert result.exit_code == int(ExitCode.OK), result.stderr
    chained = shim.with_name(shim.name + CHAINED_SUFFIX)
    assert chained.read_text(encoding="utf-8") == "#!/bin/sh\nexit 3\n"
    assert MARKER in shim.read_text(encoding="utf-8")
    assert str(chained) in result.stdout


def test_uninstall_hook_restores_the_chained_hook(tmp_path: Path, git_repo: MakeGitRepo) -> None:
    builder = git_repo()
    shim = hook_path(builder)
    shim.parent.mkdir(parents=True, exist_ok=True)
    shim.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
    assert (
        invoke(["install-hook", "--force"], cwd=builder.path, env=env_for(tmp_path)).exit_code == 0
    )
    result = invoke(["uninstall-hook"], cwd=builder.path, env=env_for(tmp_path))
    assert result.exit_code == int(ExitCode.OK), result.stderr
    assert shim.read_text(encoding="utf-8") == "#!/bin/sh\nexit 3\n"
    assert not shim.with_name(shim.name + CHAINED_SUFFIX).exists()
    assert str(shim) in result.stdout


def test_uninstall_hook_on_a_clean_repository_is_a_success(
    tmp_path: Path, git_repo: MakeGitRepo
) -> None:
    """Nothing installed is a success; someone else's hook being here is not."""
    builder = git_repo()
    result = invoke(["uninstall-hook"], cwd=builder.path, env=env_for(tmp_path))
    assert result.exit_code == int(ExitCode.OK), result.stderr
    assert "no pre-commit hook" in result.stdout


def test_uninstall_hook_refuses_a_hook_the_gate_did_not_write(
    tmp_path: Path, git_repo: MakeGitRepo
) -> None:
    builder = git_repo()
    shim = hook_path(builder)
    shim.parent.mkdir(parents=True, exist_ok=True)
    shim.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    result = invoke(["uninstall-hook"], cwd=builder.path, env=env_for(tmp_path))
    assert result.exit_code == int(ExitCode.CONFIG_ERROR)
    assert result.stdout == ""
    assert shim.read_text(encoding="utf-8") == "#!/bin/sh\nexit 0\n"


def test_install_hook_global_uses_the_users_hooks_path_and_reports_it(
    tmp_path: Path, git_repo: MakeGitRepo
) -> None:
    builder = git_repo()
    result = invoke(["install-hook", "--global"], cwd=builder.path, env=env_for(tmp_path))
    assert result.exit_code == int(ExitCode.OK), result.stderr
    installed = tmp_path / "xdg" / "git" / "hooks" / HOOK_NAME
    assert installed.is_file()
    assert str(installed) in result.stdout
    assert not hook_path(builder).exists()


def test_uninstall_hook_global_removes_the_global_shim(
    tmp_path: Path, git_repo: MakeGitRepo
) -> None:
    """Without ``--global`` here, a global install could not be undone from a repository."""
    builder = git_repo()
    assert (
        invoke(["install-hook", "--global"], cwd=builder.path, env=env_for(tmp_path)).exit_code == 0
    )
    result = invoke(["uninstall-hook", "--global"], cwd=builder.path, env=env_for(tmp_path))
    assert result.exit_code == int(ExitCode.OK), result.stderr
    assert not (tmp_path / "xdg" / "git" / "hooks" / HOOK_NAME).exists()


def test_install_hook_refuses_a_hooks_directory_inside_the_working_tree(
    tmp_path: Path, git_repo: MakeGitRepo
) -> None:
    """The committed-``.githooks`` layout is served by .pre-commit-hooks.yaml, not by this."""
    builder = git_repo()
    builder.run("config", "core.hooksPath", ".githooks")
    result = invoke(["install-hook"], cwd=builder.path, env=env_for(tmp_path))
    assert result.exit_code == int(ExitCode.CONFIG_ERROR)
    assert result.stdout == ""
    assert ".pre-commit-hooks.yaml" in result.stderr
    assert not (builder.path / ".githooks").exists()


def test_install_hook_outside_a_repository_stops_with_the_git_code(tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    result = invoke(["install-hook"], cwd=outside, env=env_for(tmp_path))
    assert result.exit_code == int(ExitCode.NOT_A_GIT_REPO)
    assert result.stdout == ""


def test_uninstall_hook_outside_a_repository_stops_with_the_git_code(tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    result = invoke(["uninstall-hook"], cwd=outside, env=env_for(tmp_path))
    assert result.exit_code == int(ExitCode.NOT_A_GIT_REPO)
    assert result.stdout == ""


# --- agent-rules (req 10.1, 10.3) ------------------------------------------------


def test_agent_rules_prints_the_snippet(tmp_path: Path, git_repo: MakeGitRepo) -> None:
    builder = git_repo()
    result = invoke(["agent-rules"], cwd=builder.path, env=env_for(tmp_path))
    assert result.exit_code == int(ExitCode.OK), result.stderr
    assert "## Maintainability rules (scitools-hook)" in result.stdout
    assert "MaxNesting" in result.stdout


def test_agent_rules_runs_outside_a_repository(tmp_path: Path) -> None:
    """The rules come from configuration; there is nothing here that needs a working tree."""
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    result = invoke(["agent-rules"], cwd=outside, env=env_for(tmp_path))
    assert result.exit_code == int(ExitCode.OK), result.stderr
    assert "## Maintainability rules (scitools-hook)" in result.stdout


def test_agent_rules_is_deterministic(tmp_path: Path, git_repo: MakeGitRepo) -> None:
    """Requirement 10.2: the block is committed, so two runs must not produce a diff."""
    builder = git_repo()
    first = invoke(["agent-rules"], cwd=builder.path, env=env_for(tmp_path))
    second = invoke(["agent-rules"], cwd=builder.path, env=env_for(tmp_path))
    assert first.stdout == second.stdout


def test_agent_rules_shows_the_limit_the_baseline_narrowed(
    tmp_path: Path, git_repo: MakeGitRepo
) -> None:
    """Requirement 8.5: an agent told the configured limit would chase the wrong number."""
    builder = git_repo()
    (builder.path / "scitools-hook.baseline.json").write_text(
        json.dumps(
            {
                "version": 1,
                "captured_at": "2026-01-01T00:00:00Z",
                "values": {"routine.MaxNesting": 2},
            }
        ),
        encoding="utf-8",
    )
    result = invoke(["agent-rules"], cwd=builder.path, env=env_for(tmp_path))
    assert result.exit_code == int(ExitCode.OK), result.stderr
    line = next(line for line in result.stdout.splitlines() if "MaxNesting" in line)
    assert "2" in line
    assert "baseline" in line


def test_agent_rules_write_inserts_between_markers_and_keeps_the_rest(
    tmp_path: Path, git_repo: MakeGitRepo
) -> None:
    builder = git_repo()
    target = builder.path / "AGENTS.md"
    target.write_text("# House rules\n\nBe careful.\n", encoding="utf-8")
    result = invoke(
        ["agent-rules", "--write", str(target)], cwd=builder.path, env=env_for(tmp_path)
    )
    assert result.exit_code == int(ExitCode.OK), result.stderr
    body = target.read_text(encoding="utf-8")
    assert body.startswith("# House rules\n\nBe careful.\n")
    assert body.count(BEGIN_MARKER) == 1
    assert body.count(END_MARKER) == 1
    assert result.stdout == ""
    assert str(target) in result.stderr


def test_agent_rules_write_twice_leaves_one_block(tmp_path: Path, git_repo: MakeGitRepo) -> None:
    builder = git_repo()
    target = builder.path / "AGENTS.md"
    target.write_text("# House rules\n", encoding="utf-8")
    argv = ["agent-rules", "--write", str(target)]
    assert invoke(argv, cwd=builder.path, env=env_for(tmp_path)).exit_code == 0
    first = target.read_text(encoding="utf-8")
    assert invoke(argv, cwd=builder.path, env=env_for(tmp_path)).exit_code == 0
    assert target.read_text(encoding="utf-8") == first
    assert first.count(BEGIN_MARKER) == 1


def test_agent_rules_write_creates_a_file_that_does_not_exist(
    tmp_path: Path, git_repo: MakeGitRepo
) -> None:
    builder = git_repo()
    target = builder.path / "docs" / "AGENTS.md"
    target.parent.mkdir()
    result = invoke(
        ["agent-rules", "--write", str(target)], cwd=builder.path, env=env_for(tmp_path)
    )
    assert result.exit_code == int(ExitCode.OK), result.stderr
    assert BEGIN_MARKER in target.read_text(encoding="utf-8")


def test_agent_rules_write_refuses_an_unusable_marker_block_naming_the_file(
    tmp_path: Path, git_repo: MakeGitRepo
) -> None:
    builder = git_repo()
    target = builder.path / "AGENTS.md"
    target.write_text(f"{BEGIN_MARKER}\nstale\n", encoding="utf-8")
    result = invoke(
        ["agent-rules", "--write", str(target)], cwd=builder.path, env=env_for(tmp_path)
    )
    assert result.exit_code == int(ExitCode.CONFIG_ERROR)
    assert result.stdout == ""
    assert str(target) in result.stderr
    assert target.read_text(encoding="utf-8") == f"{BEGIN_MARKER}\nstale\n"


def test_agent_rules_write_does_not_block_on_a_named_pipe(
    tmp_path: Path, git_repo: MakeGitRepo
) -> None:
    """Reading the target comes before writing it, and a FIFO blocks on both."""
    builder = git_repo()
    target = fifo(builder.path / "AGENTS.md")
    done = run_out_of_process(
        ["agent-rules", "--write", str(target)], cwd=builder.path, env=env_for(tmp_path)
    )
    assert done.returncode == int(ExitCode.REPORT_UNDELIVERABLE), done.stderr
    assert done.stdout == ""
    assert str(target) in done.stderr


def test_agent_rules_write_refuses_a_target_that_is_not_utf_8_text(
    tmp_path: Path, git_repo: MakeGitRepo
) -> None:
    """Inserting into bytes the Gate cannot decode would have to guess, so it refuses."""
    builder = git_repo()
    target = builder.path / "AGENTS.md"
    target.write_bytes(b"# rules\n\xff\xfe not utf-8\n")
    result = invoke(
        ["agent-rules", "--write", str(target)], cwd=builder.path, env=env_for(tmp_path)
    )
    assert result.exit_code == int(ExitCode.REPORT_UNDELIVERABLE), result.stderr
    assert result.stdout == ""
    assert target.read_bytes() == b"# rules\n\xff\xfe not utf-8\n"


def test_agent_rules_write_refuses_a_directory(tmp_path: Path, git_repo: MakeGitRepo) -> None:
    builder = git_repo()
    target = builder.path / "AGENTS.md"
    target.mkdir()
    result = invoke(
        ["agent-rules", "--write", str(target)], cwd=builder.path, env=env_for(tmp_path)
    )
    assert result.exit_code == int(ExitCode.REPORT_UNDELIVERABLE), result.stderr
    assert result.stdout == ""
    assert target.is_dir()


# --- help (req 12.1) -------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "options"),
    [
        (["init"], ("--force",)),
        (["install-hook"], ("--force", "--global")),
        (["uninstall-hook"], ("--global",)),
        (["agent-rules"], ("--write",)),
    ],
)
def test_each_command_documents_its_own_options(argv: list[str], options: tuple[str, ...]) -> None:
    result = CliRunner().invoke(app_module.app, [*argv, "--help"])
    assert result.exit_code == 0
    for option in options:
        assert option in result.stdout, option
