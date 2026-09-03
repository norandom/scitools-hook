"""Every model round-trips through JSON losslessly and is re-exported from ``models`` (3.1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fixtures import snapshot_fixture
from pydantic import BaseModel

from scitools_hook import models
from scitools_hook.config.models import Limit, ThresholdSpec
from scitools_hook.models.baseline import Baseline, BaselineIssue
from scitools_hook.models.cache import CachePaths, SyncState
from scitools_hook.models.change import (
    AffectedSet,
    ChangeSummary,
    DependencyDelta,
    EntityDelta,
    GraphFile,
    GraphTarget,
    ImpactSet,
)
from scitools_hook.models.findings import (
    EffectiveThreshold,
    Finding,
    HighestValue,
    RunResult,
    TightenedLimit,
)
from scitools_hook.models.git import (
    CommitTarget,
    IndexTarget,
    StagedChange,
    SyncDelta,
    WorktreeTarget,
)
from scitools_hook.models.snapshot import (
    ArchNode,
    DepEdge,
    EntityKey,
    EntityRecord,
    EntityRef,
    ParseError,
)
from scitools_hook.models.understand import (
    AnalyzeResult,
    ExtractRequest,
    LicenseStatus,
    RawViolation,
    UnderstandEnv,
)

KEY = EntityKey(scope="routine", path="src/cli/app.py", longname="app.build_parser", parameters="")
REF = EntityRef(key=KEY, kind="Python Function", name="build_parser", line=34)
RECORD = EntityRecord(
    ref=REF,
    language="Python",
    metrics={"CyclomaticStrict": 12.0},
    archs=["Directory Structure/src/cli"],
    is_new=True,
)
SPEC = ThresholdSpec(scope="routine", metric="CyclomaticStrict", limit=Limit(max=10))
FINDING = Finding(
    kind="threshold",
    rule="routine.CyclomaticStrict",
    metric="CyclomaticStrict",
    scope="routine",
    entity=REF,
    path="src/cli/app.py",
    line=34,
    value=12.0,
    before=6.0,
    limit=10.0,
    severity="error",
    blocking=True,
    message="CyclomaticStrict 12 exceeds the limit of 10",
    hint="extract the inner block into a routine",
    details={"overshoot": 2.0},
)
STRUCTURAL_FINDING = Finding(
    kind="structural",
    rule="structure.file_cycle",
    scope="file",
    path="src/analysis/engine.py",
    severity="error",
    message="new dependency cycle between 2 files",
    details={
        "members": ["src/analysis/engine.py", "src/analysis/rules.py"],
        "closing_refs": [{"src": "src/analysis/rules.py", "dst": "src/analysis/engine.py"}],
    },
)
DELTA = EntityDelta(
    ref=REF,
    status="modified",
    before={"CyclomaticStrict": 6.0},
    after={"CyclomaticStrict": 12.0},
    delta={"CyclomaticStrict": 6.0},
)

SAMPLES: list[BaseModel] = [
    KEY,
    REF,
    RECORD,
    DepEdge(src="src/cli/app.py", dst="src/util/text.py", refs=2, crosses_arch=True),
    ArchNode(path="Directory Structure/src/cli", members=["src/cli/app.py"]),
    ParseError(path=Path("src/analysis/rules.py"), line=41, message="unexpected token"),
    snapshot_fixture("after"),
    FINDING,
    STRUCTURAL_FINDING,
    EffectiveThreshold(spec=SPEC, metric=SPEC.ref, limit=Limit(max=8), source="baseline"),
    TightenedLimit(rule="routine.CyclomaticStrict", previous=10.0, current=8.0),
    HighestValue(scope="routine", metric="CyclomaticStrict", value=12.0, entity=REF),
    RunResult(
        tool_version="0.1.0",
        understand_version="6.5.1204",
        repo_root="/home/dev/project",
        selection="staged",
        started_at="2026-08-28T10:00:00+00:00",
        seconds=4.5,
        effective_thresholds=[SPEC],
        findings=[FINDING],
        ignored_counts={"routine": 2},
        unavailable_metrics={"Python": ["PercentLackOfCohesion"]},
        parse_errors=[ParseError(path=Path("src/analysis/rules.py"), message="unexpected token")],
        tightened=[TightenedLimit(rule="routine.CyclomaticStrict", previous=10.0, current=8.0)],
        highest=[HighestValue(scope="routine", metric="CyclomaticStrict", value=12.0)],
        analyzed_files=5,
        blocking_count=1,
        warning_count=0,
        preexisting_count=0,
    ),
    AffectedSet(
        files={"src/cli/app.py"},
        deleted_files={"src/legacy.py"},
        keys={KEY},
        neighbourhood={"src/util/text.py"},
    ),
    DELTA,
    DependencyDelta(
        src="src/cli/app.py",
        dst="src/understand/adapter.py",
        status="added",
        src_node="Directory Structure/src/cli",
        dst_node="Directory Structure/src/understand",
        crosses_arch=True,
    ),
    ImpactSet(by_depth={1: [REF]}, total=1),
    GraphTarget(key=KEY, graph="Butterfly"),
    GraphFile(key=KEY, graph="Butterfly", path=Path("review/build_parser.svg")),
    ChangeSummary(
        files={"src/cli/app.py": [DELTA]},
        dependencies=[DependencyDelta(src="a.py", dst="b.py", status="added")],
        top_by_delta=[DELTA],
        top_by_value=[DELTA],
        impact={KEY: ImpactSet(by_depth={1: [REF]}, total=1)},
        graphs=[GraphFile(key=KEY, graph="Butterfly", path=Path("review/build_parser.svg"))],
        db_path="/home/dev/.cache/scitools-hook/abc/after.und",
        open_command="und -db /home/dev/.cache/scitools-hook/abc/after.und",
    ),
    UnderstandEnv(
        home=Path("/home/dev/scitools"),
        und=Path("/home/dev/scitools/bin/linux64/und"),
        upython=Path("/home/dev/scitools/bin/linux64/upython"),
        python_api_dir=Path("/home/dev/scitools/bin/linux64/Python"),
        version="6.5.1204",
        source="path",
        api_mode="upython",
    ),
    AnalyzeResult(
        parse_errors=[ParseError(path=Path("a.py"), line=3, message="unexpected indent")],
        warnings=2,
        seconds=8.25,
    ),
    LicenseStatus(ok=False, text="No Und License Found"),
    RawViolation(
        check_id="PY_A001",
        check_name="Avoid bare except",
        path="src/cli/app.py",
        line=42,
        column=5,
        message="bare except",
        entity="app.main",
    ),
    ExtractRequest(
        files={"src/cli/app.py"},
        kinds_by_scope={"routine": "function ~unknown ~unresolved"},
        metrics_by_scope={"routine": ["CyclomaticStrict"]},
        synthetic=["CountParams"],
        population_metrics={"project": ["CyclomaticStrict"]},
        ignore={"routine": ["^test_"]},
        architecture="Directory Structure",
        depth=2,
    ),
    StagedChange(status="R", path="src/cli/app.py", old_path="src/app.py"),
    IndexTarget(),
    WorktreeTarget(),
    CommitTarget(commit="0f1e2d3"),
    SyncDelta(added=["a.py"], modified=["b.py"], deleted=["c.py"], full=True),
    CachePaths(
        root=Path("/cache/abc"),
        before_tree=Path("/cache/abc/before"),
        after_tree=Path("/cache/abc/after"),
        before_db=Path("/cache/abc/before.und"),
        after_db=Path("/cache/abc/after.und"),
        state=Path("/cache/abc/state.json"),
        graphs=Path("/cache/abc/graphs"),
    ),
    SyncState(
        after_target="index",
        after_tree_id="4b825dc",
        before_commit="0f1e2d3",
        languages=["Python"],
        created_with="6.5.1204",
    ),
    Baseline(captured_at="2026-08-28T10:00:00+00:00", values={"routine.CyclomaticStrict": 9.0}),
    BaselineIssue(key="routine.Unknown", message="no such threshold in configuration"),
]

EXPORTED: dict[str, tuple[str, ...]] = {
    "scitools_hook.models.snapshot": (
        "DataModel",
        "Side",
        "EntityKey",
        "EntityRef",
        "EntityRecord",
        "DepEdge",
        "ArchNode",
        "ParseError",
        "ProjectSnapshot",
    ),
    "scitools_hook.models.findings": (
        "FindingKind",
        "LimitSource",
        "StructureRuleName",
        "STRUCTURE_RULES",
        "AnalysisRuleName",
        "ANALYSIS_RULES",
        "PARSE_ERROR_RULE",
        "ParsedRule",
        "build_rule_name",
        "structure_rule",
        "analysis_rule",
        "codecheck_rule",
        "parse_rule_name",
        "is_valid_rule_name",
        "Finding",
        "EffectiveThreshold",
        "TightenedLimit",
        "HighestValue",
        "RunResult",
    ),
    "scitools_hook.models.change": (
        "AffectedSet",
        "EntityDelta",
        "DependencyDelta",
        "ImpactSet",
        "GraphTarget",
        "GraphFile",
        "ChangeSummary",
    ),
    "scitools_hook.models.understand": (
        "UnderstandEnv",
        "AnalyzeResult",
        "LicenseStatus",
        "RawViolation",
        "ExtractRequest",
    ),
    "scitools_hook.models.git": (
        "StagedChange",
        "IndexTarget",
        "WorktreeTarget",
        "CommitTarget",
        "SyncTarget",
        "SyncTargetKind",
        "SyncDelta",
    ),
    "scitools_hook.models.cache": ("APP_NAME", "repo_id", "cache_root", "CachePaths", "SyncState"),
    "scitools_hook.models.baseline": ("Baseline", "BaselineIssue"),
    "scitools_hook.models.progress": (
        "Progress",
        "CommandLog",
        "NullProgress",
        "NullCommandLog",
    ),
    # Protocols, not models: they carry no data and so have no round trip. They are listed
    # because this table is also what `test_models_package_re_exports_every_public_name`
    # compares `models.__all__` against, and a port that is not re-exported would make the
    # package docstring's "every public name is re-exported here" quietly false.
    "scitools_hook.models.ports": ("ShadowPort", "RepositoryRoot"),
}


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda sample: type(sample).__name__)
def test_model_round_trips_through_json(sample: BaseModel) -> None:
    wire = sample.model_dump_json(warnings="error")
    assert type(sample).model_validate(json.loads(wire)) == sample


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda sample: type(sample).__name__)
def test_model_round_trips_through_a_python_dump(sample: BaseModel) -> None:
    assert type(sample).model_validate(sample.model_dump(warnings="error")) == sample


def test_every_model_is_covered_by_the_round_trip_samples() -> None:
    covered = {type(sample).__name__ for sample in SAMPLES}
    declared = {
        name
        for names in EXPORTED.values()
        for name in names
        if name != "DataModel"
        and isinstance(getattr(models, name, None), type)
        and issubclass(getattr(models, name), BaseModel)
    }
    assert declared <= covered


def test_models_package_re_exports_every_public_name() -> None:
    expected = {name for names in EXPORTED.values() for name in names}
    assert set(models.__all__) == expected


def test_re_exported_names_are_the_submodule_objects() -> None:
    import importlib

    for module_name, names in EXPORTED.items():
        module = importlib.import_module(module_name)
        for name in names:
            assert getattr(models, name) is getattr(module, name)
