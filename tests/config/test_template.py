"""`init` template: commented TOML that round-trips into the default Settings (req 3.9)."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Final

import pytest
from fixtures.constants import LEAN_RULE_SWITCHES

from scitools_hook.config.defaults import default_settings
from scitools_hook.config.detect import PARSE_REASONS, Detection, detect
from scitools_hook.config.models import (
    CouplingRule,
    LayerRule,
    LeanRules,
    ParseAcknowledgement,
    PathScope,
    Settings,
)
from scitools_hook.config.template import (
    _COMMENT_WIDTH,
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
    "[lean]",
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


# --- the [lean] block ---------------------------------------------------------------
#
# `[lean]` is the only place the second half of requirements 1.5, 2.4, 3.4 and 4.3 can be
# said. The model expresses "off" as `None`; "and a warning when you switch it on" is not a
# fact about the type at all, it is a fact about the line an operator reads in the file this
# renderer writes. So these tests assert the *text*, not only the settings it loads to.

LEAN_UNSET_KEYS: Final[tuple[str, ...]] = (*LEAN_RULE_SWITCHES, "max_net_growth")
"""Every ``[lean]`` key that ships unset, and therefore has to be rendered commented."""


def lean_block(text: str | None = None) -> str:
    """The rendered ``[lean]`` body: the lines after its header, up to the next section."""
    rendered = render_template() if text is None else text
    return rendered.split("\n[lean]\n")[1].split("\n\n")[0]


def lean_line(name: str, text: str | None = None) -> str:
    """The single line of the block that sets ``name``, whether or not it is commented."""
    lines = [
        line
        for line in lean_block(text).splitlines()
        if line.startswith(f"{name} = ") or line.startswith(f"# {name} = ")
    ]
    assert len(lines) == 1, f"{name} is written on {len(lines)} lines: {lines}"
    return lines[0]


def test_the_lean_block_names_every_key_the_model_carries() -> None:
    """Driven off ``LeanRules`` itself: a key added later and forgotten here fails here.

    A hand-written list would pass forever while the template quietly stopped documenting
    the newest rule, which is the only failure mode this test exists for (req 10.2).
    """
    block = lean_block()
    missing = [
        name
        for name in LeanRules.model_fields
        if not any(
            line.startswith(f"{name} = ") or line.startswith(f"# {name} = ")
            for line in block.splitlines()
        )
    ]

    assert missing == []


@pytest.mark.parametrize("rule", LEAN_UNSET_KEYS)
def test_every_lean_rule_is_written_commented_and_changes_nothing(rule: str) -> None:
    """Requirement 9.1: the shipped file documents each rule without enabling one."""
    assert lean_line(rule).startswith("# ")
    assert getattr(Settings.model_validate(tomllib.loads(render_template())).lean, rule) is None


@pytest.mark.parametrize("rule", LEAN_RULE_SWITCHES)
def test_every_off_lean_line_shows_the_value_that_enables_it(rule: str) -> None:
    """Requirements 1.5, 2.4, 3.4, 4.3, 5.5: off, and a warning when the operator says so."""
    line = lean_line(rule)

    assert f'# {rule} = "warning"' in line
    assert "unset: off" in line


@pytest.mark.parametrize("rule", LEAN_RULE_SWITCHES)
def test_every_off_lean_line_says_what_the_rule_reports(rule: str) -> None:
    """An operator learns what a rule does from the line, not from the reference."""
    _, _, described = lean_line(rule).partition("unset: off.")

    assert len(described.split()) >= 4, f"{rule} is not described: {described!r}"


def test_the_lean_numbers_and_ignore_lists_are_written_as_the_values_in_force() -> None:
    """The tunable half is not commented: an operator edits a number that is already there."""
    lean = tomllib.loads(render_template())["lean"]
    shipped = default_settings().lean

    assert lean["duplicates_min_lines"] == shipped.duplicates_min_lines
    assert lean["similar_threshold"] == shipped.similar_threshold
    assert lean["similar_min_statements"] == shipped.similar_min_statements
    assert lean["pass_through_max_statements"] == shipped.pass_through_max_statements
    assert lean["verbosity_min_statements"] == shipped.verbosity_min_statements
    assert lean["net_growth_severity"] == shipped.net_growth_severity
    assert lean["unused_parameters_ignore"] == shipped.unused_parameters_ignore
    assert lean["over_export_ignore"] == shipped.over_export_ignore
    assert set(lean) & set(LEAN_UNSET_KEYS) == set()


EVERY_LEAN_KEY_SET: Final[dict[str, object]] = {
    "unused_parameters": "warning",
    "unused_classes": "error",
    "unused_variables": "warning",
    "pass_through": "warning",
    "pass_through_max_statements": 1,
    "single_implementation": "warning",
    "over_export": "error",
    "over_export_ignore": ["**/__init__.py"],
    "duplicates": "warning",
    "duplicates_min_lines": 20,
    "duplicates_ignore": ["tests/**"],
    "similar_routines": "warning",
    "similar_min_statements": 8,
    "similar_threshold": 0.95,
    "similar_ignore": ["tests/**"],
    "verbosity_min_statements": 3,
    "max_net_growth": 40,
    "net_growth_severity": "error",
}
"""Every key the renderer branches on, set away from its default, plus the numbers.

Not literally every field -- the five name-pattern ignore lists keep their shipped values --
and that is enough: ``_lean_rule`` branches only on the eight switches and ``_lean_size``
only on ``max_net_growth``, so an ignore list has one rendering and it is already asserted
against the defaults above.
"""


def test_a_configured_lean_section_round_trips_through_the_renderer() -> None:
    """An enabled rule is written uncommented, at the severity it was set to."""
    custom = default_settings().model_copy(deep=True)
    custom.lean = LeanRules.model_validate(EVERY_LEAN_KEY_SET)
    text = render_template(custom)

    assert Settings.model_validate(tomllib.loads(text)) == custom
    assert lean_line("unused_parameters", text) == 'unused_parameters = "warning"'
    assert lean_line("max_net_growth", text) == "max_net_growth = 40"


def test_no_lean_prose_line_runs_past_the_width_the_file_wraps_at() -> None:
    """The block is documentation, and documentation that wraps in an editor reads as noise.

    Only the lines this renderer writes prose on: an ignore list is a value, and the shipped
    ``pass_through_ignore`` is the same four patterns ``structure.unused_ignore`` already
    renders on one line above. The width is read off the renderer rather than repeated here,
    so moving the wrap column moves this test with it instead of past it.
    """
    prose = [line for line in lean_block().splitlines() if "  # " in line or line.startswith("# ")]
    too_wide = [line for line in prose if len(line) > _COMMENT_WIDTH]

    assert too_wide == []


def test_every_lean_number_an_operator_cannot_read_off_its_name_carries_a_note() -> None:
    """``verbosity_min_statements = 5`` says nothing on its own; the trailing comment does."""
    assert "  # " in lean_line("verbosity_min_statements")
    assert "  # " in lean_line("net_growth_severity")
    assert "  # " in lean_line("similar_threshold")


def test_the_net_growth_line_promises_a_report_and_never_a_refusal() -> None:
    """Requirement 7.5: the delta never blocks by default, so its line may not say it does.

    ``net_growth_severity`` ships at ``warning``: uncommenting ``max_net_growth`` buys a
    finding, not a refused commit. The stems are checked rather than the sentence, because
    the failure this guards against is a rewording that drifts from the shipped severity --
    which is what a reviewer caught in this line's first draft.
    """
    line = lean_line("max_net_growth").lower()

    assert "refus" not in line
    assert "block" not in line
    assert default_settings().lean.net_growth_severity == "warning"
