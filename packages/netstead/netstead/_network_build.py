"""Source-agnostic ``records -> GMNS Network`` assembler (shared by importers).

Both the OpenStreetMap (:mod:`netstead.osm`) and Overture Maps
(:mod:`netstead.overture`) importers produce the same
``(node_records, link_records)`` contract and need the same final step:
load those records onto the compute engine, attach the per-table GMNS
schema for validation, and emit a one-row ``config`` table so the network
is self-describing (GMNS is unit-agnostic — units live in ``config``).

That step lived in :mod:`netstead.osm.build` until a second importer needed
it; it moved here (a core, dependency-light module) so neither importer
has to import the other. :func:`netstead.osm.build.network_from_records`
re-exports a thin wrapper for backwards compatibility.

Only the compute engine and the vendored GMNS spec are touched here, so
this module stays free of the optional ``[osm]`` / ``[overture]`` extras
and the import-linter boundary that forbids core modules from importing
optional-extra submodules is never at risk.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from corral.dataset import Table
from corral.engines import get_engine

from .network import Network
from .spec import DEFAULT_SPEC, load_gmns_spec

__all__ = ["DEFAULT_UNITS", "network_from_records"]

# Units the importers emit, declared on the generated GMNS `config` table.
# free_speed is mph; length is geodesic metres; geometry is WKT in EPSG:4326.
# Both importers share these — override via the ``units=`` argument if an
# importer ever emits a different unit system.
DEFAULT_UNITS: dict[str, str] = {
    "short_length": "meter",
    "long_length": "meter",
    "speed": "mph",
    "crs": "EPSG:4326",
    "geometry_field_format": "WKT",
}


def _resource_schema(spec: Any, name: str) -> Any:
    """Return the resolved schema for resource ``name`` from a loaded spec."""
    for resource in spec.resources:
        if resource.name == name:
            return resource.table_schema
    return None


def _records_to_expr(eng: Any, records: Sequence[dict[str, Any]], schema: Any) -> Any:
    """Build a table expression from records, preserving columns when empty.

    An empty record list yields a column-less frame, which the ibis/duckdb
    engine rejects ("must have at least one column"). When empty, build a
    zero-row frame whose columns come from the schema so the table stays
    valid and typed.

    Args:
        eng: The compute engine to materialize through.
        records: The row dicts to load (possibly empty).
        schema: The GMNS resource schema used to seed columns when empty.

    Returns:
        A lazy engine table expression over the records.
    """
    records = list(records)
    if records:
        return eng.from_records(records, schema=schema)
    columns: dict[str, list[Any]] = {field.name: [] for field in schema.fields} if schema and schema.fields else {}
    return eng.from_records(columns, schema=schema)


def _config_records(spec_version: str, dataset_name: str, units: dict[str, str]) -> list[dict[str, Any]]:
    """Build the single-row GMNS ``config`` table declaring units + CRS.

    Args:
        spec_version: GMNS spec version string (parsed to a number for the
            ``version_number`` column; non-numeric versions become ``None``).
        dataset_name: Value for the ``dataset_name`` column (e.g.
            ``"osm_export"`` / ``"overture_export"``).
        units: The unit declarations to spread across the config row.

    Returns:
        A one-element list holding the config row dict.
    """
    try:
        version_number = float(spec_version)
    except (TypeError, ValueError):
        version_number = None
    return [{"dataset_name": dataset_name, "id_type": "integer", "version_number": version_number, **units}]


def network_from_records(
    node_records: Sequence[dict[str, Any]],
    link_records: Sequence[dict[str, Any]],
    *,
    spec_version: str = DEFAULT_SPEC,
    engine: Any = None,
    dataset_name: str = "gmns_export",
    units: dict[str, str] | None = None,
) -> Network:
    """Assemble a :class:`~netstead.network.Network` from node/link records.

    The records are loaded onto the engine verbatim (extra provenance
    columns preserved); the per-table GMNS schema is attached to each table
    so :meth:`Network.validate` can run. A one-row ``config`` table declaring
    ``units`` (defaulting to :data:`DEFAULT_UNITS`) is added so the network
    is self-describing.

    Args:
        node_records: GMNS ``node`` rows.
        link_records: GMNS ``link`` rows.
        spec_version: GMNS spec version to validate against
            (default :data:`netstead.spec.DEFAULT_SPEC`).
        engine: Engine to materialize through. Defaults to the corral
            default (ibis/duckdb).
        dataset_name: ``config.dataset_name`` value identifying the source.
        units: Unit declarations for the ``config`` row. ``None`` uses
            :data:`DEFAULT_UNITS`.

    Returns:
        A :class:`~netstead.network.Network` with ``node``, ``link`` and
        ``config`` tables and ``spec_version`` stamped.
    """
    eng = engine or get_engine()
    gmns_spec = load_gmns_spec(spec_version)
    node_schema = _resource_schema(gmns_spec, "node")
    link_schema = _resource_schema(gmns_spec, "link")
    config_schema = _resource_schema(gmns_spec, "config")
    config_units = DEFAULT_UNITS if units is None else units

    # Pass the schema to from_records so GMNS columns get their declared
    # (nullable) types even when an optional column is entirely null — an
    # all-None column otherwise infers as `null` dtype and fails schema
    # validation. cast_schema leaves non-schema (provenance) columns untouched.
    tables = {
        "node": Table(
            name="node",
            expr=_records_to_expr(eng, node_records, node_schema),
            engine=eng,
            schema=node_schema,
        ),
        "link": Table(
            name="link",
            expr=_records_to_expr(eng, link_records, link_schema),
            engine=eng,
            schema=link_schema,
        ),
        "config": Table(
            name="config",
            expr=eng.from_records(_config_records(spec_version, dataset_name, config_units), schema=config_schema),
            engine=eng,
            schema=config_schema,
        ),
    }
    net = Network(spec=gmns_spec, tables=tables, engine=eng, source=None, spec_version=spec_version)
    # Importer-built links carry WKT geometry; canonicalise to WKB in memory
    # (geometry ADR) so record- and file-sourced networks behave identically.
    from netstead._geometry import convert_geometry_to_wkb

    convert_geometry_to_wkb(net)
    return net
