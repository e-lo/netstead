"""GMNS <-> network_wrangler / ProjectCard field names, from the maintained ``mappings/gmns_to_wrangler.yaml``.

Only card I/O uses this: applying a change works in GMNS names throughout.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

__all__ = ["DEFAULT_MAPPING", "FieldMap", "load_mapping"]

#: The maintained mapping file shipped with the plugin.
DEFAULT_MAPPING = Path(str(resources.files("netstead_project_cards").joinpath("mappings/gmns_to_wrangler.yaml")))


def _frozen(table: Mapping[str, Mapping[Any, Any]]) -> Mapping[str, Mapping[Any, Any]]:
    return MappingProxyType({k: MappingProxyType(dict(v)) for k, v in table.items()})


def _reversed(table: Mapping[str, Mapping[Any, Any]]) -> Mapping[str, Mapping[Any, Any]]:
    return _frozen({k: {v: name for name, v in inner.items()} for k, inner in table.items()})


@dataclass(frozen=True)
class FieldMap:
    """Name and value translations between GMNS tables and ProjectCard records (read-only)."""

    selectors: Mapping[str, Mapping[str, str]]
    fields: Mapping[str, Mapping[str, str]]
    values: Mapping[str, Mapping[Any, Any]]
    derived: Mapping[str, Mapping[str, Any]]
    _card_fields: Mapping[str, Mapping[str, str]] = field(init=False, repr=False, compare=False)
    _card_values: Mapping[str, Mapping[Any, Any]] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Freeze the tables (read-only views) and build the reverse lookups once."""
        for name in ("selectors", "fields", "values", "derived"):
            object.__setattr__(self, name, _frozen(getattr(self, name)))
        object.__setattr__(self, "_card_fields", _reversed(self.fields))
        object.__setattr__(self, "_card_values", _reversed(self.values))

    def selector(self, table: str) -> tuple[str, str]:
        """``(GMNS key column, card selector key)`` for ``table`` (``KeyError`` for an unmapped table)."""
        ((pk, key),) = self.selectors[table].items()
        return pk, key

    def to_card(self, table: str, column: str) -> str:
        """The card name for GMNS ``table.column`` (unchanged when not listed)."""
        return self.fields.get(table, {}).get(column, column)

    def from_card(self, table: str, name: str) -> str:
        """The GMNS column for card field ``name`` on ``table`` (unchanged when not listed)."""
        return self._card_fields.get(table, {}).get(name, name)

    def card_value(self, field: str, value: Any) -> Any:
        """``value`` as the card writes it for card ``field``."""
        return self.values.get(field, {}).get(value, value)

    def gmns_value(self, field: str, value: Any) -> Any:
        """A card ``value`` for ``field``, back as the GMNS value."""
        return self._card_values.get(field, {}).get(value, value)

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


def load_mapping(path: str | Path | None = None) -> FieldMap:
    """Read a mapping file (default: :data:`DEFAULT_MAPPING`); cached per resolved path.

    Raises:
        ValueError: an unsupported ``version``, or a section of the wrong shape (named in the message).
    """
    return _load(Path(path or DEFAULT_MAPPING).resolve())


@lru_cache(maxsize=8)
def _load(path: Path) -> FieldMap:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, Mapping):
        raise ValueError(f"{path}: a mapping file must be a mapping of sections")
    if data.get("version") != 1:
        raise ValueError(f"unsupported mapping version {data.get('version')!r} (expected 1)")
    sections = {name: _tables(data.get(name), name, path) for name in ("selectors", "fields", "values", "derived")}
    for table, selector in sections["selectors"].items():
        if len(selector) != 1:
            raise ValueError(f"{path}: selectors.{table} needs exactly one 'GMNS key: card key' pair")
    for name, rule in sections["derived"].items():
        if "column" not in rule:
            raise ValueError(f"{path}: derived.{name}: needs a column")
        if not isinstance(rule.get("any_of"), list):
            raise ValueError(f"{path}: derived.{name}: any_of must be a list of uses")
    return FieldMap(**sections)


def _tables(section: Any, name: str, path: Path) -> dict[str, dict[Any, Any]]:
    """A ``{table: {key: value}}`` section (absent or empty is ``{}``)."""
    section = section or {}
    if not isinstance(section, Mapping):
        raise ValueError(f"{path}: {name} must be a mapping")
    out = {}
    for table, inner in section.items():
        inner = inner or {}
        if not isinstance(inner, Mapping):
            raise ValueError(f"{path}: {name}.{table} must be a mapping")
        out[table] = dict(inner)
    return out
