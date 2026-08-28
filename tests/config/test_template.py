"""`init` template: commented TOML that round-trips into the default Settings (req 3.9)."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from scitools_hook.config.defaults import default_settings
from scitools_hook.config.models import CouplingRule, LayerRule, Settings
from scitools_hook.config.template import CONFIG_FILENAME, render_template, write_template
from scitools_hook.errors import ConfigError
from scitools_hook.exit_codes import ExitCode

EXPECTED_SECTIONS = [
    "[understand]",
    "[project]",
    "[thresholds.routine]",
    "[thresholds.class]",
    "[thresholds.file]",
    "[thresholds.project]",
    "[ratchet]",
    "[ignore]",
    "[structure]",
    "[structure.fan]",
    "[codecheck]",
    "[baseline]",
    "[hints]",
    "[output]",
]


def test_config_filename_is_the_documented_repo_level_file() -> None:
    assert CONFIG_FILENAME == "scitools-hook.toml"


def test_template_parses_as_toml_and_validates_into_the_defaults() -> None:
    data = tomllib.loads(render_template())
    assert Settings.model_validate(data) == default_settings()


def test_template_is_deterministic() -> None:
    assert render_template() == render_template()


def test_template_contains_every_section_in_order() -> None:
    text = render_template()
    positions = [text.index(section + "\n") for section in EXPECTED_SECTIONS]
    assert positions == sorted(positions)


def test_template_is_commented() -> None:
    lines = render_template().splitlines()
    comments = [line for line in lines if line.startswith("#")]
    assert len(comments) >= 20
    assert any("scitools-hook init" in line for line in comments)


def test_template_documents_optional_settings_as_commented_examples() -> None:
    text = render_template()
    assert "# [[structure.layers]]" in text
    assert "# [[structure.coupling]]" in text
    assert "# home = " in text
    assert "# languages = " in text
    assert "# config = " in text
    data = tomllib.loads(text)
    assert "layers" not in data["structure"]
    assert "home" not in data["understand"]


def test_template_renders_min_limits_and_non_default_severities_as_tables() -> None:
    data = tomllib.loads(render_template())
    assert data["thresholds"]["file"]["RatioCommentToCode"] == {"min": 0.1, "severity": "warning"}
    assert data["thresholds"]["class"]["PercentLackOfCohesion"] == {
        "max": 70,
        "severity": "warning",
    }
    assert data["thresholds"]["routine"]["CyclomaticStrict"] == 10


def test_template_reflects_custom_settings() -> None:
    custom = default_settings().model_copy(deep=True)
    custom.ratchet.strict = True
    custom.thresholds[0].limit.max = 7
    custom.ignore.files.append(r"^vendor/")
    custom.structure.layers.append(
        LayerRule(name="cli", node="Directory Structure/src/cli", may_depend_on=["runner"])
    )
    custom.structure.coupling.append(CouplingRule(from_node="a", to_node="b", max_refs=9))
    custom.hints["routine.MaxNesting"] = "Use guard clauses."
    custom.understand.home = Path("/opt/scitools")
    custom.codecheck.config = "AllChecks"
    custom.project.languages = ["Python"]
    text = render_template(custom)
    assert Settings.model_validate(tomllib.loads(text)) == custom
    assert "\n[[structure.layers]]\n" in text
    assert "\n[[structure.coupling]]\n" in text


def test_write_template_creates_the_file(tmp_path: Path) -> None:
    target = tmp_path / CONFIG_FILENAME
    assert write_template(target) == target
    assert target.read_text(encoding="utf-8") == render_template()


def test_write_template_refuses_to_overwrite(tmp_path: Path) -> None:
    target = tmp_path / CONFIG_FILENAME
    target.write_text("existing = true\n", encoding="utf-8")
    with pytest.raises(ConfigError) as caught:
        write_template(target)
    assert caught.value.file == target
    assert caught.value.exit_code is ExitCode.CONFIG_ERROR
    assert "force" in (caught.value.hint or "")
    assert target.read_text(encoding="utf-8") == "existing = true\n"


def test_write_template_overwrites_when_forced(tmp_path: Path) -> None:
    target = tmp_path / CONFIG_FILENAME
    target.write_text("existing = true\n", encoding="utf-8")
    assert write_template(target, force=True) == target
    assert target.read_text(encoding="utf-8") == render_template()
