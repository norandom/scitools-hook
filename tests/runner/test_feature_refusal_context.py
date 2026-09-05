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
