"""The ``netstead`` CLI must start on an install without the optional extras.

The docs promise that ``netstead info`` / ``netstead validate`` /
``netstead doctor`` run on the bare install and that each optional extra
is only needed by the commands that use it. A fresh
``netstead[clean,graph]`` install on 2026-10-19 contradicted that: the
CLI entry point crashed at import with ``ModuleNotFoundError: jsonschema``
because ``netstead.select.emit`` imported it at module level and the CLI
imports the ``select`` command group eagerly. ``jsonschema`` belongs to
the ``[nl]`` extra.

The test simulates the missing module and checks that the CLI still
imports, and that the selection helper that needs it fails with the
project's standard "requires the [nl] extra" message only when called.
"""

from __future__ import annotations

import importlib
import sys

import pytest


@pytest.fixture
def without_jsonschema(monkeypatch: pytest.MonkeyPatch):
    """Make ``import jsonschema`` raise and drop cached netstead modules that may have imported it."""
    monkeypatch.setitem(sys.modules, "jsonschema", None)
    for name in [m for m in sys.modules if m == "netstead.cli" or m.startswith(("netstead.cli.", "netstead.select"))]:
        monkeypatch.delitem(sys.modules, name, raising=False)
    yield
    for name in [m for m in sys.modules if m == "netstead.cli" or m.startswith(("netstead.cli.", "netstead.select"))]:
        sys.modules.pop(name, None)


def test_cli_app_imports_when_jsonschema_is_missing(without_jsonschema) -> None:
    module = importlib.import_module("netstead.cli.app")
    assert module.app is not None


def test_validate_fragment_names_the_nl_extra_when_jsonschema_is_missing(without_jsonschema) -> None:
    emit = importlib.import_module("netstead.select.emit")
    with pytest.raises(ImportError, match=r"\[nl\] extra"):
        emit.validate_fragment({"spec_version": "0.97", "links": []})
