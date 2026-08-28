"""Mapping raw CodeCheck rows onto findings (task 4.7; req 6.9, 3.7, 7.1).

Every case builds ``RawViolation`` rows by hand: the mapper is pure, it never runs ``und``,
and the rows are exactly what the CodeCheck runner of task 6.7 will hand it. The repository
root of the fixture is ``/home/dev/repo`` because ``und codecheck`` reports absolute paths
and requirement 7.1 wants the path relative to the repository root.
"""

from __future__ import annotations

import json

import pytest

from scitools_hook.analysis.codecheck import map_violations
from scitools_hook.config.models import Severity
from scitools_hook.models.findings import Finding
from scitools_hook.models.understand import RawViolation

REPO = "/home/dev/repo"
"""Repository root of the fixture; the paths below sit under it, bar the library stub."""

LIBRARY_STUB = "/opt/scitools/conf/understand/python/python3/builtins.py"
"""A file Understand pulled in from its own installation: outside the repository root."""

FIXTURE = [
    RawViolation(
        check_id="CPP_F016",
        check_name="Function is too long",
        path=f"{REPO}/src/app.py",
        line=42,
        column=3,
        message="Function 'run' is 120 lines long",
        entity="app.run",
    ),
    RawViolation(
        check_id="PY_A001",
        check_name="Unused import",
        path=f"{REPO}/src/util.py",
        line=3,
        column=1,
        message="Import 'os' is unused",
        entity="util.os",
    ),
    RawViolation(
        check_id="PY_A001",
        check_name="Unused import",
        path=f"{REPO}/src/app.py",
        line=7,
        column=1,
        message="Import 'sys' is unused",
        entity="app.sys",
    ),
]
"""Three violations in two files, deliberately not given in their reported order."""


def paths(findings: list[Finding]) -> list[str]:
    """The file paths a batch of findings carries, in the order they came back."""
    return [finding.path for finding in findings]


def test_a_fixture_of_violations_maps_to_findings_with_severity_and_relative_paths() -> None:
    """The done criterion of task 4.7: configured severity, repo-relative paths (6.9, 7.1)."""
    findings = map_violations(FIXTURE, "warning", REPO)

    assert paths(findings) == ["src/app.py", "src/app.py", "src/util.py"]
    assert [finding.line for finding in findings] == [7, 42, 3]
    assert [finding.severity for finding in findings] == ["warning"] * 3
    assert [finding.kind for finding in findings] == ["codecheck"] * 3
    assert [finding.message for finding in findings] == [
        "Import 'sys' is unused",
        "Function 'run' is 120 lines long",
        "Import 'os' is unused",
    ]


def test_the_rule_name_is_codecheck_dot_check_id() -> None:
    """The rule follows the ``codecheck.<check_id>`` grammar, not the check's display name."""
    findings = map_violations(FIXTURE, "warning", REPO)

    assert [finding.rule for finding in findings] == [
        "codecheck.PY_A001",
        "codecheck.CPP_F016",
        "codecheck.PY_A001",
    ]


def test_a_finding_carries_the_non_metric_fields_of_the_contract() -> None:
    """A CodeCheck violation is not a metric: no value, no limit, and the hint is left empty."""
    (finding,) = map_violations(FIXTURE[:1], "warning", REPO)

    assert finding.scope == "file"
    assert finding.metric is None
    assert finding.entity is None
    assert finding.value is None
    assert finding.before is None
    assert finding.limit is None
    assert finding.limit_source == "rule"
    assert finding.preexisting is False
    assert finding.hint == ""


def test_the_entity_name_check_name_and_column_travel_in_details() -> None:
    """``RawViolation.entity`` is a name, not an ``EntityRef``, so it is carried in details."""
    (finding,) = map_violations(FIXTURE[:1], "warning", REPO)

    assert finding.details == {
        "check_name": "Function is too long",
        "column": 3,
        "entity": "app.run",
    }


@pytest.mark.parametrize(("severity", "blocking"), [("error", True), ("warning", False)])
def test_the_configured_severity_decides_severity_and_blocking(
    severity: Severity, blocking: bool
) -> None:
    """Only an ``error`` blocks the commit (req 3.7, 7.9)."""
    findings = map_violations(FIXTURE, severity, REPO)

    assert [finding.severity for finding in findings] == [severity] * 3
    assert [finding.blocking for finding in findings] == [blocking] * 3


def test_an_absolute_path_under_the_repository_root_becomes_relative() -> None:
    """``und codecheck`` reports absolute paths; requirement 7.1 wants them repo-relative."""
    violation = FIXTURE[1].model_copy(update={"path": f"{REPO}/src/nested/deep/util.py"})

    (finding,) = map_violations([violation], "warning", REPO)

    assert finding.path == "src/nested/deep/util.py"


def test_a_windows_path_is_normalised_before_the_root_is_subtracted() -> None:
    """Understand emits native separators, so a backslash must count as one (req 7.1).

    Without the normalisation ``C:\\repo\\src\\app.py`` is a single path segment, the root
    cannot be subtracted, and every finding on Windows would carry an absolute backslash
    path -- which design.md's SARIF contract (repo-relative, forward slashes) cannot hold.
    """
    row = RawViolation(
        check_id="CPP_F016",
        check_name="Function is too long",
        path=r"C:\repo\src\app.py",
        line=42,
        column=3,
        message="Function is too long",
        entity=None,
    )

    (finding,) = map_violations([row], "warning", r"C:\repo")

    assert finding.path == "src/app.py"


def test_a_posix_name_containing_a_backslash_is_split_by_that_normalisation() -> None:
    """The accepted cost of treating a backslash as a separator, recorded on purpose.

    A POSIX file name may legally contain a backslash; such a name is split into
    directories. The trade is deliberate: the failure is a misleading display path for a
    pathological name, whereas dropping the normalisation breaks every path on Windows.
    Nothing in analysis or report opens ``Finding.path``, so no read can fail because of it.
    """
    row = RawViolation(
        check_id="PY_A001",
        check_name="Unused import",
        path=f"{REPO}/src/we\\ird.py",
        line=1,
        column=1,
        message="Import is unused",
        entity=None,
    )

    (finding,) = map_violations([row], "warning", REPO)

    assert finding.path == "src/we/ird.py"


def test_an_empty_path_stays_empty_rather_than_pointing_at_the_repository_root() -> None:
    """An empty path is passed through untouched (it must not normalise to ``.``).

    ``PurePosixPath("")`` is ``.``, so without the guard a row with no path would claim to
    be a finding about the repository root itself.
    """
    row = RawViolation(
        check_id="PY_A001",
        check_name="Unused import",
        path="",
        line=0,
        column=None,
        message="Import is unused",
        entity=None,
    )

    (finding,) = map_violations([row], "warning", REPO)

    assert finding.path == ""


def test_a_file_level_row_sorts_before_a_positioned_row_at_the_same_line() -> None:
    """A row without a column is the file-level one, so it comes first (req 6.9)."""
    positioned = RawViolation(
        check_id="PY_A001",
        check_name="Unused import",
        path=f"{REPO}/src/app.py",
        line=7,
        column=4,
        message="Import is unused",
        entity=None,
    )
    file_level = RawViolation(
        check_id="PY_A001",
        check_name="Unused import",
        path=f"{REPO}/src/app.py",
        line=7,
        column=None,
        message="File-level note",
        entity=None,
    )

    findings = map_violations([positioned, file_level], "warning", REPO)

    assert [finding.message for finding in findings] == ["File-level note", "Import is unused"]


def test_a_path_outside_the_repository_root_stays_absolute() -> None:
    """A library file Understand pulled in has no repo-relative form, so it keeps its own."""
    violation = FIXTURE[1].model_copy(update={"path": LIBRARY_STUB})

    (finding,) = map_violations([violation], "warning", REPO)

    assert finding.path == LIBRARY_STUB


def test_a_relative_path_passes_through_and_no_root_leaves_a_path_alone() -> None:
    """A row that is already repo-relative is untouched, with or without a root."""
    relative = FIXTURE[1].model_copy(update={"path": "src/util.py"})

    assert paths(map_violations([relative], "warning", REPO)) == ["src/util.py"]
    assert paths(map_violations([relative], "warning")) == ["src/util.py"]
    assert paths(map_violations(FIXTURE[:1], "warning")) == [f"{REPO}/src/app.py"]


def test_a_violation_without_line_column_or_entity_maps_without_crashing() -> None:
    """CodeCheck reports file-level violations with line 0 and no column or entity."""
    violation = RawViolation(
        check_id="PY_S001",
        check_name="File has no module docstring",
        path=f"{REPO}/src/app.py",
        line=0,
        message="File 'app.py' has no module docstring",
    )

    (finding,) = map_violations([violation], "error", REPO)

    assert finding.line is None
    assert finding.path == "src/app.py"
    assert finding.details == {"check_name": "File has no module docstring"}


def test_the_order_is_deterministic_whatever_order_the_rows_arrive_in() -> None:
    """Same rows, any order in, one order out: path, then line, then column, then check id."""
    same_line = [
        RawViolation(
            check_id=check_id,
            check_name="Same place",
            path=f"{REPO}/src/app.py",
            line=9,
            column=column,
            message=f"{check_id} at column {column}",
        )
        for check_id, column in (("Z_LAST", 2), ("A_FIRST", 2), ("M_MID", 1))
    ]
    rows = [*FIXTURE, *same_line]

    findings = map_violations(rows, "warning", REPO)

    assert findings == map_violations(list(reversed(rows)), "warning", REPO)
    assert [finding.rule for finding in findings] == [
        "codecheck.PY_A001",
        "codecheck.M_MID",
        "codecheck.A_FIRST",
        "codecheck.Z_LAST",
        "codecheck.CPP_F016",
        "codecheck.PY_A001",
    ]


def test_details_survive_a_json_round_trip() -> None:
    """The findings are part of the JSON output contract (req 7.4), details included."""
    (finding,) = map_violations(FIXTURE[:1], "error", REPO)

    restored = Finding.model_validate(json.loads(finding.model_dump_json()))

    assert restored == finding
    assert restored.details["entity"] == "app.run"


def test_no_violations_yield_no_findings() -> None:
    """An empty CodeCheck run is not an error; it simply has nothing to report."""
    assert map_violations([], "error", REPO) == []
