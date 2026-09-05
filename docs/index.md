# scitools-hook

`scitools-hook` measures the routines, classes and files that a commit touches, compares
each one against what it was before the commit, and refuses the commit that makes any of
them measurably worse. The measurement comes from
[SciTools Understand](https://scitools.com/), so it covers twelve languages rather than the
one the tool happens to be written in. Debt that was already there is reported as
`pre-existing` and does not block. Only regression blocks.

```text
summary: 4 errors, 1 warning, 5 pre-existing, 0 blocking | exit 0: no blocking violations
```

That line is the whole design. Four errors are real and are printed. None of them blocks,
because none of them got worse in this commit.

## The argument

**Situation.** A coding agent is very good on a small codebase. The whole relevant
neighbourhood of a change fits in the context window, the agent reads it, and the edit it
produces is usually right the first time.

**Complication.** That stops being true as the project grows, and the usual response is to
reach for a larger, more expensive model. A larger model buys a larger context window. It
does not buy a smaller problem, so it pays to work around the thing that is actually going
wrong instead of fixing it.

**Question.** What is actually degrading? Not the model. Not the prompt.

**Answer.** The amount of code that has to be held in mind at once in order to change one
routine safely. Call it the working set. A routine with a `CyclomaticStrict` of 45 and a
`CountPath` of 9.6 &times; 10<sup>8</sup> has a working set no agent holds, at any price. It
does not reason about that routine; it pattern-matches at it and guesses. The guess compiles,
the tests that exist still pass, and a human finds the defect later.

An agent's working set is bounded. A codebase that outgrows it stops being editable by
agents at any model price. This gate keeps the shape of the code inside that bound, one
commit at a time, with a ratchet.

[Read the argument in full &rarr;](argument/working-set.md)

## This is not a fourth linter

You already run `ruff`. You probably run `mypy`, and there may be a CodeQL workflow on the
repository as well. The reasonable first reaction to another static-analysis tool in CI is
that it will produce more noise on the same axis.

It is a different axis.

| Question | Answered by |
| --- | --- |
| Is this file syntactically and stylistically correct? | `ruff`, `flake8`, `clang-tidy` |
| Do the types line up? | `mypy`, `pyright`, `tsc` |
| Does this code have a defect or a vulnerability? | CodeQL, Semgrep |
| Is this change safe to make in a routine of this shape, and did it make that shape worse? | this gate |

`ruff` has no opinion about a routine whose `CountPath` is 16384, because `CountPath` is not
a lint. Neither `ruff` nor `mypy` compares a file against what it was before the change,
because neither one has a before to compare against. The gate does both, and it does nothing
else: it never comments on style, never rewrites code, and has no view on whether your types
are right.

The two tools are not merely complementary. One of them can create work the other cannot
see: `ruff`'s `UP047` rewrites a `TypeVar` into PEP 695 `def f[T](x)` syntax, which
Understand before 7.2 could not parse. The parse aborted at the declaration and the rest of
the file silently left the database. That is documented, with the measurement, in
[What this adds beyond ruff and mypy](compare/linters.md); 8.0 reads it, and the file that
no build reads is still one construct away, so the defence stays.

[Compare against CodeQL, Semgrep, LOC tools and import-graph tools &rarr;](compare/tools.md)

## How you would prove this wrong

A document that cannot be wrong is not an argument, so here is the disproof condition,
stated plainly:

!!! quote "The claim, and what would falsify it"

    **Claim.** Agent effectiveness on a codebase decays as the working set of an average
    change grows, and a complexity ceiling enforced per commit keeps it from growing.

    **Disproof.** If your agents stay effective as your codebase grows, with no complexity
    ceiling of any kind, the claim is wrong and this tool solves a problem you do not have.

That is testable on your own repository without installing anything: take the ten routines
your agents most often get wrong, and look at their `CyclomaticStrict` and `CountPath`
against the median for the project. If there is no relationship, stop reading.

## It is written in Python. It does not only check Python

The tool is packaged as a Python project and installed with `uv`. That is the implementation
language, not the coverage. The analysis is Understand's, and Understand accepts
**twelve languages across 58 file extensions** (build 1204; build 1262 adds Rust for Cargo
projects, see [Understand 8.0](reference/understand-8.md)):

```text
Ada       .a .ada .adb .ads .gpr
Assembly  .asm .s
Basic     .vb
C#        .cs
C++       .C .H .c .cc .cpp .cu .cuh .cxx .h .hh .hpp .hxx .inl .m .mm
Fortran   .F .F90 .f .f03 .f77 .f90 .f95 .for .ftn
Java      .java
Jovial    .cpl .jov
Pascal    .dfm .dpr .fmx .pas .sp .sql
Python    .py .upy
VHDL      .vhd .vhdl
Web       .cjs .css .cts .htm .html .js .mjs .mts .php .ts .tsx .xml
```

A C++ team or a Java team installs a Python-packaged hook and gates their own language. The
complexity axis is language-agnostic in a way that a rule pack is not: `CyclomaticStrict`
means the same thing in Fortran as in TypeScript, whereas a CodeQL query pack has to be
written per language and per vulnerability class.

Coverage is not uniform across the twelve, and the gate reports what it cannot measure
rather than skipping it quietly. The details, including why `.sql` maps to Pascal and why an
HTML asset can fail to parse as source, are on the
[Languages](guide/languages.md) page.

## Maturity

This is version `0.1.0a7`. An alpha. The gradient across the twelve languages is real and
worth stating before you decide whether to try it:

| Language | Status |
| --- | --- |
| Python 3 | Exercised end to end: unit tests, contract tests against a real licensed Understand, and an `e2e` suite that drives real `git commit` runs through the installed hook. |
| C++ | Exercised in the contract suite. It is what proves `EntityKey` tells a real overload pair apart. Not exercised end to end. |
| Ada, Assembly, Basic, C#, Fortran, Java, Jovial, Pascal, VHDL, Web | Wired through the same extension map and expected to work. **Untested.** |

"Untested" is the accurate word, and it is not "unsupported". If you are deciding whether to
point this at a Fortran codebase: it should work, and nobody has run it. The extension map
itself is not a guess. A contract test re-measures it against the installed Understand build
in both directions, so a wrong or missing suffix fails loudly rather than mapping a file to
nothing.

## What you get

- **A commit-time gate.** `scitools-hook check --staged`, as a native `.git/hooks` shim or
  as a `pre-commit` framework hook. It analyses the staged change against `HEAD`, not the
  whole repository, so it is fast enough to sit in front of every commit.
- **A ratchet.** Per-entity before and after, inside one commit. You never pay down the
  debt you have; you are forbidden from adding to it.
- **Review aids.** `scitools-hook explain --graphs --impact --out DIR` exports SVG
  dependency and butterfly graphs plus an impact set, so a reviewer can look at the shape of
  a large agent-authored change before opening a single file.
- **Machine-readable output.** `--format json` and SARIF 2.1.0, with a remediation hint on
  every finding.
- **Rules an agent reads.** `scitools-hook agent-rules --write AGENTS.md` writes the
  effective limits into your agent instructions file, so the agent knows the numbers before
  it writes the code rather than after the commit is refused.

## Getting it

**This tool is not on PyPI and will not be.** It is distributed as a GitHub release, so
every install form names the source:

```bash
uv tool install git+https://github.com/norandom/scitools-hook
```

`pip install scitools-hook`, `uv tool install scitools-hook` and a bare `uvx scitools-hook`
all resolve to nothing. See [Install](guide/install.md) for the wheel-from-a-release form and
for why this distinction was a live defect rather than a documentation nicety.

## Requirements

An existing SciTools Understand installation, version 6.5 or later, with a licence. 8.0
(Build 1262) is the version the tool is measured on now; see
[Understand 8.0](reference/understand-8.md) for what changed. The tool never installs
Understand and never bundles it.

!!! warning "The PyPI package named `understand` is not SciTools Understand"

    Do not `pip install understand`. It is an unrelated project. This tool uses the API
    shipped inside your Understand installation.

[Install it &rarr;](guide/install.md) &middot;
[Run it on a repository in five minutes &rarr;](guide/quickstart.md) &middot;
[Point it at a real, messy codebase &rarr;](guide/rescue.md)

!!! tip "If your first run reports hundreds of findings, that is expected"

    A first `check --all` on a real repository returns a large number, and it is an inventory
    rather than a verdict — on this repository, 99.9% of routines are already inside the
    default complexity limit and only 3 of 230 findings are `routine.CyclomaticStrict` at all.
    [Rescuing a problematic project](guide/rescue.md) is the six-step path from that number to
    a gate you can actually commit through.

## About the numbers in these documents

Every measured claim here carries the command that produced it and the build it was measured
against. Where a number came from a repository you cannot see, the document says so. Where
something is expected but unverified, the document says "unverified" rather than rounding it
up to a fact.

That is not a stylistic preference. It is the same rule the source code is written under:
an inference stated as a fact is a defect, and the project's own history includes several
corrections where a claim turned out to have been inferred rather than measured.
