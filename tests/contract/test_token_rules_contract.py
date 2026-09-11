"""The two token rules over the whole fixture, at the shipped numbers (lean-code-rules 5.7).

``lean/`` and ``native/`` plant one copied block and one renamed twin per language, and the
fixture's docstrings argue that the twin is not a block and the block is not a twin. The test
runs both rules over the whole fixture at the shipped numbers and asserts the **exact**
finding set -- four blocks, two families -- rather than that the planted ones appear. A
fixture that produced the planted twin and three accidental families would pass a weaker
assertion and tell task 6.4 the defaults are noisier than they are. The expected set is
written out as two module constants so the test body says what it measures and the constants
say what the fixture is.

The assertion pins one shape of the shipped rule that its own module calls a gap: each block
names **three** locations, and all three are windows of the *one* other copy (``:10``, ``:11``
and ``:12`` of a fourteen-line block at twelve). That is item 7 of the review-found list in
``research.md`` -- collapse a path's locations to one per contiguous run before the cap --
owned by no task. It is asserted as measured so that the fix, when it comes, is shown to change
the output on the real build rather than only on the fake.

The coverage of the index is ``test_token_index_contract``'s subject and the docstring
accounting ``test_docstring_lines_contract``'s; ``token_index_project`` holds what they share.
"""

from __future__ import annotations

import pytest
from contract_project import (
    FILES,
    SampleProject,
    contract_settings,
    sample_project,  # noqa: F401 -- imported so the session fixture is registered here
)
from token_index_project import keys_of, token_snapshot

from scitools_hook.analysis.lean.duplicates import DEFAULT_MIN_LINES, find_duplicate_blocks
from scitools_hook.analysis.lean.similar import SIMILAR_DEFAULTS, find_similar_routines
from scitools_hook.models.findings import Finding

pytestmark = pytest.mark.contract

Block = tuple[str, int, int, float, tuple[str, ...]]
"""``(path, line, end_line, value, also_at)`` -- one duplicate-block finding as compared."""

Family = tuple[str, int, str, float, float, tuple[str, ...], object]
"""``(path, line, longname, value, similarity, family, construct)`` -- one family as compared."""

EXPECTED_BLOCKS: list[Block] = [
    (
        "lean/table_left.py",
        11,
        24,
        14.0,
        ("lean/table_right.py:10", "lean/table_right.py:11", "lean/table_right.py:12"),
    ),
    (
        "lean/table_right.py",
        10,
        23,
        14.0,
        ("lean/table_left.py:11", "lean/table_left.py:12", "lean/table_left.py:13"),
    ),
    (
        "native/lean_table_left.cpp",
        13,
        28,
        16.0,
        (
            "native/lean_table_right.cpp:7",
            "native/lean_table_right.cpp:8",
            "native/lean_table_right.cpp:9",
        ),
    ),
    (
        "native/lean_table_right.cpp",
        7,
        22,
        16.0,
        (
            "native/lean_table_left.cpp:13",
            "native/lean_table_left.cpp:14",
            "native/lean_table_left.cpp:15",
        ),
    ),
]
"""Every duplicate block the fixture holds at the shipped ``min_lines``, sorted.

The line ranges are the fixture's own: the Python block is the fourteen lines from
``return {`` to ``}`` and the C++ block the sixteen from the initialiser to the routine's
closing brace. The three locations per block are three windows of one copy: see the module
docstring.
"""

EXPECTED_FAMILIES: list[Family] = [
    (
        "lean/twin_left.py",
        9,
        "twin_left.summarise_orders",
        2.0,
        1.0,
        ("twin_right.summarise_invoices (lean/twin_right.py:9)",),
        None,
    ),
    (
        "native/lean_twin_left.cpp",
        13,
        "summarise_native_orders",
        2.0,
        1.0,
        ("summarise_native_invoices (native/lean_twin_right.cpp:4)",),
        None,
    ),
]
"""Every similar-routine family the fixture holds at the shipped threshold, sorted.

Each twin family is anchored at the ``_left`` member, which sorts first, and holds at exactly
1.0 because the two shapes are identical once names and values are one token each.
"""


def _block(finding: Finding) -> Block:
    """One duplicate-block finding as the tuple the exact-set assertion compares."""
    also_at = finding.details["also_at"]
    assert isinstance(also_at, list)
    end_line = finding.details["end_line"]
    assert isinstance(end_line, int)
    return (finding.path, finding.line or 0, end_line, finding.value or 0.0, tuple(also_at))


def _family(finding: Finding) -> Family:
    """One similar-routine finding as the tuple the exact-set assertion compares."""
    family = finding.details["family"]
    assert isinstance(family, list)
    return (
        finding.path,
        finding.line or 0,
        str(finding.details["longname"]),
        finding.value or 0.0,
        float(str(finding.details["similarity"])),
        tuple(family),
        finding.details.get("construct"),
    )


def test_contract_the_planted_block_and_twin_are_the_only_duplication_findings(
    sample_project: SampleProject,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Requirement 5.7: the exact finding set at the shipped numbers, printed for the record.

    The whole fixture is the change, so every block and every family is reported once, and
    the two lists are compared whole against :data:`EXPECTED_BLOCKS` and
    :data:`EXPECTED_FAMILIES` -- equality, not containment. The shipped numbers are read off
    a default ``LeanRules`` and held equal to the rule modules' own constants first, so the
    run is at the numbers an operator gets and not at numbers the test picked.
    """
    snapshot = token_snapshot(sample_project)
    shipped = contract_settings().lean
    assert (DEFAULT_MIN_LINES, SIMILAR_DEFAULTS.threshold, SIMILAR_DEFAULTS.min_statements) == (
        shipped.duplicates_min_lines,
        shipped.similar_threshold,
        shipped.similar_min_statements,
    )
    blocks = find_duplicate_blocks(snapshot, FILES)
    families = find_similar_routines(snapshot, keys_of(snapshot, "routine"))
    assert blocks.unavailable == ()
    assert families.unavailable == ()

    found_blocks = sorted(_block(finding) for finding in blocks.findings)
    found_families = sorted(_family(finding) for finding in families.findings)
    with capsys.disabled():
        print(f"\n  duplicate_block at min_lines {shipped.duplicates_min_lines}:")
        for block in found_blocks:
            print("   ", block)
        print(
            f"  similar_routine at threshold {shipped.similar_threshold}, "
            f"min_statements {shipped.similar_min_statements}:"
        )
        for family in found_families:
            print("   ", family)

    assert found_blocks == EXPECTED_BLOCKS
    assert found_families == EXPECTED_FAMILIES
