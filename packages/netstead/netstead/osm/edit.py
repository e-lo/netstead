"""Deep-link URL builders for the iD and JOSM OSM editors.

These helpers turn an OSM way / node / relation id (or a validation
:class:`~corral.reports.Issue` that points at one) into a URL the
user can click to jump straight to the offending element in their
editor of choice. Pure string formatters — no network access, no
dependencies beyond the standard library, no ``[osm]`` extra required.

The iD URL format (``openstreetmap.org/edit?editor=id&way=<id>``) is
undocumented but has been stable since 2015 — see
https://wiki.openstreetmap.org/wiki/ID. The format tests in
``test_osm_edit_url.py`` pin it so an upstream change is caught here
rather than in production reports.

The JOSM URL format uses the JOSM-RemoteControl ``load_object`` action
on localhost — JOSM must be running with RemoteControl enabled for the
link to work. See
https://josm.openstreetmap.de/wiki/Help/Plugin/RemoteControl.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:  # pragma: no cover - typing only
    from corral.reports import Issue

    from netstead.network import Network

__all__ = ["issue_osm_edit_url", "osm_edit_url"]


_KIND_TO_ID = {"way": "way", "node": "node", "relation": "relation"}
_KIND_TO_JOSM_PREFIX = {"way": "w", "node": "n", "relation": "r"}
_VALID_EDITORS = {"id", "josm"}


def _coerce_osm_id(osm_id: object) -> int | None:
    """Coerce a raw OSM id to ``int``, or ``None`` when not a real id.

    Handles the common CSV/pandas shapes: ``int``, numeric ``str``,
    ``None``, empty ``str``, and ``NaN``. Anything else (a non-numeric
    string, a list, …) returns ``None`` rather than raising, so callers
    can chain without branching.
    """
    if osm_id is None:
        return None
    if isinstance(osm_id, float) and math.isnan(osm_id):
        return None
    if isinstance(osm_id, str):
        stripped = osm_id.strip()
        if not stripped:
            return None
        try:
            return int(stripped)
        except ValueError:
            return None
    try:
        return int(osm_id)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def osm_edit_url(
    osm_id: int | str | float | None,
    *,
    kind: Literal["way", "node", "relation"] = "way",
    editor: Literal["id", "josm"] = "id",
) -> str | None:
    """Build a deep link to ``osm_id`` in the chosen OSM editor.

    Args:
        osm_id: The OSM element id. ``None``, empty string, ``NaN``, or
            a non-numeric value returns ``None`` (so callers can chain
            without branching on each row).
        kind: Which OSM element type the id refers to — ``"way"``
            (default — what links almost always come from), ``"node"``,
            or ``"relation"``.
        editor: Which editor to deep-link into. ``"id"`` (the in-browser
            editor at openstreetmap.org/edit) or ``"josm"`` (the
            desktop JOSM editor via its localhost RemoteControl plugin).

    Returns:
        The URL string, or ``None`` when ``osm_id`` is not a usable id.

    Raises:
        ValueError: If ``kind`` or ``editor`` is not one of the
            supported values. These are programmer bugs, not user-input
            errors — fail loudly rather than silently fall back.

    Examples:
        >>> osm_edit_url(12345)
        'https://www.openstreetmap.org/edit?editor=id&way=12345'
        >>> osm_edit_url(99, kind="node", editor="josm")
        'http://127.0.0.1:8111/load_object?objects=n99&new_layer=false'
        >>> osm_edit_url(None) is None
        True
        >>> osm_edit_url("not-an-id") is None
        True
    """
    if kind not in _KIND_TO_ID:
        raise ValueError(f"unknown kind {kind!r}; expected one of {sorted(_KIND_TO_ID)}")
    if editor not in _VALID_EDITORS:
        raise ValueError(f"unknown editor {editor!r}; expected one of {sorted(_VALID_EDITORS)}")

    coerced = _coerce_osm_id(osm_id)
    if coerced is None:
        return None

    if editor == "id":
        param = _KIND_TO_ID[kind]
        return f"https://www.openstreetmap.org/edit?editor=id&{param}={coerced}"
    # editor == "josm"
    prefix = _KIND_TO_JOSM_PREFIX[kind]
    return f"http://127.0.0.1:8111/load_object?objects={prefix}{coerced}&new_layer=false"


def _table_has_osm_provenance(network: Network, table_name: str, column: str) -> bool:
    """``True`` when ``network`` has ``table_name`` and that table carries ``column``."""
    table = network.tables.get(table_name)
    if table is None:
        return False
    try:
        return column in table.columns()
    except Exception:
        return False


def issue_osm_edit_url(
    issue: Issue,
    network: Network,
    *,
    editor: Literal["id", "josm"] = "id",
) -> str | None:
    """Resolve a single finding to its "Edit in OSM" deep link, or ``None``.

    Resolution order, first match wins:

    1. ``issue.extra["osm_way_id"]`` / ``["osm_node_id"]`` /
       ``["osm_relation_id"]`` — fast-path for callers (quality rules,
       custom checks) that already know the OSM provenance and stash
       it on the issue.
    2. ``issue.table == "link"`` + ``issue.row`` → look up the row in
       ``network.links`` and read its ``osm_way_id`` column.
    3. ``issue.table == "node"`` + ``issue.row`` → look up the row in
       ``network.nodes`` and read its ``osm_node_id`` column (only set
       on OSM-sourced networks).

    Returns ``None`` for any finding whose network has no OSM
    provenance columns, and for any finding whose row lookup yields a
    missing / NaN OSM id.

    Args:
        issue: The validation finding.
        network: The network the finding was raised against. Used for
            row lookups and OSM-provenance detection.
        editor: Which editor to link to (see :func:`osm_edit_url`).

    Returns:
        The URL string, or ``None`` when no deep link can be built.

    Examples:
        Cross-cutting finding with no OSM context::

            >>> from corral.reports import Category, Issue, Severity
            >>> issue = Issue(severity=Severity.ERROR,
            ...               category=Category.STRUCTURAL,
            ...               code="structural.missing_table",
            ...               message="link table missing")
            >>> # No network arg here would defeat the doctest — see
            >>> # test_osm_edit_url.py for network-using cases.
    """
    # 1. Fast-path: caller stashed the OSM id on the issue.
    extra = issue.extra or {}
    for kind, key in (("way", "osm_way_id"), ("node", "osm_node_id"), ("relation", "osm_relation_id")):
        if key in extra:
            url = osm_edit_url(extra[key], kind=kind, editor=editor)  # type: ignore[arg-type]
            if url is not None:
                return url

    # 2/3. Row-based lookup against the network's provenance columns.
    if issue.row is None:
        return None

    if issue.table == "link" and _table_has_osm_provenance(network, "link", "osm_way_id"):
        return _lookup_row_id(network, "link", issue.row, "osm_way_id", kind="way", editor=editor)
    if issue.table == "node" and _table_has_osm_provenance(network, "node", "osm_node_id"):
        return _lookup_row_id(network, "node", issue.row, "osm_node_id", kind="node", editor=editor)
    return None


def _lookup_row_id(
    network: Network,
    table_name: str,
    row: int,
    column: str,
    *,
    kind: Literal["way", "node", "relation"],
    editor: Literal["id", "josm"],
) -> str | None:
    """Read ``column`` from row ``row`` of ``table_name`` and format the edit URL.

    Materializes the table via ``to_pandas()`` since the lookup is
    row-positional and engines disagree on row-positional access. For
    the renderer use case this happens once per render, not per issue
    — the caller is expected to cache.
    """
    table = network.tables.get(table_name)
    if table is None:
        return None
    df = table.to_pandas()
    if row < 0 or row >= len(df):
        return None
    raw = df[column].iloc[row]
    return osm_edit_url(raw, kind=kind, editor=editor)
