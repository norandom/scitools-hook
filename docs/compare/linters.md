# What this adds beyond ruff and mypy

## The conclusion first

`ruff` and `mypy` are per-file and syntactic. Neither has any opinion about a routine whose
`CountPath` is 16384, and neither compares a change against what was there before, because
neither one has a before to compare against.

They are not competitors. Run all three. But they are also not merely complementary, because
one of them can create work the other cannot see, and that case is worth reading before you
enable it.

## The three axes

| | `ruff` | `mypy` | this gate |
| --- | --- | --- | --- |
| Unit of judgement | a file, a line | a file, a symbol | a routine, class, file, and the project |
| Compares against `HEAD`? | no | no | yes, per entity |
| Knows the call graph? | no | partially, through types | yes |
| Knows the import graph across the project? | no | yes, for types | yes, and reports cycles in it |
| Has an opinion on `CountPath` 16384? | no | no | yes |
| Can it be wrong about your style? | yes, and you configure it | yes | it has no style rules |

`ruff` will tell you a line is 120 characters. It will not tell you that the routine that
line is in went from `CyclomaticStrict` 12 to 13 in this commit. That is not a gap in `ruff`.
It is a different question.

## The one that matters: `ruff --fix` can blind the analyser

This is the sharpest example, and it is measured.

`ruff`'s `UP046`, `UP047` and `UP040` rules push PEP 695 type-parameter syntax. `UP047`
rewrites:

```python
T = TypeVar("T")


def first(items: list[T]) -> T: ...
```

into:

```python
def first[T](items: list[T]) -> T: ...
```

That is a correct, idiomatic modernisation, and `ruff --fix` will apply it across your
repository without asking.

**Understand 6.5 cannot parse a type-parameter list.** Measured against Build 1204, one such
declaration aborts the parse at the declaration and the error cascades to the end of the
file. Everything after it is absent from the database. Not reported as failed. Absent.

The measured constructs, from `report/hints.py`, with a Python 3 interpreter on `PATH`:

```text
def generic[T](x: T) -> T:   expected token '(' at token [        line 1
class Box[T]:                expected token ':' at token [        line 1
type Alias = int             expected newline at token Alias      line 1
except* ValueError:          expected token ':' at token *        line 4
```

Four out of thirteen constructs tried. The other nine parse cleanly.

The failure modes differ, and the difference matters:

| Construct | Parse error | The routine *after* it |
| --- | --- | --- |
| `def f[T]`, `class C[T]`, `async def f[T]`, `def f[**P]` | yes, cascading to end of file | **gone from the database** |
| `type A = int`, `type A = int \| str`, `type A[T] = list[T]` | yes, one error, that line only | present |

The first group is the dangerous one. An absent entity has no metrics, so it breaks no
threshold, so nothing is reported and the run is green.

### What it cost this project

From `pyproject.toml`, which is why these rules are in the ignore list:

> Measured in task 10.4 — five declarations cost five files, and the routine at the
> declaration swallowed the remainder of its file into its own metrics.

The consequences of one such declaration on a single file were measured separately, in
`runner/check.py`:

> Measured on this repository: one PEP 695 type-parameter list took `config/models.py` from
> **15 classes to 3** in the database, **hid 12 findings and fabricated 2**, and the run
> exited 0.

Twelve real findings vanished. Two fabricated findings appeared, because the truncated file
looked different from the file that exists. And the exit code said everything was fine.

### The configuration this project runs

```toml
[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]
# UP040/UP046/UP047 push PEP 695 type-parameter syntax (`def f[T](...)`, `type X = ...`).
# Understand 6.5 cannot parse a type-parameter list: one such declaration aborts the parse
# and takes the REST OF THE FILE out of the database, so the gate silently stops measuring
# there.
ignore = ["UP040", "UP046", "UP047"]
```

Every module in this project that needs a type variable writes it the old way, with the
reason in a comment at the declaration:

```python
# Written as an explicit ``TypeVar`` rather than PEP 695 ``[T]`` syntax: Understand 6.5
# cannot parse a type-parameter list, and one such declaration costs the rest of the file
# from the analysis (measured in task 10.4).
T = TypeVar("T")
```

If you run `ruff` and this gate on the same Python repository, put those three rules in your
ignore list until Understand's Python parser catches up.

## A file that fails to parse is a blocking finding

The defence against the class of problem above is not "avoid PEP 695". It is that the gate
refuses to certify a file it did not read.

```console
$ scitools-hook check --staged
parse errors: these files were NOT fully checked
  Understand could not finish parsing them. Code after a parse error can be missing
  from the analysis, so no rule ran on it: what follows covers only the code that parsed.
  A file in this run's selection that failed to parse is also a blocking analysis.parse_error
  finding below; one outside it -- the interpreter's own standard library, say -- is not.
  pricing/generic.py
    line 4: expected token '(' at token [
    line 5: expected token ':' at token indent
    line 9: expected identifier at token dedent
    ...

pricing/generic.py
  error    analysis.parse_error  line 4
    Understand could not read pricing/generic.py: 6 parse errors, the first at line 4:
    expected token '(' at token [. The analysis stops where the parse stops, so the code
    after it is absent from the database and no rule ran on it -- this file cannot be
    reported as checked.
    hint: PEP 695 type parameters: Understand 6.5 cannot parse a type-parameter list, and
    one of them costs the rest of the file. Declare the variable explicitly instead --
    `T = TypeVar("T")` at module level, then `def generic(x: T) -> T:` and
    `class Box(Generic[T]):` -- which is the same type with a spelling the analysis reads

summary: 1 error, 0 warnings, 0 pre-existing, 1 blocking | 1 file failed to parse, not fully checked | exit 1: blocking violations found
```

This is an `error`, and it blocks, and that is deliberate. From `runner/check.py`:

> A gate that certifies a file it never read has the one failure mode a gate must not have.

Two boundaries stop this being unusable:

- **Only the after side.** A parse error on the before side that this change *fixed* would
  otherwise block the very commit that fixed it.
- **Only files in the selection.** A clean run of this repository was measured producing four
  parse errors inside the *interpreter's own standard library*. Blocking a commit over
  `typing.py` would get the gate switched off. Those are reported and do not block.

You can see both halves in a real run of this repository against CPython 3.14.7, whose
`typing.py` uses PEP 695:

```console
$ scitools-hook check --all
parse errors: these files were NOT fully checked
  Understand could not finish parsing them. Code after a parse error can be missing
  from the analysis, so no rule ran on it: what follows covers only the code that parsed.
  A file in this run's selection that failed to parse is also a blocking analysis.parse_error
  finding below; one outside it -- the interpreter's own standard library, say -- is not.
  /home/linuxbrew/.linuxbrew/Cellar/python@3.14/3.14.7/lib/python3.14/typing.py
    line 2924: expected token ':' at token [
    line 2925: expected identifier at token indent
    ...
```

Reported, named, and not blocking.

If you genuinely have to ship past one, there is an acknowledgement mechanism that requires a
written reason and never lets the file read as clean. See
[Configuration](../guide/configuration.md#acknowledging-a-file-that-does-not-parse).

## What to run together

A reasonable Python stack, with each tool doing the thing it is good at:

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.16.5   # whatever you already pin
    hooks:
      - id: ruff-check
      - id: ruff-format

  - repo: https://github.com/norandom/scitools-hook
    rev: v0.1.0a6   # a tag of this repository
    hooks:
      - id: scitools-hook
```

`mypy` belongs in the same file or in CI, whichever you already do. The order matters only
in that the cheap checks should fail first: `ruff` fails in milliseconds, and there is no
point warming an Understand database for a commit that a formatter will reject.
