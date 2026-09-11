"""The wiring: a check meets the refusal before it analyses anything (requirement 1.2).

``test_feature_refusal`` drives the decision as a function. This drives it where a run does:
through ``build_context``, which finds the record beside the analysis databases and reads it
without probing, because a check must not pay a second and a half for an answer that does not
change between runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import FakeCommandLog, MakeGitRepo
from doctor_stubs import install, isolated_env, options, seed_features
from fixtures.constants import LEAN_TOKEN_RULES

from scitools_hook.errors import ConfigError
from scitools_hook.runner.context import build_context

BUILD = "(Build 1204)"
"""What the stubbed installation answers to ``und version``."""


def test_a_check_on_a_build_without_the_route_stops_before_it_analyses_anything(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """The wiring: the record is found beside the databases and read without probing."""
    repo = git_repo()
    (repo.path / "scitools-hook.toml").write_text(
        '[understand]\nbefore_side = "commit"\n', encoding="utf-8"
    )
    home = install(tmp_path / "scitools")
    env = isolated_env(tmp_path, SCITOOLS_HOME=str(home), XDG_CACHE_HOME=str(tmp_path / "cache"))
    seed_features(repo.path, env, BUILD, commit_before="not on this build")

    with pytest.raises(ConfigError) as caught:
        build_context(options(repo.path, env, command_log))

    assert "understand.before_side" in str(caught.value)


def test_the_same_repository_on_the_automatic_route_builds_its_context(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog
) -> None:
    """``auto`` is the value that works everywhere, and this is what says so."""
    repo = git_repo()
    (repo.path / "scitools-hook.toml").write_text(
        '[understand]\nbefore_side = "auto"\n', encoding="utf-8"
    )
    home = install(tmp_path / "scitools")
    env = isolated_env(tmp_path, SCITOOLS_HOME=str(home), XDG_CACHE_HOME=str(tmp_path / "cache"))
    seed_features(repo.path, env, BUILD, commit_before="not on this build")

    context = build_context(options(repo.path, env, command_log))

    assert context.settings.understand.before_side == "auto"


@pytest.mark.parametrize("rule", LEAN_TOKEN_RULES)
def test_a_token_rule_on_a_build_whose_lexer_probe_failed_stops_the_check(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog, rule: str
) -> None:
    """Requirement 9.3 where a run meets it: the configuration error names the key.

    The record beside the databases says the lexer probe answered ``not on this build``, so
    the rule cannot be evaluated and the run stops rather than going green having measured
    nothing. The other lean feature is left available on purpose: a refusal that fired on
    any missing feature would pass this with the wrong one named.
    """
    repo = git_repo(f"lexer-{rule}")
    (repo.path / "scitools-hook.toml").write_text(f'[lean]\n{rule} = "warning"\n', encoding="utf-8")
    home = install(tmp_path / f"scitools-{rule}")
    env = isolated_env(
        tmp_path, SCITOOLS_HOME=str(home), XDG_CACHE_HOME=str(tmp_path / f"cache-{rule}")
    )
    seed_features(repo.path, env, BUILD, lean_tokens="not on this build")

    with pytest.raises(ConfigError) as caught:
        build_context(options(repo.path, env, command_log))

    assert f"lean.{rule}" in str(caught.value)


@pytest.mark.parametrize("rule", LEAN_TOKEN_RULES)
def test_the_same_repository_on_a_build_whose_lexer_answered_builds_its_context(
    tmp_path: Path, git_repo: MakeGitRepo, command_log: FakeCommandLog, rule: str
) -> None:
    """The other half: the refusal is about the build, not about the key being set."""
    repo = git_repo(f"lexed-{rule}")
    (repo.path / "scitools-hook.toml").write_text(f'[lean]\n{rule} = "warning"\n', encoding="utf-8")
    home = install(tmp_path / f"scitools-ok-{rule}")
    env = isolated_env(
        tmp_path, SCITOOLS_HOME=str(home), XDG_CACHE_HOME=str(tmp_path / f"cache-ok-{rule}")
    )
    seed_features(repo.path, env, BUILD)

    context = build_context(options(repo.path, env, command_log))

    assert getattr(context.settings.lean, rule) == "warning"
