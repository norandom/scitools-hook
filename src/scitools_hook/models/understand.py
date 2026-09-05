"""Records the Understand adapter produces and consumes (req 1.x, 2.6, 5.5, 6.9).

``ExtractRequest`` is deliberately self-describing: the worker runs under Understand's own
``upython`` and must never import ``scitools_hook``, so everything it needs — which files,
which kind string per scope, which metrics, which synthetic metrics, which populations,
which ignore patterns, which architecture and depth — travels in the request. Build
``kinds_by_scope`` from ``config.metric_names.SCOPE_KINDS`` (never by iterating ``SCOPES``:
``project`` and ``arch`` have no entity kind, and the worker must never call ``db.ents("")``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, field_serializer, field_validator

from scitools_hook.config.metric_names import SCOPE_KINDS, Scope
from scitools_hook.models.snapshot import DataModel, ParseError


class UnderstandEnv(DataModel):
    """A verified Understand installation and how its Python API will be reached (req 1.1)."""

    home: Path
    und: Path
    upython: Path | None = None
    python_api_dir: Path
    version: str
    source: str
    api_mode: Literal["inprocess", "upython"]


class AnalyzeResult(DataModel):
    """Outcome of one ``und analyze`` run (req 2.6)."""

    parse_errors: list[ParseError] = Field(default_factory=list)
    warnings: int = 0
    seconds: float


class LicenseStatus(DataModel):
    """What ``und -isundlicensed`` said; ``text`` quotes ``und`` when not ok (req 1.4).

    Deliberately no more than that. A licence is a set of options and ``ok`` alone has been
    wrong in the way that matters -- on 2026-09-05 a re-activated licence carried GUI and
    command-line access and not the API, so ``1`` here and ``NoApiLicense`` on every metric
    read -- but the command that lists the options is one of the licence commands that
    rewrote the licence file that morning, so the tool does not run it. The missing option
    is caught where it bites: ``doctor``'s analysis probe opens its scratch database through
    the API, and a check exits 4 at the first metric read.
    """

    ok: bool
    text: str = ""


class AnalysisProbe(DataModel):
    """Whether ``und`` can analyse anything at all right now, and what it said when it cannot.

    The API probes ask whether the ``understand`` module loads; the licence probe asks
    ``und -isundlicensed``. Neither asks the question the gate actually depends on, and
    8.0.1262 showed the gap the morning the install was replaced: ``-isundlicensed`` had
    fallen through to ``und license``, whose new output carries no error line, both API
    probes answered ``ok (8.0.1262)``, ``doctor`` printed ``license: ok`` and ``Problems:
    none`` -- and every ``und analyze`` on the machine was failing with "No Server
    Response". A session driving the gate spent its morning on that before asking for a
    probe that runs what the hook runs. This is that probe: a one-file project, created,
    added and analysed in a scratch directory.
    """

    ok: bool
    text: str = ""


class RawViolation(DataModel):
    """One row of a CodeCheck violations CSV, before it becomes a finding (req 6.9)."""

    check_id: str
    check_name: str
    path: str
    line: int
    column: int | None = None
    message: str
    entity: str | None = None


class ExtractRequest(DataModel):
    """Everything the snapshot worker needs to build a ``ProjectSnapshot``."""

    files: set[str] = Field(default_factory=set)
    kinds_by_scope: dict[Scope, str] = Field(default_factory=dict)
    metrics_by_scope: dict[Scope, list[str]] = Field(default_factory=dict)
    synthetic: list[str] = Field(default_factory=list)
    population_metrics: dict[Scope, list[str]] = Field(default_factory=dict)
    ignore: dict[Scope, list[str]] = Field(default_factory=dict)
    architecture: str
    depth: int = Field(ge=1)
    include_edges: bool = True
    include_definitions: bool = False

    @field_validator("kinds_by_scope")
    @classmethod
    def _kinds_belong_to_scopes_that_have_entities(
        cls, kinds: dict[Scope, str]
    ) -> dict[Scope, str]:
        for scope, kind in kinds.items():
            if scope not in SCOPE_KINDS:
                raise ValueError(f"scope {scope!r} has no entities, so it has no kind string")
            if not kind.strip():
                raise ValueError(f"the kind string for scope {scope!r} is empty")
        return kinds

    @field_serializer("files")
    def _dump_files(self, files: set[str]) -> list[str]:
        return sorted(files)
