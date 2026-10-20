"""``$ref`` resolution: chained refs resolve, circular refs fail loudly (#122)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from corral.spec import SpecLoadError, load_package, load_schema


def _write(path: Path, doc: object) -> Path:
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_chained_same_document_refs_resolve_fully():
    """A ref whose target is itself a ref is followed, not left as a raw ``$ref``."""
    schema = load_schema(
        {
            "fields": [{"name": "kind", "type": "string", "constraints": {"$ref": "#/defs/a"}}],
            "defs": {"a": {"$ref": "#/defs/b"}, "b": {"enum": ["x", "y"]}},
        }
    )
    constraints = schema.fields[0].constraints
    assert constraints is not None
    assert constraints.enum == ["x", "y"]
    assert "$ref" not in (constraints.__pydantic_extra__ or {})


def test_external_target_refs_resolve_against_their_own_document(tmp_path: Path):
    """A ``#/...`` ref inside an external target points into that target's file."""
    _write(tmp_path / "defs.json", {"kind": {"$ref": "#/values"}, "values": {"enum": ["a", "b"]}})
    schema = _write(
        tmp_path / "t.schema.json",
        {"fields": [{"name": "k", "type": "string", "constraints": {"$ref": "defs.json#/kind"}}]},
    )
    constraints = load_schema(schema).fields[0].constraints
    assert constraints is not None
    assert constraints.enum == ["a", "b"]


def test_sibling_refs_to_the_same_target_are_not_circular():
    schema = load_schema(
        {
            "fields": [
                {"name": "a", "type": "string", "constraints": {"$ref": "#/defs/c"}},
                {"name": "b", "type": "string", "constraints": {"$ref": "#/defs/c"}},
            ],
            "defs": {"c": {"enum": ["x"]}},
        }
    )
    assert [f.constraints.enum for f in schema.fields if f.constraints] == [["x"], ["x"]]


def test_indirect_same_document_cycle_raises_naming_the_ref():
    with pytest.raises(SpecLoadError, match=r"Circular \$ref.*#/defs/a"):
        load_schema(
            {
                "fields": [{"name": "k", "type": "string", "constraints": {"$ref": "#/defs/a"}}],
                "defs": {"a": {"$ref": "#/defs/b"}, "b": {"$ref": "#/defs/a"}},
            }
        )


def test_self_pointing_ref_into_own_file_raises(tmp_path: Path):
    """The issue's case: a field's constraints ref point back at themselves via the file name."""
    schema = _write(
        tmp_path / "a.json",
        {"fields": [{"name": "k", "type": "string", "constraints": {"$ref": "a.json#/fields/0/constraints"}}]},
    )
    with pytest.raises(SpecLoadError, match=r"Circular \$ref.*a\.json#/fields/0/constraints"):
        load_schema(schema)


def test_cross_file_cycle_raises_from_load_package(tmp_path: Path):
    _write(tmp_path / "one.json", {"x": {"$ref": "two.json#/y"}})
    _write(tmp_path / "two.json", {"y": {"$ref": "one.json#/x"}})
    _write(
        tmp_path / "t.schema.json",
        {"fields": [{"name": "k", "type": "string", "constraints": {"$ref": "one.json#/x"}}]},
    )
    descriptor = _write(
        tmp_path / "datapackage.json",
        {"resources": [{"name": "t", "path": "t.csv", "schema": "t.schema.json"}]},
    )
    with pytest.raises(SpecLoadError, match=r"Circular \$ref"):
        load_package(descriptor)
