"""Round-trip + edge-case tests for the spec Pydantic models."""

from __future__ import annotations

import warnings

import pytest
from corral.spec import (
    Constraints,
    DataPackage,
    Field,
    ForeignKey,
    ForeignKeyReference,
    Resource,
    Schema,
    SharedCategory,
    load_package,
)


def _round_trip(model):
    """Dump to JSON and re-parse, returning the new instance."""
    cls = type(model)
    return cls.model_validate_json(model.model_dump_json(by_alias=True))


def test_constraints_round_trip():
    c = Constraints(
        required=True,
        unique=False,
        minimum=0,
        maximum=100,
        min_length=1,
        max_length=10,
        pattern=r"^[a-z]+$",
        enum=["a", "b"],
    )
    assert _round_trip(c) == c


def test_field_round_trip():
    f = Field(
        name="link_id",
        type="any",
        description="Primary key",
        constraints=Constraints(required=True),
    )
    assert _round_trip(f) == f


def test_schema_round_trip_full():
    s = Schema(
        fields=[
            Field(name="id", type="integer", constraints=Constraints(required=True)),
            Field(name="ref", type="any"),
        ],
        primary_key="id",
        foreign_keys=[
            ForeignKey(fields="ref", reference=ForeignKeyReference(resource="other", fields="id")),
        ],
        missing_values=["", "NaN"],
    )
    assert _round_trip(s) == s


def test_resource_round_trip_with_inline_schema():
    r = Resource(
        name="link",
        path="link.csv",
        table_schema=Schema(fields=[Field(name="id", type="integer")], primary_key="id"),
        type="table",
    )
    assert _round_trip(r) == r


def test_data_package_round_trip():
    pkg = DataPackage(
        name="example",
        title="Example",
        version="0.1.0",
        resources=[
            Resource(name="link", path="link.csv", table_schema=Schema(fields=[Field(name="id", type="integer")])),
        ],
    )
    assert _round_trip(pkg) == pkg


def test_shared_category_round_trip():
    sc = SharedCategory(categories=["a", "b", "c"], description="abc", source="")
    assert _round_trip(sc) == sc


def test_foreign_key_self_reference():
    """A foreign key with reference.resource == '' is a self-reference and must round-trip."""
    fk = ForeignKey(
        fields="parent_link_id",
        reference=ForeignKeyReference(resource="", fields="link_id"),
    )
    assert fk.reference.resource == ""
    assert _round_trip(fk) == fk


def test_field_with_explicit_empty_enum():
    """An explicitly empty enum (``enum: []``) must be preserved, not coerced to None."""
    c = Constraints(enum=[])
    rt = _round_trip(c)
    assert rt.enum == []
    assert rt.enum is not None


def test_schema_without_primary_key():
    """primaryKey is optional; absence must produce a Schema with primary_key is None."""
    s = Schema(fields=[Field(name="anything", type="string")])
    assert s.primary_key is None
    rt = _round_trip(s)
    assert rt.primary_key is None
    assert rt == s


def test_camel_case_aliases_accepted():
    """The on-disk JSON keys are camelCase; verify both wire- and python-name input works."""
    s_wire = Schema.model_validate(
        {
            "fields": [{"name": "id", "type": "integer"}],
            "primaryKey": "id",
            "foreignKeys": [
                {"fields": "id", "reference": {"resource": "other", "fields": "id"}},
            ],
            "missingValues": ["NaN"],
        }
    )
    assert s_wire.primary_key == "id"
    assert len(s_wire.foreign_keys) == 1
    assert s_wire.missing_values == ["NaN"]


def test_extra_properties_preserved_via_extra_allow():
    """Unknown Frictionless properties are kept in __pydantic_extra__, not raised."""
    f = Field.model_validate({"name": "x", "type": "integer", "vendorExtra": {"hello": "world"}})
    assert f.name == "x"
    extra = f.__pydantic_extra__ or {}
    assert extra.get("vendorExtra") == {"hello": "world"}


# ---------------------------------------------------------------------------
# Resource.schema vs Resource.table_schema (I8 — rename footgun)
# ---------------------------------------------------------------------------


def test_resource_schema_attribute_access_emits_future_warning():
    """Reading ``.schema`` on a Resource is almost always a rename mistake.

    The on-disk JSON key is ``"schema"`` but the Python attribute is
    ``table_schema``. Without a loud signal, ``r.schema`` silently
    returns the deprecated ``BaseModel.schema`` bound method with no
    exception and no warning. Verify the override fires.
    """
    r = Resource(name="link", path="link.csv", schema=Schema(fields=[]))
    with pytest.warns(FutureWarning, match="table_schema"):
        _ = r.schema


def test_resource_table_schema_access_is_silent():
    """The correct attribute name must NOT trigger any warning."""
    r = Resource(name="link", path="link.csv", schema=Schema(fields=[]))
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # any warning becomes a test failure
        ts = r.table_schema
    assert ts is not None


def test_resource_json_round_trip_uses_schema_alias():
    """Round-tripping to JSON must produce the ``"schema"`` key (Frictionless)."""
    r = Resource(name="link", path="link.csv", schema=Schema(fields=[]))
    dumped = r.model_dump(by_alias=True)
    assert "schema" in dumped
    assert "table_schema" not in dumped


def test_missing_values_defaults_to_frictionless_empty_string():
    """Absent ``missingValues`` means Frictionless's default ``[""]``, not ``None`` (#119)."""
    s = Schema.model_validate({"fields": [{"name": "id", "type": "integer"}]})
    assert s.missing_values == [""]


def test_missing_values_explicit_empty_list_means_no_nulls():
    """``missingValues: []`` is distinct from absent: no string is read as null (#119)."""
    s = Schema.model_validate({"fields": [], "missingValues": []})
    assert s.missing_values == []


def test_missing_values_default_is_not_shared_between_instances():
    a, b = Schema(fields=[]), Schema(fields=[])
    a.missing_values.append("NA")
    assert b.missing_values == [""]


@pytest.mark.parametrize("path", ["link.csv", ["link-1.csv", "link-2.csv"]])
def test_resource_path_loads_as_string_or_list(path):
    """Frictionless allows a list of paths; the loader carries either form through (#124)."""
    pkg = load_package({"resources": [{"name": "link", "path": path, "schema": {"fields": []}}]})
    assert pkg.resources[0].path == path


def test_collection_defaults_are_iterable_without_none_check():
    """``foreign_keys`` and ``missing_values`` both default to lists (#120)."""
    s = Schema(fields=[])
    assert isinstance(s.foreign_keys, list)
    assert isinstance(s.missing_values, list)
