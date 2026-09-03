# Quickstart

Everything on this page is real output from `scitools-hook 0.1.0a1` against Understand
6.5.1204 (Build 1204). You can reproduce all of it.

## Build a repository to try it on

```bash
mkdir -p /tmp/pricing && cd /tmp/pricing
git init -q -b main .
git config user.email demo@example.com
git config user.name Demo
mkdir -p pricing
echo '"""Pricing: the catalogue, the rate table and the settlement rule."""' > pricing/__init__.py
```

Two dull modules to start with. `pricing/rates.py`:

```python
"""Currency rates: one lookup, one conversion.

The module exists so that no other module has to know the table's shape.
"""

BASE = {"EUR": 1.0, "USD": 1.09, "GBP": 0.85}


def rate_for(currency: str) -> float:
    """Return the base rate for a currency, or 1.0 when it is unknown."""
    return BASE.get(currency, 1.0)


def convert(amount: float, currency: str) -> float:
    """Convert an amount from EUR into ``currency``."""
    return amount * rate_for(currency)
```

And `pricing/catalog.py`, six small accessors over a dict. Commit both:

```bash
git add -A && git commit -qm "the boring version"
```

## Install the hook

```bash
export SCITOOLS_HOME=/path/to/scitools     # if it is not somewhere well known
scitools-hook install-hook
```

```console
installed the pre-commit shim at /tmp/pricing/.git/hooks/pre-commit
```

The shim is a small POSIX `sh` script. It contains no thresholds and no analysis logic, so
changing a limit never means reinstalling it. If a `pre-commit` hook was already there, it is
kept beside the shim as `pre-commit.scitools-hook-chained` and run at the end, so installing
the gate does not switch off whatever you had.

## Give the agents the rules and the skills

Two more commands finish enabling a repository. Neither needs Understand:

```bash
scitools-hook agent-rules --write AGENTS.md   # the limits, where your agent already reads
scitools-hook install-skills                  # the skills, at .agents/skills
```

```console
wrote the rules block into AGENTS.md
installed: scitools-gate at /tmp/pricing/.agents/skills/scitools-gate/SKILL.md
installed: scitools-improve at /tmp/pricing/.agents/skills/scitools-improve/SKILL.md
```

`scitools-gate` drives the CLI on a change; `scitools-improve` works an already-complex
repository back down, one commit at a time. Use `--dir .claude/skills` for Claude Code, or any
other path your assistant reads. Both commands are idempotent, so they belong in whatever
script sets a repository up. See [Working with agents](agents.md).

## Now write the change an agent writes

Ask for order settlement, follow up twice, and you get `pricing/settle.py`:

```python
"""Settlement: turn an order into an amount in the customer's currency."""

from pricing.rates import convert


def settle(order, customer, market, ledger, audit):
    """Price one order."""
    total = 0.0
    for line in order.lines:
        if line.kind == "goods":
            if customer.tier == "gold":
                if market.open:
                    total += line.amount * 0.9
                else:
                    total += line.amount * 0.95
            elif customer.tier == "silver":
                if market.open:
                    total += line.amount * 0.95
                else:
                    total += line.amount
            else:
                total += line.amount
        elif line.kind == "service":
            if customer.region == "EU":
                if line.amount > 1000:
                    total += line.amount * 1.19
                else:
                    total += line.amount * 1.07
            else:
                total += line.amount
        elif line.kind == "credit":
            total -= line.amount
        else:
            audit.warn("unknown line kind")
    if customer.overdue and total > 0:
        total *= 1.05
    if ledger.frozen:
        audit.warn("ledger frozen")
        return 0.0
    return convert(total, customer.currency)
```

Nothing is wrong with it. It passes `ruff`. It would pass `mypy` with annotations. Every
branch is reachable and every branch is correct.

```bash
git add pricing/settle.py
git commit -m "add settlement"
```

```console
created the after analysis database with Python enabled
created the before analysis database with Python enabled
pricing/settle.py
  error    routine.CyclomaticModified  pricing.settle.settle  line 6  1.6x limit
    routine pricing.settle.settle CyclomaticModified is 13, which exceeds the maximum 8
    hint: collapse the case arms into a lookup table or a polymorphic call and leave only the dispatch in this routine
  error    file.MaxCyclomaticStrict  1.4x limit
    file pricing/settle.py MaxCyclomaticStrict is 14, which exceeds the maximum 10
    hint: the most complex routine in this file is over the limit: simplify that routine first by extracting its branches into named routines
  error    routine.CyclomaticStrict  pricing.settle.settle  line 6  1.4x limit
    routine pricing.settle.settle CyclomaticStrict is 14, which exceeds the maximum 10
    hint: too many decision points in one routine: extract each group of related decisions into its own named routine, and replace boolean flag parameters with separate routines
  error    routine.MaxNesting  pricing.settle.settle  line 6  1.3x limit
    routine pricing.settle.settle MaxNesting is 4, which exceeds the maximum 3
    hint: extract the inner block into its own routine, or invert the condition and return early so the body stops nesting
  warning  file.RatioCommentToCode  0.6x limit
    file pricing/settle.py RatioCommentToCode is 0.06, which is below the minimum 0.1
    hint: too little explanation: state at the top of the module, and on each exported routine, why it exists -- not what the code already says
  warning  structure.fan_out  worse than before, was 0
    file pricing/settle.py fan-out rose from 0 to 1 files; an affected entity may not depend on more than it did

summary: 4 errors, 2 warnings, 0 pre-existing, 4 blocking | exit 1: blocking violations found

agent instructions
  4 findings block this commit; fix the code, do not relax the limits.
  Re-run while editing:  scitools-hook check --worktree
  Re-run before commit:  scitools-hook check --staged
  Each finding's "hint:" line says what to change; --format json carries the same hints.
```

The commit did not happen. `HEAD` is still `the boring version`.

Four things about that output are deliberate:

- **Every finding names the entity and the line.** `pricing.settle.settle line 6`, not
  "complexity increased in this project".
- **Every finding carries a hint that says what to change.** Not what is wrong — what to do.
- **The two warnings do not block.** 6 findings, 4 blocking. Warnings are reported and
  counted and never decide a commit.
- **The last block is addressed to an agent.** It says which command to re-run while editing
  and which to run before committing, and it says explicitly not to relax the limits.

## Do what the hint says

The hint for `MaxNesting` is *"extract the inner block into its own routine"*. Do exactly
that, three times:

```python
"""Settlement: turn an order into an amount in the customer's currency.

The branching that prices a line used to sit inside ``settle``; it is now three
routines, so each one can be read on its own.
"""

from pricing.rates import convert

GOODS = {("gold", True): 0.9, ("gold", False): 0.95, ("silver", True): 0.95}


def goods_line(amount, tier, market_open):
    """Price a goods line from the tier/market table, or at full price."""
    return amount * GOODS.get((tier, market_open), 1.0)


def service_line(amount, region):
    """Price a service line: EU VAT, at the rate the 1000 threshold selects."""
    if region != "EU":
        return amount
    return amount * (1.19 if amount > 1000 else 1.07)


def line_total(line, customer, market, audit):
    """Price one order line, whichever kind it is."""
    if line.kind == "goods":
        return goods_line(line.amount, customer.tier, market.open)
    if line.kind == "service":
        return service_line(line.amount, customer.region)
    if line.kind == "credit":
        return -line.amount
    audit.warn("unknown line kind")
    return 0.0


def settle(order, customer, market, ledger, audit):
    """Price one order. The per-line branching lives in the routines above."""
    if ledger.frozen:
        audit.warn("ledger frozen")
        return 0.0
    total = sum(line_total(line, customer, market, audit) for line in order.lines)
    if customer.overdue and total > 0:
        total *= 1.05
    return convert(total, customer.currency)
```

Check it before staging, the way an agent should:

```console
$ scitools-hook check --worktree
pricing/settle.py
  warning  structure.fan_out  worse than before, was 0
    file pricing/settle.py fan-out rose from 0 to 1 files; an affected entity may not depend on more than it did

summary: 0 errors, 1 warning, 0 pre-existing, 0 blocking | exit 0: no blocking violations
```

```console
$ git add pricing/settle.py && git commit -m "add settlement, decomposed"
summary: 0 errors, 1 warning, 0 pre-existing, 0 blocking | exit 0: no blocking violations
[main 0f34ef5] add settlement, decomposed
 1 file changed, 44 insertions(+)
 create mode 100644 pricing/settle.py
```

Note that the file is *longer* now, and has three more functions in it, and the gate did not
object. Counts that a decomposition raises by construction ship with the ratchet off, for
exactly this reason — see
[the ratchet's decomposition rule](../argument/ratchet.md#the-ratchet-does-not-refuse-the-refactoring-it-just-asked-for).

## Look at what the change did

`explain` answers a different question from `check`: not "is this allowed" but "what did this
do to the shape of the code".

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

For the reviewer-facing form of this, with SVG graphs and an impact set, see
[Review at scale](review.md).

## Try the ratchet

The interesting behaviour is what happens on a file that was already bad. Add one:

```python
# legacy/report.py -- imported from an old repository, 12 branches in one routine
def render(rows, mode, locale, flags, sink): ...
```

Commit it (the gate will block, so use `git commit --no-verify` for the demo), then make a
one-line change inside it:

```console
$ scitools-hook check --staged
  error  routine.CyclomaticStrict  legacy.report.render  line 4  1.2x limit, was 12, pre-existing
  error  routine.MaxNesting        legacy.report.render  line 4  1.3x limit, was 4,  pre-existing
  ...
summary: 4 errors, 1 warning, 5 pre-existing, 0 blocking | exit 0: no blocking violations
```

Exit 0. Then add one more branch to the same routine:

```console
summary: 10 errors, 1 warning, 2 pre-existing, 9 blocking | exit 1: blocking violations found
```

The full three-act version, with complete output, is on
[The ratchet](../argument/ratchet.md#watch-it-work).

## Where to go next

| You want to | Read |
| --- | --- |
| Run it on a real repository that reports hundreds of findings | [Rescuing a problematic project](rescue.md) |
| Change the limits, or exclude directories | [Configuration](configuration.md) |
| Run it in CI, or through the `pre-commit` framework | [Hooks and CI](hooks-and-ci.md) |
| Give an agent the rules and a way to explore the repository | [Working with agents](agents.md) |
| Understand what a finding means | [Rules and defaults](../reference/rules.md) |
| Know what breaks and why | [Operations](../reference/operations.md) |
