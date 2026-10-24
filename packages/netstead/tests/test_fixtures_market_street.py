"""The bundled Market Street (Overture) fixture: metadata matches the files and the GERS ids are real."""

from __future__ import annotations

import json
import zipfile

import pytest
from netstead.fixtures import market_street


def _counts(fmt: str) -> dict[str, int]:
    return {name: table.count() for name, table in market_street.load(fmt).items()}


@pytest.mark.parametrize("fmt", ["csv", "parquet", "zip"])
def test_every_form_matches_the_metadata(fmt: str) -> None:
    assert _counts(fmt) == market_street._FIXTURE_INFO["tables"]


def test_summary_and_docstring_report_the_real_counts() -> None:
    assert "link                    186 rows" in market_street.summary()
    assert "129 nodes, 186 links" in (market_street.__doc__ or "")


def test_gers_columns_are_present_and_non_null() -> None:
    net = market_street.load("parquet")
    links, nodes = net.links.to_pandas(), net.nodes.to_pandas()
    for col in ("overture_segment_id", "overture_connector_ids"):
        assert links[col].notna().all() and (links[col] != "").all()
    assert nodes["overture_connector_id"].notna().all()


def test_connector_ids_are_unique_per_node_and_segment_ids_repeat_across_links() -> None:
    net = market_street.load("parquet")
    links, nodes = net.links.to_pandas(), net.nodes.to_pandas()
    assert nodes["overture_connector_id"].is_unique
    assert links["overture_segment_id"].nunique() < len(links)  # sub-segments and directions share one id
    node_ids = set(nodes["overture_connector_id"])
    assert {c for pair in links["overture_connector_ids"] for c in pair.split(",")} <= node_ids


def test_the_intersection_streets_and_one_way_links_are_present() -> None:
    links = market_street.load("parquet").links.to_pandas()
    assert {"Market Street", "Kearny Street", "Geary Street", "3rd Street"} <= set(links["name"].dropna())
    # a one-way segment yields one directed link, so some segments appear once
    assert (links.groupby("overture_segment_id").size() == 1).any()


def test_datapackage_and_zip_ship_with_the_fixture() -> None:
    assert json.loads(market_street.spec_path().read_text())["version"] == "0.97"
    with zipfile.ZipFile(market_street.zip_path()) as z:
        assert {"node.csv", "link.csv"} <= {n.rsplit("/", 1)[-1] for n in z.namelist()}


def test_unknown_format_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown format"):
        market_street.load("duckdb")
