"""Tests for the ResourceRef pydantic model and ResourceListing alias."""

from __future__ import annotations

import pytest
from corral.io import ResourceListing, ResourceRef


def test_resource_ref_round_trip() -> None:
    ref = ResourceRef(name="link", path="net/link.parquet", format="parquet")
    payload = ref.model_dump()
    assert payload == {
        "name": "link",
        "path": "net/link.parquet",
        "format": "parquet",
        "container_adapter": None,
        "member": None,
        "table_schema": None,
    }
    parsed = ResourceRef.model_validate(payload)
    assert parsed == ref
    assert parsed.container is None


def test_resource_ref_container_member_round_trip() -> None:
    ref = ResourceRef(
        name="link", path="net.zip::link.csv", format="csv", container_adapter="zipcsv", member="link.csv"
    )
    parsed = ResourceRef.model_validate(ref.model_dump())
    assert parsed == ref
    assert parsed.container == "net.zip"


def test_resource_ref_json_round_trip() -> None:
    ref = ResourceRef(name="node", path="s3://bucket/node.csv", format="csv")
    payload = ref.model_dump_json()
    parsed = ResourceRef.model_validate_json(payload)
    assert parsed == ref


def test_resource_ref_is_frozen() -> None:
    ref = ResourceRef(name="link", path="x", format="csv")
    with pytest.raises(Exception):  # noqa: B017 — pydantic ValidationError
        ref.name = "node"  # type: ignore[misc]


def test_resource_listing_is_list_alias() -> None:
    listing: ResourceListing = [
        ResourceRef(name="link", path="link.parquet", format="parquet"),
        ResourceRef(name="node", path="node.parquet", format="parquet"),
    ]
    assert isinstance(listing, list)
    assert len(listing) == 2
    assert all(isinstance(r, ResourceRef) for r in listing)


def test_resource_ref_carries_an_optional_schema() -> None:
    """#117: an adapter that already knows a resource's schema can hand it over; ``schema`` is the alias."""
    from corral.spec.model import Field, Schema

    schema = Schema(fields=[Field(name="link_id", type="integer")])
    ref = ResourceRef(name="link", path="x.zip::link.csv", format="csv", schema=schema)
    assert ref.table_schema == schema
    assert ResourceRef(name="link", path="x", format="csv", table_schema=schema).table_schema == schema

    parsed = ResourceRef.model_validate_json(ref.model_dump_json())
    assert parsed == ref
    assert ref.model_dump(by_alias=True)["schema"]["fields"][0]["name"] == "link_id"


def test_resource_ref_schema_defaults_to_none() -> None:
    assert ResourceRef(name="link", path="x", format="csv").table_schema is None
