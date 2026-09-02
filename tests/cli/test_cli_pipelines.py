"""The assembly the three commands stand on (task 9.2).

The command tests replace this module wholesale, which is what keeps them fast and
license-free -- so the thing they replace needs its own tests. What is asserted here is the
part no command test can see: that the settings overrides a command line produces really do
reach ``RunContext.settings``, that the cache and the database manager are built from the
repository that was found, and that requirement 12.5's answer is decided before Understand is
looked for rather than after.

The documented ``SCITOOLS_HOOK_FAKE_UNDERSTAND`` seam supplies the adapters, so these run on
any machine.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import GitRepoBuilder, MakeGitRepo

from scitools_hook.cli import pipelines
from scitools_hook.cli.common import ApiMode, GlobalOptions
from scitools_hook.errors import NotAGitRepositoryError, UnderstandNotFoundError
from scitools_hook.runner.baseline_cmd import BaselineCmd
from scitools_hook.runner.check import CheckPipeline
from scitools_hook.runner.explain import ExplainPipeline
from scitools_hook.understand.fake import FAKE_VAR


def fixtures(tmp_path: Path) -> Path:
    """An empty fixture directory: enough for the seam, which reads files on demand."""
    directory = tmp_path / "fixtures"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def options_for(cwd: Path, tmp_path: Path, *, fake: bool = True) -> GlobalOptions:
    """A command line pointed at ``cwd``, with every location decision under ``tmp_path``."""
    env = {
        "HOME": str(tmp_path / "home"),
        "PATH": "",
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
    }
    if fake:
        env[FAKE_VAR] = str(fixtures(tmp_path))
    return GlobalOptions(cwd=cwd, env=env)


@pytest.fixture
def repo(git_repo: MakeGitRepo) -> GitRepoBuilder:
    """A repository with one committed file, so ``HEAD`` exists."""
    builder = git_repo()
    builder.write("src/app.py", "def f():\n    return 1\n")
    builder.stage("src/app.py")
    builder.commit("initial")
    return builder


# --- what one assembly holds -----------------------------------------------------


def test_the_assembly_is_built_from_the_repository_the_command_ran_in(
    repo: GitRepoBuilder, tmp_path: Path
) -> None:
    assembled = pipelines.assemble(options_for(repo.path, tmp_path))
    assert assembled.ctx.repo is not None
    assert assembled.ctx.repo.root == repo.path


def test_the_cache_lives_outside_the_working_tree(repo: GitRepoBuilder, tmp_path: Path) -> None:
    """Requirement 2.2: nothing this tool owns may land in the repository."""
    assembled = pipelines.assemble(options_for(repo.path, tmp_path))
    paths = assembled.dbm.paths()
    assert not paths.after_db.is_relative_to(repo.path)
    assert paths.after_db.is_relative_to(tmp_path / "cache")


@pytest.mark.parametrize(
    ("build", "expected"),
    (
        pytest.param(lambda a: a.check(), CheckPipeline, id="check"),
        pytest.param(lambda a: a.explain(), ExplainPipeline, id="explain"),
        pytest.param(lambda a: a.baseline(), BaselineCmd, id="baseline"),
    ),
)
def test_each_command_gets_the_pipeline_the_design_names(
    repo: GitRepoBuilder, tmp_path: Path, build: object, expected: type
) -> None:
    assembled = pipelines.assemble(options_for(repo.path, tmp_path))
    assert isinstance(build(assembled), expected)  # type: ignore[operator]


# --- the settings overrides really reach the settings (req 3.2) -------------------


OVERRIDES = (
    pytest.param({"ratchet.strict": True}, "ratchet", "strict", True, id="strict"),
    pytest.param({"baseline.adaptive": True}, "baseline", "adaptive", True, id="adaptive-on"),
    pytest.param({"baseline.adaptive": False}, "baseline", "adaptive", False, id="adaptive-off"),
    pytest.param({"output.show_highest": True}, "output", "show_highest", True, id="show-highest"),
)


@pytest.mark.parametrize(("overrides", "section", "field", "expected"), OVERRIDES)
def test_a_command_line_override_reaches_the_effective_settings(
    repo: GitRepoBuilder,
    tmp_path: Path,
    overrides: dict[str, object],
    section: str,
    field: str,
    expected: bool,
) -> None:
    """The pipelines read these off ``ctx.settings``, so this is the whole of the wiring."""
    assembled = pipelines.assemble(options_for(repo.path, tmp_path), overrides)
    assert getattr(getattr(assembled.ctx.settings, section), field) is expected


def test_without_an_override_the_configured_value_stands(
    repo: GitRepoBuilder, tmp_path: Path
) -> None:
    """An absent flag must leave the configuration file's answer alone (req 3.2)."""
    (repo.path / "scitools-hook.toml").write_text("[ratchet]\nstrict = true\n", encoding="utf-8")
    assembled = pipelines.assemble(options_for(repo.path, tmp_path))
    assert assembled.ctx.settings.ratchet.strict is True


def test_an_override_outranks_the_configuration_file(repo: GitRepoBuilder, tmp_path: Path) -> None:
    (repo.path / "scitools-hook.toml").write_text("[baseline]\nadaptive = true\n", encoding="utf-8")
    assembled = pipelines.assemble(options_for(repo.path, tmp_path), {"baseline.adaptive": False})
    assert assembled.ctx.settings.baseline.adaptive is False


def test_the_global_options_own_overrides_survive_a_commands_own(
    repo: GitRepoBuilder, tmp_path: Path
) -> None:
    """``--api-mode`` travels the same channel; a command's keys must not replace it."""
    options = options_for(repo.path, tmp_path)
    options = GlobalOptions(cwd=options.cwd, env=options.env, api_mode=ApiMode.UPYTHON)
    assembled = pipelines.assemble(options, {"ratchet.strict": True})
    assert assembled.ctx.settings.understand.api_mode == "upython"
    assert assembled.ctx.settings.ratchet.strict is True


# --- requirement 12.5 is answered before Understand is looked for -----------------


def test_outside_a_repository_the_answer_is_the_git_one_even_with_no_understand(
    tmp_path: Path,
) -> None:
    """With the seam off and nothing on ``PATH``, a locator-first order would raise 3."""
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    with pytest.raises(NotAGitRepositoryError):
        pipelines.assemble(options_for(outside, tmp_path, fake=False))


def test_inside_a_repository_a_missing_understand_is_still_reported(
    repo: GitRepoBuilder, tmp_path: Path
) -> None:
    """The other half: the early git check must not swallow the installation failure."""
    with pytest.raises(UnderstandNotFoundError):
        pipelines.assemble(options_for(repo.path, tmp_path, fake=False))
