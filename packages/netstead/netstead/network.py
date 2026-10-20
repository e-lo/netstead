"""GMNS-aware :class:`Network` — see :class:`Network`.

A :class:`Network` is a thin GMNS subclass of
:class:`corral.dataset.Package`. It adds three things on top of the
generic package surface:

* **GMNS spec defaulting.** :meth:`Network.from_source` resolves the
  vendored GMNS spec (via :mod:`netstead.spec`) instead of asking the
  caller to pass a ``datapackage.json`` path.
* **Named accessors** for the canonical GMNS tables
  (``links``, ``nodes``, ``segments``, ``lanes``, ``geometry``,
  ``link_tod``, ``zones``, ``movements``, ``signal_*``, …). Required
  GMNS tables raise :class:`NetworkError` when absent; optional tables
  return ``None`` Pythonically.
* **Spec-version stamping** on the validation report so renderers (and
  saved JSON / HTML reports) advertise which GMNS version the data was
  checked against.

Everything else — validation orchestration, scope, write, sync state,
batch — is inherited from :class:`~corral.dataset.Package` unchanged.
Network semantics (connectivity / TOD resolution / geometry assembly)
land in :mod:`netstead.semantics` (task 3.8); network-aware scope
(``from_nodes``, BFS, network buffer) lands in :mod:`netstead.scope`
(task 3.10).

See ``docs/architecture.md`` section 5 ("Module map — netstead") for
the planned division of responsibility across ``network.py`` and its
siblings.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from corral.dataset import Package, PackageError, Table
from corral.reports import ValidationReport

from .spec import DEFAULT_SPEC, load_gmns_spec

if TYPE_CHECKING:  # pragma: no cover - typing only
    from corral.engines.base import Engine


__all__ = ["Network", "NetworkError"]


# ---------------------------------------------------------------------------
# Canonical GMNS table → Python attribute mapping
# ---------------------------------------------------------------------------
#
# The values are GMNS resource names exactly as they appear in the
# vendored ``<resource>.schema.json`` files (so the lookup is a direct
# ``Package.tables[name]`` hit). Keys are the Python accessor names we
# expose; plural English where GMNS uses a singular resource name
# (``link`` -> ``links``), bare resource name where pluralising adds
# nothing (``geometry``, ``link_tod``, ``signal_controller``).
#
# Required tables (``link``, ``node``) are kept in :data:`_REQUIRED`
# so the accessor knows whether to raise :class:`NetworkError` or
# return ``None`` when the table is missing. Adding a new GMNS table
# here is the entire change needed to expose a new accessor.

_TABLE_ATTRS: dict[str, str] = {
    # Core link/node graph
    "links": "link",
    "nodes": "node",
    # Geometry + lane detail
    "geometry": "geometry",
    "lanes": "lane",
    "segments": "segment",
    "segment_lanes": "segment_lane",
    # Time-of-day variants
    "link_tod": "link_tod",
    "lane_tod": "lane_tod",
    "segment_tod": "segment_tod",
    "segment_lane_tod": "segment_lane_tod",
    "movement_tod": "movement_tod",
    "time_set_definitions": "time_set_definitions",
    # Movements + zones + locations
    "movements": "movement",
    "zones": "zone",
    "locations": "location",
    "curb_segs": "curb_seg",
    # Signal family
    "signal_controller": "signal_controller",
    "signal_coordination": "signal_coordination",
    "signal_detector": "signal_detector",
    "signal_phase_mvmt": "signal_phase_mvmt",
    "signal_timing_phase": "signal_timing_phase",
    "signal_timing_plan": "signal_timing_plan",
    # Mode + config
    "use_definition": "use_definition",
    "use_group": "use_group",
    "config": "config",
}

_REQUIRED: frozenset[str] = frozenset({"link", "node"})


class NetworkError(PackageError):
    """A GMNS-required resource is missing or violates GMNS semantics.

    Specialises :class:`~corral.dataset.PackageError` so generic
    package-error handlers still catch it, while GMNS-aware callers can
    branch on the more specific type. Raised today by
    :attr:`Network.links` / :attr:`Network.nodes` when the underlying
    resource is absent from the loaded package; task 3.8 will reuse it
    for GMNS-semantics violations (broken connectivity, missing TOD
    references, …).
    """


@dataclass
class Network(Package):
    """GMNS-aware :class:`~corral.dataset.Package`.

    Subclass of :class:`~corral.dataset.Package` that defaults to the
    bundled GMNS spec (see :mod:`netstead.spec`) and exposes named
    accessors for the canonical GMNS resources (``link``, ``node``,
    ``segment``, ``lane``, ``geometry``, ``link_tod``,
    ``signal_controller``, …). Validation reports produced by
    :meth:`validate` carry the active :attr:`spec_version` so saved
    JSON / HTML reports are self-describing.

    Attributes:
        spec_version: GMNS spec version this network was loaded
            against — one of :data:`netstead.spec.SUPPORTED_SPECS`.
            Populated by :meth:`from_source`; defaults to ``""`` when
            the dataclass is constructed directly (the structural
            check still works, but :meth:`validate`'s stamp will be
            empty until the caller sets it).

    Examples:
        Load the bundled Leavenworth fixture, hit the named accessors,
        and run validation::

            >>> from netstead import Network
            >>> from netstead.fixtures import leavenworth
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> net = Network.from_source(leavenworth.csv_dir(), engine=IbisEngine())
            >>> net.links.name
            'link'
            >>> net.nodes.name
            'node'
            >>> net.segments is None  # Leavenworth has no segment table
            True
            >>> report = net.validate()
            >>> report.spec_version == net.spec_version
            True
    """

    # Optional because a Network can be constructed bare (e.g. test
    # fixtures, `from_tables`); `from_source` populates it. Using
    # ``None`` rather than ``""`` so error messages render as
    # ``"unknown"`` rather than the awkward ``"''"``.
    spec_version: str | None = field(default=None)

    # ------------------------------------------------------------------
    # Constructor
    # ------------------------------------------------------------------

    @classmethod
    def from_source(  # type: ignore[override]
        cls,
        source: str | Path,
        *,
        engine: Engine | None = None,
        spec_version: str | None = None,
        tables: Iterable[str] | None = None,
        credentials: dict[str, str] | None = None,
    ) -> Network:
        """Load a GMNS network from ``source``, defaulting the spec to gmns@spec_version.

        Resolves ``spec_version`` (caller-provided or
        :data:`netstead.spec.DEFAULT_SPEC`), validates it against
        :data:`netstead.spec.SUPPORTED_SPECS`, loads the matching
        vendored spec via :func:`netstead.spec.load_gmns_spec`, then
        delegates to :meth:`Package.from_source` for the actual I/O.
        The returned :class:`Network` carries the resolved version on
        :attr:`spec_version`.

        Args:
            source: Path / URL / directory pointing at the GMNS package
                — anything :meth:`Package.from_source` accepts.
            engine: Engine to materialise through. Defaults to the
                corral default (typically
                :class:`~corral.engines.ibis_engine.IbisEngine`).
            spec_version: GMNS spec version to validate against.
                Defaults to :data:`netstead.spec.DEFAULT_SPEC`. Must be a
                member of :data:`netstead.spec.SUPPORTED_SPECS`.
            tables: Optional subset of resource names to load. Useful
                for memory-efficient partial loads (e.g. ``["link",
                "node"]``).
            credentials: Optional explicit credentials for a URL
                ``source`` (fsspec storage options, e.g. ``{"key": ...,
                "secret": ...}`` for S3). Forwarded to
                :meth:`Package.from_source`; ignored for local paths.

        Returns:
            A populated :class:`Network` with lazy
            :class:`~corral.dataset.Table` entries and
            :attr:`spec_version` stamped.

        Raises:
            InvalidSpecVersionError: If ``spec_version`` is not in
                :data:`netstead.spec.SUPPORTED_SPECS` (subclass of
                :class:`~corral.spec.errors.SpecLoadError`).

        Examples:
            >>> from netstead import Network
            >>> from netstead.fixtures import leavenworth
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> net = Network.from_source(leavenworth.csv_dir(), engine=IbisEngine())
            >>> "link" in net
            True
            >>> net.spec_version
            '0.97'
        """
        # ``load_gmns_spec`` runs the same _check_version that would
        # have raised here; delegating avoids two copies of the
        # validation logic. The InvalidSpecVersionError bubbles up.
        resolved_version = spec_version if spec_version is not None else DEFAULT_SPEC
        gmns_spec = load_gmns_spec(resolved_version)
        # Delegate to Package.from_source — composition over copy-paste.
        # Package returns a `Package` instance; we re-pack into `cls`
        # (always `Network` in practice) so the named accessors light up.
        base = Package.from_source(source, engine=engine, spec=gmns_spec, tables=tables, credentials=credentials)
        # Canonicalise geometry to WKB bytes in memory (geometry ADR): CSV
        # packages arrive as WKT, GeoParquet as WKB — this converges both.
        from netstead._geometry import convert_geometry_to_wkb

        convert_geometry_to_wkb(base)
        return cls(
            spec=base.spec,
            tables=base.tables,
            engine=base.engine,
            source=base.source,
            dirty_tracker=base.dirty_tracker,
            metadata=dict(base.metadata),
            spec_version=resolved_version,
        )

    def write(  # type: ignore[override]
        self,
        dest: str | Path,
        *,
        format: str | None = None,
        overwrite: bool = False,
        strict_sync: bool = False,
    ) -> None:
        """Persist the network, encoding geometry per container (geometry ADR).

        In memory geometry is canonical **WKB** bytes. Binary containers
        (Parquet, DuckDB) store it as-is; a **CSV** target — a csv directory
        or a CSV-in-zip package (``format="zip"`` / ``.zip``) — must carry
        **WKT** text, so the geometry-bearing tables are decoded WKB→WKT
        first — on a throwaway proxy, leaving this network's in-memory tables
        WKB. Delegates to :meth:`corral.dataset.Package.write` for the
        actual I/O.
        """
        from dataclasses import replace

        from corral.dataset import infer_write_format

        from netstead._geometry import decode_geometry_to_wkt

        try:
            target_format = infer_write_format(dest, format)
        except Exception:
            target_format = None  # let Package.write raise the canonical error
        if target_format in {"csv", "zipcsv"}:
            proxy = replace(self, tables=dict(self.tables))
            decode_geometry_to_wkt(proxy)
            Package.write(proxy, dest, format=format, overwrite=overwrite, strict_sync=strict_sync)
            return
        super().write(dest, format=format, overwrite=overwrite, strict_sync=strict_sync)

    # ------------------------------------------------------------------
    # Indexes + scope accessor — architecture §6.2 chainable surface
    # ------------------------------------------------------------------

    def build_indexes(self, *, spatial: bool = True, graph: bool = True) -> Network:
        """Pre-build the indexes the network-aware scope ops need.

        Wraps :func:`netstead.indexes.build_indexes` so the documented
        form ``net.build_indexes(spatial=True, graph=True)`` from
        ``docs/architecture.md`` §6.2 works as a method instead of a
        free function. Built indexes are cached on :attr:`metadata`
        under the same keys :mod:`netstead.scope` consults, so the
        next scope op finds them without rebuilding.

        Returns ``self`` so callers can chain
        (``net.build_indexes(...).scope.from_nodes([...])``).

        Args:
            spatial: Build the :class:`~netstead.indexes.SpatialIndex`
                from ``link.geometry``. No-op when ``link`` has no
                ``geometry`` column.
            graph: Build the :class:`~netstead.graph.GMNSGraph` (scipy CSR
                routing engine) from ``link.from_node_id`` + ``link.to_node_id``.

        Returns:
            ``self`` (for chaining).

        Examples:
            >>> import pytest
            >>> _ = pytest.importorskip("scipy")
            >>> from netstead import Network
            >>> from netstead.fixtures import leavenworth
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> net = Network.from_source(leavenworth.csv_dir(), engine=IbisEngine())
            >>> net.build_indexes(spatial=True, graph=True) is net
            True
        """
        # Local imports keep netstead → netstead.indexes / netstead.scope
        # edges lazy — both pull in optional deps (scipy, shapely)
        # that callers may not have installed.
        from netstead.indexes import build_indexes as _build
        from netstead.scope.scope import _GRAPH_INDEX_KEY, _SPATIAL_INDEX_KEY

        links = self.tables.get("link")
        nodes = self.tables.get("node")
        # Skip graph build when nodes are missing rather than letting
        # build_indexes raise — partial loads (e.g. links-only) should
        # still build the spatial index they CAN.
        wants_graph = graph and nodes is not None
        wants_spatial = spatial and links is not None and "geometry" in links.columns()

        if not (wants_graph or wants_spatial):
            return self

        spatial_idx, graph_idx = _build(
            links=links,
            nodes=nodes,
            spatial=wants_spatial,
            graph=wants_graph,
        )
        if spatial_idx is not None:
            self.metadata[_SPATIAL_INDEX_KEY] = spatial_idx
        if graph_idx is not None:
            self.metadata[_GRAPH_INDEX_KEY] = graph_idx
        return self

    @property
    def scope(self):  # type: ignore[override]
        """Chainable scope-builder accessor — see :class:`netstead.scope.NetworkScopeAccessor`.

        Returns an accessor partially-bound to ``self`` so the
        architecture-documented form
        ``net.scope.from_nodes([1, 2, 3]).buffer_network("0.5mi")``
        works as a method chain (per ``docs/architecture.md`` §6.2).

        The accessor is also callable, so the inherited
        :meth:`corral.dataset.Package.scope` form
        ``net.scope(tables=[...], bbox=...)`` keeps working — see
        :meth:`NetworkScopeAccessor.__call__`.

        Returns:
            A fresh :class:`~netstead.scope.NetworkScopeAccessor` —
            cheap to construct, no state to share between callers.

        Examples:
            >>> import pytest
            >>> _ = pytest.importorskip("scipy")
            >>> from netstead import Network
            >>> from netstead.fixtures import leavenworth
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> net = Network.from_source(leavenworth.csv_dir(), engine=IbisEngine())
            >>> scoped = net.scope.from_nodes([1, 2, 3], path_between=False)
            >>> 1 in scoped.node_ids
            True

            Backward-compat — the callable form still routes to the
            inherited Package.scope::

                >>> only_links = net.scope(tables=["link"])
                >>> only_links.keys()
                ['link']
        """
        from netstead.scope import NetworkScopeAccessor

        return NetworkScopeAccessor(self)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _repr_html_(self) -> str:  # type: ignore[override]
        """Render a Jupyter-friendly card for the GMNS network.

        Extends :meth:`Package._repr_html_` with a header label of
        ``"Network"``, the active GMNS :attr:`spec_version`, and a
        ``links``/``nodes`` row-count line so the GMNS shape of the
        package is visible at a glance.

        Reuses :func:`corral.dataset.package._render_package_card`
        so the table preview and styling can't drift from the parent
        class.

        Examples:
            >>> from netstead import Network
            >>> from netstead.fixtures import leavenworth
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> net = Network.from_source(leavenworth.csv_dir(), engine=IbisEngine())
            >>> html = net._repr_html_()
            >>> html.startswith("<div")
            True
            >>> "Network" in html
            True
            >>> "0.97" in html
            True
        """
        from corral.dataset.package import _render_package_card
        from corral.notebook import card, kv_line, small_table, truncation_note

        extra: list[tuple[str, object]] = []
        if self.spec_version:
            extra.append(("gmns", self.spec_version))
        # Only emit links/nodes counts when present — a partial load
        # (e.g. just nodes) shouldn't crash the preview.
        link_table = self.tables.get("link")
        node_table = self.tables.get("node")
        if link_table is not None:
            try:
                extra.append(("links", int(link_table.count())))
            except (RuntimeError, OSError, ValueError):
                extra.append(("links", "?"))
        if node_table is not None:
            try:
                extra.append(("nodes", int(node_table.count())))
            except (RuntimeError, OSError, ValueError):
                extra.append(("nodes", "?"))
        return _render_package_card(
            self,
            card,
            kv_line,
            small_table,
            truncation_note,
            title="Network",
            extra_kv=extra,
        )

    def validate(self, **kwargs) -> ValidationReport:  # type: ignore[override]
        """Run the inherited :meth:`Package.validate` and stamp :attr:`spec_version` on the report.

        Pure composition — the orchestration (structural / schema / FK /
        sync-state passes) lives in :class:`~corral.dataset.Package`
        and is not duplicated here. The override exists solely so the
        returned :class:`~corral.reports.ValidationReport` advertises
        which GMNS spec version the data was checked against (renderers
        and saved JSON / HTML reports surface this in the header).

        Args:
            **kwargs (Any): Forwarded verbatim to :meth:`Package.validate`.

        Returns:
            A :class:`~corral.reports.ValidationReport` with
            ``spec_version`` set to :attr:`self.spec_version`.

        Examples:
            >>> from netstead import Network
            >>> from netstead.fixtures import leavenworth
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> net = Network.from_source(leavenworth.csv_dir(), engine=IbisEngine())
            >>> report = net.validate(foreign_keys=False, sync_state=False)
            >>> report.spec_version == net.spec_version
            True
        """
        report = super().validate(**kwargs)
        report.spec_version = self.spec_version
        return report

    # ------------------------------------------------------------------
    # Internal accessor helper
    # ------------------------------------------------------------------

    def _get_table(self, attr_name: str) -> Table | None:
        """Look up a GMNS table by its Python accessor name.

        Returns the :class:`~corral.dataset.Table` when present;
        raises :class:`NetworkError` when the underlying GMNS resource
        is in :data:`_REQUIRED` but absent; returns ``None`` for an
        absent optional resource. Keeping every accessor on top of this
        helper means a new GMNS table is exposed by adding one entry
        to :data:`_TABLE_ATTRS` (and :data:`_REQUIRED` if needed).
        """
        resource_name = _TABLE_ATTRS[attr_name]
        table = self.tables.get(resource_name)
        if table is not None:
            return table
        if resource_name in _REQUIRED:
            spec_label = self.spec_version or "unknown"
            raise NetworkError(
                f"Required GMNS table {resource_name!r} is missing from this network "
                f"— required by GMNS spec {spec_label!r}."
            )
        return None

    # ------------------------------------------------------------------
    # Named accessors — required tables (raise NetworkError when absent)
    # ------------------------------------------------------------------

    @property
    def links(self) -> Table:
        """The ``link`` table (required by GMNS).

        Raises:
            NetworkError: If the ``link`` resource is absent from the
                loaded package.

        Examples:
            >>> from netstead import Network
            >>> from netstead.fixtures import leavenworth
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> Network.from_source(leavenworth.csv_dir(), engine=IbisEngine()).links.name
            'link'
        """
        t = self._get_table("links")
        assert t is not None  # _REQUIRED guarantees a raise on absent
        return t

    @property
    def nodes(self) -> Table:
        """The ``node`` table (required by GMNS).

        Raises:
            NetworkError: If the ``node`` resource is absent from the
                loaded package.
        """
        t = self._get_table("nodes")
        assert t is not None
        return t

    # ------------------------------------------------------------------
    # Named accessors — optional tables (return None when absent)
    # ------------------------------------------------------------------

    @property
    def geometry(self) -> Table | None:
        """The ``geometry`` table or ``None`` when absent."""
        return self._get_table("geometry")

    @property
    def lanes(self) -> Table | None:
        """The ``lane`` table or ``None`` when absent."""
        return self._get_table("lanes")

    @property
    def segments(self) -> Table | None:
        """The ``segment`` table or ``None`` when absent."""
        return self._get_table("segments")

    @property
    def segment_lanes(self) -> Table | None:
        """The ``segment_lane`` table or ``None`` when absent."""
        return self._get_table("segment_lanes")

    @property
    def link_tod(self) -> Table | None:
        """The ``link_tod`` table or ``None`` when absent."""
        return self._get_table("link_tod")

    @property
    def lane_tod(self) -> Table | None:
        """The ``lane_tod`` table or ``None`` when absent."""
        return self._get_table("lane_tod")

    @property
    def segment_tod(self) -> Table | None:
        """The ``segment_tod`` table or ``None`` when absent."""
        return self._get_table("segment_tod")

    @property
    def segment_lane_tod(self) -> Table | None:
        """The ``segment_lane_tod`` table or ``None`` when absent."""
        return self._get_table("segment_lane_tod")

    @property
    def movements(self) -> Table | None:
        """The ``movement`` table or ``None`` when absent."""
        return self._get_table("movements")

    @property
    def movement_tod(self) -> Table | None:
        """The ``movement_tod`` table or ``None`` when absent."""
        return self._get_table("movement_tod")

    @property
    def time_set_definitions(self) -> Table | None:
        """The ``time_set_definitions`` table or ``None`` when absent."""
        return self._get_table("time_set_definitions")

    @property
    def zones(self) -> Table | None:
        """The ``zone`` table or ``None`` when absent."""
        return self._get_table("zones")

    @property
    def locations(self) -> Table | None:
        """The ``location`` table or ``None`` when absent."""
        return self._get_table("locations")

    @property
    def curb_segs(self) -> Table | None:
        """The ``curb_seg`` table or ``None`` when absent."""
        return self._get_table("curb_segs")

    @property
    def signal_controller(self) -> Table | None:
        """The ``signal_controller`` table or ``None`` when absent."""
        return self._get_table("signal_controller")

    @property
    def signal_coordination(self) -> Table | None:
        """The ``signal_coordination`` table or ``None`` when absent."""
        return self._get_table("signal_coordination")

    @property
    def signal_detector(self) -> Table | None:
        """The ``signal_detector`` table or ``None`` when absent."""
        return self._get_table("signal_detector")

    @property
    def signal_phase_mvmt(self) -> Table | None:
        """The ``signal_phase_mvmt`` table or ``None`` when absent."""
        return self._get_table("signal_phase_mvmt")

    @property
    def signal_timing_phase(self) -> Table | None:
        """The ``signal_timing_phase`` table or ``None`` when absent."""
        return self._get_table("signal_timing_phase")

    @property
    def signal_timing_plan(self) -> Table | None:
        """The ``signal_timing_plan`` table or ``None`` when absent."""
        return self._get_table("signal_timing_plan")

    @property
    def use_definition(self) -> Table | None:
        """The ``use_definition`` table or ``None`` when absent."""
        return self._get_table("use_definition")

    @property
    def use_group(self) -> Table | None:
        """The ``use_group`` table or ``None`` when absent."""
        return self._get_table("use_group")

    @property
    def config(self) -> Table | None:
        """The ``config`` table or ``None`` when absent."""
        return self._get_table("config")
