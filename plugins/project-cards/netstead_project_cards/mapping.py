"""GMNS <-> network_wrangler / ProjectCard field names, from the maintained ``mappings/gmns_to_wrangler.yaml``.

Only card I/O uses this: applying a change works in GMNS names throughout.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

__all__ = ["DEFAULT_MAPPING", "FieldMap", "load_mapping"]

#: The maintained mapping file shipped with the plugin.
DEFAULT_MAPPING = Path(str(resources.files("netstead_project_cards").joinpath("mappings/gmns_to_wrangler.yaml")))


@dataclass(frozen=True)
class FieldMap:
    """Name and value translations between GMNS tables and ProjectCard records."""

    selectors: dict[str, dict[str, str]]
    fields: dict[str, dict[str, str]]
    values: dict[str, dict[Any, Any]]
    derived: dict[str, dict[str, Any]]

    def selector(self, table: str) -> tuple[str, str]:
        """``(GMNS key column, card selector key)`` for ``table`` (``KeyError`` for an unmapped table)."""
        ((pk, key),) = self.selectors[table].items()
        return pk, key

    def to_card(self, table: str, column: str) -> str:
        """The card name for GMNS ``table.column`` (unchanged when not listed)."""
        return self.fields.get(table, {}).get(column, column)

    def from_card(self, table: str, name: str) -> str:
        """The GMNS column for card field ``name`` on ``table`` (unchanged when not listed)."""
        reverse = {v: k for k, v in self.fields.get(table, {}).items()}
        return reverse.get(name, name)

    def card_value(self, field: str, value: Any) -> Any:
        """``value`` as the card writes it for card ``field``."""
        return self.values.get(field, {}).get(value, value)

    def gmns_value(self, field: str, value: Any) -> Any:
        """A card ``value`` for ``field``, back as the GMNS value."""
        reverse = {v: k for k, v in self.values.get(field, {}).items()}
        return reverse.get(value, value)

    def derive(self, record: dict[str, Any]) -> dict[str, int]:
        """Derived card fields (``1``/``0``) for a GMNS link record; a rule whose column is absent yields nothing."""
        out: dict[str, int] = {}
        for name, rule in self.derived.items():
            raw = record.get(rule["column"])
            if raw is None:
                continue
            uses = {u.strip().lower() for u in str(raw).split(",")}
            out[name] = int(bool(uses & {str(v).lower() for v in rule["any_of"]}))
        return out


@lru_cache(maxsize=8)
def load_mapping(path: str | Path | None = None) -> FieldMap:
    """Read a mapping file (default: :data:`DEFAULT_MAPPING`); cached per path."""
    data = yaml.safe_load(Path(path or DEFAULT_MAPPING).read_text(encoding="utf-8")) or {}
    if data.get("version") != 1:
        raise ValueError(f"unsupported mapping version {data.get('version')!r} (expected 1)")
    return FieldMap(
        selectors=dict(data.get("selectors") or {}),
        fields=dict(data.get("fields") or {}),
        values={k: dict(v or {}) for k, v in (data.get("values") or {}).items()},
        derived=dict(data.get("derived") or {}),
    )
