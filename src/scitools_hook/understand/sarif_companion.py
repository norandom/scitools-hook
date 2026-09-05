"""Understand's own SARIF, put beside the Gate's and re-rooted on the repository (2.1, 2.4).

Understand writes two SARIF documents of its own: the parse errors and warnings of an
analysis (``und analyze -sarif``) and, where CodeCheck is licensed, the results of an
inspection (``results.sarif``). GitHub code scanning accepts several tools in one upload and
distinguishes them by ``tool.driver.name``, so the useful thing is to hand the operator all
three files rather than to merge anything: merging would mix fingerprints and rule ids from
tools that know nothing about each other.

**Why a rewrite is needed at all.** Measured on Build 1262, the document names its files
relative to *the directory containing the Understand project*::

    "originalUriBaseIds": {"UND_PROJECT": {"uri": "file:///…/cache/<repo-id>/"}}
    "artifacts": [{"location": {"uri": "after/src/a.py", "uriBaseId": "UND_PROJECT"}}]

The Gate analyses a shadow tree, so that base is a directory in the user's cache and every
path under it begins with the shadow's own segment. Uploaded unchanged, every result would
land on a file no repository has. So the base becomes the repository root and the shadow
segment comes off the front of each path -- the same normalisation
``SyncState.record_parse_errors`` already performs for parse errors, for the same reason.

Nothing else is touched. The tool name, the rules, the fingerprints and the results stay as
Understand wrote them, because they are Understand's statement and not this tool's.

A source that is missing or will not parse becomes a **reported problem**, never an
exception: the Gate's own SARIF is the deliverable, the companions are extra, and a run must
not fail over a file it was copying as a convenience (requirement 2.4).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

PROJECT_BASE: Final = "UND_PROJECT"
"""The base id Understand names its project directory with (measured on Build 1262)."""

BASE_IDS: Final = "originalUriBaseIds"
ARTIFACT_LOCATION: Final = "artifactLocation"


@dataclass(frozen=True, slots=True)
class CompanionFile:
    """One of Understand's SARIF documents, as this run left it.

    ``target`` is ``None`` exactly when ``problem`` is set: either the file was written or it
    was not, and the reason is carried rather than raised.
    """

    kind: str
    source: Path
    target: Path | None = None
    problem: str = ""


def companion_path(gate_sarif: Path, kind: str) -> Path:
    """Where one companion goes: beside the Gate's file, named so the two cannot be confused."""
    suffix = gate_sarif.suffix or ".sarif"
    return gate_sarif.with_name(f"{gate_sarif.stem}.understand-{kind}{suffix}")


def companions(
    sources: Mapping[str, Path], gate_sarif: Path, repo_root: Path, shadow: Path
) -> list[CompanionFile]:
    """Copy each of Understand's documents beside the Gate's, on repository paths.

    ``sources`` maps a kind -- ``analysis``, ``codecheck`` -- to the file Understand wrote.
    ``shadow`` is the tree the database was built over, whose own directory name prefixes
    every path in the document.
    """
    return [
        _one(kind, source, companion_path(gate_sarif, kind), repo_root, shadow.name)
        for kind, source in sorted(sources.items())
    ]


def _one(kind: str, source: Path, target: Path, repo_root: Path, segment: str) -> CompanionFile:
    """One document, read, re-rooted and written; any failure becomes its ``problem``."""
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except OSError as unreadable:
        return CompanionFile(kind=kind, source=source, problem=f"could not be read: {unreadable}")
    except ValueError as unparsable:
        return CompanionFile(kind=kind, source=source, problem=f"is not JSON: {unparsable}")
    if not isinstance(document, dict) or "runs" not in document:
        return CompanionFile(kind=kind, source=source, problem="carries no SARIF runs")
    _rebase(document, repo_root, segment)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    except OSError as unwritable:
        return CompanionFile(
            kind=kind, source=source, problem=f"could not be written: {unwritable}"
        )
    return CompanionFile(kind=kind, source=source, target=target)


def _rebase(document: dict[str, Any], repo_root: Path, segment: str) -> None:
    """Point the project base at the repository and take the shadow segment off every path."""
    runs = document.get("runs")
    for run in runs if isinstance(runs, list) else []:
        if not isinstance(run, dict):
            continue
        _rebase_base_ids(run, repo_root)
        _strip_segment(run, segment)


def _rebase_base_ids(run: dict[str, Any], repo_root: Path) -> None:
    """Rewrite the project base to the repository root, leaving any other base alone."""
    bases = run.get(BASE_IDS)
    if not isinstance(bases, dict):
        return
    project = bases.get(PROJECT_BASE)
    if isinstance(project, dict):
        project["uri"] = f"{repo_root.resolve().as_uri()}/"


def _strip_segment(node: Any, segment: str) -> None:
    """Walk the document and drop ``<segment>/`` from the front of every artifact path.

    Recursive because a path can appear in three places -- the ``artifacts`` table, a result's
    physical location, and a fix's -- and a document that grows a fourth should not silently
    keep the shadow in it.
    """
    if isinstance(node, dict):
        location = node.get(ARTIFACT_LOCATION)
        if isinstance(location, dict):
            _strip_uri(location, segment)
        if "uri" in node and "uriBaseId" in node:
            _strip_uri(node, segment)
        for value in node.values():
            _strip_segment(value, segment)
    elif isinstance(node, list):
        for value in node:
            _strip_segment(value, segment)


def _strip_uri(location: dict[str, Any], segment: str) -> None:
    """Take one leading path segment off a relative artifact URI, if it is there."""
    uri = location.get("uri")
    head = f"{segment}/"
    if isinstance(uri, str) and uri.startswith(head):
        location["uri"] = uri[len(head) :]
