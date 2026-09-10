"""The layering rules: files and routines that add a hop without adding a boundary.

This module holds the over-export rule. Its two siblings -- the pass-through routine and the
single-implementation abstraction -- join it here because all three answer one question: does
this piece of structure earn the indirection it costs?

**Over-export.** A file that defines exactly one routine or class, holds nothing else at
module level, and is depended on by exactly one other file is a name with a file wrapped
around it. The reader pays a file, an import and a jump to reach a definition that has one
user, and the agent that wrote it was usually answering "one thing per file" rather than
drawing a boundary. Requirement 4.1 asks for the finding against the file, naming the
dependant, because the fix is "move it into the file that uses it and delete this one" and
neither half of that is actionable without both names.

**The silence is the feature, and it was measured.** On this repository two files define
exactly one routine or class and *none* of them has exactly one dependant, so the rule
reports nothing at all; on a 653-file project twelve files qualify on the count and exactly
one -- a factory module -- is a genuine finding. A rule that fired on all twelve would be a
rule about file size, which this project already has in ``file.CountDeclFunction``.

**What requirement 4.2 keeps out, and why each one would be wrong.**

* *Nothing depends on it.* That is a different finding with a different fix: an unused file is
  dead code, and the dead-code rules own it. Reporting it here would tell the reader to move
  the definition into a dependant that does not exist.
* *Two or more files depend on it.* One shared definition with two users is the boundary
  working. The rule draws the line at one because the moment a second file wants the name,
  the file is where the name belongs.
* *A second module-level definition.* A routine sitting beside a module constant is a module
  with state of its own; folding it into its importer moves that state too.
* *An initialiser.* ``__init__.py``, ``index.*``, ``mod.rs`` and ``__main__.py`` exist to hold
  one name for one importer in four languages. They are excluded by path rather than by shape,
  because that is how the languages decide it -- see
  :data:`~scitools_hook.config.models.DEFAULT_LEAN_OVER_EXPORT_IGNORE`.

**Why this rule needs no "unavailable" answer while its siblings do.** It reads file metrics,
``file_edges`` and the definitions walk, all of which the snapshot already carries, and no
per-entity reference call -- so it has no third state to report and returns a plain list
(``LeanRules.wants_references`` says the same thing from the configuration side). The one
measurement it cannot fake is the definitions walk: a snapshot recorded without it would show
every file as holding no module-level binding, which is why ``config.fingerprint`` keys the
``definitions`` request on this rule being on, and a cache recorded without it is not reused.
The declaration counts get the same treatment the coupling rule gives ``CountLineCode``: a
file whose counts are *missing* is unmeasured, not empty, and is not judged.

**The ``details`` convention this family follows, decided here because five rules copy it.**
Every lean finding keeps the ``structure.`` category, so every one of them renders into the
same JSON ``details`` object and the same SARIF property bag as the existing structural
rules. That makes ``details`` one flat namespace across the category, and **whoever uses a
key second inherits its type**. ``depended_on_by`` is already taken: ``structure.fan_in``
publishes it as a *list of paths*, so this rule -- which names exactly one file -- does not
reuse it holding a bare string, or a consumer iterating the key would get a list from one
rule and the characters of a path from the other. It takes ``dependant`` instead, a scalar
key of its own, which is also what the design gives the siblings for the single entity they
name (``forwards_to`` in the pass-through rule). So: **a shared key keeps the type it already
has, and a rule naming one entity gives it a key of its own rather than narrowing somebody
else's.**
"""

from __future__ import annotations

from collections.abc import Collection, Sequence

from scitools_hook.config.models import Severity, matching_pattern
from scitools_hook.models.findings import Finding, structure_rule
from scitools_hook.models.snapshot import DepEdge, EntityRecord, ProjectSnapshot

_OVER_EXPORT_RULE = structure_rule("over_export")

DECLARATION_METRICS = ("CountDeclFunction", "CountDeclClass")
"""The two file metrics whose sum is "how many names does this file declare".

**Both have to be present**, and a record missing either one is a file this analysis did not
measure rather than a file that declares nothing. That guard is not decoration: a file
carrying ``CountDeclFunction`` 1 with no ``CountDeclClass`` would otherwise be reported as
holding one definition when the second count was never taken, and
``test_a_file_measured_for_only_one_of_the_two_counts_is_not_judged`` fails without it.

What is measured about their availability, rather than assumed. Both are shipped file-scope
defaults (``config.defaults``). For Python, on this install, the catalogue drops only
``PercentLackOfCohesion`` for the language, so both counts are reported. For C++, the
contract fixture recorded them on the installed build directly: ``native/shape.h`` scores
``CountDeclClass`` 1 and ``CountDeclFunction`` 0 (``tests/contract/contract_project.py``) --
a **zero**, which is a measurement, not an absence. No other language has been measured. The
gate reads availability from Understand's own catalogue and drops a metric the language does
not report (``config.validate``, ``RunResult.unavailable_metrics``), so a language that
reported only one of the two would leave this rule silent rather than wrong -- which is the
failure mode to prefer, and the one to look for first if the rule ever reports nothing at all
on a language nobody has measured.
"""


def find_over_exports(
    after: ProjectSnapshot,
    affected_files: Collection[str],
    severity: Severity = "warning",
    ignore: Sequence[str] = (),
) -> list[Finding]:
    """Report each affected file holding one definition for exactly one dependant (req 4.1).

    ``affected_files`` is the change's own file set, so a file the change did not touch is
    never reported however lonely it is; ``ignore`` is a list of path globs in the language
    ``[project] include`` speaks (req 4.2). Findings come back in path order.
    """
    records = _file_records(after)
    binds_variables = {definition.path for definition in after.definitions}
    dependants = _dependants(after.file_edges)
    findings: list[Finding] = []
    for path in sorted(set(affected_files)):
        sole = _sole_dependant(dependants.get(path, frozenset()))
        if sole is None or not _holds_one_export(records.get(path), path in binds_variables):
            continue
        if matching_pattern(ignore, path) is None:
            findings.append(_over_export_finding(path, sole, severity))
    return findings


def _file_records(after: ProjectSnapshot) -> dict[str, EntityRecord]:
    """The snapshot's file records, by path."""
    return {
        record.key.path: record for record in after.entities.values() if record.key.scope == "file"
    }


def _dependants(edges: Sequence[DepEdge]) -> dict[str, set[str]]:
    """The distinct files depending on each file, self-references excluded.

    Distinct files, because Understand emits one edge per pair *and reference kind*: counting
    edges would make a file imported twice from one module look like a shared one.
    """
    inbound: dict[str, set[str]] = {}
    for edge in edges:
        if edge.src != edge.dst:
            inbound.setdefault(edge.dst, set()).add(edge.src)
    return inbound


def _sole_dependant(sources: Collection[str]) -> str | None:
    """The one file depending on this one, or ``None`` for none and for more than one."""
    return next(iter(sources)) if len(sources) == 1 else None


def _holds_one_export(record: EntityRecord | None, binds_variables: bool) -> bool:
    """Whether this file declares exactly one name and binds nothing else at module level."""
    if record is None or binds_variables:
        return False
    counts = [record.metrics.get(metric) for metric in DECLARATION_METRICS]
    return None not in counts and sum(count or 0.0 for count in counts) == 1


def _over_export_finding(path: str, dependant: str, severity: Severity) -> Finding:
    """One file holding a single definition for a single importer; the pipeline adds the hint."""
    return Finding(
        kind="structural",
        rule=_OVER_EXPORT_RULE,
        scope="file",
        path=path,
        # The number the rule is about: how many files depend on this one. Always 1.0 by
        # construction, and carried all the same so the JSON and SARIF outputs say what was
        # counted rather than leaving a reader to infer it from the message.
        value=1.0,
        limit=None,
        limit_source="rule",
        severity=severity,
        blocking=severity == "error",
        message=(
            f"{path} defines one routine or class and nothing else, and {dependant} is the "
            f"only file in this project that depends on it"
        ),
        details={"dependant": dependant},
    )
