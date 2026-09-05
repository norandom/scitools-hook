"""Understand's own SARIF documents, prepared during a run and placed after it (2.1, 2.4).

Two halves of one job, split by *when* they can happen. The run knows which documents exist
and which shadow tree their paths are relative to, so it prepares them: read, re-root on the
repository, write into the analysis cache. Where they finally go is decided by ``--sarif
PATH``, which is a command-line concern, so that half happens afterwards and is a copy.

The split is also what keeps the layering honest. ``cli`` may not import ``understand``, and
re-rooting is an ``understand`` concern; both halves live here, in ``runner``, which may
import both and which the CLI may import in turn.

**None of this can fail a run.** The Gate's own SARIF is the deliverable and these are extra,
so a document that is missing, unreadable or unwritable becomes a reported problem on the
result and never an exception, and the exit code stays a function of the findings alone
(requirement 2.4).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Final

from scitools_hook.config.models import Settings
from scitools_hook.models.cache import CachePaths
from scitools_hook.models.findings import RunResult, UnderstandSarif
from scitools_hook.understand.codecheck_sarif import find_results
from scitools_hook.understand.sarif_companion import companion_path, companions

ANALYSIS: Final = "analysis"
"""``und analyze -sarif``: the parse errors and warnings of one analysis pass."""

CODECHECK: Final = "codecheck"
"""``results.sarif``: the violations of one CodeCheck inspection, where it is licensed."""

COMPANION_STEM: Final = "understand.sarif"
"""What the prepared documents are named after inside the cache.

Nothing is ever uploaded from there. ``check --sarif PATH`` copies each one beside the Gate's
file, and that name -- ``<gate>.understand-analysis.sarif`` -- is what a reader of the upload
sees.
"""

NO_DIAGNOSTICS: Final = (
    "the after side was not analysed in full this run, so Understand wrote no diagnostics to "
    "publish; a run over a cold cache, or one after `scitools-hook db rebuild`, does"
)
"""Why a warm run usually has no analysis companion (requirement 2.4).

Measured on Build 1262: ``und analyze -sarif`` reports **the pass**, not the database. A
selective pass over one clean file writes a document whose ``results`` array is empty while
the database still holds three parse errors, and the ``artifacts`` table lists every file
either way -- so nothing in the document says it is partial. Published, it would tell GitHub
that a repository parses cleanly when it does not. A partial pass therefore writes no
document at all, and this says so.
"""

DISCARDED: Final = (
    "--sarif names a destination that is not a regular file, so nothing was written beside it"
)
"""``--sarif /dev/null`` is a legitimate discard; a companion beside it would be a stray file."""


def prepare(
    sources: Mapping[str, Path | None], paths: CachePaths, repo_root: Path
) -> list[UnderstandSarif]:
    """Re-root each document Understand wrote and keep it in the cache (requirement 2.1).

    ``sources`` maps a kind to the file Understand wrote, or to ``None`` when this run
    produced none of that kind. A ``None`` under :data:`ANALYSIS` is reported rather than left
    out: the operator asked for the document, and silence would read as "this build has none".
    A kind that simply did not run -- CodeCheck on a repository that configures none -- says
    nothing.
    """
    found = {kind: path for kind, path in sources.items() if path is not None}
    prepared = [
        UnderstandSarif(
            kind=one.kind,
            source=None if one.target is None else str(one.target),
            problem=one.problem,
        )
        for one in companions(found, paths.root / COMPANION_STEM, repo_root, paths.after_tree)
    ]
    if sources.get(ANALYSIS) is None:
        prepared.append(UnderstandSarif(kind=ANALYSIS, problem=NO_DIAGNOSTICS))
    return sorted(prepared, key=lambda one: one.kind)


def write_beside(result: RunResult, gate_sarif: Path) -> RunResult:
    """Copy every prepared document beside the Gate's SARIF and say where it went (2.1, 2.4).

    Answered as an updated result rather than as a list, so the report rendered next can name
    every file written and every problem -- which is why the copies happen before the primary
    report rather than after it.
    """
    if not result.understand_sarif:
        return result
    discard = gate_sarif.exists() and not gate_sarif.is_file()
    return result.model_copy(
        update={
            "understand_sarif": [
                _one(companion, gate_sarif, discard) for companion in result.understand_sarif
            ]
        }
    )


def _one(companion: UnderstandSarif, gate_sarif: Path, discard: bool) -> UnderstandSarif:
    """One document copied to its place beside the Gate's file, or told why it was not."""
    if companion.source is None:
        return companion
    if discard:
        return companion.model_copy(update={"problem": DISCARDED})
    target = companion_path(gate_sarif, companion.kind)
    try:
        target.write_bytes(Path(companion.source).read_bytes())
    except OSError as unwritable:
        return companion.model_copy(
            update={"problem": f"could not be written to {target}: {unwritable}"}
        )
    return companion.model_copy(update={"written": str(target)})


def for_run(
    settings: Settings,
    paths: CachePaths,
    repo_root: Path,
    diagnostics: Path | None,
    inspection: Path | None,
) -> list[UnderstandSarif]:
    """What one check run has to offer, or nothing at all when the key is off (req 1.3, 2.1).

    The pipeline's one call into this module. It is a function and not a method on the
    pipeline because ``CheckPipeline`` is at its own ``CountDeclMethodNonStub`` limit, and
    because none of this needs the pipeline: two paths, the cache layout and the repository
    root are the whole input.
    """
    if not settings.understand.sarif:
        return []
    return prepare({ANALYSIS: diagnostics, CODECHECK: inspection}, paths, repo_root)


def keep_inspection(
    scratch: Path, into: Path, note: Callable[[str], None], wanted: bool
) -> Path | None:
    """Copy CodeCheck's own SARIF out of its scratch directory before that is removed (2.1).

    CodeCheck runs in a directory deleted the moment the run ends, so the document has to be
    taken out of it there and then; a path recorded and read later would name a directory that
    no longer exists.

    ``None`` covers three cases that are all "there is nothing to publish": the key is off,
    the build wrote CSVs and no SARIF at all, or the copy failed -- and the last of those is
    said out loud on the diagnostics channel rather than raised, because the inspection itself
    succeeded and its violations are already findings.
    """
    found = find_results(scratch)
    if found is None or not wanted:
        return None
    kept = into / found.name
    try:
        kept.write_bytes(found.read_bytes())
    except OSError as unwritable:
        note(f"CodeCheck's SARIF could not be kept: {unwritable}")
        return None
    return kept
