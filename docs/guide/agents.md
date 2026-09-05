# Working with agents

## The conclusion first

An agent that learns a limit from a rejected commit has already wasted the work. Give it the
numbers before it writes the code, and a command it can run on its own output.

Two things do that: `agent-rules`, which writes the effective limits into the file your agent
already reads, and three skills — `scitools-gate` to drive the CLI on a change,
`scitools-improve` to work a grown-over repository back down, and `scitools-adapt` to change
the rules themselves with evidence. `install-skills` puts all three into the repository, so
none of it depends on having this project checked out.

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

One document, `schema_version: 2`. The keys:

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
| `understand_sarif` | Understand's own SARIF documents, one entry per kind: `written` where it went beside `--sarif`, `source` where it was prepared, `problem` why there is none. Empty unless `understand.sarif` is on. |

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

## The skills

Two skills ship **inside the package**, so enabling a repository does not mean copying files
out of a checkout you do not have:

```bash
scitools-hook install-skills
```

```console
installed: scitools-gate at /your/repo/.agents/skills/scitools-gate/SKILL.md
installed: scitools-improve at /your/repo/.agents/skills/scitools-improve/SKILL.md
installed: scitools-adapt at /your/repo/.agents/skills/scitools-adapt/SKILL.md

Your agent can now run /scitools-gate to check a change, /scitools-improve to lower this
project's complexity one commit at a time, and /scitools-adapt to change the rules with the
measurement behind each decision.
```

`.agents/skills` is the vendor-neutral location. For a host that reads somewhere else, name
it:

```bash
scitools-hook install-skills --dir .claude/skills
```

Running it twice writes nothing the second time, so it is safe in a setup script. A
`SKILL.md` you have edited is refused rather than overwritten; `--force` takes the shipped
version back.

| Skill | Question it answers | May edit the configuration |
| --- | --- | --- |
| `scitools-onboard` | *What is this repository, and what limits fit it?* | yes, once, from measurement |
| `scitools-gate` | *May this change land?* | no |
| `scitools-improve` | *How does this repository get easier to change?* | no |
| `scitools-adapt` | *Are these rules right for this repository?* | yes, with evidence |

That last column is the design. The first two skills refuse to touch the configuration,
because an agent that can silence its own findings has no gate; `scitools-adapt` is where
that decision is made deliberately, and it is a separate invocation on purpose.

### `scitools-onboard`

The one-time act of deciding what a repository is. Eight commands in an order that matters,
because each answers a question the next one needs — and the point of it is that **every line
in the resulting configuration that deviates from a default carries the measurement that
justifies it.**

The shape it enforces, which is the opposite of how a configuration usually accretes:

1. **Detect before configuring.** `init --detect` classifies the tree from what it declares
   about itself and prints the evidence beside each line.
2. **Install the hooks and the rules first**, on shipped defaults — a repository is not
   enabled until a commit actually meets the gate.
3. **Measure, and change only what the measurement says.** A ceiling reported `keep` fits;
   paste only what `recommend` proposes, with its numbers in a comment above each line.
4. **Capture a baseline and turn `adaptive` on.** This is the invariant that makes onboarding
   safe to do once: limits are derived from evidence at the start, and from then on they can
   only narrow.
5. **Prove it** — a large `--all` inventory and `0 blocking` on `--staged` are both expected,
   and confusing the two is the most common first-day mistake.

Three readings it insists on, each from a measurement rather than a preference:

- **A ceiling most of the repository fails is not a limit, it is noise.** A third of files
  outside `CountDeclFunction = 25` means the default is wrong there, not that the codebase is.
- **A ceiling the repository fits, with a handful outside it, is working.** Those are
  outliers; the ratchet holds them as `pre-existing`. Do not draw a scope around them —
  scattered outliers do not cluster, and a scope round the three that blocked you has a worse
  reason than none.
- **When the routine limits and the file limits disagree, the file-level one yields.** Every
  routine hint asks for extraction and extraction raises the file counts; a file of twelve
  small named helpers is the outcome the routine limits are asking for.

And one precondition, which is easy to skip and expensive to skip: **do not derive limits from
a repository you are still reshaping.** `recommend` measures the shape a project has, so
running it mid-cleanup bakes in the shape somebody is working to change.

### `scitools-gate`

It gives an agent a protocol for driving the CLI rather than a list of commands:

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

### `scitools-improve`

The gate stops a repository getting worse. It does not, on its own, make one better — and a
project that has already grown past what an agent can reason about needs the second thing.

`scitools-improve` is the iterative loop, and it is deliberately not a clean-up task. Its
premise is the one on [The working set](../argument/working-set.md): every commit exists to
shrink the neighbourhood a single change has to be understood in.

Five phases, none of which assumes anything about your repository:

1. **Record where you are.** `init --detect`, then `baseline`, committed on their own with no
   code in the commit. It also checks `adaptive` under `[baseline]`, because **the default is
   off and the loop does nothing without it** — off, the baseline is a fixed floor; on, the
   effective limit is `min(configured, recorded)` and every whole-project run lowers a value
   the code has beaten.
2. **Decide where to aim.** `recommend`, whose output is proposed to a human and never
   applied. The skill may not propose a line that *raises* a limit.
3. **Survey, once per batch.** `check --all --show-highest`, plus a `jq` recipe that ranks by
   value *relative to its limit*. Routine-scope metrics are picked before file-scope ones,
   and churn (`git log`) breaks ties — complexity in a file nobody touches costs a model
   nothing, because no model reads it.
4. **One entity, one commit.** Read the `hint:`, change the code, **run the repository's own
   tests** — the gate measures shape and has nothing to say about whether the code still
   works — then `check --worktree`, stage, `check --staged`, commit with the movement in the
   message.
5. **Lock the gain in.** This is the part that is easy to get backwards:

    | Command | What it does to the baseline |
    | --- | --- |
    | `check --all` (with `adaptive = true`) | **Narrows only.** Lowers every recorded value the run beat; never raises one. |
    | `baseline` | **Replaces the file** — including values that got *worse*. |

    So refinement tightens with `check --all`, and `git diff` on the baseline file is read
    before it is staged. A value that went **up** in that diff is a limit you just relaxed.

Two things in it exist to stop a specific failure:

**The goal is not zero findings, and the skill says so before it says anything else.** An
agent handed `1286 blocking` reads it as a to-do list and either grinds through it or gives
up. `--all` is an inventory with no before side; the same findings are `pre-existing` under
`--staged`. The framing it uses instead: *you are never asked to fix the 1286, you are
forbidden from adding the 1287th.*

**An explicit escape hatch.** Some routines cannot be simplified without a design change that
is out of scope. Without permission to say so, an agent contorts the code until the number
moves — three badly-named helpers that satisfy the metric and leave the repository worse. The
skill's rule is that a short, honest list of what was not fixed beats a contorted change, and
its per-session output format has a `NOT FIXED` field to put it in.

### `scitools-adapt`

The other two skills, when they meet a limit they believe is wrong, are required to stop and
say so. This is what happens next.

Its question is never *how do I make this finding go away*. It is:

> Is this finding wrong about **the code**, or wrong about **what this repository is**?

Only the second is a configuration change. It works down a ladder and stops at the first rung
that fits:

| # | Rung | The change |
| ---: | --- | --- |
| 1 | The analyser could not read the file | Rewrite the construct, or `[[parse.acknowledged]]` **with a reason** |
| 2 | It is not source | `[project] exclude`, proposed by `init --detect` with evidence |
| 3 | Another tool owns the question | `severity = "warning"` — demote, keep visible |
| 4 | The region is different in kind | `[scope.X]`, never `[ignore]` |
| 5 | A project-scope rule a commit cannot act on | `severity = "warning"` and read it as a trend |
| 6 | The limit is genuinely wrong | `recommend`, then change it — in its own commit |

Three things it enforces that are easy to get wrong:

**Measure before, measure after.** A configuration edit whose effect nobody counted is
indistinguishable from turning off whatever was inconvenient. The skill carries the `jq`
recipes for grouping findings by rule and for seeing *where* a rule clusters — because "almost
all in `tests/`" means the rule's scope is wrong, not the rule.

**A demotion that made the count go to zero did not demote anything.** It hid it, and that is
the wrong rung. The output format has a `STILL VISIBLE` field for exactly this check.

**Write the reason down, every time.** A file full of undocumented overrides is
indistinguishable from one where somebody turned off whatever was inconvenient — so only keys
that deviate from the defaults, each with its measurement in a comment. Deleting an override
when re-measurement shows its reason has gone is as much a part of the skill as adding one.

It also pins the traps this project has actually fallen into: `[ignore]` is a hole rather than
a quieter scope; `max_new_dependencies_per_file = 0` is the *strictest* setting, not "off";
`Metric = false` works inside a `[scope]` and is a configuration error anywhere else; and
re-running `baseline` replaces the file with today's values, worse ones included.

The human-readable version of the same ladder, worked end to end on a 770-file repository, is
[Rescuing a problematic project](rescue.md).

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
