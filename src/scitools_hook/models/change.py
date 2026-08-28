"""What a change did: affected entities, entity and dependency deltas, impact, graphs.

These models carry the ``explain`` output (req 9) and the affected set the gate evaluates
(req 4.2). Sets are written to the wire sorted, so two runs over the same change produce
byte-identical JSON; ``ChangeSummary.impact`` is keyed by :attr:`EntityKey.token` because
JSON object keys must be strings.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import Field, FieldSerializationInfo, field_serializer, field_validator

from scitools_hook.models.snapshot import DataModel, EntityKey, EntityRef


def _mode(info: FieldSerializationInfo) -> str:
    return "json" if info.mode_is_json() else "python"


class AffectedSet(DataModel):
    """What the staged change touched (req 4.2, 4.10).

    ``files`` are the staged, still existing paths plus files whose dependency set changed;
    ``neighbourhood`` adds their direct dependents and dependencies, which cycles and fan
    rules need; ``keys`` are the entities defined in ``files``.
    """

    files: set[str] = Field(default_factory=set)
    deleted_files: set[str] = Field(default_factory=set)
    keys: set[EntityKey] = Field(default_factory=set)
    neighbourhood: set[str] = Field(default_factory=set)

    @field_serializer("files", "deleted_files", "neighbourhood")
    def _dump_paths(self, paths: set[str]) -> list[str]:
        return sorted(paths)

    @field_serializer("keys")
    def _dump_keys(
        self, keys: set[EntityKey], info: FieldSerializationInfo
    ) -> list[dict[str, Any]]:
        return [key.model_dump(mode=_mode(info)) for key in sorted(keys, key=lambda k: k.token)]


class EntityDelta(DataModel):
    """One entity added, removed or modified by the change, with its metric movement (9.1)."""

    ref: EntityRef
    status: Literal["added", "removed", "modified"]
    before: dict[str, float] = Field(default_factory=dict)
    after: dict[str, float] = Field(default_factory=dict)
    delta: dict[str, float] = Field(default_factory=dict)
    arch_path: str | None = None
    """Architecture path of the entity's container file, so a reviewer can find it (req 9.7)."""


class DependencyDelta(DataModel):
    """One dependency the change added or removed, with its architecture nodes (req 9.2)."""

    src: str
    dst: str
    status: Literal["added", "removed"]
    src_node: str | None = None
    dst_node: str | None = None
    crosses_arch: bool = False


class ImpactSet(DataModel):
    """Entities that reference one entity transitively, by depth (req 9.5)."""

    by_depth: dict[int, list[EntityRef]] = Field(default_factory=dict)
    total: int = 0


class GraphTarget(DataModel):
    """One graph to export. The names are the ones Understand 6.5 actually renders."""

    key: EntityKey
    graph: Literal["Butterfly", "Depends On"]


class GraphFile(DataModel):
    """An exported SVG, referenced from the change summary (req 9.4)."""

    key: EntityKey
    graph: str
    path: Path


class ChangeSummary(DataModel):
    """The review-at-scale view of one change (req 9.1-9.8)."""

    files: dict[str, list[EntityDelta]] = Field(default_factory=dict)
    dependencies: list[DependencyDelta] = Field(default_factory=list)
    top_by_delta: list[EntityDelta] = Field(default_factory=list)
    top_by_value: list[EntityDelta] = Field(default_factory=list)
    impact: dict[EntityKey, ImpactSet] = Field(default_factory=dict)
    graphs: list[GraphFile] = Field(default_factory=list)
    db_path: str
    open_command: str

    @field_validator("impact", mode="before")
    @classmethod
    def _accept_token_keys(cls, value: object) -> object:
        if isinstance(value, dict):
            return {
                (EntityKey.from_token(key) if isinstance(key, str) else key): impact
                for key, impact in value.items()
            }
        return value

    @field_serializer("impact")
    def _dump_impact(
        self, impact: dict[EntityKey, ImpactSet], info: FieldSerializationInfo
    ) -> dict[str, Any]:
        ordered = sorted(impact.items(), key=lambda pair: pair[0].token)
        return {key.token: value.model_dump(mode=_mode(info)) for key, value in ordered}
