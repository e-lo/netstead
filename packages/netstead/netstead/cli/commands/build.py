"""``netstead build`` — create a GMNS network from OpenStreetMap or Overture Maps.

Resolves an area (place name, ``--bbox``, or ``--point`` + ``--buffer``), reads
the road network from the chosen ``--source`` (``osm`` via Overpass, or
``overture`` via GeoParquet), converts it to GMNS ``node`` + ``link`` tables, and
writes the network to ``DEST`` (format inferred from the extension, or
``--format``).

The heavy lifting lives in :mod:`netstead.osm` / :mod:`netstead.overture`, imported
lazily through :func:`netstead.cli._extras.require_extra` so the command degrades
to a clean "install the extra" message when the extra is absent (and so the
import-linter boundary stays static-clean).
"""

from __future__ import annotations

from pathlib import Path

import typer
from corral.cli.render import render_dict

from .._extras import require_extra
from .._helpers import resolve_engine

__all__ = ["register"]


def _parse_area(place: str | None, bbox: str | None, point: str | None, buffer_m: float) -> str | tuple[float, ...]:
    """Resolve the mutually-exclusive area options into a build ``area`` argument.

    Returns the area value (place string, bbox 4-tuple, or point 2-tuple).
    Raises :class:`typer.BadParameter` on zero/multiple options or malformed
    numeric input.
    """
    provided = [name for name, value in (("place", place), ("bbox", bbox), ("point", point)) if value]
    if len(provided) != 1:
        raise typer.BadParameter("provide exactly one of --place, --bbox, or --point")

    # --buffer is only meaningful with --point; reject the silent combination
    # (a user passing --bbox --buffer 500 used to get the buffer ignored).
    if buffer_m > 0 and not point:
        raise typer.BadParameter("--buffer is only valid with --point; it has no effect on --bbox or --place")

    if place:
        return place
    if bbox:
        parts = [p.strip() for p in bbox.split(",")]
        if len(parts) != 4:
            raise typer.BadParameter("--bbox must be 'west,south,east,north'")
        try:
            return tuple(float(p) for p in parts)
        except ValueError as exc:
            raise typer.BadParameter("--bbox values must be numbers") from exc
    assert point is not None  # exactly one of place / bbox / point, checked above
    parts = [p.strip() for p in point.split(",")]
    if len(parts) != 2:
        raise typer.BadParameter("--point must be 'lat,lon'")
    if buffer_m <= 0:
        raise typer.BadParameter("--point requires a positive --buffer (metres)")
    try:
        return (float(parts[0]), float(parts[1]))
    except ValueError as exc:
        raise typer.BadParameter("--point values must be numbers") from exc


def register(app: typer.Typer) -> None:
    """Register the ``build`` command on ``app``."""

    @app.command(name="build")
    def build_cmd(
        dest: Path = typer.Argument(..., help="Output path for the GMNS network (dir or file)."),
        place: str = typer.Option(None, "--place", help="Place name to geocode (city/county/etc.)."),
        bbox: str = typer.Option(None, "--bbox", help="Bounding box 'west,south,east,north' (EPSG:4326)."),
        point: str = typer.Option(None, "--point", help="Center 'lat,lon'; use with --buffer."),
        buffer_m: float = typer.Option(0.0, "--buffer", help="Buffer in metres around --point."),
        network_type: str = typer.Option("drive", "--network-type", help="drive / walk / bike / all."),
        extra_tags: str = typer.Option(
            None, "--extra-tags", help="Comma-separated extra source properties/tags to carry."
        ),
        source: str = typer.Option("osm", "--source", help="Data source: 'osm' or 'overture'."),
        overture_release: str = typer.Option(
            None, "--overture-release", help="Overture release string (overture source only)."
        ),
        data_root: str = typer.Option(
            None, "--data-root", help="Override Overture data base URI / local snapshot dir (overture source only)."
        ),
        engine: str = typer.Option(
            None, "--engine", help="Compute engine (DuckDB is the only one; kept for compatibility)."
        ),
        spec_version: str = typer.Option(None, "--spec-version", help="GMNS spec version (default: latest)."),
        out_format: str = typer.Option(None, "--format", help="Output format: csv / parquet / duckdb / zip."),
        json_out: bool = typer.Option(False, "--json", help="Emit a JSON summary on stdout."),
    ) -> None:
        """Build a GMNS network from OpenStreetMap or Overture and write it to ``DEST``."""
        if source not in {"osm", "overture"}:
            raise typer.BadParameter("--source must be 'osm' or 'overture'")
        if source == "osm" and (overture_release or data_root):
            raise typer.BadParameter("--overture-release / --data-root are only valid with --source overture")

        area = _parse_area(place, bbox, point, buffer_m)
        tags = [t.strip() for t in extra_tags.split(",")] if extra_tags else None
        build_kwargs = {"spec_version": spec_version} if spec_version else {}

        try:
            if source == "overture":
                overture = require_extra("netstead.overture", "overture")
                overture_kwargs = {}
                if overture_release:
                    overture_kwargs["overture_release"] = overture_release
                if data_root:
                    overture_kwargs["data_root"] = data_root
                net = overture.build_network_from_overture(
                    area,
                    buffer_m=buffer_m,
                    network_type=network_type,
                    extra_tags=tags,
                    engine=resolve_engine(engine),
                    **build_kwargs,
                    **overture_kwargs,
                )
            else:
                osm = require_extra("netstead.osm", "osm")
                net = osm.build_network_from_osm(
                    area,
                    buffer_m=buffer_m,
                    network_type=network_type,
                    extra_tags=tags,
                    engine=resolve_engine(engine),
                    **build_kwargs,
                )
        except (ValueError, LookupError, OSError) as exc:
            # ValueError/LookupError: bad area, unknown network_type, empty result,
            # geocode miss. OSError covers Overpass/Nominatim/object-store I/O failures
            # (requests.RequestException subclasses OSError).
            typer.secho(str(exc), fg="red", err=True)
            raise typer.Exit(code=1) from None

        overwrite = dest.exists()
        net.write(dest, format=out_format, overwrite=overwrite)

        render_dict(
            {
                "dest": str(dest),
                "source": source,
                "network_type": network_type,
                "spec_version": net.spec_version,
                "nodes": int(net.nodes.count()),
                "links": int(net.links.count()),
            },
            json_out=json_out,
            title=f"build: {dest}",
        )
