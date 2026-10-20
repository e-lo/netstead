"""Tests for corral.docgen.markdown — the v1.0 port of the v0.3 docgen.

Six focused tests:

1. ``field_to_md_row`` — minimal field renders to a one-row markdown table
   line with the documented column order.
2. ``field_to_md_row`` — constraints render compactly (required, enum,
   min/max) without exploding into multiple cells.
3. ``schemas_to_md`` — small in-memory package renders one H2 per
   resource with a field table; foreign keys appear below the table.
4. ``package_to_md`` — small in-memory package renders an overview with
   name, version, and a resources list.
5. snapshot — ``schemas_to_md`` against the vendored GMNS 0.97 spec.
6. snapshot — ``package_to_md`` against the vendored GMNS 0.97 spec.

The snapshot files live under ``snapshots/`` and are committed; when the
spec updates, the snapshot diff is the change-detection signal.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from corral.docgen import package_to_md, schemas_to_md
from corral.docgen.markdown import field_to_md_row
from corral.spec import (
    Constraints,
    DataPackage,
    Field,
    ForeignKey,
    ForeignKeyReference,
    Resource,
    Schema,
    load_package,
)

# ---------------------------------------------------------------------------
# Small in-memory fixtures
# ---------------------------------------------------------------------------


def _small_package() -> DataPackage:
    """A 2-resource package exercising required, enum, min/max, and FKs."""
    node_schema = Schema(
        fields=[
            Field(
                name="node_id",
                type="any",
                description="Primary key.",
                constraints=Constraints(required=True),
            ),
            Field(
                name="x_coord",
                type="number",
                description="Longitude.",
                constraints=Constraints(minimum=-180, maximum=180),
            ),
        ],
        primary_key="node_id",
    )
    link_schema = Schema(
        fields=[
            Field(name="link_id", type="any", constraints=Constraints(required=True)),
            Field(name="from_node_id", type="any", constraints=Constraints(required=True)),
            Field(
                name="facility_type",
                type="string",
                description="Roadway class.",
                constraints=Constraints(enum=["motorway", "arterial", "local"]),
            ),
        ],
        primary_key="link_id",
        foreign_keys=[
            ForeignKey(
                fields="from_node_id",
                reference=ForeignKeyReference(resource="node", fields="node_id"),
            ),
        ],
    )
    return DataPackage(
        name="demo",
        title="Demo Package",
        version="0.1",
        resources=[
            Resource(name="node", path="node.csv", description="Network nodes.", schema=node_schema),
            Resource(name="link", path="link.csv", description="Network links.", schema=link_schema),
        ],
    )


# ---------------------------------------------------------------------------
# field_to_md_row
# ---------------------------------------------------------------------------


def test_field_to_md_row_minimal():
    """A bare field renders to a single pipe-delimited markdown row."""
    field = Field(name="x_coord", type="number", description="Longitude.")
    row = field_to_md_row(field)
    # Single line, leading + trailing pipe, expected columns: name, type,
    # constraints (empty), description.
    assert row.startswith("|") and row.endswith("|")
    assert "\n" not in row.strip()
    cells = [c.strip() for c in row.strip().strip("|").split("|")]
    assert cells[0] == "`x_coord`"
    assert cells[1] == "number"
    assert cells[2] == ""  # no constraints
    assert cells[3] == "Longitude."


def test_field_to_md_row_constraints_compact():
    """Required + enum + min/max collapse into one cell, separated by commas."""
    field = Field(
        name="speed",
        type="integer",
        constraints=Constraints(required=True, minimum=0, maximum=120, enum=[0, 30, 60]),
        description="Posted speed.",
    )
    row = field_to_md_row(field)
    cells = [c.strip() for c in row.strip().strip("|").split("|")]
    constraints_cell = cells[2]
    # All four constraint facets must surface in the single cell.
    assert "required" in constraints_cell
    assert "min" in constraints_cell and "0" in constraints_cell
    assert "max" in constraints_cell and "120" in constraints_cell
    # Enum values must be present; rendering shape is flexible but must
    # not contain raw Python list repr brackets with quotes.
    assert "30" in constraints_cell
    assert "60" in constraints_cell


# ---------------------------------------------------------------------------
# schemas_to_md
# ---------------------------------------------------------------------------


def test_schemas_to_md_emits_h2_table_and_foreign_keys():
    """One H2 per resource, a field-table per schema, FKs listed below."""
    md = schemas_to_md(_small_package())
    # H2 per resource — order matches DataPackage.resources order.
    assert "## node" in md
    assert "## link" in md
    assert md.index("## node") < md.index("## link")
    # Field table header for both resources.
    assert "| Field |" in md or "| Name |" in md or "| field |" in md
    # FK section appears below the link table.
    assert "from_node_id" in md
    # The reference target should appear textually somewhere in the FK
    # section (resource name or field name).
    fk_section = md[md.index("## link") :]
    assert "node" in fk_section and "node_id" in fk_section


# ---------------------------------------------------------------------------
# package_to_md
# ---------------------------------------------------------------------------


def test_package_to_md_emits_overview():
    md = package_to_md(_small_package())
    assert "Demo Package" in md or "demo" in md
    assert "0.1" in md  # version
    # Resources list — both resource names must appear.
    assert "node" in md
    assert "link" in md


def test_package_to_md_resource_links_match_the_schema_headings(spec_097_dir: Path):
    """The overview table's in-page links must hit the ``## <name>`` anchors.

    Python-Markdown's toc slug keeps underscores (``## link_tod`` ->
    ``#link_tod``). The table used to hyphenate the name, so every
    resource with an underscore in its name (16 of GMNS 0.97's 25) linked
    to an anchor that does not exist: mkdocs logged "there is no such
    anchor on this page" for each on three pages.
    """
    pkg = load_package(spec_097_dir / "datapackage.json")
    overview = package_to_md(pkg)
    schemas = schemas_to_md(pkg)
    assert "## link_tod" in schemas
    assert "[`link_tod`](#link_tod)" in overview
    assert "(#link-tod)" not in overview


# ---------------------------------------------------------------------------
# Snapshot tests against vendored GMNS 0.97
# ---------------------------------------------------------------------------


def _check_snapshot(actual: str, snapshot_path: Path) -> None:
    """Compare ``actual`` against the committed snapshot at ``snapshot_path``.

    Set ``UPDATE_SNAPSHOTS=1`` to refresh an intentional change; otherwise
    the test asserts byte-for-byte equality with the committed snapshot.
    A missing snapshot is treated as a bootstrap and written, then the
    test fails so the author both notices the new file and commits it.
    """
    if os.environ.get("UPDATE_SNAPSHOTS") == "1":
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(actual, encoding="utf-8")
        return
    if not snapshot_path.exists():
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(actual, encoding="utf-8")
        pytest.fail(f"wrote initial snapshot to {snapshot_path}; commit it and re-run")
    expected = snapshot_path.read_text(encoding="utf-8")
    if actual != expected:
        msg = (
            f"snapshot mismatch: {snapshot_path}\n"
            f"  expected {len(expected)} chars, got {len(actual)} chars\n"
            f"  re-run with UPDATE_SNAPSHOTS=1 to refresh if change is intentional"
        )
        assert actual == expected, msg


def test_schemas_to_md_snapshot_0_97(spec_097_dir: Path, snapshots_dir: Path):
    pkg = load_package(spec_097_dir / "datapackage.json")
    actual = schemas_to_md(pkg)
    _check_snapshot(actual, snapshots_dir / "gmns_0_97_schemas.md")


def test_package_to_md_snapshot_0_97(spec_097_dir: Path, snapshots_dir: Path):
    pkg = load_package(spec_097_dir / "datapackage.json")
    actual = package_to_md(pkg)
    _check_snapshot(actual, snapshots_dir / "gmns_0_97_package.md")
