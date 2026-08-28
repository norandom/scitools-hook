"""Shared pure-data models used by every layer above config.

One vocabulary for the whole package: adapters produce these models, analysis consumes and
produces them, report and runner render them. The modules are grouped by subject —
snapshots, findings, change, understand records, git records, cache state, baseline and the
progress ports — and every public name is re-exported here.
"""

from __future__ import annotations

from scitools_hook.models.baseline import Baseline, BaselineIssue
from scitools_hook.models.cache import APP_NAME, CachePaths, SyncState, cache_root, repo_id
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
    STRUCTURE_RULES,
    EffectiveThreshold,
    Finding,
    FindingKind,
    HighestValue,
    LimitSource,
    ParsedRule,
    RunResult,
    StructureRuleName,
    TightenedLimit,
    build_rule_name,
    codecheck_rule,
    is_valid_rule_name,
    parse_rule_name,
    structure_rule,
)
from scitools_hook.models.git import (
    CommitTarget,
    IndexTarget,
    StagedChange,
    SyncDelta,
    SyncTarget,
    SyncTargetKind,
    WorktreeTarget,
)
from scitools_hook.models.progress import CommandLog, NullCommandLog, NullProgress, Progress
from scitools_hook.models.snapshot import (
    ArchNode,
    DataModel,
    DepEdge,
    EntityKey,
    EntityRecord,
    EntityRef,
    ParseError,
    ProjectSnapshot,
    Side,
)
from scitools_hook.models.understand import (
    AnalyzeResult,
    ExtractRequest,
    LicenseStatus,
    RawViolation,
    UnderstandEnv,
)

__all__ = [
    "APP_NAME",
    "STRUCTURE_RULES",
    "AffectedSet",
    "AnalyzeResult",
    "ArchNode",
    "Baseline",
    "BaselineIssue",
    "CachePaths",
    "ChangeSummary",
    "CommandLog",
    "CommitTarget",
    "DataModel",
    "DepEdge",
    "DependencyDelta",
    "EffectiveThreshold",
    "EntityDelta",
    "EntityKey",
    "EntityRecord",
    "EntityRef",
    "ExtractRequest",
    "Finding",
    "FindingKind",
    "GraphFile",
    "GraphTarget",
    "HighestValue",
    "ImpactSet",
    "IndexTarget",
    "LicenseStatus",
    "LimitSource",
    "NullCommandLog",
    "NullProgress",
    "ParseError",
    "ParsedRule",
    "Progress",
    "ProjectSnapshot",
    "RawViolation",
    "RunResult",
    "Side",
    "StagedChange",
    "StructureRuleName",
    "SyncDelta",
    "SyncState",
    "SyncTarget",
    "SyncTargetKind",
    "TightenedLimit",
    "UnderstandEnv",
    "WorktreeTarget",
    "build_rule_name",
    "cache_root",
    "codecheck_rule",
    "is_valid_rule_name",
    "parse_rule_name",
    "repo_id",
    "structure_rule",
]
