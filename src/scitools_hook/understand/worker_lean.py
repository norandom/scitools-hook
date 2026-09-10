"""Every lean measurement the extractor makes, in a file the worker loads by path.

**The reference facts, so far.** `routine_facts` answers the callers, callees, forwarding
target, override flag and unused parameter names three rules read; `class_facts` answers
whether a class is used, what derives from it and how many other things name it;
`variable_referenced` answers whether a module-level binding is read; `method_declarations`
tallies, over the whole project, how many classes declare each method name. `token_index`
arrives with the duplication rules. The file itself, its place in the architecture gate and
the rule it is held to came first, before any of them, because a file that appears later
appears without.

**Why a second file rather than more of `worker.py`.** `worker.py` measures 1 060 of its
1 200 permitted code lines and 119 of its 130 functions, and six lean extractions do not fit
in what is left. The alternatives were both worse: raising the ceiling would be adapting the
tool's own limits to fit a feature it is measuring, and a second *op* would open the database
and walk every entity a second time against a 6.5 s budget. So the measurements sit here, and
`worker.py` calls them from the walk it already makes (design.md, *Architecture Pattern
Evaluation*, option C).

**Why this file may import nothing from `scitools_hook`.** The worker runs under Understand's
own interpreter, `<home>/bin/<plat>/upython`, where this package is not on `sys.path` at all.
`worker.py` reaches this file through `importlib.util.spec_from_file_location` on its own
directory -- a *path* load, not an import statement -- so the file is executed by that same
interpreter and is bound by the same rule: the standard library and nothing else. Not even
the package leaf. `tests/test_import_direction.py` holds both files to an allowance of
`frozenset()` and proves it twice, once by parsing the source and once by executing this
module under `python -I -S` and looking for `scitools_hook` in the modules it created.

A path load leaves no trace in the import graph, which is exactly why the rule is written
down here as well: nothing a reader follows from `worker.py` would lead them to this file,
and nothing a checker walks would either.

**What the worker hands over instead of imports.** Because there is nothing to import, the
two helpers the measurements need -- the project-relative path function and the `understand`
module itself -- arrive as arguments, in a small context object built by `worker.py`. That
keeps one copy of each helper across the two files rather than a copy per file.

**What belongs here and what does not.** This file counts, lists and hashes; it never judges.
Thresholds, ignore lists and severities are the analysis layer's, which reads the facts back
off the snapshot. A measurement that decided whether a number was too large would put the
policy in the one process that cannot be unit-tested without a licence.

**One more consequence.** `snapshot_cache.worker_digest()` hashes the worker's source so that
a changed measurement cannot be answered out of the before-side cache. It hashes this file too
-- reading its path from `worker.LEAN_PATH`, so the file hashed is the file the loader runs --
because a change confined to the sibling would otherwise be served stale.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Final


class LeanContext:
    """What the measurements need from ``worker.py``, since they may import nothing from it.

    ``project_path`` is ``worker._project_path``: it answers the repository-relative path of
    a *file* entity and ``None`` for anything Understand injected or anything outside the
    analysis root, which is the whole meaning of "project" in every fact below. ``api`` is the
    ``understand`` module, carried for the measurements that have to catch its own exception
    type; ``routine_facts`` never reads it.

    **Plain attributes rather than a dataclass, and that is not a style choice.** Measured
    here: ``@dataclass`` on a module loaded through ``spec_from_file_location`` without being
    registered in ``sys.modules`` raises ``AttributeError`` inside ``dataclasses._is_type``,
    which reads ``sys.modules.get(cls.__module__).__dict__``. That load is exactly the one
    ``worker._lean_module()`` performs, and the isolated-interpreter test in
    ``tests/test_import_direction.py`` performs it too -- it is what caught this.
    """

    def __init__(self, api: Any, project_path: Callable[[Any, str], str | None], root: str) -> None:
        self.api = api
        self.project_path = project_path
        self.root = root


CALLER_KINDS: Final = "callby"
CALLEE_KINDS: Final = "call"
OVERRIDE_KINDS: Final = "overrides"

CONTAINER_KINDS: Final = "definein, declarein"
"""Reference kinds leading from an entity to the file it is written in: ``worker``'s own.

The third string this file has to write out twice, and bound to ``worker.CONTAINER_REFS`` by
a test for the same reason as the other two: the fake ignores an ``Ent.ref`` filter, so a
kind dropped from here is invisible to every unit test, and a callee whose file this file
could not find is a callee the snapshot's own walk did find.
"""

PARAMETER_REFS: Final = "Define"
PARAMETER_KINDS: Final = "Parameter ~Catch"
"""The pair ``worker._count_params`` already asks ``Ent.ents`` for, spelled the same way.

``CountParams`` and ``unused_parameters`` are two answers about one declaration list, and a
routine whose parameters the two functions disagreed about would report a name the count
says is not there. A test binds the two strings, which is all that can bind them across a
file that may not import.
"""

PARAMETER_USE: Final = "useby, setby, modifyby, callby"
"""What touching a parameter looks like, in every direction Understand records one.

``callby`` is here because measurement put it here: a parameter used only as ``fn()`` has no
``Useby``, ``Setby`` or ``Modifyby`` reference at all, so a set without it reports every
callback parameter as dead. ``setby`` counts although a write is not a read -- the finding is
"nothing in the body mentions this", not "nothing reads it" -- which is the opposite of the
decision the design records for ``variable_referenced``, where a write is deliberately not a
use. The two differ because a parameter's defining assignment is the call site's rather than
the body's, so no reference here is the declaration's own.
"""

ROUTINE_KINDS: Final = (
    "function ~unknown ~unresolved, method ~unknown ~unresolved, "
    "procedure ~unknown ~unresolved, routine ~unknown ~unresolved, "
    "classmethod ~unknown ~unresolved"
)
"""What counts as a routine at either end of a call: ``config.metric_names.SCOPE_KINDS``.

The same string, written out because this file may not import it, and bound to the original
by a test. It has to be the same one: the population these facts count over is the population
the snapshot walk records, and a callee outside it is a name the rule cannot resolve.

**A call reference is not a callee on its own**, in two shapes task 1.6 recorded on the
contract project, each excluded for its own reason:

* An **unresolved** target still sits in a project file -- ``pkg/core.py``'s ``run`` calls
  ``widen``, which Understand records as a ``python Unknown Ambiguous Attribute`` written in
  ``pkg/inner/leaf.py`` -- so nothing about *where* it sits refuses it. Counted, it gives a
  routine a callee it does not have and a pass-through finding whose remedy is to delete a
  call that binds at runtime. That argument is about a real repository, not about the
  fixture: ``run`` was given a statement of its own so that the fixture's one pass-through
  case stays unique whichever way an implementation reads "callee", and citing it as
  *evidence* for this filter was a defect in an earlier draft of this docstring.
* A **constructor** call binds to the class -- ``app/entry.py``'s ``entry_point`` calls
  ``Engine().run(...)`` and the sibling target is ``core.Engine``, a Class. It is excluded
  because the contract says a callee is a project routine and ``forwards_to`` has to name
  one: a pass-through hint asks an agent to inline the callee into its caller, which is not
  something a class can be. Note the direction: refusing it *lowers* a callee count and so
  can only move a routine closer to the rule, which is why the fixture pins ``entry_point``
  with a statement rather than with this filter either.
"""


REFERENCE_KINDS: Final = (
    "callby, useby, setby, modifyby, typedby, inheritby, derive, extendby, implementby"
)
"""What using a class looks like, in every direction and every language Understand records.

**Inheritance counts as use, and leaving it out was a defect task 1.6 measured.** A base class
whose only inbound reference is the inheritance reference from its one subclass would read as
dead, and ``unused_class`` and ``single_implementation`` would then report the same class in
one run with contradictory advice: delete it, and fold it into its one implementation. The
honest reading is that a class its subclass inherits from is used. ``single_implementation``
still reaches it, because :func:`class_facts`'s ``referrers`` excludes the derived class.

**Which kind fires is per language.** Measured by tasks 1.6 and 1.7 on the contract project
on Build 1262: Python answers ``Python Inheritby`` and C++ answers ``C Public Derive``, both
recorded ON THE BASE CLASS and naming the derived one, so a set holding either alone calls
the other language's base class dead. ``extendby`` and ``implementby`` fire on neither
fixture language and are carried for the languages the contract project does not build. Read
off the installed kind documentation rather than measured: ``Extend (Extendby)`` is offered
for Fortran and Web, ``Implement (Implementby)`` for Basic, C#, Pascal, Rust, VHDL and Web,
and Objective-C carries its own ``ObjC Extend (ObjC Extendby)`` and ``ObjC Implement (ObjC
Implementby)`` pairs under the C section -- **neither kind is offered for C or C++ itself**.
Java is not in either list: it pairs its inheritance references under ``Couple (Coupleby)``
as ``Java Extend Couple`` and ``Java Implement Couple``, so the inverse spelling a Java base
class carries is unverified here and task 6.1's contract test owns it.

The cost of the fix, recorded so it is not mistaken for none: ``unused_class`` can never reach
a base class that has any subclass, including an abstract base whose whole subtree is dead.
That is the cheaper error than two rules contradicting each other on one class.
"""

DERIVED_KINDS: Final = "derive, inheritby, extendby, implementby"
"""The inheritance half of :data:`REFERENCE_KINDS`: on a class, its subclasses.

On a Python or C++ base class, what these kinds answer is that class's *subclasses* rather
than its users, which is why they are read twice: they are the whole of ``derived``, and they
contribute to ``referenced`` only because "has a subclass" and "is inherited by something"
coincide there.

``base`` -- the reference a C++ derived class carries back to its base -- is deliberately in
neither set. It would make a subclass count as a *user* of its base and take every base class
out of ``single_implementation``, which is the rule this set exists to serve.

**``derive`` is not one direction, and for two languages this set reads inverted.** The kind
documentation carries it in two different pairs: ``Base (Derive)`` for Basic, C/C++ and C#,
where ``Derive`` is the inverse recorded on the base and naming the derived type -- which is
the reading everything above assumes and task 1.7 measured on C++ -- and ``Derive
(Derivefrom)`` for **Ada and Pascal**, where ``Derive`` is the *forward* reference a derived
type carries to its base. In those two languages this set therefore hands ``derived`` the
class's own base, which is exactly the effect excluding ``base`` prevents elsewhere, and
``single_implementation`` would name the wrong end of the pair. Pascal is affected twice over,
since it also carries the Python-style ``Inherit (Inheritby)``.

Scoped rather than fixed, and recorded rather than asserted away: the contract project builds
neither language, so nothing here can measure the correction, and the candidates -- qualifying
the string by language, or reading ``derivefrom`` in place of ``derive`` where the pair is
inverted -- are both spellings this machine cannot verify. Task 6.1 prints the per-language
kind table and owns the decision.
"""

VARIABLE_USE: Final = "useby, callby, typedby"
"""What reading a module-level binding looks like. **A write is not a use.**

:data:`REFERENCE_KINDS` is the wrong set here and task 1.7's review caught it: that set holds
``setby``, and a module-level binding's own defining assignment is a ``Set Init`` reference to
it, so every variable in every project would answer used and the rule would report nothing,
ever. The contract fixture discriminates only by accident -- its dead variables happen to have
no read either.

Writes are excluded rather than merely the defining one, which is a second deliberate
decision: a variable something assigns and nothing reads is dead too, and that is also how
Understand defines its own ``CountUnusedVariable``, where declare, initialise and assign all
count as writes. ``callby`` is here for a module-level callable whose only mention is the
call, and ``typedby`` for a binding named in an annotation, which mentions the name without
writing it.
"""

MEMBER_KINDS: Final = "define, declare"
"""Reference kinds leading from a class to what is written inside it: ``worker``'s own.

``declare`` as well as ``define`` because a C++ member is declared in a header and defined in
a source, and a class that only declares its methods still declares them for requirement 1.9.

The fourth string this file has to write out twice, and bound to ``worker.MEMBER_REFS`` by a
test for the same reason as the other three: a kind dropped from it matches nothing rather
than raising, so the tally would quietly answer that no class declares any method and the
interface-method exclusion would excuse nothing at all.
"""


def routine_facts(ent: Any, ctx: LeanContext) -> dict[str, object]:
    """The reference facts three rules read, for one routine (requirements 1.2-1.4, 2.1).

    ``callers`` and ``callees`` are distinct project routines rather than call sites;
    ``forwards_to`` is the callee's long name when there is exactly one, which is the routine
    a pass-through finding names; ``overrides`` says only that an override reference exists,
    because whether that excuses the routine is the rule's decision; ``unused_parameters``
    holds every parameter name no project reference touches, in declaration order, receivers
    included -- the ignore list one layer up is what excuses ``self``.
    """
    callees = _project_callees(ent, ctx)
    return {
        "callers": len(_project_callers(ent, ctx)),
        "callees": len(callees),
        "forwards_to": str(callees[0].longname()) if len(callees) == 1 else None,
        "overrides": bool(ent.refs(OVERRIDE_KINDS)),
        "unused_parameters": _unused_parameters(ent, ctx),
    }


def _project_callers(ent: Any, ctx: LeanContext) -> list[Any]:
    """The distinct project routines whose bodies call ``ent``.

    Judged by the file the *reference* is written in, which is what the requirements mean by
    a project reference and what ``worker._referenced`` already asks of the routine rule. The
    calling entity's own definition file is a different question and is deliberately not the
    one asked, so that a reference written in an excluded file cannot be counted by way of a
    caller declared in an included one.

    **A call from module scope is not a caller, and in one shape that is a false finding
    rather than a missing one.** Python records a call outside any routine against the file,
    and a file is not a routine. Where module scope is the *only* call site, the routine reads
    as having no caller at all and the pass-through rule cannot reach it, which errs the safe
    way. Where a routine is called once from another routine and once from module scope, the
    count falls from two to one, which is exactly the predicate of requirement 2.1, and the
    rule reports a pass-through for a routine that has two call sites -- a shape the rule
    layer cannot currently tell from a true one, because this document carries a count and not
    the call sites behind it. The error is accepted rather than fixed with a sixth fact: the
    document is the design's contract, and a caller a rule can name belongs in that contract
    rather than beside it.
    """
    found: dict[int, Any] = {}
    for ref in ent.refs(CALLER_KINDS):
        caller = ref.ent()
        if ctx.project_path(ref.file(), ctx.root) is not None and _is_routine(caller):
            found[caller.id()] = caller
    return list(found.values())


def _project_callees(ent: Any, ctx: LeanContext) -> list[Any]:
    """The distinct project routines ``ent`` calls.

    The reference's own file is ``ent``'s and says nothing, so the target decides twice: it
    has to be a routine kind, and it has to be defined in a project file.
    """
    found: dict[int, Any] = {}
    for ref in ent.refs(CALLEE_KINDS):
        callee = ref.ent()
        if _is_routine(callee) and _definition_path(callee, ctx) is not None:
            found[callee.id()] = callee
    return list(found.values())


def _unused_parameters(ent: Any, ctx: LeanContext) -> list[str]:
    """The names of the declared parameters no project reference touches, in order."""
    return [
        str(param.name())
        for param in ent.ents(PARAMETER_REFS, PARAMETER_KINDS)
        if not _referenced_in_project(param, PARAMETER_USE, ctx)
    ]


def class_facts(ent: Any, ctx: LeanContext) -> dict[str, object]:
    """The reference facts two rules read, for one class (requirements 1.1, 1.3, 3.1).

    ``referenced`` is any project reference at all, inheritance included, and is what
    ``unused_class`` reads; ``derived`` holds the long names of the project classes that
    inherit from this one, which is what a ``single_implementation`` finding names;
    ``referrers`` counts the distinct project entities that reference the class other than
    itself, its members, its derived classes and their members, so that requirement 3.1's "no
    project reference other than from that derived class" is a count of zero.
    """
    derived = _derived_classes(ent, ctx)
    return {
        "referenced": _referenced_in_project(ent, REFERENCE_KINDS, ctx),
        "derived": sorted(str(cls.longname()) for cls in derived),
        "referrers": _count_referrers(ent, derived, ctx),
    }


def variable_referenced(ent: Any, ctx: LeanContext) -> bool:
    """Whether a project file READS a module-level binding (requirements 1.1, 1.3).

    A :data:`VARIABLE_USE` reference and not a write, for the reason recorded there: the
    binding's own defining assignment is a write to it, so a set that counted writes would
    answer True for every variable in every project.
    """
    return _referenced_in_project(ent, VARIABLE_USE, ctx)


def method_declarations(classes: Mapping[str, tuple[Any, str]]) -> dict[str, int]:
    """How many project classes declare each method name (requirement 1.9).

    **A project-wide tally rather than a fact about any one class, which is why it is a
    function of its own over the extractor's ``class_ents`` map** -- the same shape
    ``token_index`` takes -- rather than a key in :func:`class_facts`. Two classes declaring
    ``run`` is a fact about the pair; neither class can see it, and a per-entity document
    that carried half of it would have to be re-joined by every reader.

    It is what sees an interface method where nothing else can. Measured on facdrone,
    2026-09-10, 417 files resolving at 26%: of the 830 routines the naive dead-code predicate
    answered, exactly one carried an override reference, because that codebase satisfies its
    interfaces structurally and an implementation holds no reference at all to the interface
    it satisfies. Inheritance detects nothing there.

    Every count is recorded, not only the counts of two and above. The threshold is
    requirement 1.9's and belongs to the rule that applies it, as every other threshold in
    this file's outputs does.

    A tally is not a per-entity fact, so it has no place in ``LeanFacts``. The design names its
    home: ``ProjectSnapshot.method_declarations``, filled in ``_Extractor.build`` beside
    ``tokens`` when the plan asks for references (task 3.3), read by ``analysis/lean/dead``
    (task 4.1).
    """
    counts: dict[str, int] = {}
    for ent, _ in classes.values():
        for name in {str(member.name()) for member in _members(ent) if _is_routine(member)}:
            counts[name] = counts.get(name, 0) + 1
    return counts


def _derived_classes(ent: Any, ctx: LeanContext) -> list[Any]:
    """The distinct project classes that inherit from ``ent``.

    The target decides rather than the file the reference is written in, as it does for a
    callee and for the same reason: this is a name a finding prints, so it has to belong to a
    file the project owns. Four kinds are asked for at once, so a pair recorded under two of
    them is deduplicated by entity, or one implementation would print as two.
    """
    found: dict[int, Any] = {}
    for ref in ent.refs(DERIVED_KINDS):
        target = ref.ent()
        if _definition_path(target, ctx) is not None:
            found[target.id()] = target
    return list(found.values())


def _count_referrers(ent: Any, derived: list[Any], ctx: LeanContext) -> int:
    """The distinct project entities referencing ``ent`` that requirement 3.1 counts.

    No kind filter stands in front of the referrer: a module-scope ``Engine()`` is recorded
    against the *file*, and a file that instantiates the class is a user of it.
    """
    excluded = _own_ids(ent, derived)
    found: set[int] = set()
    for ref in ent.refs(REFERENCE_KINDS):
        referrer = ref.ent()
        if ctx.project_path(ref.file(), ctx.root) is not None and referrer.id() not in excluded:
            found.add(referrer.id())
    return len(found)


def _own_ids(ent: Any, derived: list[Any]) -> set[int]:
    """The entities a class's own use of itself runs through: it, its subclasses, their members.

    A method that names the class it is written in is the class using itself, and a subclass's
    ``super().__init__()`` is the derived class using its base. Counting either would take
    every class with a constructor out of ``single_implementation``.
    """
    ids: set[int] = set()
    for owner in (ent, *derived):
        ids.add(owner.id())
        ids.update(member.id() for member in _members(owner))
    return ids


def _members(ent: Any) -> list[Any]:
    """What a class declares or defines inside itself, methods and fields alike."""
    return [ref.ent() for ref in ent.refs(MEMBER_KINDS)]


def _referenced_in_project(ent: Any, kinds: str, ctx: LeanContext) -> bool:
    """Whether any reference of ``kinds`` to ``ent`` is written in a project file.

    The file the *reference* sits in decides, which is what the requirements mean by a project
    reference and the same reading :func:`_project_callers` takes. Three facts share this one
    body -- an unused parameter, an unreferenced class, an unread variable -- and they differ
    only in ``kinds``, which is the parameter rather than three copies of the walk.
    """
    return any(ctx.project_path(ref.file(), ctx.root) is not None for ref in ent.refs(kinds))


def _is_routine(ent: Any) -> bool:
    """Whether an entity is one of the routines the snapshot walk records."""
    return bool(ent.kind().check(ROUTINE_KINDS))


def _definition_path(ent: Any, ctx: LeanContext) -> str | None:
    """The project path of the file an entity is written in, or ``None`` when it has none."""
    ref = ent.ref(CONTAINER_KINDS)
    return None if ref is None else ctx.project_path(ref.file(), ctx.root)
