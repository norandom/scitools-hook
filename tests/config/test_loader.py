"""Settings loader: precedence, provenance and located errors (req 3.2, 3.8, 3.10)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from scitools_hook.config.defaults import default_settings
from scitools_hook.config.loader import (
    attach_source,
    load_settings,
    repo_config_path,
    source_of,
    user_config_path,
)
from scitools_hook.config.models import Settings
from scitools_hook.config.template import CONFIG_FILENAME, render_template
from scitools_hook.errors import ConfigError
from scitools_hook.exit_codes import ExitCode


@pytest.fixture
def xdg(tmp_path: Path) -> Path:
    """An isolated XDG_CONFIG_HOME so the real ``~/.config`` is never read."""
    path = tmp_path / "xdg"
    path.mkdir()
    return path


@pytest.fixture
def env(xdg: Path) -> dict[str, str]:
    return {"XDG_CONFIG_HOME": str(xdg)}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    return path


def write_user(xdg: Path, body: str) -> Path:
    path = xdg / "scitools-hook" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def write_repo(repo: Path, body: str) -> Path:
    path = repo / CONFIG_FILENAME
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def routine_limits(settings: Settings) -> dict[str, float | None]:
    return {spec.metric: spec.limit.max for spec in settings.thresholds if spec.scope == "routine"}


# --- discovery -------------------------------------------------------------------


def test_user_config_path_uses_xdg_config_home(xdg: Path) -> None:
    assert user_config_path({"XDG_CONFIG_HOME": str(xdg)}) == (
        xdg / "scitools-hook" / "config.toml"
    )


def test_user_config_path_falls_back_to_dot_config() -> None:
    assert user_config_path({}) == Path.home() / ".config" / "scitools-hook" / "config.toml"


def test_repo_config_path_is_the_documented_file_name(repo: Path) -> None:
    assert repo_config_path(repo) == repo / CONFIG_FILENAME


# --- precedence ------------------------------------------------------------------


def test_defaults_apply_when_no_file_exists(repo: Path, env: dict[str, str]) -> None:
    settings, provenance = load_settings(repo, {}, env)
    assert settings == default_settings()
    assert set(provenance.values.values()) == {"default"}


def test_without_a_repository_the_user_file_still_applies(xdg: Path, env: dict[str, str]) -> None:
    user = write_user(xdg, "[output]\ngraphs_max = 5\n")
    settings, provenance = load_settings(None, {}, env)
    assert settings.output.graphs_max == 5
    assert provenance.values["output.graphs_max"] == f"user:{user}"


def test_repo_file_overrides_user_and_untouched_keys_keep_their_default(
    repo: Path, xdg: Path, env: dict[str, str]
) -> None:
    user = write_user(
        xdg,
        """
        [thresholds.routine]
        CyclomaticStrict = 8
        CountLineCode = 40

        [ratchet]
        strict = true
        """,
    )
    repo_file = write_repo(
        repo,
        """
        [thresholds.routine]
        CyclomaticStrict = 6
        """,
    )
    settings, provenance = load_settings(repo, {}, env)
    limits = routine_limits(settings)
    assert limits["CyclomaticStrict"] == 6
    assert limits["CountLineCode"] == 40
    assert limits["MaxNesting"] == 3
    assert settings.ratchet.strict is True
    assert provenance.values["thresholds.routine.CyclomaticStrict"] == f"repo:{repo_file}"
    assert provenance.values["thresholds.routine.CountLineCode"] == f"user:{user}"
    assert provenance.values["thresholds.routine.MaxNesting"] == "default"
    assert provenance.values["ratchet.strict"] == f"user:{user}"
    assert provenance.values["structure.depth"] == "default"


def test_environment_overrides_the_repository_file(repo: Path, env: dict[str, str]) -> None:
    write_repo(repo, '[ratchet]\nstrict = false\n\n[understand]\nhome = "/repo/scitools"\n')
    env |= {
        "SCITOOLS_HOOK_RATCHET__STRICT": "1",
        "SCITOOLS_HOOK_UNDERSTAND__HOME": "/opt/scitools",
        "SCITOOLS_HOOK_THRESHOLDS__routine__CyclomaticStrict": "7",
        "SCITOOLS_HOOK_PROJECT__EXCLUDE": '["build/**"]',
    }
    settings, provenance = load_settings(repo, {}, env)
    assert settings.ratchet.strict is True
    assert settings.understand.home == Path("/opt/scitools")
    assert settings.project.exclude == ["build/**"]
    assert routine_limits(settings)["CyclomaticStrict"] == 7
    assert provenance.values["understand.home"] == "env:SCITOOLS_HOOK_UNDERSTAND__HOME"
    assert (
        provenance.values["thresholds.routine.CyclomaticStrict"]
        == "env:SCITOOLS_HOOK_THRESHOLDS__routine__CyclomaticStrict"
    )


def test_cli_overrides_beat_the_environment(repo: Path, env: dict[str, str]) -> None:
    write_repo(repo, "[structure]\ndepth = 3\n")
    env["SCITOOLS_HOOK_STRUCTURE__DEPTH"] = "4"
    settings, provenance = load_settings(
        repo, {"structure.depth": 5, "output.show_highest": None}, env
    )
    assert settings.structure.depth == 5
    assert provenance.values["structure.depth"] == "cli"
    assert provenance.values["output.show_highest"] == "default"


def test_explicit_config_replaces_the_repository_file(
    repo: Path, env: dict[str, str], tmp_path: Path
) -> None:
    write_repo(repo, "[structure]\ndepth = 3\n")
    explicit = tmp_path / "elsewhere.toml"
    explicit.write_text("[structure]\ndepth = 7\n", encoding="utf-8")
    settings, provenance = load_settings(repo, {"config": explicit}, env)
    assert settings.structure.depth == 7
    assert provenance.values["structure.depth"] == f"repo:{explicit}"


def test_missing_explicit_config_is_a_config_error(
    repo: Path, env: dict[str, str], tmp_path: Path
) -> None:
    missing = tmp_path / "nope.toml"
    with pytest.raises(ConfigError) as caught:
        load_settings(repo, {"config": missing}, env)
    assert caught.value.file == missing


# --- merge semantics -------------------------------------------------------------


def test_lists_are_replaced_and_tables_are_merged(
    repo: Path, xdg: Path, env: dict[str, str]
) -> None:
    write_user(xdg, '[project]\nexclude = ["a/**", "b/**"]\n')
    write_repo(repo, '[project]\nexclude = ["c/**"]\ninclude = ["src/**"]\n')
    settings, _ = load_settings(repo, {}, env)
    assert settings.project.exclude == ["c/**"]
    assert settings.project.include == ["src/**"]


def test_an_empty_section_keeps_the_defaults(repo: Path, env: dict[str, str]) -> None:
    write_repo(repo, "[structure.fan]\n")
    settings, provenance = load_settings(repo, {}, env)
    assert settings.structure.fan["file_fan_out"].max == 20
    assert provenance.values["structure.fan.file_fan_out"] == "default"


def test_overriding_a_limit_keeps_the_default_severity(repo: Path, env: dict[str, str]) -> None:
    path = write_repo(repo, "[thresholds.file]\nRatioCommentToCode = { min = 0.2 }\n")
    settings, provenance = load_settings(repo, {}, env)
    spec = next(spec for spec in settings.thresholds if spec.rule == "file.RatioCommentToCode")
    assert spec.limit.min == 0.2
    assert spec.severity == "warning"
    assert provenance.values["thresholds.file.RatioCommentToCode"] == f"repo:{path}"


def test_a_bare_number_override_keeps_the_default_severity(repo: Path, env: dict[str, str]) -> None:
    """A scalar override normalises to `max` without escalating the rule's severity."""
    path = write_repo(repo, "[thresholds.file]\nRatioCommentToCode = 0.5\n")
    settings, provenance = load_settings(repo, {}, env)
    spec = next(spec for spec in settings.thresholds if spec.rule == "file.RatioCommentToCode")
    assert spec.limit.max == 0.5
    assert spec.limit.min == 0.1
    assert spec.severity == "warning"
    assert provenance.values["thresholds.file.RatioCommentToCode"] == f"repo:{path}"


def test_a_new_threshold_is_added_to_its_scope(repo: Path, env: dict[str, str]) -> None:
    write_repo(repo, "[thresholds.routine]\nCountLineBlank = { max = 20, ratchet = false }\n")
    settings, provenance = load_settings(repo, {}, env)
    spec = next(spec for spec in settings.thresholds if spec.metric == "CountLineBlank")
    assert spec.limit.max == 20
    assert spec.ratchet is False
    assert "thresholds.routine.CountLineBlank" in provenance.values


# --- template round trip ---------------------------------------------------------


def test_init_template_round_trips_through_the_loader(repo: Path, env: dict[str, str]) -> None:
    path = repo / CONFIG_FILENAME
    path.write_text(render_template(), encoding="utf-8")
    settings, provenance = load_settings(repo, {}, env)
    assert settings == default_settings()
    assert provenance.values["thresholds.routine.CyclomaticStrict"] == f"repo:{path}"


def test_provenance_covers_every_leaf(repo: Path, env: dict[str, str]) -> None:
    settings, provenance = load_settings(repo, {}, env)
    for spec in settings.thresholds:
        assert f"thresholds.{spec.scope}.{spec.metric}" in provenance.values
    expected = [
        "understand.home",
        "understand.db_location",
        "understand.api_mode",
        "project.include",
        "project.exclude",
        "project.languages",
        "ratchet.strict",
        "ignore.files",
        "ignore.classes",
        "ignore.routines",
        "structure.architecture",
        "structure.depth",
        "structure.fan.file_fan_out",
        "structure.layers",
        "structure.coupling",
        "codecheck.config",
        "codecheck.severity",
        "baseline.file",
        "baseline.adaptive",
        "hints",
        "output.graphs_max",
        "output.show_highest",
    ]
    assert [key for key in expected if key not in provenance.values] == []


# --- environment parsing ---------------------------------------------------------


def test_environment_names_without_a_double_underscore_are_ignored(
    repo: Path, env: dict[str, str]
) -> None:
    env |= {
        "SCITOOLS_HOOK_SKIP": "1",
        "SCITOOLS_HOOK_SOFT_FAIL": "1",
        "SCITOOLS_HOOK_FAKE_UNDERSTAND": "/tmp/fixtures",
        "SCITOOLS_HOME": "/opt/scitools",
    }
    settings, _ = load_settings(repo, {}, env)
    assert settings == default_settings()


def test_environment_values_parse_as_toml_scalars_or_plain_strings(
    repo: Path, env: dict[str, str]
) -> None:
    env |= {
        "SCITOOLS_HOOK_OUTPUT__IMPACT_DEPTH": "4",
        "SCITOOLS_HOOK_STRUCTURE__ARCHITECTURE": "My Architecture",
        "SCITOOLS_HOOK_BASELINE__ADAPTIVE": "true",
        "SCITOOLS_HOOK_CODECHECK__CONFIG": '"Strict"',
    }
    settings, _ = load_settings(repo, {}, env)
    assert settings.output.impact_depth == 4
    assert settings.structure.architecture == "My Architecture"
    assert settings.baseline.adaptive is True
    assert settings.codecheck.config == "Strict"


def test_malformed_environment_value_names_the_variable(repo: Path, env: dict[str, str]) -> None:
    env["SCITOOLS_HOOK_PROJECT__EXCLUDE"] = '["build/**"'
    with pytest.raises(ConfigError) as caught:
        load_settings(repo, {}, env)
    assert caught.value.key == "project.exclude"
    assert "SCITOOLS_HOOK_PROJECT__EXCLUDE" in caught.value.message
    assert caught.value.hint is not None


def test_environment_type_error_names_the_variable_as_the_source(
    repo: Path, env: dict[str, str]
) -> None:
    env["SCITOOLS_HOOK_STRUCTURE__DEPTH"] = "deep"
    with pytest.raises(ConfigError) as caught:
        load_settings(repo, {}, env)
    assert caught.value.key == "structure.depth"
    assert caught.value.file is None


# --- invalid configuration -------------------------------------------------------


INVALID: list[tuple[str, str]] = [
    ("[bogus]\nvalue = 1\n", "bogus"),
    ('[understand]\nhomme = "/opt"\n', "understand.homme"),
    ("[thresholds.module]\nCountLineCode = 10\n", "thresholds.module"),
    ('[structure]\ndepth = "deep"\n', "structure.depth"),
    ('[ignore]\nfiles = ["("]\n', "ignore.files"),
    ('[thresholds.routine]\n"AVG:MODE:X" = 3\n', "thresholds.routine.AVG:MODE:X"),
    ('[thresholds.routine]\n"NOPE:X" = 3\n', "thresholds.routine.NOPE:X"),
    (
        "[thresholds.routine]\nCyclomaticStrict = { max = 1, min = 5 }\n",
        "thresholds.routine.CyclomaticStrict",
    ),
    (
        '[thresholds.routine]\nCyclomaticStrict = { max = 5, severity = "fatal" }\n',
        "thresholds.routine.CyclomaticStrict",
    ),
    (
        "[thresholds.routine]\nCyclomaticStrict = { maximum = 5 }\n",
        "thresholds.routine.CyclomaticStrict",
    ),
    ('[thresholds.routine]\nCyclomaticStrict = "ten"\n', "thresholds.routine.CyclomaticStrict"),
    ("[thresholds]\nroutine = 5\n", "thresholds.routine"),
    ("thresholds = 5\n", "thresholds"),
    ("[thresholds.file]\nCountParams = 5\n", "thresholds.file.CountParams"),
    ("[project]\ninclude = [1, 2]\n", "project.include[0]"),
    (
        '[[structure.layers]]\nname = "cli"\nnode = 3\nmay_depend_on = ["runner"]\n',
        "structure.layers[0].node",
    ),
]


@pytest.mark.parametrize(("body", "key"), INVALID, ids=[key for _, key in INVALID])
def test_invalid_repository_configuration_names_file_and_key(
    repo: Path, env: dict[str, str], body: str, key: str
) -> None:
    path = write_repo(repo, body)
    with pytest.raises(ConfigError) as caught:
        load_settings(repo, {}, env)
    assert caught.value.key == key
    assert caught.value.file == path
    assert caught.value.exit_code is ExitCode.CONFIG_ERROR


def test_invalid_user_configuration_names_the_user_file(
    repo: Path, xdg: Path, env: dict[str, str]
) -> None:
    user = write_user(xdg, '[ignore]\nroutines = ["(unclosed"]\n')
    with pytest.raises(ConfigError) as caught:
        load_settings(repo, {}, env)
    assert caught.value.key == "ignore.routines"
    assert caught.value.file == user


def test_unreadable_configuration_file_names_the_file(repo: Path, env: dict[str, str]) -> None:
    path = repo / CONFIG_FILENAME
    path.mkdir()
    with pytest.raises(ConfigError) as caught:
        load_settings(repo, {}, env)
    assert caught.value.file == path


def test_malformed_toml_names_the_file(repo: Path, env: dict[str, str]) -> None:
    path = write_repo(repo, "[structure\ndepth = 2\n")
    with pytest.raises(ConfigError) as caught:
        load_settings(repo, {}, env)
    assert caught.value.file == path
    assert caught.value.exit_code is ExitCode.CONFIG_ERROR


def test_a_semantic_check_that_names_no_key_keeps_its_own_error(
    repo: Path, env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(settings: Settings, availability: object) -> None:
        raise ConfigError("no key here")

    monkeypatch.setattr("scitools_hook.config.loader.validate_settings", refuse)
    with pytest.raises(ConfigError) as caught:
        load_settings(repo, {}, env)
    assert caught.value.key is None
    assert caught.value.file is None


def test_unknown_metric_name_from_the_cli_is_attributed_to_the_cli(
    repo: Path, env: dict[str, str]
) -> None:
    with pytest.raises(ConfigError) as caught:
        load_settings(repo, {"thresholds.routine.Bad:Name:Here": 3}, env)
    assert caught.value.key == "thresholds.routine.Bad:Name:Here"
    assert caught.value.file is None


# --- provenance helpers ----------------------------------------------------------


def test_source_of_falls_back_to_the_nearest_known_parent(repo: Path, env: dict[str, str]) -> None:
    path = write_repo(repo, '[project]\nexclude = ["c/**"]\n')
    _, provenance = load_settings(repo, {}, env)
    assert source_of(provenance, "project.exclude[0]") == f"repo:{path}"
    assert source_of(provenance, "nowhere.at.all") == "default"


def test_attach_source_fills_the_file_from_the_provenance(repo: Path, env: dict[str, str]) -> None:
    path = write_repo(repo, "[structure]\ndepth = 4\n")
    _, provenance = load_settings(repo, {}, env)
    located = attach_source(ConfigError("bad depth", key="structure.depth"), provenance)
    assert located.file == path
    assert located.key == "structure.depth"
    assert located.message == "bad depth"


def test_attach_source_keeps_an_error_that_already_names_a_file(
    repo: Path, env: dict[str, str]
) -> None:
    write_repo(repo, "[structure]\ndepth = 4\n")
    _, provenance = load_settings(repo, {}, env)
    original = ConfigError("boom", file=Path("/elsewhere.toml"), key="structure.depth")
    assert attach_source(original, provenance) is original
