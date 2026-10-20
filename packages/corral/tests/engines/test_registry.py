"""Registry mechanics tests."""

from __future__ import annotations

from typing import Any

import pytest
from corral import engines as eng
from corral.engines import (
    Engine,
    EngineNotAvailableError,
    get_engine,
    list_engines,
    register_engine,
    resolve_engine,
    set_default_engine,
)


class FakeEngine:
    """Minimal in-test engine satisfying the full post-#134 protocol.

    Includes every per-format primitive plus ``cast_schema`` so the
    runtime structural check passes after the engine/adapter inversion.
    """

    def __init__(self, name: str = "fake"):
        self.name = name

    # Read primitives
    def read_csv(self, source, schema=None, **kwargs):
        return None

    def read_parquet(self, source, schema=None, *, hive_partitioning: bool = False, **kwargs):
        return None

    def read_duckdb_table(self, source, *, table: str, schema=None, **kwargs):
        return None

    def from_records(self, records, schema=None):
        return None

    def from_arrow(self, arrow_table):
        return None

    # Write primitives
    def write_csv(self, expr, dest, **kwargs) -> None:
        return None

    def write_parquet(self, expr, dest, *, partition_by=None, **kwargs) -> None:
        return None

    def write_duckdb_table(self, expr, dest, *, table: str, **kwargs) -> None:
        return None

    # Schema cast
    def cast_schema(self, expr, schema):
        return expr

    # Convenience delegators
    def scan(self, source, format=None, schema=None, **kwargs):
        return None

    def write(self, expr, dest, fmt: str, **kwargs: Any) -> None:
        return None

    # Materialize / converters
    def materialize(self, expr):
        return None

    def to_pandas(self, expr):
        return None

    def to_polars(self, expr):
        return None

    def to_arrow(self, expr):
        return None

    # Lazy introspection — promoted to the protocol in Phase 5 hardening.
    def columns(self, expr):
        return []

    def count(self, expr):
        return 0

    def head(self, expr, n):
        return expr

    def select(self, expr, columns):
        return expr

    def order_by(self, expr, columns, descending=False):
        return expr

    def limit(self, expr, n, offset=0):
        return expr


@pytest.fixture(autouse=True)
def _restore_registry():
    """Snapshot + restore the module-level registry around each test."""
    saved_registry = dict(eng._REGISTRY)
    saved_default = eng._DEFAULT
    yield
    eng._REGISTRY.clear()
    eng._REGISTRY.update(saved_registry)
    eng._DEFAULT = saved_default


def test_ibis_is_auto_registered_as_default():
    assert "ibis" in list_engines()
    assert get_engine().name == "ibis"


def test_register_and_get_by_name():
    fake = FakeEngine()
    register_engine(fake)
    assert get_engine("fake") is fake


def test_get_with_no_arg_returns_default():
    # default at this point is whatever was registered first (ibis)
    default = get_engine()
    assert default.name == eng._DEFAULT


def test_set_default_engine_changes_default():
    fake = FakeEngine()
    register_engine(fake)
    set_default_engine("fake")
    assert get_engine().name == "fake"


def test_register_with_default_flag_sets_default_in_one_call():
    fake = FakeEngine(name="fake-default")
    register_engine(fake, default=True)
    assert get_engine().name == "fake-default"


def test_get_unknown_engine_raises_with_helpful_message():
    with pytest.raises(EngineNotAvailableError) as excinfo:
        get_engine("nonexistent")
    msg = str(excinfo.value)
    assert "nonexistent" in msg
    assert "available" in msg
    # The message should list at least one currently-registered engine
    for name in list_engines():
        assert name in msg
        break


def test_set_default_to_unknown_raises():
    with pytest.raises(EngineNotAvailableError):
        set_default_engine("nonexistent")


def test_re_registration_overwrites_without_warning():
    first = FakeEngine()
    second = FakeEngine()
    register_engine(first)
    register_engine(second)
    assert get_engine("fake") is second


def test_empty_registry_raises():
    eng._REGISTRY.clear()
    eng._DEFAULT = None
    with pytest.raises(EngineNotAvailableError) as excinfo:
        get_engine()
    assert "no engines registered" in str(excinfo.value)


def test_list_engines_is_sorted():
    register_engine(FakeEngine(name="zzz"))
    register_engine(FakeEngine(name="aaa"))
    names = list_engines()
    assert names == sorted(names)


def test_register_non_engine_raises_structured_typeerror():
    with pytest.raises(TypeError) as excinfo:
        register_engine(object())  # type: ignore[arg-type]
    msg = str(excinfo.value)
    assert "Engine protocol" in msg
    # Message should name the methods the protocol requires so the
    # caller can see what's missing.
    assert "scan" in msg
    assert "materialize" in msg
    assert "to_pandas" in msg
    assert "to_polars" in msg
    assert "to_arrow" in msg
    assert "write" in msg


def test_register_names_the_members_an_engine_is_missing():
    """M4 on #245: an engine written before ``to_arrow`` joined the protocol is told exactly that."""
    stale = type("StaleEngine", (FakeEngine,), {"to_arrow": None})(name="stale")
    with pytest.raises(TypeError, match=r"missing: to_arrow\b"):
        register_engine(stale)


def test_register_engine_with_empty_name_raises_valueerror():
    bad = FakeEngine(name="")
    with pytest.raises(ValueError):
        register_engine(bad)


# ---------------------------------------------------------------------------
# resolve_engine — replaces two duplicate _resolve_engine helpers in the
# netstead CLI / server (S1)
# ---------------------------------------------------------------------------


def test_resolve_engine_none_returns_default():
    """`resolve_engine(None)` returns the registered default engine."""
    assert isinstance(resolve_engine(None), Engine)
    # Same instance contract as get_engine() — default lookup.
    assert type(resolve_engine(None)) is type(get_engine())


def test_resolve_engine_names_are_case_insensitive():
    """`resolve_engine` accepts lower/upper/mixed case for the one engine."""
    for spelling in ("ibis", "IBIS", "DuckDB", "duckdb"):
        assert type(resolve_engine(spelling)).__name__ == "IbisEngine"


def test_resolve_engine_unknown_raises_value_error_with_known_engines():
    """Unknown engine name raises ValueError that lists the known options."""
    with pytest.raises(ValueError, match="unknown engine 'bogus'"):
        resolve_engine("bogus")
