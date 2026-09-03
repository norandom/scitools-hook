"""Which metrics the installed Understand really has, and for which language (task 10.1).

Requirement 5.5 says a metric Understand cannot answer for the language of an entity is
skipped for that entity and **reported once per run**, per language. Requirement 3.5 adds two
metrics Understand does not have at all -- ``CountParams`` and ``CountDeclMethodNonStub`` --
which the worker computes itself.

Both requirements fail silently when they are wrong. A metric that is quietly absent looks
exactly like a metric that is always inside its limit: the threshold never fires and the run
is green, and no output says the rule was not evaluated. So each test here asserts the
availability table in the direction the report is written -- **language -> metrics** -- and
names the languages on both sides of every claim, because a test that only checked "Python
lacks it" would pass just as well against an inverted map.

The table below was read out of the installed build; it is not documentation copied from
anywhere.
"""

from __future__ import annotations

import pytest
from contract_project import (
    FILES,
    SampleProject,
    extract,
    real_env,
    sample_project,  # noqa: F401 -- imported so the session fixture is registered here
)

from scitools_hook.models.progress import NullCommandLog
from scitools_hook.models.snapshot import ProjectSnapshot
from scitools_hook.understand.api_runner import ApiRunner
from scitools_hook.understand.catalogue import MetricCatalogue
from scitools_hook.understand.database import LANGUAGE_BY_SUFFIX

pytestmark = pytest.mark.contract

CONFIGURABLE_LANGUAGES: tuple[str, ...] = tuple(sorted(set(LANGUAGE_BY_SUFFIX.values())))
"""Every language a repository may enable, as the extension map spells them."""

COHESION = "PercentLackOfCohesion"
"""The class metric the shipped defaults name and Python does not have."""

COHESION_LANGUAGES = frozenset({"Basic", "C#", "C++", "Java", "Pascal"})
"""The languages that answer :data:`COHESION`, measured on 6.5.1204."""

STUB_ADJUSTMENT = "CountDeclPropertyAuto"
"""The metric ``CountDeclMethodNonStub`` subtracts; only one language has it."""

WITHOUT_CLASS_METRICS = frozenset({"Ada", "Assembly", "Fortran", "Jovial", "VHDL"})
"""Languages whose class scope answers no metric at all -- an empty list, not an error."""


@pytest.fixture(scope="module")
def catalogue() -> MetricCatalogue:
    """The metric lists of the installed Understand, asked once per module."""
    return MetricCatalogue(ApiRunner(real_env("upython"), NullCommandLog()))


@pytest.fixture(scope="module")
def alpha(sample_project: SampleProject) -> ProjectSnapshot:  # noqa: F811
    """A snapshot of the mixed Python/C++ sample project."""
    return extract(sample_project.db("alpha"), sample_project.root("alpha"), FILES, "before")


# --- availability, language by language -------------------------------------------


def test_the_catalogue_answers_a_metric_list_for_every_configurable_language(
    catalogue: MetricCatalogue,
) -> None:
    """Requirement 3.3 rests on this: a configured language must have *some* answer.

    Five languages have no class metrics on this build and Assembly has no routine metrics.
    That is an answer -- an empty set -- and not a failure, so a class threshold configured
    for an Ada project is dropped and reported rather than crashing the run. Naming them
    keeps a future build that starts answering from passing unnoticed.
    """
    without_class = {
        language
        for language in CONFIGURABLE_LANGUAGES
        if not catalogue.available(language, "class")
    }
    without_routine = {
        language
        for language in CONFIGURABLE_LANGUAGES
        if not catalogue.available(language, "routine")
    }

    assert without_class == WITHOUT_CLASS_METRICS
    assert without_routine == {"Assembly"}
    for language in CONFIGURABLE_LANGUAGES:
        assert catalogue.available(language, "file"), f"{language} has no file metrics"


def test_lack_of_cohesion_is_available_for_five_languages_and_not_for_python(
    catalogue: MetricCatalogue,
) -> None:
    """The metric requirement 5.5's report exists for, measured on both sides.

    ``PercentLackOfCohesion`` is in the shipped class defaults, so a Python-only repository
    must be told the rule was dropped rather than left to read a green run as compliance.
    Asserting the whole partition -- which languages have it *and* which do not -- is what
    makes this a measurement rather than a restatement of the default configuration.
    """
    with_cohesion = {
        language
        for language in CONFIGURABLE_LANGUAGES
        if COHESION in catalogue.available(language, "class")
    }

    assert with_cohesion == COHESION_LANGUAGES
    assert "Python" not in with_cohesion
    assert "C++" in with_cohesion


def test_the_parameter_count_understand_ships_is_unavailable_for_every_language(
    catalogue: MetricCatalogue,
) -> None:
    """Why ``CountParams`` is synthetic (req 3.5), stated more strongly than the design does.

    The design says Understand's native ``CountParams`` is "unset for Python". Measured, it
    is not in the routine metric list of **any** of the twelve languages a repository can
    configure -- C++ included. So the synthetic is not a Python workaround: it is the only
    source of a parameter count on this build, and a request that forgot to declare it would
    silently stop evaluating every parameter threshold in every language.
    """
    with_native = {
        language
        for language in CONFIGURABLE_LANGUAGES
        if "CountParams" in catalogue.available(language, "routine")
    }

    assert with_native == set()


def test_the_stub_adjustment_is_only_computable_for_one_language(
    catalogue: MetricCatalogue,
) -> None:
    """``CountDeclMethodNonStub = CountDeclMethod - 2 * CountDeclPropertyAuto``, measured.

    ``CountDeclPropertyAuto`` exists for C# alone, so on every other language the synthetic
    is arithmetically equal to ``CountDeclMethod``: the "excluding trivial accessors" part of
    requirement 3.5 never fires there. That is a real limit on what the metric means and it
    belongs in the record rather than in a reader's assumption.
    """
    with_adjustment = {
        language
        for language in CONFIGURABLE_LANGUAGES
        if STUB_ADJUSTMENT in catalogue.available(language, "class")
    }

    assert with_adjustment == {"C#"}


# --- what the snapshot reports for a real mixed-language project ------------------


def test_the_unavailable_report_is_keyed_by_language_and_names_the_metric(
    alpha: ProjectSnapshot,
) -> None:
    """Requirement 5.5 end to end, with the orientation pinned in both directions.

    ``unavailable`` maps a **language** to the metrics that language does not have. An
    inverted map would carry the same two strings and read plausibly, so the test also asserts
    that the C++ class in the same snapshot really did get a value for the metric Python is
    reported as lacking -- which an inverted map could not explain.
    """
    assert alpha.unavailable == {"Python": [COHESION]}

    cohesion = {
        key.longname: alpha.entities[key].metrics.get(COHESION)
        for key in alpha.entities
        if key.scope == "class"
    }
    assert cohesion["Shape"] is not None, "the C++ class must have the metric Python lacks"
    assert cohesion["core.Engine"] is None
    assert cohesion["leaf.Leaf"] is None


def test_the_synthetic_parameter_count_reaches_python_and_cpp_routines(
    alpha: ProjectSnapshot,
) -> None:
    """Requirement 3.5's ``CountParams`` on real entities of both languages.

    ``self`` and ``cls`` count -- they are declared parameters, and the synthetic counts the
    ``Parameter ~Catch`` entities a routine defines -- which is what makes the two overloads
    of ``Shape::area`` differ by exactly one.
    """
    counts = {
        (key.longname, key.parameters): alpha.entities[key].metrics.get("CountParams")
        for key in alpha.entities
        if key.scope == "routine"
    }

    assert counts[("core.Engine.run", "self,value")] == 2.0
    assert counts[("core.Engine.build", "cls")] == 1.0
    assert counts[("core.Engine.label", "")] == 0.0
    assert counts[("Shape::area", "int width")] == 1.0
    assert counts[("Shape::area", "int width,int height")] == 2.0
    assert counts[("main.main", "")] == 0.0


def test_the_synthetic_non_stub_method_count_equals_the_declared_count_here(
    alpha: ProjectSnapshot,
) -> None:
    """The consequence of the C#-only adjustment, asserted on the real classes.

    Both classes report a non-stub count equal to their declared method count, because
    neither language has the metric the adjustment subtracts. A future build that gave
    ``CountDeclPropertyAuto`` to Python would separate the two numbers and fail here, which
    is the point.
    """
    classes = {
        key.longname: alpha.entities[key].metrics for key in alpha.entities if key.scope == "class"
    }

    for name in ("core.Engine", "Shape"):
        metrics = classes[name]
        assert metrics["CountDeclMethodNonStub"] == metrics["CountDeclMethod"], name
    assert classes["core.Engine"]["CountDeclMethod"] == 4.0
    assert classes["Shape"]["CountDeclMethod"] == 4.0


def test_population_and_project_metrics_come_back_for_the_configured_thresholds(
    alpha: ProjectSnapshot,
) -> None:
    """A stats-prefixed threshold and a project threshold reach the worker as populations.

    ``AVG:CyclomaticStrict`` at routine scope and a plain project metric are the two shapes
    requirement 3.4 distinguishes, and the worker tells them apart by the prefix alone. An
    empty vector here would make every stats threshold evaluate against nothing.
    """
    assert len(alpha.populations["routine"]["CyclomaticStrict"]) == 13
    assert alpha.populations["project"]["MaxCyclomaticStrict"] == [1.0]
    assert set(alpha.languages) == {"C++", "Python"}
