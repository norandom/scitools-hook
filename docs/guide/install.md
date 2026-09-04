# Install

## It is not on PyPI

`scitools-hook` is distributed as a **GitHub release**, not through PyPI, and it will not be
published there. Every install form therefore has to name the source.

```bash
uv tool install git+https://github.com/norandom/scitools-hook
```

To run it without installing anything permanently:

```bash
uvx --from git+https://github.com/norandom/scitools-hook scitools-hook --version
```

Or take the wheel from a tagged release. The release workflow attaches the sdist and the
wheel to the GitHub release for every `v*` tag, and flags the release as a pre-release
automatically when the tag contains `a`, `b` or `rc`:

```bash
gh release download v0.1.0a2 --repo norandom/scitools-hook --pattern '*.whl'
uv tool install ./scitools_hook-0.1.0a2-py3-none-any.whl
```

There is no trusted publisher, no API token, and nothing to configure outside the
repository. If you have published a package to PyPI you will expect that setup; its absence
here is deliberate.

!!! danger "These do not work, and one of them used to fail dangerously"

    ```bash
    pip install scitools-hook       # resolves to nothing
    uv tool install scitools-hook   # resolves to nothing
    uvx scitools-hook check         # resolves to nothing
    ```

    This was a live defect, not a documentation nicety. On a real 770-file repository the
    installed pre-commit shim fell back to a bare `uvx scitools-hook`, which resolved to
    nothing — so **every commit was blocked**. Worse: `uvx`'s resolution failure exits **1**,
    and 1 is this gate's code for "blocking violations found". The operator was told their
    code was bad when the tool was simply absent, and `SCITOOLS_HOOK_SOFT_FAIL` could not
    rescue them, because soft-fail only covers exit 2 and above.

    Both halves are fixed. The installed shim uses `uvx --from <source>`, and a resolution
    failure is re-mapped to exit **3** (infrastructure) rather than borrowing the findings
    code. See [Hooks and CI](hooks-and-ci.md#what-the-shim-actually-does).

!!! warning "The PyPI package named `understand` is not SciTools Understand"

    Do not `pip install understand`. It is an unrelated project. This tool uses the API
    shipped inside your Understand installation.

## What else you need

| Requirement | Notes |
| --- | --- |
| SciTools Understand &ge; 6.5, licensed | Commercial. The gate never installs it and never bundles it. Everything in these documents was measured against **Build 1204** (`6.5.1204`). |
| A bare `python` reachable on `PATH` | Only if you drive `und` yourself. The gate supplies its own; see [Operations](../reference/operations.md#understand-picks-the-python-dialect-by-running-python). |
| `git` | Staged-file discovery, the before-state export, and hook installation. |
| Python &ge; 3.12 | To run the tool itself. `uv` will fetch one. |

## Point it at Understand

The installation is resolved in this order. The first one that yields a usable installation
wins:

1. `--scitools-home DIR` on the command line
2. the `SCITOOLS_HOME` environment variable
3. `understand.home` in the configuration file
4. `und` on `PATH`
5. a per-platform list of well-known directories

The well-known directories are:

| Platform | Searched |
| --- | --- |
| Linux | `~/scitools`, `/opt/scitools`, `/usr/local/scitools` |
| macOS | `/Applications/Understand.app/Contents/MacOS` |
| Windows | `C:\Program Files\SciTools` |

An unrecognised platform has none, because Understand ships no build for it and guessing a
directory would put a location in the "tried" list that never could have worked.

If nothing is found, the gate exits **3** and lists every location it tried, with the source
that named it:

```text
no SciTools Understand installation was found in 5 location(s)
  env:SCITOOLS_HOME: /opt/understand
  path: /usr/local/bin
  wellknown:/home/you/scitools: /home/you/scitools
  ...
hint: Set the installation directory with --scitools-home, the SCITOOLS_HOME environment
variable, or understand.home in the configuration file.
```

## Check it works

`doctor` is the command to run when something is wrong. It reports rather than judging, and
it **always exits 0** — because exit 1 is already spent on "blocking violations found", and
a CI job running `doctor` must not be told a commit had violations that were never measured.

```console
$ scitools-hook doctor
scitools-hook
  version:           0.1.0a1
  python:            3.14.4

Understand
  installation:      /home/mc/scitools
  found by:          env:SCITOOLS_HOME
  und:               /home/mc/scitools/bin/linux64/und
  upython:           /home/mc/scitools/bin/linux64/upython
  python api:        /home/mc/scitools/bin/linux64/Python
  und version:       (Build 1204)
  license:           ok
  api mode:          upython
  probe upython:     ok (6.5.1204)
  probe inprocess:   ok (6.5.1204)
  analysis python:   /home/mc/Source/scitools-hook/.venv/bin/python3 (3.14.4)

Repository
  inside a repository: yes
  root:              /home/mc/Source/scitools-hook
  git directory:     /home/mc/Source/scitools-hook/.git
  HEAD:              d49d9b5bce219dd29129fc3fc2d6f4fc24a56056

Analysis cache
  cache root:        /home/mc/.cache/scitools-hook/7bb1a9ede8d2b6d9
  after database:    /home/mc/.cache/scitools-hook/7bb1a9ede8d2b6d9/after.und
  before database:   /home/mc/.cache/scitools-hook/7bb1a9ede8d2b6d9/before.und
  sync state:        /home/mc/.cache/scitools-hook/7bb1a9ede8d2b6d9/state.json
  after target:      index (483373f949466958089d40e535053a525330cd9e)
  before commit:     none
  languages:         Python
  built with:        (Build 1204)

Problems
  none
```

Four rows in that output are worth understanding before you need them.

**`und version` says `(Build 1204)` and nothing else.** That is what `und version` prints on
this build. No product version. The Python API, asked separately, reports `6.5.1204`, which
is why the two probe rows show a different string from the `und version` row.

**Both API probes are reported, not just the one in use.** The normal resolution path stops
at the first mode that works. A diagnosis has the opposite job, so `doctor` runs both. This
is safe because the in-process import is run in a child process.

**`analysis python` is the row that prevents a silent false negative.** It names the
interpreter `und` will use to decide the Python dialect. If that interpreter is not Python 3,
Understand analyses Python 2 and every routine after the first modern construct disappears
from the database without an error. The row is printed on every run precisely because the
defect it guards against is that two machines analyse one commit to different depths and
nothing in the output says so.

**`built with` is how an Understand upgrade heals itself.** Both databases are discarded and
rebuilt when the Understand version or the enabled language set changes, rather than the
next run meeting a database the new build refuses to open.

## The licence

Two facts that are not obvious and that will cost you an afternoon if you assume otherwise.

**It is offline, and it is bound to the hostname.** Measured three ways: `unshare -rn und
-isundlicensed` prints `1`, so it is licensed with no network at all; with `HOME` relocated it
still prints `1`; under `unshare -u` with the hostname changed it prints `0`; restoring the
hostname inside the same namespaces prints `1` again.

So a container or a CI runner that wants to run the licensed path must pin `--hostname` to
the licensed machine's name. Moving `HOME` is fine. Renaming the machine is not.

!!! note "Provenance of that claim"

    This is a measurement of the installed licence on one machine, recorded in this
    repository's `README.md`, in its Dagger module and in its task log. It is not enforced or
    checked anywhere in the shipped code, and it is not a statement about how SciTools
    licensing works in general — check your own licence terms.

    The project's own history is worth repeating here, because it is the reason the claim is
    stated this carefully: an earlier version of it said the licence was floating and needed
    a heartbeat. That was inferred from the field names `heartrate`, `canCheckout` and
    `lastfailedheartbeat*` in `License.conf`, was never measured, and was simply wrong.

**A licence failure has its own exit code.** The gate exits **4** and quotes what `und` said,
rather than folding it into a general analysis failure:

```text
SciTools Understand reports that no valid license is available
hint: Check the license with `und license`, or set one with `und -setlicensecode`.
```

A licence error is never retried and never falls back, because a retry cannot produce a
licence.

## Versioning

The current version is `0.1.0a1`. It is an alpha; see
[the maturity table](../index.md#maturity).

`__version__` is read from the installed distribution metadata rather than written into the
source, so the CLI cannot report a different number from the artifact it came from. That
mattered: the two had already drifted once, with `pyproject.toml` at `0.1.0a1` while the
module still said `0.1.0`, and the wheel installed, ran, and reported the older number. That
string is not cosmetic — it travels into `tool_version` on every SARIF report the gate
writes, so a stale copy misattributes findings to a version that never produced them.

Running from an uninstalled source tree reports `0+unknown`, deliberately, rather than a
plausible-looking number.
