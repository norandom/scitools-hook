"""Which metrics Understand computes, per language and scope (req 5.5, 3.8).

``config.validate`` refuses a threshold whose metric exists for none of the configured
languages, and it asks this object — through the ``MetricAvailability`` protocol declared
there, so that ``config`` never imports the Understand adapter. A wrong answer here is a
configuration error the operator cannot act on, so every kind string this module composes was
measured against the installed Understand (6.5.1204, 2026-08-30):

* **The language prefixes every alternative of a kind string.** ``SCOPE_KINDS['routine']``
  lists five alternatives; prefixed as one string, ``Metric.list`` answers 49 metrics — the
  union across every language, ``CountLineBlankPhp`` and ``CountLineBlankJavascript``
  included — because only the first alternative carries the language. Prefixed one by one it
  answers the 18 metrics a Python routine really has.
* **``c++`` is not a kind-string language.** ``Metric.list("c++ file …")`` answers nothing at
  all, ``Metric.list("c file …")`` answers 42, and Understand's kind long names for C++
  entities read ``C Class Type``. ``Ent.language()`` says ``C++``, and so does the
  ``Languages`` block of ``und list settings <db>`` — there is no ``und -languages``, which
  exits 1 with "No valid command found" (measured). So exactly one alias closes the gap, and
  without it a C++ repository has no available metric and every threshold it configures is
  rejected. Everything else measured (``Python``, ``Java``, ``C#``, ``Ada``, ``Web``,
  ``Fortran``, ``Pascal``, ``Jovial``, ``VHDL``, ``Assembly``, ``Basic``) answers under its
  own name, case-insensitively; a JavaScript file is a ``Web`` entity and ``db.language()``
  says ``Web``, so no second alias is needed.
* **Two scopes have no entity kind of their own.** ``Metric.list("project")`` answers nothing
  — the project-level metrics (``MaxCyclomaticStrict``, ``MaxNesting``) are in the *bare*
  language list, which is a superset of the three element-scope lists. ``architecture``
  answers 7 metrics and must not be language-prefixed: ``python architecture`` answers none.

Both answers are cached per instance: a default configuration asks about a dozen thresholds
and each question is otherwise a subprocess.
"""

from __future__ import annotations

from typing import Final

from scitools_hook.config.metric_names import (
    PLUGIN_METRICS,
    SCOPE_KINDS,
    SYNTHETIC_METRICS,
    Scope,
)
from scitools_hook.config.validate import MetricAvailability
from scitools_hook.errors import AnalysisFailedError
from scitools_hook.understand.api_runner import ApiRunner

ARCH_KIND: Final = "architecture"
"""The kind string of the architecture metrics; it carries no language (measured)."""

LANGUAGE_KINDS: Final[dict[str, str]] = {"c++": "c"}
"""Configured language (lower case) -> the language token Understand's kind strings use."""

PLUGIN_TARGETS: Final[dict[Scope, str]] = {
    "routine": "Functions",
    "class": "Classes",
    "file": "Files",
    "arch": "Architectures",
    "project": "Project",
}
"""The Gate's scope -> the word a plugin metric's ``Target:`` tag uses (measured on 1262)."""

ANY_LANGUAGE: Final = "any"
"""Understand's word, in a ``Language:`` tag, for a metric with no language restriction."""


class MetricCatalogue:
    """The metric lists of the installed Understand, per language and scope.

    Satisfies the ``config.validate.MetricAvailability`` protocol; :func:`as_availability`
    is where mypy proves it.
    """

    def __init__(self, runner: ApiRunner):
        self.runner = runner
        self._metrics: dict[str, set[str]] = {}
        self._descriptions: dict[str, str] = {}
        self._tags: dict[str, dict[str, list[str]] | None] = {}

    def available(self, language: str, scope: Scope) -> set[str]:
        """The metric identifiers Understand computes for ``language`` at ``scope`` (5.1, 5.2).

        Two sources, unioned, because 8.0 has two. ``Metric.list`` answers the built-ins of a
        kind string; the metrics a ``.upy`` plugin computes are in **no** kind list at all
        (measured on Build 1262: the Python routine kind answers 18 metrics and none of them is
        ``CountGlobalsModified``), and are found by ``Metric.lookup`` with the tags that say
        which targets and languages they apply to.

        Without the second source a threshold on a plugin metric is refused by
        ``config.validate`` as a metric Understand does not have, which is the wrong answer:
        ``Ent.metric()`` computes it perfectly well.
        """
        kind = kind_string(language, scope)
        found = self._metrics.get(kind)
        if found is None:
            found = self._ask(kind) | self._plugins(language, scope)
            self._metrics[kind] = found
        return found

    def _plugins(self, language: str, scope: Scope) -> set[str]:
        """The declared plugin metrics whose own tags name this language and this scope.

        The declaration in ``config.metric_names`` supplies the *candidates* -- it is what
        lets a threshold be spelled at all -- and the build's own tags decide. A build that
        ships none of them answers nothing here and every plugin threshold is refused, which
        is what a 6.5 install should do.

        ``Any`` is Understand's word for no language restriction. ``C++`` matches a ``C`` tag
        as well as a ``C++`` one, for the reason :data:`LANGUAGE_KINDS` records: Understand
        tags the two separately and the Gate names the pair ``C++``.
        """
        candidates = sorted(
            metric for metric, declared in PLUGIN_METRICS.items() if scope in declared.scopes
        )
        if not candidates:
            return set()
        target = PLUGIN_TARGETS[scope]
        names = {language.casefold(), language_token(language), ANY_LANGUAGE}
        return {
            metric
            for metric, tags in self._lookup(candidates).items()
            if target in tags.get("targets", ())
            and names & {word.casefold() for word in tags.get("languages", ())}
        }

    def _lookup(self, metrics: list[str]) -> dict[str, dict[str, list[str]]]:
        """``Metric.lookup`` tags for each id, cached; an id this build lacks is absent.

        ``None`` from the worker covers both a 7.x API with no ``lookup`` at all and an id
        this build does not know, and neither is a metric that can be offered, so both are
        dropped here rather than distinguished.
        """
        unknown = [metric for metric in metrics if metric not in self._tags]
        if unknown:
            answer = self.runner.run("catalogue", {"kinds": [], "lookup": unknown})
            found = answer.get("lookup")
            tagged = found if isinstance(found, dict) else {}
            for metric in unknown:
                entry = tagged.get(metric)
                self._tags[metric] = entry if isinstance(entry, dict) else None
        return {metric: tags for metric in metrics if (tags := self._tags.get(metric)) is not None}

    def describe(self, metric: str) -> str:
        """Understand's description of ``metric``, or the Gate's own for a synthetic one."""
        known = self._descriptions.get(metric)
        if known is None:
            known = self._read(metric)
            self._descriptions[metric] = known
        return known

    def _ask(self, kind: str) -> set[str]:
        """The metrics of one kind string; an unknown kind has none, which is an answer."""
        answer = self.runner.run("catalogue", {"kinds": [kind]})
        metrics = answer.get("metrics")
        if not isinstance(metrics, dict) or kind not in metrics:
            raise _unusable(f"no metric list for {kind!r}", str(metrics)[:200])
        return {str(name) for name in _as_list(metrics[kind], kind)}

    def _read(self, metric: str) -> str:
        """One description; a metric the Gate computes is described by the Gate.

        The synthetic description wins even when Understand has one of its own: 8.0 ships
        ``CountParams`` as a HIS plugin metric and describes it ("The number of parameters
        ... PARAM metric"), while the number the gate reports is still its own count of
        ``Parameter ~Catch`` entities, because the plugin metric is unset for Python
        (measured on 8.0.1262). A description of a value nobody reads would mislead.
        """
        synthetic = SYNTHETIC_METRICS.get(metric)
        if synthetic is not None:
            return synthetic.description
        answer = self.runner.run("catalogue", {"kinds": [], "describe": [metric]})
        described = answer.get("descriptions")
        if not isinstance(described, dict) or metric not in described:
            raise _unusable(f"no description for {metric!r}", str(described)[:200])
        return str(described[metric])


def as_availability(catalogue: MetricCatalogue) -> MetricAvailability:
    """The catalogue seen as the protocol ``config.validate`` asks for.

    The annotation is the whole point: mypy checks the assignment here, so a signature that
    drifts from ``MetricAvailability.available`` fails the type check instead of failing at
    run time inside a configuration validation the operator cannot debug. The dependency runs
    one way only — ``config`` declares the protocol and never imports this package.
    """
    return catalogue


def kind_string(language: str, scope: Scope) -> str:
    """The ``Metric.list`` kind string for one language and scope.

    Every alternative of the scope's kind string carries the language, because Understand
    reads the language of each alternative separately and an unprefixed one matches every
    language at once.
    """
    if scope == "arch":
        return ARCH_KIND
    token = language_token(language)
    if scope not in SCOPE_KINDS:
        return token
    alternatives = SCOPE_KINDS[scope].split(",")
    return ", ".join(f"{token} {alternative.strip()}" for alternative in alternatives)


def language_token(language: str) -> str:
    """The language as an Understand kind string spells it (``C++`` is ``c``)."""
    name = language.strip().lower()
    return LANGUAGE_KINDS.get(name, name)


def _as_list(value: object, kind: str) -> list[object]:
    """The metric list of one kind, or the reason the answer is not one."""
    if not isinstance(value, list):
        raise _unusable(f"the metrics of {kind!r} are not a list", str(value)[:200])
    return value


def _unusable(reason: str, detail: str) -> AnalysisFailedError:
    """The error an answer the catalogue cannot read becomes."""
    return AnalysisFailedError(
        f"the catalogue operation answered with something this version cannot read: {reason}",
        stderr=detail,
        hint="The worker and the Gate are out of step; reinstall the Gate.",
    )
