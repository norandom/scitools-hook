"""Every lean measurement the extractor makes, in a file the worker loads by path.

**Empty on purpose, for now.** The measurements themselves arrive with the tasks that need
them -- `routine_facts` and `class_facts` with the reference facts, `token_index` with the
duplication rules. What exists from the first day is the file, its place in the architecture
gate and the rule it is held to, because a file that appears later appears without either.

**Why a second file rather than more of `worker.py`.** `worker.py` measures 1 060 of its
1 200 permitted code lines and 119 of its 130 functions, and six lean extractions do not fit
in what is left. The alternatives were both worse: raising the ceiling would be adapting the
tool's own limits to fit a feature it is measuring, and a second *op* would open the database
and walk every entity a second time against a 6.5 s budget. So the measurements sit here, and
`worker.py` calls them from the walk it already makes (design.md, *Architecture Pattern
Evaluation*, option C).

**Why this file may import nothing from `scitools_hook`.** The worker runs under Understand's
own interpreter, `<home>/bin/<plat>/upython`, where this package is not on `sys.path` at all.
`worker.py` reaches this file through `importlib.util.spec_from_file_location` on its own
directory -- a *path* load, not an import statement -- so the file is executed by that same
interpreter and is bound by the same rule: the standard library and nothing else. Not even
the package leaf. `tests/test_import_direction.py` holds both files to an allowance of
`frozenset()` and proves it twice, once by parsing the source and once by executing this
module under `python -I -S` and looking for `scitools_hook` in the modules it created.

A path load leaves no trace in the import graph, which is exactly why the rule is written
down here as well: nothing a reader follows from `worker.py` would lead them to this file,
and nothing a checker walks would either.

**What the worker hands over instead of imports.** Because there is nothing to import, the
two helpers the measurements need -- the project-relative path function and the `understand`
module itself -- arrive as arguments, in a small context object built by `worker.py`. That
keeps one copy of each helper across the two files rather than a copy per file.

**What belongs here and what does not.** This file counts, lists and hashes; it never judges.
Thresholds, ignore lists and severities are the analysis layer's, which reads the facts back
off the snapshot. A measurement that decided whether a number was too large would put the
policy in the one process that cannot be unit-tested without a licence.

**One more consequence.** `snapshot_cache.worker_digest()` hashes the worker's source so that
a changed measurement cannot be answered out of the before-side cache. It has to hash this
file too, or a change confined to the sibling would be served stale.
"""
