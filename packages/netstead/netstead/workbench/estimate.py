"""Size a build before running it: a cheap pre-query count, then a calibrated linear cost model.

``seconds = latency_s + links * s_per_link`` with ``links = n_elements * links_per_element``; the
coefficients live in the maintained data file ``data/build_cost.toml`` (read with ``tomllib``), which
says plainly which numbers are measured and which are guesses. The pre-queries are:

* :func:`count_osm`: the build's own Overpass query with ``out count;`` (ways only, no geometry);
* :func:`count_overture`: DuckDB ``COUNT(*)`` over the same bbox + class predicate as the read.

An estimate is advice for the approval gate, not a guarantee.
"""

from __future__ import annotations

import tomllib
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from functools import cache
from importlib import resources
from typing import Any, Literal

from netstead.network_types import NetworkTypes

from .area import BboxArea, PlaceArea, PointArea
from .extras import optional_module

__all__ = [
    "Estimate",
    "SourceKind",
    "count_osm",
    "count_overture",
    "estimate_build",
    "fit_s_per_link",
    "load_coefficients",
    "needs_approval",
]

SourceKind = Literal["osm", "overture", "osm_file", "overture_file"]
#: Overpass timeout for the count pre-query (seconds); a count that slow means "unavailable".
COUNT_TIMEOUT_S = 25


@dataclass(frozen=True)
class Estimate:
    """A build-size estimate. ``seconds is None`` means the pre-query failed (``basis`` says why)."""

    seconds: float | None
    out_bytes: int | None
    n_elements: int | None
    basis: str

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe copy (round-trips through ``Estimate(**d)``)."""
        return asdict(self)


@cache
def load_coefficients() -> dict[str, Any]:
    """The parsed ``data/build_cost.toml`` (cached; edit the file, not this module, to re-tune)."""
    text = resources.files("netstead.workbench").joinpath("data", "build_cost.toml").read_text(encoding="utf-8")
    return tomllib.loads(text)


def estimate_build(
    source: SourceKind,
    n_elements: int,
    *,
    output_format: str = "parquet",
    coefficients: dict[str, Any] | None = None,
) -> Estimate:
    """Turn a pre-query count into time and output-size estimates.

    Args:
        source: Which build path (each has its own coefficients and ``n_elements`` unit).
        n_elements: The pre-query result (ways, segments, or file bytes; see the data file).
        output_format: ``parquet``/``csv``/``duckdb``/``zip``, for the output-size estimate.
        coefficients: Override the data file (tests, calibration).

    Returns:
        An :class:`Estimate` whose ``basis`` names the count and the model.

    Examples:
        >>> est = estimate_build("osm", 10_000)
        >>> round(est.seconds, 1), est.n_elements
        (32.7, 10000)
    """
    coeffs = coefficients or load_coefficients()
    model = coeffs["sources"][source]
    links = n_elements * model["links_per_element"]
    seconds = model["latency_s"] + links * model["s_per_link"]
    out = coeffs["output"]
    out_bytes = int(out["fixed_bytes"][output_format] + links * out["bytes_per_link"][output_format])
    unit = "bytes of file" if source == "osm_file" else ("ways" if source == "osm" else "segments")
    basis = f"{source}: {n_elements:,} {unit} -> ~{links:,.0f} links (model from data/build_cost.toml)"
    return Estimate(seconds=seconds, out_bytes=out_bytes, n_elements=n_elements, basis=basis)


def needs_approval(estimate: Estimate, threshold_s: float) -> bool:
    """Whether running needs an explicit approval: over the threshold, or no estimate at all."""
    return estimate.seconds is None or estimate.seconds > threshold_s


def count_osm(
    area: BboxArea | PointArea | PlaceArea,
    *,
    network_type: NetworkTypes,
    endpoint: str,
    http: Any,
    user_agent: str,
    retries: int = 1,
) -> int:
    """Number of OSM ways the build's Overpass query would return (Overpass ``out count;``).

    Raises whatever the HTTP layer raises; :func:`netstead.workbench.build.estimate_for` turns any
    failure into an "unavailable" estimate.
    """
    osm_query = optional_module("netstead.osm.query", "osm")
    q = osm_query.build_overpass_query(
        bbox=area.to_bbox(), polygon=area.to_polygon(), network_type=network_type, timeout=COUNT_TIMEOUT_S, out="count"
    )
    elements = osm_query.fetch_osm(
        q, endpoint=endpoint, session=http, user_agent=user_agent, timeout=COUNT_TIMEOUT_S + 5, retries=retries
    )
    return int(elements[0]["tags"]["ways"])


def count_overture(
    bbox: tuple[float, float, float, float],
    *,
    network_type: NetworkTypes,
    overture_release: str,
    data_root: str | None,
    engine: Any = None,
) -> int:
    """Number of Overture road segments the build would read (``COUNT(*)``, same predicate as the read)."""
    overture_query = optional_module("netstead.overture.query", "overture")
    return overture_query.count_segments(
        bbox, network_type=network_type, overture_release=overture_release, data_root=data_root, engine=engine
    )


def fit_s_per_link(samples: Sequence[tuple[float, float]], *, latency_s: float) -> float:
    """Least-squares ``s_per_link`` through a fixed ``latency_s`` from ``(links, seconds)`` samples.

    Examples:
        >>> fit_s_per_link([(100_000, 15.0), (200_000, 25.0)], latency_s=5.0)
        0.0001
    """
    num = sum(links * (seconds - latency_s) for links, seconds in samples)
    den = sum(links * links for links, _ in samples)
    if den == 0:
        raise ValueError("need at least one sample with links > 0")
    return num / den
