"""The machine view of a run: one JSON document that *is* the ``RunResult`` (req 7.4).

Requirement 7.4 asks for a single document with a documented, versioned schema carrying the
run metadata, the effective thresholds, all findings, the ignored-entity counts, the
unavailable metrics and the parse errors -- and for nothing else on standard output. The
cheapest way to be sure nothing is missing, and to keep it that way as the model grows, is
not to select fields at all: the document is
:class:`~scitools_hook.models.findings.RunResult` serialized whole, so the schema is the
model, ``schema_version`` is the model's own field, and the round trip
``RunResult.model_validate(json.loads(render_json(result))) == result`` is a test rather than
a promise. A field added to the model reaches every consumer without an edit here; a field
whose meaning changes is a ``schema_version`` bump, which the design already treats as a
breaking change.

Three decisions are worth stating:

* **Two-space indentation, no trailing newline.** The output is committed to CI logs and
  diffed between runs, and a one-line document makes every change look like one changed line.
  The newline belongs to whoever writes the stream, exactly as in
  :func:`~scitools_hook.report.human.render_human`, so a caller can embed the document.
* **Key order is the model's declaration order** -- ``schema_version`` first, so a consumer
  reading the head of the stream knows what it is looking at -- and mapping contents keep the
  order the producer built them in. Nothing is re-sorted, because sorting would hide a
  producer that emits an unstable order instead of fixing it; the analysis layer already
  sorts what it emits (task 3.1), so the same run renders byte-identically in any process.
* **The counts are the producer's.** ``warning_count`` and ``preexisting_count`` are copied,
  not recomputed, so a disagreement with the findings list is visible in the output instead of
  being papered over by the renderer -- the human summary derives its counts from
  ``result.findings`` (task 5.1), and the pipeline must fill these fields from that same list.
  ``blocking_count`` cannot disagree: the model validates it against the findings.

Everything a consumer needs beyond this document -- the remediation hint of requirement 7.2
included -- already travels on each finding, because the pipeline attaches the hint before the
result is assembled.
"""

from __future__ import annotations

from typing import Final

from scitools_hook.models.findings import RunResult

INDENT: Final = 2
"""Spaces per level; two keeps deep findings readable without wrapping a terminal."""


def render_json(result: RunResult) -> str:
    """Render ``result`` as one JSON document, without a trailing newline (req 7.4)."""
    return result.model_dump_json(indent=INDENT)
