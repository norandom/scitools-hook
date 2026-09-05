"""The remediation-hint catalogue: what an agent should DO about a finding (req 7.2).

A finding without a hint tells an agent that something is wrong; a finding with a hint tells
it what to change. The catalogue is therefore written in the imperative and names a concrete
refactoring ("extract the inner block into its own routine"), never a target number: raising
the limit is never the remedy, and a hint that says "reduce complexity" is worth nothing.

Lookup runs through four levels, most specific first::

    variant "analysis.parse_error/type_params"               (only where a finding names one)
    rule    "routine.CountLineCode", "structure.file_cycle", "codecheck.CPP_F016"
    metric  "CountLineCode", "file_cycle", "CPP_F016"        (the name part of the rule)
    generic "generic.threshold" | "generic.ratchet" | "generic.structural" | "generic.codecheck"
            | "generic.parse"

The variant level exists for one rule and would not be worth having for a threshold. A parse
error is the one finding whose remedy is not a refactoring at all but a **rewrite of a
specific construct**, and which construct it is decides the whole of the answer: "rewrite the
type-parameter list as an explicit TypeVar" and "write `X: TypeAlias = ...` instead of `type
X = ...`" are different edits, and neither is guessable from "this file failed to parse". The
pipeline identifies the construct from the analysed source and leaves it in
``Finding.details["construct"]``; :data:`PARSE_CONSTRUCTS` is the classifier and the keys
below carry one hint each. A construct nobody recognised simply falls through to the rule
level, which is still actionable.

The rule level exists because the same metric means different things per scope -- a routine
over 60 lines is split into routines, a file over 500 lines is split into modules -- while the
metric level keeps one wording for every scope that has no reason to differ. A stats-prefixed
rule (``project.AVG:CyclomaticStrict``) also tries the bare metric, so ``MEDIAN:CountStmt``
inherits the ``CountStmt`` advice.

Operator overrides (``[hints]`` in configuration, ``Settings.hints``) are merged over the
defaults key by key, at any of the three levels. The *order* of the levels still stands above
the source of the text: overriding ``CountLineCode`` does not change ``routine.CountLineCode``,
which ships its own hint. That is deliberate and discoverable -- every finding prints its rule
name, which is exactly the key to override.

``hint`` never raises: a rule name outside the grammar simply falls through to the generic
text for the finding's kind, because a renderer must not fail on a badly configured key.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from typing import Final

from scitools_hook.config.metric_names import format_metric_name
from scitools_hook.errors import ConfigError
from scitools_hook.models.findings import (
    PARSE_ERROR_RULE,
    Finding,
    FindingKind,
    parse_rule_name,
)

GENERIC_KEYS: Final[dict[FindingKind, str]] = {
    "threshold": "generic.threshold",
    "ratchet": "generic.ratchet",
    "structural": "generic.structural",
    "codecheck": "generic.codecheck",
    "parse": "generic.parse",
}
"""Catalogue key of the last-level fallback per finding kind; overridable like any other."""

VARIANT_SEPARATOR: Final = "/"
"""Joins a rule to the construct a finding names: ``analysis.parse_error/type_params``.

A ``/`` rather than a ``.``, so a variant key can never be mistaken for -- or collide with --
a rule name: ``.`` is the rule grammar's own separator and ``analysis.parse_error.type_params``
would parse as the analysis rule ``parse_error.type_params``, which does not exist.
"""

PARSE_CONSTRUCTS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("type_params", re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+\w+\s*\[")),
    ("type_alias", re.compile(r"^\s*type\s+\w+\s*[\[=]")),
    ("except_star", re.compile(r"^\s*except\s*\*")),
)
"""The constructs Understand 6.5.1204 refuses, matched against the line the parse stopped at.

**Understand 7.2 added all four to its parser** (its release notes: "type parameters, type
alias declarations, except-star blocks"), and 8.0.1262 -- the version this project now
targets -- was measured on 2026-09-05 with the same thirteen files under the Python 3
dialect the wrapper pins: ``Analyze Completed (Errors:0 Warnings:0)``. No parse error means
no point for the cascade to start from. Whether every routine after such a construct is
present in an 8.0 database was not confirmed the same day, because the API licence was not
available for the check; the hints below say which version they describe so an agent on 8.0
re-runs rather than rewrites.

**Measured, one file per construct, with a Python 3 interpreter on ``PATH``** -- which matters,
because without one Understand analyses Python 2 and a far wider set of ordinary code fails
(task 11.10). Under Python 3 these four declarations, and no others of the thirteen tried,
aborted the parse::

    def generic[T](x: T) -> T:   expected token '(' at token [        line 1
    class Box[T]:                expected token ':' at token [        line 1
    type Alias = int             expected newline at token Alias      line 1
    except* ValueError:          expected token ':' at token *        line 4

The nine that parse cleanly are worth recording too, because three of them look like the same
family and are not: ``match``/``case``, ``except`` without the star, an f-string with nested
same quotes (PEP 701), ``[first, *rest]``, ``{**a, "b": b}``, the walrus, positional-only
parameters, parenthesized context managers, and ``int | None`` annotations. ``[first, *rest]``
is the one to remember: it *does* fail under the Python 2 dialect, which is how task 10.4 came
to record it as a hazard, and it is not a 3.12 problem at all.

Matched on the source line rather than on ``und``'s message because the message for the type
alias names the alias (``at token Alias``) and not the construct, so three of the four would be
identifiable from it and the fourth would not. The order is irrelevant -- no line can match two
of these -- but the anchors are not: leading whitespace and then the keyword itself keeps
``mytype x = 1`` out of it, as far as looking at one line can.
"""

_LAST_RESORT: Final = (
    "fix the code the finding names so the rule holds; do not relax the limit to hide it"
)
"""Used only when even the generic key was overridden with an empty string."""

_ROUTINE_HINTS: Final[dict[str, str]] = {
    "CyclomaticStrict": (
        "too many decision points in one routine: extract each group of related decisions "
        "into its own named routine, and replace boolean flag parameters with separate routines"
    ),
    "CyclomaticModified": (
        "collapse the case arms into a lookup table or a polymorphic call and leave only the "
        "dispatch in this routine"
    ),
    "Essential": (
        "unstructured control flow: extract the block that is jumped out of into a routine "
        "that returns early, so the break/continue/goto disappears"
    ),
    "MaxNesting": (
        "extract the inner block into its own routine, or invert the condition and return "
        "early so the body stops nesting"
    ),
    "CountStmt": (
        "split the routine into named steps: any block that needs a comment to explain it is "
        "a routine waiting to be extracted"
    ),
    "CountParams": (
        "group the parameters that always travel together into one object, or split the "
        "routine -- a long parameter list usually means two routines in one"
    ),
    "CountPath": (
        "the number of paths through the routine explodes: split it at its top-level branches "
        "so each part has one job"
    ),
    "routine.CountLineCode": (
        "the routine is longer than one screen: extract its phases into named routines and "
        "leave this one as the outline"
    ),
}

_CLASS_HINTS: Final[dict[str, str]] = {
    "CountDeclMethod": (
        "the class does too many things: move the methods that share a subset of the fields "
        "into a class of their own"
    ),
    "CountDeclMethodNonStub": (
        "beyond its accessors the class carries too much behaviour: extract one responsibility "
        "into a collaborator and delegate to it"
    ),
    "CountDeclInstanceVariable": (
        "the class holds too much state: group the fields that change together into a value "
        "object and hold that instead"
    ),
    "MaxInheritanceTree": (
        "the inheritance chain is too deep: replace one layer with composition -- hold the "
        "base as a field and delegate to it"
    ),
    "CountClassDerived": (
        "too many subclasses hang off this class: replace the variation with a strategy object "
        "passed in, so a new case adds data instead of a type"
    ),
    "CountClassCoupled": (
        "the class talks to too many others: hide one cluster of collaborators behind a facade, "
        "or move the behaviour next to the data it uses"
    ),
    "PercentLackOfCohesion": (
        "the methods use disjoint groups of fields: split the class along those groups, so each "
        "part's methods share its state"
    ),
}

_FILE_HINTS: Final[dict[str, str]] = {
    "file.CountLineCode": (
        "the file holds too much: move one cohesive group of routines or classes into a new "
        "module beside it"
    ),
    "CountDeclFunction": (
        "too many functions in one file: move a cohesive group into a new module and import it"
    ),
    "CountDeclClass": (
        "keep one public class per file: move the extra classes into modules of their own"
    ),
    "file.MaxCyclomaticStrict": (
        "the most complex routine in this file is over the limit: simplify that routine first "
        "by extracting its branches into named routines"
    ),
    "RatioCommentToCode": (
        "too little explanation: state at the top of the module, and on each exported routine, "
        "why it exists -- not what the code already says"
    ),
}

_PROJECT_HINTS: Final[dict[str, str]] = {
    "project.AVG:CyclomaticStrict": (
        "the average routine in the project is too branchy: fix the worst routines this run "
        "reports and the average follows"
    ),
    "project.AVG:CountLineCode": (
        "routines are on average too long: extract steps out of the longest routines this run "
        "lists, starting at the top"
    ),
    "project.MaxCyclomaticStrict": (
        "one routine dominates the project's complexity: split the routine this run names into "
        "smaller routines"
    ),
    "project.MaxNesting": (
        "the deepest routine in the project nests too far: extract its inner blocks or return "
        "early there"
    ),
}

_STRUCTURE_HINTS: Final[dict[str, str]] = {
    "structure.file_cycle": (
        "break the cycle: invert one dependency -- move the shared type into a module both "
        "files can import, or pass it in instead of importing back"
    ),
    "structure.arch_cycle": (
        "two architecture nodes depend on each other: move the shared code into a lower node "
        "both may use, or define the interface in the lower node and implement it above"
    ),
    "structure.call_cycle": (
        "these routines call each other in a cycle: give the recursion one entry point that "
        "the others do not call back into, or turn the mutual recursion into an explicit loop"
    ),
    "structure.layer": (
        "this edge crosses a layer boundary the architecture forbids: depend downwards only -- "
        "put an interface in the lower layer and inject the implementation from above"
    ),
    "structure.fan_in": (
        "so many entities depend on this one that it has become a hub: split it along the "
        "groups of clients that use different parts of it"
    ),
    "structure.fan_out": (
        "this entity depends on too many others: hide one cluster of them behind a single "
        "collaborator and depend on that instead"
    ),
    "structure.reachable_complexity": (
        "a reviewer of this routine has to hold everything it calls in their head: extract the "
        "deepest branch of the subtree behind a narrower collaborator, or simplify the "
        "routines it reaches -- the finding lists them"
    ),
    "structure.new_dependencies": (
        "the change adds too many dependencies to one file: keep the new code in a module of "
        "its own, or depend on an existing abstraction instead of on each collaborator"
    ),
    "structure.duplicate_definition": (
        "this value is written out in several files, so changing the policy means finding "
        "every copy: move it to one module and import it, and check the copies agree before "
        "you do -- a disagreeing copy is a decision somebody made and did not write down"
    ),
    "structure.coupling": (
        "these two architecture nodes reference each other too often: move the code that does "
        "the referencing next to what it uses, or narrow the traffic to one interface"
    ),
}

_SHARED_METRIC_HINTS: Final[dict[str, str]] = {
    "CountLineCode": (
        "too much code in one unit: split it into smaller units that each do one thing"
    ),
    "MaxCyclomaticStrict": (
        "the most complex routine inside this element is over the limit: simplify that routine "
        "by extracting its branches"
    ),
}
"""Metrics that appear in several scopes and read the same way in all of them."""

_PARSE_HINTS: Final[dict[str, str]] = {
    PARSE_ERROR_RULE: (
        "Understand stopped reading this file at the line named and never saw the rest of it, "
        "so nothing below that line was checked: rewrite the construct there in a spelling "
        "Understand parses, then re-run. Suppressing the finding leaves the file "
        "unmeasured, which is the state that lets a real violation through"
    ),
    f"{PARSE_ERROR_RULE}{VARIANT_SEPARATOR}type_params": (
        "PEP 695 type parameters: Understand 6.5 cannot parse a type-parameter list (8.0 can; "
        "re-run there), and one "
        "of them costs the rest of the file. Declare the variable explicitly instead -- "
        '`T = TypeVar("T")` at module level, then `def generic(x: T) -> T:` and '
        "`class Box(Generic[T]):` -- which is the same type with a spelling the analysis reads"
    ),
    f"{PARSE_ERROR_RULE}{VARIANT_SEPARATOR}type_alias": (
        "PEP 695 `type X = ...`: Understand 6.5 cannot parse the `type` statement (8.0 can; "
        "re-run there), and it "
        "costs the rest of the file. Write the alias as an assignment instead -- "
        "`X: TypeAlias = ...`, or a plain `X = ...` -- which means the same thing to a type "
        "checker and leaves the file readable"
    ),
    f"{PARSE_ERROR_RULE}{VARIANT_SEPARATOR}except_star": (
        "PEP 654 `except*`: Understand 6.5 cannot parse it (8.0 can; re-run there), and it costs "
        "the rest of the "
        "file. Catch the group with a plain `except ExceptionGroup as group:` and dispatch on "
        "its `.exceptions` inside, until the analyser catches up"
    ),
}
"""What to do about each construct :data:`PARSE_CONSTRUCTS` recognises (req 7.2).

Every one of them names the *rewrite*, not the diagnosis, because the finding's message
already carries Understand's own words. None of them says "ignore it": a file that does not
parse is a file that was not checked, and the remedy for that is never a configuration key.
"""

_GENERIC_HINTS: Final[dict[str, str]] = {
    GENERIC_KEYS["threshold"]: (
        "the value is outside its limit: change the code until the metric falls back inside "
        "it -- raising the limit is not a fix"
    ),
    GENERIC_KEYS["ratchet"]: (
        "this value got worse in this change: bring it back to at most what it was before, "
        "for instance by putting the new code in a new routine instead of growing this one"
    ),
    GENERIC_KEYS["structural"]: (
        "the change made the dependency structure worse: move or invert the dependency the "
        "finding names instead of adding another one"
    ),
    GENERIC_KEYS["codecheck"]: (
        "fix the flagged line as the check's description in Understand explains (the finding "
        "carries the check name); switch the check off in configuration only by team decision"
    ),
    GENERIC_KEYS["parse"]: (
        "this file was not read, so nothing in it was checked: make it parse before trusting "
        "any result about it"
    ),
}

DEFAULT_CATALOGUE: Final[dict[str, str]] = {
    **_ROUTINE_HINTS,
    **_CLASS_HINTS,
    **_FILE_HINTS,
    **_PROJECT_HINTS,
    **_STRUCTURE_HINTS,
    **_PARSE_HINTS,
    **_SHARED_METRIC_HINTS,
    **_GENERIC_HINTS,
}
"""Every shipped hint, keyed at the level it belongs to (req 7.2)."""


class HintCatalogue:
    """The shipped hints with the operator's overrides merged over them."""

    def __init__(self, overrides: Mapping[str, str]) -> None:
        """``overrides`` is ``Settings.hints``; it is copied, so later edits do not leak in."""
        self._hints: dict[str, str] = {**DEFAULT_CATALOGUE, **overrides}

    def hint(self, rule: str, finding: Finding) -> str:
        """The remediation text for ``rule``, most specific level first.

        ``finding`` supplies the kind that selects the generic fallback and the construct that
        selects a variant; the lookup itself follows ``rule``, so a caller may ask for the text
        of a rule other than the one the finding carries.
        """
        for key in _keys(rule, finding):
            text = self._hints.get(key)
            if text:
                return text
        return _LAST_RESORT


def construct_of(source_line: str) -> str:
    """Which of :data:`PARSE_CONSTRUCTS` ``source_line`` declares, or ``""`` for none.

    A pure function of one line of text, so the caller that has the analysed source -- the
    check pipeline -- decides where the line comes from and this module stays a catalogue.
    """
    for name, pattern in PARSE_CONSTRUCTS:
        if pattern.match(source_line):
            return name
    return ""


def _keys(rule: str, finding: Finding) -> Iterator[str]:
    """The catalogue keys to try: the variant, the rule, its name part, then the generic key."""
    construct = finding.details.get("construct")
    if isinstance(construct, str) and construct:
        yield f"{rule}{VARIANT_SEPARATOR}{construct}"
    yield rule
    yield from _name_keys(rule)
    yield GENERIC_KEYS[finding.kind]


def _name_keys(rule: str) -> tuple[str, ...]:
    """The metric-level keys of ``rule``; empty when ``rule`` is outside the grammar."""
    try:
        parsed = parse_rule_name(rule)
    except ConfigError:
        return ()
    if parsed.metric is not None:
        formatted = format_metric_name(parsed.metric)
        return (formatted, parsed.metric.metric) if parsed.metric.prefix else (formatted,)
    return (parsed.name,) if parsed.name else ()
