"""Lint, type check and packaging marker, hosted inside the suite so one command runs them all.

Task 10.3 asks that the import-direction test, ``ruff``, ``mypy --strict`` and the coverage
threshold all pass in **one** ``uv run`` invocation, documented in the README. Three of those
four are already a pytest run; the two external tools are put here rather than into a shell
one-liner so that the single invocation is a real command a person can type and a CI job can
run without a shell:

    uv run pytest --cov=src/scitools_hook --cov-branch --cov-report=term-missing \\
        --cov-fail-under=85

Coverage is the one gate that cannot be a test -- the total is not known until the session
ends -- so it stays a flag on that command. :func:`test_the_readme_documents_the_gate_command`
is what keeps the flag and the README from drifting apart, because a threshold nobody passes
on the command line is a threshold that silently does not apply.

Both tools are invoked as ``sys.executable -m <tool>``, which is the interpreter pytest is
already running under and therefore the versions pinned in the ``dev`` dependency group --
never whatever ``ruff`` happens to be first on ``PATH``.

None of this needs an Understand licence: no gate here starts ``und``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

GATE_COMMAND = (
    "uv run pytest --cov=src/scitools_hook --cov-branch "
    "--cov-report=term-missing --cov-fail-under=85"
)
"""The one invocation the task asks for, and the string the README must contain verbatim."""

TOOL_TIMEOUT_S = 600.0


def run_tool(*args: str) -> subprocess.CompletedProcess[str]:
    """Run one dev tool from the repository root under the interpreter running the tests."""
    return subprocess.run(
        [sys.executable, "-m", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=TOOL_TIMEOUT_S,
        check=False,
    )


def test_ruff_reports_no_lint_findings() -> None:
    proc = run_tool("ruff", "check", ".")
    assert proc.returncode == 0, f"\n{proc.stdout}{proc.stderr}"


def test_ruff_reports_nothing_to_reformat() -> None:
    """Steering names ruff as "lint + format", so the formatter is half of "ruff clean"."""
    proc = run_tool("ruff", "format", "--check", ".")
    assert proc.returncode == 0, f"\n{proc.stdout}{proc.stderr}"


def test_mypy_strict_reports_no_errors() -> None:
    """``mypy`` with no arguments reads ``[tool.mypy]``: ``strict = true``, ``files = ["src"]``.

    Invoking it bare rather than as ``mypy src`` is deliberate. It is the configured gate, so
    what passes here is exactly what a contributor and a CI job get; passing an explicit path
    would silently run a *different* check from the one the project declares.
    """
    proc = run_tool("mypy")
    assert proc.returncode == 0, f"\n{proc.stdout}{proc.stderr}"


def test_the_readme_documents_the_gate_command() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert GATE_COMMAND in readme, (
        f"README.md does not contain the gate command verbatim:\n  {GATE_COMMAND}"
    )


def test_the_readme_check_would_notice_a_command_that_is_absent() -> None:
    """The test above asserts a presence in a file nobody re-reads; prove the read happens."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "uv run pytest --cov=src/scitools_hook --coverage-fail-under=85" not in readme


def test_the_ci_workflow_runs_the_gate_command() -> None:
    """CI, the README and this module must name one command, not three that resemble it.

    The workflow is parsed rather than grepped: a command inside a YAML comment -- the
    commented interpreter-matrix sketch that task 11.4 will enable is one -- must not count
    as a step that runs.
    """
    yaml = pytest.importorskip("yaml")
    workflow = REPO_ROOT / ".github" / "workflows" / "gate.yml"
    assert workflow.is_file(), f"{workflow} is missing"
    doc = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    commands = [
        step["run"] for job in doc["jobs"].values() for step in job["steps"] if "run" in step
    ]
    assert any(GATE_COMMAND == command.strip() for command in commands), (
        f"no CI step runs the gate command verbatim; steps are {commands}"
    )


def test_the_package_ships_a_py_typed_marker() -> None:
    """Without it, ``mypy`` treats every import of this package by a consumer as untyped."""
    assert (REPO_ROOT / "src" / "scitools_hook" / "py.typed").is_file()


@pytest.mark.parametrize("tool", ["ruff", "mypy"])
def test_the_gate_tools_are_installed_in_this_environment(tool: str) -> None:
    """A gate that is silently absent is worse than one that fails: pin that both are here."""
    proc = run_tool(tool, "--version")
    assert proc.returncode == 0, f"{tool} is not importable by {sys.executable}"
    assert proc.stdout.strip()
