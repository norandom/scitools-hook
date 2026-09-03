"""`init` template: commented TOML that round-trips into the default Settings (req 3.9)."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from scitools_hook.config.defaults import default_settings
from scitools_hook.config.detect import PARSE_REASONS, Detection, detect
from scitools_hook.config.models import (
    CouplingRule,
    LayerRule,
    ParseAcknowledgement,
    PathScope,
    Settings,
)
from scitools_hook.config.template import (
    CONFIG_FILENAME,
    propose,
    render_template,
    write_template,
)
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


# --- scopes and acknowledged parse limitations -------------------------------------


def test_the_default_template_documents_a_scope_and_an_acknowledgement() -> None:
    text = render_template()
    assert "# [scope.tests]" in text
    assert "# [[parse.acknowledged]]" in text
    data = tomllib.loads(text)
    assert "scope" not in data and "parse" not in data, "both are examples, not settings"


def test_a_configured_scope_round_trips_through_the_renderer() -> None:
    custom = default_settings().model_copy(deep=True)
    custom.scope["tests"] = PathScope.model_validate(
        {
            "paths": ["tests/**"],
            "thresholds": {
                "routine": {"CyclomaticStrict": 15, "CountLineCode": {"max": 120}},
                "file": {"CountDeclFunction": False},
            },
        }
    )
    text = render_template(custom)
    assert Settings.model_validate(tomllib.loads(text)) == custom
    assert "\n[scope.tests]\n" in text
    assert "\n[scope.tests.thresholds.routine]\n" in text
    assert "CountDeclFunction = false" in text


def test_a_configured_acknowledgement_round_trips_through_the_renderer() -> None:
    custom = default_settings().model_copy(deep=True)
    custom.parse.acknowledged.append(
        ParseAcknowledgement(paths=["src/a.py"], reason="Understand 6.5 stops at line 10.")
    )
    text = render_template(custom)
    assert Settings.model_validate(tomllib.loads(text)) == custom
    assert "\n[[parse.acknowledged]]\n" in text


def test_a_scope_name_that_is_not_a_bare_key_is_quoted() -> None:
    custom = default_settings().model_copy(deep=True)
    custom.scope["packages/client"] = PathScope(paths=["packages/client/**"])
    text = render_template(custom)
    assert '[scope."packages/client"]' in text
    assert Settings.model_validate(tomllib.loads(text)) == custom


# --- what a detection proposes -----------------------------------------------------


def detection_of(root: Path, files: dict[str, str]) -> Detection:
    for rel, text in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return detect(root, sorted(files), default_settings().project)


def test_a_proposal_renders_a_valid_document(tmp_path: Path) -> None:
    found = detection_of(
        tmp_path,
        {
            ".gitattributes": "vendor/** linguist-vendored\n",
            "vendor/lib.py": "x = 1\n",
            "pyproject.toml": '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
            "tests/test_a.py": "def gen[T](v: T) -> T:\n    return v\n",
        },
    )
    text = render_template(proposal=propose(found))
    settings = Settings.model_validate(tomllib.loads(text))
    assert "vendor/**" in settings.project.exclude
    assert settings.scope["tests"].paths == ["tests/**"]
    assert settings.parse.acknowledged == [], "an acknowledgement is only ever a suggestion"


def test_every_proposed_line_names_the_evidence_that_produced_it(tmp_path: Path) -> None:
    """The contract: an operator can check each generated line against the file it cites."""
    found = detection_of(
        tmp_path,
        {
            ".gitattributes": "vendor/** linguist-vendored\n",
            "vendor/lib.py": "x = 1\n",
            "alembic.ini": "[alembic]\nscript_location = migrations\n",
            "migrations/versions/0001.py": "x = 1\n",
            "pyproject.toml": '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
            "tests/test_a.py": "x = 1\n",
        },
    )
    text = render_template(proposal=propose(found))
    assert "# evidence: gitattributes in .gitattributes: vendor/** linguist-vendored" in text
    assert "[alembic] script_location = migrations" in text
    assert "[tool.pytest.ini_options] testpaths = ['tests']" in text


def test_a_declaration_covering_no_tracked_file_produces_no_line(tmp_path: Path) -> None:
    """The measured shape: a repository that gitignores the tree it declares generated."""
    found = detection_of(tmp_path, {".gitattributes": "sdk/** linguist-generated\n"})
    proposal = propose(found)
    assert "sdk/**" not in proposal.settings.project.exclude


def test_a_test_tree_gets_a_scope_and_never_an_exclusion(tmp_path: Path) -> None:
    """Task 10.4 refused a blanket ``tests/**`` ignore; a scope is what it asked for instead."""
    found = detection_of(
        tmp_path,
        {
            "pyproject.toml": '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
            "tests/test_a.py": "x = 1\n",
        },
    )
    proposal = propose(found)
    assert "tests/**" not in proposal.settings.project.exclude
    assert proposal.settings.scope["tests"].paths == ["tests/**"]


def test_a_subproject_is_only_ever_suggested(tmp_path: Path) -> None:
    found = detection_of(
        tmp_path,
        {
            "packages/client/pyproject.toml": '[project]\nname = "c"\n',
            "packages/client/a.py": "x=1\n",
        },
    )
    proposal = propose(found)
    text = render_template(proposal=proposal)
    assert proposal.settings.scope == {}
    assert '# [scope."packages/client"]' in text
    assert "scope" not in tomllib.loads(text), "suggested, and not in force"


def test_an_unreadable_file_is_only_ever_suggested(tmp_path: Path) -> None:
    """The acknowledgement is written commented: it stops a file blocking, so it is not ours."""
    found = detection_of(tmp_path, {"src/a.py": "def gen[T](v: T) -> T:\n    return v\n"})
    text = render_template(proposal=propose(found))
    assert "# [[parse.acknowledged]]" in text
    assert "src/a.py  (line 1: def gen[T])" in text
    assert Settings.model_validate(tomllib.loads(text)).parse.acknowledged == []


def test_the_two_parse_reasons_are_written_as_separate_blocks(tmp_path: Path) -> None:
    """One reason for both would be false for one of them; the cost differs, measured."""
    found = detection_of(
        tmp_path,
        {
            "src/a.py": "def gen[T](v: T) -> T:\n    return v\n",
            "src/b.py": "type A = int\n",
        },
    )
    text = render_template(proposal=propose(found))
    assert text.count("# [[parse.acknowledged]]") == 2
    assert PARSE_REASONS["pep695"] in text and PARSE_REASONS["pep695-alias"] in text


def test_only_one_commented_acknowledgement_block_kind_is_offered(tmp_path: Path) -> None:
    """Without a suggestion the generic example stands; with one it is left out."""
    plain = render_template()
    found = detection_of(tmp_path, {"src/a.py": "def gen[T](v: T) -> T:\n    return v\n"})
    assert plain.count("# [[parse.acknowledged]]") == 1
    assert "src/pkg/generic.py" in plain, "the generic example"
    detected = render_template(proposal=propose(found))
    assert "src/pkg/generic.py" not in detected


def test_a_proposal_is_deterministic(tmp_path: Path) -> None:
    found = detection_of(
        tmp_path,
        {
            ".gitattributes": "vendor/** linguist-vendored\n",
            "vendor/lib.py": "x = 1\n",
            "pyproject.toml": '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
            "tests/test_a.py": "x = 1\n",
        },
    )
    assert render_template(proposal=propose(found)) == render_template(proposal=propose(found))


def test_a_proposal_starts_from_the_configuration_already_in_force(tmp_path: Path) -> None:
    base = default_settings().model_copy(deep=True)
    base.project.exclude = ["docs/**"]
    found = detection_of(
        tmp_path, {".gitattributes": "vendor/** linguist-vendored\n", "vendor/lib.py": "x = 1\n"}
    )
    proposal = propose(found, base)
    assert proposal.settings.project.exclude == ["docs/**", "vendor/**"]


def test_a_proposal_does_not_repeat_an_exclusion_that_is_already_there(tmp_path: Path) -> None:
    base = default_settings().model_copy(deep=True)
    base.project.exclude = ["vendor/**"]
    found = detection_of(
        tmp_path, {".gitattributes": "vendor/** linguist-vendored\n", "vendor/lib.py": "x = 1\n"}
    )
    assert propose(found, base).settings.project.exclude == ["vendor/**"]


def test_write_template_writes_the_proposal(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    found = detection_of(
        repo, {".gitattributes": "vendor/** linguist-vendored\n", "vendor/lib.py": "x = 1\n"}
    )
    target = tmp_path / CONFIG_FILENAME
    write_template(target, proposal=propose(found))
    assert "vendor/**" in target.read_text(encoding="utf-8")


def test_every_scope_override_shape_round_trips_through_the_renderer() -> None:
    """min, severity and ratchet all have to survive the render, not just a bare maximum."""
    custom = default_settings().model_copy(deep=True)
    custom.scope["tests"] = PathScope.model_validate(
        {
            "paths": ["tests/**"],
            "thresholds": {
                "file": {"RatioCommentToCode": {"min": 0.05, "severity": "warning"}},
                "routine": {"CountLineCode": {"max": 120, "ratchet": False}},
            },
        }
    )
    text = render_template(custom)
    assert Settings.model_validate(tomllib.loads(text)) == custom


def test_a_second_configuration_naming_the_same_test_tree_adds_one_scope(tmp_path: Path) -> None:
    """Two manifests can name one directory; the proposal must not write it twice."""
    found = detection_of(
        tmp_path,
        {
            "pyproject.toml": '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
            "tox.ini": "[tool:pytest]\ntestpaths = tests\n",
            "tests/test_a.py": "x = 1\n",
        },
    )
    proposal = propose(found)
    assert list(proposal.settings.scope) == ["tests"]
    assert Settings.model_validate(tomllib.loads(render_template(proposal=proposal))) is not None
