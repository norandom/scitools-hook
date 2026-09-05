"""What a configuration change costs a cached analysis, as one 16-character string.

Two caches in this specification are keyed on this value -- the commit-built before database
(requirement 3.5) and the before snapshot (requirement 8.6) -- and they have to agree about
when a cached answer stopped describing the project, or one of them will serve a document the
other had already thrown away.

**Where the line falls.** A setting that changes what Understand analyses, or what the worker
records while it walks, belongs in the fingerprint: the language set and the file selection,
which decide what the database holds; the architecture and its depth, which are recorded in
the snapshot itself; the ignore regexes, which the worker applies as it records; the metrics
the request asks for, because a metric nobody asked for is absent from the document rather
than null; and the acknowledged parse errors, which are here for conservatism rather than
necessity -- they change what a *check* makes of a snapshot, not the snapshot, and the safe
direction of a cache key is the one that invalidates too often.

A setting that changes only how a recorded value is *judged* does not belong: a limit, a
severity, the ratchet levers, the baseline. Editing a threshold is the commonest thing an
operator does, and making it cost a full re-analysis would be the fastest way to have the
cache switched off.

**The metric set, not the limits.** ``thresholds`` reaches the fingerprint as the set of
``scope.metric`` names it names, with the statistics prefix kept because a prefixed threshold
is collected differently. Changing a maximum leaves that set identical; adding a threshold on
a metric nothing else asked for changes it, and must, because the previous document has no
value for it.
"""

from __future__ import annotations

import hashlib
import json
from typing import Final

from scitools_hook.config.metric_names import format_metric_name
from scitools_hook.config.models import Settings

DIGEST_CHARACTERS: Final = 16
"""How much of the SHA-256 is kept: enough for a cache key, short enough for a file name."""


def analysis_fingerprint(settings: Settings) -> str:
    """The settings this analysis depends on, hashed; equal settings always answer equal.

    Stable across processes and machines -- ``json.dumps`` with sorted keys over plain data,
    never ``hash()``, whose seed changes per interpreter.
    """
    payload = {
        "languages": settings.project.languages,
        "include": sorted(settings.project.include),
        "exclude": sorted(settings.project.exclude),
        "architecture": settings.structure.architecture,
        "architecture_file": _text(settings.structure.architecture_file),
        "architecture_options": dict(sorted(settings.structure.architecture_options.items())),
        "depth": settings.structure.depth,
        "definitions": settings.structure.duplicate_definitions is not None,
        "ignore": {
            "files": sorted(settings.ignore.files),
            "classes": sorted(settings.ignore.classes),
            "routines": sorted(settings.ignore.routines),
        },
        "metrics": sorted(
            f"{spec.scope}.{format_metric_name(spec.ref)}" for spec in settings.thresholds
        ),
        "acknowledged": sorted(
            [sorted(entry.paths), entry.reason] for entry in settings.parse.acknowledged
        ),
    }
    document = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(document.encode("utf-8")).hexdigest()[:DIGEST_CHARACTERS]


def _text(value: object | None) -> str | None:
    """A path or ``None`` as a string, so the payload holds only JSON-native values."""
    return None if value is None else str(value)
