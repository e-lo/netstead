"""Structural conformance tests for the Engine protocol.

The protocol grew to include per-format primitives (``read_csv`` /
``read_parquet`` / ``read_duckdb_table`` / ``from_records`` plus the
matching ``write_*``) and a ``cast_schema`` helper as part of the
engine/adapter inversion (issue #134). These tests pin the full
structural surface so any future divergence between Engine subclasses
and the Protocol fails loudly.
"""

from __future__ import annotations

from typing import Any

from corral.engines import Engine


class FakeEngine:
    """Minimal in-test engine satisfying the full Engine protocol.

    Includes every read / write primitive plus ``cast_schema`` so the
    runtime structural check passes. ``scan`` and ``write`` are the
    convenience delegators — for a fake they can be no-ops.
    """

    name: str = "fake"

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


class IncompleteEngine:
    """Missing the read primitives — should NOT be recognised as an Engine."""

    name: str = "incomplete"

    def materialize(self, expr):
        return None

    def to_pandas(self, expr):
        return None

    def to_polars(self, expr):
        return None

    def write(self, expr, dest, fmt: str, **kwargs: Any) -> None:
        return None


def test_fake_engine_satisfies_protocol():
    assert isinstance(FakeEngine(), Engine)


def test_engine_missing_primitives_fails_protocol_check():
    assert not isinstance(IncompleteEngine(), Engine)


def test_engine_protocol_runtime_checkable_class_object():
    # Defensive sanity: re-confirm instance check after mutation in other tests.
    assert isinstance(FakeEngine(), Engine)


def test_engine_without_to_arrow_fails_protocol_check():
    """#116: ``to_arrow`` is part of the protocol, so an engine lacking it is not an Engine."""
    no_arrow = type("NoArrowEngine", (FakeEngine,), {"to_arrow": None})()
    assert not isinstance(no_arrow, Engine)
