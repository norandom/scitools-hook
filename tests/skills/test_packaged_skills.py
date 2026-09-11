"""The agent skills this package ships, as data.

Separate from ``tests/cli/test_skills_command.py`` because it is a different question with a
different reach: nothing here starts a command, builds a repository or touches an exit code.
It asks only whether the two documents are inside the installed package and well formed --
read through :mod:`importlib.resources`, not off ``src/``, so a packaging mistake that leaves
them out of the wheel fails here rather than in somebody's install.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scitools_hook import skills
from scitools_hook.config.models import LeanRules
from scitools_hook.models.change import NetDelta
from scitools_hook.models.findings import structure_rule
from scitools_hook.report.hints import DEFAULT_CATALOGUE
from scitools_hook.report.human import LEAN_ALREADY_PREFIX, net_line
from scitools_hook.report.lean_examples import LEAN_RULES

REPO_ROOT = Path(__file__).resolve().parents[2]

INSTALLED_COPIES = (".agents/skills", ".claude/skills")
"""Where this repository keeps the skills it ships: vendor-neutral, and for Claude Code."""


def test_both_skills_are_readable_from_the_installed_package() -> None:
    """The documents are package data, not files that happen to sit beside the source."""
    shipped = skills.shipped()
    assert [skill.name for skill in shipped] == [
        "scitools-onboard",
        "scitools-gate",
        "scitools-improve",
        "scitools-adapt",
    ]
    for skill in shipped:
        assert skill.text.startswith("---\n"), f"{skill.name} has no front matter"
        assert f"name: {skill.name}\n" in skill.text
        assert "description:" in skill.text
        assert skill.relative_path == f"{skill.name}/SKILL.md"


def test_a_name_this_release_does_not_ship_is_a_key_error() -> None:
    """So a typo cannot be installed as an empty skill."""
    with pytest.raises(KeyError):
        skills.read("scitools-improv")


@pytest.mark.parametrize("directory", INSTALLED_COPIES)
@pytest.mark.parametrize("name", skills.NAMES)
def test_the_checked_in_copies_match_what_is_packaged(directory: str, name: str) -> None:
    """The copies this repository commits are the shipped ones, byte for byte.

    They exist so an agent working *on this project* can load the skills from the place its
    host reads. Nothing else keeps them in step with ``src/scitools_hook/skills``, so a change
    made in one copy and not the others fails here rather than shipping a skill that
    disagrees with the documentation.
    """
    skill = skills.read(name)
    target = REPO_ROOT / directory / skill.relative_path
    assert target.is_file(), f"missing; run: scitools-hook install-skills --dir {directory}"
    assert target.read_text(encoding="utf-8") == skill.text


# --- what the skills say about the lean-code family (lean-code-rules req 8.7) ---------
#
# A claim in a skill has to be true of the shipped code, and these tests are what makes it
# fail when it stops being. Every number a skill quotes is read from the shipped default
# here rather than typed, so a defaults task cannot desynchronise the document; the net
# line's shape is produced by the renderer that prints it; the three tags are read off the
# hint catalogue. Where a skill names a *wording* the reports use, the assertion is the
# fixed fragment of that wording, so a renderer change reaches the document.

SHIPPED_LEAN = LeanRules()
"""The family's defaults as they ship; the skills quote these and nothing typed."""


def _lean_hints() -> dict[str, str]:
    """Every shipped hint of the family, variants included, keyed as the catalogue keys them."""
    names = {structure_rule(name) for name in LEAN_RULES}
    return {
        rule: hint
        for rule, hint in DEFAULT_CATALOGUE.items()
        if rule.split("/")[0] in names and not rule.endswith("/example")
    }


def _tags_shipped() -> set[str]:
    """The tag each lean hint opens with, read off the catalogue (req 8.1)."""
    return {hint.split(":", 1)[0] + ":" for hint in _lean_hints().values()}


@pytest.mark.parametrize("name", skills.NAMES)
def test_every_skill_names_the_net_line_or_a_lean_rule(name: str) -> None:
    """The task's done-when: each of the four names the net line or a lean rule."""
    text = skills.read(name).text
    lean_rules = [structure_rule(rule) for rule in LEAN_RULES]
    assert "net:" in text or any(rule in text for rule in lean_rules), name


def test_the_gate_skill_reads_the_net_line_in_the_shape_the_report_prints() -> None:
    """The example the skill shows is the renderer's output, not a transcription of it.

    A deleted file that held no routines contributes nothing to the sum, so a change that is
    only such a deletion reads as net zero over no routines; the skill says so in the words
    the report would use.
    """
    text = skills.read("scitools-gate").text
    assert net_line(NetDelta(statements=12, lines=30, routines=7)) in text
    assert net_line(NetDelta(statements=0, lines=0, routines=0)) in text
    assert LEAN_ALREADY_PREFIX.strip() in text


def test_the_gate_skill_names_the_three_tags_and_the_two_it_never_sees() -> None:
    """The tags the skill lists are the ones the catalogue ships, and only those (req 8.3)."""
    text = skills.read("scitools-gate").text
    shipped = _tags_shipped()
    assert shipped == {"delete:", "yagni:", "shrink:"}, shipped
    for tag in shipped:
        assert f"`{tag}`" in text, tag
    for never in ("stdlib:", "native:"):
        assert never not in {hint.split(":", 1)[0] + ":" for hint in _lean_hints().values()}
        assert f"`{never}`" in text, never


def test_the_gate_skill_says_what_a_refused_rule_prints() -> None:
    """The once-per-run refusal is a sentence the agent must not read as a clean answer."""
    text = skills.read("scitools-gate").text
    assert "was not evaluated" in text
    assert f"{SHIPPED_LEAN.accuracy_floor:.0%}" in text


def test_the_improve_skill_works_the_lean_findings_largest_reduction_first() -> None:
    """The ordering reads the two duplication rules' own messages, so the fragments it
    parses are the ones the rules write."""
    text = skills.read("scitools-improve").text
    assert structure_rule("similar_routine") in text
    assert structure_rule("duplicate_block") in text
    assert "is 1 of" in text, "the family message's fixed fragment"
    assert "code lines" in text, "the block message's fixed fragment"
    assert "also_at" in text, "the detail that names the other copies"
    assert "largest reduction first" in text
    assert "net:" in text


def test_the_adapt_skill_quotes_the_family_numbers_from_the_shipped_defaults() -> None:
    """Each rung names its key with the value that ships, read from ``LeanRules()``."""
    text = skills.read("scitools-adapt").text
    for key in (
        "similar_threshold",
        "similar_min_statements",
        "similar_min_family",
        "duplicates_min_lines",
    ):
        value = getattr(SHIPPED_LEAN, key)
        assert f"{key} = {value}" in text, key
    for key in ("similar_name_ignore", "similar_ignore", "duplicates_ignore"):
        assert f"`{key}`" in text, key


def test_the_adapt_skill_refuses_the_floors_as_a_rung() -> None:
    """Lowering a floor is not adaptation: the shipped value is named and marked as not a rung."""
    text = skills.read("scitools-adapt").text
    for key in ("resolution_floor", "accuracy_floor"):
        value = getattr(SHIPPED_LEAN, key)
        assert f"{key} = {value}" in text, key
    assert "never a rung" in text.lower() or "not a rung" in text.lower()


def test_the_onboard_skill_proposes_the_duplication_rules_and_refuses_dead_code_blindly() -> None:
    """The two rules it may propose are the measured half; the dead-code rules come with
    the floor that refuses them and the accuracy row to read first."""
    text = skills.read("scitools-onboard").text
    assert 'similar_routines = "warning"' in text
    assert 'duplicates = "warning"' in text
    for key in ("resolution_floor", "accuracy_floor"):
        assert f"{key} = {getattr(SHIPPED_LEAN, key)}" in text, key
    assert "after accuracy" in text, "the doctor row that says whether the floor is met"
    for rule in ("unused_parameters", "unused_classes", "unused_variables", "pass_through"):
        assert rule in text, rule
    assert "blindly" in text or "never enable" in text
