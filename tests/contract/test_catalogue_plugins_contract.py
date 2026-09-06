"""Which plugin metrics the installed build really offers, per language (5.1, 5.2).

The catalogue's second source is the one that cannot be checked without the build: the
declaration in ``config.metric_names`` names candidates, and ``Metric.lookup`` tags decide.
Get it wrong in one direction and a threshold an operator can write is refused as unknown;
get it wrong in the other and a rule is accepted that can never fire, which is the silent kind
of wrong.

Each assertion below names a metric **and** a language on both sides, because a test that only
checked "Python offers ``CountGlobalsModified``" would pass equally well against a catalogue
that offered everything to everyone.

Measured on Build 1262 while writing task 6.2 and asserted here against whatever is installed.
"""

from __future__ import annotations

import pytest
from contract_project import real_env

from scitools_hook.models.progress import NullCommandLog
from scitools_hook.understand.api_runner import ApiRunner
from scitools_hook.understand.catalogue import MetricCatalogue

pytestmark = pytest.mark.contract


def a_catalogue() -> MetricCatalogue:
    """The catalogue over the installed build, reached the way a run reaches it."""
    return MetricCatalogue(ApiRunner(real_env("upython"), NullCommandLog()))


def test_contract_a_python_routine_is_offered_the_globals_metrics() -> None:
    """``Target: Functions``, ``Language: C, C++, Python, Pascal, Web`` (measured on 1262)."""
    offered = a_catalogue().available("Python", "routine")

    assert {"CountGlobalsModified", "CountGlobalsSet", "CountGlobalsUsed"} <= offered


def test_contract_a_python_routine_is_not_offered_the_c_only_metric() -> None:
    """``CognitiveComplexity`` is tagged ``Language: C, C++`` and nothing else.

    The pair of assertions is the point: a catalogue that had simply stopped filtering by
    language would pass the first line of the previous test and fail this one.
    """
    catalogue = a_catalogue()

    assert "CognitiveComplexity" not in catalogue.available("Python", "routine")
    assert "CognitiveComplexity" in catalogue.available("C++", "routine")


def test_contract_a_class_metric_is_not_offered_to_a_routine() -> None:
    """``CountClassCoupledModified`` is tagged ``Target: Classes``."""
    catalogue = a_catalogue()

    assert "CountClassCoupledModified" not in catalogue.available("Python", "routine")
    assert "CountClassCoupledModified" in catalogue.available("Python", "class")


def test_contract_the_built_in_metrics_are_still_there_beside_them() -> None:
    """A union, not a replacement: every built-in threshold has to keep working."""
    offered = a_catalogue().available("Python", "routine")

    assert {"CyclomaticStrict", "CountLineCode", "MaxNesting"} <= offered
