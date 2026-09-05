"""The two before-side routes, measured against each other on the installed build (3.1-3.3).

Requirement 3.2 asks for one thing and it is the only thing worth asserting here: a range
check through the commit-built route must report **exactly** what the same check reports
through the exported-shadow route. Anything less makes the routes a coin toss, and a
before side that is subtly not the base commit is a ratchet comparing code against itself.

**This module exists because that is what went wrong.** The design built the before database
with ``-refdb after.und``, which copies the reference's settings and file set -- the parity a
before/after comparison wants. Measured on Build 1262, it also copies the reference's file
*paths*, the Gate's after database names its files under a shadow tree in the user's cache,
and ``-gitcommit`` pins the contents only of files **inside** the ``-gitrepo`` directory. A
file outside it is read from disk, with no warning of any kind. The before database then held
the working tree's code, identical to the after database in every metric, and a range check
that reported eight ratchet findings through the shadow route reported one.

So the route builds the before database rooted at the repository, where ``-gitcommit`` does
pin, and :func:`test_contract_a_database_outside_the_repository_is_not_pinned_at_all` keeps
the reason on the record: a build that fixed this would make the whole route simpler, and a
build that changed it in some third way must not do so silently.

The checks run the installed console script as a real process, because the question is what
an operator gets, and the two runs share nothing but the repository.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from contract_project import TIMEOUT_S, build_database, real_env, write_tree

pytestmark = pytest.mark.contract

BASE_SOURCE = "def add(a, b):\n    return a + b\n\n\ndef use():\n    return add(1, 2)\n"
"""One flat routine; every ratchet finding below is the difference between this and the next."""

HEAD_SOURCE = (
    "def add(a, b):\n"
    "    total = a + b\n"
    "    if total > 10:\n"
    "        for step in range(total):\n"
    "            if step % 2:\n"
    "                total -= 1\n"
    "    return total\n"
    "\n"
    "\n"
    "def use():\n"
    "    return add(1, 2)\n"
)
"""The same routine, nested and branchy: complexity, nesting, statements and paths all move."""

OTHER_SOURCE = "def unused():\n    return 0\n"
"""A second file neither commit touches, so the file set is the same on both sides."""


def cli_path() -> Path:
    """The installed console script, which is what an operator actually runs."""
    found = shutil.which("scitools-hook")
    if found is not None:
        return Path(found)
    beside = Path(sys.executable).parent / "scitools-hook"
    if beside.is_file():
        return beside
    pytest.skip("the installed scitools-hook console script is not on PATH")


def git(root: Path, *args: str) -> str:
    """Run git in ``root`` with the developer's configuration and hooks kept out of it."""
    done = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
        check=False,
        env={
            **os.environ,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Gate Contract",
            "GIT_AUTHOR_EMAIL": "gate@example.invalid",
            "GIT_COMMITTER_NAME": "Gate Contract",
            "GIT_COMMITTER_EMAIL": "gate@example.invalid",
        },
    )
    assert done.returncode == 0, f"git {' '.join(args)}: {done.stderr.strip()}"
    return done.stdout.strip()


def a_repository(tmp_path: Path) -> tuple[Path, str]:
    """Two commits over one routine that gets much worse, and the base commit's hash."""
    root = tmp_path / "repo"
    write_tree(root, {"pkg/core.py": BASE_SOURCE, "pkg/other.py": OTHER_SOURCE})
    git(root, "init", "--quiet", "--initial-branch=main")
    git(root, "add", "--all")
    git(root, "commit", "--quiet", "--no-verify", "--message", "base")
    base = git(root, "rev-parse", "HEAD")
    write_tree(root, {"pkg/core.py": HEAD_SOURCE})
    git(root, "add", "--all")
    git(root, "commit", "--quiet", "--no-verify", "--message", "head")
    return root, base


def run(root: Path, cache: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    """One command, with its analysis cache in a directory of this test's own."""
    return subprocess.run(
        [str(cli_path()), *argv],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
        check=False,
        env={**os.environ, "XDG_CACHE_HOME": str(cache)},
    )


def findings(root: Path, cache: Path, route: str, base: str) -> list[tuple[object, ...]]:
    """One range check through ``route``, as a comparable list of its findings.

    Each run gets its own cache directory, so neither can be answered from the other's
    databases -- which is the only way "the same findings" means anything.
    """
    (root / "scitools-hook.toml").write_text(
        f'[understand]\nbefore_side = "{route}"\n', encoding="utf-8"
    )
    measured = run(root, cache, "doctor")
    assert measured.returncode == 0, measured.stdout + measured.stderr
    done = run(root, cache, "check", "--range", f"{base}..HEAD", "--format", "json")
    assert done.returncode in (0, 1), done.stdout + done.stderr
    document = json.loads(done.stdout)
    return sorted(
        (
            found["rule"],
            found["path"],
            found.get("value"),
            found.get("before"),
            found.get("limit"),
            found.get("severity"),
            found.get("blocking"),
            found.get("preexisting"),
            (found.get("entity") or {}).get("key", {}).get("longname"),
        )
        for found in document["findings"]
    )


def route_of(root: Path, cache: Path) -> str:
    """The route ``doctor`` says the before database was built by (requirement 3.6)."""
    said = run(root, cache, "doctor")
    rows = [
        line.strip() for line in said.stdout.splitlines() if line.strip().startswith("before route")
    ]
    assert rows, said.stdout
    return rows[0].split(":", 1)[1].strip()


def test_contract_the_two_routes_report_the_same_findings(tmp_path: Path) -> None:
    """Requirement 3.2, and the whole reason the commit route may ship at all."""
    root, base = a_repository(tmp_path)

    shadow = findings(root, tmp_path / "shadow-cache", "shadow", base)
    commit = findings(root, tmp_path / "commit-cache", "commit", base)

    assert shadow, "the fixture must produce findings, or equality is vacuous"
    assert commit == shadow


def test_contract_each_route_reports_itself(tmp_path: Path) -> None:
    """Requirement 3.6, and the guard on the test above: two runs, two routes, not one twice."""
    root, base = a_repository(tmp_path)
    shadow_cache, commit_cache = tmp_path / "shadow-cache", tmp_path / "commit-cache"

    findings(root, shadow_cache, "shadow", base)
    findings(root, commit_cache, "commit", base)

    assert route_of(root, shadow_cache) == "shadow"
    assert route_of(root, commit_cache) == f"commit ({base})"


def test_contract_a_database_outside_the_repository_is_not_pinned_at_all(
    tmp_path: Path,
) -> None:
    """``-gitcommit`` pins the contents of files **inside** ``-gitrepo`` and of nothing else.

    The failure it names is silent: no warning, no non-zero status, just the working tree's
    code in a database that says it is a commit. This asserts the shape of the fault rather
    than a metric, so it keeps meaning the same thing if the sample sources change.
    """
    root, base = a_repository(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    shutil.copytree(root / "pkg", elsewhere / "pkg")
    cli = real_env("upython")

    inside = tmp_path / "inside.und"
    outside = tmp_path / "outside.und"
    _pinned(cli.und, inside, root, base, root)
    _pinned(cli.und, outside, root, base, elsewhere)

    assert _longest_routine(cli.und, inside) < _longest_routine(cli.und, outside), (
        "a database rooted outside the repository read the working tree, not the commit"
    )


def test_contract_the_reference_switch_registers_the_comparison_pair(tmp_path: Path) -> None:
    """What ``-refdb`` buys, measured, and why the Gate gives it up (requirement 5.5).

    The pair is registered on the **reference**, not on the database that names it, and no
    metric on Build 1262 reads it -- so giving it up costs a relation nothing consumes.
    """
    root, base = a_repository(tmp_path)
    cli = real_env("upython")
    reference = tmp_path / "reference.und"
    derived = tmp_path / "derived.und"
    build_database(reference, root, ("python",))
    _create(cli.und, derived, root, base, refdb=reference)
    _analyse(cli.und, derived)

    assert _comparison_db(cli.und, reference) == str(derived)
    assert _comparison_db(cli.und, derived) == "None"


def _create(und: Path, db: Path, repo: Path, commit: str, refdb: Path | None = None) -> None:
    """``und create`` for a commit-built database, with or without a reference."""
    argv = ["create", "-db", str(db), "-gitrepo", str(repo), "-gitcommit", commit]
    if refdb is not None:
        argv += ["-refdb", str(refdb)]
    _und(und, [*argv, "-languages", "Python", "-local"])


def _analyse(und: Path, db: Path) -> None:
    _und(und, ["-db", str(db), "analyze", "-all"])


def _pinned(und: Path, db: Path, repo: Path, commit: str, root: Path) -> None:
    """A commit-built database over ``root``, which may or may not be inside ``repo``."""
    _create(und, db, repo, commit)
    _und(und, ["-db", str(db), "add", str(root)])
    _analyse(und, db)


def _und(und: Path, argv: list[str]) -> str:
    done = subprocess.run(
        [str(und), *argv], capture_output=True, text=True, timeout=TIMEOUT_S, check=False
    )
    assert done.returncode == 0, f"und {' '.join(argv)}: {done.stdout}{done.stderr}"
    return done.stdout


PROBE = """
import sys
import understand
db = understand.open(sys.argv[1])
if sys.argv[2] == "comparison":
    print(db.comparison_db())
else:
    sizes = [
        ent.metric(["CountLineCode"])["CountLineCode"] or 0
        for ent in db.ents("function ~unknown ~unresolved")
        if ent.ref("definein, declarein") is not None
    ]
    print(max(sizes) if sizes else 0)
"""
"""Read from the database rather than from the wrapper: this is about Understand, not us."""


def _probe(und: Path, db: Path, question: str) -> str:
    script = db.parent / f"probe-{question}.py"
    script.write_text(PROBE, encoding="utf-8")
    upython = und.with_name("upython")
    done = subprocess.run(
        [str(upython), str(script), str(db), question],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    return done.stdout.strip().splitlines()[-1]


def _longest_routine(und: Path, db: Path) -> int:
    """The largest ``CountLineCode`` among the project's own routines."""
    return int(_probe(und, db, "size"))


def _comparison_db(und: Path, db: Path) -> str:
    """What ``Db.comparison_db()`` answers, as text, so ``None`` is a value like any other."""
    return _probe(und, db, "comparison")
