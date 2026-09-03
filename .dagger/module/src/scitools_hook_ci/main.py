"""Dagger build pipeline for scitools-hook, following the sibling facdrone repository's
pattern (.dagger/module/src/facdrone_ci/main.py).

Two layers:

* A pure, dagger-independent gate-composition core (:func:`compose_ci` + :class:`CiResult`):
  gates run in order, the first red gate aborts, no artifact is built unless every gate is
  green, and no artifact is *published* unless it has been executed. Unit-tested in the
  project venv with no Dagger engine present (``tests/test_dagger_pipeline.py``).

* The ``@object_type`` :class:`ScitoolsHookCi` -- defined only when ``dagger`` is importable,
  which is always true inside the engine and never true in the project venv -- whose
  ``@function``s run those gates in a pinned container and build the artifacts.

Why the licensed contract tests are absent from the container path
------------------------------------------------------------------
This tool drives SciTools Understand, and a large minority of the suite opens a real
Understand database -- ``tests/contract/``, ``tests/e2e/test_licensed_workflow.py`` and parts
of ``tests/understand/``: 122 of 3522 collected tests when this was written, a number the
inventory command below prints rather than asserts. They are marked ``contract`` and
``tests/conftest.py`` skips them unless ``SCITOOLS_HOME`` points at a licensed install.
A GitHub-hosted runner has neither the installation nor the licence, so this container path
runs the licence-free gates only.

That is a gate skipping its most important checks, which is the exact defect class this
project exists to prevent -- so the skip is made *visible* rather than silent:
:data:`LICENSED_INVENTORY_CMD` enumerates, by name, every test the container is not running,
and :meth:`ScitoolsHookCi.ci` prints that inventory next to the gate results. A count of
zero there would itself be a finding: it would mean the marker stopped selecting anything.

Where the licensed tests *do* run:

* a developer's machine, with ``SCITOOLS_HOME=/path/to/scitools uv run pytest``;
* a self-hosted runner with Understand installed and ``SCITOOLS_HOME`` exported;
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated

if TYPE_CHECKING:
    from collections.abc import Callable

# The single command the README documents, tests/test_quality_gates.py pins and
# .github/workflows/gate.yml runs. It is reproduced here verbatim so a Dagger run and a
# local run are the same run; tests/test_dagger_pipeline.py asserts the three agree.
GATE_COMMAND = (
    "uv run pytest --cov=src/scitools_hook --cov-branch "
    "--cov-report=term-missing --cov-fail-under=85"
)

# Lint, format and types, ahead of the suite. All three run inside the suite as well:
# tests/test_quality_gates.py invokes `ruff check .` and `ruff format --check .` over the
# whole repository -- so this module's own sources are linted there even though only `src`
# and `tests` are named below -- and bare `mypy`, which reads [tool.mypy] and checks `src`.
# The duplication is the point: a lint or type error aborts here in seconds instead of after
# the ~3 minute test run (measured: 166s for the suite on this tree).
STATIC_COMMAND = (
    "uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy"
)

GATE_CMDS: dict[str, tuple[str, ...]] = {
    "static": ("bash", "-c", STATIC_COMMAND),
    "tests": ("bash", "-c", GATE_COMMAND),
}

# Staged so the cheap gate fails first.
GATE_ORDER: tuple[str, ...] = ("static", "tests")

# `uv build` emits both in one pass; they are named separately because they are two
# published files and only one of them is executable.
ARTIFACTS: tuple[str, ...] = ("sdist", "wheel")

# A wheel that builds is not a wheel that runs: `uvx scitools-hook` is a shipped promise
# (requirement 12.2), and the typer floor found in task 9.1 was a wheel that built cleanly
# and then failed to import. Every artifact named here is installed into a container that
# holds no source tree and is then executed.
VERIFIED_ARTIFACTS: tuple[str, ...] = ("wheel",)

# What the installed console script is asked to do. `--version` is not decoration: it is the
# cheapest end-to-end proof that the entry point resolves, typer imports and the callback
# chain runs.
CLI_SMOKE_ARGS: tuple[tuple[str, ...], ...] = (("--help",), ("--version",))

# Names, not counts: this prints every licensed test the container is NOT running. See the
# module docstring. Collection alone needs no licence, so this always produces the list.
LICENSED_INVENTORY_CMD: tuple[str, ...] = (
    "bash",
    "-c",
    "uv run pytest -m contract --collect-only -q",
)

# The licensed suite is NOT run here, and `licensed_inventory` names every test it skips so
# the omission is visible rather than silent -- a gate that quietly drops its most important
# checks is the defect class this whole project exists to prevent.
LICENSED_GATE_REASON = (
    "the licensed suite needs a SciTools Understand installation and licence that a hosted "
    "runner does not have; it runs on a developer machine or on a self-hosted runner with "
    "SCITOOLS_HOME set. Note the licence is bound to the HOSTNAME (measured: relocating HOME "
    "keeps it valid, changing the hostname invalidates it), so a runner must match the "
    "licensed machine's name"
)


@dataclass(frozen=True)
class CiResult:
    ok: bool
    artifacts: list[str] = field(default_factory=list)
    failed_gate: str | None = None


def compose_ci(
    run_gate: Callable[[str], int],
    build_artifact: Callable[[str], str],
    verify_artifact: Callable[[str], int],
) -> CiResult:
    """Run the gates in order, build artifacts only when all are green, publish only what runs.

    ``run_gate(name)`` and ``verify_artifact(name)`` return process-style exit codes
    (0 == pass); ``build_artifact(name)`` returns an identifier for the thing it built.

    Three properties, each with a test that makes its failure mode reachable:

    * the first non-zero gate aborts and nothing is built;
    * artifacts are built only after every gate is green;
    * an artifact in :data:`VERIFIED_ARTIFACTS` that does not execute is not published --
      the result is red with an empty artifact list, exactly as a red gate is.
    """
    for gate in GATE_ORDER:
        if run_gate(gate) != 0:
            return CiResult(ok=False, artifacts=[], failed_gate=gate)
    artifacts = [build_artifact(name) for name in ARTIFACTS]
    for name in VERIFIED_ARTIFACTS:
        if verify_artifact(name) != 0:
            return CiResult(ok=False, artifacts=[], failed_gate=f"verify:{name}")
    return CiResult(ok=True, artifacts=artifacts, failed_gate=None)


# -- Dagger object (the engine always provides the SDK) -----------------------------------
try:
    import dagger
    from dagger import DefaultPath, Doc, dag, function, object_type

    # Pinned so a container run and the operator's run agree. 3.14 is not a preference:
    # src/scitools_hook/paths.py relies on pathlib behaviour that exists only from 3.14, and
    # its own tests fail on 3.12/3.13 while every other gate passes -- the interpreter floor
    # in pyproject.toml says >=3.12 and .github/workflows/gate.yml carries the matrix job
    # that will settle the disagreement. This container pins what the tree actually runs on.
    _BASE = "python:3.14.7-slim"
    _UV_IMAGE = "ghcr.io/astral-sh/uv:0.12.9"

    Source = Annotated[
        dagger.Directory,
        DefaultPath("/"),
        Doc("Project root (defaults to the repo)"),
    ]

    def _src(source: dagger.Directory, *, keep_git: bool = False) -> dagger.Directory:
        """Drop the working tree's caches and venv.

        A local ``dagger call`` copies the working tree, so a stale .mypy_cache or
        .ruff_cache would let a gate pass here while a clean hosted checkout recomputes it
        and fails. .dagger/module/src is kept: the contract tests read it.

        ``keep_git`` is not a preference either. The CLI exits 6 outside a git working tree,
        and at least one test runs it in the repository root, so the test environment needs
        a real ``.git``. Measured in the licensed gate container, which had none: that test
        failed with "``/work`` is not inside a git working tree". The build path does not
        need it -- nothing here derives a version from git -- so it stays out of the layer
        the wheel is built from.
        """
        result = source.without_directory(".venv")
        if not keep_git:
            result = result.without_directory(".git")
        for cache in (".mypy_cache", ".ruff_cache", ".pytest_cache", "dist"):
            result = result.without_directory(cache)
        return result

    def _uv(container: dagger.Container) -> dagger.Container:
        return container.with_file(
            "/usr/local/bin/uv",
            dag.container().from_(_UV_IMAGE).file("/uv"),
        )

    def _app_env(source: dagger.Directory) -> dagger.Container:
        """The project environment: `uv sync --locked`, the same step gate.yml runs.

        ``--locked`` rather than ``--frozen``: a lockfile that no longer matches
        pyproject.toml is itself a failure. ``typer>=0.27.2`` is a measured floor and a
        stale lock is how a floor stops being enforced.
        """
        return (
            _uv(
                dag.container()
                .from_(_BASE)
                .with_exec(
                    [
                        "bash",
                        "-c",
                        "apt-get update && apt-get install -y --no-install-recommends git"
                        " && rm -rf /var/lib/apt/lists/*"
                        # The python image puts the interpreter in /usr/local/bin, which is
                        # not on the default PATH a process inherits when it is given an
                        # empty environment (confstr _CS_PATH is /bin:/usr/bin). One test
                        # writes a `#!/usr/bin/env python3` stub and runs it with env={};
                        # without these links `env` exits 127. Measured, twice: this shape
                        # failed here and in the licensed container, and nowhere on a host.
                        " && ln -sf /usr/local/bin/python3 /usr/bin/python3"
                        " && ln -sf /usr/local/bin/python /usr/bin/python"
                        # Not root. Root ignores a directory's permission bits, so every test
                        # that builds an unreadable or unsearchable fixture and expects the
                        # Gate to report it passes on a host and fails in a root container.
                        # Measured: `dagger call tests` as root had 22 failures -- 18 of
                        # that shape, 2 from the empty-environment stub above, and 2 the
                        # working tree already had. As this user: 2, the same two the host
                        # run has. That is the property this container is for -- it
                        # reproduces the host result rather than adding failures of its own.
                        " && useradd --create-home --uid 1000 gate",
                    ]
                )
            )
            .with_env_variable("UV_FROZEN", "1")
            .with_env_variable("HOME", "/home/gate")
            .with_directory("/work", _src(source, keep_git=True), owner="gate")
            .with_workdir("/work")
            .with_user("gate")
            .with_exec(["uv", "sync", "--locked"])
        )

    @object_type
    class ScitoolsHookCi:
        """`dagger call <fn>` entrypoints for the scitools-hook build."""

        @function
        async def static(self, source: Source) -> str:
            """Gate 1 (cheap): ruff check, ruff format --check, mypy --strict."""
            return await _app_env(source).with_exec(list(GATE_CMDS["static"])).stdout()

        @function
        async def tests(self, source: Source) -> str:
            """Gate 2: the licence-free suite plus the 85% branch-coverage threshold."""
            return await _app_env(source).with_exec(list(GATE_CMDS["tests"])).stdout()

        @function
        async def licensed_inventory(self, source: Source) -> str:
            """Name every licensed test this container is NOT running (module docstring)."""
            return await _app_env(source).with_exec(list(LICENSED_INVENTORY_CMD)).stdout()

        @function
        def build_dist(self, source: Source) -> dagger.Directory:
            """sdist + wheel under /dist, built from a tree with no venv and no caches."""
            return (
                _uv(dag.container().from_(_BASE))
                .with_directory("/work", _src(source))
                .with_workdir("/work")
                .with_exec(["uv", "build", "--out-dir", "/dist"])
                .directory("/dist")
            )

        @function
        async def verify_wheel(self, source: Source) -> str:
            """Install the built wheel into a container holding no source, and run the CLI.

            The container is ``_BASE`` with the dist directory and nothing else: if the CLI
            starts here, it starts from the wheel. `uvx scitools-hook` is the shipped
            promise this proves.

            What this does NOT check is whether the version the CLI prints is the version it
            was packaged as -- they are two independent strings in this tree. That belongs to
            the step that decides whether to publish, and it lives in
            .github/workflows/release.yml, which fails the release on a mismatch.
            """
            container = (
                dag.container()
                .from_(_BASE)
                .with_directory("/dist", self.build_dist(source))
                .with_exec(["bash", "-c", "pip install --no-cache-dir /dist/*.whl"])
            )
            for args in CLI_SMOKE_ARGS:
                container = container.with_exec(["scitools-hook", *args])
            return await container.stdout()

        @function
        async def ci(self, source: Source) -> str:
            """Compose gates then artifacts: fail fast, and publish nothing that cannot run."""

            async def run_gate(name: str) -> int:
                try:
                    await _app_env(source).with_exec(list(GATE_CMDS[name])).sync()
                except dagger.ExecError:
                    return 1
                return 0

            for gate in GATE_ORDER:
                if await run_gate(gate) != 0:
                    raise RuntimeError(f"CI gate failed: {gate} -- no artifact published")

            await self.build_dist(source).sync()
            try:
                await self.verify_wheel(source)
            except dagger.ExecError as exc:
                raise RuntimeError(
                    "the wheel built but the installed CLI did not run -- no artifact "
                    f"published: {exc}"
                ) from exc

            inventory = await self.licensed_inventory(source)
            return (
                "ci: all licence-free gates green; sdist + wheel built and the installed "
                f"CLI ran.\nNOT RUN HERE ({LICENSED_GATE_REASON}):\n{inventory}"
            )

except ImportError:  # pragma: no cover - project venv (no dagger-io) running pytest
    ScitoolsHookCi = None  # type: ignore[assignment,misc]
