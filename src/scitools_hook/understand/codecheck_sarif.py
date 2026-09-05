"""CodeCheck's inspection results, read from the SARIF Understand 8.0 always writes (2.3, 2.6).

Build 1262 changed how a CodeCheck run reports. It writes ``results.sarif`` into the output
directory every time, and the three CSV exports this project reads by name -- measured, their
compiled strings are gone from ``und`` itself -- are replaced by one report from a plugin,
``CodeCheckResultsByTable.csv``, whose columns are neither the same nor complete: the plugin
leaves ``Check Name`` and ``Severity`` empty.

So on 8.0 the violations come from the SARIF, which is a documented format rather than a
compiled-in column order, and the CSV reader stays for 6.5. Both produce the same
:class:`~scitools_hook.models.understand.RawViolation`, so nothing downstream learns that a
build changed.

**This is specified and not measured.** The licence on the machine this was written on
excludes CodeCheck, so no real inspection has ever been read here: the mapping below follows
the SARIF 2.1.0 schema and the shape ``und analyze -sarif`` writes, which *is* measured, and
the contract test for a real inspection is an expected failure naming that reason
(requirement 2.6). It is the one part of this specification standing on a document rather
than on a run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final, NoReturn

from scitools_hook.errors import AnalysisFailedError
from scitools_hook.models.understand import NO_LINE, RawViolation

RESULTS_SARIF: Final = "results.sarif"
"""What ``und codecheck`` writes into its output directory on 8.0, always.

Named by ``und help codecheck``, and the one fact the 8.0 reader stands on: everything
else about the document follows the published SARIF 2.1.0 schema.
"""

_HINT: Final = (
    "Understand 8.0 writes results.sarif for every codecheck run. A file that is not one "
    "means the run failed before it inspected anything -- read und's own output above."
)

_RESULT_HINT: Final = (
    "Every CodeCheck violation names the rule it broke and the file it is in. A result "
    "missing either is a document this reader has not seen before: record it against "
    "requirement 2.6 and widen the mapping, rather than reporting a finding without one."
)


def find_results(out_dir: Path) -> Path | None:
    """The inspection's SARIF in ``out_dir``, or ``None`` when this build wrote none."""
    found = out_dir / RESULTS_SARIF
    return found if found.is_file() else None


def read_sarif_violations(path: Path) -> list[RawViolation]:
    """Every violation in one inspection document, in the order it reports them.

    Refused rather than read as empty when the document is not SARIF: a run that produced
    nothing readable is not a run that found nothing, and the difference is a gate that
    passes a commit it never checked.
    """
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as unreadable:
        raise AnalysisFailedError(f"{path} could not be read: {unreadable}", hint=_HINT) from None
    except ValueError as unparsable:
        raise AnalysisFailedError(f"{path} is not JSON: {unparsable}", hint=_HINT) from None
    runs = document.get("runs") if isinstance(document, dict) else None
    if not isinstance(runs, list):
        raise AnalysisFailedError(f"{path} carries no SARIF runs", hint=_HINT)
    return [found for run in runs if isinstance(run, dict) for found in _run_violations(run, path)]


def _run_violations(run: dict[str, Any], path: Path) -> list[RawViolation]:
    """One run's results, with the rule names and artifact paths its tables carry."""
    names = _rule_names(run)
    artifacts = _artifact_uris(run)
    results = run.get("results")
    return [
        _violation(result, names, artifacts, path)
        for result in (results if isinstance(results, list) else [])
        if isinstance(result, dict)
    ]


def _violation(
    result: dict[str, Any], names: dict[str, str], artifacts: list[str], path: Path
) -> RawViolation:
    """One result as the record the finding mapper already consumes.

    The rule id and the file are both required, for the reasons the CSV reader requires
    them: a violation with no check id reaches ``models.findings.codecheck_rule`` and raises
    a *configuration* error far from its cause, and one with no file becomes a finding
    against the repository root. Both are refused here, where the document can still be named.
    """
    check_id = str(result.get("ruleId") or "")
    if not check_id:
        _reject(path, result, "rule id")
    where = _physical(result)
    named = _path_of(where, artifacts)
    if not named:
        _reject(path, result, "file")
    region = where.get("region")
    at = region if isinstance(region, dict) else {}
    line, column = at.get("startLine"), at.get("startColumn")
    return RawViolation(
        check_id=check_id,
        check_name=names.get(check_id) or check_id,
        path=named,
        line=int(line) if isinstance(line, int) else NO_LINE,
        column=int(column) if isinstance(column, int) else None,
        message=_message(result),
        entity=_entity(result),
    )


def _reject(path: Path, result: dict[str, Any], missing: str) -> NoReturn:
    """Refuse one result the Gate cannot turn into a finding, quoting what it did say."""
    raise AnalysisFailedError(
        f"{path} carries a result with no {missing}: {_message(result)!r}",
        hint=_RESULT_HINT,
    )


def _rule_names(run: dict[str, Any]) -> dict[str, str]:
    """Rule id -> its human name, from the tool's own table; ids without one are absent.

    ``name`` is SARIF's field for a readable title. A rule that carries only an id answers
    nothing here and the caller falls back to the id, which is what the operator would have
    read in the CSV's empty ``Check Name`` column anyway.
    """
    driver = run.get("tool", {}).get("driver", {}) if isinstance(run.get("tool"), dict) else {}
    rules = driver.get("rules") if isinstance(driver, dict) else None
    found: dict[str, str] = {}
    for rule in rules if isinstance(rules, list) else []:
        if isinstance(rule, dict) and rule.get("id") and rule.get("name"):
            found[str(rule["id"])] = str(rule["name"])
    return found


def _artifact_uris(run: dict[str, Any]) -> list[str]:
    """The artifact table as a list of paths, so a result may name its file by index."""
    artifacts = run.get("artifacts")
    return [
        str(entry.get("location", {}).get("uri", ""))
        for entry in (artifacts if isinstance(artifacts, list) else [])
        if isinstance(entry, dict) and isinstance(entry.get("location"), dict)
    ]


def _physical(result: dict[str, Any]) -> dict[str, Any]:
    """The first location's physical part, or an empty one for a result that has none."""
    locations = result.get("locations")
    first = locations[0] if isinstance(locations, list) and locations else None
    where = first.get("physicalLocation") if isinstance(first, dict) else None
    return where if isinstance(where, dict) else {}


def _path_of(where: dict[str, Any], artifacts: list[str]) -> str:
    """The file a result is about, named inline or by index into the artifact table."""
    location = where.get("artifactLocation")
    if not isinstance(location, dict):
        return ""
    uri = location.get("uri")
    if isinstance(uri, str):
        return uri
    index = location.get("index")
    return artifacts[index] if isinstance(index, int) and 0 <= index < len(artifacts) else ""


def _message(result: dict[str, Any]) -> str:
    """What the check said; SARIF puts it under ``message.text``."""
    message = result.get("message")
    text = message.get("text") if isinstance(message, dict) else None
    return str(text) if text else ""


def _entity(result: dict[str, Any]) -> str | None:
    """The entity a violation is about, when the run names one."""
    locations = result.get("locations")
    first = locations[0] if isinstance(locations, list) and locations else None
    logical = first.get("logicalLocations") if isinstance(first, dict) else None
    named = logical[0] if isinstance(logical, list) and logical else None
    if not isinstance(named, dict):
        return None
    name = named.get("fullyQualifiedName") or named.get("name")
    return str(name) if name else None
