"""What the three lean-code e2e modules share: the fixture and the ways of driving it.

The lean-code family is proven end to end by three modules -- ``test_lean_human_report`` for
the three renderings of the human report, ``test_lean_machine_output`` for JSON, SARIF and
the ``agent-rules`` snippet, and ``test_lean_doctor_and_all_off`` for ``doctor``'s rows and
the every-rule-off equality -- because one module holding all of it named ten first-party
modules against the dependency rule's seven. Each subject imports a different corner of the
package (the renderer's labels, the examples, ``RunResult``) and this helper carries the part
they share: the fixture directory, the configuration texts, the workspace whose seam answers
from the fixture, and one ``check --worktree`` over the forwarding edit. It is a sibling of
``harness`` and is not collected.

The fixture directory ``tests/fixtures/e2e/lean`` describes a change to ``pkg/other.py`` whose
routine ``scan`` forwards its one call to ``pkg.deep.walk`` and has one caller -- the shape
requirement 2.1 names -- and went from three statements over four lines to two over two, so
the change has a real, negative delta. The ``after`` snapshot carries the reference facts and
the call-resolution figure a real extraction would, and ``analyze.json`` carries an accuracy
of one, so the pass-through rule is *trusted* rather than refused at requirement 1.8's floors.
What is exercised above that seam is everything the family added: the configuration key, the
feature record the check reads before it starts, the runner step, the hint and the example, the
three renderers, the ``agent-rules`` section, ``doctor``'s rows and the option resolver.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Final

from e2e.harness import E2E_FIXTURES, OTHER, Workspace, make_workspace
from scitools_hook.models.understand import Availability, Feature, FeatureReport
from scitools_hook.understand.fake import FAKE_VAR, FIXTURE_VERSION
from scitools_hook.understand.features import FEATURES_FILE

LEAN: Final = E2E_FIXTURES / "lean"
"""The fixture directory these modules are about; see the module docstring for what it holds."""

RULE: Final = "structure.pass_through"
FORWARDER: Final = "pkg.other.scan"

NET_LINE: Final = "net: -1 lloc (-2 lines) over 1 routine"
"""What the fixture's delta renders as: three statements over four lines became two over two."""

FORWARDING: Final = "def scan(items):\n    return walk(items)\n"
"""An edit to the routine the fixture snapshot describes as a forwarder.

The seam, not this text, decides the facts; the file is written so a reader can see the
shape the snapshot reports.
"""

ASKS_FOR_IT: Final = '[lean]\npass_through = "warning"\n'
"""The key that turns the rule on; it ships off, like every rule of the family (req 9.1)."""

EXCUSED: Final = ASKS_FOR_IT + 'pass_through_ignore = ["\\\\.scan$"]\n'
"""The same, with the routine excused -- a change with a lean rule looking and nothing to cut."""


def a_workspace(tmp_path: Path) -> Workspace:
    """A repository whose seam answers from the lean fixture."""
    return make_workspace(tmp_path, **{FAKE_VAR: str(LEAN)})


def enabled(workspace: Workspace, configuration: str = ASKS_FOR_IT) -> None:
    """Record what the build offers, then write the configuration -- the operator's own order.

    The record is written here rather than by ``scitools-hook doctor`` because under the seam
    ``doctor`` records every feature as unverified: it runs no Understand at all, and a probe
    answering from fixtures would be measuring the fixtures. On a real install ``doctor``
    writes exactly this file.
    """
    cache = Path(workspace.cli("db", "path").stdout.strip()).parent
    cache.mkdir(parents=True, exist_ok=True)
    measured = FeatureReport(
        build=FIXTURE_VERSION,
        features={
            feature: Availability(state="available", detail="recorded by the test")
            for feature in Feature
        },
    )
    (cache / FEATURES_FILE).write_text(measured.model_dump_json(indent=2), encoding="utf-8")
    workspace.write("scitools-hook.toml", configuration)


def checked(
    workspace: Workspace, *options: str, before: tuple[str, ...] = ()
) -> subprocess.CompletedProcess[str]:
    """One ``check --worktree`` over the forwarding edit; ``before`` are the global options."""
    workspace.write(OTHER, FORWARDING)
    return workspace.cli(*before, "check", "--worktree", *options)


def lines_of(done: subprocess.CompletedProcess[str]) -> list[str]:
    """Standard output as lines, the trailing newline dropped."""
    return done.stdout.rstrip("\n").splitlines()
