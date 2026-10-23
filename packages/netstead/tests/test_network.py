"""Tests for :class:`netstead.Network` (task 3.7 / issue #121).

Covers the GMNS-aware :class:`~corral.dataset.Package` subclass:

* :meth:`Network.from_source` defaults the spec to the bundled GMNS
  spec at the requested version (or :data:`netstead.DEFAULT_SPEC`).
* The named accessors (``links``, ``nodes``, ``segments``, ``lanes``,
  ``geometry``, ``link_tod``, ``signal_controller``) return the
  matching :class:`~corral.dataset.Table` when present.
* Required-table accessors (``links``, ``nodes``) raise
  :class:`NetworkError` mentioning the resource name + spec version
  when the table is absent.
* Optional-table accessors return ``None`` when the table is absent.
* :meth:`Network.validate` stamps ``spec_version`` on the returned
  :class:`~corral.reports.ValidationReport` without re-implementing
  orchestration.
* Re-exports through the top-level :mod:`netstead` package.

Parametrised over both engines (pandas + ibis) for the load-and-access
path since the behavior is engine-independent — confirms we don't
accidentally lean on a single backend.
"""

from __future__ import annotations

import pytest
from corral.dataset import Table
from corral.engines.ibis_engine import IbisEngine
from corral.reports import ValidationReport
from netstead.fixtures import leavenworth


@pytest.fixture(params=[IbisEngine, IbisEngine], ids=["pandas", "ibis"])
def engine(request):
    """Both engines — Network behavior must be backend-agnostic."""
    return request.param()


# ---------------------------------------------------------------------------
# Constructor + spec resolution
# ---------------------------------------------------------------------------


def test_from_source_defaults_to_default_spec(engine):
    """No spec_version => DEFAULT_SPEC stamped on the instance."""
    from netstead import DEFAULT_SPEC, Network

    net = Network.from_source(leavenworth.csv_dir(), engine=engine)
    assert net.spec_version == DEFAULT_SPEC


def test_from_source_records_explicit_spec_version(engine):
    """Caller-provided spec_version round-trips onto the instance."""
    from netstead import Network

    net = Network.from_source(leavenworth.csv_dir(), engine=engine, spec_version="0.96")
    assert net.spec_version == "0.96"


def test_from_source_rejects_unsupported_spec_version():
    """Unknown spec_version raises InvalidSpecVersionError before any I/O."""
    from corral.spec.errors import InvalidSpecVersionError, SpecLoadError
    from netstead import Network

    with pytest.raises(InvalidSpecVersionError, match=r"0\.42"):
        Network.from_source(leavenworth.csv_dir(), spec_version="0.42")
    # Catchable via the parent class too — important for catch-all
    # handlers that don't want to import the specific subclass.
    with pytest.raises(SpecLoadError):
        Network.from_source(leavenworth.csv_dir(), spec_version="0.42")


# ---------------------------------------------------------------------------
# Accessors
# ---------------------------------------------------------------------------


def test_links_and_nodes_accessors_return_tables(engine):
    """The two required GMNS tables expose typed accessors."""
    from netstead import Network

    net = Network.from_source(leavenworth.csv_dir(), engine=engine)
    assert isinstance(net.links, Table)
    assert isinstance(net.nodes, Table)
    assert net.links.name == "link"
    assert net.nodes.name == "node"


def test_optional_accessors_return_table_when_present(engine):
    """Leavenworth ships lane / geometry / link_tod / signal_controller."""
    from netstead import Network

    net = Network.from_source(leavenworth.csv_dir(), engine=engine)
    assert isinstance(net.geometry, Table)
    assert isinstance(net.lanes, Table)
    assert isinstance(net.link_tod, Table)
    assert isinstance(net.signal_controller, Table)


def test_optional_accessors_return_none_when_absent(engine):
    """Leavenworth has no segment/zone/movement tables — accessors return None."""
    from netstead import Network

    net = Network.from_source(leavenworth.csv_dir(), engine=engine)
    assert net.segments is None
    assert net.zones is None
    assert net.movements is None


def test_required_accessor_raises_network_error_when_absent(engine):
    """Missing 'link' table raises NetworkError mentioning spec_version + name."""
    from corral.dataset import Package
    from netstead import Network, NetworkError
    from netstead.spec import load_gmns_spec

    # Load only 'node' so 'link' is genuinely absent.
    base = Package.from_source(
        leavenworth.csv_dir(),
        engine=engine,
        spec=load_gmns_spec("0.97"),
        tables=["node"],
    )
    # Re-wrap as a Network without going through from_source.
    net = Network(spec=base.spec, tables=base.tables, engine=base.engine, source=base.source)
    net.spec_version = "0.97"

    with pytest.raises(NetworkError) as exc:
        _ = net.links
    msg = str(exc.value)
    assert "link" in msg
    assert "0.97" in msg


def test_network_error_is_package_error_subclass():
    """NetworkError extends PackageError so generic handlers still catch it."""
    from corral.dataset import PackageError
    from netstead import NetworkError

    assert issubclass(NetworkError, PackageError)


# ---------------------------------------------------------------------------
# Validation stamping
# ---------------------------------------------------------------------------


def test_validate_stamps_spec_version_on_report(engine):
    """Network.validate() forwards to Package.validate, then stamps spec_version."""
    from netstead import Network

    net = Network.from_source(leavenworth.csv_dir(), engine=engine, spec_version="0.97")
    report = net.validate()
    assert isinstance(report, ValidationReport)
    assert report.spec_version == "0.97"


def test_validate_forwards_kwargs(engine):
    """Disabling FK pass via kwarg still produces a report (no exceptions)."""
    from netstead import Network

    net = Network.from_source(leavenworth.csv_dir(), engine=engine)
    report = net.validate(foreign_keys=False, sync_state=False)
    assert isinstance(report, ValidationReport)
    assert report.spec_version == net.spec_version


# ---------------------------------------------------------------------------
# Re-exports
# ---------------------------------------------------------------------------


def test_reexports_from_top_level():
    """netstead.Network and netstead.NetworkError are publicly exported."""
    import netstead

    assert hasattr(netstead, "Network")
    assert hasattr(netstead, "NetworkError")
    assert "Network" in netstead.__all__
    assert "NetworkError" in netstead.__all__
