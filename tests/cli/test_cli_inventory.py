"""The CLI package's own inventory: every module scanned, and none of them prompting.

Extracted from ``test_cli_app.py`` when that file crossed ``file.CountLineCode``. The seam is
a real one: everything here is about the *package* -- which modules exist and what their
source may not contain -- while the file it came from is about the assembled application and
the behaviour of its commands.

The scan is what makes the no-prompt rule a fact rather than a habit. A gate that blocks a
commit and then waits for an answer nobody can give hangs the commit instead of refusing it,
and a hook has no terminal to answer from.
"""

from __future__ import annotations

import re
from pathlib import Path

from scitools_hook.cli import common

CLI_SOURCE_DIR = Path(common.__file__).resolve().parent

PROMPT_CALLS = re.compile(r"\b(typer\.prompt|typer\.confirm|click\.prompt|input|getpass)\s*\(")

CLI_MODULES = {
    "__init__.py",
    "agent_rules.py",
    "app.py",
    "baseline.py",
    "change.py",
    "check.py",
    "common.py",
    "config_cmd.py",
    "db.py",
    "doctor.py",
    "explain.py",
    "hooks.py",
    "pipelines.py",
    "recommend.py",
    "skills.py",
    "targets.py",
}
"""Every module in the package. A scan of nothing passes; this is what makes it a scan."""


def test_no_cli_module_calls_a_prompt_function() -> None:
    scanned = []
    offenders = []
    for source in sorted(CLI_SOURCE_DIR.glob("*.py")):
        scanned.append(source.name)
        for number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
            if PROMPT_CALLS.search(line) and not line.lstrip().startswith("#"):
                offenders.append(f"{source.name}:{number}: {line.strip()}")
    assert set(scanned) == CLI_MODULES
    assert offenders == []
