"""Tests for the build estimate: pre-query counts (faked HTTP / local fixture) and the cost model."""

import os
import time
from pathlib import Path

import pytest
from corral.engines.ibis_engine import IbisEngine
from netstead.workbench.area import BboxArea, PlaceArea
from netstead.workbench.estimate import (
    Estimate,
    count_osm,
    count_overture,
    estimate_build,
    fit_s_per_link,
    load_coefficients,
    needs_approval,
)

OVERTURE_DIR = str(Path(__file__).resolve().parent / "fixtures" / "overture")


class _Resp:
    def __init__(self, payload, status_code=200):
        self.status_code, self._payload = status_code, payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise OSError(f"HTTP {self.status_code}")


class _Http:
    def __init__(self, payload):
        self.payload, self.posts = payload, []

    def post(self, url, data=None, headers=None, timeout=None):
        self.posts.append(data)
        return _Resp(self.payload)


_COUNT = {"elements": [{"type": "count", "id": 0, "tags": {"nodes": "0", "ways": "1234", "total": "1234"}}]}


def test_coefficients_cover_every_source_and_format():
    coeffs = load_coefficients()
    assert set(coeffs["sources"]) == {"osm", "overture", "osm_file", "overture_file"}
    for model in coeffs["sources"].values():
        assert {"latency_s", "links_per_element", "s_per_link"} <= set(model)
    for table in ("bytes_per_link", "fixed_bytes"):
        assert set(coeffs["output"][table]) == {"parquet", "csv", "duckdb", "zip"}


def test_calibrated_osm_model_reproduces_the_rdu_core_run():
    """Calibrated 2026-10-02 (see build_cost.toml's CALIBRATION NOTE): the L-rung run (Raleigh-
    Durham core bbox, 303,236 links) took ~52.6 s fetch+convert+write; the fitted model should land
    within the documented high-variance tolerance (Overpass fetch time varied +/-20s run to run)."""
    osm = load_coefficients()["sources"]["osm"]
    predicted = osm["latency_s"] + 303_236 * osm["s_per_link"]
    assert predicted == pytest.approx(52.6, rel=0.3)


def test_estimate_build_is_linear():
    coeffs = {
        "sources": {"osm": {"latency_s": 2.0, "links_per_element": 2.0, "s_per_link": 0.5}},
        "output": {"bytes_per_link": {"csv": 10}, "fixed_bytes": {"csv": 100}},
    }
    est = estimate_build("osm", 3, output_format="csv", coefficients=coeffs)
    assert est == Estimate(seconds=5.0, out_bytes=160, n_elements=3, basis=est.basis)
    assert "3 ways" in est.basis and "~6 links" in est.basis


def test_round_trips_through_dict():
    est = estimate_build("overture", 500)
    assert Estimate(**est.to_dict()) == est


@pytest.mark.parametrize(("seconds", "expected"), [(None, True), (89.0, False), (90.0, False), (90.5, True)], ids=str)
def test_needs_approval(seconds, expected):
    assert needs_approval(Estimate(seconds=seconds, out_bytes=None, n_elements=None, basis="x"), 90.0) is expected


def test_count_osm_uses_out_count_and_polygon():
    http = _Http(_COUNT)
    area = PlaceArea(name="x", bbox=(-79, 35, -78, 36), polygon=[(35.0, -79.0), (35.0, -78.0), (36.0, -78.0)])
    n = count_osm(area, network_type="roads", endpoint="https://overpass.test", http=http, user_agent="t")
    assert n == 1234
    assert http.posts[0].endswith("out count;") and 'poly:"35.0 -79.0 35.0 -78.0 36.0 -78.0"' in http.posts[0]


def test_count_osm_bbox():
    http = _Http(_COUNT)
    count_osm(BboxArea(bbox=(-79, 35, -78, 36)), network_type="roads", endpoint="e", http=http, user_agent="t")
    assert '["highway"~"^(busway|' in http.posts[0] and "(35.0,-79.0,36.0,-78.0);out count;" in http.posts[0]


def test_count_overture_on_local_snapshot():
    engine = IbisEngine()
    try:
        bbox = (-0.5, -0.5, 0.5, 0.5)
        n = count_overture(bbox, network_type="roads", overture_release="x", data_root=OVERTURE_DIR, engine=engine)
    finally:
        engine.close()
    assert n > 0


def test_fit_s_per_link():
    assert fit_s_per_link([(100_000, 15.0), (200_000, 25.0)], latency_s=5.0) == pytest.approx(1e-4)
    with pytest.raises(ValueError, match="links > 0"):
        fit_s_per_link([], latency_s=1.0)


@pytest.mark.skipif(not os.environ.get("NETSTEAD_CALIBRATE"), reason="opt-in live calibration (NETSTEAD_CALIBRATE=1)")
def test_calibrate_osm_live(tmp_path):  # pragma: no cover - live network, run by hand
    """Time real OSM builds for a few bboxes and print a fitted ``s_per_link`` for data/build_cost.toml.

    Run from the repo root::

        NETSTEAD_CALIBRATE=1 uv run --all-extras pytest \
            packages/netstead/tests/test_workbench_estimate.py -k calibrate -s
    """
    from netstead.osm import build_network_from_osm

    bboxes = [(-78.65, 35.77, -78.62, 35.80), (-78.70, 35.74, -78.60, 35.82)]  # downtown Raleigh, small to medium
    latency = load_coefficients()["sources"]["osm"]["latency_s"]
    samples = []
    for bbox in bboxes:
        start = time.perf_counter()
        net = build_network_from_osm(bbox)
        samples.append((int(net.links.count().execute()), time.perf_counter() - start))
    print(f"\nsamples (links, s): {samples}\nfitted s_per_link = {fit_s_per_link(samples, latency_s=latency):.3e}")
