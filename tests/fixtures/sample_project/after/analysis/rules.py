# mypy: ignore-errors
"""Rule evaluation; after the change it calls back into the engine, closing a file cycle."""


def apply_rules(names):
    return [name.upper() for name in names]


def rerun(options):
    from analysis.engine import Engine

    return Engine().run(options)
