"""What the three token-index contract modules share: the fixture read with both token rules on.

The token index is measured by three modules -- ``test_token_index_contract`` for coverage,
``test_token_rules_contract`` for the exact finding set and ``test_docstring_lines_contract``
for the docstring accounting -- because one module holding all of it named ten first-party
modules against the dependency rule's seven. Each subject imports a different corner of the
package (the raw API and the worker, the two rules and ``Finding``, the metric names) and
this helper carries the part they share: the settings that switch the index on, the snapshot
those settings read, the index and entity lookups, and the ``tokenize`` line budget with the
table printer beside it. It is a sibling of ``contract_project`` and is not collected.
"""

from __future__ import annotations

import io
import tokenize
from typing import NamedTuple

import pytest
from contract_project import FILES, SampleProject, contract_settings, extract_with

from scitools_hook.config.metric_names import Scope
from scitools_hook.config.models import Limit, Settings, ThresholdSpec
from scitools_hook.models.snapshot import EntityKey, ProjectSnapshot, TokenIndex

LINE_METRICS = ("CountLine", "CountLineCode", "CountLineComment", "CountStmt")
"""What Understand is asked about each routine, class and file for the docstring tables."""

_STATEMENT_STARTS = frozenset(
    {tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.ENCODING}
)
"""The tokens a docstring can follow: a string anywhere else is an expression's operand."""


class LineBudget(NamedTuple):
    """One Python file's lines as Python's own tokenizer reads them.

    Every line is in exactly one of ``blank``, ``prose``, ``docstring_blank`` and ``plain``:
    a blank line inside a docstring is counted once, as the docstring's, which an earlier
    draft got wrong by subtracting it from both and reading every multi-line docstring's file
    as one code line short.
    """

    total: int
    blank: int
    docstrings: int
    prose: int
    docstring_blank: int
    plain: int


def line_budget(text: str) -> LineBudget:
    """Blank lines, docstring lines and plain code lines, read with ``tokenize``.

    A docstring is a string token standing where a statement starts; the fixture's Python
    holds no ``#`` comment, which is asserted so that "plain" means code and nothing else.
    """
    tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    assert not any(token.type == tokenize.COMMENT for token in tokens)
    covered: set[int] = set()
    docstrings = 0
    for index, token in enumerate(tokens):
        if token.type != tokenize.STRING:
            continue
        before = [prior for prior in tokens[:index] if prior.type != tokenize.NL]
        if not before or before[-1].type in _STATEMENT_STARTS:
            docstrings += 1
            covered.update(range(token.start[0], token.end[0] + 1))
    lines = dict(enumerate(text.splitlines(), 1))
    empty = {number for number, line in lines.items() if not line.strip()}
    return LineBudget(
        total=len(lines),
        blank=len(empty - covered),
        docstrings=docstrings,
        prose=len(covered - empty),
        docstring_blank=len(covered & empty),
        plain=len(lines) - len(empty | covered),
    )


def token_settings(scopes: tuple[Scope, ...] = ()) -> Settings:
    """The contract settings with both token rules on, at the shipped numbers.

    ``scopes`` adds a threshold per :data:`LINE_METRICS` on each named scope, which is how a
    metric reaches the worker at all: only a threshold asks for one.
    """
    settings = contract_settings()
    settings.thresholds = [
        *settings.thresholds,
        *(
            ThresholdSpec(scope=scope, metric=name, limit=Limit(max=10_000))
            for scope in scopes
            for name in LINE_METRICS
        ),
    ]
    settings.lean.duplicates = "warning"
    settings.lean.similar_routines = "warning"
    return settings


def token_snapshot(project: SampleProject, scopes: tuple[Scope, ...] = ()) -> ProjectSnapshot:
    """The fixture read under :func:`token_settings`."""
    return extract_with(project.db("alpha"), project.root("alpha"), FILES, token_settings(scopes))


def index_of(snapshot: ProjectSnapshot) -> TokenIndex:
    """The index the run recorded; a run that recorded none has nothing to measure."""
    assert snapshot.tokens is not None, "both token rules were on and no index was recorded"
    return snapshot.tokens


def keys_of(snapshot: ProjectSnapshot, scope: Scope) -> list[EntityKey]:
    """Every entity of one scope the snapshot recorded."""
    return [key for key in snapshot.entities if key.scope == scope]


def number_of(row: dict[str, object], name: str) -> float:
    """One numeric cell of a printed row, for the assertions that do arithmetic on it."""
    value = row[name]
    assert isinstance(value, int | float), (name, row)
    return float(value)


def print_table(capsys: pytest.CaptureFixture[str], rows: list[dict[str, object]]) -> None:
    """The rows as one Markdown table, so the research log can carry the run verbatim."""
    with capsys.disabled():
        print("\n  | " + " | ".join(rows[0]) + " |")
        print("  |" + " --- |" * len(rows[0]))
        for row in rows:
            print("  | " + " | ".join(str(value) for value in row.values()) + " |")
