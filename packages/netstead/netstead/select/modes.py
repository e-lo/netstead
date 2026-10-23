"""Map selection modes (ProjectCard vocabulary) to GMNS use names.

The parser emits ``drive``/``bike``/``walk``/``transit``; a GMNS ``allowed_uses`` column lists
use names such as ``auto``, ``truck`` or ``bus``. :func:`mode_uses` bridges the two.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

__all__ = ["MODE_USES", "mode_uses", "split_uses"]

#: ProjectCard mode -> GMNS use names it covers. ``any`` is handled in :func:`mode_uses` (no filter).
MODE_USES: dict[str, frozenset[str]] = {
    "drive": frozenset({"auto", "car", "sov", "hov", "hov2", "hov3", "truck", "taxi", "motorcycle", "drive"}),
    "bike": frozenset({"bike", "bicycle", "cycle"}),
    "walk": frozenset({"walk", "ped", "pedestrian", "foot"}),
    "transit": frozenset({"bus", "transit", "rail", "lrt", "streetcar", "brt", "tram"}),
}


def split_uses(value: object) -> set[str]:
    """Lower-cased tokens of a comma/space separated uses cell (``None``/NaN give an empty set)."""
    if value is None or value != value:  # NaN is the only value unequal to itself
        return set()
    return {t for t in str(value).replace(",", " ").lower().split() if t}


def mode_uses(modes: Iterable[object], use_groups: Mapping[str, set[str]] | None = None) -> set[str] | None:
    """GMNS use tokens that satisfy any of ``modes``; ``None`` means no filter (a mode of ``any``).

    A mode that names a ``use_groups`` entry also brings that group's member uses; an unknown
    mode word matches literally.
    """
    groups = {k.lower(): v for k, v in (use_groups or {}).items()}
    want: set[str] = set()
    for mode in modes:
        key = str(mode).strip().lower()
        if key == "any":
            return None
        want |= MODE_USES.get(key, {key})
        want |= groups.get(key, set())
    return want
