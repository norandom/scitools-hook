"""The ``recommend`` subcommand: its option grammar, its refusals, and what it never does.

The doubles are declared here rather than in ``fakes/cli.py``: that module is shared, and a
stub added to it for one command is a merge conflict waiting for another task. This one is
small enough to keep beside the assertions that read it.

Three properties are the command's own, and nothing below the CLI can hold them:

* the target is validated **before** any Understand work starts, so a typo costs no analysis;
* the report goes to standard output and no configuration file is touched, ever;
* ``--help`` distinguishes this command from ``baseline``, which is the confusion the whole
  feature can cause.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from scitools_hook.analysis.recommend import (
    Candidate,
    Distribution,
    MetricAdvice,
    Offender,
    Recommendation,
)
from scitools_hook.cli import app as app_module
from scitools_hook.cli import pipelines
from scitools_hook.cli import recommend as recommend_module
from scitools_hook.exit_codes import ExitCode

SHAPE = Distribution(count=100, p50=1, p90=5, p95=7, p99=12, maximum=45)

RAISED = MetricAdvice(
    rule="file.CountDeclClass",
    scope="file",
    metric="CountDeclClass",
    configured=3.0,
    verdict="raise",
    proposed=8.0,
    distribution=SHAPE,
    candidates=(
        Candidate(limit=3.0, outside=124, share_outside=0.164, configured=True),
        Candidate(limit=8.0, outside=24, share_outside=0.032, proposed=True),
    ),
    offenders=(Offender(value=48.0, path="src/model.py", longname="src/model.py"),),
    tail_ratio=45 / 7,
    tail_dominated=False,
)

ANSWER = Recommendation(counts={"file": 100}, advice=(RAISED,), skipped=())


@dataclass
class StubRecommend:
    """Stands in for ``RecommendCmd``: records the target it was handed."""

    targets: list[float] = field(default_factory=list)
    error: BaseException | None = None

    def run(self, target: float) -> Recommendation:
        """Answer with the fixed recommendation, having recorded what was asked."""
        self.targets.append(target)
        if self.error is not None:
            raise self.error
        return ANSWER


@dataclass
class StubAssembly:
    """The one factory this command uses."""

    command: StubRecommend = field(default_factory=StubRecommend)

    def recommend(self) -> StubRecommend:
        """The ``recommend`` command this assembly would build."""
        return self.command


@dataclass
class StubAssembler:
    """A replacement for ``cli.pipelines.assemble`` that records that it was reached."""

    assembly: StubAssembly = field(default_factory=StubAssembly)
    calls: int = 0

    def __call__(self, options: Any, overrides: Any = None) -> StubAssembly:
        """Count the assembly, so a test can prove one did **not** happen."""
        self.calls += 1
        return self.assembly


@pytest.fixture
def assembler(monkeypatch: pytest.MonkeyPatch) -> StubAssembler:
    """Replace the real assembly, so no git repository and no Understand are needed."""
    stub = StubAssembler()
    monkeypatch.setattr(pipelines, "assemble", stub)
    return stub


def run(*args: str) -> Any:
    """Invoke the real application with ``args``."""
    return CliRunner().invoke(app_module.app, list(args))


# --- the help (req 12.1) -----------------------------------------------------------


def test_the_help_says_this_is_not_a_baseline() -> None:
    """The two commands both measure the whole project; ``--help`` is where they are told apart."""
    result = run("recommend", "--help")

    assert result.exit_code == 0
    assert "Not a baseline" in result.stdout
    assert "WHERE TO AIM" in result.stdout
    assert "Nothing is written" in result.stdout


def test_the_baseline_help_points_the_other_way() -> None:
    """The contrast is written on both sides, because an operator reads only one of them."""
    result = run("baseline", "--help")

    assert result.exit_code == 0
    assert "WHERE YOU ARE" in result.stdout
    assert "not `recommend`" in result.stdout


def test_the_help_documents_the_target_and_its_shipped_default() -> None:
    """The number an operator sees, asserted as the literal it is."""
    result = run("recommend", "--help")

    assert "--target" in result.stdout
    assert "0.95" in result.stdout
    assert recommend_module.DEFAULT_TARGET == 0.95


def test_recommend_is_listed_next_to_baseline() -> None:
    """Registration order is help order; the pair has to read as a pair."""
    result = run("--help")

    assert result.stdout.index("baseline") < result.stdout.index("recommend")


# --- the target --------------------------------------------------------------------


def test_the_default_target_reaches_the_command(assembler: StubAssembler) -> None:
    result = run("recommend")

    assert result.exit_code == int(ExitCode.OK)
    assert assembler.assembly.command.targets == [0.95]


def test_an_explicit_target_reaches_the_command(assembler: StubAssembler) -> None:
    result = run("recommend", "--target", "0.99")

    assert result.exit_code == int(ExitCode.OK)
    assert assembler.assembly.command.targets == [0.99]


@pytest.mark.parametrize("bad", ["0", "0.0", "-0.5", "1.5", "2"])
def test_a_target_that_is_not_a_share_is_refused_before_anything_is_analysed(
    assembler: StubAssembler, bad: str
) -> None:
    """A target of 0 makes every limit fit and one above 1 makes none of them fit.

    Both produce a confident, entirely wrong report rather than an error, so the refusal has to
    exist -- and it has to happen before a database is opened, which is what the assembly count
    asserts. Without that second assertion the test would pass on an implementation that
    validated *after* a five-minute analysis.
    """
    result = run("recommend", "--target", bad)

    assert result.exit_code == int(ExitCode.CONFIG_ERROR)
    assert assembler.calls == 0
    assert assembler.assembly.command.targets == []


def test_a_target_of_exactly_one_is_allowed(assembler: StubAssembler) -> None:
    """ "Every entity inside" is a coherent thing to ask for, even if a strict one."""
    result = run("recommend", "--target", "1.0")

    assert result.exit_code == int(ExitCode.OK)
    assert assembler.assembly.command.targets == [1.0]


# --- what it prints, and what it never writes ---------------------------------------


def test_the_report_goes_to_standard_output(assembler: StubAssembler) -> None:
    result = run("recommend")

    assert result.exit_code == int(ExitCode.OK)
    assert "file.CountDeclClass  raise 3 -> 8" in result.stdout
    assert "CountDeclClass = 8" in result.stdout


def test_toml_narrows_the_output_to_the_lines_to_paste(assembler: StubAssembler) -> None:
    result = run("recommend", "--toml")

    assert result.exit_code == int(ExitCode.OK)
    assert "CountDeclClass = 8" in result.stdout
    assert "raise 3 -> 8" not in result.stdout


def test_the_report_can_be_written_to_a_named_file(
    assembler: StubAssembler, tmp_path: Path
) -> None:
    """``--output`` is a destination for the *report*, the way ``check --output`` is."""
    target = tmp_path / "recommendation.txt"

    result = run("recommend", "--output", str(target))

    assert result.exit_code == int(ExitCode.OK)
    assert "CountDeclClass = 8" in target.read_text(encoding="utf-8")
    assert result.stdout == ""


def test_the_command_writes_no_configuration_file(
    assembler: StubAssembler, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The property the docstring claims, asserted against the filesystem rather than read.

    The working directory is empty before the run and must be empty after it: a command that
    quietly wrote ``scitools-hook.toml`` would pass every assertion above.
    """
    monkeypatch.chdir(tmp_path)

    result = run("recommend")

    assert result.exit_code == int(ExitCode.OK)
    assert list(tmp_path.iterdir()) == []


def test_a_recommendation_never_fails_the_run(assembler: StubAssembler) -> None:
    """Requirement 7.9 belongs to ``check``: proposing a limit is not a violation of one."""
    result = run("recommend")

    assert result.exit_code == int(ExitCode.OK)
