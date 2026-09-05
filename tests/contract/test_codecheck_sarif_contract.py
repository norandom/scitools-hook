"""A real CodeCheck inspection, read from the SARIF 8.0 writes (requirements 2.3, 2.6).

**This contract is open, and the test says so on every run.** The licence on the measuring
machine excludes CodeCheck: ``und codecheck`` refuses with *No checks in this configuration
are licensed to run*, writes no report, and nothing about the 8.0 document has been measured
here. Everything in ``understand/codecheck_sarif.py`` therefore stands on the published SARIF
2.1.0 schema and on the shape ``und analyze -sarif`` writes -- which *is* measured, by
``test_analysis_sarif_contract`` -- rather than on an inspection anyone has seen.

An **expected failure** rather than a skip, and rather than nothing at all. A skip reads as
"nothing to check here"; this is the opposite. The first run on a licensed machine turns the
whole mapping from a document into a measurement in one go, and an ``XPASS`` in the report is
the signal that it did.

The reason is named at the point of failure, quoting ``und`` itself, so the operator reading
the report learns which of the two things to do: get a CodeCheck licence, or read
``requirements.md`` 2.6 and accept that the mapping is specified.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from contract_project import build_database, real_env, write_tree

from scitools_hook.errors import AnalysisFailedError, LicenseError
from scitools_hook.models.progress import NullCommandLog
from scitools_hook.understand.codecheck_sarif import (
    RESULTS_SARIF,
    find_results,
    read_sarif_violations,
)
from scitools_hook.understand.und_cli import UndCli

pytestmark = pytest.mark.contract

CONFIGURATION = "Hersteller Initiative Software (HIS) Metrics"
"""A configuration 8.0 ships under Published Standards; the licence is what is missing."""

COMPLEX = {
    "pkg/deep.py": (
        "def deep(a, b, c, d):\n"
        "    total = 0\n"
        "    for one in range(a):\n"
        "        for two in range(b):\n"
        "            for three in range(c):\n"
        "                if one and two and three and d:\n"
        "                    total += one * two * three\n"
        "                elif one or two:\n"
        "                    total -= one\n"
        "                else:\n"
        "                    total += 1\n"
        "    return total\n"
    )
}
"""Nested and branchy on purpose: a configuration about metrics has something to report."""


def inspected(tmp_path: Path) -> Path:
    """Run CodeCheck for real over one deliberately complex file, and return its output dir.

    Calls :func:`pytest.xfail` -- naming what ``und`` said -- when this licence excludes
    CodeCheck, which is the case on the machine this was written on.
    """
    root = write_tree(tmp_path / "tree", COMPLEX)
    db = tmp_path / "inspection.und"
    build_database(db, root, ("python",))
    out_dir = tmp_path / "out"
    cli = UndCli(real_env("upython"), NullCommandLog())
    try:
        cli.codecheck(db, CONFIGURATION, [root / "pkg" / "deep.py"], out_dir)
    except LicenseError as unlicensed:
        said = " ".join(unlicensed.und_output.split()) or str(unlicensed)
        pytest.xfail(f"this Understand licence excludes CodeCheck: und said {said!r}")
    except AnalysisFailedError as refused:
        pytest.xfail(f"und codecheck wrote no report: {refused}")
    return out_dir


def test_contract_a_real_inspection_writes_the_sarif_this_reader_looks_for(
    tmp_path: Path,
) -> None:
    """The one measured fact the 8.0 reader stands on: the report's name and its place."""
    out_dir = inspected(tmp_path)

    found = find_results(out_dir)
    written = sorted(path.name for path in out_dir.iterdir())
    assert found is not None, f"und wrote {written} but not {RESULTS_SARIF}"


def test_contract_a_real_inspections_results_read_as_violations(tmp_path: Path) -> None:
    """The whole mapping against a document Understand wrote rather than one this suite did.

    A violation that arrives without a rule id or without a file raises here rather than
    passing on a record the finding mapper cannot use -- so a document whose shape differs
    from the specified one fails loudly, which is the point of the contract.

    The reader is called directly rather than through
    ``understand.codecheck.read_report``: which of the two readers runs is a pure function of
    the report's name and is settled by unit tests, and the question here is only whether the
    document Understand actually writes is one this mapping can read.
    """
    out_dir = inspected(tmp_path)
    found = find_results(out_dir)
    assert found is not None

    violations = read_sarif_violations(found)

    for violation in violations:
        assert violation.check_id, violation
        assert violation.path, violation
        assert violation.check_name, violation
        assert violation.line >= 0, violation
