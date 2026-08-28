"""The SARIF view of a run: one result per finding, one rule per rule name (req 7.5).

SARIF 2.1.0 is what a code-scanning platform reads, so this renderer's job is to translate
the Gate's vocabulary into SARIF's without losing anything on the way. The design fixes the
frame -- ``$schema`` at the OASIS URL, repo-relative ``artifactLocation.uri`` against
``%SRCROOT%`` declared in ``run.originalUriBaseIds``, ``region.startLine`` at least 1 -- and
tests validate every document this module produces against the published schema, so the notes
below are only about the decisions the schema cannot make.

* **Not every path is a file.** ``Finding.path`` is an architecture node path on an arch-scope
  finding (task 4.3) and is empty on a project-wide one. A node is not an artifact, so it
  becomes a ``logicalLocation`` and gets no ``physicalLocation`` at all; a finding with no
  path gets an empty ``locations`` array, which SARIF reads as "not associated with a
  location". Inventing an artifact URI for either would point a reviewer at a file that does
  not exist.
* **Not every path is inside the repository.** Understand analyses files the repository does
  not own -- its own ``builtins.py`` stub, for one -- and task 4.7 keeps those paths absolute.
  An absolute path has no form relative to ``%SRCROOT%``, so it is emitted as an absolute
  ``file://`` URI *without* ``uriBaseId``: claiming the base id would resolve the finding to a
  nonexistent path under the repository root. Every in-repo path is repo-relative already, so
  absoluteness is the whole test -- on Windows too, where such a path keeps its drive letter.
  Separators are normalised to forward slashes before anything else, because a backslash is a
  character inside a URI path and would be percent-encoded into the filename; the URI itself
  is percent-encoded, since a path with a space in it is not a URI.
* **``startLine`` is 1-based.** ``line`` is ``None`` for a CodeCheck row that names a file
  rather than a line and for every fan finding, and 0 is what an unmapped row would carry;
  either way the region is omitted rather than emitted as the ``startLine: 0`` the SARIF spec
  forbids.
* **The entity travels as a logical location.** Requirement 7.1's qualified name lives in
  ``details["entity"]`` for a CodeCheck finding and in ``entity.key.longname`` elsewhere
  (task 4.7). Only ``fullyQualifiedName`` is emitted: SARIF's ``name`` is the *unqualified*
  leaf, and neither an Understand longname nor an architecture node path can be split into one
  without guessing a separator. A file-scope longname that merely repeats the path says
  nothing and is dropped, as in the human renderer.
* **Rules carry the hint.** One ``reportingDescriptor`` per distinct ``Finding.rule``, sorted
  by id so the array is stable however the findings are ordered, with the remediation text of
  requirement 7.2 as ``help``. The hint is a property of the rule (the catalogue is keyed by
  rule name), so it is stated once there rather than repeated on every result.
* **Everything else goes in ``properties``.** Kind, scope, metric, value, before, limit, limit
  source, blocking, pre-existing and the ``details`` bag have no SARIF home, and a SARIF file
  that says less than the JSON output would make the two formats disagree. The keys are the
  model's own names, so a consumer can join a result to the JSON document field by field.
  Absent measurements are omitted rather than written as ``null``.

``level`` follows the design: ``error`` and ``warning`` map through, and a pre-existing
finding is a ``note`` whatever its severity, because it is not what this change did.

The rule grammar is parsed with :func:`~scitools_hook.models.findings.parse_rule_name`, which
cannot fail here: ``Finding`` validates its rule name on construction, so every rule reaching
this module is inside the grammar.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final
from urllib.parse import quote

from scitools_hook.models.findings import Finding, RunResult, parse_rule_name

JsonObject = dict[str, Any]
"""One JSON object under construction; SARIF is deeply nested and only leaves are typed."""

SARIF_VERSION: Final = "2.1.0"
SARIF_SCHEMA_URI: Final = (
    "https://docs.oasis-open.org/sarif/sarif/v2.1.0/os/schemas/sarif-schema-2.1.0.json"
)
TOOL_NAME: Final = "scitools-hook"
SRCROOT: Final = "%SRCROOT%"
"""The uri base id every in-repo artifact location is expressed against."""

_INDENT: Final = 2
_FIRST_LINE: Final = 1
"""Smallest line number SARIF accepts; anything below it means "no region"."""

_NOTE: Final = "note"
_WINDOWS_DRIVE: Final = re.compile(r"^[A-Za-z]:/")
_MEASUREMENTS: Final[tuple[str, ...]] = ("metric", "value", "before", "limit")
"""Finding fields that are omitted from ``properties`` when the finding has no value for them."""


def render_sarif(result: RunResult, tool_version: str) -> str:
    """Render ``result`` as one SARIF 2.1.0 document, without a trailing newline (req 7.5)."""
    rules = _rules(result.findings)
    indexes = {rule["id"]: index for index, rule in enumerate(rules)}
    run: JsonObject = {
        "tool": {"driver": {"name": TOOL_NAME, "version": tool_version, "rules": rules}},
        "originalUriBaseIds": {SRCROOT: {"uri": _directory_uri(result.repo_root)}},
        "results": [_result(finding, indexes) for finding in result.findings],
    }
    document: JsonObject = {"$schema": SARIF_SCHEMA_URI, "version": SARIF_VERSION, "runs": [run]}
    return json.dumps(document, indent=_INDENT, ensure_ascii=False)


def _rules(findings: Iterable[Finding]) -> list[JsonObject]:
    """One descriptor per distinct rule name, sorted by id so the array is stable."""
    by_rule: dict[str, list[Finding]] = {}
    for finding in findings:
        by_rule.setdefault(finding.rule, []).append(finding)
    return [_descriptor(rule, by_rule[rule]) for rule in sorted(by_rule)]


def _descriptor(rule: str, findings: Sequence[Finding]) -> JsonObject:
    """The rule as SARIF describes it: its id, what it is, and what to do about it (req 7.2)."""
    descriptor: JsonObject = {"id": rule, "shortDescription": {"text": _describe(rule, findings)}}
    hint = _first(finding.hint for finding in findings)
    if hint:
        descriptor["help"] = {"text": hint}
    return descriptor


def _describe(rule: str, findings: Sequence[Finding]) -> str:
    """What the rule measures, in one line: a metric, a structural rule, or a check."""
    parsed = parse_rule_name(rule)
    label = rule.partition(".")[2]
    if parsed.category == "codecheck":
        name = _first(_check_name(finding) for finding in findings)
        return f"CodeCheck check {label}: {name}" if name else f"CodeCheck check {label}"
    if parsed.category == "structure":
        return f"structural rule {label}"
    return f"{parsed.scope} metric {label}"


def _check_name(finding: Finding) -> str:
    """The CodeCheck check's human name, which the mapper leaves in ``details``."""
    name = finding.details.get("check_name")
    return name if isinstance(name, str) else ""


def _first(texts: Iterable[str]) -> str:
    """The first non-empty text, or ``""`` when there is none."""
    return next((text for text in texts if text), "")


def _result(finding: Finding, indexes: Mapping[str, int]) -> JsonObject:
    """One finding as one SARIF result (req 7.5)."""
    return {
        "ruleId": finding.rule,
        "ruleIndex": indexes[finding.rule],
        "level": _level(finding),
        "message": {"text": finding.message},
        "locations": _locations(finding),
        "properties": _properties(finding),
    }


def _level(finding: Finding) -> str:
    """``error``/``warning`` as configured; anything pre-existing is reported as a note."""
    return _NOTE if finding.preexisting else finding.severity


def _properties(finding: Finding) -> JsonObject:
    """The facts SARIF has no field for, under the names the JSON output uses."""
    properties: JsonObject = {"kind": finding.kind, "scope": finding.scope}
    for field in _MEASUREMENTS:
        value = getattr(finding, field)
        if value is not None:
            properties[field] = value
    properties["limit_source"] = finding.limit_source
    properties["blocking"] = finding.blocking
    properties["preexisting"] = finding.preexisting
    if finding.details:
        properties["details"] = dict(finding.details)
    return properties


def _locations(finding: Finding) -> list[JsonObject]:
    """Where the finding is: a file region, a logical name, both, or neither."""
    location: JsonObject = {}
    physical = _physical_location(finding)
    if physical is not None:
        location["physicalLocation"] = physical
    name = _entity_name(finding)
    if name:
        location["logicalLocations"] = [{"fullyQualifiedName": name}]
    return [location] if location else []


def _physical_location(finding: Finding) -> JsonObject | None:
    """The artifact and region of a finding about a file; ``None`` when it is about neither."""
    if finding.scope == "arch" or not finding.path:
        return None
    physical: JsonObject = {"artifactLocation": _artifact_location(finding.path)}
    if finding.line is not None and finding.line >= _FIRST_LINE:
        physical["region"] = {"startLine": finding.line}
    return physical


def _artifact_location(path: str) -> JsonObject:
    """A repo-relative URI against ``%SRCROOT%``, or an absolute one for a path outside it."""
    posix = _posix(path)
    if _is_absolute(posix):
        return {"uri": _file_uri(posix)}
    return {"uri": quote(posix, safe="/"), "uriBaseId": SRCROOT}


def _entity_name(finding: Finding) -> str:
    """The qualified name to report as a logical location, or ``""`` when it adds nothing."""
    if finding.scope == "arch":
        return finding.path
    detail = finding.details.get("entity")
    name = detail if isinstance(detail, str) else ""
    if not name and finding.entity is not None:
        name = finding.entity.key.longname
    return "" if name == finding.path else name


def _directory_uri(path: str) -> str:
    """A directory as a ``file://`` URI; SARIF resolves a base id by concatenation."""
    uri = _file_uri(_posix(path))
    return uri if uri.endswith("/") else f"{uri}/"


def _file_uri(posix: str) -> str:
    """An absolute path as a ``file://`` URI, percent-encoded."""
    return f"file:///{quote(posix.lstrip('/'), safe='/:')}"


def _posix(path: str) -> str:
    """Forward slashes, whatever the platform that produced the path used."""
    return path.replace("\\", "/")


def _is_absolute(posix: str) -> bool:
    """Whether a path is absolute, on POSIX or on Windows; in-repo paths never are."""
    return posix.startswith("/") or _WINDOWS_DRIVE.match(posix) is not None
