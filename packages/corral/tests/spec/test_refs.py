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


@pytest.mark.parametrize(
    ("b_path", "ref_to_b", "ref_to_a"),
    [
        ("b.json", "./b.json#/y", "./a.json#/x"),
        ("sub/b.json", "sub/b.json#/y", "../a.json#/x"),
    ],
    ids=["dot-slash", "parent-dir"],
)
def test_cross_file_cycle_is_caught_however_the_path_is_spelled(
    tmp_path: Path, b_path: str, ref_to_b: str, ref_to_a: str
):
    """``./`` and ``../`` spellings of the same file must not hide a cycle."""
    (tmp_path / "sub").mkdir()
    a = _write(
        tmp_path / "a.json",
        {"fields": [{"name": "k", "type": "string", "constraints": {"$ref": ref_to_b}}], "x": {"$ref": ref_to_b}},
    )
    _write(tmp_path / b_path, {"y": {"$ref": ref_to_a}})
    with pytest.raises(SpecLoadError, match=r"Circular \$ref"):
        load_schema(a)


def test_root_document_is_keyed_by_its_path(tmp_path: Path):
    """A cycle back into the root file is caught on its first repeat, naming the real file."""
    schema = _write(
        tmp_path / "a.json",
        {
            "fields": [{"name": "k", "type": "string", "constraints": {"$ref": "#/defs/x"}}],
            "defs": {"x": {"$ref": "./a.json#/defs/x"}},
        },
    )
    with pytest.raises(SpecLoadError) as exc_info:
        load_schema(schema)
    message = str(exc_info.value)
    assert "<root>" not in message
    chain = message.split(": ")[-1].split(" -> ")
    assert chain == [f"{schema}#/defs/x", f"{schema}#/defs/x"]
