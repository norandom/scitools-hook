# mypy: ignore-errors
"""Rule evaluation; before the change it does not depend on the engine."""


def apply_rules(names):
    return [name.upper() for name in names]
