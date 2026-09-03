"""Run Understand's CodeCheck over a file list and read the violations it wrote (req 6.9).

Two things live here: :class:`CodeCheckRunner`, which hands a configuration and a file list
to :meth:`~scitools_hook.understand.und_cli.UndCli.codecheck`, and
:func:`read_violations`, which turns the CSV that run leaves behind into
:class:`~scitools_hook.models.understand.RawViolation` records. Mapping those records onto
findings, with severities and hints, belongs to the analysis layer; nothing here knows what
a finding is.

**The parsing is header-driven, on purpose.** No CSV could be produced while writing this —
the license on the development machine excludes CodeCheck, so ``und codecheck`` answers
``Licensing Error: No license for CodeCheck.`` and writes nothing — but the two header lines
``und`` builds are compiled into the executable, one per results model::

    Invalid,Violation,File,Directory,Entity,CheckID,Check Name,Line,Column, ,
        Snippet,Ignored,Note,Root, ,Severity              (CodeCheckResultsTreeModel)
    Invalid,Violation,File,Directory,Entity,CheckID,Check Name,Line,Column, ,
        Snippet,Ignored,Note,CheckID,Root,Severity        (CodeCheckResultsFilesTreeModel)

Two headers, sixteen columns each, disagreeing at exactly two positions (13 and 14): *blank*
column names (two in the first, one in the second), a leading column the Gate has no use for,
and ``CheckID`` appearing **twice** in the second — which is why columns are matched by name and
never by index. :data:`COLUMN_NAMES` lists the spellings each field answers to, comparison
ignores case, spacing and punctuation, unknown and reordered columns are ignored, a repeated
column resolves to its leftmost occurrence, and a header missing one of :data:`REQUIRED_COLUMNS`
raises an error quoting the header that actually arrived.

**Anything this module cannot justify from measured evidence, it refuses.** CodeCheck is
unlicensed here, so no real CSV could be produced to check any of this against; handling
invented for an unobserved shape is not coverage, it is a new way to emit a wrong answer in
silence. So the surface is deliberately narrow — a row whose meaning is not settled raises,
and the first licensed run turns the guess into a measurement in one go. Two rules keep a
malformed row from becoming a wrong finding rather than an error:

* **A row with no check id is not a violation.** The files-tree export groups violations
  under directory and file rows, whose check id is empty; passed on, such a row reaches
  ``models.findings.codecheck_rule`` and raises ``ConfigError: a CodeCheck rule name needs a
  check id`` — a configuration exit code, far from its cause, naming no file. A grouping row
  carries no violation message either, and that is the discriminator: no check id *and* no
  message is a grouping row and is skipped, while a row that states a violation and names no
  check is a column matched to the wrong place and raises on the spot. Counting rows would
  not do — one grouping row is as legitimate as ten, and one mis-mapped row is as wrong.
* **A path matches one accepted form or it is an error.** ``File`` carries a leaf name in
  the files-tree export, with ``Directory`` beside it; on its own it becomes a repo-relative
  path that points at nothing, and the analysis layer passes a relative path straight
  through. So ``Root``, ``Directory`` and ``File`` are composed in that order by
  :func:`_compose`, where an *anchored* component discards whatever came before it — which
  is how an absolute ``Directory`` wins over ``Root``, on a drive letter as well as on a
  posix root. Each of the three is held against a **whitelist** first, and what comes out is
  held against it again:

      an anchor of ``/`` or ``<letter>:/``, then one or more segments, each non-empty and
      not merely whitespace, none of them ``.`` or ``..``, and none containing a colon or a
      character whose Unicode category is a control, format, surrogate, private-use,
      unassigned, line-separator or paragraph-separator one, and none of them ``\ufffd``.
      One trailing separator is tolerated on any of the three columns, and survives into
      the result when ``File`` is anchored; ``map_violations`` drops it.

  Anything else is refused, including shapes nobody here has thought of. That inversion is
  the point. Enumerating what to *reject* — not absolute, contains ``..``, still relative —
  left a gap between the patterns every single time, most recently drive-relative
  ``C:util.c``, which is not absolute, does not traverse, and composes into
  ``/proj/C:util.c`` without a murmur. One rule that says what a path may be cannot have a
  gap of that kind. Separately, and for a reason of meaning rather than form, a *relative*
  ``File`` that carries its own directory beside a non-empty ``Directory`` is refused: the
  two columns disagree about which of them holds the directory and both readings are
  defensible. An *anchored* ``File`` states where it is and wins outright, because there is
  then nothing to disagree about.

  **The inbound side: everything between a file name the Gate holds and what und receives.**
  ``und -files`` takes a list file whose format ``und`` defines, so the hazards are its
  grammar rather than a decoder's. Each item was measured against the real binary or quoted
  from its own help; all four are **newly refused** by :func:`unusable_list_file_name`, because a
  name that cannot be *asked* about is a file that goes unchecked — a clean report on code
  nobody looked at.

  i.   *Path normalisation.* ``[Path(name) for name in files]`` collapses ``a//b`` to
       ``a/b`` and ``./x`` to ``x``, which name the same file and are allowed. It also
       collapses ``""``, ``"."``, ``"./"``, ``".//"``, ``"././"``, ``"./."`` and ``".///."``
       to ``.``, and ``".."``, ``"/"``, ``"a/.."`` and ``"src/../."`` likewise name a
       directory. Refused by testing the *outcome* — ``Path(name).name`` must be a real
       final component — not by listing the spellings: enumerating spellings is what
       :func:`_unusable` records as having failed every time.
  ii.  *The list file is line-delimited.* ``_list_file`` writes ``f"{path}\n"`` with no
       escaping, so ``Path("a\nb.c")`` lands as two entries, ``a`` and ``b.c``. A newline is
       a legal posix file name and ``git/repo.py`` can produce one. The outbound side
       refuses an LF in a path by codepoint; this side used to split it in silence.
  iii. *A comma starts a line-number list.* ``und help codecheck`` on build 1204: "a comma
       delimited list of line numbers and ranges (5,10,12-30) can be specified **after the
       file name**". So ``a,b.py`` — a legal posix name, and the very fixture this module
       uses to justify its outbound width guard — is read as file ``a`` limited to lines
       ``b.py``.
  iv.  *The list file is written UTF-8.* ``git/repo.py`` decodes names with
       ``surrogateescape``, so a latin-1 name arrives as ``'/src/caf\\udce9.c'`` and
       ``write_text`` raises ``UnicodeEncodeError`` — a ``ValueError``, neither ``GateError``
       nor ``OSError``, straight out of ``run`` and past every caught-error tuple. The
       envelope now sits in ``_list_file`` as well, so ``analyze`` and ``remove_files``,
       which share it, do not leak one either.
  v.   *A ``#`` truncates the line.* ``und help analyze``: "Lines in the file starting
       with # will be ignored." Measured with a marker per file: a ``-files`` list holding
       ``/src/#hashed.c`` exits 0 reporting ``Errors:0`` and never analyses it, while
       ``/src/vic.c#1.c`` analyses ``vic.c`` instead.
  vi.  *A backslash is rewritten to* ``/``. Measured: with ``src/back/slash.c`` and a
       literal ``src/back\\slash.c`` both on disk, ``und add`` takes only the first, and a
       ``-files`` entry naming the literal one reports ``File: …/src/back/slash.c`` and that
       file's marker — **a different file analysed**, rc 0. The outbound side already knows
       ``a\\b.c`` is a real posix name; :func:`_slashes` documents rewriting it as a cost.
       Refused only where ``\\`` is not the platform separator: on Windows it *is* one, and
       rewriting a separator to a separator changes nothing.
  vii. *A ``*`` is glob-expanded.* Measured: ``Error: path "…/st*ar.c" contained a wild card
       that did not result in any files``, rc 1. Loud rather than silent, but a legal posix
       name that cannot be asked about. ``?`` and ``[`` are not wild cards here — both were
       analysed normally — so they are not refused.
  viii. *A relative name resolves against und's own directory*, not the repository root
       (measured: run from ``/tmp``, ``src/plain.c`` became ``/tmp/src/plain.c``). Usually
       loud, but it silently checks the wrong file wherever a same-named one exists there,
       so ``run`` requires absolute names. **8.3 must pass absolute paths.**

  ``#`` is the one that had to be measured twice. ``und help analyze`` says it outright —
  "Lines in the file starting with # will be ignored" — and ``und help remove`` says "# lines
  ignored"; ``add``, ``list``, ``settings`` and ``codecheck`` describe no list-file format
  at all, so ``codecheck`` is simply the page that is silent, not a page that dissents. A
  round of this review recorded the opposite, on the strength of an ``analyze -files`` run
  that ended ``Errors:0``. **That measurement could not fail**: an error count cannot tell
  "analysed clean" from "never looked at". Re-run with a marker per file — a distinct
  syntax error in each — it fails immediately: ``analyze -all`` reports all four markers,
  while a ``-files`` list holding ``/src/#hashed.c`` reports **none**, exits 0, and says
  ``Errors:0``. Worse, ``/src/vic.c#1.c`` checks ``vic.c`` — a *different file* — because
  ``und`` truncates the line at the ``#``. Both are refused.

  ``*`` is refused for a milder reason and was measured the same way: ``und`` glob-expands
  it and fails loudly, ``Error: path "…/st*ar.c" contained a wild card that did not result
  in any files``, rc 1. ``?`` and ``[`` are **not** wild cards to it — ``q?mark.c`` and
  ``br[a]ck.c`` were both analysed, markers and all — so they are allowed, and the ban stops
  where the evidence does. Interior spaces and tabs are likewise passed through unchanged.

  **The outbound side: everything that stands between the bytes on disk and the value
  judged, enumerated.**
  Each round of review found one more of these, always upstream of the predicate, so they
  are listed rather than reasoned about — every entry below was measured, not inferred:

  1. *UTF-8 decode with* ``errors="replace"`` (:func:`_read`) — an undecodable byte becomes
     ``\ufffd``. **Contained**: ``\ufffd`` is refused in a path segment.
  2. *Universal-newline translation* (``Path.read_text``) — CR and CRLF become LF. Inside a
     quoted field this is **contained**, since LF is refused as a control character. Outside
     one it ends the record, which is why (3) exists.
  3. *CSV record and field recovery* (:mod:`csv`). Two parts. ``strict=True`` is passed, so
     junk after a closing quote is an error rather than silently appended — without it
     ``"/proj/a.c"junk`` became the accepted path ``/proj/a.cjunk``. And a record short of
     the header's width is refused by :func:`_require_whole_row`, because that is how a
     field lost to (2) looks from here. Doubled quotes still collapse to one: that is how a
     quote is written in a CSV, not a transformation of the name.
  4. :func:`_slashes` — ``\\`` becomes ``/``. **Chosen**, and the only uncontained
     *rewrite*: a posix file genuinely named ``a\\b.c`` is read as two segments. Nothing in
     a CSV distinguishes it from a Windows path.
  5. :func:`_segments` drops **one trailing separator** from each column, so ``Directory``
     may be written ``/proj/native/``. It is a trim, not a rewrite, and for an anchored
     ``File`` it survives into the reported path — ``map_violations`` drops it there.

  ``_value`` itself edits nothing: all three path columns are taken verbatim, and pydantic
  was measured to leave every field exactly as it found it (evidence from one pin, 2.13.5;
  ``str_strip_whitespace`` has defaulted false since 2.0, so it holds across the supported
  floor). The one edit that does reach a path reaches **all three** columns, and that is
  ``_slashes`` at item 4. Every other field normalises for its own reasons afterwards —
  :func:`_identifier`, :func:`_phrase`, :func:`_text`, :func:`_number`.

**What a first licensed run may well see, and what it means.** Three shapes could refuse a
real export wholesale, in the order they are worth suspecting.

*A* ``File`` *written as a multi-segment relative path beside a non-empty* ``Directory``.
Every row is refused, the run exits with the analysis-failed code, and the message names the
same two columns each time. That is the refusal working, not the runner being broken: it
means the two columns have to be read together in a way nothing here could measure.

*A ragged row* — one field more or fewer than the header. Both compiled headers are exactly
sixteen columns wide — the model *names* beside them in the binary say they are built once
rather than per run, though only the names are readable — and a properly quoted multi-line
``Snippet`` does not split a record (measured), so a ragged row should not arise.
What can make it ragged is an unquoted comma or newline in a ``Snippet`` or a path — and
those are precisely the shapes where the path that comes out is silently wrong, which is why
the row is refused rather than read as far as it goes.

*A path column carrying a banned character.* Four of those bans have a cost worth knowing
before it is paid, because each refuses a file that really does exist. ``:`` is the one most
likely to be met: a colon is legal on every posix filesystem, and ``a:b.c`` is refused
because a colon is how a drive anchor is spelled and this module cannot tell the two apart.
``Cf`` covers U+200C ZWNJ and U+200D ZWJ, ordinary Persian and Hindi orthography and
structural in emoji sequences. ``Cn`` covers any codepoint newer than the interpreter's
Unicode tables, so a recently assigned script can stop the run. An undecodable byte becomes
``\ufffd`` and is refused, because the byte it stood for is exactly what the decoder could
not determine — and a file *legitimately* named with ``\ufffd`` is refused by that same ban,
which cannot tell the two apart. Each refusal names the offending character with its
codepoint, except where a banned character occupies a **whole segment**: ``/proj/\t/a.c`` is
reported as a whitespace-only segment, because that test runs before the per-character one.

In every case the lever is the same: unset ``codecheck.config`` to carry on without
CodeCheck — no other configuration will make a file name acceptable — then record what the
export actually wrote and widen the accepted form to the one reading the evidence supports.
Every refusal quotes the offending value and the whole row, so one run produces the
measurement this module has never been able to make.

The tolerance elsewhere is deliberate, and it is narrower than it once was. A violation may
carry no line number — a project-level check reports line 0 — so a blank or unparsable line
becomes :data:`NO_LINE`, and a wholly blank row is skipped. A row of the wrong width is
*not* tolerated; that was an invented tolerance and it hid a truncated path. An *absent*
CSV, or one with no header at all, is a failure and never "no violations found": a gate that
reports a clean run because CodeCheck crashed is worse than one that stops.
"""

from __future__ import annotations

import csv
import io
import os
import re
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final

from scitools_hook.errors import AnalysisFailedError
from scitools_hook.models.understand import RawViolation
from scitools_hook.understand.und_cli import UndCli

NO_LINE: Final = 0
"""The line of a violation the CSV gives no usable line for.

Zero, not ``None`` and not one: it is Understand's own value for a check that reports
against a file rather than a position (``check.violation(ent, ent, 0, 0, …)``), and
``analysis.codecheck`` reads ``line > 0`` to decide whether a finding has a line at all.
A different value here would silently place every file-level violation on a real line.
"""

COLUMN_NAMES: Final[Mapping[str, tuple[str, ...]]] = {
    "check_id": ("checkid",),
    "check_name": ("checkname",),
    "path": ("file", "filename", "filepath", "path"),
    "directory": ("directory", "dir"),
    "root": ("root",),
    "line": ("line", "linenumber"),
    "column": ("column", "col"),
    "message": ("violation", "violationtext", "message", "description"),
    "entity": ("entity", "entityname", "entityuniquename"),
}
"""Field name → the header spellings it answers to, normalised by :func:`_normalise`.

The first alias in each tuple is the label ``und`` writes; the rest are near neighbours,
cheap insurance against a build that words a column differently. Where a header offers two
spellings of one field — or the same spelling twice, as the files-tree header does with
``CheckID`` — the leftmost column wins.
"""

REQUIRED_COLUMNS: Final[Mapping[str, str]] = {
    "check_id": "CheckID",
    "path": "File",
    "line": "Line",
    "message": "Violation",
}
"""Fields a violation cannot be reported without (req 7.1), against the label ``und`` uses.

The label, not the field name, goes into the complaint: the person reading it is looking at
a CSV header, not at this module.
"""

_ANCHOR: Final = re.compile(r"^(?:/|[A-Za-z]:/)")
"""What a placed path must start with: a posix root, or a drive letter *and its separator*.

``C:util.c`` deliberately does not match. On Windows that is relative to drive C's working
directory, so it anchors nothing — and it is the shape that slipped past a blacklist made of
"is it absolute" and "does it traverse".
"""

_FORBIDDEN_CATEGORIES: Final = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})
"""Unicode general categories no path segment may contain a character from.

A category test, not a range: the previous ``[:\\x00-\\x1f\\x7f]`` pinned three characters out
of the thirty-odd it meant to ban and had two edges to slip past — shrinking it to
``\\x1e`` let ``a\\x1fb.c`` through as a reported path. ``C*`` is every control, format,
surrogate, private-use and unassigned character, which covers NUL and TAB along with NEL
(U+0085), the C1 range and the bidirectional overrides; ``Zl``/``Zp`` add the line and
paragraph separators. Ordinary space separators (``Zs``, which includes U+00A0) stay legal:
a file may genuinely be named with one, and the point is to report the file that exists.

``Cs`` cannot arrive through :func:`_read`, because ``errors="replace"`` turns a lone
surrogate into ``\ufffd`` first. That is not a reason to drop it, and for one round it was
a reason to accept what the replacement produced — the class the ban exists for was landing
as a legal path instead of a refusal. ``\ufffd`` is banned by name in
:data:`FORBIDDEN_IN_SEGMENT`, and ``Cs`` stays for any path that reaches the predicate
without passing a decoder.
"""

FORBIDDEN_IN_SEGMENT: Final = frozenset({":", "\ufffd"})
"""The two characters a segment may not hold whatever their category says.

``:`` because a drive letter belongs to the anchor and nowhere else — that single ban is
what makes ``/proj/C:util.c`` and ``C:/proj/C:native/util.c`` unrepresentable rather than
merely unmatched by some pattern. **Its cost is the largest of any ban here**: a colon is
legal on every posix filesystem, so a real file called ``a:b.c`` is refused, and it is far
likelier to be met than ZWNJ, an unassigned codepoint or ``\ufffd``. Nothing in a CSV
distinguishes a colon in a name from a colon introducing a drive, which is why the ban wins
over the name.

``\ufffd`` because :func:`_read` writes one wherever it could not decode a byte, so its
presence means the name may not be the name. It is *not* proof of that — U+FFFD is a legal
character and a file may genuinely be called ``caf\ufffd.c``, which this build refuses with
the same message; that is the fourth documented refusal cost. What is measured is that its
category is ``So``, an ordinary symbol nothing else here would stop, and that a latin-1 file
name arrived as ``/proj/caf\ufffd.c`` and reached ``Finding.path`` as a file that does not
exist, reported as fact. It also closes the hole under the ``Cs`` ban — see
:data:`_FORBIDDEN_CATEGORIES`. A snippet is a different matter and keeps its replacements:
a mangled message misreads, a mangled path misdirects.
"""

_INBOUND_HINT: Final = (
    "und's -files list file is one path per line, and a comma after a path starts a "
    "line-number range, so a name carrying either cannot be asked for at all. Exclude the "
    "file, or unset codecheck.config to stop running CodeCheck."
)
"""What an operator can do about a file name ``und`` has no way to be asked about."""

_CONFIG_HINT: Final = (
    "This is what und's CodeCheck wrote. Unset codecheck.config to stop running it — that "
    "is the lever that always works, and it is opt-in and unset by default. Naming a "
    "different configuration helps only where the report itself is the problem, not where "
    "a file name is."
)
"""The one lever an operator has: ``codecheck.config`` is opt-in and defaults to unset."""


class CodeCheckRunner:
    """Run a CodeCheck configuration over a set of files and return its violations (6.9)."""

    def __init__(self, cli: UndCli) -> None:
        self._cli = cli

    def run(
        self, db_path: Path, config: str, files: list[str], out_dir: Path
    ) -> list[RawViolation]:
        """Check ``files`` against ``config`` and read the CSV written into ``out_dir``.

        ``config`` is either the name of a configuration held in the project or the path of
        an exported one — ``und`` accepts both. ``out_dir`` is the caller's throwaway
        directory: CodeCheck insists on one even when it finds nothing.

        An empty ``files`` list starts no process. There is nothing to check, and ``und``
        would write no CSV, which the wrapper reports as a failed run.

        Each name is held against :func:`unusable_list_file_name` before it goes anywhere, for the
        reasons enumerated in the module docstring under *the inbound side*.
        """
        if not files:
            return []
        for position, name in enumerate(files):
            problem = unusable_list_file_name(name)
            if problem is not None:
                raise AnalysisFailedError(
                    f"the file list for CodeCheck holds {name!r} at position {position}, "
                    f"which {problem}",
                    hint=_INBOUND_HINT,
                )
        written = self._cli.codecheck(db_path, config, [Path(name) for name in files], out_dir)
        return read_violations(written)


def unusable_list_file_name(name: str) -> str | None:
    """Why ``name`` cannot be handed to ``und -files``, or ``None`` when it can.

    **Public, and named for the format rather than for this module.** ``und -files`` is read
    by ``codecheck``, by ``analyze`` and by ``remove``, so the same grammar is written by
    three commands across two modules: this one, ``understand/database.py`` and
    ``runner/check.py``. It was called ``_unusable_name`` while it had one outside importer
    and kept the leading underscore when it acquired a second, with both of them aliasing it
    to this very name on the way in -- which is not a private predicate, it is a public one
    spelled twice (found by the import-direction gate, fixed by task 11.7). Its two siblings
    below stay private: nothing outside this module calls them.

    The outbound predicate has had nine rounds of scrutiny and this side had one, which is
    why it is a list of measured refusals rather than a single rule: the list file is a
    format ``und`` defines, and every entry was measured against the real binary or quoted
    from its own help.

    The refusals are asked in two groups, and the split is by *what is being judged*: what
    the string denotes, then which characters it holds. It is also what keeps the routine
    inside the gate's own complexity limits -- nine guard clauses in one body measured
    ``CyclomaticStrict`` 13 against a maximum of 10 (task 10.4). Order still matters within
    each group and the shape group still runs first, so a directory is refused as a
    directory rather than as, say, a name holding a comma.
    """
    return _unusable_shape(name) or _unusable_characters(name)


def _unusable_shape(name: str) -> str | None:
    """Refusals about what the string denotes: a directory, padded, or relative.

    The first test guards an *outcome*: ``Path(name).name`` is empty for ``""``, ``"."``,
    ``"./"`` and ``"/"``, and ``".."`` for ``".."``, ``"a/.."`` and ``"src/../."``. Listing
    those spellings is the shape :func:`_unusable` records as having failed every time.

    The edge-whitespace test uses ``str.strip``, which covers every Unicode space; the
    measurement covers the ASCII ones. That is a deliberate widening, and it widens towards
    refusing.
    """
    final = Path(name).name
    if not final or final == "..":
        return "names a directory rather than a file"
    if name != name.strip():
        return "has whitespace at an edge, which und strips from a list-file line"
    if not Path(name).is_absolute():
        return "is relative, and und resolves a list-file path against its own directory"
    return None


def _unusable_characters(name: str) -> str | None:
    """Refusals about the characters the name holds, each one a syntax ``und`` gives them.

    Every entry here was measured against the real binary or quoted from its own help; the
    list file is one path per line and ``und`` reads the line, not the path.
    """
    if "\n" in name or "\r" in name:
        return "holds a line break, and the list file is one path per line"
    if "," in name:
        return "holds a comma, which und reads as the start of a line-number list"
    if "#" in name:
        return "holds a '#', after which und ignores the rest of the line"
    if "*" in name:
        return "holds a '*', which und expands as a wild card"
    if "\\" in name and os.sep != "\\":
        return "holds a backslash, which und rewrites to '/' in a list-file entry"
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        return "cannot be encoded as UTF-8, which is what the list file is written in"
    return None


def read_violations(csv_path: Path) -> list[RawViolation]:
    """Parse a CodeCheck violations CSV, mapping its columns by the names in its header.

    Everything the parse can throw leaves as an :class:`AnalysisFailedError`. That is
    structural rather than incidental: ``csv`` raises its own ``Error`` on a field past its
    size limit, and ``int`` and pydantic's validation both raise ``ValueError`` — none of
    which the runner's or ``doctor``'s caught-error tuples know about, so any one of them
    would leave the typed envelope the whole package is built on. (A NUL byte is *not* one
    of them: measured, ``csv.reader`` parses ``bad\x00here`` straight through into a field.
    A NUL in a path is refused by :func:`_unusable` and one in a message is dropped by
    :func:`_text`.) The module's own refusals are ``GateError``s and pass straight through.
    """
    text = _read(csv_path)
    try:
        return _parse(text, csv_path)
    except (csv.Error, ValueError) as broken:
        raise AnalysisFailedError(
            f"the CodeCheck results file {csv_path} could not be parsed: {broken}",
            hint=_CONFIG_HINT,
        ) from broken


def _parse(text: str, csv_path: Path) -> list[RawViolation]:
    """The parse itself, inside :func:`read_violations`'s typed envelope."""
    rows = csv.reader(io.StringIO(text), strict=True)
    header = next(rows, None)
    if header is None:
        raise AnalysisFailedError(
            f"the CodeCheck results file {csv_path} is empty, so it has no header to read",
            hint="Re-run CodeCheck: an empty results file means the run wrote nothing.",
        )
    columns = _map_columns(header, csv_path)
    violations: list[RawViolation] = []
    for row in rows:
        if not row:
            continue
        _require_whole_row(row, len(header), csv_path)
        check_id = _identifier(_value(row, columns, "check_id"))
        if not check_id:
            _reject_unattributed(row, columns, csv_path)
            continue
        violations.append(_read_row(row, columns, csv_path, check_id))
    return violations


def _require_whole_row(row: Sequence[str], width: int, csv_path: Path) -> None:
    """Refuse a row that does not have exactly one field per header column.

    A short row is how a lost field looks from here, and one way to lose a field is silent:
    ``read_text`` turns a CR inside an *unquoted* value into LF, ``csv`` reads that as the
    end of the record, and ``/proj/a\rb.c`` becomes the row ``['R_01', '/proj/a']`` — a
    shorter path that is perfectly well formed and names a different file. Reading a short
    row "as far as it goes" was an invented tolerance; nothing measured said ``und`` omits
    trailing fields, and it hid exactly this.
    """
    if len(row) == width:
        return
    raise AnalysisFailedError(
        f"the CodeCheck results file {csv_path} has a row of {len(row)} fields where its "
        f"header has {width}, so a field was lost or gained; the row was {','.join(row)}",
        hint=_CONFIG_HINT,
    )


def _reject_unattributed(row: Sequence[str], columns: Mapping[str, int], csv_path: Path) -> None:
    """Let a grouping row past, but never a row that reports a violation and names no check.

    A row with neither a check id nor a message is the files-tree export's way of heading a
    group — and so, harmlessly, is a wholly blank row, which is why no separate guard exists
    for one. A row with a message and no check id cannot be either of those things: the
    check id column has been matched to a column that does not hold check ids, and reporting
    the rest of the file as findings would be reporting a file nobody could read.
    """
    if not _value(row, columns, "message"):
        return
    raise AnalysisFailedError(
        f"the CodeCheck results file {csv_path} states a violation in a row that names no "
        f"check: {','.join(row)}",
        hint="Every violation names the check that found it, so an empty check id beside a "
        "message means the check id column was matched to the wrong column.",
    )


def _read(csv_path: Path) -> str:
    """The CSV's text; a file that is not there means CodeCheck produced no results.

    Undecodable bytes are replaced rather than raised: a snippet quoted out of a latin-1
    source file must not cost the run every other violation in the report. A byte-order
    mark, if one is written, ends up on the first header label and is dropped there by
    :func:`_normalise`, so it needs no special decoding.
    """
    try:
        return csv_path.read_text(encoding="utf-8", errors="replace")
    except OSError as unreadable:
        raise AnalysisFailedError(
            f"the CodeCheck results file {csv_path} could not be read: {unreadable}",
            hint="CodeCheck writing no results file is a failed run, not a clean one.",
        ) from unreadable


def _map_columns(header: Sequence[str], csv_path: Path) -> dict[str, int]:
    """Which column holds which field, by name; the leftmost spelling of a field wins."""
    found: dict[str, int] = {}
    for index, label in enumerate(header):
        name = _normalise(label)
        for field, aliases in COLUMN_NAMES.items():
            if name in aliases and field not in found:
                found[field] = index
    missing = [label for field, label in REQUIRED_COLUMNS.items() if field not in found]
    if missing:
        raise AnalysisFailedError(
            f"the CodeCheck results file {csv_path} has no {', '.join(missing)} column; "
            f"its header is {','.join(header)}",
            hint="Column names are matched by name; add the spelling above to COLUMN_NAMES.",
        )
    return found


def _read_row(
    row: Sequence[str], columns: Mapping[str, int], csv_path: Path, check_id: str
) -> RawViolation:
    """One CSV row as a violation; the caller has already ruled out the rows that are not."""
    return RawViolation(
        check_id=check_id,
        check_name=_phrase(_value(row, columns, "check_name")) or check_id,
        path=_anchored_path(row, columns, csv_path),
        line=_number(_value(row, columns, "line")) or NO_LINE,
        column=_number(_value(row, columns, "column")),
        message=_text(_value(row, columns, "message")),
        entity=_phrase(_value(row, columns, "entity")) or None,
    )


def _anchored_path(row: Sequence[str], columns: Mapping[str, int], csv_path: Path) -> str:
    """Where the violation is, as a path of the one accepted form, or an error.

    ``Root``, ``Directory`` and ``File`` are each held against the accepted form, then
    composed by :func:`_compose`, and the result is held against it again. One rule, applied
    to everything that contributes and to what comes out.

    Checking the parts as well as the whole is not belt and braces. Composition drops a
    ``.`` segment, so ``File=./util.c`` under ``Root=/proj`` would arrive as ``/proj/util.c``
    and be accepted, while the identical ``File=/proj/./util.c`` — which never goes through
    composition — would be refused. Same input, two answers.

    An anchored ``File`` is taken as the whole answer and ``Directory`` is not consulted,
    while a *relative* ``File`` carrying its own directory beside a non-empty ``Directory``
    is refused as ambiguous. The asymmetry is deliberate: an anchored path states where it
    is and cannot be read any other way, so there is nothing to disagree about.
    """
    target = _slashes(_value(row, columns, "path"))
    directory = _slashes(_value(row, columns, "directory"))
    root = _slashes(_value(row, columns, "root"))
    if not target:
        raise AnalysisFailedError(
            f"the CodeCheck results file {csv_path} reports a violation with no file, "
            f"so there is nowhere to report it against; the row was {','.join(row)}",
            hint=_CONFIG_HINT,
        )
    for label, value in (("File", target), ("Directory", directory), ("Root", root)):
        _require_usable(label, value, row, csv_path)
    if _ANCHOR.match(target):
        placed = target
    else:
        _reject_two_directories(target, directory, row, csv_path)
        placed = _compose(root, directory, target)
    _require_placed(placed, row, csv_path)
    return placed


def _compose(root: str, directory: str, target: str) -> str:
    """Join the three, an *anchored* part discarding whatever came before it.

    Written out rather than left to ``PurePosixPath``, which resets only on ``/`` and never
    on ``<letter>:/`` — measured. Under it, ``Directory=C:/proj/native`` beside
    ``Root=C:/proj`` composed to ``C:/proj/C:/proj/native/util.c``, and the refusal that
    followed blamed a colon in a segment rather than the composition that put it there. The
    drive anchor exists for exactly one platform, and that was the platform it failed on.
    """
    anchor, segments = "", list[str]()
    for part in (root, directory, target):
        if not part:
            continue
        found = _ANCHOR.match(part)
        if found is None:
            segments = segments + _segments(part)
            continue
        anchor, segments = found.group(), _segments(part[found.end() :])
    return anchor + "/".join(segments)


def _segments(body: str) -> list[str]:
    """A path body split into its segments; one trailing separator is not a segment.

    ``Directory`` may legitimately be written ``/proj/native/``. Interior empties are kept,
    so ``//server/share`` still has one for :func:`_unusable` to refuse. Composition and
    validation both split here, so the tolerance cannot mean one thing in one and something
    else in the other.

    An empty ``body`` — an anchor and nothing after it, as in ``Root=/`` — has no segments,
    while a body that is *only* a separator has one empty segment. Without that distinction
    ``Directory="//"`` collapsed to a bare ``/`` and was quietly re-read rather than refused,
    and ``\\\\`` is a UNC introducer on the platform that writes backslashes.
    """
    if not body:
        return []
    trimmed = body[:-1] if body.endswith("/") else body
    return trimmed.split("/") if trimmed else [""]


def _require_usable(label: str, value: str, row: Sequence[str], csv_path: Path) -> None:
    """Refuse a column that could not appear in an accepted path; an empty one is fine."""
    if not value:
        return
    anchor = _ANCHOR.match(value)
    problem = _unusable(_segments(value[anchor.end() :] if anchor else value))
    if problem is None:
        return
    raise AnalysisFailedError(
        f"the CodeCheck results file {csv_path} reports {label} {value!r}, which {problem}; "
        f"the row was {','.join(row)}",
        hint=_CONFIG_HINT,
    )


def _require_placed(placed: str, row: Sequence[str], csv_path: Path) -> None:
    """Refuse a composed path that is not of the accepted form, saying exactly why.

    Only the anchor and "names something" are tested here. The segments do not need
    re-checking: :func:`_compose` concatenates segments that :func:`_require_usable` already
    passed, and it can drop them or replace them wholesale but never manufacture one — an
    anchored ``File`` is likewise returned as the value that was checked.
    """
    anchor = _ANCHOR.match(placed)
    if anchor is None:
        problem: str | None = "does not start at a posix root or at a drive letter separator"
    else:
        problem = _at_least_one(placed, anchor)
    if problem is None:
        return
    raise AnalysisFailedError(
        f"the CodeCheck results file {csv_path} places a violation at {placed!r}, which "
        f"{problem}; the row was {','.join(row)}",
        hint=_CONFIG_HINT,
    )


def _at_least_one(placed: str, anchor: re.Match[str]) -> str | None:
    """An anchor alone names a directory, not a file, so it cannot locate a violation."""
    return None if _segments(placed[anchor.end() :]) else "names no file, only a root"


def _unusable(segments: Sequence[str]) -> str | None:
    """Why ``segments`` could not appear in an accepted path, or ``None`` when they can.

    **This is a whitelist, and that is the whole design.** Earlier versions enumerated the
    shapes to reject — not absolute, contains ``..``, still relative — and every review found
    another class between the patterns; drive-relative ``C:util.c`` was one, a value that is
    not absolute, does not traverse, and composes into ``/proj/C:util.c`` without a murmur.
    Naming the one acceptable form instead means a shape nobody has thought of is refused by
    default rather than composed by default, which is the only safe default while CodeCheck
    is unlicensed and no real export can be measured.

    A segment must be non-empty, must hold something other than whitespace, must not be
    ``.`` or ``..``, and must contain neither :data:`FORBIDDEN_IN_SEGMENT` nor any character
    from :data:`_FORBIDDEN_CATEGORIES`.
    """
    for segment in segments:
        if not segment:
            return "has an empty path segment"
        if segment in (".", ".."):
            return f"has a {segment!r} path segment"
        if not segment.strip():
            return f"has the whitespace-only path segment {segment!r}"
        for character in segment:
            if character in FORBIDDEN_IN_SEGMENT or _category(character) in _FORBIDDEN_CATEGORIES:
                return f"has {character!r} (U+{ord(character):04X}) in path segment {segment!r}"
    return None


def _category(character: str) -> str:
    """The character's Unicode general category; unassigned code points answer ``Cn``."""
    return unicodedata.category(character)


def _reject_two_directories(
    target: str, directory: str, row: Sequence[str], csv_path: Path
) -> None:
    """Refuse a relative ``File`` that carries a directory while ``Directory`` holds one too.

    Composing them reads ``File`` as relative to ``Directory``; ignoring ``Directory`` reads
    it as relative to ``Root``. Both are defensible and they disagree, so the row is refused
    rather than resolved one way and reported as fact. The test is for a real directory
    *part* — two or more segments — so a trailing separator, which shows no such
    disagreement, is not mistaken for one. No emptiness filter: :func:`_require_usable` has
    already refused ``target`` if any segment of it was empty.
    """
    if not directory or len(_segments(target)) < 2:
        return
    raise AnalysisFailedError(
        f"the CodeCheck results file {csv_path} reports File {target!r} beside Directory "
        f"{directory!r}, so the two disagree about which holds the directory; the row was "
        f"{','.join(row)}",
        hint=_CONFIG_HINT,
    )


def _value(row: Sequence[str], columns: Mapping[str, int], field: str) -> str:
    """The cell holding ``field``, verbatim; empty only when the header lacks the column.

    There is no bounds check: :func:`_require_whole_row` has already refused any row that
    does not carry one field per header column, so an index taken from the header always
    lands.

    **Verbatim.** This used to strip, and each narrowing of what it stripped was a smaller
    version of the same mistake: a sanitiser ahead of a validator makes the validator judge
    a value the input never contained. ``str.strip()`` deleted tab, NEL and U+00A0 from a
    file name; ``str.strip(" ")`` still renamed ``" a.c"``, a perfectly legal posix file, to
    ``a.c`` — and the justification for it (that a person pads a CSV with ``a, b, c``) was an
    inference about a human-edited file, while ``und`` writes this one. Nothing was measured,
    so nothing is removed. Each field normalises for its own reasons afterwards —
    :func:`_identifier`, :func:`_phrase`, :func:`_text`, :func:`_number` — and the path
    columns — all three of them locate something — normalise for none. The only edit that
    reaches them is :func:`_slashes`, and it reaches all three alike.
    """
    index = columns.get(field)
    return "" if index is None else row[index]


def _number(text: str) -> int | None:
    """A line or column number, or ``None`` when the cell is blank or not a whole number.

    This one field does trim its own value, unlike the path column: whitespace is not part
    of a number, and the worst a mistake here can do is bound a violation to the file rather
    than to a line. A path that has been quietly edited misdirects a reader to a file that
    does not exist, which is a different kind of wrong.

    ``str.isdecimal`` and not ``int()``: a sign, a decimal point or a word must answer "no
    number here" rather than raise, and a negative line is not a position in a file. Not
    ``str.isdigit`` either — that is true of ``"\u00b2"``, which ``int`` then refuses, so the
    guard would have handed a raw ``ValueError`` to a caller expecting a typed one.
    """
    trimmed = text.strip()
    return int(trimmed) if trimmed.isdecimal() else None


def _identifier(text: str) -> str:
    """A check id with every space and newline removed.

    A CSV writer can wrap a quoted field, and ``codecheck_rule`` only refuses a *blank* id —
    so ``R_\n01`` would reach ``Finding.rule`` as ``codecheck.R_\n01``, corrupting the human
    report and every severity-map key that is meant to match it. No check id contains
    whitespace, so removing it cannot lose one.
    """
    return "".join(text.split())


def _phrase(text: str) -> str:
    """A check name or entity name with runs of whitespace collapsed to single spaces.

    These are prose and may legitimately contain a space, so the run is collapsed rather
    than removed; a wrapped field still stops carrying a newline into the report.
    """
    return " ".join(text.split())


def _text(value: str) -> str:
    """A violation message as written, less any NUL.

    The message is prose and keeps its newlines — a snippet spanning two source lines is
    quoted as two lines. A NUL is not prose: it truncates the string in every C consumer
    downstream of the report and renders as nothing at all in a terminal, so the one thing
    it can do is hide the rest of the message.
    """
    return value.replace("\x00", "")


def _slashes(text: str) -> str:
    """The same path with forward slashes, so posix and Windows values join the same way.

    Unconditional, and that has a cost worth stating: a posix file genuinely named ``a\\b.c``
    becomes the two segments ``a`` and ``b.c`` and is reported at a path that does not exist.
    Nothing in a CSV distinguishes that file from a Windows path, and Understand reports
    native separators, so the ambiguity is unavoidable rather than overlooked — a backslash
    is read as a separator because on the platform that emits them, it is one.
    """
    return text.replace("\\", "/")


def _normalise(label: str) -> str:
    """A header label reduced to its letters and digits, so spelling variants compare equal.

    ``Check ID``, ``check_id``, ``CHECK-ID`` and a label carrying a byte-order mark all come
    out as ``checkid``; the two blank columns in ``und``'s own headers come out as ``""``,
    which matches no field. Digits are *kept*, so a hypothetical second ``Line2`` column
    stays distinct from ``Line`` instead of quietly overriding it.
    """
    return "".join(character for character in label.lower() if character.isalnum())
