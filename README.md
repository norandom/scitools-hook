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
