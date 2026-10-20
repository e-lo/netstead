"""Tests for the global adapter registry."""

from __future__ import annotations

import pytest
from corral.io import (
    AdapterNotAvailableError,
    FormatError,
    InvalidAdapterError,
    _clear_registry,
    get_adapter,
    list_adapters,
    register_adapter,
)

from ._fakes import FakeCsvAdapter, FakeParquetAdapter


@pytest.fixture(autouse=True)
def _start_clean():
    """Each registry test starts with a cleared registry — the io/conftest.py snapshot covers restoration."""
    _clear_registry()
    yield


def test_empty_registry_after_clear() -> None:
    assert list_adapters() == []


def test_register_then_lookup() -> None:
    adapter = FakeCsvAdapter()
    register_adapter(adapter)
    assert list_adapters() == ["csv"]
    assert get_adapter("csv") is adapter


def test_re_register_overwrites_same_name() -> None:
    a1 = FakeCsvAdapter()
    a2 = FakeCsvAdapter()
    register_adapter(a1)
    register_adapter(a2)
    assert list_adapters() == ["csv"]
    assert get_adapter("csv") is a2


def test_get_unknown_adapter_raises() -> None:
    with pytest.raises(AdapterNotAvailableError):
        get_adapter("missing")


def test_registration_preserves_order() -> None:
    register_adapter(FakeCsvAdapter())
    register_adapter(FakeParquetAdapter())
    assert list_adapters() == ["csv", "parquet"]


def test_register_non_adapter_raises_structured_error() -> None:
    """An object that doesn't satisfy the protocol gets a categorical error."""
    with pytest.raises(InvalidAdapterError, match="FormatAdapter protocol"):
        register_adapter(object())  # type: ignore[arg-type]


def test_register_adapter_with_empty_name_raises_structured_error() -> None:
    """Empty name is rejected up front rather than producing a silent registry hole."""

    class _Nameless:
        name = ""
        extensions: tuple[str, ...] = ()
        schemes: tuple[str, ...] = ()

        def probe(self, source):
            return False

        def read(self, source, engine, schema=None, **kw):
            return None

        def write(self, expr, dest, engine, **kw):
            return None

        def scan(self, source, engine):
            return []

    with pytest.raises(InvalidAdapterError, match="non-empty"):
        register_adapter(_Nameless())


def test_invalid_adapter_error_is_format_error_subclass() -> None:
    """Callers catching FormatError see InvalidAdapterError too."""
    with pytest.raises(FormatError):
        register_adapter(object())  # type: ignore[arg-type]


def test_reset_registry_restores_the_stock_adapters() -> None:
    """``_reset_registry`` undoes any clear/register a test did: same adapters, same bindings."""
    from corral.io import _reset_registry, dispatch

    register_adapter(FakeCsvAdapter())
    _reset_registry()

    assert list_adapters() == ["csv", "duckdb", "parquet", "remote", "zipcsv"]
    assert type(dispatch("foo.csv")).__name__ == "CsvAdapter"
    assert dispatch("foo.csv.zip").name == "zipcsv"
    assert dispatch("s3://bucket/foo.parquet").name == "remote"
    assert dispatch("duckdb://foo.duckdb").name == "duckdb"
