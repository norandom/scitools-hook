"""CodeCheck violations as findings (req 6.9).

Understand's CodeCheck is the one rule engine the Gate does not own: the checks, their ids
and their wording come from the configuration the operator names, and this module only
translates each row the runner parsed into the shared ``Finding`` contract (req 7.1). It
runs nothing -- ``und codecheck`` is the runner's job (task 6.7) -- so it stays pure and
testable without a license, like every other module under ``analysis``.

Three decisions are worth stating, because a row carries less than a metric finding does:

* **Paths become repo-relative.** ``und codecheck`` reports absolute paths, and requirement
  7.1 asks for the path relative to the repository root, so ``repo_root`` is subtracted
  lexically -- ``PurePosixPath`` arithmetic only, never a filesystem call. A row that is
  already relative passes through, and a row *outside* the root keeps its absolute path:
  Understand analyses files the repository does not own (its own ``builtins.py`` stub for
  Python, for one), and inventing a relative path for those would point at nothing.
  Separators are normalised to forward slashes first, because Understand reports native
  paths and every other path in a snapshot is posix.
* **The entity travels in ``details``.** ``RawViolation.entity`` is a name string, not an
  ``EntityRef``; there is no ``EntityKey`` to build from it without guessing a kind and a
  path, so ``Finding.entity`` stays ``None`` and the name is carried in
  ``details["entity"]`` beside ``check_name`` and ``column``. A key is present only when
  its value is known, so a file-level row with no column and no entity carries neither.
* **The finding is file-scoped and metric-free.** A violation is not a measurement:
  ``value``, ``before`` and ``limit`` are ``None`` and ``limit_source`` is ``"rule"``, as
  for the structural rules. Line 0 is CodeCheck's way of saying "the file", so it becomes
  no line at all.

``severity`` is the configured ``codecheck.severity`` (req 3.7) and decides ``blocking``,
exactly as in the structural evaluators; ``hint`` is left empty for the pipeline to fill.
Findings come back sorted by path, line, column and check id, so the same rows always
produce the same output whatever order the CSV happened to list them in.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import PurePath, PurePosixPath

from scitools_hook.config.models import Severity
from scitools_hook.models.findings import Finding, codecheck_rule
from scitools_hook.models.understand import RawViolation

_NO_COLUMN = -1
"""Sort stand-in for a missing column, so a file-level row precedes a positioned one."""


def map_violations(
    violations: Iterable[RawViolation],
    severity: Severity,
    repo_root: str | PurePath | None = None,
) -> list[Finding]:
    """Turn parsed CodeCheck rows into findings with repo-relative paths (req 6.9).

    ``repo_root`` may be omitted when the rows are already repo-relative. The result is
    sorted by path, line, column and check id; ``hint`` is left for the pipeline.
    """
    root = None if repo_root is None else _posix(str(repo_root))
    rows = [(_repo_relative(violation.path, root), violation) for violation in violations]
    rows.sort(key=lambda row: _order(row[0], row[1]))
    return [_finding(path, violation, severity) for path, violation in rows]


def _posix(raw: str) -> PurePosixPath:
    """A path as forward-slash segments; Understand reports native separators."""
    return PurePosixPath(raw.replace("\\", "/"))


def _repo_relative(raw: str, root: PurePosixPath | None) -> str:
    """``raw`` relative to ``root``, or unchanged when it lies outside ``root`` (req 7.1)."""
    if not raw:
        return raw
    path = _posix(raw)
    if root is None:
        return path.as_posix()
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _order(path: str, violation: RawViolation) -> tuple[str, int, int, str]:
    """Sort key of one row: where it is, then which check reported it."""
    column = _NO_COLUMN if violation.column is None else violation.column
    return (path, violation.line, column, violation.check_id)


def _finding(path: str, violation: RawViolation, severity: Severity) -> Finding:
    """One violation as a finding; ``hint`` is attached by the pipeline (req 6.9, 7.1)."""
    return Finding(
        kind="codecheck",
        rule=codecheck_rule(violation.check_id),
        scope="file",
        path=path,
        line=violation.line if violation.line > 0 else None,
        limit_source="rule",
        severity=severity,
        blocking=severity == "error",
        message=violation.message,
        details=_details(violation),
    )


def _details(violation: RawViolation) -> dict[str, object]:
    """What the finding fields cannot hold: the check's name, its column and its entity."""
    details: dict[str, object] = {"check_name": violation.check_name}
    if violation.column is not None:
        details["column"] = violation.column
    if violation.entity is not None:
        details["entity"] = violation.entity
    return details
