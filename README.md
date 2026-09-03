# scitools-hook

A maintainability gate for git pre-commit hooks, CI and coding agents, backed by
[SciTools Understand](https://scitools.com/).

It measures the routines, classes and files a commit touches, compares each one against what
it was before the commit, and refuses the commit that makes any of them measurably worse.
Debt that was already there reports as `pre-existing` and does not block; only regression
blocks. Because the analysis is Understand's, it covers twelve languages, not just the one
the tool is written in.

**Documentation: <https://norandom.github.io/scitools-hook/>**

## Install

**This tool is not on PyPI and will not be.** It is distributed as a GitHub release, so
every install form has to name the source:

```bash
uv tool install git+https://github.com/norandom/scitools-hook
```

To run it without installing anything permanently:

```bash
uvx --from git+https://github.com/norandom/scitools-hook scitools-hook --help
```

`pip install scitools-hook`, `uv tool install scitools-hook` and a bare `uvx scitools-hook`
all resolve to nothing. The installed pre-commit shim uses the `--from` form, and re-maps a
`uvx` resolution failure to exit 3 rather than letting it borrow exit 1, which is this gate's
code for "blocking violations found".

Or take the wheel from a tagged release, which the release workflow attaches to it:

```bash
gh release download v0.1.0a1 --repo norandom/scitools-hook --pattern '*.whl'
uv tool install ./scitools_hook-0.1.0a1-py3-none-any.whl
```

`scitools-hook` needs an existing SciTools Understand installation (`und` and the
bundled Python API). It never installs Understand for you.

> **Note:** the PyPI package named `understand` is unrelated to SciTools Understand.
> Do not `pip install understand`; this tool uses the API shipped inside your
> Understand installation.

## Development

The project's gates run in one command, and none of them needs an Understand licence:

```bash
uv run pytest --cov=src/scitools_hook --cov-branch --cov-report=term-missing --cov-fail-under=85
```

That single invocation covers all four gates:

| Gate | Where it lives |
|------|----------------|
| Import-direction (the allowed-import matrix, and `worker.py` under `python -I`) | `tests/test_import_direction.py` |
| `ruff check` and `ruff format --check` | `tests/test_quality_gates.py` |
| `mypy --strict` (configured: `strict = true`, `files = ["src"]`) | `tests/test_quality_gates.py` |
| Branch coverage of `src/`, threshold 85% | the `--cov-*` flags above |

The coverage threshold applies to **all** of `src/scitools_hook` with no omissions: the
adapters that only run against a licensed install are covered through their fixture-backed
fakes, so no module has to be excluded to reach the number.

The suite is licence-free by construction — the tests marked `contract` are skipped unless
`SCITOOLS_HOME` points at a licensed install:

```bash
SCITOOLS_HOME=/path/to/scitools uv run pytest -m contract
```

## Container gates (Dagger)

`.dagger/` holds a Dagger module that runs the licence-free gates in a pinned container
(`python:3.14.7-slim`, `uv` 0.12.9) and builds the release artifacts:

```bash
dagger call static            # ruff check, ruff format --check, mypy -- fails in seconds
dagger call tests             # the gate command above, coverage threshold included
dagger call build-dist        # sdist + wheel
dagger call verify-wheel      # install the wheel where there is no source, and run the CLI
dagger call licensed-inventory  # name the licensed tests this path is not running
dagger call ci                # all of it: fail fast, publish nothing that cannot run
```

The gates are staged so the cheap one fails first: a lint or type error aborts before the
~3-minute test run rather than after it.

The decision layer — which gates run, in what order, what aborts the run, and what may be
published — is a pure function (`compose_ci` in
`.dagger/module/src/scitools_hook_ci/main.py`) with no Dagger import, so
`tests/test_dagger_pipeline.py` checks it in the ordinary suite on a machine that has
neither Dagger nor Docker.

### What the container path does not run, and where it does run

The `contract`-marked tests need a licensed SciTools Understand, which a hosted runner does
not have, so they skip there. The skip is printed rather than left silent — `dagger call
ci` and the release workflow both list, by name, every licensed test that did not run.

They do run:

```bash
SCITOOLS_HOME=/path/to/scitools uv run pytest        # a developer machine
.dagger/licensed/run.sh                              # the whole suite, in a container
```

`.dagger/licensed/run.sh` runs the entire suite — contract tests included — inside
`docker run --network none`. Measured on this tree: 3633 passed, 5 skipped, 1 xfailed,
branch coverage 98.19%, with every outbound connection attempt refused. The container reads every file in the repository, so having no
network is a security boundary rather than a tuning knob, and the container proves it before
it reads anything: it opens outbound connections, requires every one of them to fail, and
aborts the run if any succeeds. The Understand installation and the licence directory are
bind-mounted read-only and are never copied into an image layer.

It is a `docker` invocation rather than a Dagger `@function` for a measured reason: Dagger
v0.21.9's `Container` type has no field that disables networking (82 fields, none matching
"net"), so a Dagger container cannot honour that boundary.

It reads `SCITOOLS_HOME` (default `~/scitools`) and `SCITOOLS_LICENCE_DIR` (default
`~/.config/SciTools`), and mounts the installation at `/srv/understand` rather than
`/opt/scitools` — the latter is one of the locator's well-known search locations, so mounting
it there makes every "no installation was found anywhere" test find one.

Three other things the container has to get right, each of which was found by running the
suite rather than by reasoning about it. It runs as a non-root user, because root ignores a
directory's permission bits and every test that builds an unreadable fixture then passes for
the wrong reason. It links `/usr/bin/python3`, because one test runs a
`#!/usr/bin/env python3` stub with an empty environment and `/usr/local/bin` is not on the
PATH a process inherits then. And it starts `Xvfb`, because Understand draws graphs with a
Qt binary whose only shipped platform plugin is xcb: with no display the graph tests come
back empty with the loader's complaint folded into a warning, which is a silent red.

The licence turns out to need the machine's **hostname**, not the network — measured:
`unshare -rn und -isundlicensed` prints `1`, while changing only the hostname prints `0` —
which is why the script passes `--hostname`.

## Documentation

The site at <https://norandom.github.io/scitools-hook/> is built from `docs/` with
[mkdocs-material](https://squidfunk.github.io/mkdocs-material/). Build it locally:

```bash
uv run --no-project --with mkdocs-material==9.7.7 mkdocs serve
uv run --no-project --with mkdocs-material==9.7.7 mkdocs build --strict
```

`--no-project` is deliberate: mkdocs is not a dependency group of this package, because
`gate.yml` runs `uv sync --locked` and a documentation dependency would couple every docs
change to `uv.lock`. `--strict` turns a broken internal link, a dangling anchor or a page no
nav entry reaches into a build failure, and `.github/workflows/docs.yml` runs it on every
pull request that touches `docs/`.

## Releasing

`.github/workflows/release.yml` builds the sdist and wheel, runs the built wheel, and
attaches both to a **GitHub release** on a `v*` tag. Nothing is published to PyPI, so there
is no trusted publisher, no API token and nothing to configure outside this repository; a tag
containing `a`, `b` or `rc` is flagged as a pre-release automatically. It calls `gate.yml`
rather than repeating its command, so a tag is still gated (a tag push does not match
`gate.yml`'s branch filter on its own).

Cutting a release:

```bash
uv version --short                 # confirm pyproject.toml says what you intend to tag
uv build --out-dir dist            # sdist + wheel
git tag -a v0.1.0a1 -m "0.1.0a1"   # the tag must equal the packaged version
git push origin v0.1.0a1           # this is what starts the release job
```

The workflow refuses to publish a wheel it could not start, and refuses one whose
`scitools-hook --version` disagrees with the version it was packaged as.
