"""The architecture gate: the allowed-import matrix, enforced by parsing every module (12.2).

The design's Boundary Commitments state one layer direction --
``config -> models -> understand | git -> analysis -> report -> runner -> cli`` -- and say
that a violation is a review blocker. Nothing else in this repository enforces it. Every
other gate here (ruff, mypy, the unit suite) is happy with a module that reaches upward or
sideways, so this file is the only mechanical reason the layering survives contact with the
next feature.

**Why an AST parse and not a grep.** ``tests/test_paths.py`` records the failure directly:
its first source-level guard was a regex, and it matched the module's own *docstrings* --
the prose naming the very predicates it was documenting as unused. A docstring is an
``ast.Constant``, a comment does not survive ``ast.parse`` at all, and an ``import`` is an
``ast.Import``/``ast.ImportFrom`` node wherever it sits: at module level, inside
``if TYPE_CHECKING:``, or deferred inside a function. :func:`ast.walk` sees all three, and a
type-only import is still an architectural dependency, so all three count.

**Why the detector has its own tests.** Every assertion below is of the shape "no forbidden
import exists", which passes just as happily when the search is broken. So the same
:func:`check_module` that sweeps ``src/`` is driven, in the ``--- the detector fires ---``
section, over sources that *do* contain each forbidden shape, and each case asserts the
violation it must produce. :func:`test_the_sweep_sees_the_whole_package` closes the other
half: a discovery that found nothing would make the sweep vacuous.

**Recorded exceptions are single module pairs, never whole layers.** A matrix edited to match
the code enforces nothing, so :data:`RECORDED_EXCEPTIONS` is keyed on
``(importing module, imported module)``. Permitting ``cli.db -> understand.database``
leaves every *other* ``cli -> understand`` import refused, and
:func:`test_a_recorded_exception_does_not_licence_its_neighbours` proves that. An entry whose
reason begins ``OPEN VIOLATION`` is an edge some task found, could not fix inside its
boundary, and reported rather than accommodated -- written down so it cannot be lost, not
because it is approved. Task 10.3 recorded one here and an ``OPEN FINDING`` in
:data:`RECORDED_PRIVATE_IMPORTS`; task 11.7 closed both by changing the code, so neither
table holds one now, and :data:`RECORDED_PRIVATE_IMPORTS` is empty outright -- its last
entry, ``cli.config_cmd -> config.loader._threshold_tables``, was closed the same way. The
convention stays because the next one will need somewhere to go that is not the matrix.

**A rule the design states belongs in the matrix, not in the exception table.** The two are
different things and 11.7 found them mixed: ``cli/pipelines.py`` was recorded as five
separate exceptions reading "DRIFT, reported", which said the code disagreed with the plan
while the plan said nothing about the module at all. The design now names it the composition
root and says why (design.md, *Allowed Dependencies*), so it is a named entry in
:data:`MODULE_RULES` -- one module, written out in full -- and the exception table is back to
holding only things nobody has approved. The naming is held to that one module by the
composition-root tests below, which drive the same :func:`check_module` over a *sibling* cli
module making the identical imports and require every one of them to be reported.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
PACKAGE = "scitools_hook"
WORKER_MODULE = "scitools_hook.understand.worker"
WORKER_PATH = SRC / "scitools_hook" / "understand" / "worker.py"

SUBPROCESS_TIMEOUT_S = 60.0


# --- the matrix -------------------------------------------------------------------

LEAF = frozenset({"__init__", "exit_codes", "errors", "paths"})
"""The package leaf: modules that sit below every layer and may be imported by all of them.

``paths`` and ``exit_codes`` are here by decision, not by accident. ``paths`` was moved to
the leaf because the same path-classification question is asked at four sites across
``config``, ``understand`` and ``runner``, and keeping one copy per layer is what let two of
them go on using ``Path.exists()`` after the other two were fixed (tasks.md, 8.2 PLACEMENT).
A leaf module earns its place by importing nothing from the package -- see
:data:`ALLOWED`, where three of the four map to the empty set.
"""

ALLOWED: dict[str, frozenset[str]] = {
    # The leaf tier. Empty means "no intra-package import at all": that is what makes a
    # module safe to import from everywhere without creating a direction to enforce.
    "__init__": frozenset(),
    "exit_codes": frozenset(),
    "paths": frozenset(),
    "errors": frozenset({"exit_codes"}),
    # The layers, bottom to top. Every layer may also import itself; that is implicit.
    "config": LEAF,
    "models": LEAF | {"config"},
    # The two adapters are siblings: neither may import the other, and neither may reach
    # above itself. `understand.database` used to break this by importing `git.shadow`
    # directly; it now depends on `models.ports.ShadowPort`, which is below both (task 11.7).
    "understand": LEAF | {"config", "models"},
    "git": LEAF | {"config", "models"},
    "analysis": LEAF | {"config", "models"},
    "report": LEAF | {"config", "models", "analysis"},
    "runner": LEAF | {"config", "models", "analysis", "report", "understand", "git"},
    # `cli -> report` is here because the File Structure Plan puts `--format` handling in
    # `cli/check.py` with the renderers in `report/`. The design's matrix sentence used to
    # omit it, which made a planned edge look like an unrecorded one; 11.7 amended the
    # sentence to say it. Everything the sentence still omits is either a single recorded
    # exception below or the composition root's named entry in MODULE_RULES.
    "cli": LEAF | {"config", "models", "report", "runner"},
}

COMPOSITION_ROOT = "scitools_hook.cli.pipelines"
"""The one module the design lets build both adapters (design.md, *Allowed Dependencies*).

Somewhere has to know every concrete class at once, or nothing can be wired together. The
design puts that somewhere in ``cli`` rather than in ``runner/context.py``, and the reason is
measured rather than stylistic: requirement 12.5 says a command that needs git stops with
exit 6 outside a working tree, ``build_context`` deliberately answers ``None`` instead of
raising for that case (``doctor`` and ``config`` must run without a repository), and the
locator it then reaches raises exit 3. Measured on this repository with no reachable
Understand: outside a working tree the command exits 6 as written, and exits 3 -- "no
SciTools Understand installation was found" -- with the early ``GitRepo.discover`` deleted.
The refusal therefore has to be raised above ``build_context``, and ``assemble`` also takes
``cli.common.GlobalOptions``, so moving it down would turn the five sideways edges into an
upward one.
"""

MODULE_RULES: dict[str, frozenset[str]] = {
    # worker.py runs under Understand's own interpreter (`<home>/bin/<plat>/upython`), where
    # this package is not on sys.path at all. A single intra-package import would pass every
    # unit test and fail on the first licensed machine, so its allowance is *nothing* -- not
    # even the leaf. `test_the_worker_answers_ping_under_an_isolated_interpreter` below
    # executes it to prove the rule holds at runtime and not only in the parse.
    WORKER_MODULE: frozenset(),
    # The composition root, widened rather than restricted, and the only entry of that kind.
    # `"cli"` is written out because a module named here does not get the implicit same-layer
    # allowance -- that is what stops `worker` importing its own siblings -- so an entry that
    # omitted it would forbid `cli.pipelines -> cli.common` and say something nobody meant.
    COMPOSITION_ROOT: ALLOWED["cli"] | {"cli", "understand", "git"},
}

RECORDED_EXCEPTIONS: dict[tuple[str, str], str] = {
    (
        "scitools_hook.cli.agent_rules",
        "scitools_hook.analysis.baseline",
    ): (
        "The design's File Structure Plan gives `agent-rules` a cli module and a report "
        "module and no runner module, so the command's own orchestration is in cli by plan."
    ),
    (
        "scitools_hook.cli.config_cmd",
        "scitools_hook.git.repo",
    ): "`init`/`config` need the repository root; the plan gives them no runner module.",
    (
        "scitools_hook.cli.db",
        "scitools_hook.git.repo",
    ): "`db path|rebuild|analyze` is a cli module in the plan with no runner counterpart.",
    (
        "scitools_hook.cli.db",
        "scitools_hook.understand.database",
    ): "Same: `db` drives DatabaseManager directly because the plan defines no runner/db.",
    (
        "scitools_hook.cli.hooks",
        "scitools_hook.git.hooks",
    ): "`install-hook`/`uninstall-hook` are cli + git in the plan, with nothing in between.",
    (
        "scitools_hook.cli.hooks",
        "scitools_hook.git.repo",
    ): "Same command: it needs the repository to find the hooks directory.",
}

RECORDED_PRIVATE_IMPORTS: dict[tuple[str, str, str], str] = {}
"""Cross-module imports of a private name that are permitted anyway. **Empty, and that is the
point.** Two entries have stood here: ``understand.database -> codecheck._unusable_name``,
closed by task 11.7 giving the name a public one, and ``cli.config_cmd ->
config.loader._threshold_tables``, closed the same way once the first closure left it citing a
precedent that no longer existed. An entry here is a problem written down, never an approval,
so the table is kept for the next one rather than deleted."""

FORBIDDEN_IN_PURE_LAYERS = frozenset({"os", "subprocess", "shutil", "tempfile", "socket"})
"""Modules that ``analysis`` and ``report`` may not import.

The design's Allowed Dependencies say in one breath that the matrix holds and that
"``analysis`` and ``report`` never touch the filesystem or subprocesses". This list plus the
builtin ``open`` is a **proxy** for that second half, in the same sense that
``tests/test_paths.py``'s pathlib guard is labelled a proxy: it cannot tell a correct use
from an incorrect one, only that none is present. ``pathlib`` is deliberately absent -- a
``Path`` is a value these layers legitimately carry in a model.
"""

PURE_LAYERS = frozenset({"analysis", "report"})


# --- the checker ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Violation:
    """One import that the matrix refuses, named well enough to fix without re-deriving it."""

    module: str
    target: str
    line: int
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"{self.module}:{self.line} imports {self.target} [{self.rule}] {self.detail}"


def module_name(path: Path) -> str:
    """The dotted name of a module file under ``src/``."""
    parts = list(path.relative_to(SRC).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def known_modules() -> frozenset[str]:
    """Every module and package name that exists under ``src/``."""
    return frozenset(module_name(p) for p in SRC.rglob("*.py"))


KNOWN = known_modules()


def layer_of(module: str) -> str:
    """The matrix key for a dotted module name.

    ``scitools_hook.analysis.structure.fan`` is ``analysis``; ``scitools_hook.paths`` is
    ``paths``; the package itself is ``__init__``. A name that answers something not in
    :data:`ALLOWED` is reported as ``unknown-layer`` rather than silently permitted, so a new
    top-level package cannot appear with no rules attached to it.
    """
    parts = module.split(".")
    if len(parts) == 1:
        return "__init__"
    return parts[1]


def _resolve_relative(module: str, is_package: bool, node: ast.ImportFrom) -> str:
    """The absolute name a relative ``from . import x`` refers to.

    Handled because the sweep asserts an absence: a checker blind to relative imports would
    report a clean package while ``from ..runner import check`` sat in ``analysis``.
    """
    base = module.split(".") if is_package else module.split(".")[:-1]
    drop = node.level - 1
    if drop:
        base = base[: len(base) - drop] if drop <= len(base) else []
    tail = [node.module] if node.module else []
    return ".".join(base + tail)


def _normalise(target: str, name: str) -> str:
    """``from pkg.sub import mod`` names the module ``pkg.sub.mod`` when that module exists."""
    candidate = f"{target}.{name}"
    return candidate if candidate in KNOWN else target


def intra_package_imports(
    module: str, source: str, *, is_package: bool = False
) -> list[tuple[str, str | None, int]]:
    """``(imported module, imported name or None, line)`` for every import of this package.

    Both statement forms and both spellings are covered: ``import a.b``, ``import a.b as c``,
    ``from a.b import c``, ``from a.b import c as d`` and every relative form. Imports inside
    ``if TYPE_CHECKING:`` and inside function bodies are included, because a dependency the
    type checker sees is a dependency of the architecture.
    """
    found: list[tuple[str, str | None, int]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == PACKAGE:
                    found.append((alias.name, None, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                target = _resolve_relative(module, is_package, node)
            elif node.module and node.module.split(".")[0] == PACKAGE:
                target = node.module
            else:
                continue
            if target.split(".")[0] != PACKAGE:
                continue
            for alias in node.names:
                found.append((_normalise(target, alias.name), alias.name, node.lineno))
    return found


def check_module(module: str, source: str, *, is_package: bool = False) -> list[Violation]:
    """Every matrix violation in one module's source. Empty means the module is compliant."""
    violations: list[Violation] = []
    source_layer = layer_of(module)
    if source_layer not in ALLOWED:
        return [
            Violation(
                module,
                "-",
                1,
                "unknown-layer",
                f"{source_layer!r} has no entry in ALLOWED; add its rules to the matrix",
            )
        ]
    allowed = MODULE_RULES.get(module, ALLOWED[source_layer])

    for target, name, line in intra_package_imports(module, source, is_package=is_package):
        target_layer = layer_of(target)
        if target_layer not in ALLOWED:
            violations.append(
                Violation(
                    module,
                    target,
                    line,
                    "unknown-layer",
                    f"{target_layer!r} has no entry in ALLOWED; add its rules to the matrix",
                )
            )
            continue
        same_layer = target_layer == source_layer and module not in MODULE_RULES
        if not same_layer and target_layer not in allowed:
            if (module, target) not in RECORDED_EXCEPTIONS:
                violations.append(
                    Violation(
                        module,
                        target,
                        line,
                        "forbidden-layer",
                        f"{source_layer} may import {sorted(allowed)}, not {target_layer}",
                    )
                )
                continue
        if (
            name is not None
            and name.startswith("_")
            and not (name.startswith("__") and name.endswith("__"))
            and target != module
            and (module, target, name) not in RECORDED_PRIVATE_IMPORTS
        ):
            violations.append(
                Violation(
                    module,
                    target,
                    line,
                    "private-name",
                    f"{name!r} is private to {target}; give it a public name or record it",
                )
            )
    return violations


def check_purity(module: str, source: str) -> list[Violation]:
    """Filesystem and subprocess reach in ``analysis`` and ``report`` (a proxy -- see above)."""
    if layer_of(module) not in PURE_LAYERS:
        return []
    violations: list[Violation] = []
    for node in ast.walk(ast.parse(source)):
        roots: list[tuple[str, int]] = []
        if isinstance(node, ast.Import):
            roots = [(a.name.split(".")[0], node.lineno) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            roots = [(node.module.split(".")[0], node.lineno)]
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "open":
                violations.append(
                    Violation(
                        module,
                        "open()",
                        node.lineno,
                        "layer-purity",
                        f"{layer_of(module)} never touches the filesystem",
                    )
                )
        for root, line in roots:
            if root in FORBIDDEN_IN_PURE_LAYERS:
                violations.append(
                    Violation(
                        module,
                        root,
                        line,
                        "layer-purity",
                        f"{layer_of(module)} never touches the filesystem or subprocesses",
                    )
                )
    return violations


def source_modules() -> list[tuple[str, str, bool]]:
    """``(module name, source, is package)`` for every module under ``src/``."""
    return [
        (module_name(path), path.read_text(encoding="utf-8"), path.name == "__init__.py")
        for path in sorted(SRC.rglob("*.py"))
    ]


# --- the sweep --------------------------------------------------------------------


def test_the_sweep_sees_the_whole_package() -> None:
    """Without this, every assertion below passes by finding nothing to check.

    The counts are floors, not exact numbers -- new modules are expected -- but they are high
    enough that a discovery returning one file, or the wrong root, fails here first.
    """
    modules = source_modules()
    assert len(modules) > 60, f"only {len(modules)} modules found under {SRC}"
    layers = {layer_of(name) for name, _, _ in modules}
    assert layers == set(ALLOWED), f"layers on disk {sorted(layers)} != matrix {sorted(ALLOWED)}"
    assert any(name == WORKER_MODULE for name, _, _ in modules)
    total_imports = sum(
        len(intra_package_imports(name, src, is_package=pkg)) for name, src, pkg in modules
    )
    assert total_imports > 200, f"only {total_imports} intra-package imports parsed"


def test_every_module_obeys_the_allowed_import_matrix() -> None:
    found = [
        v for name, src, pkg in source_modules() for v in check_module(name, src, is_package=pkg)
    ]
    assert not found, "\n".join(str(v) for v in found)


def test_analysis_and_report_reach_neither_the_filesystem_nor_a_subprocess() -> None:
    found = [v for name, src, _ in source_modules() for v in check_purity(name, src)]
    assert not found, "\n".join(str(v) for v in found)


def test_the_package_leaf_imports_nothing_from_the_package() -> None:
    """The leaf tier's founding property, asserted directly rather than inferred from ALLOWED.

    ``errors`` is the one exception and it is one edge deep: it needs ``ExitCode`` to attach
    an exit code to each error class.
    """
    by_name = {name: (src, pkg) for name, src, pkg in source_modules()}
    for module in ("scitools_hook", "scitools_hook.exit_codes", "scitools_hook.paths"):
        src, pkg = by_name[module]
        assert intra_package_imports(module, src, is_package=pkg) == [], module
    src, pkg = by_name["scitools_hook.errors"]
    targets = {t for t, _, _ in intra_package_imports("scitools_hook.errors", src, is_package=pkg)}
    assert targets == {"scitools_hook.exit_codes"}


def test_the_worker_imports_nothing_from_the_package_at_all() -> None:
    """Not even the leaf: ``upython`` has no copy of this project on its path."""
    source = WORKER_PATH.read_text(encoding="utf-8")
    assert intra_package_imports(WORKER_MODULE, source) == []
    assert check_module(WORKER_MODULE, source) == []


def test_the_worker_answers_ping_under_an_isolated_interpreter() -> None:
    """The parse above is a claim about the text; this runs the module and reads its answer.

    ``-I`` drops the environment and the script's own directory from ``sys.path`` and ``-S``
    drops ``site-packages``, so nothing this project installed is reachable -- the same
    condition ``upython`` presents. A ``scitools_hook`` import anywhere in the module would
    raise ``ModuleNotFoundError`` before ``ping`` could answer, and the JSON parse below is
    what makes that a failure rather than an unread traceback. ``tests/understand/
    test_worker.py`` carries the sibling of this check; it is repeated here so the
    architecture gate is complete on its own.
    """
    proc = subprocess.run(
        [sys.executable, "-I", "-S", str(WORKER_PATH), "ping"],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=SUBPROCESS_TIMEOUT_S,
        check=False,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert PACKAGE not in proc.stderr
    answer = json.loads(proc.stdout)
    if "error" in answer:
        assert answer["error"]["type"] in {"ApiUnavailable", "NoApiLicense"}
    else:
        assert answer["version"]


# --- the recorded exceptions stay narrow and stay used -----------------------------


def test_every_recorded_exception_is_still_needed() -> None:
    """A stale exception widens the matrix for ever and nothing else would notice."""
    edges = {
        (name, target)
        for name, src, pkg in source_modules()
        for target, _, _ in intra_package_imports(name, src, is_package=pkg)
    }
    unused = sorted(pair for pair in RECORDED_EXCEPTIONS if pair not in edges)
    assert not unused, f"recorded exceptions no longer in the source: {unused}"

    private = {
        (name, target, imported)
        for name, src, pkg in source_modules()
        for target, imported, _ in intra_package_imports(name, src, is_package=pkg)
        if imported is not None
    }
    stale = sorted(entry for entry in RECORDED_PRIVATE_IMPORTS if entry not in private)
    assert not stale, f"recorded private imports no longer in the source: {stale}"


def test_every_recorded_exception_carries_a_reason() -> None:
    for pair, reason in RECORDED_EXCEPTIONS.items():
        assert len(reason) > 40, f"{pair} is recorded without a reason"
    for entry, reason in RECORDED_PRIVATE_IMPORTS.items():
        assert len(reason) > 40, f"{entry} is recorded without a reason"


def test_a_recorded_exception_does_not_licence_its_neighbours() -> None:
    """The point of keying exceptions on module pairs rather than on layers.

    Four different neighbours of the live exceptions: ``cli.db -> understand.database`` is
    recorded, so a *different importer* of the same module, the *same importer* of a different
    understand module, and the same importer of the sibling adapter must each still be
    reported. The fourth is the private-name rule with no exception left to lean on --
    ``config.loader._read_toml`` is private and is refused wherever it is read from.
    """
    assert check_module(
        "scitools_hook.cli.check",
        "from scitools_hook.understand.database import DatabaseManager\n",
    )
    assert check_module(
        "scitools_hook.cli.db",
        "from scitools_hook.understand.snapshot import SnapshotExtractor\n",
    )
    assert check_module(
        "scitools_hook.cli.db",
        "from scitools_hook.git.shadow import ShadowSync\n",
    )
    assert check_module(
        "scitools_hook.report.human",
        "from scitools_hook.config.loader import _read_toml\n",
    )


def test_the_cross_adapter_edge_is_refused_now_that_its_exception_is_gone() -> None:
    """The edge 11.7 removed, asserted as a rule; the sweep is what asserts it of the source.

    ``understand.database`` reached into ``git.shadow`` for :class:`ShadowSync` until the port
    in ``models/ports.py`` took its place. There is no exception for it any more, so the
    general adapter rule now covers it -- and this names the pair that used to be special, in
    both directions, so re-adding the import fails here as well as in the sweep.
    """
    assert check_module(
        "scitools_hook.understand.database",
        "from scitools_hook.git.shadow import ShadowSync\n",
    )
    assert check_module(
        "scitools_hook.git.shadow",
        "from scitools_hook.understand.database import DatabaseManager\n",
    )
    assert (
        check_module(
            "scitools_hook.understand.database",
            "from scitools_hook.models.ports import ShadowPort\n",
        )
        == []
    )


def test_the_list_file_predicate_is_public_and_its_private_siblings_are_not() -> None:
    """The rename 11.7 made, in the terms this gate refuses names by.

    ``codecheck._unusable_name`` had two importers outside its module, both aliasing it to
    ``unusable_list_file_name``; it now carries that name. The two predicates it delegates to
    stay private, so importing either from outside is still reported -- which is what makes
    this a rename rather than a general opening of the module.
    """
    assert (
        check_module(
            "scitools_hook.understand.database",
            "from scitools_hook.understand.codecheck import unusable_list_file_name\n",
        )
        == []
    )
    for private in ("_unusable_shape", "_unusable_characters", "_unusable"):
        assert check_module(
            "scitools_hook.runner.check",
            f"from scitools_hook.understand.codecheck import {private}\n",
        ), private


def test_only_the_named_composition_root_may_assemble_the_adapters() -> None:
    """One module is named; its siblings get nothing, and it is bound by every other rule.

    The adapter imports below are the ones ``cli/pipelines.py`` really makes. Each is driven
    twice -- once as the composition root, once as ``cli.check``, which is a plain command
    module -- so the widening cannot be read as "cli may reach the adapters".
    """
    adapters = [
        "from scitools_hook.git.repo import GitRepo\n",
        "from scitools_hook.git.shadow import ShadowSync\n",
        "from scitools_hook.understand.codecheck import CodeCheckRunner\n",
        "from scitools_hook.understand.database import DatabaseManager\n",
        "from scitools_hook.understand.snapshot import SnapshotExtractor\n",
    ]
    for source in adapters:
        assert check_module(COMPOSITION_ROOT, source) == [], source
        assert check_module("scitools_hook.cli.check", source), source
    # Naming a module in MODULE_RULES drops its implicit same-layer allowance, so the two
    # edges the root shares with every other cli module are asserted rather than assumed.
    assert (
        check_module(COMPOSITION_ROOT, "from scitools_hook.cli.common import GlobalOptions\n") == []
    )
    assert (
        check_module(COMPOSITION_ROOT, "from scitools_hook.runner.context import build_context\n")
        == []
    )
    # Widened for the adapters, not exempted from the rest.
    assert check_module(COMPOSITION_ROOT, "from scitools_hook.config.loader import _private\n")
    assert check_module(COMPOSITION_ROOT, "from scitools_hook.brandnew.thing import Thing\n")


def test_the_composition_root_is_the_module_the_source_tree_actually_holds() -> None:
    """A rule naming a module that is not there would be a rule about nothing."""
    assert COMPOSITION_ROOT in KNOWN
    names = {name for name, _, _ in source_modules()}
    assert COMPOSITION_ROOT in names
    assert set(MODULE_RULES) <= names


# --- the detector fires -----------------------------------------------------------
#
# Everything above asserts an absence. These drive the same check_module over sources that
# do contain each forbidden shape, so a checker that has stopped looking fails here.


@pytest.mark.parametrize(
    ("module", "source", "rule"),
    [
        pytest.param(
            "scitools_hook.analysis.thresholds",
            "from scitools_hook.runner.check import CheckPipeline\n",
            "forbidden-layer",
            id="upward-from-import",
        ),
        pytest.param(
            "scitools_hook.understand.locator",
            "from scitools_hook.git.repo import GitRepo\n",
            "forbidden-layer",
            id="sideways-between-adapters",
        ),
        pytest.param(
            "scitools_hook.understand.database",
            "from scitools_hook.git.shadow import ShadowSync\n",
            "forbidden-layer",
            id="the-cross-adapter-edge-11-7-removed",
        ),
        pytest.param(
            "scitools_hook.git.repo",
            "from scitools_hook.understand.locator import locate\n",
            "forbidden-layer",
            id="sideways-the-other-way",
        ),
        pytest.param(
            "scitools_hook.config.loader",
            "from scitools_hook.models.findings import Finding\n",
            "forbidden-layer",
            id="config-may-not-import-models",
        ),
        pytest.param(
            "scitools_hook.report.human",
            "import scitools_hook.runner.check\n",
            "forbidden-layer",
            id="plain-dotted-import",
        ),
        pytest.param(
            "scitools_hook.report.human",
            "import scitools_hook.runner.check as pipeline\n",
            "forbidden-layer",
            id="aliased-dotted-import",
        ),
        pytest.param(
            "scitools_hook.analysis.thresholds",
            "from ..runner import check\n",
            "forbidden-layer",
            id="relative-import",
        ),
        pytest.param(
            "scitools_hook.analysis.thresholds",
            "from ...scitools_hook.runner import check\n",
            "forbidden-layer",
            id="deeper-relative-import",
        ),
        pytest.param(
            "scitools_hook.analysis.thresholds",
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from scitools_hook.runner.check import CheckPipeline\n",
            "forbidden-layer",
            id="type-checking-guarded",
        ),
        pytest.param(
            "scitools_hook.analysis.thresholds",
            "def f():\n    from scitools_hook.runner.check import CheckPipeline\n",
            "forbidden-layer",
            id="deferred-inside-a-function",
        ),
        pytest.param(
            "scitools_hook.paths",
            "from scitools_hook.errors import GateError\n",
            "forbidden-layer",
            id="leaf-may-import-nothing",
        ),
        pytest.param(
            "scitools_hook.errors",
            "from scitools_hook.paths import classify_file\n",
            "forbidden-layer",
            id="errors-may-import-only-exit-codes",
        ),
        pytest.param(
            WORKER_MODULE,
            "from scitools_hook.exit_codes import ExitCode\n",
            "forbidden-layer",
            id="worker-may-not-even-import-the-leaf",
        ),
        pytest.param(
            "scitools_hook.report.human",
            "from scitools_hook.models.findings import _private\n",
            "private-name",
            id="private-name-across-modules",
        ),
        pytest.param(
            "scitools_hook.brandnew.thing",
            "from scitools_hook.config.models import Settings\n",
            "unknown-layer",
            id="a-new-layer-has-no-rules-yet",
        ),
        pytest.param(
            "scitools_hook.report.human",
            "from scitools_hook.brandnew.thing import Thing\n",
            "unknown-layer",
            id="importing-a-layer-with-no-rules",
        ),
    ],
)
def test_the_checker_reports_a_forbidden_import(module: str, source: str, rule: str) -> None:
    found = check_module(module, source)
    assert found, f"{module} importing from {source!r} was not reported"
    assert [v.rule for v in found] == [rule]


@pytest.mark.parametrize(
    ("module", "source"),
    [
        pytest.param(
            "scitools_hook.runner.check",
            "from scitools_hook.understand.database import DatabaseManager\n",
            id="runner-imports-everything-below-it",
        ),
        pytest.param(
            "scitools_hook.report.human",
            "from scitools_hook.analysis.classify import classify\n",
            id="report-imports-analysis",
        ),
        pytest.param(
            "scitools_hook.models.findings",
            "from scitools_hook.config.models import Settings\n",
            id="models-imports-config",
        ),
        pytest.param(
            "scitools_hook.analysis.baseline",
            "from scitools_hook.analysis.population import reduce\n",
            id="within-a-layer",
        ),
        pytest.param(
            "scitools_hook.cli.app",
            "from scitools_hook import __version__\n",
            id="dunder-names-are-not-private",
        ),
        pytest.param(
            "scitools_hook.analysis.thresholds",
            "import json\nimport os\nfrom pathlib import Path\nfrom . import population\n",
            id="external-and-sibling-imports",
        ),
        pytest.param(
            "scitools_hook.cli.db",
            "from scitools_hook.understand.database import DatabaseManager\n",
            id="the-recorded-adapter-exception",
        ),
        pytest.param(
            "scitools_hook.cli.config_cmd",
            "from scitools_hook.config.loader import threshold_tables\n",
            id="a-public-name-crosses-a-module-boundary",
        ),
        pytest.param(
            COMPOSITION_ROOT,
            "from scitools_hook.understand.database import DatabaseManager\n",
            id="the-composition-root-builds-the-adapters",
        ),
        pytest.param(
            "scitools_hook.understand.database",
            "from scitools_hook.models.ports import ShadowPort\n",
            id="an-adapter-reaches-its-sibling-through-a-port-in-models",
        ),
    ],
)
def test_the_checker_permits_a_legal_import(module: str, source: str) -> None:
    assert check_module(module, source) == []


@pytest.mark.parametrize(
    ("module", "source"),
    [
        pytest.param(
            "scitools_hook.analysis.ratchet",
            "import subprocess\n",
            id="analysis-may-not-run-a-subprocess",
        ),
        pytest.param(
            "scitools_hook.report.human",
            "import os\n",
            id="report-may-not-import-os",
        ),
        pytest.param(
            "scitools_hook.analysis.ratchet",
            "from shutil import copy\n",
            id="from-form-is-seen-too",
        ),
        pytest.param(
            "scitools_hook.report.markdown",
            "def f(p):\n    return open(p).read()\n",
            id="the-builtin-open-is-seen",
        ),
    ],
)
def test_the_purity_checker_reports_a_reach_outside(module: str, source: str) -> None:
    assert [v.rule for v in check_purity(module, source)] == ["layer-purity"]


@pytest.mark.parametrize(
    ("module", "source"),
    [
        pytest.param(
            "scitools_hook.analysis.ratchet",
            "from pathlib import Path\nimport math\n",
            id="a-path-is-a-value-these-layers-carry",
        ),
        pytest.param(
            "scitools_hook.runner.check",
            "import subprocess\nimport os\n",
            id="the-rule-is-only-for-analysis-and-report",
        ),
        pytest.param(
            "scitools_hook.report.human",
            "def f(x):\n    return x.open()\n",
            id="a-method-named-open-is-not-the-builtin",
        ),
    ],
)
def test_the_purity_checker_permits_what_the_layers_may_do(module: str, source: str) -> None:
    assert check_purity(module, source) == []


def test_the_import_scan_reports_the_names_and_lines_it_found() -> None:
    """The scan's own output shape, pinned: the assertions above are only as good as this."""
    source = (
        "import scitools_hook.cli.app\n"
        "from scitools_hook.config import models\n"
        "from scitools_hook.config.loader import load_settings, _threshold_tables\n"
        "import json\n"
        "from . import population\n"
    )
    assert intra_package_imports("scitools_hook.analysis.baseline", source) == [
        ("scitools_hook.cli.app", None, 1),
        ("scitools_hook.config.models", "models", 2),
        ("scitools_hook.config.loader", "load_settings", 3),
        ("scitools_hook.config.loader", "_threshold_tables", 3),
        ("scitools_hook.analysis.population", "population", 5),
    ]


def test_a_relative_import_in_a_package_init_resolves_to_the_package_itself() -> None:
    """``a/b/__init__.py`` is its own package; ``a/b/c.py`` resolves one level higher.

    Reading an ``__init__.py`` as a plain module would place every relative import one layer
    too low, which is the direction that turns a real violation into a clean answer.
    """
    source = "from . import check\n"
    assert intra_package_imports("scitools_hook.runner", source, is_package=True) == [
        ("scitools_hook.runner.check", "check", 1)
    ]
    assert intra_package_imports("scitools_hook.runner", source, is_package=False) == [
        ("scitools_hook", "check", 1)
    ]
    assert intra_package_imports(
        "scitools_hook.runner.explain", "from .check import CheckPipeline\n"
    ) == [("scitools_hook.runner.check", "CheckPipeline", 1)]


def test_a_docstring_naming_a_forbidden_import_is_not_a_forbidden_import() -> None:
    """The regex failure ``tests/test_paths.py`` records, asserted against directly here."""
    source = (
        '"""This module deliberately does not do:\n\n'
        "    from scitools_hook.runner.check import CheckPipeline\n\n"
        'because runner sits above it."""\n'
        "# import scitools_hook.cli.app\n"
        "CODE = 'from scitools_hook.runner.check import CheckPipeline'\n"
    )
    assert intra_package_imports("scitools_hook.analysis.thresholds", source) == []
    assert check_module("scitools_hook.analysis.thresholds", source) == []
