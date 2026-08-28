"""Understand adapter records: environment, analyze results, extraction requests (1.x, 5.5)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from scitools_hook.config.metric_names import ELEMENT_SCOPES, SCOPE_KINDS
from scitools_hook.models.snapshot import ParseError
from scitools_hook.models.understand import (
    AnalyzeResult,
    ExtractRequest,
    LicenseStatus,
    RawViolation,
    UnderstandEnv,
)


def _env() -> UnderstandEnv:
    return UnderstandEnv(
        home=Path("/home/dev/scitools"),
        und=Path("/home/dev/scitools/bin/linux64/und"),
        upython=Path("/home/dev/scitools/bin/linux64/upython"),
        python_api_dir=Path("/home/dev/scitools/bin/linux64/Python"),
        version="6.5.1204",
        source="env:SCITOOLS_HOME",
        api_mode="upython",
    )


def _request() -> ExtractRequest:
    return ExtractRequest(
        files={"src/cli/app.py", "src/analysis/engine.py"},
        kinds_by_scope=dict(SCOPE_KINDS),
        metrics_by_scope={
            "routine": ["CyclomaticStrict", "CountParams"],
            "class": ["CountDeclMethod"],
            "file": ["CountLineCode"],
        },
        synthetic=["CountParams"],
        population_metrics={"project": ["CyclomaticStrict"]},
        ignore={"routine": [r"^test_"], "file": [r"^tests/"]},
        architecture="Directory Structure",
        depth=2,
    )


# --- UnderstandEnv -------------------------------------------------------------


def test_understand_env_records_the_resolved_installation() -> None:
    env = _env()
    assert env.api_mode == "upython"
    assert env.source == "env:SCITOOLS_HOME"


def test_understand_env_allows_an_installation_without_upython() -> None:
    env = _env().model_copy(update={"upython": None, "api_mode": "inprocess"})
    assert env.upython is None


def test_understand_env_rejects_an_unknown_api_mode() -> None:
    payload = _env().model_dump() | {"api_mode": "auto"}
    with pytest.raises(ValidationError):
        UnderstandEnv.model_validate(payload)


def test_understand_env_round_trips_through_json() -> None:
    env = _env()
    assert UnderstandEnv.model_validate(json.loads(env.model_dump_json())) == env


# --- analyze results and license ----------------------------------------------


def test_analyze_result_defaults_to_a_clean_run() -> None:
    result = AnalyzeResult(seconds=1.5)
    assert result.parse_errors == []
    assert result.warnings == 0


def test_analyze_result_carries_parse_errors() -> None:
    result = AnalyzeResult(
        parse_errors=[ParseError(path=Path("src/a.py"), line=3, message="unexpected indent")],
        warnings=2,
        seconds=8.25,
    )
    assert result.parse_errors[0].message == "unexpected indent"


def test_license_status_distinguishes_ok_from_missing() -> None:
    assert LicenseStatus(ok=True).text == ""
    missing = LicenseStatus(ok=False, text="No Und License Found")
    assert (missing.ok, missing.text) == (False, "No Und License Found")


def test_raw_violation_keeps_the_codecheck_row() -> None:
    violation = RawViolation(
        check_id="PY_A001",
        check_name="Avoid bare except",
        path="src/cli/app.py",
        line=42,
        column=5,
        message="bare except",
        entity="app.main",
    )
    assert violation.check_id == "PY_A001"
    assert (
        RawViolation(
            check_id="PY_A001", check_name="Avoid bare except", path="a.py", line=1, message="x"
        ).column
        is None
    )


# --- ExtractRequest ------------------------------------------------------------


def test_extract_request_is_self_describing() -> None:
    request = _request()
    assert request.kinds_by_scope == dict(SCOPE_KINDS)
    assert request.metrics_by_scope["routine"] == ["CyclomaticStrict", "CountParams"]
    assert request.synthetic == ["CountParams"]
    assert request.population_metrics["project"] == ["CyclomaticStrict"]
    assert request.ignore["routine"] == [r"^test_"]
    assert (request.architecture, request.depth) == ("Directory Structure", 2)
    assert request.include_edges is True


def test_extract_request_kinds_cover_only_scopes_with_entities() -> None:
    assert set(_request().kinds_by_scope) == set(ELEMENT_SCOPES)


def test_extract_request_rejects_a_scope_that_has_no_entity_kind() -> None:
    payload = _request().model_dump() | {"kinds_by_scope": {"project": "project"}}
    with pytest.raises(ValidationError):
        ExtractRequest.model_validate(payload)


def test_extract_request_rejects_an_empty_kind_string() -> None:
    payload = _request().model_dump() | {"kinds_by_scope": {"file": "  "}}
    with pytest.raises(ValidationError):
        ExtractRequest.model_validate(payload)


def test_extract_request_requires_a_positive_depth() -> None:
    payload = _request().model_dump() | {"depth": 0}
    with pytest.raises(ValidationError):
        ExtractRequest.model_validate(payload)


def test_extract_request_round_trips_through_json() -> None:
    request = _request()
    assert ExtractRequest.model_validate(json.loads(request.model_dump_json())) == request


def test_extract_request_serialises_files_in_a_stable_order() -> None:
    wire = json.loads(_request().model_dump_json())
    assert wire["files"] == ["src/analysis/engine.py", "src/cli/app.py"]
