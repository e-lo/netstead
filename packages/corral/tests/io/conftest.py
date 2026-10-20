"""Fixtures for the io/ tests.

These are the tests that clear and re-populate the module-level adapter
registry. The fixture below is the one place that puts the stock adapters
back, so tests elsewhere always see the registry ``import corral.io`` built.

(It lives here rather than in ``tests/conftest.py``: under
``--import-mode=importlib`` that file would be the module ``tests.conftest``,
the same name as ``packages/netstead/tests/conftest.py``, and pytest refuses
to register both.)
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _stock_adapter_registry():
    """Run every io/ test against the stock adapter registry, and put it back afterwards.

    Tests are free to ``_clear_registry()`` and register fakes in their body
    or their own fixtures.
    """
    from corral.io import _reset_registry

    _reset_registry()
    yield
    _reset_registry()
