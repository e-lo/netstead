"""Resolve a SelectionIntent to GMNS link/node ids against a network.

ProjectCard-select-links parity. Pipeline (engine-agnostic; pandas frames):
  1. base selection   — ``link_ids`` | ``select_all`` | facility (name/ref, any class)
  2. direction filter — facility bearing within +-75 deg of the cardinal
  3. modes/conditions — AND-filters on link attributes
  4. segment or whole — if from/to anchors: shortest path along the facility
     (freeway gore/merge, or at-grade intersections); else the whole filtered set.
     Names match exactly, then by spelling, direction or a close misspelling
     (:class:`~netstead.select.streets.StreetIndex`). A partly-freeway facility
     (a trunk road with signals) that has no interchange path tries its at-grade
     intersections, and a street whose own name breaks off (a nameless
     roundabout, a median crossing) is bridged by short links.
"""

from __future__ import annotations

import collections

import numpy as np
import pandas as pd

from . import anchors as _anchors
from ._support import (
    bearing_deg,
    dijkstra_links,
    link_length,
    matches_direction,
    route_alias,
    to_py,
)
from .intent import SelectionIntent
from .modes import mode_uses, split_uses
from .result import SelectionResult
from .streets import StreetIndex

__all__ = ["resolve", "resolve_frames"]

_FREEWAY_TYPES = ("motorway", "trunk")
#: Bridging links cost this many times their length, so a path keeps to the street when it can.
_BRIDGE_PENALTY = 3.0


def _node_coords(nodes, needed):
    """``(x, y)`` dicts for the ``needed`` node ids only (a network has far more nodes than a selection)."""
    near = nodes[nodes["node_id"].isin(needed)]
    return (
        dict(zip(near["node_id"], near["x_coord"], strict=False)),
        dict(zip(near["node_id"], near["y_coord"], strict=False)),
    )


def _facility_links(links, facility, index: StreetIndex):
    """Links whose ref or name matches the facility (any road class), plus notes saying which column matched.

    ``ref``/``name`` may each be a single value or a list (OR-matched). A facility value matches the **union**
    of the links whose ``ref`` shares a route-number token with it (``"US 2"`` ~ ``"US-2"`` ~ ``"US Highway 2"``)
    and the links whose ``name`` it names (exact, then spelling, then directional): a route is often named on
    some of its links and only numbered on others (OSM ways with ``ref=US 2`` and no ``name``). A name match
    that is not exact adds a note naming the streets it stood for. A value given as a ``ref`` that matches no
    ``ref`` is tried as a street name and the other way round (small models put "Capital Blvd" in ``ref`` or
    "US 1" in ``name``); that fallback adds a note. A looser (spelling or fuzzy) street match is never added to a
    route number that matched. When both columns match, a note gives the count per column. ``ref`` and ``name``
    are optional GMNS fields; a missing column matches nothing.
    """
    notes = []
    hit = []
    for field, values in (("ref", facility.refs()), ("name", facility.names())):
        for value in values:
            by_ref = index.ref_rows(value)
            by_name, match = index.name_rows(value)
            if not by_name.size and (short := route_alias(str(value))) != str(value):
                by_name, match = index.name_rows(short)  # "US Highway 2" is named "US 2" on some links
            own = by_ref if field == "ref" else by_name
            if field == "ref" and by_ref.size and match.how != "exact":
                by_name = by_name[:0]  # a loose street match never widens a route number that matched
            if not own.size and (by_ref.size or by_name.size):
                notes.append(f"treated {field} {value!r} as {'a street name' if field == 'ref' else 'a route number'}")
            if by_ref.size and by_name.size:
                notes.append(f"matched {value!r} on ref ({by_ref.size} links) and on name ({by_name.size} links)")
            if by_name.size:
                notes.extend(_loose_match_note(value, match))
            hit.append(np.union1d(by_ref, by_name))
    rows = np.unique(np.concatenate(hit)) if hit else np.array([], dtype=np.int64)
    return links.iloc[rows], notes


def _loose_match_note(value, match) -> list[str]:
    """A diagnostic naming the streets ``value`` stood for, when it didn't match one exactly."""
    if match.how in (None, "exact"):
        return []
    misspelled = " (a close spelling)" if match.how == "fuzzy" else ""
    return [f"matched {value!r} to {', '.join(repr(n) for n in match.names)}{misspelled}"]


def _filter_direction(mainline, direction, nx, ny):
    if direction is None:
        return mainline
    keep = [
        row.link_id
        for row in mainline.itertuples()
        if matches_direction(bearing_deg(nx, ny, row.from_node_id, row.to_node_id), direction)
    ]
    return mainline[mainline["link_id"].isin(keep)]


def _apply_conditions(base, conditions: dict):
    """Attribute AND-filters: ``{col: value}`` -> ==, ``{col: [values]}`` -> isin."""
    missing = []
    for col, val in (conditions or {}).items():
        if col not in base.columns:
            missing.append(col)
            continue
        base = base[base[col].isin(list(val))] if isinstance(val, (list, tuple, set)) else base[base[col] == val]
    return base, missing


def _apply_modes(base, modes, use_groups=None):
    """Mode filter: ``(links, applied, diagnostics)``.

    Modes are ProjectCard names (``drive``, ``bike``, ...); they are mapped to GMNS use names
    (:func:`~netstead.select.modes.mode_uses`) and matched against the ``allowed_uses``/``modes``
    column. ``use_groups`` maps a use_group name to its member uses. Without mode data the
    selection is unfiltered (modes are still emitted).
    """
    if not modes:
        return base, False, []
    want = mode_uses(modes, use_groups)
    if want is None:
        return base, False, []
    for col in ("allowed_uses", "modes"):
        if col in base.columns:
            tokens = base[col].apply(split_uses)
            kept = base[tokens.apply(lambda have: bool(want & have))]
            label = list(modes)
            if kept.empty:
                seen = sorted(set().union(*tokens)) if len(tokens) else []
                shown = ", ".join(seen[:10]) + (", ..." if len(seen) > 10 else "")
                return (
                    kept,
                    True,
                    [f"mode filter {label} matched none of {len(base)} link(s); their {col}: {shown or '(empty)'}"],
                )
            diags = [f"mode filter {label}: {len(base)} -> {len(kept)} link(s)"] if len(kept) < len(base) else []
            return kept, True, diags
    return base, False, []  # no mode data: selection unfiltered (modes still emitted)


def _base_selection(intent, links, index):
    """``(links, source label, notes)`` for the primary selector; notes record any ref/name fallback."""
    if intent.link_ids:
        return links[links["link_id"].isin(list(intent.link_ids))], "explicit link_ids", []
    if intent.select_all:
        return links, "all links", []
    base, notes = _facility_links(links, intent.facility, index)
    return base, "facility " + repr(list(intent.facility.refs()) + list(intent.facility.names())), notes


def _whole(intent, base, diags):
    link_ids = [to_py(i) for i in base["link_id"].tolist()]
    status = "resolved" if link_ids else "not_found"
    return SelectionResult(status, intent, link_ids=link_ids, node_path=[], diagnostics=diags)


def _row_len(nx, ny, row):
    return link_length(
        nx, ny, {"length": getattr(row, "length", None), "from_node_id": row.from_node_id, "to_node_id": row.to_node_id}
    )


def _result_from_path(intent, base, from_m, to_m, path, diags, *, cand_note):
    if path is None:
        return SelectionResult(
            "not_found",
            intent,
            from_match=from_m,
            to_match=to_m,
            diagnostics=[*diags, "no path between resolved anchor nodes"],
        )
    link_ids = [to_py(i) for i in path[0]]
    node_path = [to_py(n) for n in path[1]]
    status = "ambiguous" if (from_m.candidates or to_m.candidates) else "resolved"
    if status == "ambiguous":
        diags = [*diags, cand_note]
    return SelectionResult(
        status, intent, link_ids=link_ids, node_path=node_path, from_match=from_m, to_match=to_m, diagnostics=diags
    )


def _resolve_anchors(intent, allowed: set, resolve_unpinned):
    """``(from match, to match)``: a pinned role is accepted only on ``allowed``; others go to ``resolve_unpinned``."""
    matches = []
    for role in ("from", "to"):
        text, pin = getattr(intent, f"{role}_anchor"), getattr(intent, f"{role}_node")
        if pin is not None:
            matches.append(_anchors.pinned_anchor(text, pin, allowed))
        else:
            matches.append(resolve_unpinned(text, role))
    return matches[0], matches[1]


def _freeway_segment(intent, base, links, index, nx, ny, diags, among):
    """Gore/merge anchors + shortest directed path along the carriageway."""
    facility_all = _facility_links(links, intent.facility, index)[0] if intent.facility else base
    directed_nodes = set(base["from_node_id"]) | set(base["to_node_id"])
    mainline_nodes = set(facility_all["from_node_id"]) | set(facility_all["to_node_id"])
    interchanges = _anchors.classify_interchanges(links, directed_nodes, mainline_nodes, index=index)
    from_m, to_m = _resolve_anchors(
        intent,
        directed_nodes,
        lambda text, role: _anchors.resolve_anchor(text, role, links, interchanges, index=index, among=among),
    )
    diags = [*diags, from_m.detail, to_m.detail]
    if from_m.node_id is None or to_m.node_id is None:
        return SelectionResult("not_found", intent, from_match=from_m, to_match=to_m, diagnostics=diags)
    adj = collections.defaultdict(list)
    for row in base.itertuples():
        adj[row.from_node_id].append((row.to_node_id, _row_len(nx, ny, row), row.link_id))
    return _result_from_path(
        intent,
        base,
        from_m,
        to_m,
        dijkstra_links(adj, from_m.node_id, to_m.node_id),
        diags,
        cand_note="multiple interchange candidates; picked nearest (see candidates)",
    )


def _surface_segment(intent, base, links, nodes, index, nx, ny, diags, among):
    """At-grade intersection (or freeway-ramp) anchors + undirected path along the facility.

    When the facility's own links don't join the two anchors, short links next to it may bridge
    the gaps (:meth:`StreetIndex.bridge_rows_near`), at a penalty so the facility is preferred.
    """
    facility_nodes = set(base["from_node_id"]) | set(base["to_node_id"])
    from_m, to_m = _resolve_anchors(
        intent,
        facility_nodes,
        lambda text, _role: _anchors.resolve_surface_anchor(text, facility_nodes, links, index=index, among=among),
    )
    diags = [*diags, from_m.detail, to_m.detail]
    if from_m.node_id is None or to_m.node_id is None:
        return SelectionResult("not_found", intent, from_match=from_m, to_match=to_m, diagnostics=diags)
    adj = collections.defaultdict(list)  # undirected: surface streets are usually two-way
    _add_undirected(adj, base, nx, ny)
    path = dijkstra_links(adj, from_m.node_id, to_m.node_id)
    if path is None:
        bridges = links.iloc[index.bridge_rows_near(facility_nodes)]
        bridges = bridges[~bridges["link_id"].isin(base["link_id"])]
        bx, by = _node_coords(nodes, set(bridges["from_node_id"]) | set(bridges["to_node_id"]))
        _add_undirected(adj, bridges, {**nx, **bx}, {**ny, **by}, penalty=_BRIDGE_PENALTY)
        path = dijkstra_links(adj, from_m.node_id, to_m.node_id)
        if path is not None:
            crossed = set(path[0]) & set(bridges["link_id"])
            diags = [*diags, f"bridged gaps in the facility with {len(crossed)} short or unnamed link(s)"]
    return _result_from_path(
        intent,
        base,
        from_m,
        to_m,
        path,
        diags,
        cand_note="multiple intersection candidates; picked nearest (see candidates)",
    )


def _add_undirected(adj, frame, nx, ny, *, penalty: float = 1.0) -> None:
    """Add each link of ``frame`` to ``adj`` both ways, weighted by its length times ``penalty``."""
    for row in frame.itertuples():
        w = _row_len(nx, ny, row) * penalty
        adj[row.from_node_id].append((row.to_node_id, w, row.link_id))
        adj[row.to_node_id].append((row.from_node_id, w, row.link_id))


def resolve_frames(
    intent: SelectionIntent, links, nodes, use_groups=None, *, index: StreetIndex | None = None
) -> SelectionResult:
    """Resolve against materialized pandas link/node frames (the testable core).

    ``use_groups`` (use_group name -> member uses) widens mode filters; see :func:`_apply_modes`.
    ``index`` is a :class:`~netstead.select.streets.StreetIndex` of ``links``: pass one built once
    per network version, or each call builds its own.
    """
    if index is None:
        index = StreetIndex.build(links)
    else:
        index.check(links)

    # 1. base selection
    base, source, notes = _base_selection(intent, links, index)
    if base.empty:
        return SelectionResult("not_found", intent, diagnostics=[f"{source}: no links matched"])
    found = base  # before the filters: its cross streets are where an anchor may leave out its type

    # 2. direction filter (facility only)
    direction = intent.facility.direction if intent.facility else None
    nx, ny = {}, {}  # bearings and segment lengths need coordinates; a whole selection does not
    if direction or (intent.from_anchor and intent.to_anchor):
        nx, ny = _node_coords(nodes, set(base["from_node_id"]) | set(base["to_node_id"]))
    if direction:
        base = _filter_direction(base, direction, nx, ny)
        if base.empty:
            return SelectionResult(
                "not_found", intent, diagnostics=[*notes, f"facility found but no links in direction {direction!r}"]
            )

    # 3. modes + attribute conditions (AND)
    base, missing_cols = _apply_conditions(base, intent.conditions)
    base, _mode_applied, mode_diags = _apply_modes(base, intent.modes, use_groups)
    diags = [*notes, f"{source}: {len(base)} link(s)", *mode_diags]
    if missing_cols:
        diags.append(f"ignored conditions on unknown column(s): {missing_cols}")
    if base.empty:
        return SelectionResult("not_found", intent, diagnostics=[*diags, "no links after filters"])

    # 4. whole-facility / all  vs  segment (from/to)
    if not (intent.from_anchor and intent.to_anchor):
        return _whole(intent, base, diags)

    # --- segment (from/to): freeway gore/merge, or surface at-grade ---
    freeway = base["facility_type"].isin(_FREEWAY_TYPES)
    rows = np.flatnonzero(np.isin(index.link_ids, found["link_id"].to_numpy()))
    among = index.names_meeting(rows, ramp_hops=_anchors._MAX_RAMP_HOPS if freeway.any() else 0)
    for anchor in (intent.from_anchor, intent.to_anchor):
        diags.extend(_loose_match_note(anchor, index.lookup_name(anchor, among=among)))
    if intent.facility is None or not freeway.any():
        return _surface_segment(intent, base, links, nodes, index, nx, ny, diags, among)
    result = _freeway_segment(intent, base, links, index, nx, ny, diags, among)
    if result.status != "not_found" or freeway.all():
        return result
    # Partly freeway (a trunk road with signals): its anchors may be at-grade intersections instead.
    surface = _surface_segment(
        intent, base, links, nodes, index, nx, ny, [*diags, "no interchange path; tried at-grade intersections"], among
    )
    return surface if surface.status != "not_found" else result


def resolve(intent: SelectionIntent, net) -> SelectionResult:
    """Resolve against a netstead Network (materializes link/node tables)."""
    links, nodes, use_groups = network_frames(net)
    return resolve_frames(intent, links, nodes, use_groups=use_groups)


def network_frames(net) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, set[str]]]:
    """``(links, nodes, use_groups)`` of a netstead Network, materialized for :func:`resolve_frames`."""

    def _pd(table):
        return table.to_pandas() if hasattr(table, "to_pandas") else table.execute()

    groups = getattr(net, "use_group", None)
    group_df = _pd(groups) if groups is not None else None
    return _pd(net.links), _pd(net.nodes), _use_groups(group_df)


def _use_groups(df) -> dict[str, set[str]]:
    """``{use_group: member uses}`` from a ``use_group`` frame (empty when absent or malformed)."""
    if df is None or not {"use_group", "uses"} <= set(df.columns):
        return {}
    return {str(g): split_uses(u) for g, u in zip(df["use_group"], df["uses"], strict=False)}
