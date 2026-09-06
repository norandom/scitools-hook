"""How many times a run reads each side, and how wide each read is (requirements 4.11, 8.3).

Its own module because ``test_check_pipeline`` is at its own ``CountDeclFunction`` limit, and
because this is one question rather than a corner of another: **a check extracts each side
once**. It used to be twice -- once for the selected files, which is all the affected-set
resolver needs, and once for the affected files and their neighbourhood -- and the four
extractions together were 88% of a warm one-line check, measured.

One walk now records two dependency rings and the documents are narrowed in process, which
``tests/understand/test_narrow.py`` proves equal to the bounded extractions they replace.
"""

from __future__ import annotations

import test_check_pipeline
from test_check_pipeline import Harness

staged_harness = test_check_pipeline.staged_harness
"""The fixture this module drives, borrowed rather than rebuilt: it is the one the shape
of a run is already pinned against, and a second copy would drift from it."""


def test_a_staged_run_analyses_the_after_side_first_and_then_the_before_side(
    staged_harness: Harness,
) -> None:
    """Both databases are brought up to date, and each side is extracted **once** (req 8.3).

    It used to be twice per side: once for the selected files, which is all the affected-set
    resolver needs, and once for the affected files and their neighbourhood. The extractions
    were 88% of a warm run, measured, so one walk now records two rings and the documents are
    narrowed in process.
    """
    staged_harness.run()
    assert staged_harness.analyzed_sides == ["after", "before"]
    assert staged_harness.extractor.sides() == ["after", "before"]


def test_the_single_extraction_asks_about_the_change_and_widens_by_rings(
    staged_harness: Harness,
) -> None:
    """The request names the change; the ring count is what widens what is recorded."""
    staged_harness.run()
    assert staged_harness.extractor.requested("after", 0) == {
        "src/cli/app.py",
        "src/analysis/rules.py",
    }
    assert staged_harness.extractor.rings("after", 0) == 2
    assert staged_harness.extractor.rings("before", 0) == 2
