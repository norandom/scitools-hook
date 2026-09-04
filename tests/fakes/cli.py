"""Test doubles for the three command pipelines of task 9.2.

Imported as ``fakes.cli`` rather than through ``fakes/__init__.py``, for the reason
:mod:`fakes.api` states: a module only ever imported by its own path cannot collide with
another task's edit of the package header.

**Every double answers from what it was asked, never from what it holds.** That is the
lesson task 8.4 recorded after its own stub extractor hid a real requirement defect for two
tasks: a double that answers the whole scripted document regardless of the request cannot
fail when the caller forgets to ask for something. So :class:`StubCheck` stamps the selection
it received into the ``RunResult`` it returns, and :class:`StubExplain` stamps the target and
the options it received into the ``ChangeSummary`` -- which means a command that passes the
wrong selection, or drops ``--graphs``, changes the *rendered output* a test reads, not just
a counter a test might forget to assert.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fixtures.constants import STARTED_AT

from scitools_hook.config.defaults import default_settings
from scitools_hook.config.models import Settings
from scitools_hook.models.baseline import Baseline
from scitools_hook.models.change import ChangeSummary
from scitools_hook.models.findings import Finding, HighestValue, RunResult
from scitools_hook.runner.baseline_cmd import BaselineCapture
from scitools_hook.runner.explain import CommitRange, ExplainOptions
from scitools_hook.runner.pipeline import Selection

TOOL_VERSION = "9.2.0-test"
UNDERSTAND_VERSION = "Understand 6.5 (Build 1204)"
DB_PATH = "/cache/repo/after.und"


def describe(selection: Selection | CommitRange) -> str:
    """One line naming what a run covered; the same shape ``runner.check`` records."""
    if isinstance(selection, CommitRange):
        return f"range: {selection.base}..{selection.head}"
    if selection.mode == "files":
        return f"files: {', '.join(selection.files)}"
    return selection.mode


def a_finding(**overrides: Any) -> Finding:
    """One blocking routine-scope threshold finding, adjustable field by field."""
    fields: dict[str, Any] = {
        "kind": "threshold",
        "rule": "routine.CyclomaticStrict",
        "metric": "CyclomaticStrict",
        "scope": "routine",
        "path": "src/app.py",
        "line": 12,
        "value": 21.0,
        "limit": 10.0,
        "severity": "error",
        "blocking": True,
        "message": "CyclomaticStrict is 21, over the limit of 10",
        "hint": "split the routine at its top-level branches",
    }
    fields.update(overrides)
    return Finding(**fields)


HIGHEST = (HighestValue(scope="routine", metric="CyclomaticStrict", value=21.0),)
"""One highest value, so ``--show-highest`` has something to print.

Without it the flag has **no observable effect** -- an empty ``highest`` list renders the
same section-free output either way, which made a first version of the ``--show-highest``
test pass against a renderer that ignored the setting entirely (found by mutation).
"""


def a_result(selection: str, findings: Sequence[Finding] = ()) -> RunResult:
    """A ``RunResult`` for ``selection``, with ``blocking_count`` kept consistent."""
    return RunResult(
        tool_version=TOOL_VERSION,
        understand_version=UNDERSTAND_VERSION,
        repo_root="/repo",
        selection=selection,
        started_at=STARTED_AT,
        seconds=0.5,
        findings=list(findings),
        highest=list(HIGHEST),
        analyzed_files=1,
        blocking_count=sum(1 for finding in findings if finding.blocking),
        warning_count=sum(1 for finding in findings if finding.severity == "warning"),
        preexisting_count=sum(1 for finding in findings if finding.preexisting),
    )


@dataclass
class StubCheck:
    """Stands in for ``CheckPipeline``: records the selection and answers about it."""

    findings: Sequence[Finding] = ()
    error: BaseException | None = None
    selections: list[Selection] = field(default_factory=list)

    def run(self, selection: Selection) -> RunResult:
        """Answer for ``selection``; the returned result names the selection it was given."""
        self.selections.append(selection)
        if self.error is not None:
            raise self.error
        return a_result(describe(selection), self.findings)


def a_summary(
    selection: Selection | CommitRange, options: ExplainOptions | None = None
) -> ChangeSummary:
    """The summary :class:`StubExplain` answers with: one that quotes its own input."""
    asked = ExplainOptions() if options is None else options
    return ChangeSummary(
        db_path=DB_PATH,
        open_command=(
            f"understand {DB_PATH} [{describe(selection)}]"
            f"[graphs={asked.graphs}][impact={asked.impact}][out={asked.out_dir}]"
        ),
    )


@dataclass
class StubExplain:
    """Stands in for ``ExplainPipeline``: records the target and the aids it was asked for."""

    error: BaseException | None = None
    targets: list[Selection | CommitRange] = field(default_factory=list)
    options: list[ExplainOptions] = field(default_factory=list)

    def run(
        self, selection: Selection | CommitRange, options: ExplainOptions | None = None
    ) -> ChangeSummary:
        """Answer for ``selection``; the summary quotes the target and the aids requested."""
        asked = ExplainOptions() if options is None else options
        self.targets.append(selection)
        self.options.append(asked)
        if self.error is not None:
            raise self.error
        return a_summary(selection, asked)


@dataclass
class StubBaseline:
    """Stands in for ``BaselineCmd``: records the path it was given and reports on it."""

    written: bool = True
    missing: tuple[str, ...] = ()
    error: BaseException | None = None
    paths: list[Path | None] = field(default_factory=list)
    default_path: Path = Path("/repo/scitools-hook-baseline.json")

    def run(self, path: Path | None = None) -> BaselineCapture:
        """Capture into ``path``; ``None`` resolves the way ``BaselineCmd`` resolves it."""
        self.paths.append(path)
        if self.error is not None:
            raise self.error
        return BaselineCapture(
            path=self.default_path if path is None else path,
            baseline=Baseline(captured_at=STARTED_AT, values={"routine.CyclomaticStrict": 12.0}),
            missing=self.missing,
            written=self.written,
        )


@dataclass
class StubContext:
    """The two things a command reads off ``RunContext`` after the pipeline has answered."""

    settings: Settings = field(default_factory=default_settings)


@dataclass
class StubAssembly:
    """Stands in for ``cli.pipelines.Assembly``: hands out one stub per command."""

    check_pipeline: StubCheck = field(default_factory=StubCheck)
    explain_pipeline: StubExplain = field(default_factory=StubExplain)
    baseline_command: StubBaseline = field(default_factory=StubBaseline)
    ctx: StubContext = field(default_factory=StubContext)

    def check(self) -> StubCheck:
        """The ``check`` pipeline this assembly would build."""
        return self.check_pipeline

    def explain(self) -> StubExplain:
        """The ``explain`` pipeline this assembly would build."""
        return self.explain_pipeline

    def baseline(self) -> StubBaseline:
        """The ``baseline`` command this assembly would build."""
        return self.baseline_command

    @property
    def targets(self) -> list[Selection | CommitRange]:
        """Everything any pipeline here was pointed at; one command runs per invocation."""
        return list(self.check_pipeline.selections) + list(self.explain_pipeline.targets)


@dataclass
class StubAssembler:
    """A replacement for ``cli.pipelines.assemble`` that records how it was called."""

    assembly: StubAssembly = field(default_factory=StubAssembly)
    error: BaseException | None = None
    overrides: list[dict[str, object]] = field(default_factory=list)
    cwds: list[Path] = field(default_factory=list)

    def __call__(self, options: Any, overrides: Mapping[str, object] | None = None) -> StubAssembly:
        """Record the settings overrides this command line produced and answer with the stub."""
        self.overrides.append(dict(overrides or {}))
        self.cwds.append(options.cwd)
        if self.error is not None:
            raise self.error
        return self.assembly

    @property
    def last_overrides(self) -> dict[str, object]:
        """The overrides of the most recent call; a command that never ran has none."""
        if not self.overrides:
            raise AssertionError("the command never assembled a run")
        return self.overrides[-1]
