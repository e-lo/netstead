"""``Network.from_source(url, credentials=...)`` forwards the dict to ``Package.from_source``."""

from __future__ import annotations

import netstead.network as network_mod
from netstead import Network
from netstead.fixtures import leavenworth


def test_from_source_forwards_credentials(monkeypatch) -> None:
    captured: dict = {}
    original = network_mod.Package.from_source

    def spy(source, **kwargs):
        captured.update(kwargs)
        return original(source, **kwargs)

    monkeypatch.setattr(network_mod.Package, "from_source", spy)

    net = Network.from_source(leavenworth.csv_dir(), credentials={"key": "k", "secret": "s"})

    assert captured["credentials"] == {"key": "k", "secret": "s"}
    assert "link" in net
