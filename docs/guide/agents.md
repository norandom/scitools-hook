# Working with agents

## The conclusion first

An agent that learns a limit from a rejected commit has already wasted the work. Give it the
numbers before it writes the code, and a command it can run on its own output.

Two things do that: `agent-rules`, which writes the effective limits into the file your agent
already reads, and a skill that lets the agent drive the whole CLI.

## `agent-rules --write`

```bash
scitools-hook agent-rules --write AGENTS.md
```

```console
wrote the rules block into AGENTS.md
```

It inserts between `<!-- scitools-hook:begin -->` and `<!-- scitools-hook:end -->`, so
everything else in the file is preserved and re-running replaces the block rather than
appending a second one. Run it again whenever the configuration changes. `CLAUDE.md`,
`.cursorrules` and anything else your agent reads work the same way.

The block is generated from the *effective* configuration, so what the agent reads is what
the gate will actually enforce. It cannot drift.

??? example "The full block, as generated from the shipped defaults"

    ```markdown
    ## Maintainability rules (scitools-hook)

    This repository is gated by `scitools-hook`, which measures the code a change touches with
    SciTools Understand and refuses a commit that makes it worse. These are the rules your work
    is judged by; read them before you write code. Regenerate this block with
    `scitools-hook agent-rules` whenever the configuration changes.

    ## Limits

    Each limit below is checked on the entities your change touches. An `error` blocks the
    commit; a `warning` is reported but does not block.

    ### Routines (functions and methods)

    - `CountLineCode`: at most 60 (error)
    - `CountParams`: at most 5 (error)
    - `CountPath`: at most 100 (error)
    - `CountStmt`: at most 40 (error)
    - `CyclomaticModified`: at most 8 (error)
    - `CyclomaticStrict`: at most 10 (error)
    - `Essential`: at most 4 (warning)
    - `MaxNesting`: at most 3 (error)

    ### Classes

    - `CountClassCoupled`: at most 12 (error)
    - `CountClassDerived`: at most 8 (error)
    - `CountDeclInstanceVariable`: at most 10 (error)
    - `CountDeclMethod`: at most 20 (error)
    - `CountDeclMethodNonStub`: at most 15 (error)
    - `MaxInheritanceTree`: at most 4 (warning)
    - `PercentLackOfCohesion`: at most 70 (warning)

    ### Files

    - `CountDeclClass`: at most 3 (error)
    - `CountDeclFunction`: at most 25 (error)
    - `CountLineCode`: at most 500 (error)
    - `MaxCyclomaticStrict`: at most 10 (error)
    - `RatioCommentToCode`: at least 0.1 (warning)

    ### Project-wide

    - `AVG:CountLineCode`: at most 30 (error) -- `AVG` over the whole project
    - `AVG:CyclomaticStrict`: at most 3 (error) -- `AVG` over the whole project
    - `MaxCyclomaticStrict`: at most 15 (error) -- measured over the whole project
    - `MaxNesting`: at most 5 (error) -- measured over the whole project

    ## Structural rules

    These are about how the code fits together, not about one entity's own numbers.
    The rules that group by architecture use `Directory Structure`; the file-level ones apply to every file.

    - New import or include cycles between files are reported (error)
    - New cycles between the architecture nodes of `Directory Structure` are reported (error)
    - Fan-in of a file (the files that depend on it): at most 50 (warning)
    - Fan-out of a file (the files it depends on): at most 20 (warning)
    - Fan-in of a class (the classes that depend on it): at most 30 (warning)
    - Fan-out of a class (the classes it depends on): at most 12 (warning)
    - One file may gain at most 5 new dependencies in a single change (error)

    ## The ratchet

    A metric may not get worse than it was, even when it stays inside its limit. Taking
    `CyclomaticStrict` on an existing routine from 4 to 6 is reported although the limit is 10:
    the number to beat is the one that routine had before your change. Leave every metric where
    it is, or make it better.

    Strict mode is off: a violation that was already there before your change is reported
    but does not block, as long as you do not make it worse.

    These limits are not ratcheted, so a value inside them may move: `class.CountClassCoupled`,
    `class.CountClassDerived`, `class.CountDeclInstanceVariable`, `class.CountDeclMethod`,
    `class.CountDeclMethodNonStub`, `file.CountDeclClass`, `file.CountDeclFunction`,
    `file.CountLineCode`.

    ## Check your own work

    Run the gate yourself rather than learning about a violation from a rejected commit.

    ```sh
    scitools-hook check --worktree   # your edits as they stand, before you stage anything
    scitools-hook check --staged     # what you are about to commit; the hook runs this
    ```

    Use `--worktree` while you are still editing and `--staged` once before you commit. Exit
    code 0 means nothing blocks; exit code 1 means the change is blocked.

    ## Read the JSON output

    ```sh
    scitools-hook check --worktree --format json
    ```

    That prints one JSON document. `findings` is the array of everything the run found; each
    entry carries `rule`, `scope`, `path`, `line`, `value`, `before`, `limit`, `severity`,
    `blocking`, `preexisting`, `message` and `hint`. `preexisting` marks a violation that was
    already there before this change. `hint` is the remediation text -- it says what to change,
    so read it before you edit. `blocking_count` counts the findings that block the commit;
    keep working until it is `0`.

    ## When the gate blocks a commit

    A blocking finding is an `error` your change introduced: the code was inside the rule
    before you touched it and is outside it now. When a commit is blocked:

    1. Read the `message` and the `hint` of every finding whose `blocking` is `true`.
    2. Fix the code. Do not raise a limit, do not add an ignore pattern and do not re-capture
       the baseline to make a finding disappear -- those change the rules, not the code.
    3. Re-run `scitools-hook check --worktree` until nothing blocks any more.
    4. Stage the change and commit; the hook runs `scitools-hook check --staged` again.

    A `warning` never blocks, and a pre-existing violation blocks only in strict mode. Fixing
    either is welcome, but neither is what a blocked commit is asking you to do.

    One blocking finding is not about a limit at all. `analysis.parse_error` means the analyser
    could not read a file your change is adding or editing, so nothing after the line it names
    was measured and no rule ran on it -- an empty report about that file is not a clean one. Its
    `hint` names the construct to rewrite. Do not silence it: a file that does not parse is a
    file nobody checked.
    ```

Three things about that block are worth pointing out, because they are the parts that
actually change agent behaviour.

**It says what to run, and when.** `--worktree` while editing, `--staged` before committing.
An agent that only learns about a limit from the hook has already produced the wrong code.

**It names the ratchet explicitly**, with an example that is inside the limit: taking
`CyclomaticStrict` from 4 to 6 is *reported* although the maximum is 10. Read "reported"
literally — that finding arrives as a warning and does not block, because the routine is
still inside its limit. Without the sentence an agent optimises against the wrong number;
with it, an agent that treats every warning as a refusal wastes a cycle, which is why the
[JSON contract](#the-json-contract) below tells it to work from `blocking_count` rather than
from the length of `findings`.

**It forbids the shortcut, by name.** *"Do not raise a limit, do not add an ignore pattern and
do not re-capture the baseline to make a finding disappear — those change the rules, not the
code."* That is the failure mode a capable agent will otherwise find on its own, because it
is the cheapest way to make the command exit 0.

## The JSON contract

```bash
scitools-hook check --staged --format json
```

One document, `schema_version: 1`. The keys:

| Key | Contents |
| --- | --- |
| `schema_version`, `tool_version`, `understand_version` | Provenance. `understand_version` is the installed build's own string. |
| `repo_root`, `selection`, `started_at`, `seconds` | What was run, where, and for how long. |
| `effective_thresholds` | Every threshold that applied, with its limit, severity, ratchet flag and source. |
| `findings` | The array. |
| `blocking_count`, `warning_count`, `preexisting_count` | The counts the summary line prints. |
| `parse_errors` | Files the analyser could not read. |
| `unavailable_metrics` | Keyed **language &rarr; metrics**. Rules that were not evaluated. |
| `ignored_counts`, `tightened`, `highest`, `analyzed_files` | Skipped entities, baseline movements, worst values, and how much was analysed. |

One finding:

```json
{
  "kind": "threshold",
  "rule": "file.RatioCommentToCode",
  "metric": "RatioCommentToCode",
  "scope": "file",
  "entity": {
    "key": {"scope": "file", "path": "pricing/__init__.py",
            "longname": "pricing/__init__.py", "parameters": null},
    "kind": "python File", "name": "__init__.py", "line": null
  },
  "path": "pricing/__init__.py",
  "line": null,
  "value": 0.0,
  "before": null,
  "limit": 0.1,
  "limit_source": "config",
  "severity": "warning",
  "blocking": false,
  "preexisting": false,
  "message": "file pricing/__init__.py RatioCommentToCode is 0, which is below the minimum 0.1",
  "hint": "too little explanation: state at the top of the module, and on each exported routine, why it exists -- not what the code already says",
  "details": {}
}
```

`kind` is one of `threshold`, `ratchet`, `structural`, `codecheck`, `parse`. `parse` is the
odd one out and deliberately so: every other kind is a statement about code the gate *read*,
while a `parse` finding says a file in the selection was never read at all. It carries no
value and no limit, because there is no measurement.

The invariant to program against: **keep working until `blocking_count` is `0`.** Do not
count `findings`, because warnings and pre-existing violations are in there and neither
decides a commit.

## The Claude Code skill

This repository ships a skill at `.claude/skills/scitools-gate/SKILL.md`. It gives an agent
a protocol for driving the CLI rather than a list of commands:

- **Preconditions.** Run `doctor` and read three specific rows before trusting any result:
  the licence, the API mode, and the interpreter Understand will analyse with. An agent that
  reports a clean run from a machine with no licence has made a false completion claim.
- **Check.** `--worktree` while editing, `--staged` before committing, `--format json` for
  the machine-readable form. Work until `blocking_count` is `0`.
- **Explain.** `explain --range` for the structural picture, `--graphs --impact --out DIR`
  for the reviewer-facing artefacts.
- **Survey.** `check --all --show-highest` for the worst value per metric, with a `jq` recipe
  for a ranked list of the worst routines.
- **Configuration.** `config --why PATH` for "why does this file get those numbers".

Two parts of it exist to stop a specific failure:

**Only exit 0 and 1 are statements about the code.** Exit 3 means no Understand was found and
exit 4 means no licence, and in both cases nothing was measured. The skill's output format
has a `NOT_CHECKED` status for exactly that, so a run that could not happen is never reported
as a pass.

**A rationalisation table**, in the style of the other skills in this repository:

| Rationalization | Reality |
| --- | --- |
| "It only exceeds the limit slightly." | The limit is the limit. Growth *inside* a limit is only a warning; a blocking finding means the value crossed the limit or worsened something already over it. |
| "I'll add it to `[ignore]` and fix it later." | That is changing the rules. Fix the code or escalate. |
| "The check exited 3, so there is nothing to fix." | Exit 3 means nothing was checked. It is not a pass. |
| "Splitting the routine will trip `file.CountDeclFunction`." | It will not. Eight decomposition counts ship with the ratchet off. |
| "The file has no findings, so it is clean." | Not if it is named in `parse_errors`. |

## The loop

```bash
# 1. The agent reads the rules. They are already in AGENTS.md.
# 2. The agent writes code.
# 3. The agent checks its own work, before staging anything.
scitools-hook check --worktree --format json

# 4. blocking_count > 0? Read hint, fix the code, go to 3.
# 5. blocking_count == 0? Stage and commit. The hook runs check --staged again.
git add -A && git commit -m "..."
```

Step 3 is the one that matters. Everything else in this page exists to make step 3 possible
before the commit rather than after it.
