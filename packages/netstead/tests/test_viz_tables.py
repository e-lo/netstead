"""Unit tests for netstead.viz.tables — paged/sorted/filtered GMNS table access."""

from importlib import resources

import pandas as pd
import pyarrow as pa
import pytest
from corral.engines.ibis_engine import IbisEngine
from netstead.viz.tables import (
    FILTER_OPS,
    MAX_LIMIT,
    FilterError,
    distinct_values,
    locate_row,
    page_table,
    primary_key,
    table_schema,
)


@pytest.fixture
def df():
    return pd.DataFrame(
        {
            "link_id": [1, 2, 3, 4],
            "lanes": [2, 4, 4, None],
            "facility_type": ["motorway", "primary", "motorway", "service"],
            "name": ["I 40", "Page Road", "I 40", None],
            "geometry": ["LINESTRING(0 0,1 1)"] * 4,
        }
    )


def test_primary_key_prefers_table_name():
    assert primary_key("link", ["link_id", "from_node_id"]) == "link_id"
    assert primary_key("segment", ["from_node_id", "x"]) == "from_node_id"  # first *_id fallback
    assert primary_key("zone", ["a", "b"]) is None


def test_schema_classifies_kinds(df):
    kinds = {c["name"]: c["kind"] for c in table_schema("link", df)["columns"]}
    assert kinds["lanes"] == "num" and kinds["facility_type"] == "str" and kinds["geometry"] == "geom"


def test_page_excludes_geometry_and_pages(df):
    p = page_table(df, offset=1, limit=2)
    assert "geometry" not in p["columns"]
    assert len(p["rows"]) == 2 and p["total"] == 4 and p["offset"] == 1


def test_sort_desc(df):
    p = page_table(df, sort="link_id", direction="desc")
    assert [r[p["columns"].index("link_id")] for r in p["rows"]] == [4, 3, 2, 1]


def test_filter_eq_and_gte(df):
    p = page_table(
        df,
        filter_spec=[{"col": "facility_type", "op": "eq", "val": "motorway"}, {"col": "lanes", "op": "gte", "val": 3}],
    )
    assert p["total"] == 1 and p["rows"][0][p["columns"].index("link_id")] == 3


def test_filter_contains_and_isnull(df):
    assert page_table(df, filter_spec=[{"col": "name", "op": "contains", "val": "page"}])["total"] == 1
    assert page_table(df, filter_spec=[{"col": "name", "op": "notnull"}])["total"] == 3


def test_filter_in(df):
    p = page_table(df, filter_spec=[{"col": "link_id", "op": "in", "val": [1, 3]}])
    assert p["total"] == 2


def test_ids_crossfilter(df):
    assert page_table(df, ids=[2, 4], pk="link_id")["total"] == 2


def test_unknown_column_and_op_raise(df):
    with pytest.raises(FilterError):
        page_table(df, filter_spec=[{"col": "nope", "op": "eq", "val": 1}])
    with pytest.raises(FilterError):
        page_table(df, filter_spec=[{"col": "lanes", "op": "bogus", "val": 1}])
    with pytest.raises(FilterError):
        page_table(df, sort="nope")


def test_limit_clamped(df):
    assert page_table(df, limit=99999)["limit"] == MAX_LIMIT


def test_null_scalars_serialize(df):
    p = page_table(df, sort="link_id")
    assert p["rows"][3][p["columns"].index("lanes")] is None  # NaN -> None, JSON-safe


# --- lazy corral Table path (G1): engine push-down, pandas-parity ---


def _ibis_link_table():
    """A lazy corral Table over the fixture links, on the ibis/duckdb engine."""
    from corral.dataset import Table

    base = resources.files("netstead.fixtures.rdu_i40").joinpath("parquet")
    df = pd.read_parquet(base.joinpath("link.parquet"))
    e = IbisEngine()
    return Table(name="link", expr=e.from_arrow(pa.Table.from_pandas(df, preserve_index=False)), engine=e), df


def test_page_table_lazy_matches_pandas():
    t, df = _ibis_link_table()
    spec = [{"col": "facility_type", "op": "eq", "val": "motorway"}]
    lazy = page_table(t, limit=5, sort="lanes", direction="desc", filter_spec=spec)
    eager = page_table(df, limit=5, sort="lanes", direction="desc", filter_spec=spec)
    assert lazy["total"] == eager["total"] > 0
    assert "geometry" not in lazy["columns"]  # WKT dropped before materializing
    li = lazy["columns"].index("lanes")
    lv = [r[li] for r in lazy["rows"] if r[li] is not None]
    assert lv == sorted(lv, reverse=True)


def test_page_table_lazy_ids_crossfilter():
    t, df = _ibis_link_table()
    ids = [int(i) for i in df["link_id"].iloc[:3]]
    assert page_table(t, ids=ids, pk="link_id", limit=500)["total"] == 3


def test_page_table_lazy_bad_filter_raises():
    t, _ = _ibis_link_table()
    with pytest.raises(FilterError):
        page_table(t, filter_spec=[{"col": "nonsuch", "op": "eq", "val": 1}])


def test_table_schema_on_lazy_table():
    t, _ = _ibis_link_table()
    sch = table_schema("link", t)
    kinds = {c["name"]: c["kind"] for c in sch["columns"]}
    assert kinds["lanes"] == "num" and kinds["facility_type"] == "str"
    assert sch["primary_key"] == "link_id" and sch["rows"] > 50


def test_locate_row_follows_filter_and_sort():
    df = pd.DataFrame({"link_id": [5, 3, 9, 1], "name": ["a", "b", "a", "c"]})
    assert locate_row(df, 9, pk="link_id") == 2
    assert locate_row(df, 9, pk="link_id", sort="link_id", direction="desc") == 0
    assert locate_row(df, 9, pk="link_id", filter_spec=[{"col": "name", "op": "eq", "val": "a"}]) == 1
    assert locate_row(df, 3, pk="link_id", filter_spec=[{"col": "name", "op": "eq", "val": "a"}]) is None
    assert locate_row(df, 1, pk="link_id", ids=[1, 9]) == 1  # ids keep table order: 9, then 1
    assert locate_row(df, 9, pk="link_id", ids=[]) is None  # an explicit empty id list holds no rows


def test_locate_row_is_none_for_lazy_tables_and_missing_keys(tmp_path):
    df = pd.DataFrame({"link_id": [1, 2]})
    assert locate_row(df, 1, pk="nope") is None
    path = tmp_path / "t.parquet"
    df.to_parquet(path)
    engine = IbisEngine()
    from corral.dataset.table import Table

    assert locate_row(Table(name="link", expr=engine.scan(path), engine=engine), 1, pk="link_id") is None
    engine.close()


# --- token and range operators (beta feedback: comma-separated categoricals such as allowed_uses) ---


@pytest.fixture
def uses():
    return pd.DataFrame({"id": [0, 1, 2, 3], "allowed_uses": ["auto", "auto,truck", "walk", None]})


def _ids(src, spec):
    p = page_table(src, filter_spec=spec, sort="id")
    return [r[p["columns"].index("id")] for r in p["rows"]]


def test_any_of_matches_a_token_in_a_comma_list(uses):
    assert _ids(uses, [{"col": "allowed_uses", "op": "any_of", "val": ["auto"]}]) == [0, 1]
    assert _ids(uses, [{"col": "allowed_uses", "op": "any_of", "val": "auto"}]) == [0, 1]  # a bare string is one token


def test_all_of_needs_every_token(uses):
    assert _ids(uses, [{"col": "allowed_uses", "op": "all_of", "val": ["auto", "truck"]}]) == [1]


def test_none_of_keeps_nulls(uses):
    assert _ids(uses, [{"col": "allowed_uses", "op": "none_of", "val": ["auto"]}]) == [2, 3]


def test_tokens_are_stripped_and_a_substring_is_not_a_token():
    df = pd.DataFrame({"id": [0, 1], "u": ["auto , truck", "automobile"]})
    assert _ids(df, [{"col": "u", "op": "any_of", "val": ["truck"]}]) == [0]
    assert _ids(df, [{"col": "u", "op": "any_of", "val": ["auto"]}]) == [0]


def test_between_is_inclusive():
    df = pd.DataFrame({"id": [0, 1, 2, 3], "speed": [30, 40, 50, None]})
    assert _ids(df, [{"col": "speed", "op": "between", "val": [35, 45]}]) == [1]
    assert _ids(df, [{"col": "speed", "op": "between", "val": [40, 50]}]) == [1, 2]


def test_between_needs_a_pair():
    df = pd.DataFrame({"id": [0], "speed": [30]})
    with pytest.raises(FilterError):
        page_table(df, filter_spec=[{"col": "speed", "op": "between", "val": [35]}])
    with pytest.raises(FilterError):
        page_table(df, filter_spec=[{"col": "speed", "op": "between", "val": 35}])


def test_new_operators_are_in_filter_ops():
    assert {"any_of", "all_of", "none_of", "between"} <= set(FILTER_OPS)


def test_token_and_range_ops_on_a_lazy_table_match_pandas(uses):
    from corral.dataset import Table

    df = uses.assign(speed=[30, 40, 50, None])
    e = IbisEngine()
    t = Table(name="t", expr=e.from_arrow(pa.Table.from_pandas(df, preserve_index=False)), engine=e)
    for spec in (
        [{"col": "allowed_uses", "op": "any_of", "val": ["auto"]}],
        [{"col": "allowed_uses", "op": "all_of", "val": ["auto", "truck"]}],
        [{"col": "allowed_uses", "op": "none_of", "val": ["auto"]}],
        [{"col": "speed", "op": "between", "val": [35, 50]}],
    ):
        assert _ids(t, spec) == _ids(df, spec), spec
    with pytest.raises(FilterError):
        page_table(t, filter_spec=[{"col": "speed", "op": "between", "val": [35]}])


# --- distinct values (the column-filter popover's value list) ---


def test_distinct_values_splits_comma_lists():
    out = distinct_values(pd.DataFrame({"u": ["auto", "auto,truck", "walk", None]}), "u")
    assert out == {"values": ["auto", "truck", "walk"], "truncated": False, "list_like": True}


def test_distinct_values_plain_text_and_numbers():
    out = distinct_values(pd.DataFrame({"a": ["a", "b", "a"]}), "a")
    assert out == {"values": ["a", "b"], "truncated": False, "list_like": False}
    assert distinct_values(pd.DataFrame({"n": [3, 1, 1, None]}), "n")["values"] == ["1", "3"]


def test_distinct_values_truncates():
    out = distinct_values(pd.DataFrame({"a": ["a", "b", "a"]}), "a", limit=1)
    assert out["values"] == ["a"] and out["truncated"] is True


def test_distinct_values_unknown_column():
    with pytest.raises(FilterError):
        distinct_values(pd.DataFrame({"a": [1]}), "nope")


def test_distinct_values_on_a_lazy_table():
    t, df = _ibis_link_table()
    lazy, eager = distinct_values(t, "facility_type"), distinct_values(df, "facility_type")
    assert lazy == eager and "motorway" in lazy["values"]


def test_schema_enum_is_none_without_a_spec(df):
    assert all(c["enum"] is None for c in table_schema("link", df)["columns"])
