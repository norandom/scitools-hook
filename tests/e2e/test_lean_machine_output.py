"""The lean-code family's machine outputs through the installed command (lean-code req 7.1,
8.2, 8.4).

The fixture and the run are ``lean_fixture``'s; this module reads what a tool reads: the JSON
document's delta and the example beside the hint, the SARIF run property, and the
``agent-rules`` snippet's lean section with the rule in force.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

from e2e.harness import report
from e2e.lean_fixture import FORWARDER, RULE, a_workspace, checked, enabled
from scitools_hook.exit_codes import ExitCode
from scitools_hook.report.lean_examples import EXAMPLE_SUFFIX, EXAMPLES

EXAMPLE: Final = EXAMPLES[f"{RULE}{EXAMPLE_SUFFIX}"]
"""The shipped worked example for the rule, which the JSON finding must carry whole."""


def findings_of(document: object, rule: str) -> list[dict[str, object]]:
    """The findings a run reported under ``rule``."""
    findings = document["findings"]  # type: ignore[index]
    assert isinstance(findings, list)
    return [dict(one) for one in findings if one["rule"] == rule]


# --- the machine formats (7.1, 8.2) ------------------------------------------------


def test_json_carries_the_delta_and_the_example_beside_the_hint(tmp_path: Path) -> None:
    workspace = a_workspace(tmp_path)
    enabled(workspace)

    done = checked(workspace, "--format", "json")

    assert done.returncode == int(ExitCode.OK), done.stdout + done.stderr
    document = report(done)
    assert document["net_delta"] == {"statements": -1, "lines": -2, "routines": 1}
    found = findings_of(document, RULE)
    assert len(found) == 1, found
    assert found[0]["severity"] == "warning"
    assert found[0]["blocking"] is False
    assert FORWARDER in str(found[0]["message"])
    assert str(found[0]["hint"]).startswith("yagni:")
    assert found[0]["details"]["example"] == EXAMPLE  # type: ignore[index]


def test_sarif_carries_the_delta_as_a_run_property(tmp_path: Path) -> None:
    workspace = a_workspace(tmp_path)
    enabled(workspace)

    done = checked(workspace, "--format", "sarif")

    assert done.returncode == int(ExitCode.OK), done.stdout + done.stderr
    document = json.loads(done.stdout)
    run = document["runs"][0]
    assert run["properties"]["net_delta"] == {"statements": -1, "lines": -2, "routines": 1}
    assert RULE in {result["ruleId"] for result in run["results"]}


# --- the agent-rules snippet (8.4) ------------------------------------------------


def test_the_agent_rules_snippet_carries_the_lean_section_with_the_rule_in_force(
    tmp_path: Path,
) -> None:
    workspace = a_workspace(tmp_path)
    enabled(workspace)

    done = workspace.cli("agent-rules")

    assert done.returncode == int(ExitCode.OK), done.stderr
    assert "## Lean code" in done.stdout
    assert "### The rules in force" in done.stdout
    assert f"- `{RULE}` (warning), `yagni:`" in done.stdout
    assert "net: +12 lloc (+30 lines) over 7 routines" in done.stdout, "how to read the net line"
    assert "`stdlib:` or `native:`" in done.stdout, "the two tags the Gate never emits (8.3)"
