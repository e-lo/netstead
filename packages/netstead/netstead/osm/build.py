"""Orchestration: fetch OSM -> convert -> assemble a GMNS :class:`~netstead.network.Network`.

:func:`build_network_from_osm` is the public entry point for the whole pipeline
(area resolution + Overpass fetch + node/link conversion + Network assembly).
:func:`network_from_records` is the records -> Network half on its own, exposed
so callers (and the benchmark harness) can build a Network from already-fetched
records without re-hitting the network.

The assembled Network carries the GMNS ``node`` and ``link`` tables on the
chosen engine (corral default ibis, or an explicit ``engine=``). Provenance
columns (``osm_way_id``, ``osm_node_ids``) ride along as extra columns; the
per-table GMNS schema is attached for validation.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from netstead._network_build import network_from_records as _network_from_records
from netstead.network import Network
from netstead.network_types import NetworkTypes
from netstead.spec import DEFAULT_SPEC

from . import convert, local, query

__all__ = ["build_network_from_osm", "build_network_from_osm_file", "network_from_records"]


def network_from_records(
    node_records: Sequence[dict[str, Any]],
    link_records: Sequence[dict[str, Any]],
    *,
    spec_version: str = DEFAULT_SPEC,
    engine: Any = None,
) -> Network:
    """Assemble a :class:`~netstead.network.Network` from OSM node/link records.

    Thin wrapper over :func:`netstead._network_build.network_from_records` that
    stamps the ``osm_export`` dataset name on the generated ``config`` table.
    The records are loaded onto the engine verbatim (extra provenance columns
    ``osm_way_id`` / ``osm_node_ids`` preserved); the per-table GMNS schema is
    attached so :meth:`Network.validate` can run.

    Args:
        node_records: GMNS ``node`` rows (e.g. from
            :func:`netstead.osm.convert.build_node_link_tables`).
        link_records: GMNS ``link`` rows.
        spec_version: GMNS spec version to validate against
            (default :data:`netstead.spec.DEFAULT_SPEC`).
        engine: Engine to materialize through. Defaults to the corral
            default (ibis).

    Returns:
        A :class:`~netstead.network.Network` with ``node`` and ``link`` tables
        and ``spec_version`` stamped.
    """
    return _network_from_records(
        node_records,
        link_records,
        spec_version=spec_version,
        engine=engine,
        dataset_name="osm_export",
    )


def build_network_from_osm(
    area: str | Sequence[float],
    *,
    buffer_m: float = 0.0,
    network_type: NetworkTypes = "drive",
    extra_tags: list[str] | None = None,
    spec_version: str = DEFAULT_SPEC,
    engine: Any = None,
    endpoint: str = query.OVERPASS_URL,
    session: Any = None,
    user_agent: str = query.USER_AGENT,
    timeout: int = 180,
    retries: int = 3,
    sleep: Callable[[float], None] = time.sleep,
) -> Network:
    """Build a GMNS network from OpenStreetMap for ``area``.

    Args:
        area: A place string, a ``(lat, lon)`` point, or a
            ``(west, south, east, north)`` bbox.
        buffer_m: Buffer in metres applied when ``area`` is a point.
        network_type: One of ``drive``/``walk``/``bike``/``all``.
        extra_tags: OSM tag keys to carry onto each link as extra columns.
        spec_version: GMNS spec version (default :data:`netstead.spec.DEFAULT_SPEC`).
        engine: Engine to materialize through (default: corral ibis).
        endpoint: Overpass API endpoint URL.
        session: HTTP session (injectable for tests). Defaults to ``requests``.
        user_agent: ``User-Agent`` header value.
        timeout: Per-request timeout, seconds.
        retries: Retries on transient status codes.
        sleep: Sleep function used between retries.

    Returns:
        A populated :class:`~netstead.network.Network`.
    """
    nodes, ways = query.fetch_network_elements(
        area,
        buffer_m=buffer_m,
        network_type=network_type,
        endpoint=endpoint,
        session=session,
        user_agent=user_agent,
        timeout=timeout,
        retries=retries,
        sleep=sleep,
    )
    node_records, link_records = convert.build_node_link_tables(nodes, ways, extra_tags=extra_tags)
    if not link_records:
        raise ValueError(
            f"no OSM ways matched for network_type={network_type!r} in the requested area; "
            "try a larger area or a different --network-type."
        )
    return network_from_records(node_records, link_records, spec_version=spec_version, engine=engine)


def build_network_from_osm_file(
    path: str | Path,
    *,
    network_type: NetworkTypes = "drive",
    extra_tags: list[str] | None = None,
    spec_version: str = DEFAULT_SPEC,
    engine: Any = None,
) -> Network:
    """Build a GMNS network from a local ``.osm`` XML file or Overpass JSON export (no network access).

    Args:
        path: The local OSM file (see :func:`netstead.osm.local.read_osm_file` for formats).
        network_type: One of ``drive``/``walk``/``bike``/``all``; applied locally to the ``highway`` tag.
        extra_tags: OSM tag keys to carry onto each link as extra columns.
        spec_version: GMNS spec version (default :data:`netstead.spec.DEFAULT_SPEC`).
        engine: Engine to materialize through (default: corral ibis).

    Returns:
        A populated :class:`~netstead.network.Network`.

    Raises:
        ValueError: Unsupported/malformed file, or no ways match ``network_type``.
    """
    nodes, ways = local.read_osm_file(path, network_type=network_type)
    node_records, link_records = convert.build_node_link_tables(nodes, ways, extra_tags=extra_tags)
    if not link_records:
        raise ValueError(f"no OSM ways in {Path(path).name} matched network_type={network_type!r}")
    return network_from_records(node_records, link_records, spec_version=spec_version, engine=engine)
