"""Assembling one run: settings, repository, Understand, adapters (task 8.2; req 1.5, 12.5).

``RunContext`` is the single place a run's inputs come together, so the properties tested
here are the ones every pipeline above it inherits:

* **The repository is optional and its absence is not fatal** (req 12.5): ``doctor`` and
  ``config`` must work outside one, so the context builds anyway and the pipelines that do
  need git ask for it explicitly.
* **The availability report survives the assembly.** Task 2.4 made ``validate_settings``
  answer with the thresholds that survived the metric catalogue, which defaults it dropped,
  and which metrics are unavailable per language. If the context reduced that to a settings
  object, a threshold could go unevaluated with nobody told -- so the report is carried whole
  and its orientation (language -> metrics) is pinned here.
* **The fixture seam replaces the installation, not just the answers**: with
  ``SCITOOLS_HOOK_FAKE_UNDERSTAND`` set, no location is searched and no ``und`` is run.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import signal
from collections.abc import Iterator, Mapping
from datetime import datetime
from pathlib import Path

import pytest
from conftest import FakeCommandLog, MakeGitRepo

from scitools_hook.config import loader as loader_module
from scitools_hook.config.defaults import DEFAULT_THRESHOLDS
from scitools_hook.errors import (
    AnalysisFailedError,
    ConfigError,
    NotAGitRepositoryError,
    UnderstandNotFoundError,
)
from scitools_hook.exit_codes import ExitCode
from scitools_hook.git.repo import GitRepo
from scitools_hook.runner import context as context_module
from scitools_hook.runner.baseline_store import BaselineStore
from scitools_hook.runner.context import (
    ContextOptions,
    RunContext,
    build_context,
    find_repository,
)
from scitools_hook.understand.catalogue import kind_string
from scitools_hook.understand.fake import FAKE_VAR, FixtureApiRunner, FixtureUndCli

PYTHON = "Python"
"""The one configured language of the availability tests; matching is case-insensitive."""

DROPPED = "PercentLackOfCohesion"
"""A real C++/Java class metric Python does not have; a shipped default, so it drops (2.4)."""


@contextlib.contextmanager
def time_limit(seconds: int) -> Iterator[None]:
    """Fail rather than hang: a blocking read must not take the whole suite down with it."""

    def ring(signum: int, frame: object) -> None:
        raise AssertionError(f"the call blocked for more than {seconds}s")

    previous = signal.signal(signal.SIGALRM, ring)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def isolated_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    """An environment that cannot reach this machine's real Understand or user config.

    ``HOME`` is redirected because the locator expands ``~/scitools`` from the environment it
    is given, and this developer machine has one; ``PATH`` is emptied so ``und`` is not found
    there either. Without both, a test asserting "no installation anywhere" would find the
    real one and pass for the wrong reason.
    """
    return {
        "HOME": str(tmp_path / "home"),
        "PATH": "",
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
        **extra,
    }


def options(cwd: Path, env: Mapping[str, str], log: FakeCommandLog) -> ContextOptions:
    """The inputs the CLI will hand ``build_context``."""
    return ContextOptions(cwd=cwd, env=dict(env), log=log)


def seam(tmp_path: Path, **extra: str) -> tuple[Path, dict[str, str]]:
    """A fixture directory and an environment pointing the seam at it."""
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir(parents=True, exist_ok=True)
    return fixtures, isolated_env(tmp_path, **{FAKE_VAR: str(fixtures), **extra})


def write_catalogue(fixtures: Path, without: str) -> None:
    """A ``catalogue`` fixture answering for Python at every scope, minus one metric.

    The catalogue is asked one kind string at a time and answers per kind, so the fixture
    lists every scope the shipped defaults name. Leaving ``without`` out of the class scope
    reproduces the real Python case that task 2.4 exists for.
    """
    metrics = {
        kind_string(PYTHON, scope): sorted(set(table) - {without})
        for scope, table in DEFAULT_THRESHOLDS.items()
    }
    (fixtures / "catalogue.json").write_text(json.dumps({"metrics": metrics}), encoding="utf-8")


def configure(repo_root: Path, body: str) -> None:
    """Write a repository configuration file."""
    (repo_root / "scitools-hook.toml").write_text(body, encoding="utf-8")


# --- the repository is optional (req 12.5) ---------------------------------------


def test_inside_a_repository_the_context_knows_its_root(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """Every path a pipeline resolves hangs off this root, so it is found once, here."""
    repo = git_repo()
    fixtures, env = seam(tmp_path)
    context = build_context(options(repo.path, env, command_log))
    assert context.repo is not None
    assert context.repo.root == repo.path.resolve()
    assert context.require_repo().root == repo.path.resolve()
    assert fixtures.exists()


def test_outside_a_repository_the_context_still_builds(
    tmp_path: Path, command_log: FakeCommandLog
) -> None:
    """Requirement 12.5: ``doctor`` and ``config`` run without a repository."""
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    _, env = seam(tmp_path)
    context = build_context(options(outside, env, command_log))
    assert context.repo is None
    assert context.cache is None


def test_a_pipeline_that_needs_git_asks_and_is_refused_outside_one(
    tmp_path: Path, command_log: FakeCommandLog
) -> None:
    """The refusal is explicit and typed, so the CLI maps it to the documented exit code."""
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    _, env = seam(tmp_path)
    context = build_context(options(outside, env, command_log))
    with pytest.raises(NotAGitRepositoryError):
        context.require_repo()


def test_the_command_log_reaches_the_git_adapter(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """Requirement 12.8's ``--verbose`` trace is worthless if the log is not threaded through."""
    repo = git_repo()
    _, env = seam(tmp_path)
    build_context(options(repo.path, env, command_log))
    assert any("rev-parse" in command for command in command_log.commands)


# --- settings, provenance and where the cache goes -------------------------------


def test_the_repository_configuration_is_loaded_and_attributed(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """Requirement 1.5 asks for the effective configuration *and the file each value came from*."""
    repo = git_repo()
    configure(repo.path, "[thresholds.routine]\nMaxNesting = 2\n")
    _, env = seam(tmp_path)
    context = build_context(options(repo.path, env, command_log))
    nesting = next(
        spec for spec in context.settings.thresholds if spec.rule == "routine.MaxNesting"
    )
    assert nesting.limit.max == 2
    assert context.provenance.values["thresholds.routine.MaxNesting"].startswith("repo:")


def test_the_cache_of_a_repository_can_be_placed_beside_its_git_directory(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """``understand.db_location`` decides, and nothing is ever written into the worktree (2.2)."""
    repo = git_repo()
    configure(repo.path, '[understand]\ndb_location = "gitdir"\n')
    _, env = seam(tmp_path)
    context = build_context(options(repo.path, env, command_log))
    assert context.cache is not None
    assert context.cache.root == repo.path.resolve() / ".git" / "scitools-hook"
    assert context.cache.after_db.name == "after.und"


def test_the_baseline_store_points_at_the_configured_repository_file(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """Requirement 8.1's default is a repository-level file, not one relative to the cwd."""
    repo = git_repo()
    _, env = seam(tmp_path)
    context = build_context(options(repo.path, env, command_log))
    store = context.baseline_store()
    assert isinstance(store, BaselineStore)
    assert store.path == repo.path.resolve() / "scitools-hook.baseline.json"


# --- the fixture seam ------------------------------------------------------------


def test_the_seam_substitutes_both_adapters_without_looking_for_an_installation(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """No ``und`` exists anywhere in this environment, and the context builds regardless."""
    repo = git_repo()
    fixtures, env = seam(tmp_path)
    context = build_context(options(repo.path, env, command_log))
    assert isinstance(context.und, FixtureUndCli)
    assert isinstance(context.api, FixtureApiRunner)
    assert context.understand.home == fixtures
    assert FAKE_VAR in context.understand.source


def test_a_blank_seam_variable_is_off_and_the_installation_is_searched_for(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """An exported-but-empty variable means nothing; it must not stand in for an install."""
    repo = git_repo()
    env = isolated_env(tmp_path, **{FAKE_VAR: "  "})
    with pytest.raises(UnderstandNotFoundError):
        build_context(options(repo.path, env, command_log))


def test_without_understand_the_context_says_every_place_it_looked(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """Requirement 1.3: the failure lists the locations tried and how to name the right one."""
    repo = git_repo()
    env = isolated_env(tmp_path)
    with pytest.raises(UnderstandNotFoundError) as raised:
        build_context(options(repo.path, env, command_log))
    assert raised.value.tried
    assert any("wellknown" in entry for entry in raised.value.tried)
    assert "SCITOOLS_HOME" in (raised.value.hint or "")


# --- the availability report is produced here and carried whole (task 2.4) -------


def test_without_configured_languages_every_threshold_survives(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """No language, no catalogue question: the Gate cannot know what is unavailable."""
    repo = git_repo()
    _, env = seam(tmp_path)
    context = build_context(options(repo.path, env, command_log))
    assert list(context.availability.thresholds) == context.settings.thresholds
    assert context.availability.dropped == ()
    assert context.availability.unavailable == {}


def test_a_default_the_configured_language_cannot_compute_is_dropped_and_reported(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """The whole point of task 2.4: the drop must reach the run, keyed language -> metrics."""
    repo = git_repo()
    configure(repo.path, f'[project]\nlanguages = ["{PYTHON}"]\n')
    fixtures, env = seam(tmp_path)
    write_catalogue(fixtures, without=DROPPED)
    context = build_context(options(repo.path, env, command_log))
    assert [spec.rule for spec in context.availability.dropped] == [f"class.{DROPPED}"]
    assert dict(context.availability.unavailable) == {PYTHON: (DROPPED,)}
    evaluated = {spec.rule for spec in context.availability.thresholds}
    assert f"class.{DROPPED}" not in evaluated
    assert f"class.{DROPPED}" in {spec.rule for spec in context.settings.thresholds}


def test_a_language_the_catalogue_knows_nothing_about_stops_the_run(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """A misspelt language would otherwise drop every rule and report a green run (2.4)."""
    repo = git_repo()
    configure(repo.path, '[project]\nlanguages = ["Pyhton"]\n')
    fixtures, env = seam(tmp_path)
    (fixtures / "catalogue.json").write_text(
        json.dumps({"metrics": {kind_string("Pyhton", scope): [] for scope in DEFAULT_THRESHOLDS}}),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as raised:
        build_context(options(repo.path, env, command_log))
    assert raised.value.key == "project.languages"
    assert raised.value.file == repo.path.resolve() / "scitools-hook.toml"


def test_the_context_is_frozen_so_a_pipeline_cannot_rewrite_the_run(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """One run, one set of inputs: a later stage must not be able to swap the settings."""
    repo = git_repo()
    _, env = seam(tmp_path)
    context = build_context(options(repo.path, env, command_log))
    assert isinstance(context, RunContext)
    with pytest.raises(AttributeError):
        context.settings = context.settings  # type: ignore[misc]


# --- failures that must stop the run, with the right exit code ------------------


def test_a_configuration_file_that_is_not_utf8_stops_the_run_as_a_configuration_error(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """The exit code is the point: this is a config fault, not an internal defect.

    ``UnicodeDecodeError`` is a ``ValueError``, so it escaped both ``config.loader``'s
    ``OSError`` guard and every caller's ``except ConfigError`` -- and reached the CLI as the
    unexpected-error code (70) for a file any Latin-1 editor can save.
    """
    repo = git_repo()
    (repo.path / "scitools-hook.toml").write_bytes(b'[project]\nlanguages = ["\xff\xfe"]\n')
    _, env = seam(tmp_path)
    with pytest.raises(ConfigError) as raised:
        build_context(options(repo.path, env, command_log))
    assert raised.value.exit_code == ExitCode.CONFIG_ERROR
    assert raised.value.file == repo.path.resolve() / "scitools-hook.toml"
    assert "UTF-8" in raised.value.message


def test_a_seam_pointing_at_no_directory_is_refused_rather_than_answered(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """A mistyped fixture path must fail loudly: a missing directory has no ``analyze.json``,
    and a missing ``analyze.json`` is the one absence the seam reads as "parsed cleanly"."""
    repo = git_repo()
    env = isolated_env(tmp_path, **{FAKE_VAR: str(tmp_path / "no-such-fixtures")})
    with pytest.raises(ConfigError) as raised:
        build_context(options(repo.path, env, command_log))
    assert raised.value.exit_code == ExitCode.CONFIG_ERROR
    assert FAKE_VAR in raised.value.message


def test_only_a_missing_repository_becomes_none_never_a_broken_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command_log: FakeCommandLog
) -> None:
    """Widening this guard would let ``check`` run outside the repository it should refuse.

    A ``git`` that cannot be started is an environment fault with its own exit code; turning
    it into "there is no repository here" would send the operator to look in the wrong place,
    and would let a pipeline proceed with ``repo=None`` on a machine that has a repository.
    """

    def unrunnable(*args: object, **kwargs: object) -> GitRepo:
        raise AnalysisFailedError("git could not be started", command=["git"])

    monkeypatch.setattr(GitRepo, "discover", unrunnable)
    with pytest.raises(AnalysisFailedError):
        find_repository(tmp_path, command_log)


def test_the_clock_is_read_exactly_once_per_run(
    tmp_path: Path,
    git_repo: MakeGitRepo,
    command_log: FakeCommandLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One run, one timestamp: ``RunResult.started_at`` and the baseline capture must agree.

    Note 4.5 forbids ``analysis`` reading the clock, so the stamp is taken here and passed
    down. A second read would let a run report two different moments for itself.

    This is the *only* test of that property, deliberately. A companion once asserted
    ``context.started_at == context.started_at``, which is a tautology and could not have
    caught the defect it named even in principle: ``now()`` truncates to whole seconds, so a
    per-access clock agrees with itself across any two adjacent reads. Counting the calls is
    what distinguishes them, so the tautology was deleted rather than repaired.
    """
    reads: list[int] = []

    def counted() -> str:
        reads.append(1)
        return f"2026-08-30T12:00:0{len(reads)}+00:00"

    monkeypatch.setattr(context_module, "now", counted)
    repo = git_repo()
    _, env = seam(tmp_path)
    built = build_context(options(repo.path, env, command_log))
    assert reads == [1]
    assert built.started_at == "2026-08-30T12:00:01+00:00"


# --- what ``ContextOptions.env`` actually controls -------------------------------


def test_the_user_configuration_is_found_through_the_environment_that_was_passed(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """``HOME`` in the mapping must decide, or the run reads the developer's own settings.

    ``Path.home()`` resolves ``~`` from the ambient ``os.environ`` whatever mapping it is
    handed, so the user-level configuration used to come from the real ``~/.config`` even when
    a caller passed a different ``HOME``. A reviewer's probe read a genuine ``MaxNesting = 99``
    out of this developer's file that way. The suite only looked isolated because it also set
    ``XDG_CONFIG_HOME``, which short-circuits that path -- so this test deliberately does not.
    """
    repo = git_repo()
    home = tmp_path / "home"
    user_config = home / ".config" / "scitools-hook" / "config.toml"
    user_config.parent.mkdir(parents=True)
    user_config.write_text("[thresholds.routine]\nMaxNesting = 7\n", encoding="utf-8")
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    env = {"HOME": str(home), "PATH": "", FAKE_VAR: str(fixtures)}
    context = build_context(options(repo.path, env, command_log))
    nesting = next(
        spec for spec in context.settings.thresholds if spec.rule == "routine.MaxNesting"
    )
    assert nesting.limit.max == 7
    assert context.provenance.values["thresholds.routine.MaxNesting"] == f"user:{user_config}"


def test_a_configuration_nested_too_deeply_is_a_configuration_error(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """``RecursionError`` from ``tomllib`` is neither an ``OSError`` nor a ``ValueError``.

    Measured: it starts at about 497 levels of nesting and 450 parses cleanly. Unmapped it
    reached the CLI as the unexpected-error code for what is plainly a bad config file.
    """
    repo = git_repo()
    depth = 600
    (repo.path / "scitools-hook.toml").write_text(f"value = {'[' * depth}{']' * depth}\n")
    _, env = seam(tmp_path)
    with pytest.raises(ConfigError) as raised:
        build_context(options(repo.path, env, command_log))
    assert raised.value.exit_code == ExitCode.CONFIG_ERROR
    assert "too deeply" in raised.value.message


def test_a_configuration_that_is_a_fifo_is_refused_instead_of_read(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """Opening it would block forever; the kind is settled by ``stat``, which does not."""
    repo = git_repo()
    os.mkfifo(repo.path / "scitools-hook.toml")
    _, env = seam(tmp_path)
    with time_limit(10), pytest.raises(ConfigError) as raised:
        build_context(options(repo.path, env, command_log))
    assert raised.value.exit_code == ExitCode.CONFIG_ERROR
    assert "not a regular file" in raised.value.message


def test_the_run_timestamp_is_whole_seconds_in_utc(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """It is written into the baseline and into ``RunResult``, so its shape is a contract.

    Sub-second precision would make two records of the same run differ in text while naming
    the same moment, and a naive stamp would compare wrongly across machines.
    """
    repo = git_repo()
    _, env = seam(tmp_path)
    stamp = build_context(options(repo.path, env, command_log)).started_at
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00", stamp), stamp
    assert datetime.fromisoformat(stamp).tzinfo is not None


def test_the_home_of_the_user_configuration_may_be_named_by_userprofile(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """On Windows ``USERPROFILE`` is the only home variable there is.

    Without it the ambient-``Path.home()`` leak that ``_config_home`` exists to close would
    still be wide open on that platform, with nothing in the suite noticing.
    """
    repo = git_repo()
    home = tmp_path / "profile"
    user_config = home / ".config" / "scitools-hook" / "config.toml"
    user_config.parent.mkdir(parents=True)
    user_config.write_text("[thresholds.routine]\nMaxNesting = 4\n", encoding="utf-8")
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    env = {"USERPROFILE": str(home), "PATH": "", FAKE_VAR: str(fixtures)}
    context = build_context(options(repo.path, env, command_log))
    assert context.provenance.values["thresholds.routine.MaxNesting"] == f"user:{user_config}"


def test_a_blank_home_counts_as_unset_rather_than_as_a_path(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """An exported-but-empty variable is how a shell says nothing.

    Reading it as a path would name the current directory; the convention everywhere else in
    the package (``locator._env_home``, ``fake.fake_directory``) is that blank means unset, so
    the next candidate must win.
    """
    repo = git_repo()
    home = tmp_path / "profile"
    user_config = home / ".config" / "scitools-hook" / "config.toml"
    user_config.parent.mkdir(parents=True)
    user_config.write_text("[thresholds.routine]\nMaxNesting = 5\n", encoding="utf-8")
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    env = {"HOME": "   ", "USERPROFILE": str(home), "PATH": "", FAKE_VAR: str(fixtures)}
    context = build_context(options(repo.path, env, command_log))
    assert context.provenance.values["thresholds.routine.MaxNesting"] == f"user:{user_config}"


def test_a_value_nested_too_deeply_in_an_environment_variable_is_a_configuration_error(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """The third of the enumerated escapes, and the one the docstring claimed was pinned.

    ``_env_value`` parses each ``SCITOOLS_HOOK_*`` value as a TOML fragment, so the same
    ``RecursionError`` reaches the Gate by a route no file-based test touches: both existing
    "too deeply" tests write a file, and deleting this handler survived them.
    """
    repo = git_repo()
    _, env = seam(tmp_path)
    depth = 600
    env["SCITOOLS_HOOK_THRESHOLDS__ROUTINE__CyclomaticStrict"] = "[" * depth + "]" * depth
    with pytest.raises(ConfigError) as raised:
        build_context(options(repo.path, env, command_log))
    assert raised.value.exit_code == ExitCode.CONFIG_ERROR
    assert "too deeply" in raised.value.message
    assert raised.value.key == "thresholds.routine.CyclomaticStrict"


def test_a_configuration_path_holding_a_null_byte_is_a_configuration_error(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """``open`` raises a plain ``ValueError`` for it, which is bad input, not a Gate defect.

    Same shape as the Latin-1 file and the same function; unmapped it was reported as an
    internal error. No OS-level route to it is known -- ``execve`` rejects NUL in argv and in
    the environment -- so it is reached the way an embedding caller would, through ``--config``.
    """
    repo = git_repo()
    _, env = seam(tmp_path)
    overrides: dict[str, object] = {"config": f"{tmp_path}/bad\x00name.toml"}
    with pytest.raises(ConfigError) as raised:
        build_context(
            ContextOptions(cwd=repo.path, env=env, cli_overrides=overrides, log=command_log)
        )
    assert raised.value.exit_code == ExitCode.CONFIG_ERROR


def test_an_environment_value_is_judged_and_returned_as_the_same_text(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """Deciding on the stripped text and returning the unstripped raw is a latent mismatch.

    A shell that exports a value with a trailing space would otherwise put that space into the
    setting while every decision about the value was made without it.
    """
    repo = git_repo()
    _, env = seam(tmp_path)
    env["SCITOOLS_HOOK_STRUCTURE__ARCHITECTURE"] = "  Directory Structure  "
    context = build_context(options(repo.path, env, command_log))
    assert context.settings.structure.architecture == "Directory Structure"


def test_home_is_preferred_over_userprofile_when_both_are_named(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """The precedence is the half of ``USERPROFILE`` support that matters on Windows.

    With both set, swapping the order silently changes which user configuration a run merges,
    and no test that sets only one of them can notice.
    """
    repo = git_repo()
    chosen, ignored = tmp_path / "home", tmp_path / "profile"
    for home, nesting in ((chosen, 3), (ignored, 9)):
        config = home / ".config" / "scitools-hook" / "config.toml"
        config.parent.mkdir(parents=True)
        config.write_text(f"[thresholds.routine]\nMaxNesting = {nesting}\n", encoding="utf-8")
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    env = {
        "HOME": str(chosen),
        "USERPROFILE": str(ignored),
        "PATH": "",
        FAKE_VAR: str(fixtures),
    }
    context = build_context(options(repo.path, env, command_log))
    nesting = next(
        spec for spec in context.settings.thresholds if spec.rule == "routine.MaxNesting"
    )
    assert nesting.limit.max == 3
    expected = chosen / ".config" / "scitools-hook" / "config.toml"
    assert context.provenance.values["thresholds.routine.MaxNesting"] == f"user:{expected}"


def test_a_blank_xdg_config_home_falls_through_to_the_home_directory(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """The blank-is-unset convention has to hold for every variable that claims it.

    A blank value here used to name ``Path("  ")/scitools-hook/config.toml`` -- a relative
    path under a whitespace directory -- rather than falling through to ``HOME``.
    """
    repo = git_repo()
    home = tmp_path / "home"
    config = home / ".config" / "scitools-hook" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text("[thresholds.routine]\nMaxNesting = 6\n", encoding="utf-8")
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    env = {"HOME": str(home), "XDG_CONFIG_HOME": "   ", "PATH": "", FAKE_VAR: str(fixtures)}
    context = build_context(options(repo.path, env, command_log))
    assert context.provenance.values["thresholds.routine.MaxNesting"] == f"user:{config}"


def test_a_home_with_trailing_whitespace_names_that_directory_in_both_consumers(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """``ContextOptions.env`` promises to control ``HOME``; its two readers must agree on it.

    ``config.loader`` decided blankness on the stripped text and then used it as the path,
    while ``locator._expand_user`` used the raw value -- so one variable named two different
    directories. Blankness is now decided stripped and the path is taken raw, which is what
    the locator does.
    """
    repo = git_repo()
    home = tmp_path / "home "
    config = home / ".config" / "scitools-hook" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text("[thresholds.routine]\nMaxNesting = 8\n", encoding="utf-8")
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    env = {"HOME": str(home), "PATH": "", FAKE_VAR: str(fixtures)}
    context = build_context(options(repo.path, env, command_log))
    assert context.provenance.values["thresholds.routine.MaxNesting"] == f"user:{config}"


def test_an_environment_value_failing_in_an_unlisted_way_is_still_a_configuration_error(
    tmp_path: Path,
    git_repo: MakeGitRepo,
    command_log: FakeCommandLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The env-var parser carries the same promise as the file parser, so it is guarded alike.

    No configuration file exists in this run, so ``tomllib.loads`` is reached only through
    ``_env_value``. The failure is injected because the point is the *outcome* -- a
    ``ConfigError`` naming the key -- and not whichever type happens to expose it; the two
    that did were ``RecursionError`` and ``MemoryError``, neither of which is a ``ValueError``.
    """
    repo = git_repo()
    _, env = seam(tmp_path)
    env["SCITOOLS_HOOK_THRESHOLDS__ROUTINE__CyclomaticStrict"] = "12"

    def exhausted(*args: object, **kwargs: object) -> dict[str, object]:
        raise MemoryError("cannot allocate")

    monkeypatch.setattr(loader_module.tomllib, "loads", exhausted)
    with pytest.raises(ConfigError) as raised:
        build_context(options(repo.path, env, command_log))
    assert raised.value.exit_code == ExitCode.CONFIG_ERROR
    assert "MemoryError" in raised.value.message
    assert raised.value.key == "thresholds.routine.CyclomaticStrict"


def test_a_repository_configuration_that_leads_nowhere_is_reported_not_skipped(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """A dangling ``scitools-hook.toml`` answers ``exists()`` False and was silently skipped.

    The run then proceeds on defaults believing the repository has no configuration, which is
    the same silence ``BaselineStore`` was fixed for -- and the two readers disagreed until
    both decided absence with ``os.lstat``.
    """
    repo = git_repo()
    (repo.path / "scitools-hook.toml").symlink_to(repo.path / "nowhere.toml")
    _, env = seam(tmp_path)
    with pytest.raises(ConfigError) as raised:
        build_context(options(repo.path, env, command_log))
    assert raised.value.exit_code == ExitCode.CONFIG_ERROR
    assert "scitools-hook.toml" in raised.value.message


@pytest.mark.parametrize("variable", ["XDG_CACHE_HOME", "HOME"])
def test_the_cache_root_is_taken_from_the_environment_that_was_passed(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog, variable: str
) -> None:
    """The cache root is a requirement 1.5 report field, and it leaked the ambient ``HOME``.

    ``platformdirs.user_cache_dir`` expands ``~`` from the real ``os.environ`` whatever mapping
    it is handed, so a caller supplying its own ``HOME`` still got ``/home/<real user>/.cache``
    -- the same class as the ``Path.home()`` leak, one consumer along, and it also let a test
    assert about a directory outside its own sandbox.
    """
    repo = git_repo()
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    home = tmp_path / "home"
    env = {"HOME": str(home), "PATH": "", FAKE_VAR: str(fixtures)}
    expected = home / ".cache"
    if variable == "XDG_CACHE_HOME":
        expected = tmp_path / "xdg-cache"
        env["XDG_CACHE_HOME"] = str(expected)
    context = build_context(options(repo.path, env, command_log))
    assert context.cache is not None
    assert context.cache.root.parent == expected
    assert str(Path.home()) not in str(context.cache.root)
