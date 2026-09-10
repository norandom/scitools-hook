"""The lean-code family's hints and worked examples (lean-code req 8.1, 8.2, 8.3, 8.6).

The rest of the catalogue is tested in ``test_hints.py``; this module holds the three
properties that are the family's own and that no other rule has:

* **every lean hint opens with one of three tags** -- ``delete:``, ``yagni:`` or ``shrink:``
  -- so an agent reading a finding knows before the first comma whether the code goes away,
  the layer goes away, or the same logic gets shorter (8.1);
* **neither of ponytail's other two tags is ever emitted**. ``stdlib:`` and ``native:`` ask
  whether a routine re-implements something the language already ships, which is a semantic
  question a reference database cannot answer, so the Gate leaves both to the agent (8.3).
  The test asserts their *absence*, because a hint carrying one would be the Gate claiming
  knowledge it does not have -- a stricter failure than a hint carrying none of the three;
* **every lean rule carries one worked before-and-after example** (8.2), and an operator can
  replace the hint and the example independently at the rule level (8.6).

The family list itself is :data:`~scitools_hook.report.lean_examples.LEAN_RULES`, derived
from the examples table, so a rule with no example cannot reach these tests as a rule with
one: it drops out of the tuple and the tail assertion below fails instead.
"""

from __future__ import annotations

from typing import Final

import pytest

from scitools_hook.models.findings import (
    STRUCTURE_RULES,
    Finding,
    StructureRuleName,
    structure_rule,
)
from scitools_hook.report.hints import (
    DEFAULT_CATALOGUE,
    PARSE_CONSTRUCTS,
    SAME_FILE,
    VARIANT_SEPARATOR,
    HintCatalogue,
)
from scitools_hook.report.lean_examples import EXAMPLE_SUFFIX, EXAMPLES, LEAN_RULES

TAGS: Final = ("delete:", "yagni:", "shrink:")
"""The three ponytail tags the Gate can answer from references, metrics and tokens (8.1)."""

NEVER: Final = ("stdlib:", "native:")
"""The two it never emits (8.3): whether a routine re-implements a library is semantics."""

SIMILAR: Final = structure_rule("similar_routine")
"""The one lean rule with two hints: a twin in another file, and a twin in this one."""


def lean_finding(rule: str, construct: str = "") -> Finding:
    """A structural finding carrying the fields the catalogue reads: its kind and construct."""
    details = {"construct": construct} if construct else {}
    return Finding(kind="structural", rule=rule, scope="file", message="lean", details=details)


# --- the tag every lean hint opens with (8.1, 8.3) --------------------------------


@pytest.mark.parametrize("name", LEAN_RULES)
def test_every_lean_hint_opens_with_a_measurable_tag(name: StructureRuleName) -> None:
    rule = structure_rule(name)
    got = HintCatalogue({}).hint(rule, lean_finding(rule))

    assert got.startswith(TAGS), f"{rule}: {got[:40]!r}"


@pytest.mark.parametrize("name", LEAN_RULES)
def test_no_lean_hint_claims_a_tag_the_gate_cannot_answer(name: StructureRuleName) -> None:
    """Not the assertion above: a hint may open with a legal tag and still reach for another.

    "delete: the wrapper -- stdlib: has this" passes the opening test and is exactly the
    claim requirement 8.3 says the Gate has no evidence for, so the tag is refused anywhere
    in the text rather than only at the front.
    """
    rule = structure_rule(name)
    got = HintCatalogue({}).hint(rule, lean_finding(rule))

    for tag in NEVER:
        assert tag not in got, f"{rule} emits {tag}"


def test_the_same_file_family_shrinks_where_the_cross_file_family_deletes() -> None:
    """The one rule whose remedy depends on where the other copies are (design, `similar`).

    Two files: one member of the family goes away, so the tag is `delete:`. One file: the
    family becomes a single routine and the file gets shorter, so the tag is `shrink:`. The
    variant level of the existing four-level lookup selects it from `details["construct"]`.
    """
    catalogue = HintCatalogue({})

    across = catalogue.hint(SIMILAR, lean_finding(SIMILAR))
    within = catalogue.hint(SIMILAR, lean_finding(SIMILAR, construct=SAME_FILE))

    assert across.startswith("delete:")
    assert within.startswith("shrink:")


def test_the_same_file_construct_is_spelled_the_way_the_rule_writes_it() -> None:
    """Pins the *value*, which is half of an agreement the other half cannot import.

    ``analysis.lean.similar`` sits below ``report`` and may not import :data:`SAME_FILE`, so
    it writes ``details["construct"] = "same_file"`` as a literal. Every other test here reads
    the constant, which means all of them pass under a renamed constant while the rule on the
    analysis side keeps writing the old string and silently falls through to the cross-file
    `delete:` wording -- wrong advice, not an error. This is the assertion that fails first.
    """
    assert SAME_FILE == "same_file"


def test_the_net_growth_hint_is_a_shrink() -> None:
    """Nothing is dead here: the change is bigger than it needs to be (7.5, 8.1)."""
    rule = structure_rule("net_growth")

    assert HintCatalogue({}).hint(rule, lean_finding(rule)).startswith("shrink:")


# --- the worked example (8.2) -----------------------------------------------------


@pytest.mark.parametrize("name", LEAN_RULES)
def test_every_lean_rule_carries_a_worked_example(name: StructureRuleName) -> None:
    got = HintCatalogue({}).example(structure_rule(name))

    assert got
    assert got.strip() == got


@pytest.mark.parametrize("name", LEAN_RULES)
def test_an_example_opens_in_ponytails_form_and_then_shows_the_shorter_form(
    name: StructureRuleName,
) -> None:
    """A location and a tag on the first line, then the shorter form in two to five lines.

    The point of the shape is that the example *shows* the code rather than describing it,
    so the body is pinned to a length that cannot hold a paragraph, and the before and after
    sides are pinned to being there at all.
    """
    rule = structure_rule(name)
    example = HintCatalogue({}).example(rule)
    assert example is not None
    header, *body = example.splitlines()

    assert ":" in header
    assert any(tag in header for tag in TAGS), header
    assert 2 <= len([line for line in body if line.strip()]) <= 5, example
    assert "before" in example and "after" in example


@pytest.mark.parametrize("name", LEAN_RULES)
def test_an_example_never_names_a_tag_the_gate_cannot_answer(name: StructureRuleName) -> None:
    example = HintCatalogue({}).example(structure_rule(name))
    assert example is not None

    for tag in NEVER:
        assert tag not in example, f"{structure_rule(name)} example emits {tag}"


def test_a_rule_outside_the_family_has_no_example() -> None:
    """Requirement 8.2 is about the lean family; everything else answers ``None``."""
    assert HintCatalogue({}).example(structure_rule("file_cycle")) is None
    assert HintCatalogue({}).example("routine.CountLineCode") is None


def test_an_ungrammatical_rule_asks_for_an_example_without_raising() -> None:
    assert HintCatalogue({}).example("nonsense") is None


# --- overrides at the rule level (8.6) --------------------------------------------


def test_an_override_replaces_the_hint_and_the_example_independently() -> None:
    rule = structure_rule("pass_through")
    catalogue = HintCatalogue({f"{rule}{EXAMPLE_SUFFIX}": "our example"})

    assert catalogue.example(rule) == "our example"
    assert catalogue.hint(rule, lean_finding(rule)) == DEFAULT_CATALOGUE[rule]


def test_an_override_of_the_hint_alone_leaves_the_shipped_example_standing() -> None:
    """The fourth corner of the matrix, and the one an operator reaches for first.

    Rewording a hint in house language must not silently take the worked example with it:
    they are two keys, and overriding one is not a statement about the other.
    """
    rule = structure_rule("single_implementation")
    catalogue = HintCatalogue({rule: "yagni: our wording"})

    assert catalogue.hint(rule, lean_finding(rule)) == "yagni: our wording"
    assert catalogue.example(rule) == DEFAULT_CATALOGUE[f"{rule}{EXAMPLE_SUFFIX}"]


def test_an_override_can_replace_both_at_once() -> None:
    rule = structure_rule("over_export")
    catalogue = HintCatalogue(
        {rule: "delete: our wording", f"{rule}{EXAMPLE_SUFFIX}": "our example"}
    )

    assert catalogue.hint(rule, lean_finding(rule)) == "delete: our wording"
    assert catalogue.example(rule) == "our example"


def test_an_example_emptied_by_an_override_prints_nothing_rather_than_a_blank_block() -> None:
    """An operator silencing an example must not leave an empty `example:` under the hint."""
    rule = structure_rule("duplicate_block")
    catalogue = HintCatalogue({f"{rule}{EXAMPLE_SUFFIX}": ""})

    assert catalogue.example(rule) is None


def test_an_operator_can_add_an_example_to_a_rule_that_ships_none() -> None:
    rule = structure_rule("fan_in")
    catalogue = HintCatalogue({f"{rule}{EXAMPLE_SUFFIX}": "split the hub"})

    assert catalogue.example(rule) == "split the hub"


# --- the keys themselves ----------------------------------------------------------


def test_the_family_closes_the_structural_rule_list() -> None:
    """A rule added to the family without an example drops out of :data:`LEAN_RULES`.

    The tuple is derived from the examples table, so a missing example shortens it and this
    assertion fails: `STRUCTURE_RULES` is the grammar, and the tail of it is the family.
    """
    assert LEAN_RULES
    assert STRUCTURE_RULES[-len(LEAN_RULES) :] == LEAN_RULES


def test_every_example_key_is_the_rule_plus_the_reserved_suffix() -> None:
    assert EXAMPLE_SUFFIX == f"{VARIANT_SEPARATOR}example"
    assert set(EXAMPLES) == {f"{structure_rule(name)}{EXAMPLE_SUFFIX}" for name in LEAN_RULES}
    assert all(DEFAULT_CATALOGUE[key] == text for key, text in EXAMPLES.items())


def test_example_is_reserved_as_a_variant_name_so_no_construct_may_take_it() -> None:
    """The example key rides the variant namespace, so `example` cannot also be a construct.

    A rule setting ``details["construct"] = "example"`` would make the hint lookup return the
    worked example instead of the hint, silently. Nothing does; this is what keeps it so.
    """
    assert "example" not in {name for name, _ in PARSE_CONSTRUCTS}
    assert SAME_FILE != "example"
