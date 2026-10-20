"""Tests for the dispatcher resolution chain."""

from __future__ import annotations

import pytest
from corral.io import (
    AdapterNotAvailableError,
    FormatNotDetected,
    _clear_registry,
    dispatch,
    register_adapter,
)

from ._fakes import (
    FakeCsvAdapter,
    FakeDuckdbAdapter,
    FakeParquetAdapter,
    FakeZipCsvAdapter,
    FuzzyAdapter,
)


@pytest.fixture()
def populated_registry():
    _clear_registry()
    csv = FakeCsvAdapter()
    parquet = FakeParquetAdapter()
    duckdb = FakeDuckdbAdapter()
    zipcsv = FakeZipCsvAdapter()
    # Order matters: register the compound (zipcsv) before the simple (csv)
    # so we also verify the dispatcher honours longest-extension precedence
    # rather than registration order alone.
    register_adapter(csv)
    register_adapter(parquet)
    register_adapter(duckdb)
    register_adapter(zipcsv)
    yield {"csv": csv, "parquet": parquet, "duckdb": duckdb, "zipcsv": zipcsv}
    _clear_registry()


def test_dispatch_csv_extension(populated_registry) -> None:
    assert dispatch("foo.csv") is populated_registry["csv"]


def test_dispatch_parquet_extension(populated_registry) -> None:
    assert dispatch("foo.parquet") is populated_registry["parquet"]


def test_dispatch_compound_csv_zip_wins_over_zip(populated_registry) -> None:
    assert dispatch("foo.csv.zip") is populated_registry["zipcsv"]


def test_dispatch_duckdb_by_extension(populated_registry) -> None:
    assert dispatch("foo.duckdb") is populated_registry["duckdb"]


def test_dispatch_duckdb_by_scheme(populated_registry) -> None:
    assert dispatch("duckdb://foo.duckdb") is populated_registry["duckdb"]


def test_explicit_format_overrides_extension(populated_registry) -> None:
    assert dispatch("foo.csv", format="parquet") is populated_registry["parquet"]


def test_explicit_format_unknown_raises(populated_registry) -> None:
    with pytest.raises(AdapterNotAvailableError):
        dispatch("foo.parquet", format="excel")


def test_unknown_extension_no_probe_match_raises(populated_registry) -> None:
    with pytest.raises(FormatNotDetected) as excinfo:
        dispatch("foo.unknown")
    msg = str(excinfo.value)
    # The error message should list registered adapters so the user can
    # see what's installed.
    for name in ("csv", "parquet", "duckdb", "zipcsv"):
        assert name in msg


def test_probe_chain_resolves_when_extension_misses() -> None:
    _clear_registry()
    register_adapter(FakeCsvAdapter())
    register_adapter(FakeParquetAdapter())
    fuzzy = FuzzyAdapter()
    register_adapter(fuzzy)
    try:
        # No extension/scheme match — falls through to probe chain.
        assert dispatch("magic://hello") is fuzzy
    finally:
        _clear_registry()


def test_probe_chain_skips_when_extension_matches() -> None:
    _clear_registry()
    csv = FakeCsvAdapter()
    fuzzy = FuzzyAdapter()
    register_adapter(csv)
    register_adapter(fuzzy)
    try:
        # Extension match wins even though FuzzyAdapter would also accept
        # any string starting with magic://.
        assert dispatch("foo.csv") is csv
    finally:
        _clear_registry()


def test_dispatch_accepts_pathlike(populated_registry) -> None:
    from pathlib import Path

    assert dispatch(Path("foo.csv")) is populated_registry["csv"]


def test_probe_exception_treated_as_non_match() -> None:
    """A misbehaving adapter probe must not crash dispatch."""
    _clear_registry()

    class ExplodingAdapter(FuzzyAdapter):
        name = "boom"

        def probe(self, source):
            raise RuntimeError("kaboom")

    register_adapter(ExplodingAdapter())
    register_adapter(FuzzyAdapter())
    try:
        # ExplodingAdapter raises; FuzzyAdapter still resolves.
        assert dispatch("magic://x").name == "fuzzy"
    finally:
        _clear_registry()


def test_probe_exception_is_logged_at_debug(caplog) -> None:
    """A probe that raises is skipped, but the failure is visible at DEBUG for whoever debugs the adapter."""
    import logging

    _clear_registry()

    class ExplodingAdapter(FuzzyAdapter):
        name = "boom"

        def probe(self, source):
            raise RuntimeError("kaboom")

    register_adapter(ExplodingAdapter())
    register_adapter(FuzzyAdapter())

    with caplog.at_level(logging.DEBUG, logger="corral.io"):
        assert dispatch("magic://x").name == "fuzzy"

    [record] = [r for r in caplog.records if "boom" in r.getMessage()]
    assert record.levelno == logging.DEBUG
    assert "kaboom" in record.getMessage()


def test_probe_exception_is_silent_above_debug(caplog) -> None:
    import logging

    _clear_registry()

    class ExplodingAdapter(FuzzyAdapter):
        name = "boom"

        def probe(self, source):
            raise RuntimeError("kaboom")

    register_adapter(ExplodingAdapter())
    register_adapter(FuzzyAdapter())

    with caplog.at_level(logging.INFO, logger="corral.io"):
        dispatch("magic://x")

    assert caplog.records == []


def test_probe_exception_log_hides_url_userinfo(caplog) -> None:
    """A probe error that echoes a ``user:secret@`` URL must not put the secret in the log."""
    import logging

    _clear_registry()

    class LeakyAdapter(FuzzyAdapter):
        name = "leaky"

        def probe(self, source):
            raise ValueError(f"cannot open {source}")

    register_adapter(LeakyAdapter())
    register_adapter(FuzzyAdapter())

    with caplog.at_level(logging.DEBUG, logger="corral.io"):
        dispatch("magic://alice:hunter2@host/x")

    assert "hunter2" not in caplog.text
    assert "magic://***@host/x" in caplog.text


@pytest.mark.parametrize(
    ("text", "secrets", "kept"),
    [
        ("cannot open s3://AK:se@cret@bucket/x", ["se@cret", "cret@", "AK:"], "s3://***@bucket/x"),
        ("cannot open ftp://u:pa/ss@host/x", ["pa/ss", "u:pa"], "ftp://***@host/x"),
        (
            "cannot open https://h.example/f.csv?X-Amz-Signature=abc123&X-Amz-Credential=AKIA",
            ["abc123", "AKIA", "X-Amz-Signature="],
            "https://h.example/f.csv?***",
        ),
        ("plain error, no url", [], "plain error, no url"),
    ],
)
def test_probe_log_redaction(text, secrets, kept) -> None:
    """Userinfo is masked up to the *last* ``@`` (a password may hold ``@`` or ``/``); query strings are dropped."""
    from corral.io import _redact_urls

    redacted = _redact_urls(text)
    for secret in secrets:
        assert secret not in redacted
    assert kept in redacted


def test_probe_exception_log_drops_a_presigned_query(caplog) -> None:
    import logging

    _clear_registry()

    class LeakyAdapter(FuzzyAdapter):
        name = "leaky"

        def probe(self, source):
            raise ValueError(f"cannot open {source}")

    register_adapter(LeakyAdapter())
    register_adapter(FuzzyAdapter())

    with caplog.at_level(logging.DEBUG, logger="corral.io"):
        dispatch("magic://host/x?X-Amz-Signature=abc123")

    assert "abc123" not in caplog.text
