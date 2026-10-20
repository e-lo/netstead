"""Tests for ``corral.validation.structural`` (task 2.5).

The structural check compares a spec :class:`DataPackage` (what the
source SHOULD contain) against a :class:`ResourceListing` from a
``FormatAdapter.scan()`` call (what's actually there) and emits one
:class:`Issue` per discrepancy. See :mod:`corral.validation.structural`
for the policy decisions (required vs. optional defaulting, code
namespacing, severity choices).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from corral.io.base import ResourceRef
from corral.spec.model import DataPackage, Resource
from corral.validation import (
    Category,
    Severity,
    ValidationReport,
    check_structural,
    check_structural_from_source,
)

# ---------------------------------------------------------------------------
# Tiny constructor helpers — kept inline rather than in a conftest because
# every test needs to see exactly what's in the synthetic package, and
# hiding that in a fixture obscures the assertion.
# ---------------------------------------------------------------------------


def _pkg(*resources: Resource) -> DataPackage:
    return DataPackage(name="test-pkg", resources=list(resources))


def _ref(name: str, *, path: str | None = None, fmt: str = "csv") -> ResourceRef:
    return ResourceRef(name=name, path=path or f"{name}.csv", format=fmt)


# ---------------------------------------------------------------------------
# Leavenworth fixture imports — keep them lazy so the corral test
# suite can still run if netstead is not installed (it is, in this workspace,
# but we don't want to bake in the dependency).
# ---------------------------------------------------------------------------


def _leavenworth():
    """Return ``(spec_pkg, csv_dir, zip_path, duckdb_path)`` for the bundled fixture."""
    try:
        from corral.spec.loader import load_package
        from netstead.fixtures import leavenworth
    except ImportError:  # pragma: no cover - workspace install always has netstead
        pytest.skip("netstead fixture not available in this environment")

    # The GMNS 0.97 spec datapackage is the authoritative declaration of
    # what tables a GMNS network *can* contain (link/node required, the
    # rest optional). The fixture's own datapackage.json only lists the
    # tables present in the fixture — it is the actual_resources side,
    # not the spec side. Pull both.
    import netstead

    spec_root = Path(netstead.__file__).parent / "spec" / "0.97" / "datapackage.json"
    spec_pkg = load_package(spec_root)
    return spec_pkg, leavenworth.csv_dir(), leavenworth.zip_path(), leavenworth.duckdb_path()


# ===========================================================================
# 1. Clean Leavenworth — info-level "optional missing" entries are OK,
# but no errors / warnings should appear.
# ===========================================================================


def test_clean_leavenworth_no_issues():
    spec_pkg, csv_dir, _, _ = _leavenworth()
    report = check_structural_from_source(csv_dir, spec=spec_pkg)

    # No ERROR (every required resource is present).
    assert not report.has_errors, f"unexpected errors: {[i.message for i in report.by_severity(Severity.ERROR)]}"
    # No WARNING (no unexpected resources in the Leavenworth fixture).
    assert not report.has_warnings, f"unexpected warnings: {[i.message for i in report.by_severity(Severity.WARNING)]}"


# ===========================================================================
# 2. Missing required → ERROR
# ===========================================================================


def test_missing_required_table_flagged_as_error():
    spec = _pkg(
        Resource(name="link", path="link.csv", required=True),
        Resource(name="node", path="node.csv", required=True),
    )
    # Actual: only node.
    actual = [_ref("node")]

    report = check_structural(spec, source="syn/datapackage.json", actual_resources=actual)
    errors = report.by_severity(Severity.ERROR)
    codes = [i.code for i in errors]
    assert "structural.missing_required_resource" in codes
    assert any(i.table == "link" for i in errors)


# ===========================================================================
# 3. Missing optional → INFO
# ===========================================================================


def test_missing_optional_table_flagged_as_info():
    spec = _pkg(
        Resource(name="link", path="link.csv", required=True),
        Resource(name="zone", path="zone.csv", required=False),
    )
    actual = [_ref("link")]

    report = check_structural(spec, source="syn/datapackage.json", actual_resources=actual)
    infos = report.by_severity(Severity.INFO)
    codes = [i.code for i in infos]
    assert "structural.missing_optional_resource" in codes
    assert any(i.table == "zone" for i in infos)
    # And no ERROR for the optional one.
    assert all(i.table != "zone" for i in report.by_severity(Severity.ERROR))


# ===========================================================================
# 4. Unexpected resource → WARNING
# ===========================================================================


def test_unexpected_resource_flagged_as_warning():
    spec = _pkg(Resource(name="link", path="link.csv", required=True))
    actual = [_ref("link"), _ref("extra_table")]

    report = check_structural(spec, source="syn/datapackage.json", actual_resources=actual)
    warnings = report.by_severity(Severity.WARNING)
    codes = [i.code for i in warnings]
    assert "structural.unexpected_resource" in codes
    assert any(i.table == "extra_table" for i in warnings)


# ===========================================================================
# 5. Missing-file → ERROR (knowable from actual_resources comparison)
# ===========================================================================


def test_missing_file_flagged_as_error():
    # Spec declares link at link.csv but actual scan returned nothing.
    spec = _pkg(Resource(name="link", path="link.csv", required=True))
    actual: list[ResourceRef] = []

    report = check_structural(spec, source="empty.gmns", actual_resources=actual)
    errors = report.by_severity(Severity.ERROR)
    codes = [i.code for i in errors]
    # When *required* AND missing AND a path is declared, we expect the
    # specific missing_file diagnostic in addition to the
    # missing_required_resource one. The file fact is more actionable.
    assert "structural.missing_file" in codes


# ===========================================================================
# 6. actual_resources=None → only spec-shape check; no file-presence checks
# ===========================================================================


def test_actual_resources_none_skips_file_check():
    spec = _pkg(Resource(name="link", path="link.csv", required=True))
    report = check_structural(spec, source=None, actual_resources=None)

    # No file-presence findings should be emitted when we don't have
    # actual_resources to compare against.
    codes = [i.code for i in report.issues]
    assert "structural.missing_file" not in codes
    assert "structural.missing_required_resource" not in codes
    assert "structural.missing_optional_resource" not in codes
    assert "structural.unexpected_resource" not in codes


# ===========================================================================
# 7. Messages include source and resource name
# ===========================================================================


def test_messages_include_source_and_resource_name():
    spec = _pkg(Resource(name="link", path="link.csv", required=True))
    actual: list[ResourceRef] = []
    src = "syn/datapackage.json"
    report = check_structural(spec, source=src, actual_resources=actual)
    assert report.issues, "expected at least one issue"
    for issue in report.issues:
        assert "link" in issue.message, f"resource name missing from: {issue.message!r}"
    # Source should appear at least on the missing_required_resource issue.
    msgs = [i.message for i in report.issues if i.code == "structural.missing_required_resource"]
    assert any(src in m for m in msgs), (
        f"source identifier not found in any missing_required_resource message: {msgs!r}"
    )


# ===========================================================================
# 8. All emitted issues use Category.STRUCTURAL
# ===========================================================================


def test_issues_have_correct_category():
    spec = _pkg(
        Resource(name="link", path="link.csv", required=True),
        Resource(name="zone", path="zone.csv", required=False),
    )
    actual = [_ref("extra")]  # missing link, missing zone, extra is unexpected
    report = check_structural(spec, source="syn/datapackage.json", actual_resources=actual)
    assert report.issues, "expected at least one issue"
    for issue in report.issues:
        assert issue.category is Category.STRUCTURAL, f"issue {issue.code!r} has wrong category {issue.category!r}"


# ===========================================================================
# 9. Issue.table is the resource name on per-resource issues
# ===========================================================================


def test_issues_have_table_field_set():
    spec = _pkg(
        Resource(name="link", path="link.csv", required=True),
        Resource(name="zone", path="zone.csv", required=False),
    )
    actual = [_ref("extra")]  # missing link (req), missing zone (opt), extra unexpected
    report = check_structural(spec, source="syn/datapackage.json", actual_resources=actual)

    by_code = {i.code: i for i in report.issues}
    if "structural.missing_required_resource" in by_code:
        assert by_code["structural.missing_required_resource"].table == "link"
    if "structural.missing_optional_resource" in by_code:
        assert by_code["structural.missing_optional_resource"].table == "zone"
    if "structural.unexpected_resource" in by_code:
        assert by_code["structural.unexpected_resource"].table == "extra"


# ===========================================================================
# 10-12. Cross-format wrapper smoke tests against Leavenworth
# ===========================================================================


def test_check_structural_from_source_with_leavenworth_csv_dir():
    spec_pkg, csv_dir, _, _ = _leavenworth()
    report = check_structural_from_source(csv_dir, spec=spec_pkg)
    assert isinstance(report, ValidationReport)
    assert not report.has_errors, [i.message for i in report.by_severity(Severity.ERROR)]


def test_check_structural_from_source_with_zip():
    spec_pkg, _, zip_path, _ = _leavenworth()
    report = check_structural_from_source(zip_path, spec=spec_pkg)
    assert isinstance(report, ValidationReport)
    assert not report.has_errors, [i.message for i in report.by_severity(Severity.ERROR)]


def test_check_structural_from_source_with_duckdb():
    spec_pkg, _, _, duckdb_path = _leavenworth()
    report = check_structural_from_source(duckdb_path, spec=spec_pkg)
    assert isinstance(report, ValidationReport)
    assert not report.has_errors, [i.message for i in report.by_severity(Severity.ERROR)]


# ===========================================================================
# 13. Appends to existing report; older issues preserved
# ===========================================================================


def test_check_structural_appends_to_existing_report():
    spec = _pkg(Resource(name="link", path="link.csv", required=True))
    actual: list[ResourceRef] = []

    prior = ValidationReport(spec_version="0.97", source="x.gmns")
    prior.add(
        severity=Severity.WARNING,
        category=Category.SYNC_STATE,
        code="sync.fk_stale",
        message="prior issue",
        table="link",
    )
    n_prior = len(prior.issues)

    out = check_structural(spec, source="x.gmns", actual_resources=actual, report=prior)
    assert out is prior  # mutates in place
    assert len(prior.issues) > n_prior
    assert any(i.code == "sync.fk_stale" for i in prior.issues), "prior issue must be preserved"


# ===========================================================================
# 14. Documented "required vs optional" default: required=None ↦ optional
# (opt-in required, matching GMNS 0.97's explicit `required: true` pattern).
# ===========================================================================


def test_required_vs_optional_default_is_opt_in_required():
    # Resource with no required flag at all (None) is OPTIONAL by policy.
    spec = _pkg(Resource(name="zone", path="zone.csv"))
    actual: list[ResourceRef] = []

    report = check_structural(spec, source="syn/datapackage.json", actual_resources=actual)
    # Should NOT be an error; should be an info-level optional-missing.
    assert not report.has_errors, (
        "unspecified required-ness must default to OPTIONAL, not error; "
        f"got errors: {[i.message for i in report.by_severity(Severity.ERROR)]}"
    )
    codes = [i.code for i in report.issues]
    assert "structural.missing_optional_resource" in codes
