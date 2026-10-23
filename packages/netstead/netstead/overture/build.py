"""Orchestration: read Overture -> convert -> assemble a GMNS :class:`Network`.

:func:`build_network_from_overture` is the public entry point for the whole
pipeline (area resolution + Overture GeoParquet read + segment/connector
conversion + Network assembly). Its signature mirrors
:func:`netstead.osm.build.build_network_from_osm` as closely as the data model
allows, so a user can swap ``from_osm`` -> ``from_overture`` and keep the same
call in the common case. Source-specific keyword args (HTTP-fetch concerns for
OSM; object-store read concerns for Overture) differ, but the shared
``area`` / ``buffer_m`` / ``network_type`` / ``extra_tags`` / ``spec_version`` /
``engine`` surface is identical.

Records assembly is delegated to the shared, source-agnostic
:func:`netstead._network_build.network_from_records` (the same helper the OSM
source uses), stamping ``overture_export`` on the generated ``config`` table.

Attribution: Overture data is **ODbL**; derived products must credit
"© OpenStreetMap contributors, © Overture Maps Foundation"
(https://docs.overturemaps.org/attribution/). This module reads Overture data
and does not vendor it.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from netstead._network_build import network_from_records
from netstead.network import Network
from netstead.network_types import NetworkTypes
from netstead.spec import DEFAULT_SPEC

from . import convert, query

__all__ = ["build_network_from_overture"]


def build_network_from_overture(
    area: str | Sequence[float],
    *,
    buffer_m: float = 0.0,
    network_type: NetworkTypes = "roads",
    extra_tags: list[str] | None = None,
    spec_version: str = DEFAULT_SPEC,
    engine: Any = None,
    overture_release: str = query.OVERTURE_RELEASE,
    data_root: str | None = None,
) -> Network:
    """Build a GMNS network from Overture Maps for ``area``.

    Args:
        area: A place string, a ``(lat, lon)`` point, or a
            ``(west, south, east, north)`` bbox. (Place strings are geocoded via
            the OSM source's Nominatim helper and need the ``[osm]`` extra.)
        buffer_m: Buffer in metres applied when ``area`` is a point.
        network_type: One of ``roads``/``paths`` or a list (filters on
            Overture ``class``; see ``overture_network_filters.yaml``).
        extra_tags: Extra Overture segment property paths (dotted, e.g.
            ``"subclass"`` / ``"road_surface"``) to carry onto each link as
            columns. Same purpose as the OSM source's ``extra_tags``, with
            Overture property names instead of OSM tag keys.
        spec_version: GMNS spec version (default :data:`netstead.spec.DEFAULT_SPEC`).
        engine: Compute engine to materialize through (default: corral
            ibis/duckdb).
        overture_release: Pinned Overture release string (ignored when
            ``data_root`` is set). Bump to move releases reproducibly.
        data_root: Override base URI for the data (Azure mirror or a local
            snapshot directory holding ``segment.parquet`` / ``connector.parquet``).

    Returns:
        A populated :class:`~netstead.network.Network` (nodes + links with WKT
        geometry, carrying ``name`` / ``ref`` and Overture provenance columns,
        plus a ``config`` table declaring CRS + units).

    Raises:
        ValueError: If no segments match ``network_type`` in the area, or a
            referenced connector is missing.
    """
    segments, connectors = query.fetch_network_elements(
        area,
        buffer_m=buffer_m,
        network_type=network_type,
        overture_release=overture_release,
        data_root=data_root,
        engine=engine,
        extra_tags=extra_tags,
    )
    node_records, link_records = convert.build_node_link_tables(segments, connectors, extra_tags=extra_tags)
    if not link_records:
        raise ValueError(
            f"no Overture segments matched for network_type={network_type!r} in the requested area; "
            "try a larger area or a different network_type."
        )
    return network_from_records(
        node_records,
        link_records,
        spec_version=spec_version,
        engine=engine,
        dataset_name="overture_export",
    )
