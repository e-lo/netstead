"""GMNS data-quality rule pack — entry-point registered into :mod:`corral.quality`.

Re-exports the rule classes + the :func:`register_all` factory wired
as the ``corral.quality.rules`` entry point for the ``netstead``
distribution (see ``packages/netstead/pyproject.toml``).

Once :mod:`corral.quality` runs entry-point discovery on its first
:func:`~corral.quality.run_quality` call, every rule in this module
becomes available via ``corral.quality.list_rules()`` and runs
against any GMNS :class:`Network` passed to ``run_quality(net)``.

Direct invocation (without the entry-point dance) is supported for
tests + ad-hoc scripts:

    >>> from netstead.quality import register_all
    >>> rules = register_all()
    >>> len(rules) >= 9
    True
"""

from .rules import (
    DEFAULT_UNIQUE_COMBINATIONS,
    DisconnectedComponentsRule,
    DuplicateNearNodesRule,
    HighSpeedResidentialRule,
    ImplausibleVcRule,
    LaneCountMismatchRule,
    MissingCriticalFieldsRule,
    NonUniqueCombinationRule,
    OverlappingLinksRule,
    SharpAngleBendsRule,
    register_all,
)

__all__ = [
    "DEFAULT_UNIQUE_COMBINATIONS",
    "DisconnectedComponentsRule",
    "DuplicateNearNodesRule",
    "HighSpeedResidentialRule",
    "ImplausibleVcRule",
    "LaneCountMismatchRule",
    "MissingCriticalFieldsRule",
    "NonUniqueCombinationRule",
    "OverlappingLinksRule",
    "SharpAngleBendsRule",
    "register_all",
]
