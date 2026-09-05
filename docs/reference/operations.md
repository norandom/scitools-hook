# Operations: what breaks, and why

Everything on this page was measured against Understand **6.5.1204** on Linux. Most of it is
invisible until it costs you something, and several items are silent false negatives — a run
that reports success over code it never read.

If you are wiring `und` into something yourself rather than using this tool, read the first
section at minimum.

## Understand picks the Python dialect by running `python`

**`und` decides which Python dialect to parse by *executing* a bare `python` it finds on
`PATH`. When there is none, it analyses under a Python 2 model.**

Measured on identical sources, the same `und`, with only `PATH` differing:

| `python` on `PATH` | Result |
| --- | --- |
| present | `Errors:0`, routines `['after', 'before']` |
| absent | `Errors:8`, routines `['before']`, and `has_key` / `iteritems` / `raw_input` in the database |

Look at the second row again. The routine after the parse failure is not an error. It is an
**absence**. It has no metrics, so it breaks no threshold, so nothing is reported and the run
exits 0.

That is the worst failure a gate can have, and it is the default behaviour of a machine that
has `python3` but no `python`.

### The name is the whole difference

A directory holding only `python3` gives the Python 2 model. The **same real interpreter**
symlinked under the name `python` gives the Python 3 model. Nothing else changes.

### What this tool does about it

It supplies its own. Every `und` invocation runs with a `PATH` whose first entry is a
`0700` temporary directory containing one symlink named `python`, pointing at
`sys.executable`.

`sys.executable` rather than a search, and the reason is availability rather than taste:
"missing" is the one input that must be impossible, and the only interpreter guaranteed to
exist, to be executable and to be Python 3 is the one the process is already running.

The minor version does not matter. Understand's fallback is binary — a Python 2 model or a
Python 3 model — and 3.12.3 and 3.14.4 were both measured producing the Python 3 model.

Three further details, each of which was measured because assuming otherwise was wrong:

**Per invocation, not per process.** A database analysed under the Python 3 model reverts to
the Python 2 model on the very next `analyze` run without a `python` — eight parse errors,
`has_key` back in the database, the routine after the failure gone again. Nothing is
remembered in the database or its settings, so nothing but pinning *every* call holds the
dialect.

**Prepending, not replacing.** Measured in both directions: a decoy `python` printing
`Python 2.7.18` placed *before* the link gives the Python 2 model; the same two directories in
the other order give the Python 3 model.

**`PATH` alone is not enough.** Three environment variables were each measured defeating the
pin:

| Variable | What it did |
| --- | --- |
| `PYTHONHOME=/nonexistent` | The pinned link dies with a fatal error before printing anything, `und` reads that as "no python", and the database comes back **Python 2**. |
| `PYTHONPATH=<repo>/src` | On this repository's 172 files: **1272 intra-tree edges without it, 66 with it** — the same 66 an unpinned run gives. That is the whole of `structure.new_dependencies` and `structure.fan` switched off by one variable, with nothing in the report naming it. |
| `PYTHONUSERBASE` pointing at a `site-packages` with a `.pth` | The same leak, which is why `PYTHONNOUSERSITE` is *set* rather than merely cleared. |

The pin therefore strips every `PYTHON*` variable — the environment spelling of `python -E`,
as a prefix rule rather than a list — and sets `PYTHONNOUSERSITE=1`, the spelling of
`python -s`. Everything non-Python reaches `und` untouched, because `und` reads its licence
from `HOME` and its configuration from the rest of the environment.

**A pin that cannot be built is a refusal, not a fallback.** An `und` given an uncontrolled
`PATH` may analyse Python 2 and report success over code it never read, and a failure an
operator can see is worth more than a green run nobody can question.

### Why not `und settings -PythonExe`

It fixes the dialect too, and it writes `~/.config/SciTools/Und.conf`, which becomes the
default for **every database created afterwards on the machine** and is rewritten on every
run.

Two consecutive runs of one identical command were measured producing **316 and 231
findings** before that key was controlled.

### What the pin costs you, deliberately

The pinned interpreter is a bare one, so Understand cannot see your third-party packages.
Measured: with the gate's own virtual environment `python` directly on `PATH`, a run
**enrolled 365 files, 224 of them from `site-packages`**, and a class deriving from
`pydantic.BaseModel` scored `MaxInheritanceTree` **5**. Through the pinned link, the same
sources **enrolled 2 files** and the same class scored **1**.

That is why `class.MaxInheritanceTree` ships as a warning. See
[Rules and defaults](rules.md#classmaxinheritancetree-measures-where-a-base-class-lives).

`doctor` prints the interpreter on every run:

```text
analysis python:   /home/mc/Source/scitools-hook/.venv/bin/python3 (3.14.4)
```

The whole defect that row exists for is that two machines analyse one commit to different
depths and *nothing in the output says so*.

## A file that does not parse is a blocking finding

Not a warning. A file the analyser could not read must never report as clean.

```console
$ scitools-hook check --staged
parse errors: these files were NOT fully checked
  Understand could not finish parsing them. Code after a parse error can be missing
  from the analysis, so no rule ran on it: what follows covers only the code that parsed.
  A file in this run's selection that failed to parse is also a blocking analysis.parse_error
  finding below; one outside it -- the interpreter's own standard library, say -- is not.
  pricing/generic.py
    line 4: expected token '(' at token [
    line 5: expected token ':' at token indent
    line 9: expected identifier at token dedent
    ...

pricing/generic.py
  error    analysis.parse_error  line 4
    Understand could not read pricing/generic.py: 6 parse errors, the first at line 4:
    expected token '(' at token [. The analysis stops where the parse stops, so the code
    after it is absent from the database and no rule ran on it -- this file cannot be
    reported as checked.
    hint: PEP 695 type parameters: Understand 6.5 cannot parse a type-parameter list, and
    one of them costs the rest of the file. Declare the variable explicitly instead --
    `T = TypeVar("T")` at module level, then `def generic(x: T) -> T:` and
    `class Box(Generic[T]):` -- which is the same type with a spelling the analysis reads

summary: 1 error, 0 warnings, 0 pre-existing, 1 blocking | 1 file failed to parse, not fully checked | exit 1: blocking violations found
```

Measured on this repository: one PEP 695 type-parameter list took `config/models.py` from
**15 classes to 3** in the database, **hid 12 findings and fabricated 2**, and the run exited
0.

Two boundaries keep this usable:

- **Only the after side.** A before-side error that this change *fixed* would otherwise block
  the very commit that fixed it.
- **Only files in the selection.** A clean run of this repository was measured producing four
  parse errors inside the interpreter's own standard library. Blocking a commit over
  `typing.py` would get the gate switched off. Those are reported and do not block.

### The constructs that fail, on 6.5.1204

Measured on 6.5.1204 with a Python 3 interpreter on `PATH`; on 8.0.1262 all thirteen parse with
zero errors (2026-09-05), so the rest of this section describes a 6.5 install. Four
declarations of thirteen tried aborted
the parse:

```text
def generic[T](x: T) -> T:   expected token '(' at token [        line 1
class Box[T]:                expected token ':' at token [        line 1
type Alias = int             expected newline at token Alias      line 1
except* ValueError:          expected token ':' at token *        line 4
```

The failure modes differ:

| Construct | The routine *after* it |
| --- | --- |
| `def f[T]`, `class C[T]`, `async def f[T]`, `def f[**P]` | **gone from the database** — the error cascades to end of file |
| `type A = int`, `type A = int \| str`, `type A[T] = list[T]` | present — one error, that line only |

Detection uses an `ast` walk rather than a regular expression, because a regular expression
for `def f[T](` matches this project's own hint catalogue. A file holding both kinds is
reported as the truncating one.

One entry on that list is a red herring worth knowing about: `[first, *rest]` *does* fail
under the Python 2 dialect, which is how it came to be recorded as a hazard. It is not a 3.12
problem at all.

### Parse errors survive a warm run

`und analyze` is incremental, so a warm run re-parses nothing and reports nothing.

Measured on this repository: a cold staged run named **9 unparsed files**, and three
consecutive warm runs over the same two databases — still holding the same unparseable files
— named **none**. A git hook is always warm.

So the sync state carries each side's errors between runs, and every analysis rewrites only
the part of that record it actually re-read. A full pass replaces the side's whole set; a
`-files` pass replaces the entries of the files it named, so fixing a file clears its errors,
and carries the rest forward untouched.

## The licence

**Offline, and bound to the hostname.** Measured three ways:

| Test | Result |
| --- | --- |
| `unshare -rn und -isundlicensed` | `1` — licensed with no network at all |
| the same, with `HOME` relocated | `1` |
| `unshare -u`, hostname changed | **`0`** |
| the hostname restored inside the same namespaces | `1` |

A container or a CI runner that wants the licensed path must pin `--hostname` to the licensed
machine's name. Moving `HOME` is fine.

!!! note "Provenance"

    This is a measurement of one installed licence on one machine. It is recorded in this
    repository's `README.md`, its Dagger module and its task log; it is not enforced or
    checked anywhere in the shipped code, and it is not a statement about SciTools licensing
    in general.

    An earlier version of this claim said the licence was floating and needed a heartbeat.
    That was inferred from the field names `heartrate`, `canCheckout` and
    `lastfailedheartbeat*` in `License.conf`, was never measured, and was simply wrong.

A licence failure exits **4** and is never retried and never falls back, because a retry
cannot produce a licence.

A separate licence line covers CodeCheck. On a licence that excludes it, `und codecheck`
answers `Licensing Error: No license for CodeCheck.` and writes nothing.

### `NoApiLicense`, `No Server Response`, "requires a license with exporting enabled"

All three are licensing, all three are the operator's to fix with `und` itself, and none is
this tool's to work around. See [licensing is done from the command
line](../guide/install.md#licensing-is-done-from-the-command-line). The option the gate needs
is **API Access**; `doctor` lists the enabled options and names it when it is missing. On 8.0
CodeCheck refuses with *"No checks in this configuration are licensed to run"*, which the
wrapper reports as a licence refusal (exit 4) rather than a broken analysis.

## API modes

The Python API is reached in one of two ways, and `--api-mode` / `understand.api_mode`
selects which.

| Mode | What it does |
| --- | --- |
| `upython` | Runs `upython worker.py <op>` as a subprocess, request JSON in, answer JSON out. |
| `inprocess` | Imports the `understand` module into the host interpreter and calls the same functions. |
| `auto` | Tries `upython` first, falls back to `inprocess`. The default. |

`auto` prefers `upython` for a measured reason. The in-process import itself is **not**
broken: with a licence active, `import understand`, `open()`, entity iteration, `metric()`
and `close()` all succeed, on both `/usr/bin/python3.12` and CPython 3.14.4.

`Ent.draw` is the exception. In-process it dies with:

```text
symbol lookup error: <home>/bin/linux64/Perl/auto/Fcntl/Fcntl.so:
  undefined symbol: Perl_xs_handshake
```

status 127. Drawing loads Understand's bundled Perl and Qt stack, which resolves only under
`upython`. In-process that abort kills the **gate**, not one operation, so the `graphs`
operation is routed to `upython` whatever the mode is.

A forced mode runs only its own probe. The in-process probe is the dangerous one, and an
operator who forced `upython` must never have it run behind their back.

There is one more oddity, at the other end of a drawing run. After `Ent.draw` has rendered a
dependency graph, the bundled interpreter aborts at shutdown with `Fatal Python error:
PyInterpreterState_Delete: remaining subinterpreters` and dies of `SIGABRT`. The answer is
complete and correct on standard output by then; only the exit status is destroyed. Drawing a
butterfly graph *first* happens to avoid it and drawing one afterwards does not, so there is
no ordering a caller could rely on. The worker therefore flushes and calls `os._exit(status)`.

Never `pip install understand`. The PyPI package of that name is unrelated to SciTools
Understand.

## Databases

Two per repository — `before` and `after` — kept outside the working tree, under the per-user
cache directory by default:

```text
/home/you/.cache/scitools-hook/7bb1a9ede8d2b6d9/after.und
/home/you/.cache/scitools-hook/7bb1a9ede8d2b6d9/before.und
/home/you/.cache/scitools-hook/7bb1a9ede8d2b6d9/state.json
```

`understand.db_location = "gitdir"` puts them in `.git/scitools-hook/` instead. Neither is
committed.

The cache root is created `0700` **before** anything is written into it, and the mode is set
explicitly rather than left to the umask, because the databases and shadow trees are copies
of your source. A database created into a world-readable directory is readable for as long as
it takes to fix.

Both databases are discarded and rebuilt when the enabled language set or the Understand
version changes. Without that, the first run after an Understand upgrade meets a database the
new build refuses to open, and the operator has to work out that `db rebuild` is the answer.

You will see the language-set case in ordinary use: adding one `.html` file to a Python
repository takes the run from `created the after analysis database with Python enabled` to
`with Python, Web enabled`, which is a full rebuild.

## `und version` does not print a version

```console
$ und version
(Build 1204)
```

No product version. The Python API, asked separately, reports `6.5.1204`. `und -version` and
`und --version` are not switches this build knows.

So if you are parsing that output, do not expect a `6.5.x` string. `doctor` shows both, which
is why its `und version` row and its `probe` rows disagree.

## Understand's own quirks that the extension map exists for

`.sql` enters under **Pascal**. `.m` and `.mm` are **C++**. The map is case-sensitive: `a.C`
is C++ and `a.PY` is nothing at all. `.txt`, `.pl`, `.bat` and `.cbl` are absent although
Understand's `FileTypes` table names them, and `.bas` is absent while `.vb` is present.

None of that is derivable from the table. It was measured by building a database per language
over a tree holding one file per extension and reading `und list files` back, and a contract
test re-measures it against the installed build in both directions.

`c++` is also not a kind-string language inside Understand: `Metric.list("c++ file …")`
answers nothing while `Metric.list("c file …")` answers 42, and the kind long names read
`C Class Type`. Exactly one alias closes that gap; without it a C++ repository has no
available metric and every threshold it configures is rejected.

See [Languages](../guide/languages.md).

## When something is wrong

```bash
scitools-hook doctor          # always exits 0; read the Problems section
scitools-hook --verbose ...   # external commands, timings, tracebacks
scitools-hook db rebuild      # discard both databases and analyse again
scitools-hook db path         # works even when Understand does not
```

`doctor` uses a 60-second timeout on its `und` calls rather than the wrapper's 900, because
it is the command an operator runs when things are already broken and a wedged `und` would
otherwise delay the report by up to half an hour.
