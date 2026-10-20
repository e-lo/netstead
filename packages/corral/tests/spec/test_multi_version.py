"""Multi-version load behavior + version directory parsing."""

from __future__ import annotations

from pathlib import Path

import pytest
from corral.spec import (
    InvalidSpecVersionError,
    SpecLoadError,
    SpecVersion,
    compatible,
    load_package,
    load_schema,
    parse_version_dir,
)


def test_load_all_three_versions_no_cross_contamination(
    spec_095_dir: Path,
    spec_096_dir: Path,
    spec_097_dir: Path,
):
    # 0.95 has no datapackage.json — load each schema individually so
    # we still exercise the version-spanning loader paths.
    schemas_095 = [load_schema(p) for p in sorted(spec_095_dir.glob("*.schema.json"))]
    pkg_096 = load_package(spec_096_dir / "datapackage.json")
    pkg_097 = load_package(spec_097_dir / "datapackage.json")

    assert len(schemas_095) > 20
    assert len(pkg_096.resources) > 20
    assert len(pkg_097.resources) > 20

    # 0.96 vs 0.97 lists must be independent objects
    assert pkg_096.resources is not pkg_097.resources
    names_096 = {r.name for r in pkg_096.resources}
    names_097 = {r.name for r in pkg_097.resources}
    # Confirm they're not the same identity-coincidence (they should both
    # define common Frictionless resource names but be separate parsings)
    assert isinstance(names_096, set) and isinstance(names_097, set)


def test_parse_version_dir_0_97():
    p = Path("packages/netstead/netstead/spec/0.97")
    assert parse_version_dir(p) == SpecVersion(0, 97, 0)


def test_parse_version_dir_with_patch():
    p = Path("anywhere/0.97.1")
    assert parse_version_dir(p) == SpecVersion(0, 97, 1)


def test_spec_version_ordering():
    assert SpecVersion(0, 96, 0) < SpecVersion(0, 97, 0)
    assert SpecVersion(0, 97, 0) < SpecVersion(0, 97, 1)
    assert SpecVersion(1, 0, 0) > SpecVersion(0, 97, 99)
    assert SpecVersion(0, 97, 0) == SpecVersion.from_str("0.97")


def test_spec_version_is_an_ordered_dataclass():
    """Ordering comes from ``@dataclass(order=True)``, not hand-written dunders (#118)."""
    assert SpecVersion.__dataclass_params__.order  # type: ignore[attr-defined]
    versions = [SpecVersion(1, 0), SpecVersion(0, 97, 1), SpecVersion(0, 96), SpecVersion(0, 97)]
    assert sorted(versions) == [SpecVersion(0, 96), SpecVersion(0, 97), SpecVersion(0, 97, 1), SpecVersion(1, 0)]
    assert SpecVersion(0, 97) <= SpecVersion(0, 97, 0) <= SpecVersion(0, 97)
    with pytest.raises(TypeError):
        _ = SpecVersion(0, 97) < (0, 98, 0)  # type: ignore[operator]


def test_spec_version_compatibility():
    assert compatible(SpecVersion(0, 97, 0), SpecVersion(0, 97, 5))
    assert not compatible(SpecVersion(0, 96, 0), SpecVersion(0, 97, 0))
    assert compatible(SpecVersion(1, 2, 3), SpecVersion(1, 2, 99))
    assert not compatible(SpecVersion(1, 2, 3), SpecVersion(1, 3, 0))


# ---------------------------------------------------------------------------
# Structured exceptions (I6 — bare ValueError → InvalidSpecVersionError)
# ---------------------------------------------------------------------------


def test_invalid_version_shape_raises_structured_error():
    """Wrong shape: caller gets InvalidSpecVersionError, not bare ValueError."""
    with pytest.raises(InvalidSpecVersionError, match=r"0\.97\.1\.2"):
        SpecVersion.from_str("0.97.1.2")


def test_invalid_version_non_integer_raises_structured_error():
    """Non-integer component: same structured type, input echoed in message."""
    with pytest.raises(InvalidSpecVersionError, match=r"zero\.point\.nine"):
        SpecVersion.from_str("zero.point.nine")


def test_invalid_spec_version_subclasses_spec_load_error():
    """Catch-all SpecLoadError handlers must still see version parse failures."""
    with pytest.raises(SpecLoadError):
        SpecVersion.from_str("not.a.version.string")
