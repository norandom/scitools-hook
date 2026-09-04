# CLI reference

```console
$ scitools-hook --help
Usage: scitools-hook [OPTIONS] COMMAND [ARGS]...

  Maintainability gate backed by SciTools Understand.

  Runs the same way locally, in a git hook, in CI and when driven by an agent:
  findings go to standard output, diagnostics to standard error, and nothing
  ever prompts. Global options belong before the subcommand: scitools-hook
  --verbose check --staged.

Commands:
  check           Check a change against the maintainability rules.
  explain         Explain what a change did to the code, for a reviewer or an agent.
  baseline        Capture the adaptive baseline from the current state of the project.
  init            Write a configuration file for this repository.
  config          Show the effective configuration and where each setting came from.
  doctor          Report the Understand installation, licence, repository and configuration.
  install-hook    Install the pre-commit shim into this repository's hooks directory.
  uninstall-hook  Remove the pre-commit shim and restore whatever it replaced.
  agent-rules     Print the effective rules as a block a coding agent can follow.
  db              Inspect and maintain the Understand database for this repository.
```

Three properties hold for every command: findings go to standard output, diagnostics go to
standard error, and **nothing ever prompts**. That is what lets the same binary run in a hook,
in CI and under an agent without a special mode.

Shell-completion installation is deliberately absent, because it writes to the user's shell
profile, which is not something a gate run from a hook should ever do.

## Global options

Global options go **before** the subcommand.

```bash
scitools-hook --verbose check --staged
```

| Option | Effect |
| --- | --- |
| `--scitools-home DIR` | The Understand installation to use. Highest precedence in the locator order. |
| `--config PATH` | Read this configuration file instead of the discovered one. |
| `--api-mode <auto\|inprocess\|upython>` | How to reach Understand's Python API. See [Operations](operations.md#api-modes). |
| `--verbose` | Print external commands, timings and tracebacks. |
| `--color` / `--no-color` | Force colour on or off, whatever stdout is. |
| `--quiet` | Print only the summary and blocking findings. |
| `--version` | Print the installed version and exit. |

## Exit codes

| Code | Meaning |
| ---: | --- |
| 0 | No blocking violations |
| 1 | Blocking violations found |
| 2 | Configuration error (unknown key, metric, scope, regex, architecture) |
| 3 | No usable SciTools Understand installation found |
| 4 | Understand reported no valid license |
| 5 | Analysis failed (`und` error, timeout or unusable database) |
| 6 | Not inside a git repository |
| 7 | The analysis ran but its report could not be delivered |
| 70 | Unexpected internal error |

Only 0 and 1 are statements about the code. Everything above 1 means nothing was measured.

---

## `check`

!!! warning "`--files` and `--staged` measure the **index**, not your working tree"

    Only `--worktree` reads the files as they are on disk. If you fix a file and run
    `check --files that/file.py` without staging it, the run measures the *staged* content and
    reports the problem you just fixed. Stage it, or use `--worktree`.


Check a change against the maintainability rules. This is the command the hook runs.

```console
Usage: scitools-hook check [OPTIONS] [PATH]...

Arguments:
  [PATH]...  Same as --files: the paths a pre-commit framework appends to the entry line.

Options:
  --staged                     Analyse the staged changes.
  --worktree                   Analyse the working tree, staged or not.
  --all                        Analyse the whole project.
  --files PATH                 Analyse exactly these files; repeatable.
  --format <human|json|sarif>  How to render the findings.  [default: human]
  --output PATH                Write the findings here instead of stdout.
  --sarif PATH                 Also write the findings here as SARIF 2.1.0.
  --strict                     Count pre-existing violations in affected code as blocking.
  --adaptive / --no-adaptive   Apply the recorded baseline as the effective limit.
  --show-highest               Report the highest value found per metric.
```

`--all` has no before side, so nothing is ever `pre-existing` and no ratchet finding can
fire. It is an inventory, not a gate.

`--sarif PATH` writes SARIF *in addition to* the human report. `--format sarif` sends SARIF
to standard output instead.

`--show-highest` adds a section naming the largest value found per metric, with the entity
and line, whether or not it breaks a limit:

```text
highest values: the largest value per metric, whether or not it breaks a limit
  file.CountLineCode  25  pricing/settle.py
  routine.CountPath  4  pricing.settle.line_total  pricing/settle.py  line 24
  routine.CyclomaticStrict  4  pricing.settle.line_total  pricing/settle.py  line 24
```

## `explain`

`--range A..B` compares the two commits. `--range A...B` compares from their **merge base** —
what the branch did, without the commits the base gathered meanwhile. That is what
`git diff A...B` shows and what a pull request shows, so it is usually the one you want for a
review: `--range "origin/main...HEAD"`.


Explain what a change did to the code. Never blocks anything.

```console
Usage: scitools-hook explain [OPTIONS] [PATH]...

Options:
  --staged / --worktree / --all / --files PATH   as for check
  --range A..B                     Explain what happened between two commits.
  --range A...B                    The same, measured from their merge base.
  --graphs                         Export callers/callees and depends-on graphs as SVG.
  --impact                         List what references each changed routine and class.
  --out DIR                        Directory the exported graphs are written into.
  --format <human|json|markdown>   How to render the change summary.  [default: human]
  --output PATH                    Write the findings here instead of stdout.
```

`--out` without `--graphs` is refused rather than ignored. See
[Review at scale](../guide/review.md).

## `baseline`

Capture the adaptive baseline from the current state of the project.

```console
Usage: scitools-hook baseline [OPTIONS]

Options:
  --file PATH  Write the baseline here instead of the configured file.
```

Records the worst current value per ratcheted rule. Only useful with
`baseline.adaptive = true`. Do not commit a baseline captured by accident.

## `recommend`

Measure this repository and propose thresholds that fit it, with the cost of each.

```console
Usage: scitools-hook recommend [OPTIONS]

Options:
  --target SHARE  Share of a scope's entities a limit must contain to fit (0 < share <= 1).
  --toml          Print only the configuration lines to paste, without the evidence report.
  --output PATH   Write the report here instead of standard output.
```

Not a baseline. `baseline` records **where you are** — today's worst value per rule, so
existing debt reports as `pre-existing`. `recommend` says **where to aim**: for every ceiling
in force, how much of the repository is already inside it, what each candidate limit would
cost in entities reported, and who the worst offenders are. A limit that already fits is
reported `keep`.

It writes nothing and applies nothing. Paste what you agree with.

## `init`

Write a configuration file for this repository.

```console
Usage: scitools-hook init [OPTIONS]

Options:
  --force   Overwrite an existing configuration file.
  --detect  Classify the repository from what it declares about itself, with the evidence.
  --print   Write the configuration to standard output instead of to the file.
```

The written file contains every value at its default, with a comment on each section, so it
reads as documentation you can edit. `--detect` adds what the repository declares about
itself, and proposes any needed `[parse] acknowledged` entries **commented out** — because
uncommenting one is the operator's decision.

## `config`

Show the effective configuration and where each setting came from.

```console
Usage: scitools-hook config [OPTIONS]

Options:
  --detect    Classify the repository from what it declares about itself, with the evidence.
  --why PATH  Explain how one path is classified and which scopes apply to it.
```

```console
$ scitools-hook config --why legacy/report.py
path: legacy/report.py
role: product
  no region covers this path; it is product code by default
scopes: legacy
  [scope.legacy] matched by 'legacy/**'
    routine.CyclomaticStrict = max=20
    routine.MaxNesting = false (the rule does not apply here)
parse: not acknowledged; an unreadable file here blocks the commit
```

## `doctor`

Report the Understand installation, licence, repository and configuration in use.

```console
Usage: scitools-hook doctor [OPTIONS]
```

**Always exits 0.** It reports what it found rather than judging it, because exit 1 is
already spent on "blocking violations found" and a CI job running `doctor` must not be told
a commit had violations that were never measured.

It runs **both** API probes, not just the one that would be used, because a diagnosis has the
opposite job from a resolution. That is safe because the in-process import is run in a child
process. Full output on the [Install](../guide/install.md#check-it-works) page.

## `install-hook` / `uninstall-hook`

```console
Usage: scitools-hook install-hook [OPTIONS]

Options:
  --force   Replace an existing pre-commit hook, keeping it and chaining to it.
  --global  Use the user's global hooks path instead of this repository's.
```

```console
Usage: scitools-hook uninstall-hook [OPTIONS]

Options:
  --global  Use the user's global hooks path instead of this repository's.
```

See [Hooks and CI](../guide/hooks-and-ci.md).

## `agent-rules`

Print the effective rules as a block a coding agent can follow.

```console
Usage: scitools-hook agent-rules [OPTIONS]

Options:
  --write FILE  Insert the block into this file between the scitools-hook markers.
```

The markers are `<!-- scitools-hook:begin -->` and `<!-- scitools-hook:end -->`. Re-running
replaces the block; everything else in the file is preserved. See
[Working with agents](../guide/agents.md).

## `install-skills`

Install the agent skills that drive this tool into a repository.

```console
Usage: scitools-hook install-skills [OPTIONS]

Options:
  --dir DIR  Write the skills here instead of .agents/skills.
  --force    Replace a SKILL.md that differs from the shipped one.
```

Writes three documents an agent host can load:

| Skill | Answers |
| --- | --- |
| `scitools-gate` | *May this change land?* Preconditions, `check`, `explain`, the exit-code contract. |
| `scitools-improve` | *How does this repository get easier to change?* The baseline loop, one entity per commit. |
| `scitools-adapt` | *Are these rules right for this repository?* The six-rung ladder, with a measurement per decision. |

The default location is `.agents/skills`, which is vendor-neutral, and is resolved against
the **repository root** so the command works from any subdirectory. `--dir` is resolved
against the directory you typed it in, the same asymmetry `baseline --file` draws.

Running it twice writes nothing the second time. A `SKILL.md` that differs from the shipped
one is refused with exit 2 rather than overwritten — the skills are documents an operator may
have edited — and `--force` takes the shipped version back.

It needs no Understand installation and no repository. See
[Working with agents](../guide/agents.md#the-skills).

## `db`

Inspect and maintain the Understand database for this repository.

```console
Usage: scitools-hook db [OPTIONS] COMMAND [ARGS]...

Commands:
  path     Print the path of this repository's analysis database.
  rebuild  Discard the analysis databases and analyse the project again.
  analyze  Bring the analysis database up to date with the index.
```

### `db path`

Prints exactly one line: the path of the **after** database, the one that holds the project
as it currently stands. So it substitutes straight into the GUI command:

```bash
understand "$(scitools-hook db path)"
```

**This command needs no Understand installation.** The operator asking is usually the one
whose installation is not working, and answering "no usable Understand installation found" to
"where is my database?" would be both wrong and unhelpful.

### `db rebuild`

Discards both databases and the sync state, then analyses again. It reports what was removed
**before** the analysis starts, in its own write, because a destructive step whose record is
lost when the step after it fails is the worst way to learn what happened to your cache.

### `db analyze`

Brings the after database up to date with **the index**, not the working tree — because that
is the state `check --staged` and the hook analyse, and warming any other target would leave
the next commit paying for a full re-sync.

---

## Ordering guarantee

Not being in a git repository is reported before Understand is looked for. A run from the
wrong directory says exit 6, rather than reporting a missing installation (exit 3) it never
needed.
