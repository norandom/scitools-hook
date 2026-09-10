"""Dead code beyond routines: a parameter, a class, a module binding nothing uses (req 1.1-1.9).

**These are the most dangerous rules in the family, and the danger is measured rather than
suspected.** Two failures were recorded, and they are failures of two different things:

* **facdrone, 2026-09-10.** 417 source files, about 101 800 lines, ``und analyze -accuracy``
  26%. The naive predicate -- "no project reference, therefore dead" -- answers **830 routines
  and roughly 6160 lines**, and is wrong nearly every time: the names are ``.load``,
  ``.decide``, ``.commit``, a dashboard callback, a CLI subcommand. That codebase satisfies
  its interfaces *structurally*, so an implementation holds no reference at all to the
  interface it satisfies, and exactly **one** of the 830 carried an override reference. Its
  call resolution was never measured.
* **this repository, 2026-09-10**, accuracy 19%, measured during task 3.3's review: every one
  of the **sixteen** module bindings in ``src/`` that the snapshot answers
  ``referenced: false`` for is in fact read. Understand recorded no use reference because the
  use sites sit inside regions its analysis errored on, and a controlled two-file probe shows
  the same constants resolving correctly in a clean parse. A **hundred per cent**
  false-positive rate for the unused-variable rule, on the tool's own source.

The failure mode is therefore not "the rule misses something". It is "the rule tells an agent
to delete working code, at scale, confidently". Requirements 1.8 and 1.9 exist because of
those two measurements, and this module builds both guards before it builds a finding.

**Two floors, because two quantities bound two different failures** (requirement 1.8, as
amended). Neither covers the other's evidence, so :class:`Trust` carries both and the gate
refuses when *either* is below its own, saying which one stopped it and at what measured
value.

* **Call resolution** -- :attr:`~scitools_hook.models.snapshot.CallResolution.internal`, the
  share of a language's call sites that became an edge of this project's call graph -- bounds
  whether the Gate knows *what calls what*. That is the 830-routine failure on a structurally
  typed codebase. It is per language, because the substrates differ: Python and C++ do not
  resolve alike on one build, and one averaged figure would let the weaker hide behind the
  better. This repository measures 9 894 resolved of 22 974 Python call sites, **43%**
  (research.md); no corpus has had this figure paired with a false-positive count.
* **Analysis accuracy** -- what ``und analyze -accuracy`` reports, the share of parsed files
  with no error or warning -- bounds whether a file was *read at all*. That is the failure
  behind all sixteen module bindings above, and it is the one the resolution floor covers
  least: a binding's use site inside a region the analysis errored on is invisible however
  well the call graph resolved elsewhere. It is per **side** rather than per language, which
  is the only shape Understand reports it in, so it gates the whole run.

*Where the accuracy figure comes from, because it is not on the snapshot.*
:class:`~scitools_hook.models.snapshot.ProjectSnapshot` carries none: the figure lives on
``AnalyzeResult.accuracy``, is remembered across warm runs by ``SyncState.accuracy``, and
reaches this layer **only as an argument** -- exactly as it reaches
:func:`scitools_hook.analysis.accuracy.evaluate_accuracy`, which the runner feeds from
``runner.check._figures``. So :class:`Trust` is how it arrives here too, and a caller that
passes none gets silence and a sentence saying so, never findings.

*Why an absent accuracy refuses here while it raises nothing there.*
:mod:`scitools_hook.analysis.accuracy` records that "a missing figure is not a bad one" and
makes no finding without one -- correctly, because there the accuracy **is** the subject of
the finding. Here it is the *licence* to make a finding about something else, and a licence
nobody produced is not a licence.

**The interface-method exclusion (requirement 1.9).** A method name declared by
:data:`INTERFACE_DECLARERS` or more project classes is an interface method under structural
typing, whether or not any inheritance edge exists, and neither it nor its parameters is
reported. :attr:`~scitools_hook.models.snapshot.ProjectSnapshot.method_declarations` carries
the tally; the threshold is applied here, as every other threshold over the worker's outputs
is. The exclusion is applied by **short name**, so a plain function sharing a name with two
classes' methods is excused too. That is a false negative, taken deliberately: telling a
method from a function by Understand's kind string is a language-by-language guess, and for a
rule whose false positives delete working code the safe direction is silence. How much it
excuses is task 6.4's measurement.

**Three states, never two.** Every fact these rules read is ``True``, ``False`` or ``None``
for "the worker was not asked", and a ``None`` on **any** affected record of a rule's scope
yields that rule's unavailable message and nothing else (requirement 1.6) -- not a partial
answer over the records that were measured, because a run that could not measure one of them
did not measure the run. The reachable case is a warm analysis cache recorded before the rule
was switched on.

**Deleted entities need no rule to exclude them** (requirement 1.7): all three read the after
side, where an entity the change deleted is absent.

*The* ``details`` *keys, in the flat namespace the whole* ``structure.`` *category shares.*
``parameter`` is new and is this rule's own. ``longname`` is
``structure.unused_routine``'s, a string, and the class rule uses it for the same thing.
``definition`` is ``structure.duplicate_definition``'s, the name of a module-level binding,
and the variable rule reports the same kind of thing under it -- a shared key keeps the type
it already has (see :mod:`scitools_hook.analysis.lean.layering` for what learning that cost).
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from typing import Final, NamedTuple

from scitools_hook.config.models import Severity
from scitools_hook.models.findings import Finding, structure_rule
from scitools_hook.models.snapshot import (
    CallResolution,
    Definition,
    EntityKey,
    EntityRecord,
    ProjectSnapshot,
)

PARAMETER_RULE: Final = structure_rule("unused_parameter")
CLASS_RULE: Final = structure_rule("unused_class")
VARIABLE_RULE: Final = structure_rule("unused_variable")

DEFAULT_RESOLUTION_FLOOR: Final = 0.75
"""Placeholder. **No rate at which these rules become sound has been measured.**

It is deliberately set where nothing yet measured can reach it, and it is not a calibration:
the two corpora where the predicate was measured wrong were measured by *accuracy*, not by
call resolution, and the one call-resolution figure that exists (43% here) has never been
paired with a false-positive count. Requirement 1.10's two-repository measurement, which task
6.4 owns, is what re-derives this number; until then a rule that reaches its floor would be a
rule whose floor was chosen to make it speak rather than to make it right.

Silence is not absence: below the floor the rules say what stopped them and at what measured
value, which is a different product from a rule that is simply off.
"""

DEFAULT_ACCURACY_FLOOR: Final = 0.75
"""Placeholder, on the same terms as :data:`DEFAULT_RESOLUTION_FLOOR` and for the same reason.

Both corpora that produced a false-positive count report an accuracy far below it -- 19% here
and 26% on facdrone -- so this floor is silent on both, but no corpus has been measured where
these rules are right, and no rate has been shown to be the rate at which they become so.
Task 6.4 re-derives it from two repositories.
"""

INTERFACE_DECLARERS: Final = 2
"""Declaring classes at which a method name is an interface method (requirement 1.9)."""

UNKNOWN_LANGUAGE: Final = "unknown"
"""The language of a file no record names in a project analysed as more than one.

Never a key of ``call_resolution``, so the gate refuses it -- which is the point: a wrong
language would gate the finding on a substrate the entity was not written in.
"""

_NO_ROUTINE_FACTS: Final = "reference measurement for every affected routine"
_NO_TALLY: Final = "declaring-class tally that sees an interface method without an edge"
_NO_CLASS_FACTS: Final = "reference measurement for every affected class"
_NO_BINDING_FACTS: Final = "reference measurement for every module binding the change touched"


class Trust(NamedTuple):
    """How far this run may be trusted about its own analysis, and the floors it is held to.

    ``accuracy`` is the **after side's** ``und analyze -accuracy`` figure, or ``None`` for
    "this run measured none" -- which is what a 6.5 install reports, what a build that was
    not asked reports, and what a caller that has not wired the figure through passes. All
    three refuse, because the figure is the licence to say a name is unused and a licence
    nobody produced is not a licence. That is also why the default is ``None``: a rule wired
    up but not given its measurement must fall silent and say so, not report.

    The two floors default to the placeholders this module documents. Task 4.3 owns making
    both configurable and passing the measured figure in.
    """

    accuracy: float | None = None
    resolution_floor: float = DEFAULT_RESOLUTION_FLOOR
    accuracy_floor: float = DEFAULT_ACCURACY_FLOOR


UNMEASURED: Final = Trust()
"""The trust a caller that wired nothing through passes: no accuracy, both placeholders.

Every rule refuses on it and says so. It is a module-level singleton rather than a
``Trust()`` written into three signatures because ruff's B008 refuses a call in a default --
and the singleton is the better shape anyway: one object, one docstring, one place to read
what "not measured" means here.
"""


class LeanOutcome(NamedTuple):
    """What one lean rule found, and every reason it could not look.

    ``unavailable`` carries **one message per reason**, not one per entity: a rule that could
    not be evaluated at all has one, a run below the accuracy floor has one, and a rule gated
    by the call-resolution floor has one for each language it refused. The step that calls the
    rules prints them once per run.
    """

    findings: list[Finding]
    unavailable: tuple[str, ...] = ()


class _Routine(NamedTuple):
    """One affected routine whose facts were all measured."""

    record: EntityRecord
    longname: str
    overrides: bool
    parameters: tuple[str, ...]


class _Class(NamedTuple):
    """One affected class whose reference flag was measured."""

    record: EntityRecord
    longname: str
    referenced: bool


class _TrustGate:
    """Requirement 1.8's two floors, asked once per entity and answered once per reason.

    It records the reason it refused rather than raising or returning it, because the caller
    needs the refusal at the end of the walk and the decision in the middle of it.

    **The accuracy floor is decided once and the call-resolution floor once per language**,
    which is the shape of the two measurements: accuracy is reported per side, resolution per
    language. An accuracy refusal therefore ends the matter -- it is one sentence about the
    whole run, and a list of languages underneath it would say nothing more.

    **Nothing is said about a rule that was never asked.** A change touching no entity of a
    rule's scope leaves ``allows`` uncalled, and a floor message then would be a complaint
    about a judgement nobody wanted.
    """

    def __init__(self, after: ProjectSnapshot, rule: str, trust: Trust) -> None:
        self._resolution: Mapping[str, CallResolution] = after.call_resolution
        self._rule = rule
        self._trust = trust
        self._unread = _accuracy_refusal(rule, trust)
        self._refused: dict[str, str] = {}
        self._asked = False

    def allows(self, language: str) -> bool:
        """Whether a finding about this language's code is a measurement at all."""
        self._asked = True
        if self._unread is not None:
            return False
        share = self._share(language)
        if share is None:
            self._refused[language] = _unmeasured_resolution(self._rule, language, self._trust)
            return False
        if share < self._trust.resolution_floor:
            self._refused[language] = _below_resolution(self._rule, language, share, self._trust)
            return False
        return True

    @property
    def messages(self) -> tuple[str, ...]:
        """Every floor that stopped this rule, in a fixed order so a run reads the same twice."""
        if not self._asked:
            return ()
        if self._unread is not None:
            return (self._unread,)
        return tuple(self._refused[language] for language in sorted(self._refused))

    def _share(self, language: str) -> float | None:
        """The share of this language's call sites that became a project call edge."""
        found = self._resolution.get(language)
        if found is None:
            return None
        return found.internal


def find_unused_parameters(
    after: ProjectSnapshot,
    affected: Collection[EntityKey],
    severity: Severity = "warning",
    ignore: Sequence[str] = (),
    trust: Trust = UNMEASURED,
) -> LeanOutcome:
    """One finding per parameter of an affected routine that the routine never reads (1.2).

    ``affected`` is the change's own entity set; ``ignore`` is a list of regular expressions
    over **parameter names**, which is where requirement 1.4's receiver exclusion lives -- the
    worker counts ``self`` unused, because it is, and the shipped list excuses it. A routine
    that overrides or implements another signature contributes nothing, and neither does a
    method whose name two project classes declare (1.4, 1.9). ``trust`` carries requirement
    1.8's two floors and the figure one of them is judged against.
    """
    records = _affected(after, affected, "routine")
    # The one rule of the three that has to ask this, and the reason is the next guard but
    # one: a change touching no routine must not be told that the declaring-class tally is
    # missing, because a rule with nothing to judge did not need it. Its two siblings read
    # only facts that travel on the records themselves, so an empty walk there answers
    # nothing without a guard, and a guard that can be cut with the suite green is worse
    # than none.
    if not records:
        return LeanOutcome(findings=[])
    routines = _routine_facts(records)
    if routines is None:
        return _unavailable(PARAMETER_RULE, _NO_ROUTINE_FACTS)
    declarations = after.method_declarations
    if declarations is None:
        return _unavailable(PARAMETER_RULE, _NO_TALLY)
    gate = _TrustGate(after, PARAMETER_RULE, trust)
    judged = [item for item in routines if _judged(item, declarations, gate)]
    return LeanOutcome(
        findings=_parameter_findings(judged, _compiled(ignore), severity),
        unavailable=gate.messages,
    )


def find_unused_classes(
    after: ProjectSnapshot,
    affected: Collection[EntityKey],
    severity: Severity = "warning",
    ignore: Sequence[str] = (),
    trust: Trust = UNMEASURED,
) -> LeanOutcome:
    """One finding per affected class that nothing in the project references (req 1.1).

    The decision is the worker's and covers the whole database rather than the change's
    neighbourhood (requirement 1.3), so a class used from a file this commit never touched is
    referenced. ``ignore`` is a list of regular expressions over class long names.
    """
    records = _affected(after, affected, "class")
    classes = _class_facts(records)
    if classes is None:
        return _unavailable(CLASS_RULE, _NO_CLASS_FACTS)
    gate = _TrustGate(after, CLASS_RULE, trust)
    return LeanOutcome(
        findings=_class_findings(classes, gate, _compiled(ignore), severity),
        unavailable=gate.messages,
    )


def find_unused_variables(
    after: ProjectSnapshot,
    affected_files: Collection[str],
    severity: Severity = "warning",
    ignore: Sequence[str] = (),
    trust: Trust = UNMEASURED,
) -> LeanOutcome:
    """One finding per module-level binding of an affected file that nothing reads (req 1.1).

    Reported once per definition, at the line the name is bound, so the finding points at the
    copy the commit touched. ``ignore`` is a list of regular expressions over binding names,
    and the shipped list covers the names a runtime reads rather than the code. This is the
    rule with the measured hundred-per-cent false-positive rate, and the accuracy floor in
    ``trust`` is the guard that covers it.
    """
    bindings = _affected_bindings(after, affected_files)
    if any(definition.referenced is None for definition, _ in bindings):
        return _unavailable(VARIABLE_RULE, _NO_BINDING_FACTS)
    gate = _TrustGate(after, VARIABLE_RULE, trust)
    return LeanOutcome(
        findings=_variable_findings(bindings, gate, _compiled(ignore), severity),
        unavailable=gate.messages,
    )


def _affected(
    after: ProjectSnapshot, affected: Collection[EntityKey], scope: str
) -> list[tuple[EntityKey, EntityRecord]]:
    """The affected records of one scope, in file order, from the after side alone (req 1.7).

    Two statements rather than one ``and``: branch coverage records no arc for a short
    circuit, so a fused condition can lose half of itself with the suite green -- and the
    halves say different things. The first keeps each rule to the scope it walks; the second
    is requirement 1.7 itself, because an entity the change deleted has no after-side record.
    """
    found: list[tuple[EntityKey, EntityRecord]] = []
    for key in affected:
        if key.scope != scope:
            continue
        record = after.entities.get(key)
        if record is None:
            continue
        found.append((key, record))
    return sorted(found, key=lambda pair: (pair[0].path, pair[0].longname))


def _routine_facts(records: Sequence[tuple[EntityKey, EntityRecord]]) -> list[_Routine] | None:
    """Every affected routine with its facts, or ``None`` when one of them was not measured."""
    found: list[_Routine] = []
    for key, record in records:
        item = _routine_fact(key, record)
        if item is None:
            return None
        found.append(item)
    return found


def _routine_fact(key: EntityKey, record: EntityRecord) -> _Routine | None:
    """One routine's measured facts, or ``None`` where any of the three is absent.

    Three separate refusals, one per fact, because each is a different way to be wrong: no
    facts at all is a snapshot recorded before the rule existed, an unmeasured ``overrides``
    read as ``False`` reports the parameter an interface forces, and an unmeasured parameter
    list read as empty reports nothing while looking like a clean bill of health.
    """
    facts = record.lean
    if facts is None:
        return None
    if facts.overrides is None:
        return None
    if facts.unused_parameters is None:
        return None
    return _Routine(record, key.longname, facts.overrides, tuple(facts.unused_parameters))


def _class_facts(records: Sequence[tuple[EntityKey, EntityRecord]]) -> list[_Class] | None:
    """Every affected class with its reference flag, or ``None`` when one was not measured."""
    found: list[_Class] = []
    for key, record in records:
        item = _class_fact(key, record)
        if item is None:
            return None
        found.append(item)
    return found


def _class_fact(key: EntityKey, record: EntityRecord) -> _Class | None:
    """One class's measured reference flag, or ``None`` where the walk did not record it."""
    facts = record.lean
    if facts is None:
        return None
    if facts.referenced is None:
        return None
    return _Class(record, key.longname, facts.referenced)


def _affected_bindings(
    after: ProjectSnapshot, affected_files: Collection[str]
) -> list[tuple[Definition, str]]:
    """Every module binding of an affected file, with the language its file was analysed as.

    The language is the file's rather than the binding's because a binding has none of its
    own, and the call-resolution floor is per language (requirement 1.8): a constant in a C++
    header is gated by the C++ figure however well the project's Python resolved.
    """
    paths = set(affected_files)
    languages = _file_languages(after)
    fallback = _sole_language(after)
    found = [
        (definition, languages.get(definition.path, fallback))
        for definition in after.definitions
        if definition.path in paths
    ]
    return sorted(found, key=lambda pair: (pair[0].path, pair[0].line, pair[0].name))


def _file_languages(after: ProjectSnapshot) -> dict[str, str]:
    """The language each recorded file was analysed as, by path."""
    return {
        key.path: record.language for key, record in after.entities.items() if key.scope == "file"
    }


def _sole_language(after: ProjectSnapshot) -> str:
    """The one language this side was analysed as, or :data:`UNKNOWN_LANGUAGE`.

    The fallback for a file the snapshot holds no record for. A single-language project has
    exactly one answer and it is not a guess; a mixed one has none, and the gate refuses the
    unknown rather than borrowing whichever substrate happens to have resolved better.
    """
    languages = after.languages
    if len(languages) == 1:
        return languages[0]
    return UNKNOWN_LANGUAGE


def _judged(item: _Routine, declarations: Mapping[str, int], gate: _TrustGate) -> bool:
    """Whether this routine's parameters may be reported at all (req 1.4, 1.8, 1.9).

    The gate is asked first and asked for every candidate, whatever the facts say, because a
    run below either floor was **not evaluated** -- that is the sentence requirement 1.8 wants
    said, and a rule that consulted the facts first would fall silent about an analysis it
    never judged, on the commit where every affected entity happens to look alive. The three
    rules here share that order through their three predicates.
    """
    if not gate.allows(item.record.language):
        return False
    if item.overrides:
        return False
    return declarations.get(item.record.ref.name, 0) < INTERFACE_DECLARERS


def _parameter_findings(
    routines: Sequence[_Routine], excused: Sequence[re.Pattern[str]], severity: Severity
) -> list[Finding]:
    """One finding per unused parameter the operator did not excuse, in declaration order."""
    return [
        _parameter_finding(item, name, severity)
        for item in routines
        for name in item.parameters
        if not _matches(excused, name)
    ]


def _class_findings(
    classes: Sequence[_Class],
    gate: _TrustGate,
    excused: Sequence[re.Pattern[str]],
    severity: Severity,
) -> list[Finding]:
    """One finding per unreferenced class, the gate asked before the facts are believed."""
    return [
        _class_finding(item, severity) for item in classes if _reported_class(item, gate, excused)
    ]


def _reported_class(item: _Class, gate: _TrustGate, excused: Sequence[re.Pattern[str]]) -> bool:
    """Whether this class may be reported: the floors, the measurement, the ignore list."""
    if not gate.allows(item.record.language):
        return False
    if item.referenced:
        return False
    return not _matches(excused, item.longname)


def _variable_findings(
    bindings: Sequence[tuple[Definition, str]],
    gate: _TrustGate,
    excused: Sequence[re.Pattern[str]],
    severity: Severity,
) -> list[Finding]:
    """One finding per unread module binding, gated by the language of the file it sits in."""
    return [
        _variable_finding(definition, severity)
        for definition, language in bindings
        if _reported_binding(definition, language, gate, excused)
    ]


def _reported_binding(
    definition: Definition,
    language: str,
    gate: _TrustGate,
    excused: Sequence[re.Pattern[str]],
) -> bool:
    """Whether this binding may be reported: the floors, the measurement, the ignore list."""
    if not gate.allows(language):
        return False
    if definition.referenced:
        return False
    return not _matches(excused, definition.name)


def _compiled(patterns: Sequence[str]) -> tuple[re.Pattern[str], ...]:
    """The ignore list, compiled. Invalid patterns are refused by the settings model."""
    return tuple(re.compile(pattern) for pattern in patterns)


def _matches(excused: Sequence[re.Pattern[str]], subject: str) -> bool:
    """Whether a name is one the operator excused.

    ``search`` and not ``match``, like every other ignore list in this project: a pattern
    naming a suffix -- ``Error$`` for an exception class -- has to be able to find it anywhere
    in the name.
    """
    return any(pattern.search(subject) for pattern in excused)


def _parameter_finding(item: _Routine, name: str, severity: Severity) -> Finding:
    """One parameter nothing in its routine reads, reported at the routine (req 1.2)."""
    return Finding(
        kind="structural",
        rule=PARAMETER_RULE,
        scope="routine",
        entity=item.record.ref,
        path=item.record.key.path,
        line=item.record.ref.line,
        value=0.0,
        limit=None,
        limit_source="rule",
        severity=severity,
        blocking=severity == "error",
        message=(
            f"{item.longname} declares the parameter {name} and never reads, sets or "
            f"modifies it; no signature it overrides asks for it either"
        ),
        details={"parameter": name, "longname": item.longname},
    )


def _class_finding(item: _Class, severity: Severity) -> Finding:
    """One class nothing references, reported where it is declared (req 1.1)."""
    return Finding(
        kind="structural",
        rule=CLASS_RULE,
        scope="class",
        entity=item.record.ref,
        path=item.record.key.path,
        line=item.record.ref.line,
        value=0.0,
        limit=None,
        limit_source="rule",
        severity=severity,
        blocking=severity == "error",
        message=(
            f"nothing in this project names the class {item.longname}; every reference to "
            f"it is either absent or outside the analysis root"
        ),
        details={"longname": item.longname},
    )


def _variable_finding(definition: Definition, severity: Severity) -> Finding:
    """One module-level binding nothing reads, reported where it is bound (req 1.1)."""
    return Finding(
        kind="structural",
        rule=VARIABLE_RULE,
        scope="file",
        path=definition.path,
        line=definition.line,
        value=0.0,
        limit=None,
        limit_source="rule",
        severity=severity,
        blocking=severity == "error",
        message=(
            f"nothing in this project reads the module-level {definition.name} that "
            f"{definition.path} binds"
        ),
        details={"definition": definition.name},
    )


def _unavailable(rule: str, missing: str) -> LeanOutcome:
    """The rule's one message for a run that could not measure what it reads (req 1.6)."""
    return LeanOutcome(
        findings=[],
        unavailable=(
            f"{rule} is on, but this snapshot carries no {missing}, so nothing was judged "
            f"unused; the analysis cache predates the rule -- run `scitools-hook db rebuild`, "
            f"or make one change to force a fresh extraction",
        ),
    )


def _accuracy_refusal(rule: str, trust: Trust) -> str | None:
    """Requirement 1.8's run-wide floor: was the code read at all, and how well.

    Two separate refusals because they are two different things to go and fix: a run that
    measured no accuracy has an install or a wiring to sort out, and a run below the floor has
    an analysis to repair. Neither may report, for the reason the module docstring measures --
    a name whose use sites sit in a region the analysis errored on reads as unused while it is
    read, sixteen times out of sixteen.
    """
    if trust.accuracy is None:
        return _unmeasured_accuracy(rule, trust.accuracy_floor)
    if trust.accuracy < trust.accuracy_floor:
        return _below_accuracy(rule, trust.accuracy, trust.accuracy_floor)
    return None


def _unmeasured_accuracy(rule: str, floor: float) -> str:
    """The run holds no accuracy figure, so nothing licenses a claim that a name is unused."""
    return (
        f"{rule} was not evaluated: this run measured no analysis accuracy, and the accuracy "
        f"floor of {floor:.0%} is what says a file was read at all -- an absent figure is not "
        f"a good one"
    )


def _below_accuracy(rule: str, accuracy: float, floor: float) -> str:
    """The accuracy floor stopped the rule, naming the share measured and the floor."""
    return (
        f"{rule} was not evaluated: Understand parsed {accuracy:.0%} of this analysis without "
        f"an error, below the accuracy floor of {floor:.0%}; a name whose use sites sit in a "
        f"region the analysis errored on reads as unused while it is read"
    )


def _unmeasured_resolution(rule: str, language: str, trust: Trust) -> str:
    """The run holds no call-resolution figure for a language it was asked about."""
    return (
        f"{rule} was not evaluated for {language}: this run measured no {language} call "
        f"resolution, and the call-resolution floor of {trust.resolution_floor:.0%} is what "
        f"says the Gate knows what calls what -- an absent figure is not a good one"
    )


def _below_resolution(rule: str, language: str, share: float, trust: Trust) -> str:
    """The call-resolution floor stopped the rule, naming the share measured and the floor."""
    return (
        f"{rule} was not evaluated for {language}: {share:.0%} of this run's {language} call "
        f"sites resolved to a project routine, below the call-resolution floor of "
        f"{trust.resolution_floor:.0%}, so a finding would report the analysis, not the code"
    )
