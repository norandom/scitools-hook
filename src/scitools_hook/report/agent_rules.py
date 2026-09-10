"""The rules an agent reads before it writes code, and how they get into its file (req 10).

Two pure functions. :func:`render_rules` turns the effective configuration into a Markdown
snippet -- the limits, the structural rules, the lean-code family and its ladder, the
ratchet, the two commands, the JSON fields and what to do about a blocked commit -- and
:func:`insert_between_markers` puts that
snippet into a file the operator already owns, between the markers this module names, so a
regeneration updates the block instead of appending a second copy (req 10.3).

Like the other renderers this returns a plain string with no trailing newline and no
``rich``: the CLI prints or writes it raw. The decisions worth knowing about:

* **Determinism is the contract, not a nicety** (req 10.2). The snippet is meant to be
  committed to an agent instructions file, so an identical effective configuration must
  produce identical bytes -- otherwise every run of ``agent-rules --write`` shows up as a
  diff. Nothing here reads the clock, the environment, the filesystem or a version number,
  and nothing iterates a dict whose order came from a TOML file: the thresholds are sorted
  by scope (in the canonical :data:`~scitools_hook.config.metric_names.SCOPES` order) and
  then by metric name, the fan limits follow :data:`~scitools_hook.config.models.FAN_KEYS`,
  and the layer and coupling rules are sorted by their own names. The only machine-specific
  string a configuration could carry -- ``understand.home``, the baseline path -- is never
  printed, because it is not a rule.
* **The effective limit is the one printed**, not the configured one. A baseline only ever
  narrows a limit (task 4.5), so an agent told the configured number would chase a looser
  limit than the one being applied; the line therefore shows
  :attr:`~scitools_hook.models.findings.EffectiveThreshold.limit` and says where it came
  from. Only a baseline-sourced limit is attributed; saying "from the configuration" on
  every other line would be noise.
* **Only rules that are in force are described.** An empty fan table, an empty layer list,
  ``max_new_dependencies_per_file = None`` and an unset CodeCheck configuration each drop
  their line entirely: a rules document that lists rules nobody enabled teaches an agent to
  distrust the document. The two cycle rules cannot be switched off (they carry a severity,
  not an on/off switch), so they are always described. The one thing printed whether or not
  a rule is behind it is the lean-code ladder, and only because two of its rungs are the
  ones the Gate says outright it will never check for you (lean-code req 8.3).
* **The text is written at an agent, in the imperative.** Requirement 10.1 asks for plain
  language and a workflow, not a configuration dump, so a population threshold says what it
  is measured over, a warning says that it does not block, and the blocked-commit section
  says in as many words that raising a limit, adding an ignore pattern or re-capturing the
  baseline is not the fix.

Insertion is byte-preserving outside the markers. The region between the markers is
replaced; everything before the begin marker and everything from the end marker onward is
copied through untouched, including a missing trailing newline and any content that follows
the block. A file that does not yet contain the markers gets the block appended after a
blank line, with a newline first when its last line had none. Line endings follow the file:
a mostly-CRLF file gets CRLF lines, anything else gets LF. Anything ambiguous -- a begin
marker without an end marker, an end marker before its begin marker, a second copy of
either, or a snippet that contains a marker itself -- raises
:class:`~scitools_hook.errors.ConfigError` rather than guessing, because both plausible
guesses (update the first block, append a second) silently leave a stale block behind.
"""

from __future__ import annotations

import textwrap
from collections.abc import Iterable, Mapping, Sequence
from typing import Final

from scitools_hook.config.metric_names import (
    ELEMENT_SCOPES,
    SCOPES,
    Scope,
    format_metric_name,
)
from scitools_hook.config.models import (
    FAN_KEYS,
    CodeCheckSettings,
    CouplingRule,
    FanKey,
    LayerRule,
    LeanRules,
    Limit,
    Settings,
    StructureRules,
)
from scitools_hook.errors import ConfigError
from scitools_hook.models.findings import (
    EffectiveThreshold,
    StructureRuleName,
    structure_rule,
)
from scitools_hook.report.hints import DEFAULT_CATALOGUE
from scitools_hook.report.lean_examples import LEAN_RULES

BEGIN_MARKER: Final = "<!-- scitools-hook:begin -->"
"""Start of the region :func:`insert_between_markers` owns (req 10.3)."""

END_MARKER: Final = "<!-- scitools-hook:end -->"
"""End of that region; everything from here on is the operator's."""

_WIDTH: Final = 95
"""Where a generated bullet folds; the width the prose in this module is written at."""

_IGNORE_KEY: Final = "_ignore"
"""What a clause of a hint that is about configuration rather than about code names.

Every ignore list in ``[lean]`` and ``[structure]`` ends in it -- ``unused_parameters_ignore``,
``unused_classes_ignore``, ``duplicates_ignore`` -- so a clause mentioning one is recognised
without a second list of key names here to fall out of step with the settings model.
"""

_MARKER_HINT: Final = (
    f"the block is delimited by exactly one {BEGIN_MARKER} and one {END_MARKER}, "
    "in that order; fix the file or delete the block and regenerate it"
)

_SCOPE_HEADINGS: Final[dict[Scope, str]] = {
    "routine": "Routines (functions and methods)",
    "class": "Classes",
    "file": "Files",
    "project": "Project-wide",
    "arch": "Architecture nodes",
}
"""Scope -> the heading its limits are listed under, in :data:`SCOPES` order."""

_POPULATION_NOUN: Final[dict[Scope, str]] = {
    "routine": "all routines",
    "class": "all classes",
    "file": "all files",
    "project": "the whole project",
    "arch": "all architecture nodes",
}
"""What a population threshold of each scope is measured over."""

_FAN_SUBJECTS: Final[dict[FanKey, str]] = {
    "file_fan_in": "Fan-in of a file (the files that depend on it)",
    "file_fan_out": "Fan-out of a file (the files it depends on)",
    "class_fan_in": "Fan-in of a class (the classes that depend on it)",
    "class_fan_out": "Fan-out of a class (the classes it depends on)",
}

_TITLE: Final = """\
## Maintainability rules (scitools-hook)

This repository is gated by `scitools-hook`, which measures the code a change touches with
SciTools Understand and refuses a commit that makes it worse. These are the rules your work
is judged by; read them before you write code. Regenerate this block with
`scitools-hook agent-rules` whenever the configuration changes."""

_LIMITS_INTRO: Final = """\
## Limits

Each limit below is checked on the entities your change touches. An `error` blocks the
commit; a `warning` is reported but does not block."""

_NO_LIMITS: Final = "- no metric limits are configured; only the structural rules apply"

_LEAN_INTRO: Final = """\
## Lean code

Every limit above asks whether a piece of code is too complex. None of them asks whether it
should exist at all: a routine nothing calls once its replacement landed, a parameter nothing
reads, a wrapper that only forwards, an abstraction with one implementation, a file that
exports one name, the same twelve lines in three places. Each of those is inside every limit
above and costs a reader anyway, so this section is about cutting rather than simplifying."""

_LADDER: Final = """\
### Before you write it

Walk these in order and stop at the first that answers; only the last one writes code.

1. Does this need to exist at all?
2. Does it already exist in this codebase?
3. Does the standard library do it?
4. Does a native platform feature cover it?
5. Does an already-installed dependency solve it?
6. Can it be one line?
7. Then write the minimum that works.

Rungs 3, 4 and 5 are yours alone. No finding will ever carry either `stdlib:` or `native:`:
whether the standard library, the platform or a dependency this project already installs
would have done the job is a semantic question a reference database cannot ask. Their
absence from a report is not a clearance -- nobody checked them but you."""

_LEAN_RULES_INTRO: Final = """\
### The rules in force

The rules this repository has enabled: the severity each reports at, and the tag its hint
opens with. The tag is the edit being asked for -- `delete:` it is not used, `yagni:` it
should not have been written, `shrink:` the same work fits in less code."""

_NET_LINE: Final = """\
### The net line

A check with a before side ends with one line for the whole change:
`net: +12 lloc (+30 lines) over 7 routines`. That is logical lines added minus logical lines
removed, the source-line delta beside it, and the number of routines it was summed over.
`--all` has no before side, so it prints no net line.

A positive number is not a violation -- a feature costs code -- but it is the figure to argue
with. The usual reason it is positive is that the path the change replaced is still there:
delete that, and the tests that only covered it, before you accept the number."""

_RATCHET_INTRO: Final = """\
## The ratchet

A metric may not get worse than it was, even when it stays inside its limit. Taking
`CyclomaticStrict` on an existing routine from 4 to 6 is reported although the limit is 10:
the number to beat is the one that routine had before your change. Leave every metric where
it is, or make it better."""

_STRICT_ON: Final = (
    "Strict mode is on: a violation that was already there before your change also blocks\n"
    "the commit, so touching a file means fixing what is already wrong in it."
)

_STRICT_OFF: Final = (
    "Strict mode is off: a violation that was already there before your change is reported\n"
    "but does not block, as long as you do not make it worse."
)

_COMMANDS: Final = """\
## Check your own work

Run the gate yourself rather than learning about a violation from a rejected commit.

```sh
scitools-hook check --worktree   # your edits as they stand, before you stage anything
scitools-hook check --staged     # what you are about to commit; the hook runs this
```

Use `--worktree` while you are still editing and `--staged` once before you commit. Exit
code 0 means nothing blocks; exit code 1 means the change is blocked."""

_JSON: Final = """\
## Read the JSON output

```sh
scitools-hook check --worktree --format json
```

That prints one JSON document. `findings` is the array of everything the run found; each
entry carries `rule`, `scope`, `path`, `line`, `value`, `before`, `limit`, `severity`,
`blocking`, `preexisting`, `message` and `hint`. `preexisting` marks a violation that was
already there before this change. `hint` is the remediation text -- it says what to change,
so read it before you edit. `blocking_count` counts the findings that block the commit;
keep working until it is `0`."""

_BLOCKED: Final = """\
## When the gate blocks a commit

A blocking finding is an `error` your change introduced: the code was inside the rule
before you touched it and is outside it now. When a commit is blocked:

1. Read the `message` and the `hint` of every finding whose `blocking` is `true`.
2. Fix the code. Do not raise a limit, do not add an ignore pattern and do not re-capture
   the baseline to make a finding disappear -- those change the rules, not the code.
3. Re-run `scitools-hook check --worktree` until nothing blocks any more.
4. Stage the change and commit; the hook runs `scitools-hook check --staged` again.

A `warning` never blocks, and a pre-existing violation blocks only in strict mode. Fixing
either is welcome, but neither is what a blocked commit is asking you to do.

One blocking finding is not about a limit at all. `analysis.parse_error` means the analyser
could not read a file your change is adding or editing, so nothing after the line it names
was measured and no rule ran on it -- an empty report about that file is not a clean one. Its
`hint` names the construct to rewrite. Do not silence it: a file that does not parse is a
file nobody checked."""


def render_rules(settings: Settings, effective: Sequence[EffectiveThreshold]) -> str:
    """Render the effective configuration as a Markdown snippet for an agent (req 10.1).

    ``effective`` is what ``analysis.baseline.apply`` produced, so the limits shown are the
    ones the run will actually enforce. The result is deterministic for a given effective
    configuration (req 10.2) and carries no trailing newline; embedding it in a file is
    :func:`insert_between_markers`' job.
    """
    return "\n\n".join(
        [
            _TITLE,
            _limits_section(effective),
            _structure_section(settings.structure, settings.codecheck),
            _lean_section(settings.lean, {**DEFAULT_CATALOGUE, **settings.hints}),
            _ratchet_section(settings, effective),
            _COMMANDS,
            _JSON,
            _BLOCKED,
        ]
    )


def insert_between_markers(existing: str, snippet: str) -> str:
    """Put ``snippet`` between the markers in ``existing``, leaving every other byte (10.3).

    Replaces the region between an existing pair of markers, or appends a fresh marked block
    when the text has none. Raises :class:`ConfigError` when the markers are unbalanced,
    out of order or duplicated, or when ``snippet`` contains a marker itself.
    """
    _reject_markers_in_snippet(snippet)
    newline = _newline_style(existing)
    inner = f"{newline}{_as_body(snippet, newline)}{newline}"
    span = _marker_span(existing)
    if span is None:
        return _append_block(existing, inner, newline)
    start, stop = span
    return f"{existing[:start]}{inner}{existing[stop:]}"


# --- the snippet ----------------------------------------------------------------


def _limits_section(effective: Sequence[EffectiveThreshold]) -> str:
    """Every effective limit, grouped by scope and sorted inside each group."""
    lines = [_LIMITS_INTRO]
    for scope in SCOPES:
        in_scope = sorted(
            (item for item in effective if item.spec.scope == scope), key=_threshold_key
        )
        if not in_scope:
            continue
        lines.append(f"\n### {_SCOPE_HEADINGS[scope]}\n")
        lines.extend(f"- {_threshold_line(item)}" for item in in_scope)
    if len(lines) == 1:
        lines.append(f"\n{_NO_LIMITS}")
    return "\n".join(lines)


def _threshold_key(threshold: EffectiveThreshold) -> tuple[str, str, str]:
    """A total order inside a scope: metric name, then the limit, then the severity."""
    return (
        format_metric_name(threshold.metric),
        _limit_text(threshold.limit),
        threshold.spec.severity,
    )


def _threshold_line(threshold: EffectiveThreshold) -> str:
    """``\\`Metric\\`: at most 10 (error)`` plus the notes that line needs."""
    name = format_metric_name(threshold.metric)
    head = f"`{name}`: {_limit_text(threshold.limit)} ({threshold.spec.severity})"
    notes = [note for note in (_population_note(threshold), _source_note(threshold)) if note]
    return head if not notes else f"{head} -- {'; '.join(notes)}"


def _population_note(threshold: EffectiveThreshold) -> str:
    """What a threshold is measured over, when it is not one entity's own value (req 3.4)."""
    scope = threshold.spec.scope
    noun = _POPULATION_NOUN[scope]
    if threshold.metric.prefix is not None:
        return f"`{threshold.metric.prefix}` over {noun}"
    return f"measured over {noun}" if scope not in ELEMENT_SCOPES else ""


def _source_note(threshold: EffectiveThreshold) -> str:
    """Where the limit came from, said only when it is not the configuration (task 4.5)."""
    if threshold.source != "baseline":
        return ""
    return "from the baseline, not from the configuration"


def _limit_text(limit: Limit) -> str:
    """``at most 10``, ``at least 0.1`` or ``between 1 and 200``; a limit always has one."""
    if limit.max is not None and limit.min is not None:
        return f"between {_number(limit.min)} and {_number(limit.max)}"
    if limit.max is not None:
        return f"at most {_number(limit.max)}"
    return f"at least {_number(limit.min)}" if limit.min is not None else "unbounded"


def _structure_section(structure: StructureRules, codecheck: CodeCheckSettings) -> str:
    """The structural rules that are in force, and only those (req 6.1-6.6, 6.9, lean 8.5)."""
    bullets = [f"- {rule}" for rule in _structure_rules(structure, codecheck)]
    return "\n".join([_structure_intro(structure), "", *bullets])


def _structure_rules(structure: StructureRules, codecheck: CodeCheckSettings) -> list[str]:
    """Every structural rule in force, in the order the section lists them."""
    return [
        *_cycle_rules(structure),
        *_fan_rules(structure),
        *_new_dependency_rules(structure),
        *_layer_rules(structure.layers),
        *_coupling_rules(structure.coupling),
        *_call_graph_rules(structure),
        *_project_wide_rules(structure),
        *_codecheck_rules(codecheck),
    ]


def _cycle_rules(structure: StructureRules) -> list[str]:
    """The two rules that cannot be switched off; both carry a severity and no switch."""
    return [
        f"New import or include cycles between files are reported ({structure.file_cycles})",
        f"New cycles between the architecture nodes of `{structure.architecture}` are "
        f"reported ({structure.arch_cycles})",
    ]


def _structure_intro(structure: StructureRules) -> str:
    """What the rules below are measured over; the call-graph line only when one is listed.

    Both call-graph rules ship off, so the sentence about them is printed only when one of
    them is: a document that describes what a rule nobody enabled applies to has listed the
    rule, and this module refuses to list one.

    It asks :func:`_call_graph_rules` the same question the bullet list asks, rather than
    taking a flag from the caller, so the sentence and the bullets cannot come apart.
    """
    lines = [
        "## Structural rules",
        "",
        "These are about how the code fits together, not about one entity's own numbers.",
        f"The rules that group by architecture use `{structure.architecture}`; "
        "the file-level ones apply to every file.",
    ]
    if _call_graph_rules(structure):
        lines.append("The call-graph ones apply to every routine your change touches.")
    return "\n".join(lines)


def _call_graph_rules(structure: StructureRules) -> list[str]:
    """The two rules that read which routine calls which, each only when it is on (8.5).

    Both ship off and both were missing from this snippet entirely, which is worse than a
    rule an agent disagrees with: a rule nobody is told about is one nobody can satisfy.

    ``reachable_complexity`` prints its own ``max`` rather than :func:`_limit_text`, because
    ``evaluate_reachable_complexity`` judges the maximum and nothing else -- a limit carrying
    only a minimum switches the rule off there, and "at least 5" would describe a rule that
    does not run.
    """
    rules = []
    limit = structure.reachable_complexity
    if limit is not None and limit.max is not None:
        rules.append(
            "Reachable complexity of a routine (its own `CyclomaticStrict` and that of "
            f"everything it transitively calls): at most {_number(limit.max)} "
            f"({structure.reachable_complexity_severity})"
        )
    if structure.call_cycles is not None:
        rules.append(
            f"Two or more routines that call one another are reported "
            f"({structure.call_cycles}); a routine that calls itself is not a cycle"
        )
    return rules


def _project_wide_rules(structure: StructureRules) -> list[str]:
    """Unused routines and scattered definitions: two questions asked of the whole project.

    Both are decided over the database rather than over the change -- a routine called from a
    file this commit never touched is used -- so the line says so, or an agent reads a
    finding as a claim about its own diff and goes looking in the wrong place (8.5).
    """
    rules = []
    if structure.unused_routines is not None:
        rules.append(
            "A routine your change touches that nothing in the whole project calls or uses "
            f"is reported ({structure.unused_routines})"
        )
    limit = structure.duplicate_definitions
    if limit is not None:
        rules.append(
            f"A module-level name bound to the same value in more than {limit} files is "
            f"reported ({structure.duplicate_definitions_severity})"
        )
    return rules


def _fan_rules(structure: StructureRules) -> list[str]:
    """Fan limits in canonical key order; an unconfigured direction says nothing (6.4)."""
    return [
        f"{_FAN_SUBJECTS[key]}: {_limit_text(structure.fan[key])} ({structure.fan_severity})"
        for key in FAN_KEYS
        if key in structure.fan
    ]


def _new_dependency_rules(structure: StructureRules) -> list[str]:
    """The new-dependencies-per-file limit, when one is set (req 6.5)."""
    limit = structure.max_new_dependencies_per_file
    if limit is None:
        return []
    return [
        f"One file may gain at most {limit} new dependencies in a single change "
        f"({structure.new_dependencies_severity})"
    ]


def _layer_rules(layers: Sequence[LayerRule]) -> list[str]:
    """Layer rules sorted by name then node, so the list never rides on config order (6.3)."""
    ordered = sorted(layers, key=lambda rule: (rule.name, rule.node))
    return [_layer_rule(rule) for rule in ordered]


def _layer_rule(rule: LayerRule) -> str:
    """One layer rule; an empty allow-list means the node may gain no dependency at all."""
    head = f"Layer rule `{rule.name}`: `{rule.node}` may"
    if not rule.may_depend_on:
        return f"{head} not gain a dependency on any other architecture node ({rule.severity})"
    allowed = ", ".join(f"`{node}`" for node in rule.may_depend_on)
    return f"{head} depend only on {allowed} ({rule.severity})"


def _coupling_rules(coupling: Sequence[CouplingRule]) -> list[str]:
    """Node-pair reference limits, sorted by the pair they constrain (req 6.6)."""
    ordered = sorted(coupling, key=lambda rule: (rule.from_node, rule.to_node, rule.max_refs))
    return [
        f"Coupling `{rule.from_node}` -> `{rule.to_node}`: at most {rule.max_refs} "
        f"references ({rule.severity})"
        for rule in ordered
    ]


def _codecheck_rules(codecheck: CodeCheckSettings) -> list[str]:
    """The CodeCheck configuration, named only when one is configured (req 6.9)."""
    if not codecheck.config:
        return []
    return [
        f"The CodeCheck configuration `{codecheck.config}` runs on the files you change "
        f"({codecheck.severity})"
    ]


def _lean_section(lean: LeanRules, hints: Mapping[str, str]) -> str:
    """The lean-code family: the ladder and the net line always, the rules when any is on.

    One of the three parts is conditional and two are not. The **ladder** is the agent's own
    work -- rungs 3, 4 and 5 ask whether the standard library, the platform or an installed
    dependency already does this, which no reference database can answer -- so it prints
    whatever the configuration says, together with the promise that no finding will ever
    carry ``stdlib:`` or ``native:`` (lean-code req 8.3). Nothing else in the snippet, and no
    finding, raises that question. The **net line** prints unconditionally too: requirement
    7.1 computes the delta whenever a check has a before side and attaches no condition about
    this family, and 7.6 shows the author scoping by enablement where they meant to. A line
    the report prints in the shipped default and this document declines to explain would be
    the worse failure of the two.

    The **rules in force** are the conditional part, because the whole family ships off and
    naming a rule nobody enabled is what teaches an agent to distrust the document.

    ``hints`` is the shipped catalogue under the operator's ``[hints]`` table, so the tag
    beside a rule is the tag its findings will carry even where 8.6 retagged one. The design
    sketches ``_lean_section(lean, effective)``; the thresholds are not read, because the
    family's two shrink *metrics* are ordinary limits, listed under Limits already, and
    naming them here would put lean-code lines in a snippet with the family switched off.
    """
    parts = [_LEAN_INTRO, _LADDER]
    rules = _lean_rule_lines(lean, hints)
    if rules:
        parts.append("\n".join([_LEAN_RULES_INTRO, "", *rules]))
    parts.append(_NET_LINE)
    return "\n\n".join(parts)


def _lean_rule_lines(lean: LeanRules, hints: Mapping[str, str]) -> list[str]:
    """One line per enabled rule, in the family's own order (req 8.4).

    The order is :data:`~scitools_hook.report.lean_examples.LEAN_RULES` rather than the order
    :class:`~scitools_hook.config.models.LeanRules` declares its fields in, so the snippet
    reads the same as the documentation and cannot pick up a configuration's order.

    A rule in the family with no entry in :func:`_lean_labels` raises ``KeyError`` here rather
    than dropping out of the list: the family is a compile-time constant, and the parametrised
    test over ``LEAN_RULES`` is what stops a tenth rule reaching an operator as a silence.
    """
    labels = _lean_labels(lean)
    lines = []
    for name in LEAN_RULES:
        label = labels[name]
        if not label:
            continue
        rule = structure_rule(name)
        lines.append(_lean_rule_line(rule, label, hints.get(rule, "")))
    return lines


def _lean_labels(lean: LeanRules) -> dict[StructureRuleName, str]:
    """Each lean rule's severity as it is printed; ``""`` is a rule that is switched off.

    The one place the family's rule names and its configuration keys meet. They differ by
    more than a plural -- ``duplicate_block`` is configured as ``duplicates`` and
    ``similar_routine`` as ``similar_routines`` -- so the mapping has to be written down
    once, and this is the only copy.
    """
    return {
        "unused_parameter": lean.unused_parameters or "",
        "unused_class": lean.unused_classes or "",
        "unused_variable": lean.unused_variables or "",
        "pass_through": lean.pass_through or "",
        "single_implementation": lean.single_implementation or "",
        "over_export": lean.over_export or "",
        "duplicate_block": lean.duplicates or "",
        "similar_routine": lean.similar_routines or "",
        "net_growth": _net_growth_label(lean),
    }


def _net_growth_label(lean: LeanRules) -> str:
    """The one rule in the family carrying a number, and the number is its switch (req 7.5).

    ``net_growth_severity`` has a value whether or not the rule is on, so the severity cannot
    say whether it is; ``max_net_growth`` is what an operator sets to turn it on, and ``0`` is
    a legal maximum meaning "this change may not make the project longer".
    """
    if lean.max_net_growth is None:
        return ""
    return f"{lean.net_growth_severity}, at most +{lean.max_net_growth} lloc per change"


def _lean_rule_line(rule: str, label: str, hint: str) -> str:
    """``- `structure.pass_through` (warning), `yagni:` -- delete the routine and ...``.

    The hint is quoted whole but for its ignore-list advice (:func:`_quoted`). An earlier
    draft of this task kept only the first sentence, and that is the one edit this line must
    not make: three of the nine hints carry a **behavioural exclusion** in their second
    sentence -- an overridden signature keeps its parameter, a constant read by an importer
    outside the project stays, a class a registry reaches is not dead -- and each of those
    sentences exists because task 2.3's review caught the hint telling an agent to delete
    working code without it. Truncated here they would be missing from the one document an
    agent reads *before* it writes anything.
    """
    head = f"- `{rule}` ({label})"
    text = _quoted(hint)
    if not text:
        return head
    tag = _tag_of(text)
    if not tag:
        return _wrapped(f"{head} -- {text}")
    return _wrapped(f"{head}, `{tag}` -- {text.removeprefix(tag).strip()}")


def _quoted(hint: str) -> str:
    """``hint`` without the clauses that name an ignore list, and without anything else cut.

    An ignore list is a configuration decision and this section is read before any code is
    written, so ``add its name to lean.unused_variables_ignore`` is the one part of a hint
    that does not belong here. Everything else does, exclusions first.

    A sentence that is *nothing but* that advice is kept whole all the same, because in
    ``structure.unused_class`` the exclusion is the subordinate clause of the sentence that
    names the list -- "if a registry, an entry point or a test collection reaches it" -- and
    dropping the clause would drop the exclusion with it.
    """
    return ". ".join(_without_ignore_advice(part) for part in hint.strip().split(". "))


def _without_ignore_advice(sentence: str) -> str:
    """One sentence without any ``; ...`` clause naming an ignore list; never emptied."""
    kept = [clause for clause in sentence.split("; ") if _IGNORE_KEY not in clause]
    return "; ".join(kept) if kept else sentence


def _wrapped(line: str) -> str:
    """One bullet folded to the width the rest of the snippet is written at.

    The other structural bullets are short statements about a number; these carry a sentence
    of a hint and run to twice the width of any prose in the document, which is what makes a
    generated block read as machine output. Hyphens and long words are left alone, so the
    ``--`` of a hint and a backticked rule name are never broken across lines. The caller
    always passes a bullet, so there is no empty-input case to guard: ``textwrap.wrap``
    returns a list of one for anything shorter than the width.
    """
    return "\n".join(
        textwrap.wrap(
            line,
            width=_WIDTH,
            subsequent_indent="  ",
            break_on_hyphens=False,
            break_long_words=False,
        )
    )


def _tag_of(hint: str) -> str:
    """The ponytail tag a hint opens with, or ``""`` when it opens with prose (req 8.1).

    Read off the hint rather than kept in a table beside it, so an operator who retags a rule
    through ``[hints]`` retags it here too. A hint that carries no tag -- which only an
    override can produce -- prints no tag rather than the first word of a sentence.
    """
    word = hint.split(" ", 1)[0]
    return word if word.endswith(":") else ""


def _ratchet_section(settings: Settings, effective: Iterable[EffectiveThreshold]) -> str:
    """The ratchet in plain words, strict mode, and the limits it does not cover (4.4, 4.7)."""
    parts = [
        _RATCHET_INTRO,
        "",
        _STRICT_ON if settings.ratchet.strict else _STRICT_OFF,
    ]
    excluded = sorted({item.rule for item in effective if not item.spec.ratchet})
    if excluded:
        names = ", ".join(f"`{rule}`" for rule in excluded)
        parts.extend(
            ["", f"These limits are not ratcheted, so a value inside them may move: {names}."]
        )
    return "\n".join(parts)


def _number(value: float) -> str:
    """A limit without the trailing ``.0`` an integral threshold would otherwise carry."""
    return f"{value:g}"


# --- marker insertion -----------------------------------------------------------


def _reject_markers_in_snippet(snippet: str) -> None:
    """A marker inside the block would make every later regeneration ambiguous."""
    if BEGIN_MARKER in snippet or END_MARKER in snippet:
        raise ConfigError(
            "the generated snippet must not contain the scitools-hook markers itself",
            hint=_MARKER_HINT,
        )


def _marker_span(existing: str) -> tuple[int, int] | None:
    """The region between the markers, or ``None`` when the text has neither.

    Anything else -- one marker without the other, either of them twice, or an end marker
    before its begin marker -- is unusable, and guessing would leave a stale block behind.
    """
    begins = existing.count(BEGIN_MARKER)
    ends = existing.count(END_MARKER)
    if begins == 0 and ends == 0:
        return None
    if begins != 1 or ends != 1:
        raise ConfigError(
            f"the target text must contain each scitools-hook marker exactly once; "
            f"found {begins} begin and {ends} end markers",
            hint=_MARKER_HINT,
        )
    start = existing.index(BEGIN_MARKER) + len(BEGIN_MARKER)
    stop = existing.index(END_MARKER)
    if stop < start:
        raise ConfigError(
            "the scitools-hook end marker comes before the begin marker in the target text",
            hint=_MARKER_HINT,
        )
    return start, stop


def _newline_style(existing: str) -> str:
    """The line ending the text mostly uses; an empty or LF-majority text means ``\\n``."""
    crlf = existing.count("\r\n")
    return "\r\n" if crlf > existing.count("\n") - crlf else "\n"


def _as_body(snippet: str, newline: str) -> str:
    """The snippet with its own line endings normalised to the file's and no blank edges."""
    normalised = snippet.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    return normalised if newline == "\n" else normalised.replace("\n", newline)


def _append_block(existing: str, inner: str, newline: str) -> str:
    """Append a fresh block, terminating an unterminated last line and leaving a blank one."""
    block = f"{BEGIN_MARKER}{inner}{END_MARKER}{newline}"
    if not existing:
        return block
    terminator = "" if existing.endswith("\n") else newline
    return f"{existing}{terminator}{newline}{block}"
