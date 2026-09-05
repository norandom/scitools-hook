# Review at scale

## The conclusion first

Reviewing agent output line by line does not scale. Reviewing its structure does. `explain`
is the half of this tool that produces the structure, and it is the only command that never
blocks anything.

`check` answers "is this allowed". `explain` answers "what did this do to the shape of the
code" — for a human before they open a file, or for an agent that needs to know what its own
change touched.

## The change summary

```console
$ scitools-hook explain --staged
change summary
  database: /home/you/.cache/scitools-hook/e7cde7af638ed147/after.und

files (1)
  pricing/settle.py  [Directory Structure/pricing]
    added     file     pricing/settle.py
      CountDeclFunction  - -> 1  (+1)
      CountLineCode  - -> 35  (+35)
      MaxCyclomaticStrict  - -> 14  (+14)
      RatioCommentToCode  - -> 0.06  (+0.06)
    added     routine  pricing.settle.settle  line 6
      CountLineCode  - -> 34  (+34)
      CountParams  - -> 5  (+5)
      CountPath  - -> 44  (+44)
      CountStmt  - -> 25  (+25)
      CyclomaticModified  - -> 13  (+13)
      CyclomaticStrict  - -> 14  (+14)
      Essential  - -> 1  (+1)
      MaxNesting  - -> 4  (+4)

dependencies (1)
  Directory Structure/pricing
    added    pricing/settle.py -> pricing/rates.py

largest deltas (10)
   1. pricing.settle.settle (pricing/settle.py)  CountPath  - -> 44  (+44)
   2. pricing/settle.py  CountLineCode  - -> 35  (+35)
   3. pricing.settle.settle (pricing/settle.py)  CountLineCode  - -> 34  (+34)
   4. pricing.settle.settle (pricing/settle.py)  CountStmt  - -> 25  (+25)
   5. pricing/settle.py  MaxCyclomaticStrict  - -> 14  (+14)
   6. pricing.settle.settle (pricing/settle.py)  CyclomaticStrict  - -> 14  (+14)
   7. pricing.settle.settle (pricing/settle.py)  CyclomaticModified  - -> 13  (+13)
   8. pricing.settle.settle (pricing/settle.py)  CountParams  - -> 5  (+5)
   9. pricing.settle.settle (pricing/settle.py)  MaxNesting  - -> 4  (+4)
  10. pricing/settle.py  CountDeclFunction  - -> 1  (+1)

largest values (10)
   1. pricing.settle.settle (pricing/settle.py)  CountPath  44
   2. pricing/settle.py  CountLineCode  35
   3. pricing.settle.settle (pricing/settle.py)  CountLineCode  34
   ...

impact (0)
  none

graphs (0)
  none

open in the Understand GUI: understand /home/you/.cache/scitools-hook/e7cde7af638ed147/after.und
```

Every entity is labelled `added`, `modified` or `deleted`, with the architecture node it sits
in. `largest deltas` is what to read first on a change you did not write: it is the list of
what moved most, ordered, regardless of whether anything broke a rule.

The last line is a command you can paste. It opens the exact database the report was built
from, in the Understand GUI, so a reviewer who wants to keep digging does not have to
reproduce the analysis.

## Selections

| Flag | Analyses |
| --- | --- |
| `--staged` | The index, against `HEAD`. What the hook runs. |
| `--worktree` | The working tree, staged or not. What an agent runs while editing. |
| `--all` | The whole project. No before side, so no ratchet and no `pre-existing`. |
| `--files PATH` | Exactly these paths. Repeatable. Bare trailing paths mean the same thing. |
| `--range A..B` | What happened between two commits. `explain` only. |

`--range` is the pull-request form:

```bash
scitools-hook explain --range "origin/main...HEAD" --graphs --impact --out review/
```

## Graphs and the impact set

```console
$ scitools-hook explain --range HEAD~1..HEAD --graphs --impact --out review/
impact (4)
  pricing.settle.goods_line (pricing/settle.py)  2 total; depth 1: 1, depth 2: 1
  pricing.settle.line_total (pricing/settle.py)  1 total; depth 1: 1
  pricing.settle.service_line (pricing/settle.py)  2 total; depth 1: 1, depth 2: 1
  pricing.settle.settle (pricing/settle.py)  0 total

graphs (5)
  Depends On  pricing/settle.py                            review/pricing_settle_py-ccbe74db8212-Depends_On.svg
  Butterfly   pricing.settle.goods_line (pricing/settle.py)  review/pricing_settle_goods_line-6fa67e526589-Butterfly.svg
  Butterfly   pricing.settle.line_total (pricing/settle.py)  review/pricing_settle_line_total-0f12cee419a5-Butterfly.svg
  Butterfly   pricing.settle.service_line (pricing/settle.py) review/pricing_settle_service_line-106fff5a5eb7-Butterfly.svg
  Butterfly   pricing.settle.settle (pricing/settle.py)      review/pricing_settle_settle-9cd08e5b4901-Butterfly.svg
```

`--out DIR` without `--graphs` is refused rather than ignored.

### What a butterfly graph is

One graph per changed routine or class: what calls it on the left, what it calls on the
right. This is `review/pricing_settle_line_total-0f12cee419a5-Butterfly.svg`, exactly as
Understand rendered it:

<div class="und-graph" markdown="0">
<svg width="550" height="79" xmlns="http://www.w3.org/2000/svg" version="1.1" role="img" aria-label="Butterfly graph: settle calls line_total, which calls goods_line and service_line">
 <polygon points="547,4 504,4 504,30 547,30 547,4" stroke="#8f8f8f" fill="none"/>
 <text x="512" y="19" fill="#000000" font-family="Arial" font-size="12">get </text>
 <polygon points="432,3 321,3 321,30 432,30 432,3" stroke="#90c4e4" fill="none"/>
 <text x="330" y="18" fill="#000000" font-family="Arial" font-size="12">goods_line ▶</text>
 <path d="M 432 17 C 455 17, 480 17, 498 17" stroke="#4e8cb9" fill="none"/>
 <polygon points="498,15 503,17 498,19 498,15" stroke="#4e8cb9" fill="#4e8cb9"/>
 <polygon points="249,26 135,26 135,53 249,53 249,26" stroke="#1976d2" stroke-width="3" fill="none"/>
 <text x="143" y="41" fill="#000000" font-family="Arial" font-size="12">▶ line_total ▶</text>
 <path d="M 250 33 C 271 30, 294 27, 315 25" stroke="#4e8cb9" fill="none"/>
 <polygon points="315,23 320,24 315,26 315,23" stroke="#4e8cb9" fill="#4e8cb9"/>
 <polygon points="429,49 324,49 324,75 429,75 429,49" stroke="#90c4e4" fill="none"/>
 <text x="333" y="64" fill="#000000" font-family="Arial" font-size="12">service_line </text>
 <path d="M 250 47 C 272 50, 297 52, 318 55" stroke="#4e8cb9" fill="none"/>
 <polygon points="318,53 323,56 318,57 318,53" stroke="#4e8cb9" fill="#4e8cb9"/>
 <polygon points="63,27 4,27 4,53 63,53 63,27" stroke="#90c4e4" fill="none"/>
 <text x="12" y="42" fill="#000000" font-family="Arial" font-size="12">settle </text>
 <path d="M 63 40 C 81 40, 105 40, 127 40" stroke="#4e8cb9" fill="none"/>
 <polygon points="127,38 132,40 127,42 127,38" stroke="#4e8cb9" fill="#4e8cb9"/>
</svg>
</div>

`settle` calls `line_total`, which calls `goods_line` and `service_line`. On a repository the
size of one you would actually review, this is the picture that tells you whether a change
sits at a leaf or in the middle of everything.

Two kinds are exported, and only two, because they are the two Understand will actually
render for the entities in question (measured on 6.5 and again on 8.0):

| Kind | Drawn for |
| --- | --- |
| `Butterfly` | each affected routine and class |
| `Depends On` | each affected file |

A routine draws `Butterfly`, `Calls` and `Called By`, and **refuses** `Depends On` with
`UnderstandError('Unknown Graph')` while writing no file. A refused graph is a warning, not a
failure: one target the installed Understand will not render must not cost the reviewer every
other graph.

File names are `<slug of the long name>-<12 hex of the key digest>-<slug of the graph>.svg`.
Neither part can contain a path separator, which is what confines the export to the directory
you chose.

### The impact set

`--impact` lists what references each changed routine and class, by depth:

```text
pricing.settle.goods_line  2 total; depth 1: 1, depth 2: 1
```

One entity references it directly and one more at two hops. The depth is
`output.impact_depth`, default 3. An entity is reported at the shallowest depth that reaches
it and never again, and the entity the walk started from is never part of its own impact set
— reference graphs are full of cycles.

`depth = 0` is a legal answer meaning "report nothing".

## Sizing

```toml
[output]
graphs_max = 20        # per group: routines/classes, and files, counted separately
impact_depth = 3
show_highest = false
```

`graphs_max` is applied to each group on its own. Leaving the files unbounded would let a
whole-project run draw one graph per file in the repository. Zero draws nothing and opens no
database at all.

## Output formats

```bash
scitools-hook explain --staged --format markdown --output review.md
scitools-hook explain --staged --format json | jq '.largest_deltas[0]'
```

`human`, `json` and `markdown`. The markdown form is the one to paste into a pull request.

## A reviewing workflow

For a large agent-authored branch:

```bash
# 1. What moved, structurally, and by how much.
scitools-hook explain --range "origin/main...HEAD" --format markdown --output review.md

# 2. The pictures, for the parts of it that look load-bearing.
scitools-hook explain --range "origin/main...HEAD" --graphs --impact --out review/

# 3. Whether any of it broke a rule, or made an existing problem worse.
scitools-hook check --files $(git diff --name-only origin/main...HEAD)

# 4. Open the database and keep digging, if step 2 raised a question.
understand "$(scitools-hook db path)"
```

Read the shape first. Open files second, and only the ones the shape made interesting.
