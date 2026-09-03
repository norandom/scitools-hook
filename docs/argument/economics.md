# The economics

## The conclusion first

Context is the budget. An overgrown routine plus the neighbourhood you need in order to
change it safely does not fit inside it, so the agent re-reads, guesses, and produces changes
that need human repair. Buying a larger model buys a larger budget. It does not shrink the
thing you are spending it on.

The ratchet is the cost control, and it is a cheap one, because it does not ask anyone to pay
down existing debt. It forbids making things worse, where "worse" means crossing a limit or
worsening something already over one — not merely moving.

**There is no saving claimed on this page.** No hours, no dollars, no percentage. None of
that was measured, and a number invented to make an argument is exactly the kind of defect
this project's source is written to avoid. What follows is the mechanism.

## Where the budget goes

A change to one routine costs, at minimum:

- the routine's own text;
- the routines it calls, at least their signatures and often their bodies;
- the callers whose expectations the change might break;
- the data shapes moving across all of that;
- and the tests that pin any of it.

Only the first of those is bounded by the routine's line count. The rest grow with its
branching, because each branch is a case whose interaction with the change has to be checked.
That is what `CountPath` counts. A routine at `CountPath` 44 has 44 things to check. A
routine at `CountPath` 32 800 has, in practice, none — nobody checks 32 800 cases, so nobody
checks any of them, and the change is made by resemblance instead.

The failure is not "the agent ran out of tokens". It is that the agent silently switched
strategies, from reasoning to pattern-matching, at a threshold nothing reported.

## Why the bigger model is the expensive answer

A larger model with a larger window does genuinely help. It moves the threshold. What it does
not do is any of the following:

- stop the codebase growing past the new threshold, which it will, because the same agents
  are still writing into it;
- make the threshold visible, so you still find out from a defect rather than from a run;
- reduce the number of times the same neighbourhood is re-read across a session.

So the spend is recurring, it scales with the size of the problem, and it buys tolerance
rather than a fix. Meanwhile the shape of the code — the thing that decided how much had to
be held in mind — is unchanged and free to keep getting worse.

Constraining the shape is a fixed cost paid once per commit, by the agent that is already
in context, at the moment the change is cheapest to make.

## Why paying down debt is the wrong ask, and the ratchet avoids it

Point a complexity tool at a real repository and it will report hundreds of findings. Here is
this repository's own source and test tree, measured:

```console
$ scitools-hook check --all
summary: 230 errors, 131 warnings, 0 pre-existing, 230 blocking
         | 1 file failed to parse, not fully checked
         | 1 metric unavailable, those limits were not evaluated
         | exit 1: blocking violations found
```

230 blocking findings on a project that gates itself, over `src/` and `tests/` together. If
adoption required that number to reach zero, adoption would not happen, and the usual outcome
is a tool that is installed, found to be noisy, and switched off within a fortnight.

That number is not a measure of how bad the code is, and reading it as one is the most common
mistake a first run produces. On the same tree, of 4232 routines measured: the median
`CyclomaticStrict` is **1**, the p95 is **4**, the maximum is **14**, and **99.9%** are inside
the default limit of 10. Only 3 of the 230 blocking findings are `routine.CyclomaticStrict`
at all. The count comes from file-scope counts, structural rules and a long tail. See
[Rescuing a problematic project](../guide/rescue.md#the-first-number-is-not-a-quality-score).

The gate is not adopted that way. A commit is checked with `--staged`, which measures the
affected entities and compares them against `HEAD`. On that path the same 230 findings are
reported as `pre-existing` whenever a commit touches their file, and none of them blocks.
What blocks is a change that made something worse.

That has three consequences worth being explicit about:

1. **Adoption is cheap.** Install the hook on any repository, however bad, and a commit that
   leaves the numbers where they were still passes.
2. **The debt is visible without being an obstacle.** Every run that touches a debt-carrying
   file prints the finding and its remediation hint. Fixing it is opportunistic, done by
   whoever is already in that file.
3. **The trend is monotonic.** The worst value of each measured entity can only stay level or
   improve, one commit at a time. Nobody has to schedule the improvement.

!!! note "What \"worse\" means, precisely"

    A commit that grows an entity which is still inside its limit is **reported and does not
    block**: `CountLineCode rose from 23 to 24, still within the maximum 60` is a warning.
    What blocks is growth that crosses a limit, or growth on an entity already over one.

    That single default is what makes adoption cheap rather than merely possible, and it was
    added after a field report where the gate refused the very refactorings its own hints had
    asked for, and the team's commits went in under `SCITOOLS_HOOK_SKIP=1` for a day. See
    [growth inside the limit](ratchet.md#growth-inside-the-limit-is-reported-not-refused).

## The cost of running it

Honest accounting of what the gate itself costs:

| Cost | What it is |
| --- | --- |
| A SciTools Understand licence | Commercial, per seat. The gate does not bundle it, does not install it, and cannot work without it. This is the real cost of the tool. |
| Wall-clock time per commit | A staged run analyses the affected files, not the repository. Two Understand databases are kept warm in a per-user cache and updated incrementally. |
| A first run | Cold, it builds both databases. This is the slow one. |
| Attention | A blocked commit costs the agent a re-run and an edit. A blocked commit that the agent cannot fix costs a human. |

The last row is the one to watch during a trial. If the gate blocks changes your agents
cannot resolve, the limits are wrong for that repository and the right response is to
configure them, once, with the reason written down — not to switch the gate off.

Two of the shipped limits were demoted to warnings for exactly that reason after measurement
showed they ranked style rather than complexity. See
[Rules and defaults](../reference/rules.md#two-limits-that-were-demoted-to-warnings).

## What the ratchet cannot do

- It cannot improve a codebase on its own. It stops the trend; it does not reverse it.
- It cannot see complexity that is spread thinly. Ten routines at `CyclomaticStrict` 9 each
  pass every rule, and the system they form together may still be unreasonable.
- It cannot tell you whether a routine is *worth* its complexity. Some genuinely are, and the
  answer for those is a configured scope with a written reason, not a silent ignore.
