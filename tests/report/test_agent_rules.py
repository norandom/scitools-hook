"""The agent-rules snippet and its insertion into a target file (req 10.1, 10.2, 10.3).

The tests are organised as the requirement is. :func:`render_rules` must state every
effective limit, every structural rule that is actually in force, the ratchet in plain
words, the two commands, how to read the JSON and what to do about a blocked commit (10.1),
and it must do so byte-identically for the same effective configuration (10.2) -- which is
checked here both inside one process and across three interpreters started with different
``PYTHONHASHSEED`` values, because a dict iteration order that happens to be stable in this
process is exactly the bug 10.2 is about. :func:`insert_between_markers` must leave every
byte outside the markers alone (10.3), which is checked with content before *and* after the
block, a file with no trailing newline, and a CRLF file.

The fixture configuration is deliberately hostile to a renderer that leans on its input
order: the thresholds arrive scope-scrambled and, inside a scope, in reverse alphabetical
order, the layer and coupling rules arrive unsorted, and the baseline-sourced threshold's
effective limit differs from the limit its spec was configured with, so a renderer that
prints ``spec.limit`` instead of the effective one is caught rather than tolerated.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from typing import Final

import pytest

from scitools_hook.config.defaults import default_settings
from scitools_hook.config.metric_names import SCOPES, Scope
from scitools_hook.config.models import (
    CodeCheckSettings,
    CouplingRule,
    LayerRule,
    Limit,
    RatchetSettings,
    Settings,
    Severity,
    StructureRules,
    ThresholdSpec,
)
from scitools_hook.errors import ConfigError
from scitools_hook.models.findings import EffectiveThreshold
from scitools_hook.report.agent_rules import (
    BEGIN_MARKER,
    END_MARKER,
    insert_between_markers,
    render_rules,
)

CORE: Final = "Directory Structure/src/core"
UI: Final = "Directory Structure/src/ui"


def threshold(
    scope: Scope,
    metric: str,
    limit: Limit,
    *,
    severity: Severity = "error",
    ratchet: bool = True,
) -> ThresholdSpec:
    """One configured threshold, in the shape the loader produces."""
    return ThresholdSpec(
        scope=scope, metric=metric, limit=limit, severity=severity, ratchet=ratchet
    )


def effective(spec: ThresholdSpec, limit: Limit | None = None) -> EffectiveThreshold:
    """``spec`` with the limit that actually applies; a widened one came from the baseline."""
    applied = spec.limit if limit is None else limit
    return EffectiveThreshold(
        spec=spec,
        metric=spec.ref,
        limit=applied,
        source="config" if limit is None else "baseline",
    )


BASELINE_SPEC: Final = threshold("routine", "CountLineCode", Limit(max=60))
"""Configured at 60; the baseline narrowed it to 47, so 47 is what the agent must be told."""

SCRAMBLED: Final[tuple[EffectiveThreshold, ...]] = (
    effective(threshold("project", "AVG:CyclomaticStrict", Limit(max=3))),
    effective(threshold("routine", "MaxNesting", Limit(max=3))),
    effective(threshold("file", "RatioCommentToCode", Limit(min=0.1), severity="warning")),
    effective(threshold("class", "CountDeclMethod", Limit(max=20), ratchet=False)),
    effective(BASELINE_SPEC, Limit(max=47)),
    effective(threshold("routine", "CyclomaticStrict", Limit(max=10))),
    effective(threshold("file", "CountLineCode", Limit(max=200, min=1))),
    effective(threshold("project", "MaxCyclomaticStrict", Limit(max=15))),
)
"""Scopes out of order, metrics reverse-alphabetical inside ``routine`` and ``file``."""

FULL_STRUCTURE: Final = StructureRules(
    architecture="Custom Layers",
    file_cycles="error",
    arch_cycles="warning",
    max_new_dependencies_per_file=5,
    new_dependencies_severity="error",
    fan={"class_fan_in": Limit(max=30), "file_fan_out": Limit(max=20)},
    fan_severity="warning",
    layers=[
        LayerRule(name="ui-is-a-leaf", node=UI, may_depend_on=[CORE], severity="error"),
        LayerRule(name="core-is-closed", node=CORE, may_depend_on=[], severity="warning"),
    ],
    coupling=[
        CouplingRule(from_node=UI, to_node=CORE, max_refs=40, severity="warning"),
        CouplingRule(from_node=CORE, to_node=UI, max_refs=0, severity="error"),
    ],
)

BARE_STRUCTURE: Final = StructureRules(
    file_cycles="error",
    arch_cycles="error",
    max_new_dependencies_per_file=None,
    fan={},
    layers=[],
    coupling=[],
)
"""Nothing configured beyond the two cycle rules, which cannot be switched off."""


def settings_with(
    structure: StructureRules,
    *,
    strict: bool = False,
    codecheck_config: str | None = None,
) -> Settings:
    """A ``Settings`` built from the parts this module renders and nothing else."""
    return Settings(
        structure=structure,
        ratchet=RatchetSettings(strict=strict),
        codecheck=CodeCheckSettings(config=codecheck_config),
    )


@pytest.fixture
def snippet() -> str:
    """The fixture configuration rendered once."""
    return render_rules(settings_with(FULL_STRUCTURE), list(SCRAMBLED))


# --- requirement 10.1: what the snippet must say --------------------------------


def section(text: str, heading: str) -> str:
    """The body of one second-level section, up to the next one."""
    return text.split(f"## {heading}", 1)[1].split("\n## ", 1)[0]


def test_every_effective_limit_is_stated(snippet: str) -> None:
    """Each configured metric appears with the limit that applies to it."""
    for name in (
        "CyclomaticStrict",
        "MaxNesting",
        "CountLineCode",
        "CountDeclMethod",
        "RatioCommentToCode",
        "AVG:CyclomaticStrict",
        "MaxCyclomaticStrict",
    ):
        assert f"`{name}`" in snippet, name
    assert "at most 10" in snippet
    assert "at least 0.1" in snippet
    assert "between 1 and 200" in snippet


def test_limits_are_grouped_by_scope_in_a_fixed_order(snippet: str) -> None:
    """Scope groups follow the canonical scope order, not the order of the input list.

    The fixture list starts with a ``project`` threshold and ends with one, so a renderer
    that groups by first appearance produces a different document from this one.
    """
    headings = [line for line in snippet.splitlines() if line.startswith("### ")]
    assert len(headings) == 4
    assert "Routine" in headings[0]
    assert "Class" in headings[1]
    assert "File" in headings[2]
    assert "Project" in headings[3]


def test_metrics_are_sorted_inside_their_scope(snippet: str) -> None:
    """Reverse-alphabetical input comes out alphabetical, so nothing rides on input order."""
    assert snippet.index("`CountLineCode`") < snippet.index("`CyclomaticStrict`")
    assert snippet.index("`CyclomaticStrict`") < snippet.index("`MaxNesting`")
    assert snippet.index("`CountLineCode`: between 1 and 200") < snippet.index(
        "`RatioCommentToCode`"
    )
    assert snippet.index("`AVG:CyclomaticStrict`") < snippet.index("`MaxCyclomaticStrict`")


def test_a_baseline_limit_is_attributed_and_shows_the_applied_number(snippet: str) -> None:
    """The widened limit is the one printed, and it says where it came from (task 4.5)."""
    line = next(
        text for text in snippet.splitlines() if text.startswith("- `CountLineCode`: at most 47")
    )
    assert "baseline" in line
    assert BASELINE_SPEC.limit.max == 60
    assert "at most 60" not in snippet


def test_a_configured_limit_is_not_attributed_to_the_baseline(snippet: str) -> None:
    """Only the baseline-sourced threshold mentions the baseline."""
    limit_lines = [
        text for text in snippet.splitlines() if text.startswith("- `") and ": at most " in text
    ]
    attributed = [text for text in limit_lines if "baseline" in text]
    assert len(attributed) == 1
    assert attributed[0].startswith("- `CountLineCode`: at most 47")


def test_severity_of_every_limit_is_stated(snippet: str) -> None:
    """An agent has to know which limits block; the warning-severity one says so."""
    assert "`RatioCommentToCode`: at least 0.1 (warning)" in snippet
    assert "`CyclomaticStrict`: at most 10 (error)" in snippet


def test_population_thresholds_are_marked_as_populations(snippet: str) -> None:
    """A stats-prefixed limit is not a per-entity limit and must not read like one."""
    assert "- `AVG:CyclomaticStrict`: at most 3 (error) -- `AVG` over the whole project" in snippet
    assert "- `MaxCyclomaticStrict`: at most 15 (error) -- measured over the whole project" in (
        snippet
    )
    assert "- `MaxNesting`: at most 3 (error)\n" in snippet


def test_configured_structural_rules_are_stated(snippet: str) -> None:
    """Every structural rule in force is named, with its nodes, numbers and severity."""
    assert "`Custom Layers`" in snippet
    assert "cycles between files are reported (error)" in snippet
    assert "architecture nodes of `Custom Layers` are reported (warning)" in snippet
    assert "Fan-out of a file (the files it depends on): at most 20 (warning)" in snippet
    assert "Fan-in of a class (the classes that depend on it): at most 30 (warning)" in snippet
    assert "at most 5 new dependencies in a single change (error)" in snippet
    assert f"Layer rule `ui-is-a-leaf`: `{UI}` may depend only on `{CORE}` (error)" in snippet
    assert f"Layer rule `core-is-closed`: `{CORE}` may not gain a dependency" in snippet
    assert f"Coupling `{CORE}` -> `{UI}`: at most 0 references (error)" in snippet
    assert f"Coupling `{UI}` -> `{CORE}`: at most 40 references (warning)" in snippet


def test_structural_rules_are_sorted(snippet: str) -> None:
    """Unsorted layer, coupling and fan tables come out in a fixed order.

    The fixture builds all three the wrong way round, so a renderer that walks its input
    renders a different document from this one -- including the fan table, whose canonical
    order is :data:`~scitools_hook.config.models.FAN_KEYS` and not insertion order.
    """
    assert snippet.index("core-is-closed") < snippet.index("ui-is-a-leaf")
    assert snippet.index("at most 0 references") < snippet.index("at most 40 references")
    assert snippet.index("Fan-out of a file") < snippet.index("Fan-in of a class")


def test_unconfigured_structural_rules_are_absent() -> None:
    """A rule nobody configured must not be described as being in force."""
    bare = render_rules(settings_with(BARE_STRUCTURE), list(SCRAMBLED))
    assert "new dependencies" not in bare
    assert "fan" not in bare.lower()
    assert "Layer rule" not in bare
    assert "Coupling" not in bare
    assert "CodeCheck" not in bare
    assert "cycles between files are reported (error)" in bare


def test_codecheck_is_mentioned_only_when_configured() -> None:
    """The CodeCheck configuration is a rule in force only when one is named."""
    with_check = render_rules(
        settings_with(BARE_STRUCTURE, codecheck_config="house-style"),
        list(SCRAMBLED),
    )
    assert "house-style" in with_check
    assert "CodeCheck" in with_check


def test_ratchet_is_explained_in_plain_words(snippet: str) -> None:
    """Requirement 4.4 in a sentence an agent can act on."""
    lowered = snippet.lower()
    assert "worse" in lowered
    assert "inside its limit" in lowered


def test_metrics_excluded_from_the_ratchet_are_listed(snippet: str) -> None:
    """``ratchet = false`` on a spec is a rule the agent is entitled to know."""
    assert "not ratcheted" in snippet
    assert "`class.CountDeclMethod`" in snippet


def test_a_fully_ratcheted_configuration_lists_no_exclusions() -> None:
    """With nothing excluded the exclusion sentence is absent, not empty."""
    ratcheted = [item for item in SCRAMBLED if item.spec.ratchet]
    text = render_rules(settings_with(FULL_STRUCTURE), ratcheted)
    assert "CountDeclMethod" not in text
    assert "not ratcheted" not in text


def test_strict_mode_changes_the_text() -> None:
    """Strict mode decides whether a pre-existing violation blocks (req 4.7)."""
    lenient = render_rules(settings_with(FULL_STRUCTURE), list(SCRAMBLED))
    strict = render_rules(settings_with(FULL_STRUCTURE, strict=True), list(SCRAMBLED))
    assert lenient != strict
    assert "Strict mode is off" in lenient
    assert "Strict mode is on" in strict
    assert "does not block, as long as you do not make it worse" in lenient
    assert "does not block, as long as you do not make it worse" not in strict
    assert "your change also blocks" in strict
    assert "your change also blocks" not in lenient


def test_both_commands_are_named(snippet: str) -> None:
    """10.5's pre-staging check and the command run before committing (10.1).

    Both are looked for inside the section that tells the agent what to run, not anywhere
    in the document: the blocked-commit workflow names ``--staged`` too, so a snippet that
    dropped the command from its own section would otherwise still look complete.
    """
    running = section(snippet, "Check your own work")
    assert "scitools-hook check --worktree" in running
    assert "scitools-hook check --staged" in running
    assert "```sh" in running


def test_json_reading_is_explained(snippet: str) -> None:
    """The agent is told how to get machine output and which fields carry the answer."""
    reading = section(snippet, "Read the JSON output")
    assert "scitools-hook check --worktree --format json" in reading
    assert "`findings`" in reading
    assert "`hint`" in reading
    assert "`blocking_count`" in reading


def test_blocked_commit_workflow_is_explained(snippet: str) -> None:
    """Requirement 10.4's workflow, including what not to do."""
    lowered = snippet.lower()
    assert "blocking" in lowered
    assert "do not raise a limit" in lowered
    assert "re-run" in lowered


def test_snippet_carries_no_trailing_newline(snippet: str) -> None:
    """Like the other renderers: the caller decides how the text is terminated."""
    assert snippet == snippet.rstrip("\n")
    assert snippet.startswith("## ")


def test_every_scope_can_be_rendered() -> None:
    """A threshold on any scope the grammar allows has a heading and a population note.

    ``arch`` never appears in the shipped defaults, so nothing else would notice that its
    heading was missing until an operator configured one and the renderer raised.
    """
    for scope in SCOPES:
        item = effective(threshold(scope, "CountLineCode", Limit(max=1)))
        text = render_rules(settings_with(BARE_STRUCTURE), [item])
        headings = [line for line in text.splitlines() if line.startswith("### ")]
        assert len(headings) == 1
        assert "- `CountLineCode`: at most 1 (error)" in text


def test_a_configuration_without_thresholds_says_so() -> None:
    """An empty limits section must state the fact rather than render as a blank heading."""
    text = render_rules(settings_with(BARE_STRUCTURE), [])
    assert "no metric limits" in text
    assert "scitools-hook check --staged" in text


# --- requirement 10.2: determinism ----------------------------------------------


def test_rendering_twice_yields_identical_text(snippet: str) -> None:
    """The done criterion, in one process."""
    assert render_rules(settings_with(FULL_STRUCTURE), list(SCRAMBLED)) == snippet


def test_rendering_the_defaults_twice_yields_identical_text() -> None:
    """The shipped configuration, whose thresholds come out of dict literals."""
    settings = default_settings()
    thresholds = [effective(spec) for spec in settings.thresholds]
    assert render_rules(settings, thresholds) == render_rules(settings, thresholds)


def test_input_order_does_not_change_the_output() -> None:
    """A reordered input list is the same effective configuration, so it renders the same."""
    reversed_input = list(reversed(SCRAMBLED))
    assert render_rules(settings_with(FULL_STRUCTURE), reversed_input) == render_rules(
        settings_with(FULL_STRUCTURE), list(SCRAMBLED)
    )


_DIGEST_SCRIPT: Final = """\
import hashlib
from scitools_hook.config.defaults import default_settings
from scitools_hook.models.findings import EffectiveThreshold
from scitools_hook.report.agent_rules import render_rules

settings = default_settings()
effective = [
    EffectiveThreshold(spec=spec, metric=spec.ref, limit=spec.limit)
    for spec in settings.thresholds
]
print(hashlib.sha256(render_rules(settings, effective).encode()).hexdigest())
"""


def _digest_in_subprocess(seed: str) -> str:
    """Render the default configuration in a fresh interpreter and hash the result."""
    completed = subprocess.run(
        [sys.executable, "-c", _DIGEST_SCRIPT],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    return completed.stdout.strip()


def test_output_is_identical_across_processes() -> None:
    """Requirement 10.2 across interpreters with different string hashing (task guidance)."""
    digests = {_digest_in_subprocess(seed) for seed in ("0", "1", "random")}
    assert len(digests) == 1
    settings = default_settings()
    thresholds = [effective(spec) for spec in settings.thresholds]
    local = hashlib.sha256(render_rules(settings, thresholds).encode()).hexdigest()
    assert digests == {local}


# --- requirement 10.3: marker insertion -----------------------------------------

PREFIX: Final = "# Agent instructions\n\nAlways read the repository README first.\n"
SUFFIX: Final = "\n## House style\n\nTwo-space indentation."


def block_body(text: str) -> str:
    """What sits between the markers, without the newlines the inserter added."""
    after_begin = text.split(BEGIN_MARKER, 1)[1]
    return after_begin.split(END_MARKER, 1)[0].strip("\r\n")


def test_insert_appends_a_marked_block_when_the_file_has_none() -> None:
    """A file that has never been written to keeps every byte it had."""
    result = insert_between_markers(PREFIX, "RULES")
    assert result.startswith(PREFIX)
    assert BEGIN_MARKER in result and END_MARKER in result
    assert block_body(result) == "RULES"
    assert result.endswith(f"{END_MARKER}\n")


def test_insert_separates_the_block_from_existing_text() -> None:
    """The block never continues the last line, and is always one blank line below it."""
    block = f"{BEGIN_MARKER}\nRULES\n{END_MARKER}\n"
    unterminated = insert_between_markers("last line without a newline", "RULES")
    assert unterminated == f"last line without a newline\n\n{block}"
    terminated = insert_between_markers("last line with a newline\n", "RULES")
    assert terminated == f"last line with a newline\n\n{block}"


def test_insert_into_an_empty_file_writes_only_the_block() -> None:
    """No leading blank lines when there was nothing to separate from."""
    result = insert_between_markers("", "RULES")
    assert result == f"{BEGIN_MARKER}\nRULES\n{END_MARKER}\n"


def test_insert_replaces_the_previous_block_and_keeps_the_rest() -> None:
    """The done criterion of 10.3: only the managed region changes."""
    existing = f"{PREFIX}{BEGIN_MARKER}\nOLD RULES\n{END_MARKER}{SUFFIX}"
    result = insert_between_markers(existing, "NEW RULES")
    assert "OLD RULES" not in result
    assert block_body(result) == "NEW RULES"
    assert result.startswith(PREFIX)
    assert result.endswith(SUFFIX)


def test_insert_twice_yields_one_block_and_an_identical_file() -> None:
    """The done criterion, over a file that had no block: inserting twice is inserting once."""
    once = insert_between_markers(f"{PREFIX}{SUFFIX}", "RULES")
    twice = insert_between_markers(once, "RULES")
    assert twice == once
    assert twice.count(BEGIN_MARKER) == 1
    assert twice.count(END_MARKER) == 1
    assert block_body(twice) == "RULES"
    assert twice.startswith(f"{PREFIX}{SUFFIX}")


def test_insert_twice_keeps_the_text_around_an_existing_block_byte_identical() -> None:
    """The done criterion over a file that already has a block: nothing outside it moves."""
    existing = f"{PREFIX}{BEGIN_MARKER}\nOLD RULES\n{END_MARKER}{SUFFIX}"
    once = insert_between_markers(existing, "RULES")
    twice = insert_between_markers(once, "RULES")
    assert twice == once
    assert twice.count(BEGIN_MARKER) == 1
    assert block_body(twice) == "RULES"
    assert twice.startswith(PREFIX)
    assert twice.endswith(SUFFIX)
    assert len(twice) == len(existing) - len("OLD RULES") + len("RULES")


def test_insert_twice_with_a_changed_snippet_updates_in_place() -> None:
    """Regeneration after a configuration change replaces, never appends (req 10.2)."""
    once = insert_between_markers(PREFIX, "OLD")
    twice = insert_between_markers(once, "NEW")
    assert twice.count(BEGIN_MARKER) == 1
    assert block_body(twice) == "NEW"
    assert "OLD" not in twice


def test_insert_preserves_a_missing_trailing_newline_after_the_end_marker() -> None:
    """The bytes after the end marker are the operator's, down to the last one."""
    existing = f"{BEGIN_MARKER}\nOLD\n{END_MARKER}\ntail without newline"
    result = insert_between_markers(existing, "NEW")
    assert result.endswith("\ntail without newline")
    assert not result.endswith("\n")


def test_insert_preserves_crlf_line_endings() -> None:
    """A CRLF file stays a CRLF file, including the lines the inserter writes."""
    existing = f"# Title\r\n\r\n{BEGIN_MARKER}\r\nOLD\r\n{END_MARKER}\r\ntail\r\n"
    result = insert_between_markers(existing, "line one\nline two")
    assert "\n" not in result.replace("\r\n", "")
    assert f"{BEGIN_MARKER}\r\nline one\r\nline two\r\n{END_MARKER}" in result
    assert result.endswith("\r\ntail\r\n")
    assert insert_between_markers(result, "line one\nline two") == result


def test_insert_appends_with_crlf_into_a_crlf_file_without_markers() -> None:
    """A file with no block yet still gets CRLF-terminated lines."""
    result = insert_between_markers("# Title\r\nbody\r\n", "RULES")
    assert result.endswith(f"{BEGIN_MARKER}\r\nRULES\r\n{END_MARKER}\r\n")
    assert "\n" not in result.replace("\r\n", "")


def test_insert_keeps_lf_for_a_mostly_lf_file() -> None:
    """One stray CRLF does not turn an LF file into a CRLF file."""
    existing = "alpha\nbeta\r\ngamma\ndelta\n"
    result = insert_between_markers(existing, "RULES")
    assert result.startswith(existing)
    assert result.endswith(f"{BEGIN_MARKER}\nRULES\n{END_MARKER}\n")


def test_begin_marker_without_an_end_marker_is_refused() -> None:
    """Half a block is not something to guess about."""
    with pytest.raises(ConfigError) as caught:
        insert_between_markers(f"{PREFIX}{BEGIN_MARKER}\nOLD\n", "RULES")
    assert BEGIN_MARKER in str(caught.value) or "marker" in str(caught.value)


def test_end_marker_without_a_begin_marker_is_refused() -> None:
    """The mirror case."""
    with pytest.raises(ConfigError):
        insert_between_markers(f"{PREFIX}{END_MARKER}\n", "RULES")


def test_markers_in_the_wrong_order_are_refused() -> None:
    """One of each is not enough; they have to delimit a region."""
    with pytest.raises(ConfigError):
        insert_between_markers(f"{END_MARKER}\nOLD\n{BEGIN_MARKER}\n", "RULES")


def test_duplicated_markers_are_refused() -> None:
    """Two blocks: updating one and leaving the other stale would be worse than failing."""
    doubled = f"{BEGIN_MARKER}\nONE\n{END_MARKER}\n\n{BEGIN_MARKER}\nTWO\n{END_MARKER}\n"
    with pytest.raises(ConfigError):
        insert_between_markers(doubled, "RULES")


def test_nested_begin_marker_is_refused() -> None:
    """A marker inside the block is the same ambiguity."""
    nested = f"{BEGIN_MARKER}\nONE\n{BEGIN_MARKER}\nTWO\n{END_MARKER}\n"
    with pytest.raises(ConfigError):
        insert_between_markers(nested, "RULES")


def test_a_snippet_containing_a_marker_is_refused() -> None:
    """Writing a marker into the block would break every later regeneration."""
    with pytest.raises(ConfigError):
        insert_between_markers(PREFIX, f"RULES\n{END_MARKER}")


def test_the_rendered_snippet_round_trips_through_insertion(snippet: str) -> None:
    """The two halves of task 5.4 together, over the real text."""
    once = insert_between_markers(f"{PREFIX}{BEGIN_MARKER}\n{END_MARKER}{SUFFIX}", snippet)
    twice = insert_between_markers(once, snippet)
    assert twice == once
    assert block_body(once) == snippet
    assert once.startswith(PREFIX)
    assert once.endswith(SUFFIX)
    assert "## Limits" in once
