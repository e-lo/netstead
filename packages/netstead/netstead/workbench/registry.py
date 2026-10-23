"""Open-network registry: one :class:`NetworkHandle` bundle per loaded network.

A handle bundles components (``roadway`` today; ``transit`` is reserved for the
GTFS feed in phase P6) and carries a ``version`` that every mutation bumps.
Derived artifacts (pandas frames, binary map buffers) are cached per version,
so a mutation invalidates them, unlike the old viz app's permanent caches.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from netstead import Network

from .redact import scrub, scrub_source

__all__ = [
    "COMPONENTS",
    "Component",
    "NetworkHandle",
    "NetworkRegistry",
    "as_pandas",
    "default_label",
    "derived_copy",
]

Component = Literal["roadway", "transit"]
COMPONENTS: tuple[Component, ...] = ("roadway", "transit")

#: Other canonical GMNS tables exposed in the data-table view when present (kept lazy).
_EXTRA_TABLES = ("lanes", "segments", "segment_lanes", "zones", "movements", "link_tod")
#: A leading ``scheme:`` (two or more letters, so a Windows drive letter is not a scheme).
_HAS_SCHEME = re.compile(r"\s*[A-Za-z][A-Za-z0-9+.-]+:")
#: Directory/file stems too generic to name a network by.
_GENERIC_NAMES = {"csv", "parquet", "duckdb", "zip", "data", "network", ""}


def default_label(source: str) -> str:
    """A human label for ``source``: its stem, or its parent's name when the stem is generic.

    Only a URL's path is used: its query string (a presigned signature) and userinfo never reach a label.
    """
    text = str(source)
    if _HAS_SCHEME.match(text):  # a URL, even a malformed one: drop its query string and userinfo
        text = scrub_source(text)
    p = Path(text.split("://", 1)[-1].rstrip("/"))
    for candidate in (p.stem, p.parent.name):
        if candidate not in _GENERIC_NAMES:
            return candidate
    return "network"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "network"


def as_pandas(table: Any) -> pd.DataFrame:
    """Materialize an ibis/corral table as pandas."""
    return table.to_pandas() if hasattr(table, "to_pandas") else table.execute()


def derived_copy(net: Network) -> Network:
    """A copy of ``net`` whose tables can be edited without touching ``net`` (copy-on-write).

    The :class:`~corral.dataset.Table` wrappers are copied; their expressions are immutable, so
    they are shared, not duplicated. The copy has no sync-state tracker: it was never read from,
    and must never be written back to, the base network's source.
    """
    tables = {name: replace(table, metadata=dict(table.metadata)) for name, table in net.tables.items()}
    return replace(net, tables=tables, dirty_tracker=None, metadata=dict(net.metadata))


def _extra_tables(net: Network) -> dict[str, Any]:
    """Additional GMNS tables the network carries, kept lazy (best-effort), keyed by singular name."""
    out: dict[str, Any] = {}
    for accessor in _EXTRA_TABLES:
        try:
            tbl = getattr(net, accessor)
            if tbl is not None and tbl.count() > 0:
                out[accessor[:-1] if accessor.endswith("s") else accessor] = tbl
        except (AttributeError, KeyError, ValueError, FileNotFoundError):
            continue
    return out


@dataclass
class NetworkHandle:
    """A loaded network bundle plus its per-version derived-data cache."""

    id: str
    label: str
    source: str
    roadway: Network
    transit: Any | None = None
    version: int = 0
    lineage: list[str] = field(default_factory=list)
    derived_from: str | None = None  # the id of the network this one is a copy-on-write copy of
    _cache: dict[tuple[str, int], Any] = field(default_factory=dict, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    #: Increases on every change of the tables, including a rollback that sets ``version`` back:
    #: caches are keyed by it, so an artifact built before a rollback is never stored after one.
    _generation: int = field(default=0, repr=False)

    def cached(self, key: str, build: Callable[[], Any]) -> Any:
        """Return ``build()`` memoised for the current version.

        ``build`` runs *outside* the handle lock, so a slow DuckDB read for one artifact never
        blocks a cached read of another. Two threads may both build a missing artifact; the first
        stored wins. A result built while the network was mutated (``bump``) or rolled back
        (``restore``) is returned but not cached, since it may describe other tables.
        """
        with self._lock:
            generation = self._generation
            slot = (key, generation)
            if slot in self._cache:
                return self._cache[slot]
        value = build()
        with self._lock:
            if self._generation != generation:
                return value
            return self._cache.setdefault(slot, value)

    def prime(self, **artifacts: Any) -> None:
        """Seed the current version's cache with artifacts built elsewhere (e.g. frames loaded off the lock)."""
        with self._lock:
            for key, value in artifacts.items():
                self._cache[(key, self._generation)] = value

    def bump(self) -> int:
        """Mark the network mutated: increment ``version`` and drop every cached artifact."""
        with self._lock:
            self.version += 1
            self._generation += 1
            self._cache.clear()
            return self.version

    def replace_tables(self, tables: Mapping[str, tuple[Any, bool]]) -> int:
        """Swap in new roadway table ``(expr, dirty)`` pairs in one step, then :meth:`bump`; return the version.

        Each replaced table's sync stamp is dropped (as corral's ``apply_edit`` does), since the stamp no
        longer describes the table.
        """
        with self._lock:
            self._set_tables(tables)
            tracker = self.roadway.dirty_tracker
            if tracker is not None:
                for name in tables:
                    tracker.mark_dirty(name)
            return self.bump()

    def restore(self, tables: Mapping[str, tuple[Any, bool]], version: int) -> None:
        """Put back roadway table ``(expr, dirty)`` pairs and the ``version`` they had (a rollback); drop caches."""
        with self._lock:
            self._set_tables(tables)
            self.version = version
            self._generation += 1
            self._cache.clear()

    def _set_tables(self, tables: Mapping[str, tuple[Any, bool]]) -> None:
        for name, (expr, dirty) in tables.items():
            table = self.roadway.tables[name]
            table.expr, table.dirty = expr, dirty

    def links_df(self) -> pd.DataFrame:
        """The roadway ``link`` table as pandas (cached per version)."""
        return self.cached("links_df", lambda: as_pandas(self.roadway.links))

    def nodes_df(self) -> pd.DataFrame:
        """The roadway ``node`` table as pandas (cached per version)."""
        return self.cached("nodes_df", lambda: as_pandas(self.roadway.nodes))

    def geometry_df(self) -> pd.DataFrame | None:
        """The roadway ``geometry`` table (link shapes by ``geometry_id``) as pandas, or ``None`` when absent."""
        return self.cached(
            "geometry_df", lambda: None if self.roadway.geometry is None else as_pandas(self.roadway.geometry)
        )

    def node_xy(self) -> dict[Any, tuple[float, float]]:
        """``node_id -> (lon, lat)`` for anchor placement (cached per version)."""
        return self.cached(
            "node_xy", lambda: {r.node_id: (float(r.x_coord), float(r.y_coord)) for r in self.nodes_df().itertuples()}
        )

    def tables(self) -> dict[str, Any]:
        """Grid-browsable tables: eager ``link``/``node`` frames plus lazy extras."""
        return self.cached(
            "tables", lambda: {"link": self.links_df(), "node": self.nodes_df(), **_extra_tables(self.roadway)}
        )

    def summary(self) -> dict[str, Any]:
        """JSON-safe description for the session state."""
        return {
            "id": self.id,
            "label": self.label,
            "source": scrub(self.source, limit=None),  # shown in the browser: no signed query string
            "version": self.version,
            "components": [c for c in COMPONENTS if getattr(self, c) is not None],
            "links": len(self.links_df()),
            "nodes": len(self.nodes_df()),
            "lineage": list(self.lineage),
            "derived_from": self.derived_from,
        }


class NetworkRegistry:
    """Ordered collection of open :class:`NetworkHandle` objects keyed by id."""

    def __init__(self) -> None:
        """Start empty."""
        self._handles: dict[str, NetworkHandle] = {}

    def add(self, net: Network, *, source: str, label: str | None = None, net_id: str | None = None) -> NetworkHandle:
        """Register ``net`` under a unique id derived from ``net_id``/``label``/``source``."""
        derived = default_label(source)
        base = _slug(net_id or label or derived)
        hid, n = base, 2
        while hid in self._handles:
            hid, n = f"{base}-{n}", n + 1
        # an explicit label is kept; a derived one falls back to the unique id on collision
        handle = NetworkHandle(id=hid, label=label or (derived if hid == base else hid), source=source, roadway=net)
        self._handles[hid] = handle
        return handle

    def get(self, net_id: str) -> NetworkHandle:
        """Return the handle for ``net_id`` or raise ``KeyError("unknown network ...")``."""
        try:
            return self._handles[net_id]
        except KeyError:
            raise KeyError(f"unknown network {net_id!r}") from None

    def remove(self, net_id: str) -> None:
        """Forget ``net_id`` (``KeyError`` if unknown)."""
        self.get(net_id)
        del self._handles[net_id]

    def ids(self) -> list[str]:
        """Handle ids in insertion order."""
        return list(self._handles)

    def __iter__(self) -> Iterator[NetworkHandle]:
        """Iterate handles in insertion order."""
        return iter(list(self._handles.values()))

    def __len__(self) -> int:
        """Number of open networks."""
        return len(self._handles)
