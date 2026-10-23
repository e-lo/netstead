"""Build areas: the one shape every Area-step tab (draw, coordinates, place) produces.

``Area`` is a discriminated union on ``kind``. Every variant reduces to one
``(west, south, east, north)`` bbox (EPSG:4326) for preview, estimate, and the
Overture read; a place may also carry its (simplified) boundary polygon, which
the OSM build passes to Overpass. Storing the resolved bbox/polygon on the
action (rather than the place name alone) keeps a replay from re-geocoding.
"""

from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = ["Area", "BboxArea", "PlaceArea", "PointArea"]

BBox = tuple[float, float, float, float]
#: Metres per degree of latitude (the same spherical approximation the OSM/Overture builders use).
_M_PER_DEG_LAT = 111320.0


def _check_bbox(bbox: BBox) -> BBox:
    west, south, east, north = bbox
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError(f"bbox must be (west, south, east, north) with west<east and south<north, got {bbox}")
    return bbox


class _Area(BaseModel):
    model_config = ConfigDict(extra="forbid")

    def to_bbox(self) -> BBox:
        """The area's ``(west, south, east, north)`` bbox."""
        raise NotImplementedError

    def to_polygon(self) -> list[tuple[float, float]] | None:
        """Boundary as ``(lat, lon)`` vertices, or ``None`` when the bbox is the whole area."""
        return None


class BboxArea(_Area):
    """A rectangle, drawn on the map or typed as ``W,S,E,N``."""

    kind: Literal["bbox"] = "bbox"
    bbox: BBox

    @model_validator(mode="after")
    def _valid(self) -> BboxArea:
        _check_bbox(self.bbox)
        return self

    def to_bbox(self) -> BBox:
        """The rectangle itself."""
        return self.bbox


class PointArea(_Area):
    """A center point plus a buffer (metres) on every side."""

    kind: Literal["point"] = "point"
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    buffer_m: float = Field(gt=0)

    @model_validator(mode="after")
    def _valid(self) -> PointArea:
        # Server-side, like the other areas: a buffer that crosses the antimeridian or a pole (or is
        # infinite) gives a bbox the builders cannot use, whatever the browser checked.
        try:
            _check_bbox(self.to_bbox())
        except ValueError as exc:
            raise ValueError(f"point + buffer_m={self.buffer_m:g} does not fit in one bbox: {exc}") from None
        return self

    def to_bbox(self) -> BBox:
        """The square ``buffer_m`` out from the point (``netstead.osm.query.point_buffer_bbox`` maths)."""
        dlat = self.buffer_m / _M_PER_DEG_LAT
        cos_lat = math.cos(math.radians(self.lat))
        dlon = self.buffer_m / (_M_PER_DEG_LAT * cos_lat) if cos_lat else dlat
        return (self.lon - dlon, self.lat - dlat, self.lon + dlon, self.lat + dlat)


class PlaceArea(_Area):
    """A geocoded place chosen from the candidate list: its name, bbox, and optional outline."""

    kind: Literal["place"] = "place"
    name: str = Field(min_length=1)
    bbox: BBox
    polygon: list[tuple[float, float]] | None = None

    @model_validator(mode="after")
    def _valid(self) -> PlaceArea:
        _check_bbox(self.bbox)
        if self.polygon is not None and len(self.polygon) < 3:
            raise ValueError("a place polygon needs at least 3 vertices")
        return self

    def to_bbox(self) -> BBox:
        """The place's bounding box."""
        return self.bbox

    def to_polygon(self) -> list[tuple[float, float]] | None:
        """The place outline as ``(lat, lon)`` vertices (what Overpass ``poly:`` expects), if known."""
        return self.polygon


Area = Annotated[BboxArea | PointArea | PlaceArea, Field(discriminator="kind")]
