"""`init` template: commented TOML that round-trips into the default Settings (req 3.9)."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path
from types import ModuleType
from typing import Final

import pytest
from fixtures.constants import LEAN_RULE_SWITCHES

from scitools_hook.config.defaults import default_settings
from scitools_hook.config.detect import PARSE_REASONS, Detection, detect
from scitools_hook.config.metric_names import declared_floor
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
from scitools_hook.runner import check, lean

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
    assert lean["resolution_floor"] == shipped.resolution_floor
    assert lean["accuracy_floor"] == shipped.accuracy_floor
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
    assert "  # " in lean_line("resolution_floor")
    assert "  # " in lean_line("accuracy_floor")


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


# --- the claim the block's help text makes about its two floors ----------------------
#
# `_LEAN_HELP` tells an operator why the two `*_floor` keys ship SET while every rule in the
# block ships off: no rule owns them, `runner.lean.evaluate` reads them on every run whatever
# the switches say, and every *other* set key belongs to one rule -- that rule's limit, its
# exception list or its severity -- and does nothing until the rule is on, with one named
# exception, `verbosity_min_statements`, whose rule `[thresholds.routine] LinesPerStatement`
# does ship on.
#
# That is a claim about the rendered block, and two earlier drafts of it were false on the
# screen they were printed on. The first ("the only lines below that ship SET") was refuted by
# every ignore list under it. The second reserved "set and in force while every rule here is
# off" for the floors, which `verbosity_min_statements` has too, and enumerated "limit or
# exception list" four lines above `net_growth_severity`. Both survived a test that asserted a
# paraphrase. So the sentence is asserted here key by key against the text `init` writes -- by
# kind, and by whether the rule that reads the key ships on -- and the owner names are bound to
# the code that reads them rather than left as free text a reviewer can rewrite unnoticed.

LEAN_FLOOR_KEYS: Final[frozenset[str]] = frozenset(
    name for name in LeanRules.model_fields if name.endswith("_floor")
)
"""The keys the help text calls "the two *_floor keys", read off the model rather than typed."""

LEAN_KEY_OWNERS: Final[dict[str, str]] = {
    "unused_parameters_ignore": "unused_parameters",
    "unused_classes_ignore": "unused_classes",
    "unused_variables_ignore": "unused_variables",
    "pass_through_max_statements": "pass_through",
    "pass_through_ignore": "pass_through",
    "single_implementation_ignore": "single_implementation",
    "over_export_ignore": "over_export",
    "duplicates_min_lines": "duplicates",
    "duplicates_ignore": "duplicates",
    "similar_min_statements": "similar_routines",
    "similar_threshold": "similar_routines",
    "similar_ignore": "similar_routines",
    "verbosity_min_statements": "LinesPerStatement",
    "net_growth_severity": "max_net_growth",
}
"""The rule that reads each ``[lean]`` key that ships set, floors excepted.

Hand-written on purpose: naming the rule is the work the claim asks of whoever adds a key,
and a mapping derived from the key names could not have caught the three keys whose name
does not carry their rule's -- ``similar_min_statements``, ``verbosity_min_statements`` and
``net_growth_severity``. The test below refuses to pass while a set key is missing from here.

The names are not free text either. Eight of them are checked against the switch that guards
the key's only read in ``runner.lean``, ``verbosity_min_statements``'s against the metric
catalogue and the unguarded read in ``runner.check``, and the five token keys against the
stem they share with their rule -- because a plausible wrong name here is exactly where the
defect this comment describes hid through two rounds of review.
"""

LEAN_KEY_KINDS: Final[dict[str, str]] = {
    "unused_parameters_ignore": "exception list",
    "unused_classes_ignore": "exception list",
    "unused_variables_ignore": "exception list",
    "pass_through_max_statements": "limit",
    "pass_through_ignore": "exception list",
    "single_implementation_ignore": "exception list",
    "over_export_ignore": "exception list",
    "duplicates_min_lines": "limit",
    "duplicates_ignore": "exception list",
    "similar_min_statements": "limit",
    "similar_threshold": "limit",
    "similar_ignore": "exception list",
    "verbosity_min_statements": "limit",
    "net_growth_severity": "severity",
}
"""What each owned key *is*, in the words the help text enumerates.

Hand-written for the same reason as the owners: ``net_growth_severity`` is a severity and
not a limit, and the draft that called every owned key "a limit or an exception list" was
wrong about a key printed four lines below it. A key of a fourth kind fails the test below
until the help text learns to say that kind.
"""

HELP_KINDS: Final[frozenset[str]] = frozenset(LEAN_KEY_KINDS.values())
"""The kinds the help text has to enumerate, read off the table rather than typed twice."""


def lean_set_keys(text: str | None = None) -> set[str]:
    """Every key the rendered block ships *set*: an uncommented ``name = value`` line."""
    return _set_keys(lean_block(text))


def routine_metrics(text: str | None = None) -> set[str]:
    """Every metric ``[thresholds.routine]`` ships live, which is every rule that block sets."""
    rendered = render_template() if text is None else text
    return _set_keys(rendered.split("\n[thresholds.routine]\n")[1].split("\n\n")[0])


def _set_keys(block: str) -> set[str]:
    return {
        line.split(" = ", 1)[0]
        for line in block.splitlines()
        if not line.startswith("#") and " = " in line
    }


def lean_help(text: str | None = None) -> str:
    """The help paragraph ``_LEAN_HELP`` renders above the header, as one line of prose."""
    rendered = render_template() if text is None else text
    comments = rendered.split("\n[lean]\n")[0].split("\n\n")[-1]
    return " ".join(line.removeprefix("# ") for line in comments.splitlines())


def test_every_set_lean_key_is_one_of_the_three_kinds_the_help_text_enumerates() -> None:
    """The enumeration, asserted over the rendered block rather than over a paraphrase.

    Three kinds and no fourth: a key that is neither a limit, an exception list nor a
    severity fails here, and so does a key nobody classified. One sentence of the help text
    has to name all three, so a kind dropped from the enumeration -- or a fourth kind added
    to the block and left out of it -- fails here rather than on the operator's screen.
    """
    enumerating = [
        sentence
        for sentence in lean_help().split(". ")
        if all(kind in sentence for kind in HELP_KINDS)
    ]

    assert lean_set_keys() == set(LEAN_KEY_OWNERS) | LEAN_FLOOR_KEYS
    assert set(LEAN_KEY_KINDS) == set(LEAN_KEY_OWNERS)
    assert HELP_KINDS == {"limit", "exception list", "severity"}
    assert len(enumerating) == 1, "no one sentence of the help text names all three kinds"


def test_the_one_set_lean_key_whose_rule_ships_on_is_the_exception_the_help_text_names() -> None:
    """Requirement 1.8: the floors are not the only set keys in force, and the block says so.

    ``verbosity_min_statements`` is read by ``[thresholds.routine] LinesPerStatement``, which
    ships on, so it does something the moment `init` writes it while every rule in this block
    is off. That is the property an earlier draft reserved for the floors. It is counted here
    -- exactly one such key -- and the operator has to meet it on the same screen, so the
    help text is required to name both the key and the rule. Every other owned key waits on a
    ``[lean]`` switch that ships commented off.
    """
    live = {key: owner for key, owner in LEAN_KEY_OWNERS.items() if owner in routine_metrics()}

    assert live == {"verbosity_min_statements": "LinesPerStatement"}
    for key in sorted(set(LEAN_KEY_OWNERS) - set(live)):
        owner = LEAN_KEY_OWNERS[key]
        assert owner in LEAN_UNSET_KEYS, f"{key}'s rule {owner} is no switch this block ships"
        assert lean_line(owner).startswith("# "), f"{key}'s rule {owner} does not ship off"
    for named in ("verbosity_min_statements", "LinesPerStatement", "[thresholds.routine]"):
        assert named in lean_help(), f"the help text does not name {named}"


def test_the_key_whose_rule_ships_on_is_read_with_no_lean_switch_between() -> None:
    """The exception, as the code states it: ``LinesPerStatement`` is the floored metric, and
    ``runner.check.run`` stamps the operator's number on the thresholds unconditionally.

    Both halves matter. The catalogue half binds the owner name -- a plausible wrong rule
    typed into ``LEAN_KEY_OWNERS`` names no metric that declares a floor. The unguarded-read
    half is why the key is in force at all: the call is a statement of ``run``'s own body, so
    no ``[lean]`` switch stands above it, and ``run`` reads no switch anywhere.
    """
    floored = {metric for metric in routine_metrics() if declared_floor(metric) is not None}
    run = _function(check, "run")
    stamps = [line for line in _statements(run) if "with_floor(" in line]

    assert floored == {LEAN_KEY_OWNERS["verbosity_min_statements"]}
    assert stamps == [
        "effective = with_floor(effective, self.ctx.settings.lean.verbosity_min_statements)"
    ]
    assert [switch for switch in LEAN_RULE_SWITCHES if f"lean.{switch}" in ast.unparse(run)] == []


def test_the_floors_are_the_only_set_lean_keys_no_rule_owns() -> None:
    """The other half of the sentence: two floors, owned by nothing, read on every run.

    ``runner.lean.evaluate`` builds the run's ``Trust`` from both of them in a statement of
    its own body -- no switch above it, and nothing in the section can suppress it -- which is
    why they can ship set without turning a rule on.
    """
    built = [line for line in _statements(_function(lean, "evaluate")) if "Trust(" in line]

    assert len(LEAN_FLOOR_KEYS) == 2
    assert LEAN_FLOOR_KEYS.isdisjoint(LEAN_KEY_OWNERS)
    assert built == ["trust = Trust(accuracy, rules.resolution_floor, rules.accuracy_floor)"]


def test_every_owner_a_wired_rule_names_is_the_switch_its_key_s_read_is_guarded_by() -> None:
    """``LEAN_KEY_OWNERS`` bound to the code, so a plausible wrong name fails here.

    ``runner.lean`` reads a rule's own keys only where that rule's switch has been found set,
    either inside ``if rules.<switch> is not None:`` or after ``if rules.<switch> is None:
    return``. That guard *is* the ownership the help text claims, so it is read out of the
    module and compared, name by name.

    Six keys have no such read yet. ``verbosity_min_statements`` never will -- the test above
    owns it -- and the five token keys wait on tasks 5.1-5.5, which wire ``duplicates`` and
    ``similar_routines`` into this step; until then their owner is checked against the stem
    the key shares with its rule, and this list has to shrink when those tasks land.
    """
    guarded = _guarded_reads(_source(lean))
    unwired = set(LEAN_KEY_OWNERS) - set(guarded)

    assert {key: LEAN_KEY_OWNERS[key] for key in guarded} == guarded
    assert unwired == {"verbosity_min_statements", *_TOKEN_RULE_KEYS}
    for key in sorted(_TOKEN_RULE_KEYS):
        owner = LEAN_KEY_OWNERS[key]
        assert owner in LEAN_RULE_SWITCHES, f"{key}'s rule {owner} is no rule of this block"
        assert owner.split("_")[0] == key.split("_")[0], f"{key} is not {owner}'s key"


_TOKEN_RULE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "duplicates_min_lines",
        "duplicates_ignore",
        "similar_min_statements",
        "similar_threshold",
        "similar_ignore",
    }
)
"""The keys of the two rules answered from token streams, which tasks 5.1-5.5 still owe."""


def test_the_accuracy_floor_note_keeps_the_other_key_it_exists_to_be_told_apart_from() -> None:
    """``[lean] accuracy_floor`` refuses; ``[analysis] accuracy_floor`` only reports.

    The note beside the line is where an operator meets that, and the wording is the whole
    of it: stripped back to "the parse floor" the line still parses, still carries a comment
    marker, and quietly leaves the two keys looking like one. So the words that separate them
    are asserted rather than the marker alone.
    """
    _, _, note = lean_line("accuracy_floor").partition("  # ")

    assert "[analysis] accuracy_floor" in note
    assert "only reports" in note
    assert "REFUSES" in lean_help()


def _source(module: ModuleType) -> ast.Module:
    return ast.parse(Path(module.__file__ or "").read_text(encoding="utf-8"))


def _function(module: ModuleType, name: str) -> ast.FunctionDef:
    """The one routine ``name`` in ``module``, found wherever it sits in the file."""
    found = [
        node
        for node in ast.walk(_source(module))
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    assert len(found) == 1, f"{name} is not the only {name} in {module.__name__}"
    return found[0]


def _statements(function: ast.FunctionDef) -> list[str]:
    """``function``'s own statements, unparsed. A branch's body is not one of them."""
    return [ast.unparse(statement) for statement in function.body]


def _guarded_reads(tree: ast.Module) -> dict[str, str]:
    """Each ``rules.<key>`` read only reachable once a switch was found set, and the switch."""
    owners: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            owners.update(_reads_in(node))
    return owners


def _reads_in(function: ast.FunctionDef) -> dict[str, str]:
    owners: dict[str, str] = {}
    after: str | None = None
    for statement in function.body:
        switch, on = _switch_guard(statement)
        if switch is not None and on:
            owners.update(dict.fromkeys(_keys_read(statement), switch))
        elif switch is not None:
            after = switch
        elif after is not None:
            owners.update(dict.fromkeys(_keys_read(statement), after))
    return owners


def _switch_guard(statement: ast.stmt) -> tuple[str | None, bool]:
    """The switch an ``if rules.<switch> is [not] None`` tests, and whether it tests it set."""
    if not isinstance(statement, ast.If):
        return None, False
    return _guard_test(statement.test)


def _guard_test(test: ast.expr) -> tuple[str | None, bool]:
    if not isinstance(test, ast.Compare) or not _is_none(test.comparators):
        return None, False
    named = _rules_attribute(test.left)
    if named not in LEAN_UNSET_KEYS:
        return None, False
    return named, isinstance(test.ops[0], ast.IsNot)


def _is_none(comparators: list[ast.expr]) -> bool:
    against = comparators[0] if len(comparators) == 1 else None
    return isinstance(against, ast.Constant) and against.value is None


def _rules_attribute(node: ast.expr) -> str | None:
    if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
        return None
    return node.attr if node.value.id == "rules" else None


def _keys_read(statement: ast.stmt) -> set[str]:
    """The ``rules.<key>`` names ``statement`` reads, switches excluded: those are the guards."""
    read = [node for node in ast.walk(statement) if isinstance(node, ast.Attribute)]
    named = {_rules_attribute(node) for node in read}
    return {name for name in named if name is not None} - set(LEAN_UNSET_KEYS)
