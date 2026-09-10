"""The lean-code rules: what a change leaves behind that nothing needs.

Six rules and a delta, each a pure function over a :class:`~scitools_hook.models.snapshot.
ProjectSnapshot` and the affected set, in the shape the structural rules of
:mod:`scitools_hook.analysis.structure` already have. The package exists because six rule
modules and a delta are not one module, and because the family shares one configuration
section, one severity convention -- ``Severity | None``, where ``None`` is off -- and one
promise: it imports ``config`` and ``models`` and nothing else.
"""
