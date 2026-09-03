"""The release pipeline's gate-composition contract, verified with no Dagger engine present.

The engine runs containers; the *decisions* -- which gates run, in what order, what aborts
the run, and what may be published -- are a pure function in
``.dagger/module/src/scitools_hook_ci/main.py``. That function is what this file drives, the
way the sibling facdrone repository drives its own (``tests/contracts/test_dagger_pipeline``).
Nothing here starts a container, so the contract stays checkable in the ordinary suite on a
machine with neither Dagger nor Docker installed.

Two shapes recur in this repository's recorded false greens, and both are guarded here:

* **An assertion that something is ABSENT passes just as happily when the search is broken.**
  Every "X is not in Y" check below is paired with a case that proves the search finds
  something when it is there.
* **A test whose failure mode is unreachable.** :func:`compose_ci` is therefore driven with
  gates that really do fail and a wheel verification that really does fail, and each of
  those asserts that *nothing was built* -- recording the calls, not just the return value.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
from test_quality_gates import GATE_COMMAND as PROJECT_GATE_COMMAND

REPO_ROOT = Path(__file__).resolve().parents[1]
DAGGER_JSON = REPO_ROOT / ".dagger" / "dagger.json"
DAGGER_SRC = REPO_ROOT / ".dagger" / "module" / "src" / "scitools_hook_ci" / "main.py"


def load_pipeline() -> ModuleType:
    """Import the Dagger module's core by path.

    By path and not by name: the module lives outside the package and its directory is not
    on ``sys.path``. The import must therefore succeed with ``dagger`` absent, which is the
    property being tested -- the ``except ImportError`` branch in the module under test is
    the only reason this works in the project venv.
    """
    spec = importlib.util.spec_from_file_location("scitools_hook_ci_pipeline", DAGGER_SRC)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def pipeline() -> ModuleType:
    return load_pipeline()


# --- the module exists and loads without an engine ---------------------------------------


def test_the_dagger_module_is_laid_out_where_the_cli_looks_for_it() -> None:
    assert DAGGER_JSON.is_file()
    assert DAGGER_SRC.is_file()
    assert (DAGGER_SRC.parent / "__init__.py").is_file()


def test_the_core_imports_with_no_dagger_sdk_installed(pipeline: ModuleType) -> None:
    """The gate-composition core must be reachable from the project venv.

    ``dagger-io`` is not a dependency of this project and is not in the dev group, so the
    ``@object_type`` cannot exist here. If it ever does, the assertion below is skipped
    rather than silently inverted.
    """
    assert callable(pipeline.compose_ci)
    if importlib.util.find_spec("dagger") is None:
        assert pipeline.ScitoolsHookCi is None
    else:  # pragma: no cover - only when someone installs dagger-io into the project venv
        assert pipeline.ScitoolsHookCi is not None


# --- compose_ci: the failure modes are reachable -----------------------------------------


def test_all_green_builds_the_sdist_and_the_wheel(pipeline: ModuleType) -> None:
    result = pipeline.compose_ci(
        lambda gate: 0,
        lambda artifact: f"built:{artifact}",
        lambda artifact: 0,
    )
    assert result.ok
    assert result.artifacts == ["built:sdist", "built:wheel"]
    assert result.failed_gate is None


def test_the_first_red_gate_aborts_and_nothing_is_built(pipeline: ModuleType) -> None:
    built: list[str] = []
    verified: list[str] = []

    result = pipeline.compose_ci(
        lambda gate: 1,
        lambda artifact: built.append(artifact) or artifact,
        lambda artifact: verified.append(artifact) or 0,
    )
    assert not result.ok
    assert result.failed_gate == pipeline.GATE_ORDER[0]
    assert result.artifacts == []
    assert built == [], "a red gate must not build anything"
    assert verified == []


def test_a_late_red_gate_also_aborts_and_the_earlier_gates_still_ran(
    pipeline: ModuleType,
) -> None:
    """The first gate passing is not licence to build: every gate has to be green."""
    last = pipeline.GATE_ORDER[-1]
    ran: list[str] = []
    built: list[str] = []

    def run_gate(gate: str) -> int:
        ran.append(gate)
        return 1 if gate == last else 0

    result = pipeline.compose_ci(
        run_gate,
        lambda artifact: built.append(artifact) or artifact,
        lambda artifact: 0,
    )
    assert ran == list(pipeline.GATE_ORDER), "a gate was skipped"
    assert not result.ok
    assert result.failed_gate == last
    assert built == []


def test_a_wheel_that_builds_but_cannot_run_is_not_published(pipeline: ModuleType) -> None:
    """Task 9.1's typer floor: the wheel built cleanly and the CLI then failed to import.

    Building is therefore not the end of the pipeline. The artifact list must come back
    empty, not merely flagged, so that a caller which only looks at ``artifacts`` cannot
    publish it anyway.
    """
    built: list[str] = []
    result = pipeline.compose_ci(
        lambda gate: 0,
        lambda artifact: built.append(artifact) or artifact,
        lambda artifact: 1,
    )
    assert built == list(pipeline.ARTIFACTS), (
        "the artifacts must actually be built before verification, or this test would "
        "pass for the wrong reason"
    )
    assert not result.ok
    assert result.artifacts == []
    assert result.failed_gate == f"verify:{pipeline.VERIFIED_ARTIFACTS[0]}"


def test_only_the_artifacts_named_for_verification_are_executed(pipeline: ModuleType) -> None:
    """An sdist is not executable; asserting on the exact call list keeps that honest."""
    verified: list[str] = []
    pipeline.compose_ci(
        lambda gate: 0,
        lambda artifact: artifact,
        lambda artifact: verified.append(artifact) or 0,
    )
    assert verified == list(pipeline.VERIFIED_ARTIFACTS)
    assert "wheel" in verified
    assert "sdist" not in verified


# --- the gate table itself ----------------------------------------------------------------


def test_there_is_at_least_one_gate(pipeline: ModuleType) -> None:
    """An empty order would build artifacts with nothing verified, and pass every test above."""
    assert pipeline.GATE_ORDER
    assert pipeline.ARTIFACTS
    assert pipeline.VERIFIED_ARTIFACTS


def test_every_ordered_gate_has_a_command(pipeline: ModuleType) -> None:
    for name in pipeline.GATE_ORDER:
        assert name in pipeline.GATE_CMDS, f"{name} is ordered but has no command"


def test_the_cheap_gate_is_ordered_first(pipeline: ModuleType) -> None:
    """A lint or type error must abort in seconds, not after the whole test run."""
    assert pipeline.GATE_ORDER.index("static") < pipeline.GATE_ORDER.index("tests")


def test_the_container_runs_the_projects_own_gate_command_verbatim(pipeline: ModuleType) -> None:
    """One command, not three that resemble each other.

    ``test_quality_gates`` already pins that string against the README and gate.yml; this
    binds the container path to the same string, so a Dagger run reproduces a local run.
    """
    assert pipeline.GATE_COMMAND == PROJECT_GATE_COMMAND
    assert pipeline.GATE_CMDS["tests"] == ("bash", "-c", PROJECT_GATE_COMMAND)


@pytest.mark.parametrize(
    "fragment",
    ["ruff check", "ruff format --check", "mypy"],
)
def test_the_static_gate_runs_lint_format_and_types(pipeline: ModuleType, fragment: str) -> None:
    assert fragment in pipeline.STATIC_COMMAND


def test_the_static_gate_command_would_notice_a_missing_tool(pipeline: ModuleType) -> None:
    """Positive case for the three assertions above: the search is a substring search over a
    real string, so a tool that is genuinely absent is genuinely reported."""
    assert "pyright" not in pipeline.STATIC_COMMAND
    assert "ruff" in pipeline.STATIC_COMMAND


def test_the_cli_smoke_test_starts_the_installed_console_script(pipeline: ModuleType) -> None:
    flags = {arg for args in pipeline.CLI_SMOKE_ARGS for arg in args}
    assert "--help" in flags
    assert "--version" in flags


# --- the licensed suite's absence is visible, not silent ----------------------------------


def test_the_licensed_tests_are_not_in_the_container_gate_order(pipeline: ModuleType) -> None:
    """The container path is licence-free by construction; say so where it can be checked."""
    assert "licensed" not in pipeline.GATE_ORDER
    assert "contract" not in pipeline.GATE_CMDS
    # Positive case for those two absences: the same lookups do find the names that are
    # there, so an empty or renamed table cannot pass the two assertions above.
    assert "static" in pipeline.GATE_ORDER
    assert "tests" in pipeline.GATE_CMDS


def test_the_container_enumerates_the_licensed_tests_it_is_not_running(
    pipeline: ModuleType,
) -> None:
    """A gate that silently skips its most important checks is this project's defect class.

    The container therefore prints the licensed tests by name. The command has to select the
    ``contract`` marker and has to be collection-only -- running them without a licence would
    just skip them again, which is the silence being avoided.
    """
    command = " ".join(pipeline.LICENSED_INVENTORY_CMD)
    assert "-m contract" in command
    assert "--collect-only" in command


def test_the_marker_the_inventory_selects_is_the_marker_the_project_declares() -> None:
    """An inventory keyed on a marker nobody uses would print nothing and look green."""
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'markers = ["contract' in pyproject
    marked = list(REPO_ROOT.glob("tests/contract/test_*.py"))
    assert marked, "no licensed contract tests were found to be absent from the container"
    assert any("pytest.mark.contract" in path.read_text(encoding="utf-8") for path in marked)


def test_the_reason_names_where_licensed_tests_run_and_the_hostname_binding(
    pipeline: ModuleType,
) -> None:
    reason = pipeline.LICENSED_GATE_REASON
    assert "SCITOOLS_HOME" in reason
    # The hostname binding is the non-obvious half and the one that will bite whoever wires up
    # a self-hosted runner: measured, relocating HOME keeps the licence valid while changing
    # the hostname invalidates it. A reason that omits it sends them looking at the network.
    assert "HOSTNAME" in reason.upper()


# --- the release workflow -----------------------------------------------------------------

GATE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "gate.yml"
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"


def load_workflow(path: Path) -> dict[str, object]:
    """Parse a workflow. Parsed, not grepped: a command inside a YAML comment does not run.

    ``on:`` is YAML 1.1's boolean ``true``, so the trigger block comes back under the key
    ``True`` rather than the string. Both are looked at, because a loader that stops doing
    that would otherwise turn every trigger assertion below into a silent pass.
    """
    yaml = pytest.importorskip("yaml")
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def triggers(document: dict[str, object]) -> dict[str, object]:
    block = document.get(True, document.get("on"))
    assert isinstance(block, dict), f"no trigger block found; keys are {list(document)}"
    return block


def run_steps(document: dict[str, object], job: str) -> list[str]:
    jobs = document["jobs"]
    assert isinstance(jobs, dict)
    steps = jobs[job]["steps"]
    return [step["run"] for step in steps if "run" in step]


def test_the_release_workflow_releases_only_on_a_tag_and_only_to_github() -> None:
    """Distribution is a GitHub release. This tool is not on PyPI and will not be.

    That is not a preference to restate: the pre-commit shim's fallback resolves
    `uvx --from git+...` against exactly these artifacts, and a bare `uvx scitools-hook`
    blocked every commit on a real repository because the name resolves to nothing.
    """
    document = load_workflow(RELEASE_WORKFLOW)
    assert "tags" in triggers(document)["push"]
    release = document["jobs"]["release"]
    assert "refs/tags/v" in release["if"], "the release must be gated on a tag"
    assert release["needs"] == "build", "the release must not run before the wheel is verified"
    assert "publish" not in document["jobs"], "a PyPI publish job must not come back"
    commands = "\n".join(run_steps(document, "release"))
    assert "gh release create" in commands
    assert "pypi" not in commands.lower(), "nothing here may upload to PyPI"


def test_the_release_workflow_calls_the_gate_rather_than_repeating_its_command() -> None:
    """One gate command in the repository, not two that drift.

    A tag push does not match gate.yml's branch filter, so a release that did not call it
    would build from a tree nothing had gated.
    """
    release = load_workflow(RELEASE_WORKFLOW)
    assert release["jobs"]["gate"]["uses"] == "./.github/workflows/gate.yml"
    assert release["jobs"]["build"]["needs"] == "gate"
    assert "workflow_call" in triggers(load_workflow(GATE_WORKFLOW))

    # The command must live in gate.yml and nowhere else. The positive case for that absence
    # is the line below it: the same search does find the command where it belongs.
    assert PROJECT_GATE_COMMAND not in RELEASE_WORKFLOW.read_text(encoding="utf-8")
    assert PROJECT_GATE_COMMAND in GATE_WORKFLOW.read_text(encoding="utf-8")


def test_the_release_workflow_starts_the_wheel_it_built() -> None:
    # The build and the wheel smoke-test run THROUGH the Dagger module, so the workflow no
    # longer spells those commands itself. A workflow that re-spells them is a second
    # definition that drifts from the first, and the drift is only discovered by a release
    # behaving differently from every local run before it. So assert the call, and assert the
    # module still owns the behaviour.
    document = load_workflow(RELEASE_WORKFLOW)
    build = document["jobs"]["build"]
    uses = [step.get("uses", "") for step in build["steps"]]
    assert any("dagger-for-github" in u for u in uses), "the build must go through Dagger"
    args = " ".join(str(step.get("with", {}).get("args", "")) for step in build["steps"])
    assert "ci --source=." in args, "the gate/build/smoke-test call"
    assert "build-dist --source=. export" in args, "the artifact must come from the same module"
    source = DAGGER_SRC.read_text(encoding="utf-8")
    assert "uv build" in source
    assert "--help" in source and "--version" in source
    # In a container of its own: a source tree on sys.path would hide a broken wheel.
    assert "verify_wheel" in source


def test_the_release_workflow_refuses_a_wheel_whose_cli_misreports_its_version() -> None:
    """Measured on this tree: the wheel is packaged 0.1.0a1 and the CLI prints 0.1.0.

    ``src/scitools_hook/__init__.py`` holds its own ``__version__`` literal and pyproject.toml
    holds another; nothing in the tree ties them together, and that string is embedded in
    every SARIF report the tool writes. The check is here so it cannot quietly be dropped.
    """
    # The two versions can no longer disagree: `__version__` is read from the distribution
    # metadata, so pyproject.toml is the only place a version exists. What remains for the
    # workflow is the one comparison the module cannot make -- the git TAG against the
    # packaged version, since the module never sees the ref.
    commands = "\n".join(run_steps(load_workflow(RELEASE_WORKFLOW), "build"))
    assert "GITHUB_REF_NAME" in commands, "the tag must be compared with the packaged version"
    assert 'test "$packaged" = "$tagged"' in commands
    init = (REPO_ROOT / "src" / "scitools_hook" / "__init__.py").read_text(encoding="utf-8")
    assert "importlib.metadata" in init, "the version must come from distribution metadata"
    assert '__version__ = "0.' not in init, "a hard-coded version literal must not come back"


def test_the_release_workflow_names_the_licensed_tests_it_did_not_run() -> None:
    commands = "\n".join(run_steps(load_workflow(RELEASE_WORKFLOW), "build"))
    assert "-m contract --collect-only" in commands


# --- the vendored Dagger SDK must not leak into anything ----------------------------------


def test_the_vendored_dagger_sdk_is_excluded_from_lint_and_from_the_sdist() -> None:
    """`dagger develop` writes ~2 MB of generated SDK under .dagger/module/sdk.

    It is gitignored, and that is not enough twice over, both measured:

    * ruff only honours a .gitignore inside a git checkout. The licensed gate container runs
      over a plain copy of the tree with no .git, and `ruff check .` there reported 83
      findings, every one of them inside the vendored SDK.
    * hatchling put all 52 of its files into the sdist -- a published file -- adding 320 KB.

    Both need an explicit exclude, so both are pinned here.
    """
    tomllib = pytest.importorskip("tomllib")
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    vendored = ".dagger/module/sdk"
    assert vendored in config["tool"]["ruff"]["extend-exclude"]
    assert vendored in config["tool"]["hatch"]["build"]["targets"]["sdist"]["exclude"]
    # Positive case: the same lookups find the entries that were already there, so an empty
    # or renamed table cannot make the two assertions above vacuous.
    assert ".kiro" in config["tool"]["ruff"]["extend-exclude"]
    assert config["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == ["src/scitools_hook"]
