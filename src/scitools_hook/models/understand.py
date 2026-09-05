"""Records the Understand adapter produces and consumes (req 1.x, 2.6, 5.5, 6.9).

``ExtractRequest`` is deliberately self-describing: the worker runs under Understand's own
``upython`` and must never import ``scitools_hook``, so everything it needs — which files,
which kind string per scope, which metrics, which synthetic metrics, which populations,
which ignore patterns, which architecture and depth — travels in the request. Build
``kinds_by_scope`` from ``config.metric_names.SCOPE_KINDS`` (never by iterating ``SCOPES``:
``project`` and ``arch`` have no entity kind, and the worker must never call ``db.ents("")``).
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Final, Literal

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
    accuracy: float | None = None
    """The share of files the analysis parsed with no error or warning (req 7.1).

    ``und analyze -accuracy`` prints ``N of M parsed files had no errors or warnings (P%)``
    after the summary; this is that fraction. ``None`` means the switch was not passed or the
    build does not know it, which is not a figure of zero -- a build that reports nothing and
    a project that resolved nothing must not read alike.
    """

    sarif_path: Path | None = None
    """Where ``und analyze -sarif`` wrote its diagnostics, when it was asked to (req 2.1)."""


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


class Feature(StrEnum):
    """What this specification adds that a given Understand build may or may not offer (1.1).

    Measured rather than inferred from a version number: 6.5 and 8.0 already differ in three
    of these, and a table of build numbers would be wrong at the next build. ``doctor`` prints
    one row per member and stores the answers for a check to validate its configuration
    against.
    """

    UNDERSTAND_SARIF = "understand_sarif"
    COMMIT_BEFORE = "commit_before"
    GENERATED_ARCHS = "generated_archs"
    PLUGIN_METRICS = "plugin_metrics"
    UNUSED_RULE = "unused_rule"
    ACCURACY = "accuracy"


class Availability(DataModel):
    """Whether the installed build offers one feature, and what was learnt while asking.

    Three states, and the third is the one that matters: ``unverified`` is a probe that could
    not run -- no git on the machine, no scratch directory -- and saying that is not the same
    as saying the build lacks the feature. A configuration that asks for something
    ``unverified`` fails closed rather than being quietly ignored.
    """

    state: Literal["available", "not on this build", "unverified"]
    detail: str = ""
    generated: list[str] = Field(default_factory=list)
    """For the generated architectures, the names ``und arch -list`` offered (req 4.2)."""


class FeatureReport(DataModel):
    """What one build offered, when it was asked; stored between runs and stale on a new build.

    The build string is part of the record because the answers are only about that build. A
    report from another one is discarded rather than trusted, which is why the check that
    validates a configuration against this asks for the build it holds.
    """

    build: str
    features: dict[Feature, Availability] = Field(default_factory=dict)

    def offers(self, feature: Feature) -> bool:
        """Whether ``feature`` was measured as available; anything else is a no."""
        found = self.features.get(feature)
        return found is not None and found.state == "available"


class RawViolation(DataModel):
    """One row of a CodeCheck violations CSV, before it becomes a finding (req 6.9)."""

    check_id: str
    check_name: str
    path: str
    line: int
    column: int | None = None
    message: str
    entity: str | None = None


NO_LINE: Final = 0
"""The line a violation carries when its report gives no usable one.

Zero, not ``None`` and not one: it is Understand's own value for a check that reports
against a file rather than a position (``check.violation(ent, ent, 0, 0, ...)``), and
``analysis.codecheck`` reads ``line > 0`` to decide whether a finding has a line at all.
A different value here would silently place every file-level violation on a real line.

It lives beside :class:`RawViolation` rather than in the CSV reader because two readers
now produce that record -- the CSV one for 6.5 and the SARIF one for 8.0 -- and a sentinel
owned by one of them would have to be imported by the other.
"""


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
