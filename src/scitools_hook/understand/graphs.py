"""Export Understand's own pictures of a change as SVG files (requirement 9.4).

``GraphExporter`` asks the worker's ``graphs`` operation to draw one graph per target and
returns the files it wrote. Three things are decided here rather than by the worker:

* **The output directory is made absolute.** The worker creates it *before* it opens the
  database and writes its names into the answer, so a relative path would be resolved against
  the worker's working directory and the answer would name files the caller cannot find.
* **A target set that is empty opens no database.** Drawing nothing costs an Understand
  license checkout and a full entity walk otherwise.
* **A graph Understand will not render is a warning.** Verified live: a routine draws
  ``Butterfly``, ``Calls`` and ``Called By`` and refuses ``Depends On``; one unavailable
  picture must not cost the reviewer every other one.

The operation itself never runs in this process — an in-process ``Ent.draw`` aborts the host
interpreter — which :class:`~scitools_hook.understand.api_runner.ApiRunner` guarantees.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from scitools_hook.config.metric_names import SCOPE_KINDS
from scitools_hook.errors import AnalysisFailedError
from scitools_hook.models.change import GraphFile, GraphTarget
from scitools_hook.understand.api_runner import ApiRunner


class GraphExporter:
    """Draws the requested graphs into one directory and reports the files it got.

    ``warnings`` collects every target Understand would not draw, across every call, because
    the answer type has nowhere to put them and the reviewer's report needs them.
    """

    def __init__(self, runner: ApiRunner):
        self.runner = runner
        self.warnings: list[str] = []

    def export(
        self, db: Path, root: Path, targets: Sequence[GraphTarget], out_dir: Path
    ) -> list[GraphFile]:
        """Export one SVG per target into ``out_dir`` and return the files that were written."""
        if not targets:
            return []
        answer = self.runner.run("graphs", self._request(db, root, targets, out_dir))
        self.warnings.extend(_warnings(answer))
        return _graph_files(answer)

    def _request(
        self, db: Path, root: Path, targets: Sequence[GraphTarget], out_dir: Path
    ) -> dict[str, object]:
        """The self-describing ``graphs`` request."""
        return {
            "db": str(db),
            "root": str(root),
            "kinds_by_scope": dict(SCOPE_KINDS),
            "targets": [
                {"key": target.key.model_dump(mode="json"), "graph": target.graph}
                for target in targets
            ],
            "out_dir": str(out_dir.resolve()),
        }


def _warnings(answer: dict[str, object]) -> list[str]:
    """The warnings an answer carries; an export that drew everything carries none."""
    reported = answer.get("warnings")
    if not isinstance(reported, list):
        return []
    return [str(warning) for warning in reported]


def _graph_files(answer: dict[str, object]) -> list[GraphFile]:
    """The exported files, validated into the records the change summary references."""
    written = answer.get("graphs")
    if not isinstance(written, list):
        raise _unusable("the answer carries no 'graphs' list", str(written)[:200])
    try:
        return [GraphFile.model_validate(document) for document in written]
    except ValidationError as invalid:
        raise _unusable("an exported graph is not one", str(invalid)) from invalid


def _unusable(reason: str, detail: str) -> AnalysisFailedError:
    """The error an answer the models cannot read becomes."""
    return AnalysisFailedError(
        f"the graphs operation answered with something this version cannot read: {reason}",
        stderr=detail,
        hint="The worker and the Gate are out of step; reinstall the Gate.",
    )
