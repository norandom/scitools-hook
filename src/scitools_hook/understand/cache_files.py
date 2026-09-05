"""The file-level operations on the analysis cache, in one place (requirements 2.1, 2.7).

Three things, and each is here because more than one module needs it and a second copy could
learn a different answer. The database manager owns the cache and both before-side routes
write into it; the commit route
(:mod:`~scitools_hook.understand.commit_before`) is imported *by* the manager, so anything
the two share has to sit below both.

The judgements themselves are the point. Whether a path is a database, whether it is missing
or merely unreadable, and whether removing it should follow a symlink are questions with one
right answer each, and answering them twice is how two callers come to disagree about the
same directory.
"""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path
from typing import Final

from scitools_hook.errors import AnalysisFailedError
from scitools_hook.paths import classify_directory

CACHE_HINT: Final = "Point understand.db_location or the cache directory somewhere writable."
"""Every failure to create or clear the cache has the same one fix."""

REBUILD_HINT: Final = "Run `scitools-hook db rebuild`, or remove what is at that path."
"""What to do about a path that is taken by something that is not a usable database."""


def discard(path: Path) -> None:
    """Remove a database, a state file or whatever else has taken their place.

    The kind is settled with ``lstat`` before anything is deleted, so a symlink is unlinked
    rather than followed into someone else's directory tree, and absence is not an error --
    discarding what is not there is the state this leaves behind anyway.
    """
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return
    except (OSError, ValueError) as unreachable:
        raise AnalysisFailedError(
            f"{path} could not be examined: {unreachable}", hint=CACHE_HINT
        ) from unreachable
    try:
        if stat.S_ISDIR(mode) and not stat.S_ISLNK(mode):
            shutil.rmtree(path)
        else:
            os.unlink(path)
    except OSError as undeletable:
        raise AnalysisFailedError(
            f"{path} could not be removed: {undeletable}", hint=CACHE_HINT
        ) from undeletable


def present(db: Path) -> bool:
    """Whether ``db`` is there to be used; a path that is taken but unusable is raised.

    Absence is an answer -- an operator who cleared the cache gets a fresh database -- and it
    is asked through the shared classifier, because ``Path.exists()`` cannot tell "no database
    yet" from "a database this user cannot read".
    """
    verdict = classify_directory(db)
    if verdict.absent:
        return False
    if not verdict.usable:
        raise AnalysisFailedError(f"the analysis database {db} {verdict.reason}", hint=REBUILD_HINT)
    return True
