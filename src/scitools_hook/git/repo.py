"""Read a git repository through plumbing, and materialise its content without touching it.

Everything here is written against a **measured** git (2.43.0), because the plumbing has
several behaviours no summary implies. The ones this module is built around:

* **``--git-common-dir`` answers relative to the working directory** while ``--git-dir``
  answers absolutely — measured ``../../.git`` from two levels down. Every path git hands
  back is therefore resolved against the directory the command ran in, which is why every
  call after discovery runs with ``-C <root>``.
* **``git diff --cached`` needs no ``HEAD``.** On an unborn branch it compares the index
  against the empty tree and reports every staged file as ``A``, exit status 0 — which is
  exactly the state a pre-commit hook sees before the very first commit. ``git read-tree
  HEAD``, by contrast, exits 128 there, so :meth:`GitRepo.head` returns ``None`` and the
  caller skips the before side.
* **``--name-status -z`` is a stream, not a table.** The record length depends on the status
  token: ``A\\0added.py\\0`` is two fields, ``R100\\0old\\0new\\0`` is three, and the token
  carries a similarity score that is not part of the status. Reading it in fixed-size groups
  desynchronises the whole stream at the first rename.
* **``checkout-index --prefix`` is a string prefix, not a directory.** Measured:
  ``--prefix=/tmp/dest`` writes ``/tmp/destmain.py``. The trailing slash is mandatory.
* **``core.hooksPath`` may come from any configuration level** and may be relative, in which
  case git resolves it against the *working tree root* — and against each linked worktree's
  own root. ``rev-parse --git-path hooks`` applies all of that; nothing here re-implements it.

**Both sides are exported by the same command**, and that is the point rather than an
incidental detail. The obvious alternative for the before side, ``git archive``, honours two
``.gitattributes`` settings that a checkout ignores, and an archive cannot carry some content
a checkout writes happily — so the two shadows would disagree about content neither side
changed. All three were measured on git 2.43.0:

* ``export-ignore`` **drops files from the archive**: with ``tests/ export-ignore``, ``git
  archive`` emitted ``{.gitattributes, src/s.py}`` while ``checkout-index`` emitted those
  plus ``tests/test_a.py``. Every entity in an ignored file would look new on every run, so
  the ratchet would silently stop firing there (requirement 4.3).
* ``export-subst`` **rewrites content**: ``$Format:%H$`` became the commit hash in the
  archive and stayed literal under ``checkout-index``, so the shadows differ byte-wise with
  no ``export-ignore`` anywhere.
* A committed symlink to an absolute path (``/etc/hostname``) or out of the tree
  (``../outside.txt``) is ordinary repository content that ``checkout-index`` writes
  verbatim, and that Python's ``tarfile`` ``data`` filter refuses outright.

So the before side is materialised through a **throwaway index** — ``GIT_INDEX_FILE=<temp>
git read-tree <commit>`` followed by the same ``checkout-index`` call the after side uses.
The temporary index goes to the system temporary directory and is deleted afterwards; that
is outside the repository under any ordinary configuration, though it honours ``TMPDIR``, so
an operator who points ``TMPDIR`` inside the working tree gets it there and requirement 2.2
becomes theirs to keep. That is deliberately *not* guarded the way ``XDG_CONFIG_HOME`` is:
relativity is not the discriminator here, because ``TMPDIR=/path/to/repo/tmp`` lands in the
tree just as effectively as ``TMPDIR=.``, so a relative-value guard would close nothing.
Enforcing 2.2 would mean comparing the resolved temporary directory against ``root``, which
is a different decision from the configuration ones — those produce a *persisted* answer that
``install-hook`` then writes into, while this file exists only for the duration of one call.

The repository's own index is never read or written: measured, its
``write-tree`` id is unchanged across the call even when it holds staged content the commit
does not.

Failures map to the existing hierarchy: a directory git will not accept as a working tree
becomes :class:`~scitools_hook.errors.NotAGitRepositoryError` (requirement 12.5), and every
other non-zero status, timeout or unstartable executable becomes
:class:`~scitools_hook.errors.AnalysisFailedError` carrying the argv and stderr. Every
attempt is recorded on the injected :class:`~scitools_hook.models.progress.CommandLog` with
its timing and status, including the ones that never ran (requirement 12.8).
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal

from scitools_hook.errors import AnalysisFailedError, ConfigError, NotAGitRepositoryError
from scitools_hook.models.git import StagedChange
from scitools_hook.models.progress import CommandLog, NullCommandLog

DEFAULT_TIMEOUT_S: Final = 300
"""Ceiling for one git call; a full ``checkout-index`` of a large repository still fits."""

TIMEOUT_RC: Final = 124
"""Status recorded for a command that had to be killed; GNU ``timeout``'s convention."""

MISSING_RC: Final = 127
"""Status recorded for a command that never started; the shell's "not found" convention."""

NO_ANSWER_RC: Final = 1
"""git's "I looked and there is nothing", as opposed to 128, "I could not look at all".

``rev-parse --verify --quiet HEAD`` and ``config --get`` both use it, and the distinction
matters: an unborn branch answers 1, while a repository whose ``HEAD`` file is corrupt
answers 128 — measured ``fatal: not a git repository``. Reading the second as the first
would skip the before side and silently stop the ratchet (requirement 4.3).
"""

GIT_EXECUTABLE: Final = "git"
"""The default executable; :class:`GitRepo` carries it so a test can point elsewhere."""

DISCOVERY_ARGS: Final = ("rev-parse", "--show-toplevel", "--git-dir", "--git-common-dir")
"""Root, this worktree's git directory, and the shared one — in that order, one per line."""

DISCOVERY_LINES: Final = 3
"""How many paths :data:`DISCOVERY_ARGS` asks for; fewer means git answered something else."""

StagedStatus = Literal["A", "M", "D", "R"]
"""The four statuses :class:`~scitools_hook.models.git.StagedChange` accepts."""

STATUS_LETTERS: Final[dict[str, StagedStatus]] = {
    "A": "A",
    "C": "A",
    "D": "D",
    "M": "M",
    "R": "R",
    "T": "M",
}
"""Git's status letters mapped onto the four the models know.

``C`` (a copy) is an addition, because the source is still there and nothing moved. Git
produces ``C`` records readily — measured, ``-C`` alone does it as soon as the copy's source
is also modified in the same diff, and so does ``-M -C``; with ``diff.renames = copies`` even
an unmodified source is enough. What makes the branch unreachable *here* is narrower, and is
the only thing claimed: the fixed argv :func:`_name_status` builds passes ``-M`` and never
``-C``, and that form reported the copy as ``A`` in all four combinations measured
(``diff.renames`` unset or ``copies``, copy source modified or not). The branch is kept
because :func:`parse_name_status` is public and reads any ``--name-status -z`` stream.

``T`` (a type change) is a modification: measured when a committed file is replaced by a
symlink, the path stays and only its content and mode move.
"""

TWO_PATH_LETTERS: Final = frozenset({"C", "R"})
"""The statuses whose record carries a source path *and* a destination path."""

UNMERGED_LETTER: Final = "U"
"""``U\\0path\\0``, measured during a conflicted merge; nothing downstream can read it."""

GLOBAL_HOOKS_FALLBACK: Final = "git/hooks"
"""Git defines no default global hooks directory, so one is chosen beside the user's config."""


def parse_name_status(payload: bytes) -> list[StagedChange]:
    """Read ``--name-status -z`` output as a stream of records driven by the status token.

        The number of paths a record carries is decided by its status, so the fields are consumed
        one record at a time rather than split into fixed-size groups: a rename in the middle of
        the stream would otherwise shift every record after it by one field.

    Only the first half of the trailing-field guard is a **known equivalent mutant**:
        ``bytes.split`` never returns an empty list (``b"".split(b"\0")`` is ``[b""]``), so
        ``fields`` is always truthy and testing it changes nothing. That is provable rather than
        observed.

        The second half, ``fields[-1] == b""``, is **not** equivalent and is pinned by test: on a
        payload that does not end in NUL — which this public function can be handed, whatever git
        itself emits — popping unconditionally would discard a real path. ``b"M\0f.txt"`` parses
        as one modification with the guard and loses its path without it. An earlier version of
        this docstring claimed both halves were equivalent; that claim was inferred from the
        mutant surviving, which shows only that it was untested.
    """
    fields = payload.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    changes: list[StagedChange] = []
    at = 0
    while at < len(fields):
        token = _decode(fields[at])
        letter = _status_letter(token)
        wanted = 2 if letter in TWO_PATH_LETTERS else 1
        paths = [_decode(chunk) for chunk in fields[at + 1 : at + 1 + wanted]]
        if len(paths) != wanted:
            raise _truncated(token, wanted, len(paths))
        changes.append(_change(letter, paths))
        at += 1 + wanted
    return changes


@dataclass(frozen=True)
class GitResult:
    """One finished git invocation, before any of it is interpreted."""

    argv: list[str]
    rc: int
    stdout: bytes
    stderr: str
    seconds: float


@dataclass(frozen=True)
class GitRepo:
    """The repository's plumbing, as typed methods (requirements 4.1, 4.3, 12.5, 12.8).

    The instance holds only the three directories git reported plus the log, the executable
    and the timeout, so it is safe to share and every call is independent of the last.
    """

    root: Path
    git_dir: Path
    common_dir: Path
    log: CommandLog = field(default_factory=NullCommandLog)
    git: str = GIT_EXECUTABLE
    timeout_s: int = DEFAULT_TIMEOUT_S

    # --- discovery --------------------------------------------------------------

    @classmethod
    def discover(
        cls,
        cwd: Path,
        log: CommandLog,
        git: str = GIT_EXECUTABLE,
        timeout_s: int = DEFAULT_TIMEOUT_S,
    ) -> GitRepo:
        """Find the repository containing ``cwd`` (requirement 12.5).

        A bare repository, a directory inside ``.git`` and a directory outside any repository
        all fail this call — measured, git says ``this operation must be run in a work tree``
        for the first two and ``not a git repository`` for the third — and all three mean the
        same thing to the Gate: there is no working tree to analyze.

        The status and the number of paths are checked independently. Real git never
        disagrees with itself, so each guard is pinned by a stand-in ``git`` that answers with
        one of the two halves wrong; without those, either could be deleted unnoticed.
        """
        argv = [git, "-C", str(cwd)] + list(DISCOVERY_ARGS)
        result = _execute(argv, log, timeout_s)
        lines = _decode(result.stdout).splitlines()
        if result.rc != 0 or len(lines) != DISCOVERY_LINES:
            raise NotAGitRepositoryError(
                f"{cwd} is not inside a git working tree: "
                f"{result.stderr.strip() or 'git reported nothing'}",
                hint="Run from inside a repository; `doctor` and `config` work without one.",
            )
        return cls(
            root=_against(cwd, lines[0]),
            git_dir=_against(cwd, lines[1]),
            common_dir=_against(cwd, lines[2]),
            log=log,
            git=git,
            timeout_s=timeout_s,
        )

    # --- reading state ----------------------------------------------------------

    def head(self) -> str | None:
        """The commit ``HEAD`` names, or ``None`` on an unborn branch (requirement 4.3).

        ``rev-parse --verify --quiet`` exits 1 and prints nothing when there is no commit,
        which is the state of every repository until its first one — the very moment a
        pre-commit hook runs. The caller then has no before side and treats every entity as
        new (requirement 4.5).

        Only status 1 means that. A corrupt ``HEAD`` file exits 128 (measured: git stops
        recognising the directory as a repository at all), and reading *that* as "unborn"
        would skip the before side and quietly disable the ratchet, so it is raised.
        """
        result = self._run(["rev-parse", "--verify", "--quiet", "HEAD"])
        if result.rc == NO_ANSWER_RC:
            return None
        if result.rc != 0:
            raise _failed(result)
        return _decode(result.stdout).strip()

    def staged_changes(self) -> list[StagedChange]:
        """What the index changes against ``HEAD`` (requirement 4.1).

        Unstaged edits to the same files are invisible here, which is the whole point of
        staged mode. Measured: with no ``HEAD`` this compares against the empty tree and
        reports every staged file as an addition, so no special case is needed.
        """
        return parse_name_status(self._checked(_name_status(["diff", "--cached"])).stdout)

    def worktree_changes(self) -> list[StagedChange]:
        """What the working tree changes against ``HEAD`` (requirement 10.5).

        Staged mode's counterpart, and the reason it cannot be reused: ``--worktree`` exists
        so that an agent can check edits it has **not** staged, and those are invisible to
        ``git diff --cached``. One revision is enough here -- ``git diff <commit>`` compares
        the working tree against it -- so the same fixed argv and the same terminators apply.

        With no ``HEAD`` there is nothing to diff against, and ``git diff HEAD`` would exit
        128 rather than report the obvious answer. Every tracked path is then an addition,
        which is exactly what :meth:`staged_changes` reports in the same state, so the two
        modes agree on an unborn branch instead of one of them failing.
        """
        head = self.head()
        if head is None:
            return [StagedChange(status="A", path=path) for path in self.tracked_files()]
        return parse_name_status(self._checked(_name_status(["diff"], head)).stdout)

    def diff_names(self, a: str, b: str) -> list[StagedChange]:
        """What changed between two commits, for ``explain --range`` (requirement 9.1)."""
        return parse_name_status(self._checked(_name_status(["diff"], a, b)).stdout)

    def index_tree_id(self) -> str:
        """The tree the index would commit to; the cache key of the after side.

        ``write-tree`` adds tree objects to the object database but changes neither the index
        nor the working tree, so it is safe to call from a hook. Measured: it exits 128 with
        ``error building trees`` while a merge conflict stands, which becomes a typed error
        rather than a silently wrong cache key.
        """
        return _decode(self._checked(["write-tree"]).stdout).strip()

    def tracked_files(self) -> list[str]:
        """Every path in the index, root-relative, each one once.

        Measured: ``ls-files -z`` prints an unmerged path three times, once per merge stage,
        so the paths are de-duplicated while keeping git's order.
        """
        listed = self._checked(["ls-files", "-z"]).stdout
        unique = dict.fromkeys(_decode(chunk) for chunk in listed.split(b"\0") if chunk)
        return list(unique)

    # --- materialising content --------------------------------------------------

    def export_index(self, dest: Path, paths: list[str] | None = None) -> None:
        """Write the **staged** content of the index under ``dest`` (requirement 4.1).

        The working tree is never read and never written: what lands in ``dest`` is what a
        commit would contain, even where the file on disk says something else. ``paths=None``
        exports the whole index; an explicit empty list is a request to export nothing, which
        starts no process at all.
        """
        _make_destination(dest)
        self._checkout_index(dest, paths)

    def export_commit(self, commit: str, dest: Path, paths: list[str] | None = None) -> None:
        """Write the content of ``commit`` under ``dest``, for the before side (req 4.3).

        ``commit`` is read into a **throwaway index** and checked out from there, so this is
        the same ``checkout-index`` call :meth:`export_index` makes and the two shadows can
        only differ where the change itself differs — see the module docstring for the three
        measured ways ``git archive`` would have made them disagree. The temporary index is
        created in the system temporary directory, so nothing is written inside the
        repository (requirement 2.2) and the repository's own index is untouched.
        """
        _make_destination(dest)
        if paths is not None and not paths:
            return
        with tempfile.TemporaryDirectory(prefix="scitools-hook-") as scratch:
            env = dict(os.environ, GIT_INDEX_FILE=str(Path(scratch) / "index"))
            self._checked(["read-tree", "--end-of-options", commit], env=env)
            self._checkout_index(dest, paths, env)

    def _checkout_index(
        self, dest: Path, paths: list[str] | None, env: dict[str, str] | None = None
    ) -> None:
        """Materialise an index under ``dest``; ``env`` selects a throwaway one.

        The whole-index form and the path-list form differ **only** in the selection switches
        and the payload. Everything they must agree on — ``-f``, ``--prefix`` and ``env`` —
        is written once and shared, deliberately: this method previously built two argv lists
        and twice acquired a defect that was fixed on one of them and missed on the other
        (``-f``, then ``env``). Sharing them makes that class of divergence unrepresentable
        rather than merely tested for.

        ``-f`` is load-bearing rather than tidy: measured, checking out into a destination
        that already holds the file exits 1 with ``… already exists, no checkout`` — in both
        forms — which is exactly what an incremental re-sync into an existing shadow does
        every time.

        Note for the shadow sync (7.2): on a **conflicted** index this exits 0 while writing
        nothing at all for the unmerged path, so a caller that reaches it during a merge would
        silently export a shadow with a file missing. Nothing can reach it that way today,
        because :meth:`staged_changes` and :meth:`index_tree_id` both raise on an unmerged
        index first; a sync path that stops calling those has to check for itself.
        """
        if paths is not None and not paths:
            return
        selection = ["-a"] if paths is None else ["-z", "--stdin"]
        payload = None if paths is None else b"".join(_encode(path) + b"\0" for path in paths)
        argv = ["checkout-index", "-f", f"--prefix={_prefix(dest)}"] + selection
        self._checked(argv, stdin=payload, env=env)

    # --- hooks ------------------------------------------------------------------

    def hooks_dir(self, global_: bool = False) -> Path:
        """Where a pre-commit hook belongs (requirements 11.1, 11.9).

        Without ``global_`` the answer comes from ``rev-parse --git-path hooks``, which
        applies ``core.hooksPath`` from every configuration level, resolves a relative value
        against this worktree's root, and otherwise points at the *common* git directory so
        linked worktrees share one set of hooks. With ``global_`` the answer is the user's
        own ``core.hooksPath``, ignoring whatever this repository says.
        """
        if global_:
            return self._global_hooks_dir()
        answer = self._checked(["rev-parse", "--git-path", "hooks"])
        found = _against(self.root, _decode(answer.stdout).strip())
        if found == self.root:
            self._reject_empty_hooks_path()
        return found

    def _reject_empty_hooks_path(self) -> None:
        """Refuse a ``core.hooksPath`` set to the empty string (decision, recorded here).

        Measured: with the key set but empty, ``rev-parse --git-path hooks`` answers ``./``
        at **status 0**, so the value silently resolves to the working-tree root. Two
        independent reasons not to pass that on:

        * Git runs nothing from there. A ``pre-commit`` placed in the working-tree root was
          measured *not* to fire, while the same file under ``.git/hooks`` with the key unset
          did. So there is no git behaviour to mirror — the answer is simply useless.
        * It is the one value that would make ``install-hook`` write a file into the
          repository working tree, which requirement 2.2 forbids outright.

        An explicit ``core.hooksPath = .`` also resolves to the root and is **not** refused:
        that is a value the operator wrote on purpose, and reporting it faithfully is this
        method's job. Refusing to *install* into a directory inside the working tree is the
        hook installer's call to make (7.3), which can compare the answer with ``root``.

        Deliberate asymmetry with :meth:`_global_hooks_dir`, which treats the same empty value
        as *unset* and falls back to the XDG location: there, no directory has been named and
        a default is exactly what is wanted, whereas here git has already resolved the value
        to a concrete and useless path that would be written into.

        The ``configured.rc == 0`` conjunct below is a second **known equivalent mutant**.
        This method is only reachable when ``--git-path hooks`` already succeeded and returned
        the root, and the only value producing that answers status 0; a valueless key makes
        both that command and this probe exit 128 first (measured). Dropping the conjunct is
        therefore untestable. It is kept so that a future caller reaching this in a state
        where the probe failed cannot be told "the value is empty" when it is unreadable.
        """
        configured = self._hooks_path_setting(global_=False)
        if configured.rc == 0 and not _decode(configured.stdout).strip():
            raise ConfigError(
                "core.hooksPath is set to the empty string, which git resolves to the "
                "working tree root and then runs no hook from",
                key="core.hooksPath",
                hint="Unset it (`git config --unset core.hooksPath`) or give it a directory.",
            )

    def _global_hooks_dir(self) -> Path:
        """The user's hooks directory, which must name one place and not depend on context.

        ``--get`` exits 1 when the key is unset, and exits **0 with empty output** when the
        key is set to the empty string (measured). Both mean the user has named no directory,
        so both fall back rather than one of them reporting a failure at status zero.

        ``--type=path`` expands a leading ``~`` to an absolute path but leaves a *relative*
        value exactly as written (measured: ``~/tilde`` came back ``/home/me/tilde`` while
        ``myhooks`` came back ``myhooks``), so after this call a value is absolute or it is
        relative, and a relative one is refused — see :meth:`_reject_relative_hooks_path`.

        ``.resolve()`` therefore only ever normalises an already-absolute path; it is kept so
        that ``..`` segments and symlinks collapse the same way they do on the repository
        branch, and it is pinned by a test using a value containing ``..``.
        """
        result = self._hooks_path_setting(global_=True)
        if result.rc not in (0, NO_ANSWER_RC):
            raise _failed(result)
        value = _decode(result.stdout).strip()
        if not value:
            return (_user_config_dir() / GLOBAL_HOOKS_FALLBACK).resolve()
        self._reject_relative_hooks_path(value)
        return Path(value).resolve()

    def _reject_relative_hooks_path(self, value: str) -> None:
        """Refuse a relative *global* ``core.hooksPath`` (decision, recorded here).

        ``hooks_dir(global_=True)`` answers "where would a globally-installed hook live" — one
        location, independent of where the operator happens to be standing. A relative value
        cannot express that, and there is no honest base to anchor it to:

        * Git does not treat it as global at all. Measured on 2.43.0, ``rev-parse --git-path
          hooks`` resolves a relative value **per repository, against that worktree's root** —
          ``myhooks`` from the root and ``../myhooks`` one level down. There is no single
          directory for this method to name.
        * Anchoring it to the process working directory, which is what an unguarded
          ``Path(value).resolve()`` does, makes the answer depend on an incidental property of
          the process: measured, one repository with one global setting produced three
          different answers from three directories, one of them inside an unrelated
          repository's working tree.

        Refusing names the problem instead of inventing semantics, and matches the empty-string
        decision on this same function. The repository branch is unaffected: there a relative
        value is meaningful and is resolved against the worktree root, exactly as git does.
        """
        if not Path(value).is_absolute():
            raise ConfigError(
                f"the global core.hooksPath {value!r} is relative, but a global hooks path "
                "must name one directory that does not depend on the current directory",
                key="core.hooksPath",
                hint="Give it an absolute path, or a `~/...` path, which git expands.",
            )

    def _hooks_path_setting(self, global_: bool) -> GitResult:
        """Read ``core.hooksPath``, from the user's configuration alone or from all of it.

        Both callers share this so the switches they must agree on are written once, and both
        scopes are exercised by tests: a version that asked only ``--global`` answered the
        working-tree root for a value set per-repository, which is the defect this signature
        exists to prevent.

        ``--type=path`` is what expands a leading ``~`` (measured: without it the value comes
        back as the literal ``~/hooks``), and is pinned by test.

        ``--get`` is a **known equivalent mutant by measurement, not by proof** — the
        distinction matters, because nothing in git's documentation guarantees the two forms
        stay identical. Measured across six states —
        unset, empty, ``~``-expanded, duplicate key, a valueless key, and a repository value
        shadowing a global one — status and stdout were byte-identical in every one (1/empty,
        0/empty, 0/expanded, 0/last-wins, 128/bad-config, 0/repository-wins). It is kept as
        the documented spelling and recorded here rather than left looking untested.
        """
        scope = ["--global"] if global_ else []
        return self._run(["config"] + scope + ["--get", "--type=path", "core.hooksPath"])

    # --- running ----------------------------------------------------------------

    def _run(
        self,
        argv: list[str],
        stdin: bytes | None = None,
        env: dict[str, str] | None = None,
    ) -> GitResult:
        """Run one git command in the repository root, recording it whatever happens.

        ``env`` replaces the whole environment for that one call, which is how the throwaway
        index reaches ``read-tree`` and ``checkout-index`` without ``GIT_INDEX_FILE`` leaking
        into any other call — a hook already has that variable set to the real index.
        """
        whole = [self.git, "-C", str(self.root)] + argv
        return _execute(whole, self.log, self.timeout_s, stdin, env)

    def _checked(
        self,
        argv: list[str],
        stdin: bytes | None = None,
        env: dict[str, str] | None = None,
    ) -> GitResult:
        """Run one git command and turn a non-zero status into a typed error."""
        result = self._run(argv, stdin, env)
        if result.rc != 0:
            raise _failed(result)
        return result


# --- helpers ------------------------------------------------------------------------


def _execute(
    argv: list[str],
    log: CommandLog,
    timeout_s: int,
    stdin: bytes | None = None,
    env: dict[str, str] | None = None,
) -> GitResult:
    """Run ``argv`` as bytes-in, bytes-out, and record the attempt even when it fails.

    ``check=False`` is a **known equivalent mutant**: it restates :func:`subprocess.run`'s
    default, so deleting it changes nothing. It is written out because every non-zero status
    here is interpreted rather than raised, and that is worth saying at the call site.
    """
    started = time.monotonic()
    try:
        done = subprocess.run(
            argv, input=stdin, capture_output=True, timeout=timeout_s, check=False, env=env
        )
    except subprocess.TimeoutExpired as expired:
        log.record(argv, time.monotonic() - started, TIMEOUT_RC)
        raise _timed_out(argv, timeout_s) from expired
    except OSError as broken:
        log.record(argv, time.monotonic() - started, MISSING_RC)
        raise _unrunnable(argv, broken) from broken
    seconds = time.monotonic() - started
    log.record(argv, seconds, done.returncode)
    return GitResult(argv, done.returncode, done.stdout, _decode(done.stderr), seconds)


def _name_status(argv: list[str], *revisions: str) -> list[str]:
    """The switches that make a diff a NUL-separated status stream with renames detected.

    **Two different terminators, guarding two different confusions.** The trailing ``--``
    separates revisions from *paths*, so a ref that is also a tracked filename is read as a
    ref. ``--end-of-options`` separates them from *options*, and nothing else does: measured,
    ``git diff --name-status -z -M --output=pwned.txt HEAD --`` exits 0, reports no changes,
    and writes ``pwned.txt`` into the working tree. Requirement 9.1 has the operator supply
    these revisions, so both confusions are reachable from ordinary input; with the terminator
    the same command exits 128 (``bad revision '--output=pwned.txt'``) and writes nothing.
    """
    head = argv + ["--name-status", "-z", "-M", "--end-of-options"]
    return head + list(revisions) + ["--"]


def _make_destination(dest: Path) -> None:
    """Create the export destination, turning a refusal into a typed error.

    ``mkdir`` raises a bare :class:`FileExistsError` when ``dest`` is an existing regular
    file, which was the one path in this module where an ``OSError`` escaped untyped and
    reached the caller as an unexpected-error exit rather than an analysis failure.
    """
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError as broken:
        raise AnalysisFailedError(
            f"the export destination {dest} could not be created: {broken}",
            hint="Point the cache at a directory, or remove whatever is in the way.",
        ) from broken


def _decode(raw: bytes) -> str:
    """Turn git's bytes into text without losing a path that is not valid UTF-8."""
    return raw.decode("utf-8", "surrogateescape")


def _encode(text: str) -> bytes:
    """The inverse of :func:`_decode`, so a round-tripped path still names the same file."""
    return text.encode("utf-8", "surrogateescape")


def _against(base: Path, value: str) -> Path:
    """Resolve one of git's answers, which may be relative to the directory it ran in.

    ``Path.__truediv__`` already discards ``base`` when ``value`` is absolute, so an explicit
    branch on absoluteness would be dead code — and git mixes the two forms freely: measured,
    ``--git-dir`` answers absolutely while ``--git-common-dir`` answers ``../../.git``.
    """
    return (base / value).resolve()


def _prefix(dest: Path) -> str:
    """``checkout-index``'s ``--prefix`` is concatenated, so it must end in a separator.

    Measured: ``--prefix=/tmp/dest`` writes ``/tmp/destmain.py`` beside ``/tmp/dest``. A
    forward slash is used on every platform because git accepts it in paths on Windows too,
    while a trailing backslash there would be swallowed by argument quoting.

    ``.resolve()`` is load-bearing for a **relative** ``dest``, which is the value class that
    makes this more than tidiness. Every git command here runs with ``-C <root>``, so a
    relative prefix would be interpreted against the *repository* and the export would land
    inside the working tree — the one thing requirement 2.2 forbids. Resolving it first
    anchors it to the caller's own directory, which is what the caller meant.
    """
    return f"{dest.resolve()}/"


def _user_config_dir() -> Path:
    """Where git keeps the user's configuration, and so where its hooks belong.

    ``XDG_CONFIG_HOME`` and ``HOME`` are operator-controlled path inputs, and a **relative**
    value in either would be anchored to the process working directory — putting the answer
    inside whatever repository the operator happens to be standing in. That is the same harm
    :meth:`GitRepo._reject_relative_hooks_path` refuses on the configured branch and the same
    that requirement 2.2 forbids, and it was reachable here: measured from a repository,
    ``XDG_CONFIG_HOME=myconf`` produced ``<repo>/myconf/git/hooks`` and ``~/tconf`` produced
    ``<repo>/~/tconf/git/hooks``, with a literal ``~`` component.

    The two inputs are treated differently because their specifications differ. The XDG Base
    Directory specification says a value that is not an absolute path "must be ignored", so a
    relative ``XDG_CONFIG_HOME`` silently falls back to ``$HOME`` — refusing would contradict
    the spec the variable comes from. ``HOME`` has no such fallback, so a relative one is
    refused rather than guessed at.

    Measured edge: ``HOME=""`` makes :meth:`Path.home` answer ``/``, which is absolute and so
    passes — nonsense, but outside any working tree, so it cannot cause the harm above.
    """
    configured = os.environ.get("XDG_CONFIG_HOME")
    if configured and Path(configured).is_absolute():
        return Path(configured)
    home = Path.home()
    if not home.is_absolute():
        raise ConfigError(
            f"HOME is set to the relative path {str(home)!r}, so the user's configuration "
            "directory would depend on the current directory",
            key="HOME",
            hint="Set HOME to an absolute path.",
        )
    return home / ".config"


def _status_letter(token: str) -> str:
    """The status of one record; ``R100``/``C085`` carry a similarity score after it."""
    letter = token[:1]
    if letter == UNMERGED_LETTER:
        raise AnalysisFailedError(
            f"the index holds unmerged paths (git reported {token!r})",
            hint="Finish the merge — resolve the conflicts and stage them — then run again.",
        )
    # `len(token) > 1` is a known equivalent mutant: for a one-character token the slice is
    # empty and `"".isdigit()` is already False, so the guard below reads the same either way.
    scored = len(token) > 1 and token[1:].isdigit()
    if letter not in STATUS_LETTERS or (len(token) > 1 and not scored):
        raise AnalysisFailedError(
            f"git reported the status {token!r}, which is not a status this build understands",
            hint="Report this with the output of `git diff --cached --name-status -z -M`.",
        )
    return letter


def _change(letter: str, paths: list[str]) -> StagedChange:
    """One record as a model; only a rename keeps the path it came from."""
    status = STATUS_LETTERS[letter]
    if len(paths) == 1:
        return StagedChange(status=status, path=paths[0])
    source, destination = paths
    return StagedChange(status=status, path=destination, old_path=source if status == "R" else None)


def _truncated(token: str, wanted: int, found: int) -> AnalysisFailedError:
    """The error a record with too few paths becomes; the stream cannot be read on."""
    return AnalysisFailedError(
        f"the status {token!r} needs {wanted} path(s) and the record carries {found}",
        hint="Report this with the output of `git diff --cached --name-status -z -M`.",
    )


def _failed(result: GitResult) -> AnalysisFailedError:
    """The error a non-zero status becomes, carrying the argv and what git said."""
    return AnalysisFailedError(
        f"{' '.join(result.argv)} failed with exit status {result.rc}",
        command=result.argv,
        stderr=result.stderr.strip(),
    )


def _timed_out(argv: list[str], limit: int) -> AnalysisFailedError:
    """The error a killed command becomes; ``TimeoutExpired`` is not an ``OSError``."""
    return AnalysisFailedError(
        f"{' '.join(argv)} timed out after {limit}s",
        command=argv,
        hint="A git command this slow usually means a lock is held by another process.",
    )


def _unrunnable(argv: list[str], broken: OSError) -> AnalysisFailedError:
    """The error an executable that never started becomes."""
    return AnalysisFailedError(
        f"{argv[0]} could not be run: {broken}",
        command=argv,
        stderr=str(broken),
        hint="Install git, or put it on PATH, and check with `scitools-hook doctor`.",
    )
