"""Shape checks for quality-rule thresholds, shared by the rules and by settings validation.

Dependency-free on purpose: :mod:`netstead.config` checks ``validation.rules`` when settings load (so the
Workbench's ``SetSetting`` rejects a bad value up front), and importing :mod:`netstead.quality` there would pull in
ibis. The rules call the same functions at run time as a backstop for configs built in code.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = ["check_combinations", "check_epsilon", "check_rule_thresholds"]

DUPLICATE_NEAR_NODES = "quality.duplicate_near_nodes"
NON_UNIQUE_COMBINATION = "quality.non_unique_combination"


def check_epsilon(value: Any) -> float:
    """``epsilon_units`` as a float; raise :class:`ValueError` unless it is a number >= 0."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{DUPLICATE_NEAR_NODES}: thresholds['epsilon_units'] must be a number, got {value!r}")
    if value < 0:
        raise ValueError(f"{DUPLICATE_NEAR_NODES}: thresholds['epsilon_units'] must be >= 0, got {value}")
    return float(value)


def check_combinations(value: Any) -> dict[str, list[list[str]]]:
    """``combinations`` as ``{table: [[column, ...], ...]}``; raise :class:`ValueError` for any other shape."""
    expected = (
        f"{NON_UNIQUE_COMBINATION}: thresholds['combinations'] must map table names to lists of column-name "
        'lists, e.g. {"link_tod": [["link_id", "time_day"]]}'
    )
    if not isinstance(value, Mapping):
        raise ValueError(f"{expected}; got {type(value).__name__}")
    out: dict[str, list[list[str]]] = {}
    for table_name, combos in value.items():
        if isinstance(combos, str) or not isinstance(combos, (list, tuple)):
            raise ValueError(f"{expected}; {table_name!r} maps to {combos!r}")
        for combo in combos:
            if (
                isinstance(combo, str)
                or not isinstance(combo, (list, tuple))
                or not combo
                or not all(isinstance(c, str) for c in combo)
            ):
                raise ValueError(f"{expected}; {table_name!r} has the combination {combo!r}")
        out[str(table_name)] = [list(c) for c in combos]
    return out


def check_rule_thresholds(code: str, thresholds: Mapping[str, Any]) -> None:
    """Raise :class:`ValueError` if a known rule's thresholds have the wrong shape (other rules pass through)."""
    if code == DUPLICATE_NEAR_NODES and "epsilon_units" in thresholds:
        check_epsilon(thresholds["epsilon_units"])
    if code == NON_UNIQUE_COMBINATION and "combinations" in thresholds:
        check_combinations(thresholds["combinations"])
