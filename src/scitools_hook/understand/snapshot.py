"""Turn one database into an immutable ``ProjectSnapshot`` (req 3.5, 5.5, 6.7, 6.8, 9.7).

The worker runs under Understand's own interpreter and may not import this package, so the
whole configuration travels in the request: which files are of interest, the Understand kind
string of every scope that has entities, the metrics each scope is judged by, the metrics
whose *population* a threshold reduces, the synthetic metric ids the worker must compute
itself, the ignore regexes, the architecture and its depth. Building that request is this
module's real work; the answer is validated and handed on unchanged.

Four things here are decisions rather than transcription, and each one has a silent failure
behind it:

* **The kind strings come from ``SCOPE_KINDS``, never from ``SCOPES``.** ``project`` and
  ``arch`` have no entity kind, and a caller that iterated the scopes would send the worker
  an empty kind string — ``db.ents("")`` — instead of leaving those scopes alone.
* **Population metrics keep their stats prefix.** The worker splits ``AVG:CyclomaticStrict``
  itself and needs the prefix to tell a project metric it reads from ``db.metric`` (plain)
  from one it reduces over a scope's population (prefixed). Stripping it here would turn
  every project-scope population threshold into a single database metric read.
* **The analysis root travels exactly as the caller named it.** It has to be the directory
  ``und add`` was pointed at, character for character, because the worker makes every
  entity's long name relative to it; resolving symlinks would produce a root the database
  never saw, and the answer would be a valid, empty, entirely green document.
* **``ExtractRequest`` cannot carry four of the thirteen keys.** It has no ``db``, ``root``,
  ``side`` or ``parse_errors`` field and forbids extras, and ``models/`` is outside this
  task's boundary (recorded as a concern in tasks.md 6.2). Those four are therefore added to
  the wire dictionary by hand, from :class:`SnapshotTarget`, and nothing else is.

The design sketches ``SnapshotExtractor(runner, ignore, catalogue)`` and
``extract(db_path, req)``. The shipped shape takes the whole ``Settings`` — the request is
built *here* (task 6.6) rather than by the caller, and ignore rules, thresholds and
structure rules all feed it — and takes one :class:`SnapshotTarget` instead of a database
path plus a request the model cannot express.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from scitools_hook.config.metric_names import (
    PLUGIN_METRICS,
    SCOPE_KINDS,
    SYNTHETIC_METRICS,
    Scope,
    format_metric_name,
)
from scitools_hook.config.models import Settings, ThresholdSpec
from scitools_hook.errors import AnalysisFailedError
from scitools_hook.models.snapshot import ParseError, ProjectSnapshot, Side
from scitools_hook.models.understand import ExtractRequest
from scitools_hook.understand.api_runner import ApiRunner


@dataclass(frozen=True, slots=True)
class SnapshotTarget:
    """Which database to read, and everything about it the configuration cannot know.

    ``root`` is the directory the database was built from — the shadow tree, not the
    repository — and ``files`` are repository-relative paths inside it. ``parse_errors`` are
    the ones ``und analyze`` reported for this side (req 2.6); the worker only echoes them.
    """

    db: Path
    root: Path
    side: Side
    files: frozenset[str] = frozenset()
    parse_errors: tuple[ParseError, ...] = ()
    rings: int = 0
    """How many dependency steps beyond ``files`` to record entities for (req 8.3).

    Zero is a bounded extraction, which is what whole-project mode and every command but
    ``check`` asks for. Two records the set the check pipeline used to obtain with a second
    whole-project walk."""


class SnapshotExtractor:
    """Builds one ``snapshot`` request from settings and validates the document it gets back."""

    def __init__(self, runner: ApiRunner, settings: Settings, include_edges: bool = True):
        self.runner = runner
        self.settings = settings
        self.include_edges = include_edges

    def request(self, files: Iterable[str] = (), rings: int = 0) -> ExtractRequest:
        """The self-describing request, everything but what only the target knows."""
        return ExtractRequest(
            files=set(files),
            kinds_by_scope=dict(SCOPE_KINDS),
            metrics_by_scope=_element_metrics(self.settings.thresholds),
            synthetic=_synthetic_ids(self.settings.thresholds),
            population_metrics=_population_metrics(self.settings.thresholds),
            plugin_metrics=_plugin_metrics(self.settings.thresholds),
            ignore=_ignore_patterns(self.settings),
            architecture=self.settings.structure.architecture,
            depth=self.settings.structure.depth,
            include_edges=self.include_edges,
            include_definitions=self.settings.structure.duplicate_definitions is not None,
            record_referenced=self.settings.structure.unused_routines is not None,
            neighbourhood_rings=rings,
        )

    def wire_request(self, target: SnapshotTarget) -> dict[str, object]:
        """The request as the worker reads it: the model, plus the four keys it cannot hold."""
        request: dict[str, object] = self.request(target.files, target.rings).model_dump(
            mode="json"
        )
        request["db"] = str(target.db)
        request["root"] = str(target.root)
        request["side"] = target.side
        request["parse_errors"] = [error.model_dump(mode="json") for error in target.parse_errors]
        return request

    def extract(self, target: SnapshotTarget) -> ProjectSnapshot:
        """Read one database into the snapshot every rule is evaluated against."""
        answer = self.runner.run("snapshot", self.wire_request(target))
        try:
            return ProjectSnapshot.model_validate(answer)
        except ValidationError as invalid:
            raise _unusable(target, invalid) from invalid


# --- what the settings ask for ---------------------------------------------------


def _element_metrics(specs: Iterable[ThresholdSpec]) -> dict[Scope, list[str]]:
    """The metrics each element scope's entities are judged by, prefix stripped (req 5.1-5.3).

    Only these count towards the ``unavailable`` report of requirement 5.5: they are what an
    entity is measured against. A metric collected purely to build a population vector is a
    project-level threshold, and an empty vector is reported once by the evaluator instead of
    once per entity of the wrong language.
    """
    found: dict[Scope, set[str]] = {}
    for spec in specs:
        if spec.scope in SCOPE_KINDS and not spec.ref.is_population:
            found.setdefault(spec.scope, set()).add(spec.ref.metric)
    return {scope: sorted(names) for scope, names in found.items()}


def _plugin_metrics(specs: Iterable[ThresholdSpec]) -> dict[Scope, list[str]]:
    """The configured metrics a plugin computes, per element scope (requirement 5.1).

    A subset of :func:`_element_metrics`, sent separately so the worker can ask for them where
    a record is produced instead of once per entity of the scope. Nothing is sent when a
    repository configures none, which is the shipped case: no plugin metric is a shipped
    threshold (requirement 5.4), so an untouched configuration adds no key and the worker's
    walk is byte-for-byte the one a 6.5 install has always done.

    A population threshold is never one of these. A plugin metric reduced over the whole
    project would have to be read for every entity, which is the cost this key exists to
    avoid; :func:`~scitools_hook.config.validate` refuses such a configuration.
    """
    found: dict[Scope, set[str]] = {}
    for spec in specs:
        if spec.scope in SCOPE_KINDS and not spec.ref.is_population:
            if spec.ref.metric in PLUGIN_METRICS:
                found.setdefault(spec.scope, set()).add(spec.ref.metric)
    return {scope: sorted(names) for scope, names in found.items()}


def _population_metrics(specs: Iterable[ThresholdSpec]) -> dict[Scope, list[str]]:
    """The metrics whose population a threshold reduces, **with** their stats prefixes (3.4).

    Two kinds of threshold land here: a stats-prefixed one at any scope, which is evaluated
    over that scope's population, and *every* project-scope one, because ``project`` has no
    entities of its own — a plain project metric is read from ``db.metric`` and a prefixed one
    is reduced over the routine population, and only the prefix tells the worker which.
    ``arch`` is excluded: its values come from the architecture nodes and their edges.
    """
    found: dict[Scope, set[str]] = {}
    for spec in specs:
        if spec.scope != "arch" and (spec.ref.is_population or spec.scope == "project"):
            found.setdefault(spec.scope, set()).add(format_metric_name(spec.ref))
    return {scope: sorted(names) for scope, names in found.items()}


def _synthetic_ids(specs: Iterable[ThresholdSpec]) -> list[str]:
    """The synthetic metric ids the worker must compute for this configuration (req 3.5).

    Understand's own ``CountParams`` is unset for Python (verified), so a request that does
    not declare the synthetic gets the native metric, finds nothing, and every parameter-count
    threshold silently stops firing.
    """
    return sorted({spec.ref.metric for spec in specs} & set(SYNTHETIC_METRICS))


def _ignore_patterns(settings: Settings) -> dict[Scope, list[str]]:
    """The ignore regexes per scope, as the worker applies them (req 3.6)."""
    ignore = settings.ignore
    patterns: dict[Scope, list[str]] = {
        "file": list(ignore.files),
        "class": list(ignore.classes),
        "routine": list(ignore.routines),
    }
    return {scope: found for scope, found in patterns.items() if found}


def _unusable(target: SnapshotTarget, invalid: ValidationError) -> AnalysisFailedError:
    """The error a document that is not a snapshot becomes.

    The models forbid unknown keys, which is what turns a drifting worker contract into a
    failure here — naming the database and the side — instead of a missing metric three
    layers away.
    """
    return AnalysisFailedError(
        f"the snapshot of {target.db} ({target.side}) is not a document this version "
        f"understands: {invalid.error_count()} problem(s)",
        stderr=str(invalid),
        hint="The worker and the Gate are out of step; reinstall the Gate.",
    )
