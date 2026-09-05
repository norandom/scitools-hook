"""The stubbed ``und`` the wrapper tests drive, and the transcripts it replays.

Every unit test of :mod:`scitools_hook.understand.und_cli` drives a **stubbed executable** --
a small Python script written into ``tmp_path`` and made executable -- instead of the real
``und``. That keeps the suite runnable without a licence while still exercising the parts
that matter: the exact argv, the exit status, both output streams, and a command that never
returns. The stub replays a plan (``plan.json`` beside it) keyed by subcommand, appends every
argv it was given to ``calls.jsonl``, and snapshots the content of every file named on the
command line *while it runs* -- the only moment a temporary ``@list`` file still exists.

The scripted outputs are transcripts of the real ``und`` (6.5 build 1204 and 8.0 build 1262)
on a licensed machine, not invented text. This module holds the stub and the transcripts more
than one test module replays; ``test_und_cli.py`` keeps the ones only it uses.
"""

from __future__ import annotations

import json
import stat
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest

from scitools_hook.models.progress import CommandLog
from scitools_hook.models.understand import UnderstandEnv
from scitools_hook.understand.und_cli import (
    UndCli,
)

# --- transcripts of the real und -----------------------------------------------

VERSION_OUTPUT = "(Build 1204)\n"
"""``und version`` on 6.5 build 1204: a build number and no product version."""

NO_COMMAND_OUTPUT = (
    'Error: No valid command found. Type "und help" for help.\n'
    'Error: Unrecognized arguments. Type "und help" for help.\n'
)
"""What ``und -version`` answers: this build has no ``--version`` switch."""

LICENSE_OUTPUT = "Reply Code : D36C3CA9FF44A\nReply Date : 2036-08-28\n\n"
"""``und license`` with a valid license: a reply code, and no mention of a problem."""

NO_LICENSE_OUTPUT = "Licensing Error: No Und License Found\n"
"""The licensing text built into the executable; the wrapper must map it, not report it."""

CODECHECK_NO_LICENSE = "Licensing Error: No license for CodeCheck. \nStopping CodeCheck. \n"
"""Measured verbatim on the licensed machine, whose license excludes CodeCheck."""

BAD_DB_STDERR = (
    "Error: unable to open /nonexistent/nope.und\n"
    "Error: An open database is required for this action. \n"
)
"""``und -db <missing> analyze -all``: exit status 1 and this on standard error."""


# --- the stubbed executable ------------------------------------------------------

STUB_SHEBANG = f"#!{sys.executable}"
"""The stub is launched by absolute path, never through ``PATH``.

The tests below hand ``und`` deliberately hostile search paths -- one holding a Python 2
decoy, one holding nothing at all -- and a stub whose own shebang was ``/usr/bin/env
python3`` would then fail to start and exit 127. This project has read such a 127 as evidence
about the code under test five times; an absolute shebang removes the question.
"""

STUB_SOURCE = '''#!/usr/bin/env python3
"""Stand-in for ``und``: record the call and its environment, snapshot list files, replay a plan."""
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ARGV = sys.argv[1:]

with open(os.path.join(HERE, "calls.jsonl"), "a", encoding="utf-8") as handle:
    handle.write(json.dumps(ARGV) + "\\n")

# The real und resolves a bare `python` off PATH *while it runs* and analyses Python 2 when
# it finds none, so the answer has to be taken here: the directory the Gate builds is gone
# by the time the test looks.
FOUND = shutil.which("python")
with open(os.path.join(HERE, "environment.json"), "w", encoding="utf-8") as handle:
    json.dump(
        {
            "PATH": os.environ.get("PATH", ""),
            "python": FOUND,
            "python_real": os.path.realpath(FOUND) if FOUND else None,
            "marker": os.environ.get("SCITOOLS_HOOK_STUB_MARKER", ""),
        },
        handle,
    )

# Merged across calls, not overwritten: one wrapper method can run several und commands
# (`declare_architecture` runs four), and a snapshot taken by the last of them would hide
# the temporary document an earlier one handed over -- which only exists while it runs.
SEEN = {}
if os.path.isfile(os.path.join(HERE, "lists.json")):
    with open(os.path.join(HERE, "lists.json"), encoding="utf-8") as handle:
        SEEN = json.load(handle)
for token in ARGV:
    named = token[1:] if token.startswith("@") else token
    if os.path.isfile(named):
        with open(named, encoding="utf-8") as handle:
            SEEN[os.path.basename(named)] = handle.read()
with open(os.path.join(HERE, "lists.json"), "w", encoding="utf-8") as handle:
    json.dump(SEEN, handle)

with open(os.path.join(HERE, "plan.json"), encoding="utf-8") as handle:
    PLANS = json.load(handle)
PLAN = next((PLANS[token] for token in ARGV if token in PLANS), PLANS.get("default", {}))

time.sleep(PLAN.get("sleep", 0))
OUT = ARGV[-1] if ARGV and os.path.isdir(ARGV[-1]) else HERE
for name in PLAN.get("mkdir", []):
    os.makedirs(os.path.join(OUT, name), exist_ok=True)
for name in PLAN.get("symlink_loop", []):
    target = os.path.join(OUT, name)
    os.symlink(target, target)
for name, text in PLAN.get("write", {}).items():
    with open(os.path.join(OUT, name), "w", encoding="utf-8") as handle:
        handle.write(text)
# `analyze -sarif <file>` names the file it writes after a switch rather than at the end, so
# a plan that could only write into a directory or at the tail could not stand in for it.
for switch, text in PLAN.get("write_switch", {}).items():
    if switch in ARGV and ARGV.index(switch) + 1 < len(ARGV):
        with open(ARGV[ARGV.index(switch) + 1], "w", encoding="utf-8") as handle:
            handle.write(text)
# `export -arch <name> <file>` names the file it writes as its last argument, so a plan that
# could only write into a directory could not stand in for it at all.
if "write_argv" in PLAN and ARGV:
    with open(ARGV[-1], "w", encoding="utf-8") as handle:
        handle.write(PLAN["write_argv"])
sys.stdout.write(PLAN.get("stdout", ""))
sys.stderr.write(PLAN.get("stderr", ""))
sys.exit(PLAN.get("rc", 0))
'''


@dataclass(frozen=True)
class UndStub:
    """An executable that impersonates ``und`` and reports what it was asked to do."""

    root: Path

    @property
    def path(self) -> Path:
        """The executable an :class:`UnderstandEnv` should point at."""
        return self.root / "und"

    def plan(self, plans: Mapping[str, Mapping[str, object]]) -> None:
        """Script the answers, keyed by any argv token (a subcommand) or ``default``."""
        (self.root / "plan.json").write_text(json.dumps(plans), encoding="utf-8")

    @property
    def calls(self) -> list[list[str]]:
        """Every argv the stub was run with, in order, without the executable itself."""
        recorded = self.root / "calls.jsonl"
        if not recorded.exists():
            return []
        return [json.loads(line) for line in recorded.read_text(encoding="utf-8").splitlines()]

    @property
    def argv(self) -> list[str]:
        """The single call the test made; fails loudly when there was not exactly one."""
        assert len(self.calls) == 1, f"expected one und call, got {self.calls}"
        return self.calls[0]

    @property
    def environment(self) -> dict[str, str | None]:
        """What the stub saw of its own environment, recorded while it ran.

        ``python`` is resolved *inside* the stub for the reason ``und`` resolves it inside
        ``und``: the directory the wrapper builds exists only for the length of one call, so
        a test that looked afterwards would find nothing and could not tell a working pin
        from an absent one.
        """
        seen = self.root / "environment.json"
        assert seen.exists(), "the stub did not run, so it recorded no environment"
        return dict(json.loads(seen.read_text(encoding="utf-8")))

    @property
    def lists(self) -> dict[str, str]:
        """Content of every file named on the last command line, read while it ran."""
        snapshot = self.root / "lists.json"
        if not snapshot.exists():
            return {}
        return dict(json.loads(snapshot.read_text(encoding="utf-8")))

    def env(self) -> UnderstandEnv:
        """An installation whose ``und`` is this stub."""
        return understand_env(self.path)


def understand_env(und: Path) -> UnderstandEnv:
    """The minimal :class:`UnderstandEnv` the wrapper needs: it only ever reads ``und``."""
    home = und.parent
    return UnderstandEnv(
        home=home,
        und=und,
        upython=None,
        python_api_dir=home / "Python",
        version=VERSION_OUTPUT.strip(),
        source="test",
        api_mode="upython",
    )


def write_stub(tmp_path: Path) -> UndStub:
    """A stubbed ``und`` executable with an empty plan, ready to be scripted.

    Each test module that drives the stub declares its own ``stub`` fixture over this, so
    that the fixture and the parameter every test names after it live in one module.
    """
    root = tmp_path / "bin"
    root.mkdir()
    script = root / "und"
    body = STUB_SOURCE.split("\n", 1)[1]
    script.write_text(f"{STUB_SHEBANG}\n{body}", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    made = UndStub(root)
    made.plan({})
    return made


@dataclass
class RecordingLog:
    """A ``CommandLog`` that keeps what it was told, so timing and rc can be asserted."""

    entries: list[tuple[list[str], float, int]]

    def record(self, argv: list[str], seconds: float, rc: int) -> None:
        """Keep one finished command."""
        self.entries.append((list(argv), seconds, rc))

    @property
    def codes(self) -> list[int]:
        """The exit status of every recorded command."""
        return [rc for _, _, rc in self.entries]


def cli(stub: UndStub, log: CommandLog, timeout_s: int = 900) -> UndCli:
    """The wrapper under test, pointed at the stub."""
    return UndCli(stub.env(), log, timeout_s=timeout_s)


def db_path(tmp_path: Path) -> Path:
    """A database path; a ``.und`` database is a directory, so nothing is created here."""
    return tmp_path / "cache" / "after.und"


def assume_unsorted_readdir(directory: Path) -> None:
    """Assert there is something for sorting to do before pinning a sorted message.

    The filesystem decides what order ``iterdir`` hands entries back in. Where that order is
    already alphabetical there is nothing an unsorted implementation could get wrong, and a
    test claiming otherwise would be claiming more than it checked.
    """
    listed = [path.name for path in directory.iterdir()]
    if listed == sorted(listed):
        pytest.skip(f"this filesystem lists {directory} already sorted; nothing to pin")
