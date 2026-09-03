"""Shared pure-data models used by every layer above config.

One vocabulary for the whole package: adapters produce these models, analysis consumes and
produces them, report and runner render them. The modules are grouped by subject —
snapshots, findings, change, understand records, git records, cache state, baseline, the
progress ports and the shadow port — and every public name is re-exported here.

Two of those modules hold ``Protocol``s rather than data (``progress``, ``ports``). They are
here because a port has to sit *below* both the component that calls it and the component
that satisfies it, and ``models`` is the only layer below both adapters.
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
    ANALYSIS_RULES,
    PARSE_ERROR_RULE,
    STRUCTURE_RULES,
    AnalysisRuleName,
    EffectiveThreshold,
    Finding,
    FindingKind,
    HighestValue,
    LimitSource,
    ParsedRule,
    RunResult,
    StructureRuleName,
    TightenedLimit,
    analysis_rule,
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
from scitools_hook.models.ports import RepositoryRoot, ShadowPort
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
    "ANALYSIS_RULES",
    "APP_NAME",
    "PARSE_ERROR_RULE",
    "STRUCTURE_RULES",
    "AffectedSet",
    "AnalysisRuleName",
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
    "RepositoryRoot",
    "RunResult",
    "ShadowPort",
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
    "analysis_rule",
    "build_rule_name",
    "cache_root",
    "codecheck_rule",
    "is_valid_rule_name",
    "parse_rule_name",
    "repo_id",
    "structure_rule",
]
