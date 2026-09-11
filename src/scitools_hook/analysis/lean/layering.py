"""The layering rules: files and routines that add a hop without adding a boundary.

**What the whole ``analysis.lean`` package is, recorded here rather than in its initialiser.**
Six rules and a delta, each a pure function over a
:class:`~scitools_hook.models.snapshot.ProjectSnapshot` and the affected set, in the shape the
structural rules of :mod:`scitools_hook.analysis.structure` already have. The package exists
because six rule modules and a delta are not one module, and because the family shares one
configuration section, one severity convention -- ``Severity | None``, where ``None`` is off --
and one promise: it imports ``config`` and ``models`` and nothing else. The promise has exactly
one exception, named in the design and taken by :mod:`.net` alone,
``analysis.ratchet.pair_changed_signatures``, which joins a routine whose parameter list
changed to the key it had before; that join already exists, is the only identity work in this
layer that is not local to one snapshot, and a second copy of it would drift.

*Why it is here and not in* ``analysis/lean/__init__.py``, *because it is a finding about the
gate itself.* ``analysis.structure.coupling.namespace_targets`` drops a dependency on a package
initialiser that holds no code, on the stated ground that "importing through it couples the
importer to nothing" -- and it reads emptiness off ``CountLineCode == 0``. Understand reports a
**multi-line module docstring as code lines**, so an initialiser whose whole content is a
paragraph of prose is charged as a real dependency while every one-line-docstring initialiser
in this package is correctly dropped. Measured on Build 1262 while task 2.6 was landing:
``runner/lean.py`` names six modules and was charged eight dependencies against
``max_new_dependencies_per_file = 7``, and shortening this package's initialiser to one line --
with no other change anywhere -- took the figure to seven. The defect belongs to the base gate
and is reported rather than absorbed; the prose belongs with the code it describes, which is
here, where the family's other cross-rule conventions are already recorded.

This module holds three rules -- the over-exporting file, the pass-through routine and the
single-implementation abstraction -- because all three answer one question: does this piece of
structure earn the indirection it costs?

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

**Pass-through.** A routine with exactly one project caller, exactly one project callee and a
body too short to hold anything else forwards a call and adds a name. Requirement 2.2 is the
half that keeps the rule honest: one caller alone is **not** a finding, because a routine with
one caller and a body of its own is the decomposition this tool's own hints ask for. What
makes a pass-through is the conjunction, and the statement budget is what says "too short to
hold anything else" -- ``return other(x)`` is two statements on Understand's count, which is
where :data:`DEFAULT_MAX_STATEMENTS` comes from.

*Requirement 2.1 was amended by this task, and the reason is a fact about the snapshot rather
than a preference.* The criterion asked the finding to name **the caller and the callee**;
:attr:`~scitools_hook.models.snapshot.LeanFacts.callers` is a *count*, so no rule can recover a
name that was never kept. The finding therefore names the callee alone, and three measurements
decided that rather than adding a sixth fact:

* The caller is not part of the remedy the tag form states. Requirement 8.1 asks the hint to
  say what to cut and what replaces it: the routine is what to cut and its single callee is
  what replaces it. The caller is the edit site, which an agent reaches from the routine's own
  long name.
* A named caller would be a claim this rule may not make. ``worker_lean._project_callers``
  counts distinct project **routines**, and Python records a call made outside any routine
  against the *file*, which is not one. A routine called once from a routine and once from
  module scope therefore measures one caller and satisfies this rule's predicate -- the error
  task 3.1 recorded and accepted -- so a finding naming that routine as "the only caller"
  would be wrong about the one thing it asserted.
* Carrying the name would not have fixed that. ``callers`` and a caller's long name come from
  the same filtered list, so the module-scope call site is invisible either way: the undercount
  lives in what counts as a caller, not in count-versus-name. Widening it changes this rule's
  predicate and belongs with requirement 2.6's floor measurement, not with the finding's
  wording.

**The floors, and why this rule's direction makes them worse than the dead-code rules'**
(requirement 2.6, and requirement 1.8 as amended). The two floors are
:class:`~scitools_hook.analysis.lean.dead.Trust`'s, asked through the same
:class:`~scitools_hook.analysis.lean.dead.TrustGate`, because a second gate would be a second
place for the same two questions to be answered differently. An unresolved call site *removes*
a caller, and this rule reports on there being exactly one: a routine with three callers of
which two did not resolve measures one and qualifies. A partly resolved call graph therefore
does not make this rule quiet, it makes it wrong, which is what requirement 2.6 says in as
many words.

**Single implementation.** A class with exactly one derived class and no project referrer
other than that derived class is an abstraction kept for a second implementation that never
arrived. Requirement 3.2 is the half that decides the shape of the walk: the commit worth
telling is usually the one that adds the *derived* class, not the one that wrote the base, so
this rule walks every class the snapshot records and reports a base whose own key **or** whose
single derived class is in the change. A rule reading only the affected records would be
silent on exactly that commit.

*This rule ships with no floor, and only one of requirement 1.8's two floors has actually been
argued away.* Requirement 2.6 gives the pass-through rule both floors in as many words and
requirement 3 names neither, so nothing is *specified* here. On the merits the two come apart,
and the earlier version of this note answered one of them and claimed both:

* The **call-resolution** floor is not the bounding quantity. ``referrers`` counts use, type
  and inheritance references rather than call edges, so a partly resolved *call graph* does
  not bound it. That much task 4.2 established.
* The **accuracy** floor plausibly is. Requirement 1.8's amendment says analysis accuracy
  bounds whether a file was read at all, and that is the failure behind the sixteen module
  bindings this repository reports unreferenced while every one of them is read: their use
  sites sit in regions the analysis errored on. ``referrers == 0`` is an absence-of-references
  claim of exactly that shape, so a low-accuracy run can produce it for a class that has
  users. Nothing measured here refutes it, and no corpus has paired an accuracy figure with a
  false-positive count for this rule.

So: the rule ships **off** (req 3.4) and asks no gate, the accuracy question is open rather
than settled, and task 6.4 owns the measurement that would decide whether a gate belongs here.
Task 4.2 recorded the concern rather than shipping a guard nothing measured -- and records
which half of it is still a guess.

**Why the over-export rule needs no "unavailable" answer while its siblings do.** It reads
file metrics,
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

*The three keys the siblings take, checked against that namespace before they were named.*
``forwards_to`` is the pass-through rule's own and is a scalar long name, the same shape and
the same meaning as :attr:`~scitools_hook.models.snapshot.LeanFacts.forwards_to`. ``longname``
is ``structure.unused_routine``'s, a string, and both rules use it for the entity the finding
is about. ``derived_class`` is new, and it is deliberately **not** ``derived``: ``LeanFacts``
publishes ``derived`` as a *list* of long names, and a details key repeating a model field's
name while narrowing it to the single member this rule reports is the same trap
``depended_on_by`` set above, one namespace over.

**What this module's own gate says about the two rules above, recorded rather than configured
away.** Measured with ``scitools-hook check --worktree`` on Build 1262 while task 4.2 landed
and **re-measured on the shipped file** after its review, all of it non-blocking:

* ``_forwarder_fact`` scores ``Essential`` 5 and ``_reported_forwarder`` 5, against a warning
  limit of 4. Both are one guard per statement -- four facts read three-state in the first,
  the gate plus four conditions in the second -- and every one of them is separately
  mutation-tested, in its *position* as well as its presence. The battery for this task is 43
  mutants over both rules and the four predicates they call. The first 42 are quoted as they
  first ran: 40 killed and **two survivors**, both of them fixtures where the right and the
  wrong answer coincided -- a walk over every recorded entity rather than every recorded class,
  and a finding publishing the statement *budget* where the measured count belongs; two tests
  were added for those. The forty-third is this task's third-round finding, that
  ``_reported_forwarder``'s **fourth** post-gate condition had no position test: hoisting
  ``name_excused`` above ``gate.allows`` passed the whole suite while dropping requirement
  2.6's once-per-run sentence entirely. All four positions in that chain were re-run against
  the shipped file on that round and each died to one dedicated test, the new one being
  ``test_the_floor_is_asked_before_the_ignore_list_is_consulted``.
  **Every run clears ``__pycache__`` first**, and that is not hygiene: a same-size reorder
  restored by copying the original back over it is served from stale bytecode, so without the
  clear every order-swap mutant reads as killed. The first round of this task reported a clean
  sweep it had not measured, for exactly that reason. Several mutants die *only* because the
  guards are separate statements, since a short circuit records no branch arc -- the four that
  demote ``gate.allows`` below one of the four conditions after it are the clearest. Fusing one
  pair into an ``and`` takes both routines under the limit and deletes the arc, and splitting
  either routine again would be the accounting shape task 1.9 refused. The deviation is the
  honest answer.
* The file's fan-out rose from 6 to 8. Six of the eight were already here; the two added are
  :mod:`scitools_hook.analysis.lean.dead` and the ``analysis`` initialiser Python executes on
  the way to it, and they are the price of asking that module for the family's shared
  primitives instead of writing a second copy of the two floors, the stale-cache sentence and
  the ignore-list matcher.
* ``dead.TrustGate`` is reported as a class whose fan-out "rose from 0 to 1", and the finding
  names the 1: ``models.snapshot.CallResolution``, which is the edge ``_TrustGate`` already had
  through its own ``Mapping[str, CallResolution]`` field. What moved is the node's *name*, not
  the class. ``analysis.structure.fan`` compares the two sides by ``EntityKey``, ``_TrustGate``
  became ``TrustGate`` so this module could ask it, and ``_Fan.count`` answers 0 for a node the
  before graph does not hold -- so the ratchet is comparing a renamed class against a node that
  never existed. That is what the measurement says; "nothing about the class changed" was the
  claim and this is the evidence for it.
* This file's ``RatioCommentToCode`` fell from 1.97 to 1.37 as two rules' worth of
  code landed under one docstring's worth of prose. It is still more than thirteen times the
  minimum of 0.1, and the finding is the ratchet noting the direction rather than a limit being
  crossed.

*Keys reserved by the same reasoning, recorded here because this is where the family looks.*
A bare ``lines`` belongs to the rule that names a **line range** -- the duplicate-block rule,
whose finding is about where in a file the repetition sits -- so a rule carrying a *count* of
lines does not take it: ``net`` publishes ``net_lines`` and ``net_routines`` beside the
statement figure it puts in ``Finding.value``. The same applies to any later rule wanting to
publish a number of lines: the plural noun is the location, not the size.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from typing import Final, NamedTuple

from scitools_hook.analysis.lean.dead import (
    UNMEASURED,
    LeanOutcome,
    Trust,
    TrustGate,
    affected_records,
    compiled_patterns,
    name_excused,
    unavailable,
)
from scitools_hook.config import models
from scitools_hook.config.models import LeanRules, Severity, matching_pattern
from scitools_hook.models.findings import Finding, structure_rule
from scitools_hook.models.snapshot import (
    DepEdge,
    EntityKey,
    EntityRecord,
    ProjectSnapshot,
)

_OVER_EXPORT_RULE = structure_rule("over_export")
PASS_THROUGH_RULE: Final = structure_rule("pass_through")
SINGLE_IMPLEMENTATION_RULE: Final = structure_rule("single_implementation")

STATEMENT_METRIC: Final = models.STATEMENT_METRIC
"""The metric that says how much body a routine has, and the one requirement 2.1 budgets.

Understand's logical-line count, which is what the whole family means by a line: ``net``
sums it and ``LinesPerStatement`` divides by it. A routine whose record does **not** carry
it is unmeasured rather than empty and is not judged, for the reason
:data:`DECLARATION_METRICS` records one metric family over -- a rule reading it with a
default of zero reports every routine whose body was never counted.

Read from ``config.models`` rather than spelled here, because the extractor asks for the
metric under the same name whenever the rule is on (``LeanRules.wants_statements``): a
second spelling would let the request and the reader drift apart, and the reader would then
find no count on any record and judge nothing.
"""

DEFAULT_MAX_STATEMENTS: Final = LeanRules().pass_through_max_statements
"""The shipped statement budget, asked of the settings section that owns it.

Requirement 2.1 wants the budget configurable and ``[lean]`` is where an operator configures
it, so this default is read from the model rather than restated: a second literal ``2`` here
would be a second decision, and the measurement behind the first one
(``LeanRules.pass_through_max_statements``) is recorded there.
"""

_NO_ROUTINE_FACTS: Final = "call measurement for every affected routine"
_NO_CLASS_FACTS: Final = "reference measurement for every class it recorded"

_JUDGED: Final = "judged"
"""What these two rules decide, for the sentence :func:`dead.unavailable` writes once.

The dead-code rules say "judged unused" because a name is the thing they judge. These two
judge a *shape* -- a hop that adds no behaviour, an abstraction with one implementation --
and there is no adjective that fits both, so they say what was not done rather than what was
not found. ``unavailable`` takes the word as a required argument for the reason task 4.2's
review found: with a default, every call site could be deleted and three other rules'
user-facing sentences would change with the suite green.
"""

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


class PassThroughLimits(NamedTuple):
    """What the operator configured about *which* routines the pass-through rule reports.

    The statement budget and the ignore list travel together because they are one decision in
    two halves -- how short a body has to be before it counts as forwarding, and which
    routines forward on purpose -- and both are read from the same two lines of ``[lean]``.
    The severity stays a parameter of its own, as on every sibling rule: it says how loud the
    answer is rather than which routines qualify.

    They are one object rather than two parameters because this project's own gate said so:
    with both spelled out, ``find_pass_through`` took six parameters against a maximum of five
    and the check on the worktree exited 1. The grouping the finding asked for is the one
    above, which is also the one that reads.
    """

    max_statements: int = DEFAULT_MAX_STATEMENTS
    ignore: Sequence[str] = ()


PASS_THROUGH_DEFAULTS: Final = PassThroughLimits()
"""The shipped budget with no routine excused; a named default, since B008 refuses a call."""


class _Forwarder(NamedTuple):
    """One affected routine whose four call facts were all measured."""

    record: EntityRecord
    longname: str
    callers: int
    callees: int
    forwards_to: str | None
    overrides: bool
    statements: float | None


class _Base(NamedTuple):
    """One recorded class whose derived list and referrer count were both measured."""

    record: EntityRecord
    longname: str
    derived: tuple[str, ...]
    referrers: int


def find_pass_through(
    after: ProjectSnapshot,
    affected: Collection[EntityKey],
    severity: Severity = "warning",
    limits: PassThroughLimits = PASS_THROUGH_DEFAULTS,
    trust: Trust = UNMEASURED,
) -> LeanOutcome:
    """One finding per affected routine that forwards one call and adds nothing (req 2.1).

    ``affected`` is the change's own entity set; ``limits`` carries the statement budget and
    the ignore list, a list of regular expressions over routine long names matched anywhere in
    the name, the shipped one covering entry points and test functions (req 2.3). ``trust``
    carries requirement 1.8's two floors, which requirement 2.6 gives this rule for the reason
    the module docstring measures. The finding names the callee and not the caller, which is
    requirement 2.1 as this task amended it.
    """
    records = affected_records(after, affected, "routine")
    # **No empty-change guard here, and its two siblings show why one is not needed.** A
    # change touching no routine walks no record, so `_forwarder_facts` answers an empty list
    # rather than `None`, the gate is never asked and `messages` is empty: the run is told
    # nothing without a guard saying so. `dead.find_unused_parameters` needs one because it
    # also checks a run-wide fact -- the declaring-class tally -- which would report itself
    # missing to a change with nothing to judge, and `find_single_implementations` below
    # needs one because its walk is the whole snapshot rather than the change. A guard added
    # here for symmetry would be one no test could fail, which is worse than none.
    routines = _forwarder_facts(records)
    if routines is None:
        return unavailable(PASS_THROUGH_RULE, _NO_ROUTINE_FACTS, _JUDGED)
    gate = TrustGate(after, PASS_THROUGH_RULE, trust)
    excused = compiled_patterns(limits.ignore)
    findings = [
        _pass_through_finding(item, limits.max_statements, severity)
        for item in routines
        if _reported_forwarder(item, gate, excused, limits)
    ]
    return LeanOutcome(findings=findings, unavailable=gate.messages)


def find_single_implementations(
    after: ProjectSnapshot,
    affected: Collection[EntityKey],
    severity: Severity = "warning",
    ignore: Sequence[str] = (),
) -> LeanOutcome:
    """One finding per class kept for the single implementation it ever got (req 3.1, 3.2).

    Evaluated over **every** class the snapshot records rather than over the affected ones,
    because requirement 3.2 asks for the base on the commit that adds its only derived class
    and that commit touches the derived class alone. ``ignore`` is a list of regular
    expressions over class long names; the shipped list excuses exception bases (req 3.3).
    """
    touched = {key.longname for key in affected if key.scope == "class"}
    if not touched:
        return LeanOutcome(findings=[])
    classes = _base_facts(after)
    if classes is None:
        return unavailable(SINGLE_IMPLEMENTATION_RULE, _NO_CLASS_FACTS, _JUDGED)
    excused = compiled_patterns(ignore)
    findings = [
        _single_implementation_finding(item, severity)
        for item in classes
        if _reported_base(item, touched, excused)
    ]
    return LeanOutcome(findings=findings)


def _forwarder_facts(
    records: Sequence[tuple[EntityKey, EntityRecord]],
) -> list[_Forwarder] | None:
    """Every affected routine with its call facts, or ``None`` when one was not measured."""
    found: list[_Forwarder] = []
    for key, record in records:
        item = _forwarder_fact(key, record)
        if item is None:
            return None
        found.append(item)
    return found


def _forwarder_fact(key: EntityKey, record: EntityRecord) -> _Forwarder | None:
    """One routine's measured call facts, or ``None`` where any of the three is absent.

    Three separate refusals, one per fact, because each is a different way to be wrong: no
    facts at all is a snapshot recorded before the rule existed, an unmeasured ``callers``
    read as zero silences the rule on the routine it is about, and an unmeasured ``overrides``
    read as ``False`` reports the forwarding override requirement 2.3 excuses.

    ``forwards_to`` is deliberately **not** one of them: the worker sets it exactly when
    ``callees == 1``, so its absence is the ordinary answer for a routine with a body, and
    refusing on it would report every measured run as unavailable. The statement count is not
    one either -- a routine whose ``CountStmt`` was never taken is a record this rule does not
    judge rather than a run it cannot judge, which is the treatment ``_holds_one_export``
    gives the declaration counts one rule over.
    """
    facts = record.lean
    if facts is None:
        return None
    if facts.callers is None:
        return None
    if facts.callees is None:
        return None
    if facts.overrides is None:
        return None
    return _Forwarder(
        record=record,
        longname=key.longname,
        callers=facts.callers,
        callees=facts.callees,
        forwards_to=facts.forwards_to,
        overrides=facts.overrides,
        statements=record.metrics.get(STATEMENT_METRIC),
    )


def _reported_forwarder(
    item: _Forwarder,
    gate: TrustGate,
    excused: Sequence[re.Pattern[str]],
    limits: PassThroughLimits,
) -> bool:
    """Whether this routine may be reported: the floors, the shape, the body, the list.

    **The gate is asked first and asked for every candidate**, whatever the facts say, because
    a run below either floor was *not evaluated* -- that is the sentence requirement 2.6 wants
    said, and a rule that consulted the facts first would fall silent about an analysis it
    never judged, on the commit where nothing happened to qualify. ``dead._judged`` takes the
    same order for the same reason. The four guards after it commute, and stand in the order
    requirement 2.1 reads in.

    *"First" needs four tests, not one -- what the reviews of task 4.2 found.* Each guard below
    can be lifted above the gate on its own, and each lift is its own requirement 2.6 violation:
    ``test_the_floor_is_asked_before_the_facts_are_believed`` pins the gate above ``overrides``,
    ``..._before_the_shape_is_read`` above ``_forwards_one_call``,
    ``..._before_the_body_is_measured`` above ``_within_budget``, and
    ``..._before_the_ignore_list_is_consulted`` above ``name_excused``.

    One statement per guard rather than one fused ``and``, here and in the two predicates
    below, because branch coverage records no arc for a short circuit and a guard folded into
    a boolean expression can be deleted with the suite green. Splitting the chain into named
    halves is what keeps that discipline affordable: with all seven guards here, this project's
    own gate scored the routine ``Essential`` 8 against a limit of 4, the file's worst.
    """
    if not gate.allows(item.record.language):
        return False
    if item.overrides:
        return False
    if not _forwards_one_call(item):
        return False
    if not _within_budget(item, limits.max_statements):
        return False
    return not name_excused(excused, item.longname)


def _forwards_one_call(item: _Forwarder) -> bool:
    """One caller, one callee, and a name for that callee -- the shape requirement 2.1 names.

    **The two callee facts are checked against each other, and each guard is pinned by the
    record where they disagree.** ``worker_lean.routine_facts`` keeps the invariant that
    ``forwards_to`` is set exactly when ``callees == 1``, so it produces neither of those
    records -- which is the point: the count is the measurement requirement 2.2 is written
    about, and a rule trusting the name alone would follow a later worker that gave
    ``forwards_to`` a second meaning. A record claiming one callee and carrying no name for it
    cannot state its own remedy either, and would report a fix reading "call None".
    """
    if item.callers != 1:
        return False
    if item.callees != 1:
        return False
    return item.forwards_to is not None


def _within_budget(item: _Forwarder, max_statements: int) -> bool:
    """Whether the body is short enough to be forwarding, and was measured at all (req 2.2).

    An unmeasured ``CountStmt`` is not a body of zero statements, which is the treatment
    :data:`DECLARATION_METRICS` gets one rule over: a record the analysis did not measure is
    not judged rather than judged as empty.
    """
    if item.statements is None:
        return False
    return item.statements <= max_statements


def _base_facts(after: ProjectSnapshot) -> list[_Base] | None:
    """Every recorded class with its facts, or ``None`` when one of them was not measured.

    **Every class, not the affected ones**, and the reason is requirement 3.2: the base this
    rule reports may sit in a file the change never touched, so a walk narrowed to the change
    would answer "nothing" on the commit the finding is for. It follows that an unmeasured
    class anywhere is a class this rule read, and one is enough to make the run unavailable.
    """
    found: list[_Base] = []
    for key, record in sorted(
        ((key, record) for key, record in after.entities.items() if key.scope == "class"),
        key=lambda pair: (pair[0].path, pair[0].longname),
    ):
        item = _base_fact(key, record)
        if item is None:
            return None
        found.append(item)
    return found


def _base_fact(key: EntityKey, record: EntityRecord) -> _Base | None:
    """One class's measured facts, or ``None`` where any of the three is absent.

    Three separate refusals, one per fact, as ``dead._class_fact`` takes them and for the same
    reasons: no facts at all is a snapshot recorded before the rule existed, an unmeasured
    ``derived`` read as an empty list makes every base class look like a leaf, and an
    unmeasured ``referrers`` read as zero reports every base with a subclass and a user.
    """
    facts = record.lean
    if facts is None:
        return None
    if facts.derived is None:
        return None
    if facts.referrers is None:
        return None
    return _Base(record, key.longname, tuple(facts.derived), facts.referrers)


def _reported_base(
    item: _Base, touched: Collection[str], excused: Sequence[re.Pattern[str]]
) -> bool:
    """Whether this class may be reported: one implementation, no other user, in the change.

    The first guard has to come first -- the affectedness test below reads ``derived[0]``, and
    a class with no derived class has no such member. The rest carry no message of their own,
    so their order is a reading order rather than a decision, unlike the pass-through rule's.
    """
    if len(item.derived) != 1:
        return False
    if item.referrers != 0:
        return False
    if item.longname not in touched and item.derived[0] not in touched:
        return False
    return not name_excused(excused, item.longname)


def _pass_through_finding(item: _Forwarder, max_statements: int, severity: Severity) -> Finding:
    """One routine that forwards a single call, reported where it is declared (req 2.1)."""
    return Finding(
        kind="structural",
        rule=PASS_THROUGH_RULE,
        scope="routine",
        entity=item.record.ref,
        path=item.record.key.path,
        line=item.record.ref.line,
        # The statement count against the budget it was judged by, so a reader can see both
        # the measurement and the number an operator would raise to silence it.
        value=item.statements,
        limit=float(max_statements),
        limit_source="rule",
        severity=severity,
        blocking=severity == "error",
        message=(
            f"exactly one project routine calls {item.longname}, and its body does nothing "
            f"but call {item.forwards_to}; the hop adds a name and no behaviour"
        ),
        details={"forwards_to": item.forwards_to, "longname": item.longname},
    )


def _single_implementation_finding(item: _Base, severity: Severity) -> Finding:
    """One base class with a single implementation, reported at the base (req 3.1)."""
    return Finding(
        kind="structural",
        rule=SINGLE_IMPLEMENTATION_RULE,
        scope="class",
        entity=item.record.ref,
        path=item.record.key.path,
        line=item.record.ref.line,
        # How many classes derive from it. Always 1.0 by construction, carried for the same
        # reason the over-export rule carries its dependant count: the JSON and SARIF outputs
        # say what was counted rather than leaving a reader to infer it.
        value=1.0,
        limit=None,
        limit_source="rule",
        severity=severity,
        blocking=severity == "error",
        message=(
            f"{item.longname} has exactly one derived class, {item.derived[0]}, and nothing "
            f"else in this project references it"
        ),
        details={"derived_class": item.derived[0], "longname": item.longname},
    )
