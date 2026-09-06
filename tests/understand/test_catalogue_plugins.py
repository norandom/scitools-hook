"""The catalogue's second source: the metrics a plugin computes (requirements 5.1, 5.2).

Understand 8.0 has two kinds of metric and only one of them is in a kind list. Measured on
Build 1262, ``Metric.list("python function ~unknown ~unresolved")`` answers 18 built-ins and
none of them is ``CountGlobalsModified``, while ``Metric.lookup("CountGlobalsModified")``
finds it with the tags ``Target: Functions`` and ``Language: C, C++, Python, Pascal, Web``.
``Ent.metric()`` computes it either way.

Without the second source a threshold on one is refused by ``config.validate`` as a metric
Understand does not have, which is the wrong answer and one an operator cannot act on. With
it, the answer has to stay **per language and per scope**: offering a class metric to a
routine threshold, or a Java metric to a Python project, would let a rule be configured that
can never fire.

The declaration in ``config.metric_names`` supplies the candidates and the build's own tags
decide, so a 6.5 install -- whose API has no ``lookup`` at all -- offers none of them.
"""

from __future__ import annotations

from typing import Final

import pytest

from scitools_hook.config.metric_names import Scope
from scitools_hook.config.models import Limit, ProjectSettings, Settings, ThresholdSpec
from scitools_hook.config.validate import validate_settings
from scitools_hook.errors import ConfigError
from scitools_hook.understand.catalogue import MetricCatalogue, as_availability

BUILT_IN: Final[list[str]] = ["CyclomaticStrict", "CountLineCode"]
"""What the kind list answers, so the union can be told from a replacement."""

GLOBALS_TAGS: Final[dict[str, list[str]]] = {
    "targets": ["Functions"],
    "languages": ["C", "C++", "Python", "Pascal", "Web"],
}
"""``Metric.lookup("CountGlobalsUsed").tags()`` on Build 1262, recorded verbatim."""

COUPLED_TAGS: Final[dict[str, list[str]]] = {
    "targets": ["Classes"],
    "languages": ["Basic", "C#", "Java", "Pascal", "Python"],
}
"""``CountClassCoupledModified``: a class metric, and no C or C++ among its languages."""

CORE_TAGS: Final[dict[str, list[str]]] = {
    "targets": ["Architectures", "Project"],
    "languages": ["Any"],
}
"""``CorePercentage``: two targets and no language restriction at all."""


class Answers:
    """A runner that answers ``catalogue`` for whatever kind and ids it is asked about.

    Local rather than :class:`fakes.api.FakeApiRunner`, which replays one fixed document: the
    catalogue asks two questions per kind now, and what matters here is that each gets the
    answer it asked for rather than the same one twice.
    """

    def __init__(self, lookup: dict[str, object] | None = None) -> None:
        self.lookup = dict(lookup or {})
        self.asked: list[dict[str, object]] = []

    def run(self, op: str, request: dict[str, object]) -> dict[str, object]:
        """One ``catalogue`` answer: the built-ins for each kind, the tags for each id."""
        assert op == "catalogue", op
        self.asked.append(dict(request))
        kinds = request.get("kinds") or []
        wanted = request.get("lookup") or []
        return {
            "metrics": {str(kind): list(BUILT_IN) for kind in kinds},
            "descriptions": {},
            "lookup": {str(name): self.lookup.get(str(name)) for name in wanted},
        }


def a_catalogue(lookup: dict[str, object] | None = None) -> MetricCatalogue:
    """A catalogue over a build whose ``Metric.lookup`` answers ``lookup``."""
    return MetricCatalogue(Answers(lookup))  # type: ignore[arg-type]


def offered(catalogue: MetricCatalogue, language: str, scope: Scope) -> set[str]:
    """What the catalogue answers, with the built-ins taken out so the union is visible."""
    return catalogue.available(language, scope) - set(BUILT_IN)


# --- the union ---------------------------------------------------------------------------


def test_a_plugin_metric_the_build_knows_is_offered_beside_the_built_ins() -> None:
    """The whole point: a threshold on it is a threshold Understand can answer."""
    found = a_catalogue({"CountGlobalsUsed": GLOBALS_TAGS}).available("Python", "routine")

    assert "CountGlobalsUsed" in found
    assert set(BUILT_IN) <= found, "the kind's own metrics are unioned with, not replaced by"


def test_a_build_that_knows_none_of_them_offers_none() -> None:
    """A 6.5 API has no ``Metric.lookup``, so the worker answers ``None`` for every id."""
    catalogue = a_catalogue({"CountGlobalsUsed": None})

    assert offered(catalogue, "Python", "routine") == set()


def test_a_build_that_answers_no_lookup_key_at_all_offers_none() -> None:
    """The 7.x shape: the operation runs and the key is simply absent."""
    assert offered(a_catalogue(), "Python", "routine") == set()


# --- per scope and per language ------------------------------------------------------------


def test_a_class_metric_is_not_offered_to_a_routine_threshold() -> None:
    """``Target: Classes`` decides; a rule configured on the wrong scope can never fire."""
    catalogue = a_catalogue({"CountClassCoupledModified": COUPLED_TAGS})

    assert offered(catalogue, "Python", "routine") == set()
    assert offered(catalogue, "Python", "class") == {"CountClassCoupledModified"}


def test_a_metric_whose_languages_exclude_this_one_is_not_offered() -> None:
    """``CountClassCoupledModified`` names no C among its languages (measured on 1262)."""
    catalogue = a_catalogue({"CountClassCoupledModified": COUPLED_TAGS})

    assert offered(catalogue, "C++", "class") == set()


def test_a_c_plus_plus_project_matches_understands_own_c_tag() -> None:
    """Understand tags C and C++ separately and the Gate names the pair ``C++``."""
    catalogue = a_catalogue({"CountGlobalsUsed": GLOBALS_TAGS})

    assert "CountGlobalsUsed" in offered(catalogue, "C++", "routine")


def test_any_is_understands_word_for_no_language_restriction() -> None:
    catalogue = a_catalogue({"CorePercentage": CORE_TAGS})

    assert "CorePercentage" in offered(catalogue, "Fortran", "project")
    assert "CorePercentage" in offered(catalogue, "Python", "arch")


def test_only_the_candidates_of_this_scope_are_looked_up() -> None:
    """A lookup is a subprocess round trip, so asking about every declared id would waste one."""
    catalogue = a_catalogue({"CountGlobalsUsed": GLOBALS_TAGS})
    catalogue.available("Python", "class")

    runner = catalogue.runner
    asked = [request["lookup"] for request in runner.asked if request.get("lookup")]
    assert asked, "the class scope has declared candidates, so one lookup is expected"
    assert "CountGlobalsUsed" not in asked[0], "that one targets Functions"


# --- what a configuration made of them does (requirements 3.8, 5.5) -----------------------


def asking(language: str, scope: Scope, metric: str) -> Settings:
    """A configuration with one plugin threshold, over one language."""
    return Settings(
        project=ProjectSettings(languages=[language]),
        thresholds=[ThresholdSpec(scope=scope, metric=metric, limit=Limit(max=5))],
    )


def test_a_plugin_threshold_the_build_offers_survives_validation() -> None:
    """Without the second source this is a ``ConfigError`` about an unknown metric."""
    catalogue = a_catalogue({"CountGlobalsUsed": GLOBALS_TAGS})

    report = validate_settings(
        asking("Python", "routine", "CountGlobalsUsed"), as_availability(catalogue)
    )

    assert [spec.ref.metric for spec in report.thresholds] == ["CountGlobalsUsed"]
    assert report.unavailable == {}


def test_a_plugin_threshold_no_configured_language_has_is_refused_by_name() -> None:
    """Treated exactly like any other metric a language cannot compute (requirement 3.8).

    No plugin metric is a shipped default (requirement 5.4), so this is always a threshold
    somebody wrote, and a written threshold that can never fire is a configuration error
    rather than something to drop quietly. The message names the metric, the language and the
    scope, which are the three things the operator has to choose between.
    """
    catalogue = a_catalogue({"CountClassCoupledModified": COUPLED_TAGS})

    with pytest.raises(ConfigError) as caught:
        validate_settings(
            asking("C++", "class", "CountClassCoupledModified"), as_availability(catalogue)
        )

    said = str(caught.value)
    assert "CountClassCoupledModified" in said
    assert "C++" in said
    assert "class" in said
