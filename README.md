# scitools-hook

A maintainability gate for git pre-commit hooks, CI and coding agents, backed by
[SciTools Understand](https://scitools.com/).

## Install

Run it without creating a virtual environment:

```bash
uvx scitools-hook --help
```

`scitools-hook` needs an existing SciTools Understand installation (`und` and the
bundled Python API). It never installs Understand for you.

> **Note:** the PyPI package named `understand` is unrelated to SciTools Understand.
> Do not `pip install understand`; this tool uses the API shipped inside your
> Understand installation.

## Development

The project's gates run in one command, and none of them needs an Understand licence:

```bash
uv run pytest --cov=src/scitools_hook --cov-branch --cov-report=term-missing --cov-fail-under=85
```

That single invocation covers all four gates:

| Gate | Where it lives |
|------|----------------|
| Import-direction (the allowed-import matrix, and `worker.py` under `python -I`) | `tests/test_import_direction.py` |
| `ruff check` and `ruff format --check` | `tests/test_quality_gates.py` |
| `mypy --strict` (configured: `strict = true`, `files = ["src"]`) | `tests/test_quality_gates.py` |
| Branch coverage of `src/`, threshold 85% | the `--cov-*` flags above |

The coverage threshold applies to **all** of `src/scitools_hook` with no omissions: the
adapters that only run against a licensed install are covered through their fixture-backed
fakes, so no module has to be excluded to reach the number.

The suite is licence-free by construction — the tests marked `contract` are skipped unless
`SCITOOLS_HOME` points at a licensed install:

```bash
SCITOOLS_HOME=/path/to/scitools uv run pytest -m contract
```
