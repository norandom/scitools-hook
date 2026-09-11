"""Every lean measurement the extractor makes, in a file the worker loads by path.

**The reference facts.** `routine_facts` answers the callers, callees, forwarding target,
override flag and unused parameter names three rules read; `class_facts` answers whether a
class is used, what derives from it and how many other things name it; `variable_referenced`
answers whether a module-level binding is read; `method_declarations` tallies, over the whole
project, how many classes declare each method name. The file itself, its place in the
architecture gate and the rule it is held to came first, before any of them, because a file
that appears later appears without.

**And the token half.** `token_index` reads the project's lexeme streams once and answers with
one hash per code line and one normalised shape per routine, which is everything the
duplicate-block and similar-routine rules see of the source. It is the only measurement here
that is whole-project rather than per entity, along with `method_declarations`, and the only
one that can report a file it could not read at all. `lexer_probe` is the one question
`doctor` asks on those two rules' behalf, and the only routine in this file that is not a
measurement of a project at all: it opens a scratch database of its own and asks whether this
*build* yields a lexeme stream, which is what a configuration enabling either rule needs
answered before a check runs.

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

import hashlib
from bisect import bisect_left, bisect_right
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
a test for the same reason as the other two: a kind dropped from here is invisible to every
unit test, and a callee whose file this file could not find is a callee the snapshot's own
walk did find. The fake honours a ``definein`` / ``declarein`` / ``end`` filter and ignores
every other kind, but the entities the reference measurements are given all carry a
definition, so narrowing this pair to either half goes on answering for all of them. Only
:data:`START_REFS`, which is the one query that *means* the narrower half, is bound by
behaviour instead.
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


# --- the token index the duplication rules read (requirements 5.4, 5.8) ----------------


DROPPED_TOKENS: Final = frozenset({"Whitespace", "Comment", "Newline", "Indent", "Dedent"})
"""The token classes that carry no code, in Understand's own spelling for ``Lexeme.token()``.

Requirement 5.4 in one set, and the design's own list: a re-indented, re-commented copy has to
hash equal to its original, so layout and prose are what the comparison must not see.
``Indent`` and ``Dedent`` are the classes the documented vocabulary carries for a change of
indentation and are dropped for the reason ``Whitespace`` is; ``Newline`` is what would
otherwise put one matching token on the end of every line of every file.

**Not a superset of "things with no text".** A lexeme whose text is empty is dropped as well,
by :func:`_code_lines` and on its text rather than on its class, because the documented
classes carry no name for the end-of-file artefact that produces one.
"""

IDENTIFIER_TOKEN: Final = "Identifier"
LITERAL_TOKENS: Final = frozenset({"String", "Literal"})
IDENTIFIER_SHAPE: Final = "ID"
LITERAL_SHAPE: Final = "LIT"
"""The two classes a shape sees in place of a name or a value (requirement 5.4).

A renamed twin is still a twin, so the names are exactly what the similarity comparison must
not read; ``String`` and ``Literal`` are one class here because a message and a number are
both values, and a routine that differs from another only in what it prints is a copy.
Everything else keeps its text, so a copy differing in one *operator* is not one.
"""

START_REFS: Final = "definein"
END_REFS: Final = "end"
"""Where a routine's body begins and where it ends, as two single-kind reference queries.

``definein`` alone rather than :data:`CONTAINER_KINDS`, which is what the design specifies and
what a range needs: a declaration's line is not the first line of a body, so a span reading
``declarein .. end`` would run from a header to a source file. Which of the two
:data:`CONTAINER_KINDS` a build answers first for a method with both is **not** measured here,
and nothing depends on it: the two references are read as a pair and :func:`_routine_span`
refuses any span whose ends do not land in one project file.

**A routine carrying only a declaration is the case that makes this string load-bearing**,
and it is a live one on every C++ and Java project: a pure virtual, an interface method or a
prototype whose definition is outside the analysis passes the extractor's kind filter, is kept
by ``worker._remember`` -- whose ``_container_of`` reads :data:`CONTAINER_KINDS` and so takes
either half -- and answers ``None`` here. It has no body to compare, so it is absent from the
index rather than measured out of its own header.
"""

LINE_HASH_CHARS: Final = 16
"""How much of each line's SHA-256 the index keeps: 64 bits, as design.md specifies.

The index is one entry per code line of a whole project, so the width is a size decision
rather than a security one; the hash is compared with other hashes of the same run and never
inverted.
"""


def token_index(
    file_ents: Mapping[str, Any], routines: Mapping[str, tuple[Any, str]], ctx: LeanContext
) -> dict[str, object]:
    """The project's code lines and routine shapes, for the two duplication rules (5.4, 5.8).

    ``files`` is one ``(line, hash)`` pair per code line of each project file, keeping the
    file's own line numbers so a finding names a range a reader opens; ``routines`` is one
    shape per routine the database gives both ends of, encoded through ``vocabulary``;
    ``unreadable`` names the files whose lexer refused, which contribute nothing and are said
    once per run rather than arriving as a file with nothing in it (requirement 5.8).

    Both maps are the extractor's own -- ``file_ents`` and ``routine_ents`` -- taken whole for
    the reason :func:`method_declarations` takes ``class_ents`` whole: the entity walk has
    already collected them, whole-project, and a second query would pay for them twice. The
    path half of each routine pair is deliberately **not** read: it comes from ``definein,
    declarein``, which is not known to be the file a body's lines are in, while a shape has to
    name the file it was clipped from.

    **One lexer pass per file, and a file is finished before the next one is opened.** Both
    halves read the same filtered lines rather than a second ``lexemes(start, end)`` call, so
    a token dropped from one cannot survive in the other -- and no file's lines outlive it.
    Keeping them all for a shape pass at the end instead measured 96 MB against 26 MB on a
    project this repository's size and grew with the project rather than with its largest
    file. The spans come first because they need only ``Ent.ref``; the keys are sorted at the
    end because the shaping order is now the file order.
    """
    vocabulary = _Vocabulary()
    spans = _routine_spans(routines, ctx)
    files: dict[str, list[list[object]]] = {}
    shapes: dict[str, dict[str, object]] = {}
    unreadable: list[str] = []
    for path in sorted(file_ents):
        lines = _code_lines(file_ents[path], ctx)
        if lines is None:
            unreadable.append(path)
            continue
        files[path] = _line_hashes(lines)
        shapes.update(_file_shapes(path, spans.get(path, []), lines, vocabulary))
    return {
        "vocabulary": vocabulary.texts,
        "files": files,
        "routines": {token: shapes[token] for token in sorted(shapes)},
        "unreadable": unreadable,
    }


class _Vocabulary:
    """The token texts a shape indexes into, in the order they were first seen.

    A shape is a list of small integers rather than of strings because the index carries one
    entry per token of every routine in the project, and the rule that reads it compares whole
    shapes of them. One vocabulary for the whole document, so two routines in two files
    encode the same token to the same number -- which is the only way their shapes can be
    compared at all.

    A plain class for the reason :class:`LeanContext` is one: ``@dataclass`` raises under the
    path load ``worker._lean_module()`` performs.
    """

    def __init__(self) -> None:
        self.texts: list[str] = []
        self.ids: dict[str, int] = {}

    def index(self, text: str) -> int:
        """The number standing for ``text``, assigning it one the first time it is asked for."""
        if text not in self.ids:
            self.ids[text] = len(self.texts)
            self.texts.append(text)
        return self.ids[text]


def _code_lines(ent: Any, ctx: LeanContext) -> list[tuple[int, list[tuple[str, str]]]] | None:
    """One file's code lines in line order, each with its ``(class, text)`` tokens in order.

    ``None`` -- and only ``None`` -- means the lexer refused, which is requirement 5.8's file:
    ``Ent.lexer`` documents ``UnderstandError`` when the source is gone or has changed since
    the parse, a condition a gate measuring a worktree between two commits meets often. A
    readable file with no code in it answers an empty list, and the two must not be confused:
    one is the absence of a measurement, the other is a measurement. A line left with no
    tokens at all is **absent** rather than empty, so that the padding between two functions
    is not a line every other blank line matches.

    The API's own error class is named through ``ctx.api`` rather than caught as
    ``Exception``, which is what the context carries the module for: a bug in this function
    must fail the run rather than mark every file unreadable.

    The stream is read into plain tuples in one pass because each ``token()`` and ``text()``
    call crosses into the API, and both halves of the index read every token.

    **Grouped and ordered here rather than in either half**, for two reasons. The document a
    run produces must not depend on an ordering the lexer documents nothing about, or the two
    sides of a change would not be comparable document to document. And a routine's shape is
    clipped by a binary search over these line numbers, which is a search this ordering is
    what makes correct -- the tokens *within* a line keep the order they arrived in, because
    that order is the line's text.
    """
    try:
        lexemes = list(ent.lexer(False).lexemes())
    except ctx.api.UnderstandError:
        return None
    grouped: dict[int, list[tuple[str, str]]] = {}
    for lexeme in lexemes:
        token_class, text = str(lexeme.token()), str(lexeme.text())
        if token_class not in DROPPED_TOKENS and text != "":
            grouped.setdefault(int(lexeme.line_begin()), []).append((token_class, text))
    return sorted(grouped.items())


def _line_hashes(lines: list[tuple[int, list[tuple[str, str]]]]) -> list[list[object]]:
    """One ``[line, hash]`` pair per code line, in the order :func:`_code_lines` put them.

    The texts of a line are joined and hashed, so two lines differing only in layout or in
    comments hash equal (requirement 5.4).
    """
    return [[line, _line_hash("".join(text for _, text in tokens))] for line, tokens in lines]


def _line_hash(text: str) -> str:
    """The first :data:`LINE_HASH_CHARS` hex characters of one line's SHA-256."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:LINE_HASH_CHARS]


def _routine_spans(
    routines: Mapping[str, tuple[Any, str]], ctx: LeanContext
) -> dict[str, list[tuple[str, int, int, int | None]]]:
    """Every routine the index can place, as ``(key, first, last, statements)`` per file path.

    Grouped by path so that :func:`token_index` can finish a file while its lines are still
    in hand, and resolved before any lexing because a span is read from ``Ent.ref`` alone.

    **The keys are sorted, and that is load-bearing rather than tidiness.** A group is shaped
    in the order it was built, and :meth:`_Vocabulary.index` hands out its numbers in the
    order it is first asked -- so for two routines written in *one* file, the order the walk
    collected them in would otherwise decide both the vocabulary and every shape in the
    document. Sorting the keys here is what :func:`_code_lines` promises for lines: the
    document a run produces does not depend on an ordering nothing documents, or the two
    sides of a change would not be comparable document to document. Deleting the ``sorted``
    leaves the rest of the suite green and fails
    ``test_two_routines_in_one_file_number_their_tokens_the_same_either_way_round``.

    A routine is left out rather than approximated in two shapes here, each of which is an
    absence and not a zero: the database gives no end for it, or its two ends sit in different
    files. A third is left out later, by having no group of its own: the file it is written in
    is one the lexer refused, or one the walk never collected. The similar-routine rule reads
    an absence as "not measured for that routine" and reports nothing about it.

    ``CountStmt`` rides on the span, taken **here** where the walk's entity is in hand and
    each routine is visited exactly once, rather than inside the per-file clipping loop.
    """
    grouped: dict[str, list[tuple[str, int, int, int | None]]] = {}
    for token in sorted(routines):
        span = _routine_span(routines[token][0], ctx)
        if span is None:
            continue
        path, start, end = span
        statements = _statements(routines[token][0])
        grouped.setdefault(path, []).append((token, start, end, statements))
    return grouped


def _file_shapes(
    path: str,
    spans: list[tuple[str, int, int, int | None]],
    lines: list[tuple[int, list[tuple[str, str]]]],
    vocabulary: _Vocabulary,
) -> dict[str, dict[str, object]]:
    """The shapes of the routines written in one file, clipped from that file's code lines.

    Each range is found by binary search rather than by filtering the whole file per routine.
    At this repository's own scale -- 316 files, 110 425 lines, about 21 routines per file --
    the filtering form measures 2.38 s against 0.35 s, and it degrades with the *product* of a
    file's routines and its tokens, so the worst file in a project costs the most.
    """
    numbers = [line for line, _ in lines]
    shaped: dict[str, dict[str, object]] = {}
    for token, start, end, statements in spans:
        clipped = lines[bisect_left(numbers, start) : bisect_right(numbers, end)]
        shape = [
            vocabulary.index(_shape_text(token_class, text))
            for _, tokens in clipped
            for token_class, text in tokens
        ]
        shaped[token] = {
            "path": path,
            "start": start,
            "end": end,
            "statements": statements,
            "shape": shape,
        }
    return shaped


def _routine_span(ent: Any, ctx: LeanContext) -> tuple[str, int, int] | None:
    """The project path and the first and last line of a routine's body, or ``None``.

    Both references are required and both must be written in the same project file. A routine
    with no ``Definein`` is one the database has only a declaration of, which is a shape every
    C++ and Java project carries (:data:`START_REFS`); a routine with no ``End`` is one the
    database did not see the end of; and a range clipped from a file that holds only one of
    its ends would be some other routine's tokens.

    The ``None`` half of the path test is what makes the pair a path rather than a maybe-path;
    a routine whose file is not project code has no lines to clip from either way, so it is
    ``mypy --strict`` rather than a case that refuses it.
    """
    start, end = ent.ref(START_REFS), ent.ref(END_REFS)
    if start is None or end is None:
        return None
    path = ctx.project_path(start.file(), ctx.root)
    if path is None or path != ctx.project_path(end.file(), ctx.root):
        return None
    return path, int(start.line()), int(end.line())


STATEMENT_METRIC: Final = "CountStmt"
"""The metric the similar-routine rule takes its ``similar_min_statements`` floor on.

Spelled out rather than read from ``analysis.lean.layering.STATEMENT_METRIC``, because this
file may import nothing of this package (see the module docstring).
``test_a_shape_carries_the_statement_count_the_record_carries`` is what holds the two
spellings equal, and it holds them by behaviour rather than by a second string comparison --
one run over one entity, answering the same number into the index and into the entity record.
A name that matched nothing would record every routine as unmeasured, which the family rule
reads as "do not judge this routine": the rule would then report nothing at all, on every
project, with every test green.
"""


def _statements(ent: Any) -> int | None:
    """One routine's ``CountStmt``, or ``None`` where this build did not measure it.

    ``None`` and not ``0``, which is the distinction the family rule's floor is written
    around: a routine that was never counted is not a routine of no statements. The API
    answers ``None`` for a metric it does not offer, and that answer is passed through rather
    than filled in.
    """
    value = ent.metric([STATEMENT_METRIC]).get(STATEMENT_METRIC)
    return None if value is None else int(value)


def _shape_text(token_class: str, text: str) -> str:
    """One token as the similarity comparison sees it: a name, a value, or its own text."""
    if token_class == IDENTIFIER_TOKEN:
        return IDENTIFIER_SHAPE
    if token_class in LITERAL_TOKENS:
        return LITERAL_SHAPE
    return text


LEXER_PROBE_KIND: Final = "file ~unknown ~unresolved"
"""Which entities :func:`lexer_probe` may read; the kind string a snapshot reads files with.

Written out rather than read from ``config.metric_names.SCOPE_KINDS``, because this file may
import nothing of this package (see the module docstring).
``test_the_lexer_probe_reads_the_file_kind_a_snapshot_reads_files_with`` is what holds the two
spellings equal, since a probe asking for a kind string that names nothing would answer "not
on this build" on every build there is.
"""

_NOTHING_TO_LEX: Final = "the database recorded no file to lex"
"""Why a probe can answer no lexemes on a build whose lexer is perfectly good."""


def lexer_probe(api: Any, db_path: str) -> dict[str, object]:
    """Whether this build's file entities answer ``Ent.lexer(False)`` (lean-code req 9.2).

    The duplicate-block and similar-routine rules read a lexical pass and nothing else, so
    what decides whether they can run on a build is whether one file of one database yields
    lexemes. ``doctor`` asks it of its own scratch database -- one file, created, added and
    analysed in a temporary directory -- so nothing here reads the operator's own code.

    It rides ``worker``'s ``catalogue`` operation, because that is where ``doctor`` already
    asks the build what it knows, and it is the one question in that operation that needs a
    database. The database is opened here and closed in a ``finally``, as ``worker._op_archs``
    does and for the reason that operation states: the API crashes the process when entities
    outlive their database.

    ``api`` arrives as an argument rather than through a :class:`LeanContext`, because the
    two things a context carries are a project-relative path function and an analysis root,
    and this probe has neither a project nor a root -- it has one scratch database and one
    question about the build that opened it.
    """
    db = api.open(db_path)
    try:
        return _first_lexed(api, db)
    finally:
        db.close()


def _first_lexed(api: Any, db: Any) -> dict[str, object]:
    """The lexeme count of the first file of ``db``, or zero and the reason there is none.

    Two ways to answer zero and they are different facts, so both carry their own sentence: a
    lexer that raised -- ``Ent.lexer`` documents ``UnderstandError`` when the source is gone
    or has changed since the parse, which is what :func:`_code_lines` reads as requirement
    5.8's unreadable file -- and a database with no file entity in it at all. The second is
    not a statement about the build, and a probe reporting it as one would refuse the token
    rules on an installation that runs them perfectly well.

    The count rather than a flag, because an empty stream is not an answer: a build whose
    lexer returned nothing would read as available on a boolean, and the two rules would then
    report nothing on every run while looking as though they had looked.
    """
    for ent in db.ents(LEXER_PROBE_KIND):
        try:
            lexemes = list(ent.lexer(False).lexemes())
        except api.UnderstandError as refused:
            return {"lexemes": 0, "detail": str(refused)}
        return {"lexemes": len(lexemes), "detail": ""}
    return {"lexemes": 0, "detail": _NOTHING_TO_LEX}
