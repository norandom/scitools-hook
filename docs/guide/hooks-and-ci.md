# Hooks and CI

## The push boundary

`pre-commit` judges what is staged. `pre-push` judges what the commits being pushed did — at
push time nothing is staged and the working tree is beside the point, so the only honest
question is the range.

```bash
scitools-hook install-hook --pre-push
```

For each ref git offers, the shim runs `check --range <remote oid>..<local oid>`. Two cases
carry no range and are reported rather than guessed at:

| Case | What happens |
| --- | --- |
| A ref being **deleted** (local oid all zeros) | Nothing to judge; skipped silently. |
| A branch the **remote does not have yet** (remote oid all zeros) | No before side. A note on stderr; not checked. Inventing a base would judge commits this push is not responsible for. |

It is a separate hook from `pre-commit`, installed and removed on its own, so you can have
either or both:

```bash
scitools-hook install-hook                 # the commit boundary
scitools-hook install-hook --pre-push      # the push boundary
scitools-hook uninstall-hook --pre-push    # and only that one
```

`SCITOOLS_HOOK_SKIP=1` and `SCITOOLS_HOOK_SOFT_FAIL=1` work as they do for `pre-commit`, and
`git push --no-verify` skips every hook. Findings (exit 1) refuse the push whatever
`SOFT_FAIL` says; only an infrastructure failure is downgradable.

A chained `pre-push` hook is handed the ref list on its own standard input. The shim reads
those lines with shell builtins and replays them — a hook that consumed them would leave the
chained one believing nothing was pushed.

!!! note "One ref's failure cannot mask another's"

    The shim keeps *findings* and *could not run* apart rather than reducing them to a worst
    status, so one ref's missing licence plus `SOFT_FAIL` cannot excuse another ref's real
    findings.

## Two ways to install the hook

### Native git hook

```bash
scitools-hook install-hook
```

```console
installed the pre-commit shim at /tmp/pricing/.git/hooks/pre-commit
```

`--global` installs into the user's global hooks path instead of the repository's, so every
repository on the machine is gated. `uninstall-hook` removes it and restores whatever it
replaced.

If a `pre-commit` hook was already there, it is kept beside the shim as
`pre-commit.scitools-hook-chained` and run at the end of the shim, so installing the gate
never switches off what you had. `install-hook --force` is what replaces an existing hook you
did not install.

### The `pre-commit` framework

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/norandom/scitools-hook
    rev: v0.1.0a6
    hooks:
      - id: scitools-hook
```

The hook definition sets `require_serial: true`, and that is mandatory rather than tidy.
Without it the framework shards the staged file list across parallel invocations, and each
one would build and analyse its own Understand database of the same repository.

Its `entry` is a bare `scitools-hook check` rather than `check --files`. Both work — trailing
bare paths mean the same thing as `--files` — but `--files` with no value is a usage error, so
the bare form is the one that also survives a run where the framework passes no paths at all.

## What the shim actually does

The installed shim is a small POSIX `sh` script, exercised under `dash`. It contains no
thresholds and no analysis logic, so changing a limit never means reinstalling it.

Its resolution order:

1. `scitools-hook` on `PATH`, if there is one.
2. Otherwise `uvx --from 'git+https://github.com/norandom/scitools-hook' scitools-hook check --staged`.
3. Otherwise a message saying nothing was checked, and exit 3.

The `--from` is load-bearing, and its absence was a live defect:

```sh
# `uvx scitools-hook` alone CANNOT work: this tool is not on PyPI and never will be,
# so the bare name resolves to nothing. It is published as a GitHub release, hence
# --from. Measured on a real repository before this was fixed: every commit was
# blocked, because uvx's resolution failure exits 1 and 1 is the Gate's code for
# "blocking violations found" -- so the operator was told their code was bad when
# the tool was simply absent, and SCITOOLS_HOOK_SOFT_FAIL could not help them.
```

On a 770-file repository, that meant every commit was refused with a findings report the
operator could not act on, and the only way to commit at all was to put a checkout's virtual
environment on `PATH`.

Both halves are fixed. The shim substitutes the release source at install time, and it tells
a resolution failure apart from a findings failure:

```sh
uvx --from 'git+https://github.com/norandom/scitools-hook' scitools-hook check --staged
status=$?
if [ "$status" -eq 1 ] && ! uvx --from '...' scitools-hook --version >/dev/null 2>&1; then
    note 'scitools-hook: uvx could not resolve the tool, so nothing was checked.'
    note 'hint: install it with `uv tool install git+https://github.com/norandom/scitools-hook`, or set SCITOOLS_HOOK_SOFT_FAIL=1.'
    status=3
fi
```

Exit 1 means findings. Exit 3 means the tool was not there. Those must not share a code,
because `SCITOOLS_HOOK_SOFT_FAIL` covers exit 2 and above and deliberately does not cover
findings.

The source is substituted at install time rather than written into the template, so a fork
installs its own.

The header of every installed shim records which of the three paths was available when it was
installed:

```sh
# At install time the Gate resolved to: scitools-hook, found on PATH
```

The three possible strings are fixed, so nothing from the environment — a directory name, a
user name, a version — can reach the script's text.

## The two environment variables

```bash
SCITOOLS_HOOK_SKIP=1 git commit -m "..."
```

Skips the gate's check for this one commit, and prints a notice saying so. A chained hook
still runs — the variable turns off the gate, not somebody else's hook. `git commit
--no-verify` is what skips every hook.

```bash
export SCITOOLS_HOOK_SOFT_FAIL=1
```

Turns an infrastructure failure into a warning: exit 2 and above, which is the tool missing,
Understand missing, a broken configuration, or a report that could not be written. **Findings
still block**, whatever this is set to, because they are the answer the gate exists to give.

## Exit codes

The shim's `case` is written as "0, 1, and everything else", so there is no boundary to get
wrong and the `128 + signal` statuses of a crashing gate are covered too.

| Code | Meaning | Soft-fail covers it? |
| ---: | --- | --- |
| 0 | No blocking violations | — |
| 1 | Blocking violations found | **No.** This is the answer. |
| 2 | Configuration error (unknown key, metric, scope, regex, architecture) | Yes |
| 3 | No usable SciTools Understand installation found | Yes |
| 4 | Understand reported no valid license | Yes |
| 5 | Analysis failed (`und` error, timeout, unusable database) | Yes |
| 6 | Not inside a git repository | Yes |
| 7 | The analysis ran but its report could not be delivered | Yes |
| 70 | Unexpected internal error | Yes |

## In CI

The gate needs a licensed Understand, which a GitHub-hosted runner does not have. There are
three honest options.

### A self-hosted runner

The licence is offline but **bound to the hostname**, so a container or a runner must match
the licensed machine's name. See [Install](install.md#the-licence).

```yaml
jobs:
  maintainability:
    runs-on: [self-hosted, understand]
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0          # the ratchet needs HEAD's parent
      - run: scitools-hook check --all --sarif findings.sarif
      - uses: github/codeql-action/upload-sarif@v3
        if: always()
        with:
          sarif_file: findings.sarif
```

`--sarif` writes SARIF 2.1.0, so findings land in the same GitHub code-scanning view as
CodeQL results. `--format sarif` sends it to standard output instead; `--sarif PATH` writes
it *in addition to* the human report.

### On the pull request's range

`explain --range A..B` gives a reviewer the structural picture of a branch:

```bash
scitools-hook explain --range "origin/main...HEAD" --graphs --impact --out review/
```

### Locally only

Nothing forces this into CI. The commit hook is where it does its work, and a team that runs
it locally and nowhere else still gets the ratchet.

## What this repository's own workflows do

Worth reading if you are wiring up your own, because they are explicit about what they cannot
run.

`gate.yml` runs one command on a hosted runner and needs no licence at all:

```bash
uv run pytest --cov=src/scitools_hook --cov-branch --cov-report=term-missing --cov-fail-under=85
```

The tests that need a licensed Understand are marked `contract` and skip themselves unless
`SCITOOLS_HOME` points at a licensed install. That skip is **printed rather than left
silent** — the release workflow lists, by name, every licensed test that did not run.

`release.yml` builds the sdist and wheel, installs the wheel where there is no source tree and
runs the CLI from it, and then attaches both artifacts to a GitHub release:

```bash
gh release create "$GITHUB_REF_NAME" dist/* \
  --repo "$GITHUB_REPOSITORY" --title "$GITHUB_REF_NAME" --generate-notes $prerelease
```

`$prerelease` is `--prerelease` when the tag contains `a`, `b` or `rc`. There is no PyPI
step, no trusted publisher and no API token, because the tool is not published to PyPI.

The full licensed suite runs in a container with **no network at all**
(`docker run --network none`), which proves the boundary before it reads anything: it opens
outbound connections, requires every one of them to fail, and aborts if any succeeds.
Measured on this tree: 3633 passed, 5 skipped, 1 xfailed, branch coverage 98.19%.

That is a `docker` invocation rather than a Dagger function for a measured reason: Dagger
v0.21.9's `Container` type has no field that disables networking (82 fields, none matching
"net"), so a Dagger container cannot honour that boundary.
