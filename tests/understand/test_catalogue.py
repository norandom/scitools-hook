"""The metric catalogue: which metrics Understand has, per language and scope (5.5, 3.8).

``config.validate`` refuses a threshold whose metric exists for none of the configured
languages, and this is the object that answers it, so a wrong answer here is a configuration
error the operator cannot act on. Two measurements on the licensed machine (2026-08-30) shape
the whole module and are pinned below:

* **The language prefixes every alternative of a kind string, not the string.**
  ``Metric.list("python function ~unknown ~unresolved, method …, classmethod …")`` answers 49
  metrics — the *union across every language*, ``CountLineBlankPhp`` included — because only
  the first alternative carries the language. Prefixed one by one it answers 18, the ones
  Python routines really have.
* **``c++`` is not a kind-string language; ``c`` is.** ``Metric.list("c++ file …")`` answers
  nothing at all while ``Metric.list("c file …")`` answers 42, and Understand's own kind long
  names for a C++ entity read ``C Class Type``. The name in configuration and in
  ``Ent.language()`` is ``C++``, so exactly one alias is needed — and without it a C++-only
  repository has *no* available metric and every threshold it configures is rejected.

Two scopes have no entity kind string of their own and were measured separately:
``Metric.list("project")`` answers nothing, while the bare language answers the full
language-wide list the project metrics come from; ``architecture`` answers 7 metrics and
must not be language-prefixed (``python architecture`` answers nothing).
"""

from __future__ import annotations

from typing import Final

import pytest
from conftest import understand_probe
from fakes.api import FakeApiRunner
from test_api_runner import real_env

from scitools_hook.config.defaults import default_settings
from scitools_hook.config.metric_names import SYNTHETIC_METRICS
from scitools_hook.config.models import Limit, ProjectSettings, Settings, ThresholdSpec
from scitools_hook.config.validate import MetricAvailability, validate_settings
from scitools_hook.errors import AnalysisFailedError, ConfigError
from scitools_hook.models.progress import NullCommandLog
from scitools_hook.understand.api_runner import ApiRunner
from scitools_hook.understand.catalogue import ARCH_KIND, MetricCatalogue

PYTHON_ROUTINE_KIND: Final = (
    "python function ~unknown ~unresolved, python method ~unknown ~unresolved, "
    "python procedure ~unknown ~unresolved, python routine ~unknown ~unresolved, "
    "python classmethod ~unknown ~unresolved"
)
"""``SCOPE_KINDS['routine']`` with the language on every alternative, as measured."""

PYTHON_ROUTINE_METRICS: Final[list[str]] = [
    "CountLine",
    "CountLineBlank",
    "CountLineCode",
    "CountPath",
    "CountStmt",
    "Cyclomatic",
    "CyclomaticModified",
    "CyclomaticStrict",
    "Essential",
    "MaxNesting",
    "RatioCommentToCode",
]
"""Part of the real answer for that kind string; ``CountLineBlankPhp`` is deliberately absent."""


def a_catalogue(
    metrics: dict[str, list[str]], descriptions: dict[str, str] | None = None
) -> MetricCatalogue:
    """A catalogue whose runner answers ``catalogue`` from a recorded metric table."""
    answer: dict[str, object] = {
        "metrics": metrics,
        "descriptions": descriptions if descriptions is not None else {},
    }
    return MetricCatalogue(FakeApiRunner(answers={"catalogue": answer}))


def runner_of(catalogue: MetricCatalogue) -> FakeApiRunner:
    """The fake runner behind a catalogue built by :func:`a_catalogue`."""
    runner = catalogue.runner
    assert isinstance(runner, FakeApiRunner)
    return runner


# --- the kind strings ------------------------------------------------------------


def test_the_language_is_repeated_on_every_alternative_of_the_kind_string() -> None:
    # Prefixing the string once leaves every alternative but the first language-free, and
    # Understand then answers with the union over all languages — which would make req 3.8
    # accept a metric that does not exist for the configured language.
    catalogue = a_catalogue({PYTHON_ROUTINE_KIND: PYTHON_ROUTINE_METRICS})

    found = catalogue.available("python", "routine")

    assert "CyclomaticStrict" in found
    assert runner_of(catalogue).request_for("catalogue")["kinds"] == [PYTHON_ROUTINE_KIND]


def test_the_language_name_is_matched_whatever_its_case() -> None:
    # `Ent.language()` answers `Python`, configuration says `Python`, kind strings are
    # lower-case; Understand itself matches case-insensitively (measured).
    catalogue = a_catalogue({PYTHON_ROUTINE_KIND: PYTHON_ROUTINE_METRICS})

    assert catalogue.available("Python", "routine") == set(PYTHON_ROUTINE_METRICS)


def test_cpp_asks_for_the_c_kind_string_understand_actually_has() -> None:
    kind = "c file ~unknown ~unresolved"
    catalogue = a_catalogue({kind: ["CountLineCode", "PercentLackOfCohesion"]})

    assert catalogue.available("C++", "file") == {"CountLineCode", "PercentLackOfCohesion"}
    assert runner_of(catalogue).request_for("catalogue")["kinds"] == [kind]


def test_the_project_scope_asks_the_language_itself() -> None:
    # There is no `project` kind (measured: zero metrics); the project-level metrics are the
    # language-wide list, which is where `MaxCyclomaticStrict` and `MaxNesting` live.
    catalogue = a_catalogue({"python": ["MaxCyclomaticStrict", "MaxNesting", "CountDeclFile"]})

    assert "MaxCyclomaticStrict" in catalogue.available("Python", "project")
    assert runner_of(catalogue).request_for("catalogue")["kinds"] == ["python"]


def test_the_architecture_scope_is_never_language_prefixed() -> None:
    # Measured: `architecture` answers 7 metrics, `python architecture` answers none.
    catalogue = a_catalogue({ARCH_KIND: ["CountLineCode", "RatioCommentToCode"]})

    assert catalogue.available("Python", "arch") == {"CountLineCode", "RatioCommentToCode"}
    assert runner_of(catalogue).request_for("catalogue")["kinds"] == [ARCH_KIND]


def test_a_language_understand_does_not_know_has_no_metrics_rather_than_an_error() -> None:
    catalogue = a_catalogue({"cobol file ~unknown ~unresolved": []})

    assert catalogue.available("cobol", "file") == set()


def test_each_language_and_scope_is_asked_about_once() -> None:
    # `config.validate` asks per threshold; a default configuration would otherwise start a
    # subprocess for every one of them.
    catalogue = a_catalogue({PYTHON_ROUTINE_KIND: PYTHON_ROUTINE_METRICS})

    catalogue.available("Python", "routine")
    catalogue.available("python", "routine")

    assert runner_of(catalogue).ops == ["catalogue"]


def test_an_answer_without_the_kind_that_was_asked_about_is_a_broken_contract() -> None:
    catalogue = a_catalogue({"something else": []})

    with pytest.raises(AnalysisFailedError):
        catalogue.available("Python", "routine")


# --- descriptions ----------------------------------------------------------------


def test_a_metric_is_described_by_understands_own_text() -> None:
    text = "Maximum nesting level of control constructs"
    catalogue = a_catalogue({}, {"MaxNesting": text})

    assert catalogue.describe("MaxNesting") == text
    assert runner_of(catalogue).request_for("catalogue")["describe"] == ["MaxNesting"]


def test_a_synthetic_metric_is_described_by_the_gate_whatever_understand_says() -> None:
    """The number is the gate's own count, so the description is the gate's own too.

    6.5 answered ``""`` for ``CountParams``; 8.0 ships a HIS plugin metric of that name and
    describes it, while the value the snapshot reports is still computed here. Neither
    answer is asked for: a synthetic metric never reaches Understand.
    """
    catalogue = a_catalogue({}, {"CountParams": "<p>The number of parameters</p>"})

    assert catalogue.describe("CountParams") == SYNTHETIC_METRICS["CountParams"].description


def test_a_metric_nobody_can_describe_is_empty_rather_than_absent() -> None:
    catalogue = a_catalogue({}, {"NoSuchMetric": ""})

    assert catalogue.describe("NoSuchMetric") == ""


def test_each_description_is_asked_for_once() -> None:
    catalogue = a_catalogue({}, {"MaxNesting": "text"})

    catalogue.describe("MaxNesting")
    catalogue.describe("MaxNesting")

    assert runner_of(catalogue).ops == ["catalogue"]


# --- the configuration protocol --------------------------------------------------


def test_the_catalogue_is_the_metric_availability_configuration_asks_for() -> None:
    # `config` must never import the Understand adapter, so the two meet through a Protocol;
    # the annotation is what makes mypy prove the shapes agree.
    availability: MetricAvailability = a_catalogue({})

    assert isinstance(availability, MetricAvailability)


def one_threshold(metric: str) -> Settings:
    """A Python-only configuration with one routine threshold on ``metric``."""
    return Settings(
        project=ProjectSettings(languages=["Python"]),
        thresholds=[ThresholdSpec(scope="routine", metric=metric, limit=Limit(max=10))],
    )


def test_a_metric_the_catalogue_knows_passes_configuration_validation() -> None:
    # `routine.CyclomaticStrict` is a shipped default, so a catalogue answer that stopped
    # matching would drop it rather than raise: assert it is evaluated, not merely accepted.
    catalogue = a_catalogue({PYTHON_ROUTINE_KIND: PYTHON_ROUTINE_METRICS})

    report = validate_settings(one_threshold("CyclomaticStrict"), catalogue)

    assert {spec.rule for spec in report.thresholds} == {"routine.CyclomaticStrict"}
    assert report.dropped == ()


def test_a_metric_the_catalogue_does_not_know_is_a_configuration_error() -> None:
    catalogue = a_catalogue({PYTHON_ROUTINE_KIND: PYTHON_ROUTINE_METRICS})

    with pytest.raises(ConfigError) as failure:
        validate_settings(one_threshold("NoSuchMetric"), catalogue)

    assert failure.value.key == "thresholds.routine.NoSuchMetric"


# --- against the real Understand -------------------------------------------------


def real_catalogue() -> MetricCatalogue:
    """A catalogue wired to the installed Understand through a real ``ApiRunner``."""
    return MetricCatalogue(ApiRunner(real_env("upython"), NullCommandLog()))


@pytest.mark.contract
def test_the_real_catalogue_answers_per_language_and_scope() -> None:
    catalogue = real_catalogue()

    routines = catalogue.available("Python", "routine")

    assert {"CyclomaticStrict", "CountLineCode", "MaxNesting", "CountStmt"} <= routines
    # The tell of a kind string whose language reached only its first alternative.
    assert "CountLineBlankPhp" not in routines
    assert "CountDeclMethod" not in routines


@pytest.mark.contract
def test_the_real_catalogue_answers_for_cpp_under_the_name_understand_uses() -> None:
    catalogue = real_catalogue()

    assert catalogue.available("C++", "file")
    assert "PercentLackOfCohesion" in catalogue.available("C++", "class")
    assert "PercentLackOfCohesion" not in catalogue.available("Python", "class")


@pytest.mark.contract
def test_the_real_catalogue_answers_for_the_project_and_architecture_scopes() -> None:
    catalogue = real_catalogue()

    assert {"MaxCyclomaticStrict", "MaxNesting"} <= catalogue.available("Python", "project")
    assert "RatioCommentToCode" in catalogue.available("Python", "arch")


@pytest.mark.contract
def test_the_built_in_defaults_pass_validation_against_the_real_catalogue() -> None:
    """Requirement 3.8 against the shipped configuration: no default may be rejected — and
    none may be quietly dropped either, since between them Python and C++ have every one."""
    settings = default_settings()
    settings.project.languages = ["Python", "C++"]

    report = validate_settings(settings, real_catalogue())

    assert report.dropped == ()
    assert len(report.thresholds) == len(settings.thresholds)


@pytest.mark.contract
def test_the_built_in_defaults_run_on_a_python_only_repository() -> None:
    """Requirement 3.1 against the real install: the shipped defaults must not refuse to start
    on this very repository, which is Python-only. ``PercentLackOfCohesion`` is a C++/Java
    class metric Understand does not compute for Python, so it is dropped and reported (5.5),
    not fatal — the two-language test above passes even when this one fails."""
    settings = default_settings()
    settings.project.languages = ["Python"]

    report = validate_settings(settings, real_catalogue())

    dropped = {spec.rule for spec in report.dropped}
    assert "class.PercentLackOfCohesion" in dropped
    assert "PercentLackOfCohesion" in report.unavailable["Python"]
    assert "routine.CyclomaticStrict" in {spec.rule for spec in report.thresholds}
    assert dropped.isdisjoint({spec.rule for spec in report.thresholds})


@pytest.mark.contract
def test_the_real_catalogue_describes_a_metric_and_a_synthetic_one() -> None:
    catalogue = real_catalogue()

    assert "Cyclomatic" in catalogue.describe("CyclomaticStrict")
    assert catalogue.describe("CountParams") == SYNTHETIC_METRICS["CountParams"].description


@pytest.mark.contract
def test_the_licensed_machine_is_the_one_these_transcripts_came_from() -> None:
    # Guards the recorded lists above: if the installed build stopped answering this way the
    # unit tests would still pass while testing a contract that no longer exists.
    assert understand_probe().usable
    assert set(PYTHON_ROUTINE_METRICS) <= real_catalogue().available("python", "routine")
