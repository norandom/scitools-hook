# Languages

## The tool is written in Python. It does not only check Python

The gate is a Python package installed with `uv`. The analysis is not Python's; it is
Understand's. A C++ team, a Java team or a Fortran team installs a Python-packaged hook and
gates their own language, with the same commands, the same ratchet and the same defaults.

The complexity axis is language-agnostic in a way that a security rule pack is not.
`CyclomaticStrict` counts decision points, and it counts them the same way in Ada as in
TypeScript. A CodeQL query pack has to be written per language and per vulnerability class.

## What Understand build 1204 accepts

Twelve languages, 58 file extensions.

| Language | Extensions |
| --- | --- |
| Ada | `.a` `.ada` `.adb` `.ads` `.gpr` |
| Assembly | `.asm` `.s` |
| Basic | `.vb` |
| C# | `.cs` |
| C++ | `.C` `.H` `.c` `.cc` `.cpp` `.cu` `.cuh` `.cxx` `.h` `.hh` `.hpp` `.hxx` `.inl` `.m` `.mm` |
| Fortran | `.F` `.F90` `.f` `.f03` `.f77` `.f90` `.f95` `.for` `.ftn` |
| Java | `.java` |
| Jovial | `.cpl` `.jov` |
| Pascal | `.dfm` `.dpr` `.fmx` `.pas` `.sp` `.sql` |
| Python | `.py` `.upy` |
| VHDL | `.vhd` `.vhdl` |
| Web | `.cjs` `.css` `.cts` `.htm` `.html` `.js` `.mjs` `.mts` `.php` `.ts` `.tsx` `.xml` |

Anything else exits 1 with `Error: JavaScript is not a valid language`.

The table is **case-sensitive**, because the installed one is: `a.C` is C++, and `a.PY` is
nothing at all.

## Three things about that table you would get wrong by reading the documentation

### `.sql` maps to Pascal

Not a typo. Understand's own `FileTypes` table calls it `Sql`, and no *language* is named
that. It enters the database under **Pascal**, which was found only by building a database
per language over a tree holding one file per extension and reading `und list files` back.

From `understand/database.py`:

> `.sql` is here because the measurement said so and reading the table would not have: it is
> `Sql` there, no language is called that, and it enters under **Pascal** — found by asking
> each of the twelve in turn. It is the one entry no amount of care with the table would have
> produced, and it is why the contract test measures both directions instead of one.

Two other entries have the same origin. `.m` and `.mm` are C++, not a language of their own,
even though the `FileTypes` table names `Objective-C`. And `.txt`, `.pl`, `.bat`, `.cbl` and
`.bas` are absent although the table names them, while `.vb` is present.

A contract test re-measures the whole map against the installed build **in both directions**,
so a build whose table differs says so instead of quietly analysing less.

### `Web` is one language, and it will enrol your assets as source

`Web` spans JavaScript, TypeScript, CSS, HTML, PHP and XML. There is no separate
`JavaScript` and no separate `HTML`. A `.css` file and a `.tsx` file are entities of the same
language.

The practical consequence, measured on the quickstart repository by adding three ordinary web
assets to it:

```console
$ scitools-hook check --staged
created the after analysis database with Python, Web enabled
site/app.css
  warning  file.RatioCommentToCode  0x limit
    file site/app.css RatioCommentToCode is 0, which is below the minimum 0.1
site/index.html
  warning  file.RatioCommentToCode  0x limit
    file site/index.html RatioCommentToCode is 0, which is below the minimum 0.1
site/partial.html
  warning  file.RatioCommentToCode  0x limit
    file site/partial.html RatioCommentToCode is 0, which is below the minimum 0.1

summary: 0 errors, 3 warnings, 0 pre-existing, 0 blocking
```

Three assets, three file-scope findings, none of which is about code. They parsed fine —
these are ordinary, valid files — but they are being judged by rules written for source.

Two further things are visible in that one line of output:

- **The enabled language set is detected from the files present.** Adding an HTML file took
  the run from `Python enabled` to `Python, Web enabled`. Changing the language set discards
  and rebuilds both databases, so the first run after adding a new file type is a slow one.
- **A web asset that Understand cannot parse is a blocking finding**, like any other
  unreadable file in the selection. This has been reported on a real repository, with the
  message `expected selector at token EOF`. Ordinary HTML and CSS reproduced no such failure
  here, so treat the specific message as second-hand; the mechanism is not, and is documented
  under [parse errors](../reference/operations.md#a-file-that-does-not-parse-is-a-blocking-finding).

The fix is to exclude what is not code:

```toml
[project]
exclude = [
    ".git/**", "node_modules/**", "dist/**", "build/**",
    # Web assets are Web *source* to Understand. Exclude the ones that are not code.
    "**/*.css",
    "**/*.html",
    "**/*.xml",
    "static/**",
    "**/*.min.js",
]
```

Lists replace rather than merge, so repeat the defaults you want to keep. The shipped
defaults already exclude `*.min.js` and `*.generated.*`.

### Coverage is not uniform, and the gate says so

Not every metric exists for every language. The gate reports what it could not evaluate
rather than skipping it quietly, because a metric that is quietly absent looks exactly like a
metric that is always inside its limit: the threshold never fires, the run is green, and no
output says the rule was not evaluated.

```console
summary: 231 errors, 131 warnings, 0 pre-existing, 231 blocking
         | 1 file failed to parse, not fully checked
         | 1 metric unavailable, those limits were not evaluated
```

The two segments after the counts are the honest half. They appear only when there is
something to say.

Measured against Build 1204, on 2026-08-30:

| Metric | Availability |
| --- | --- |
| `CountParams` | **Unavailable for all twelve.** Understand's native metric is unset. The gate computes it itself, as the entities a routine defines with kind `Parameter ~Catch`. |
| `CountDeclMethodNonStub` | Synthetic: `CountDeclMethod - 2 * CountDeclPropertyAuto`. |
| `CountDeclPropertyAuto` | **C# alone.** So on every other language, including Python and C++, `CountDeclMethodNonStub == CountDeclMethod`. |
| `PercentLackOfCohesion` | Basic, C#, C++, Java, Pascal. Not Python. |
| Class-scope metrics generally | Ada, Assembly, Fortran, Jovial and VHDL answer **no class metric at all** — an empty list, not an error. |

A configured threshold whose metric exists for none of the enabled languages is a
configuration error and exits 2. A shipped default whose metric is missing for one language
is dropped for that language and reported once per run.

There is one more asymmetry worth knowing if you configure by hand: `c++` is not a
kind-string language inside Understand. `Metric.list("c++ file …")` answers nothing, while
`Metric.list("c file …")` answers 42, and Understand's kind long names for C++ entities read
`C Class Type`. The gate carries exactly one alias to close that gap. Every other language
answers under its own name.

## Maturity, per language

This is `0.1.0a4`. The gradient is real:

| Language | Status |
| --- | --- |
| **Python 3** | Exercised end to end. Unit tests, contract tests against a real licensed Understand, and an `e2e` suite that drives real `git commit` runs through the installed shim. |
| **C++** | Exercised in the contract suite — it is what proves `EntityKey` tells a real overload pair apart, since two overloads share a long name and differ only in their parameter list. Not exercised end to end. |
| **Ada, Assembly, Basic, C#, Fortran, Java, Jovial, Pascal, VHDL, Web** | Wired through the same extension map, and expected to work. **Untested.** |

"Untested" is the accurate word, and it is not "unsupported". Nothing in the analysis path is
Python-specific: the extension map decides which languages `und create -languages` is given,
Understand does the parsing, and the metric, ratchet and structure logic operates on
`EntityKey`s that carry no language at all.

If you point it at a Fortran codebase, it should work and nobody has run it. The two things
most likely to need attention are the class-scope thresholds, which Fortran answers nothing
for, and the default `RatioCommentToCode` minimum, which is calibrated on nothing in
particular.

## Enabling languages explicitly

By default the enabled set is detected from the files that survive `include` and `exclude`.
You can pin it:

```toml
[project]
languages = ["C++", "Python"]
```

Pinning is worth doing on a mixed repository where a stray file type would otherwise change
the language set and trigger a full rebuild of both databases.
