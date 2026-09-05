"""The sample repository task 10.1 measures the installed Understand against.

Not a test module: it holds the fixture the contract tests share, so every test reads the same
measured project. The fixture is session-scoped, but importing it into several test modules
registers it once per module, so the databases are built once per module that asks for them --
about 1.3 s a pair, which is why this is left simple rather than cached across modules.

**Why the project is written here rather than reused from ``tests/fixtures/sample_project``.**
That fixture is a before/after pair whose two sides differ on purpose, and several other
tasks' contract tests assert against its exact contents. Requirement 4.4 needs the opposite:
two databases built from *different directories* over **token-identical** sources, so that any
difference between the two sides is a difference Understand introduced -- a root leaking into
an entity's identity -- and never a difference in the code. It also needs constructs that
fixture deliberately does not have: C++ overloads, a file sitting directly in the analysis
root, and a directory that holds both files and a subdirectory.

Everything here is built with plain ``und`` subprocess calls. A contract test that built its
database through :class:`~scitools_hook.understand.database.DatabaseManager` would be testing
the manager; the databases are the *given*, and the adapters are what is under test.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
from conftest import understand_probe

from scitools_hook.config.models import Limit, Settings, StructureRules, ThresholdSpec
from scitools_hook.models.progress import NullCommandLog
from scitools_hook.models.snapshot import ProjectSnapshot, Side
from scitools_hook.models.understand import UnderstandEnv
from scitools_hook.understand.api_runner import ApiRunner
from scitools_hook.understand.snapshot import SnapshotExtractor, SnapshotTarget

TIMEOUT_S = 600
"""Generous ceiling: a contract run must fail loudly rather than hang the suite."""

LANGUAGES = ("python", "c++")
"""The two languages the sample repository is written in."""

ROOTS = ("alpha", "beta")
"""The two directory names the identical sources are analysed from (req 4.4)."""

SOURCES: dict[str, str] = {
    # A module sitting directly in the analysis root. Every real repository has one
    # (`setup.py`, `conftest.py`, `manage.py`) and it is the case that made `relname()`
    # return a path prefixed with the root's own name.
    "main.py": '''"""Entry module sitting directly in the analysis root."""

from app.entry import entry_point


def main():
    return entry_point()
''',
    # A sibling of `pkg/`, so that one architecture node really depends on another.
    "app/entry.py": '''"""A directory beside the package, so a sibling architecture edge exists."""

from pkg.core import Engine


def entry_point():
    return Engine().run(1)
''',
    # A directory that holds a file *and* a subdirectory: `pkg/core.py` beside `pkg/inner/`.
    # This is what decides which node holds `core.py` at depth 2.
    "pkg/core.py": '''"""A class with a method, a classmethod and a staticmethod."""

from pkg.inner.leaf import Leaf


class Engine:
    def __init__(self):
        self.leaf = Leaf()

    def run(self, value):
        return self.leaf.widen(value)

    @classmethod
    def build(cls):
        return cls()

    @staticmethod
    def label():
        return "engine"
''',
    "pkg/inner/leaf.py": '''"""The deepest package, two directory levels below the analysis root."""


class Leaf:
    def widen(self, value):
        return value + 1
''',
    # Two overload pairs: one member function and one free function. `EntityKey` must tell
    # each pair apart, and the header/source split must not produce two entities per routine.
    "native/shape.h": """#ifndef SAMPLE_SHAPE_H
#define SAMPLE_SHAPE_H

class Shape {
public:
    explicit Shape(int side);
    int area(int width) const;
    int area(int width, int height) const;
    int side() const;

private:
    int side_;
};

int scale(int value);
int scale(int value, int factor);

#endif
""",
    "native/shape.cpp": """#include "shape.h"

Shape::Shape(int side) : side_(side) {}

int Shape::area(int width) const { return width * side_; }

int Shape::area(int width, int height) const { return width * height; }

int Shape::side() const { return side_; }

int scale(int value) { return value * 2; }

int scale(int value, int factor) { return value * factor; }
""",
}
"""The sample repository, one entry per file, written verbatim under both roots."""

BASE_SOURCES: dict[str, str] = {
    **SOURCES,
    "main.py": "from app.entry import entry_point\n\n\ndef main():\n    return entry_point()\n",
}
"""The same files one commit earlier: ``main`` without its docstring line.

The contract project needs a history, not just a tree, because three things in the
understand-8-features specification are about a repository rather than a directory: a before
database built from a commit (requirement 3.2), a git-derived architecture generated from
commit dates and authors (requirement 4.3), and the comparison pair the two register with
each other (requirement 5.5). One commit would give a history with nothing before it, so
there are two, and the difference is deliberately the smallest thing that still moves a
metric -- ``main.py`` loses a line -- so that a before/after comparison over this project has
something to compare while every existing expectation about the *working tree* is untouched.
"""

FILES: tuple[str, ...] = tuple(sorted(SOURCES))
"""Every source file, root-relative with forward slashes -- the request's ``files``."""


def und() -> Path:
    """The licensed ``und`` the session probe found; the marker skips the test without one."""
    probe = understand_probe()
    assert probe.und is not None, probe.reason
    return probe.und


def upython() -> Path:
    """The interpreter Understand ships next to ``und``; skip when this build has none."""
    found = und().parent / "upython"
    if not found.exists():
        pytest.skip(f"no upython next to {und()}")
    return found


def real_env(mode: str) -> UnderstandEnv:
    """The installation this machine has, in the requested execution mode."""
    interpreter = upython()
    bin_dir = interpreter.parent
    return UnderstandEnv(
        home=bin_dir.parent.parent,
        und=bin_dir / "und",
        upython=interpreter,
        python_api_dir=bin_dir / "Python",
        version="6.5.1204",
        source="env:SCITOOLS_HOME",
        api_mode="upython" if mode == "upython" else "inprocess",
    )


def run_und(*args: str) -> subprocess.CompletedProcess[str]:
    """Run ``und`` directly, never through the adapters these tests exist to justify."""
    return subprocess.run(
        [str(und()), *args], capture_output=True, text=True, timeout=TIMEOUT_S, check=False
    )


def write_tree(root: Path, sources: dict[str, str] | None = None) -> Path:
    """Write one copy of the sample repository under ``root`` and return it."""
    for name, text in (sources or SOURCES).items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return root


@dataclass(frozen=True)
class History:
    """The two commits written over the sample sources, newest last."""

    base: str
    head: str


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run git in ``root`` with the developer's configuration and hooks kept out of it."""
    environment = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Gate Contract",
        "GIT_AUTHOR_EMAIL": "gate@example.invalid",
        "GIT_COMMITTER_NAME": "Gate Contract",
        "GIT_COMMITTER_EMAIL": "gate@example.invalid",
    }
    done = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
        check=False,
        env=environment,
    )
    if done.returncode != 0:
        pytest.fail(f"git {' '.join(args)} exited {done.returncode}: {done.stderr.strip()}")
    return done


def init_history(root: Path) -> History:
    """Write the sample sources as two commits, leaving the tree at the second.

    The working tree ends holding exactly :data:`SOURCES`, which is what every contract test
    written before this fixture had a history expects to find there. What the history adds is
    a *base commit* that holds something else.
    """
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "--quiet", "--initial-branch=main")
    commits = []
    for sources, message in ((BASE_SOURCES, "the base commit"), (SOURCES, "the head commit")):
        write_tree(root, sources)
        _git(root, "add", "--all")
        _git(root, "commit", "--quiet", "--no-verify", "--message", message)
        commits.append(_git(root, "rev-parse", "HEAD").stdout.strip())
    return History(base=commits[0], head=commits[1])


def build_database(db: Path, root: Path, languages: tuple[str, ...] = LANGUAGES) -> None:
    """Create a database over ``root`` and analyse it whole, failing loudly if it cannot.

    ``-quiet`` is used for ``create``/``add`` only: it silences the *answer* as well as the
    noise, and ``analyze`` needs its parse errors (measured, tasks.md 6.5).
    """
    for argv in (
        ["-quiet", "create", "-db", str(db), "-languages", *languages, "-local"],
        ["-quiet", "-db", str(db), "add", str(root)],
        ["-db", str(db), "analyze", "-all", "-errors", "-warnings"],
    ):
        done = run_und(*argv)
        if done.returncode != 0:
            pytest.fail(f"und {' '.join(argv)} exited {done.returncode}: {done.stderr.strip()}")


@dataclass(frozen=True)
class SampleProject:
    """The same sources analysed from two different directories (requirement 4.4)."""

    workdir: Path
    history: History

    @property
    def repo(self) -> Path:
        """The analysis root that is also a git repository, for the tests that need one."""
        return self.root(ROOTS[0])

    @property
    def base_commit(self) -> str:
        """The commit a before side of this project represents (requirements 3.2, 4.3)."""
        return self.history.base

    def root(self, name: str) -> Path:
        """The analysis root of one side, exactly as ``und add`` received it."""
        return self.workdir / name

    def db(self, name: str) -> Path:
        """The database built from one side."""
        return self.workdir / f"{name}.und"

    def analysed(self, name: str) -> list[str]:
        """The files the database holds, root-relative, as ``und list files`` reports them."""
        listing = run_und("-db", str(self.db(name)), "list", "files")
        assert listing.returncode == 0, listing.stderr
        root = f"{self.root(name)}/"
        return sorted(
            line.strip()[len(root) :] for line in listing.stdout.splitlines() if root in line
        )


@pytest.fixture(scope="session")
def sample_project(tmp_path_factory: pytest.TempPathFactory) -> SampleProject:
    """Two databases over token-identical sources, built from two differently named roots."""
    workdir = tmp_path_factory.mktemp("contract-project")
    project = SampleProject(workdir, init_history(workdir / ROOTS[0]))
    for name in ROOTS[1:]:
        write_tree(project.root(name))
    for name in ROOTS:
        build_database(project.db(name), project.root(name))
    return project


def contract_settings(depth: int = 2) -> Settings:
    """Thresholds naming every metric these tests read, including both synthetic ones.

    The request the worker receives is built by the production
    :class:`~scitools_hook.understand.snapshot.SnapshotExtractor` from these settings, so a
    metric only reaches the worker because a threshold asks for it -- exactly as it would on
    a real run. ``depth`` is the architecture depth requirement 6.7 makes configurable.
    """
    return Settings(
        thresholds=[
            ThresholdSpec(scope="routine", metric="CyclomaticStrict", limit=Limit(max=10)),
            ThresholdSpec(scope="routine", metric="CountLineCode", limit=Limit(max=60)),
            ThresholdSpec(scope="routine", metric="MaxNesting", limit=Limit(max=4)),
            ThresholdSpec(scope="routine", metric="CountParams", limit=Limit(max=5)),
            ThresholdSpec(scope="class", metric="CountDeclMethod", limit=Limit(max=20)),
            ThresholdSpec(scope="class", metric="CountDeclMethodNonStub", limit=Limit(max=20)),
            ThresholdSpec(scope="class", metric="PercentLackOfCohesion", limit=Limit(max=80)),
            ThresholdSpec(scope="file", metric="CountLineCode", limit=Limit(max=500)),
            ThresholdSpec(scope="file", metric="CountDeclFunction", limit=Limit(max=25)),
            ThresholdSpec(scope="file", metric="RatioCommentToCode", limit=Limit(min=0.0)),
            ThresholdSpec(scope="routine", metric="AVG:CyclomaticStrict", limit=Limit(max=5)),
            ThresholdSpec(scope="project", metric="MaxCyclomaticStrict", limit=Limit(max=15)),
        ],
        structure=StructureRules(depth=depth),
    )


def extract(
    db: Path, root: Path, files: tuple[str, ...], side: Side = "after", depth: int = 2
) -> ProjectSnapshot:
    """Read one real database into a snapshot through the production extractor."""
    runner = ApiRunner(real_env("upython"), NullCommandLog())
    extractor = SnapshotExtractor(runner, contract_settings(depth))
    target = SnapshotTarget(db=db, root=root, side=side, files=frozenset(files))
    return extractor.extract(target)


def a_symlink(source: Path, link: Path) -> None:
    """Make ``link`` point at ``source``, skipping where the filesystem refuses symlinks."""
    try:
        link.symlink_to(source)
    except (OSError, NotImplementedError) as refused:  # pragma: no cover - platform specific
        pytest.skip(f"this filesystem does not support symlinks: {refused!r}")


def comma_decimal_locale() -> str:
    """An installed locale whose decimal separator is a comma, or skip.

    Setting ``LC_NUMERIC`` to a locale the system does not have is a no-op, which would make
    a test that measures the comma-decimal hazard pass while proving nothing.
    """
    listing = shutil.which("locale")
    if listing is None:  # pragma: no cover - measured on a machine that has `locale`
        pytest.skip("no `locale` command, so no installed comma-decimal locale can be named")
    done = subprocess.run(
        [listing, "-a"], capture_output=True, text=True, timeout=TIMEOUT_S, check=False
    )
    installed = {line.strip().lower() for line in done.stdout.splitlines()}
    for candidate in ("de_DE.UTF-8", "fr_FR.UTF-8", "es_ES.UTF-8", "pt_BR.UTF-8"):
        if candidate.lower() in installed or candidate.lower().replace("-", "") in installed:
            return candidate
    pytest.skip("no comma-decimal locale is installed, so the SVG hazard cannot be provoked")
