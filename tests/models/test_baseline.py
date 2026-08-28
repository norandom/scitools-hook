"""Baseline records: the stored file shape and the tolerant-parse issue record (8.1, 8.6)."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from scitools_hook.models.baseline import Baseline, BaselineIssue


def test_baseline_pins_version_one() -> None:
    baseline = Baseline(captured_at="2026-08-28T10:00:00+00:00")
    assert baseline.version == 1
    with pytest.raises(ValidationError):
        Baseline(version=2, captured_at="2026-08-28T10:00:00+00:00")  # type: ignore[arg-type]


def test_baseline_values_are_keyed_by_rule_name() -> None:
    baseline = Baseline(
        captured_at="2026-08-28T10:00:00+00:00",
        values={"routine.CyclomaticStrict": 9, "project.AVG:CyclomaticStrict": 3.4},
    )
    assert baseline.values["routine.CyclomaticStrict"] == 9.0
    assert baseline.values["project.AVG:CyclomaticStrict"] == 3.4


def test_baseline_values_default_to_empty() -> None:
    assert Baseline(captured_at="2026-08-28T10:00:00+00:00").values == {}


def test_baseline_matches_the_documented_file_shape() -> None:
    baseline = Baseline(
        captured_at="2026-08-28T10:00:00+00:00", values={"routine.CyclomaticStrict": 9}
    )
    wire = json.loads(baseline.model_dump_json())
    assert wire == {
        "version": 1,
        "captured_at": "2026-08-28T10:00:00+00:00",
        "values": {"routine.CyclomaticStrict": 9.0},
    }
    assert Baseline.model_validate(wire) == baseline


def test_baseline_issue_locates_the_offending_entry() -> None:
    issue = BaselineIssue(key="routine.Unknown", message="no such threshold in configuration")
    assert issue.key == "routine.Unknown"


def test_baseline_issue_key_is_optional_for_whole_file_problems() -> None:
    issue = BaselineIssue(message="baseline file is not valid JSON")
    assert issue.key is None
