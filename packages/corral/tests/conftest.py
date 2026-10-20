"""Shared fixtures for the corral test suite."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _stock_adapter_registry():
    """Run every test against the stock IO adapter registry, and put it back afterwards.

    Tests are free to ``_clear_registry()`` and register fakes in their body
    or their own fixtures; this is the one place that restores the five stock
    adapters, so no test depends on what an earlier one left behind.
    """
    from corral.io import _reset_registry

    _reset_registry()
    yield
    _reset_registry()
