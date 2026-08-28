"""Snapshot vocabulary: entity identity, records, dependency edges, architecture nodes.

An entity is identified by ``(scope, path, longname, parameters)``. A live experiment
(research.md, "Entity identity across two databases") confirmed that ``relname``,
``longname`` and ``parameters`` are identical in the before and after databases while
``uniquename`` embeds the analysis root and is therefore unusable across the two shadows.
Architecture membership comes from the container file, because ``Db.archs()`` is empty for
routines and classes.

``ProjectSnapshot.entities`` is a mapping keyed by :class:`EntityKey` because every rule
joins the two sides by that key. JSON object keys must be strings, so the mapping is
written to the wire as a **list of records**, the key being ``record.ref.key``; validation
rebuilds the mapping and checks that both agree. Where a key must be an object key after
all (``ChangeSummary.impact``), :attr:`EntityKey.token` provides a reversible string form.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FieldSerializationInfo,
    field_serializer,
    field_validator,
    model_validator,
)

from scitools_hook.config.metric_names import Scope

Side = Literal["before", "after"]
"""Which side of the change a snapshot describes."""


class DataModel(BaseModel):
    """Base of every model in this layer: unknown keys are a validation error.

    Snapshots and worker results cross a process boundary as JSON; forbidding extras turns
    a drifting contract into a validation failure instead of silently dropped data.
    """

    model_config = ConfigDict(extra="forbid")


class EntityKey(DataModel):
    """Identity of one entity, stable across the before and after databases.

    ``path`` is repo-relative with forward slashes, ``longname`` is Understand's qualified
    name and ``parameters`` distinguishes overloads (``None`` for entities that have none).
    Frozen, so it can key dictionaries and sets.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: Scope
    path: str
    longname: str
    parameters: str | None = None

    @property
    def token(self) -> str:
        """Reversible string form, for use wherever a key has to be a string."""
        return json.dumps(
            [self.scope, self.path, self.longname, self.parameters], separators=(",", ":")
        )

    @classmethod
    def from_token(cls, token: str) -> Self:
        """Rebuild a key from :attr:`token`; raise ``ValueError`` for anything else."""
        try:
            parts = json.loads(token)
        except json.JSONDecodeError as err:
            raise ValueError(f"not an entity key token: {token!r}") from err
        if not isinstance(parts, list) or len(parts) != 4:
            raise ValueError(f"an entity key token has four elements, got {token!r}")
        scope, path, longname, parameters = parts
        return cls(scope=scope, path=path, longname=longname, parameters=parameters)


class EntityRef(DataModel):
    """An entity as it is shown to a user: its key plus Understand's kind, name and line."""

    key: EntityKey
    kind: str
    name: str
    line: int | None = None


class EntityRecord(DataModel):
    """One entity with everything the rules need: metrics, architectures, newness."""

    ref: EntityRef
    language: str
    metrics: dict[str, float] = Field(default_factory=dict)
    archs: list[str] = Field(default_factory=list)
    is_new: bool = False

    @property
    def key(self) -> EntityKey:
        """The identity of the recorded entity."""
        return self.ref.key


class DepEdge(DataModel):
    """A dependency with its reference count.

    ``src`` and ``dst`` are file paths, architecture node paths, or :attr:`EntityKey.token`
    values for class edges, depending on which edge list holds the edge.
    """

    src: str
    dst: str
    refs: int = Field(ge=0)
    crosses_arch: bool = False


class ArchNode(DataModel):
    """A node of an Understand architecture at the configured depth, plus its member files."""

    path: str
    members: list[str] = Field(default_factory=list)

    @property
    def depth(self) -> int:
        """Number of path components below the architecture root."""
        return self.path.count("/")


class ParseError(DataModel):
    """One parse error reported by ``und analyze`` (req 2.6)."""

    path: Path
    line: int | None = None
    message: str


class ProjectSnapshot(DataModel):
    """Immutable view of one database side; every rule works on these, never on live objects."""

    side: Side
    languages: list[str] = Field(default_factory=list)
    entities: dict[EntityKey, EntityRecord] = Field(default_factory=dict)
    file_edges: list[DepEdge] = Field(default_factory=list)
    class_edges: list[DepEdge] = Field(default_factory=list)
    arch_nodes: list[ArchNode] = Field(default_factory=list)
    arch_edges: list[DepEdge] = Field(default_factory=list)
    populations: dict[Scope, dict[str, list[float]]] = Field(default_factory=dict)
    unavailable: dict[str, list[str]] = Field(default_factory=dict)
    parse_errors: list[ParseError] = Field(default_factory=list)

    @field_validator("entities", mode="before")
    @classmethod
    def _accept_the_record_list_wire_form(cls, value: object) -> object:
        if isinstance(value, list):
            records = [EntityRecord.model_validate(item) for item in value]
            return {record.ref.key: record for record in records}
        return value

    @model_validator(mode="after")
    def _keys_match_their_records(self) -> Self:
        for key, record in self.entities.items():
            if record.ref.key != key:
                raise ValueError(
                    f"entity is stored under {key.token} but identifies as {record.ref.key.token}"
                )
        return self

    @field_serializer("entities")
    def _dump_entities(
        self, entities: dict[EntityKey, EntityRecord], info: FieldSerializationInfo
    ) -> list[dict[str, Any]]:
        mode = "json" if info.mode_is_json() else "python"
        ordered = sorted(entities.values(), key=lambda record: record.ref.key.token)
        return [record.model_dump(mode=mode) for record in ordered]
