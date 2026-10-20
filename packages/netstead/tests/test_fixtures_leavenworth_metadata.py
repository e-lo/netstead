"""The bundled-fixture metadata must describe the files that actually ship.

``netstead.fixtures.leavenworth.summary()`` and the module docstring are
what a new user sees first (``print(leavenworth.summary())`` is the
documented way to learn what is in the fixture). They are maintained by
hand, so they drift when the fixture is regenerated: the 2026-10 docs
pass found the module claiming 75 nodes / 214 links while the CSVs held
121 / 339. This test pins the hand-written table to the real row counts.
"""

from __future__ import annotations

from netstead.fixtures import leavenworth


def _actual_row_counts() -> dict[str, int]:
    net = leavenworth.load()
    return {name: table.count() for name, table in net.items()}


def test_fixture_info_table_counts_match_the_bundled_csvs() -> None:
    assert leavenworth._FIXTURE_INFO["tables"] == _actual_row_counts()


def test_summary_reports_the_real_link_and_node_counts() -> None:
    text = leavenworth.summary()
    assert "link                    339 rows" in text
    assert "node                    121 rows" in text


def test_module_docstring_matches_the_bundled_csvs() -> None:
    doc = leavenworth.__doc__ or ""
    assert "121 nodes, 339 links" in doc
    assert "214 links" not in doc
