"""Map-styling helpers shared by ``netstead viz`` and the workbench.

Pure functions over pandas link frames: which columns can drive color-by, the
per-link values for one property, JSON-safe scalars, and the keyless basemap
style. No FastAPI imports, so the workbench and tests use them directly.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

__all__ = ["basemap_style", "json_scalar", "property_payload", "styleable_columns"]

#: Columns never offered as a color-by property (geometry/opaque or identity), per table.
_SKIP_STYLE_COLS = {
    "link": {"geometry", "osm_node_ids", "osm_way_id", "link_id", "from_node_id", "to_node_id"},
    "node": {"geometry", "node_id", "x_coord", "y_coord", "osm_node_id", "zone_id"},
}
_MAX_CATEGORIES = 25

_ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas"
#: Free, no-key vector Positron (OpenMapTiles/OSM data).
_POSITRON_URL = "https://tiles.openfreemap.org/styles/positron"


def json_scalar(v: Any) -> Any:
    """Return ``v`` as a JSON-safe Python scalar (NaN/NA → ``None``, numpy → builtin)."""
    try:
        if v is None or pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return getattr(v, "item", lambda: v)()


def styleable_columns(frame: pd.DataFrame, table: str = "link") -> list[dict]:
    """List columns usable for color-by, classified continuous vs categorical.

    ``table`` names which identity columns to leave out: ``"link"`` (the default) or ``"node"``.
    """
    out = []
    skip = _SKIP_STYLE_COLS.get(table, set())
    for c in frame.columns:
        if c in skip:
            continue
        s = frame[c]
        if pd.api.types.is_numeric_dtype(s):
            out.append({"name": c, "kind": "continuous"})
        elif s.nunique(dropna=True) <= _MAX_CATEGORIES:  # skip high-cardinality (e.g. name)
            out.append({"name": c, "kind": "categorical"})
    return out


def property_payload(links: pd.DataFrame, name: str) -> dict | None:
    """Index-aligned values for one color-by property, or ``None`` if the column is absent."""
    if name not in links.columns:
        return None
    s = links[name]
    values = [json_scalar(v) for v in s]
    if pd.api.types.is_numeric_dtype(s):
        nn = [v for v in values if v is not None]
        return {
            "name": name,
            "kind": "continuous",
            "values": values,
            "min": min(nn) if nn else 0,
            "max": max(nn) if nn else 1,
        }
    cats = sorted({str(v) for v in values if v is not None})
    return {
        "name": name,
        "kind": "categorical",
        "values": [None if v is None else str(v) for v in values],
        "categories": cats,
    }


def basemap_style(basemap: str = "positron") -> str | dict:
    """Return a keyless MapLibre style: ``"positron"`` (style URL, default) or ``"esri"`` (raster, ≤z16)."""
    if basemap == "esri":
        return {
            "version": 8,
            "sources": {
                "basemap": {
                    "type": "raster",
                    "tileSize": 256,
                    "maxzoom": 16,
                    "attribution": "Esri, © OpenStreetMap contributors",
                    "tiles": [f"{_ESRI}/World_Light_Gray_Base/MapServer/tile/{{z}}/{{y}}/{{x}}"],
                },
                "labels": {
                    "type": "raster",
                    "tileSize": 256,
                    "maxzoom": 16,
                    "tiles": [f"{_ESRI}/World_Light_Gray_Reference/MapServer/tile/{{z}}/{{y}}/{{x}}"],
                },
            },
            "layers": [
                {"id": "basemap", "type": "raster", "source": "basemap"},
                {"id": "labels", "type": "raster", "source": "labels"},
            ],
        }
    return _POSITRON_URL
