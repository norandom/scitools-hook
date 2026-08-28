"""The adaptive baseline file and the problems a tolerant read of it reports (req 8).

Keys follow the rule-name grammar of ``models.findings`` (``routine.CyclomaticStrict``,
``project.AVG:CyclomaticStrict``). They are not validated here: requirement 8.6 asks for a
tolerant read that reports bad entries as :class:`BaselineIssue` and keeps going, which is
``analysis.baseline.parse_baseline``'s job.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from scitools_hook.models.snapshot import DataModel


class Baseline(DataModel):
    """Recorded maxima that adaptive mode lowers over time, never raises (req 8.1, 8.4)."""

    version: Literal[1] = 1
    captured_at: str
    values: dict[str, float] = Field(default_factory=dict)


class BaselineIssue(DataModel):
    """One problem found while reading a baseline; ``key`` is unset for file-level problems."""

    message: str
    key: str | None = None
