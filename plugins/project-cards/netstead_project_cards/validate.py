"""Check a ProjectCard dict with the ``projectcard`` package's own validator (its schema, its rules).

``projectcard.validate_card`` fills schema defaults into the dict it is given (``parse_defaults=True``), so a card
missing a required ``ignore_missing`` passes, and is changed in place. We validate a deep copy with
``parse_defaults=False``: the file must say what the schema requires. jsonschema stops at its best-matching error,
so :func:`card_problems` returns at most one problem. (The schema does not type-check property values; the change
model does, see :mod:`netstead_project_cards.types`.)
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from projectcard import validate_card
from projectcard.errors import ProjectCardValidationError

__all__ = ["card_problems"]


def card_problems(card: Mapping[str, Any]) -> list[str]:
    """What makes ``card`` an invalid ProjectCard, as ``["<json path>: <message>"]`` (empty when it is valid)."""
    try:
        validate_card(copy.deepcopy(dict(card)), parse_defaults=False)
    except ProjectCardValidationError as exc:
        cause = exc.__cause__
        if cause is None:  # a pycode error (the plugin never writes pycode) carries its own message
            return [str(exc)]
        where = "/".join(str(p) for p in cause.absolute_path) or "(card)"
        return [f"{where}: {cause.message}"]
    return []
