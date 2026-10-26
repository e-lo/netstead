"""Structural validation — does the source actually have the tables the spec declares?

This is the cheapest validation pass: no row reads, no schema parsing,
just a list-vs-list compare between

    * ``package.resources`` — the spec's declared resources, "what
      should be at the source", and
    * the :class:`~corral.io.base.ResourceListing` returned by a
      :class:`~corral.io.base.FormatAdapter.scan` call — "what's
      actually there".

Every discrepancy becomes one :class:`~corral.validation.Issue` with
``category=Category.STRUCTURAL`` and a stable dotted ``code``:

* ``structural.missing_required_resource`` — ERROR. Spec marks the
  resource as required and the scan didn't find it.
* ``structural.missing_optional_resource`` — INFO. Spec marks the
  resource as optional and the scan didn't find it. Informational only.
* ``structural.unexpected_resource`` — WARNING. Scan found a resource
  the spec doesn't declare. Could be a legal extension; could be a typo.
* ``structural.missing_file`` — ERROR. Spec declares a resource at a
  ``path`` and that path is not present at the source.

**Required vs. optional policy (opt-in required).** Frictionless does
not authoritatively define the semantics of the ``required`` field on
a :class:`~corral.spec.model.Resource`. We treat it as a positive
assertion:

    * ``required=True``   → required (ERROR if missing)
    * ``required=False``  → explicitly optional (INFO if missing)
    * ``required=None`` (omitted) → **treated as optional** (INFO if missing)

The reason this is opt-in rather than opt-out: GMNS 0.97's
``datapackage.json`` declares ``required: true`` on exactly the link
and node resources and omits the field on all others — the
straightforward read of that is "everything else is optional". A
default-required policy would flag every unused optional GMNS table as
an ERROR when validating against the canonical GMNS spec, which is
clearly wrong. This same policy is the natural one for any other
Frictionless package that follows the GMNS convention.

If a downstream caller wants stricter behavior ("any declared
resource must be present"), it can iterate the report and re-categorise
``structural.missing_optional_resource`` to ERROR — the codes are
stable for exactly this kind of policy layering.

The companion :func:`check_structural_from_source` is a one-call
wrapper that takes a source + a spec, runs the adapter's
:meth:`~corral.io.base.FormatAdapter.scan` for you, and threads the
result into :func:`check_structural`. It also handles the (common!)
case of a *directory of csvs* / *directory of parquet files* — a
shape that no single :class:`~corral.io.base.FormatAdapter` owns
because the directory itself has no extension to dispatch on.

Examples:
    Synthetic spec missing one required table::

        >>> from corral.spec.model import DataPackage, Resource
        >>> from corral.io.base import ResourceRef
        >>> from corral.validation.structural import check_structural
        >>> pkg = DataPackage(
        ...     name="demo",
        ...     resources=[
        ...         Resource(name="link", path="link.csv", required=True),
        ...         Resource(name="node", path="node.csv", required=True),
        ...     ],
        ... )
        >>> actual = [ResourceRef(name="node", path="node.csv", format="csv")]
        >>> r = check_structural(pkg, source="syn", actual_resources=actual)
        >>> r.has_errors
        True
        >>> "structural.missing_required_resource" in {i.code for i in r.issues}
        True
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from corral.io import (
    FormatNotDetected,
    ResourceListing,
    ResourceRef,
    dispatch,
    get_adapter,
    list_adapters,
)
from corral.reports import Category, Issue, Severity, ValidationReport
from corral.spec.loader import load_package
from corral.spec.model import DataPackage

if TYPE_CHECKING:  # pragma: no cover - typing only
    from corral.engines.base import Engine

__all__ = ["check_structural", "check_structural_from_source"]


# ---------------------------------------------------------------------------
# Resource required-ness
# ---------------------------------------------------------------------------


def _is_required(resource) -> bool:
    """Is ``resource`` required?

    Policy: opt-in required — only ``required=True`` makes a resource
    required. ``required=False`` and ``required=None`` (unspecified)
    both mean optional. See the module docstring for the rationale.
    """
    return resource.required is True


# ---------------------------------------------------------------------------
# Source labelling — used inside Issue.message
# ---------------------------------------------------------------------------


def _source_label(source: str | None) -> str:
    """Human-readable identifier for ``source`` in Issue messages."""
    if source is None or source == "":
        return "<unknown source>"
    return source


# ---------------------------------------------------------------------------
# check_structural — the core list-vs-list compare
# ---------------------------------------------------------------------------


def check_structural(
    package: DataPackage,
    source: str | None,
    actual_resources: ResourceListing | None = None,
    *,
    report: ValidationReport | None = None,
) -> ValidationReport:
    """Verify all spec-declared resources are present at the source.

    Compares ``package.resources`` (the spec's declared resources — what
    SHOULD be at the source) against ``actual_resources`` (what's
    actually there, typically from a
    :meth:`~corral.io.base.FormatAdapter.scan` call) and emits one
    :class:`Issue` per discrepancy:

        * ``structural.missing_required_resource`` — a resource the
          spec marks as required is absent. Severity: ERROR.
        * ``structural.missing_optional_resource`` — an optional
          resource is absent. Severity: INFO (informational only;
          users may have intentionally chosen not to include it).
        * ``structural.unexpected_resource`` — ``actual_resources``
          contains a resource the spec doesn't declare. Severity:
          WARNING (might be valid, might be a typo'd name).
        * ``structural.missing_file`` — the spec declares a resource at
          path X but the file at X is not in ``actual_resources``.
          Severity: ERROR. Fires only when ``actual_resources`` is
          provided.

    See the module docstring for the required-vs-optional policy.

    Args:
        package: The :class:`DataPackage` describing what the source
            SHOULD contain.
        source: Identifier for the source (path or URL). Appears in
            Issue messages and in the report header. May be ``None``
            for in-memory packages.
        actual_resources: What
            :meth:`~corral.io.base.FormatAdapter.scan` actually
            found at the source. If ``None``, only the spec-shape
            check runs — no missing-file / missing-resource /
            unexpected-resource diagnostics are emitted.
        report: An existing :class:`ValidationReport` to append into.
            Created if not given. Returned in either case.

    Returns:
        The (possibly newly created) :class:`ValidationReport`,
        populated with one :class:`Issue` per discrepancy. Issues use
        ``category=Category.STRUCTURAL`` and the codes above.

    Examples:
        >>> from corral.spec.model import DataPackage, Resource
        >>> from corral.io.base import ResourceRef
        >>> pkg = DataPackage(
        ...     name="demo",
        ...     resources=[Resource(name="link", path="link.csv", required=True)],
        ... )
        >>> actual = [ResourceRef(name="link", path="link.csv", format="csv")]
        >>> r = check_structural(pkg, source="ok", actual_resources=actual)
        >>> r.is_clean
        True

        Missing required → error:

        >>> r = check_structural(pkg, source="bad", actual_resources=[])
        >>> r.has_errors
        True
    """
    if report is None:
        report = ValidationReport(source=source)
    elif report.source is None:
        # Carry the source through if the caller built an empty report.
        report.source = source

    src_label = _source_label(source)

    # If we have nothing to compare against, run only the spec-shape
    # checks. Today the spec model is validated at parse time so there's
    # nothing extra to flag here — return the (possibly already
    # populated) report unchanged.
    if actual_resources is None:
        return report

    # Index actual resources by logical name once so the spec-side loop
    # is a pure dict lookup. The same actual_name set drives the
    # unexpected-resource check below.
    actual_by_name: dict[str, ResourceRef] = {ref.name: ref for ref in actual_resources}
    actual_names: set[str] = set(actual_by_name)
    spec_names: set[str] = {r.name for r in package.resources}

    # --- per-spec-resource checks ----------------------------------------
    for resource in package.resources:
        if resource.name in actual_names:
            continue  # present — nothing to flag

        required = _is_required(resource)

        if required:
            # 1. The high-level "this required resource is gone" issue.
            report.add_issue(
                Issue(
                    severity=Severity.ERROR,
                    category=Category.STRUCTURAL,
                    code="structural.missing_required_resource",
                    message=(f"package source {src_label!r}: required resource {resource.name!r} is missing"),
                    table=resource.name,
                    fix_hint=(
                        f"Add the {resource.name} table to your data package, "
                        f"or set this resource to required=False in the spec "
                        f"if it's optional in your context."
                    ),
                )
            )
            # 2. If the resource declares a path, also emit the more
            # actionable "the file at <path> isn't there" diagnostic.
            # We surface both because they're different facts: one
            # speaks to the spec, the other to the filesystem.
            if resource.path:
                path_repr = resource.path if isinstance(resource.path, str) else ", ".join(resource.path)
                report.add_issue(
                    Issue(
                        severity=Severity.ERROR,
                        category=Category.STRUCTURAL,
                        code="structural.missing_file",
                        message=(
                            f"resource {resource.name!r} declares path "
                            f"{path_repr!r} but the file is not present at "
                            f"the source"
                        ),
                        table=resource.name,
                        fix_hint=(
                            f"Create {path_repr} under the source, or update the spec to point at the file that exists."
                        ),
                        extra={"path": resource.path},
                    )
                )
        else:
            # Optional + missing → informational only.
            report.add_issue(
                Issue(
                    severity=Severity.INFO,
                    category=Category.STRUCTURAL,
                    code="structural.missing_optional_resource",
                    message=(f"package source {src_label!r}: optional resource {resource.name!r} is not present"),
                    table=resource.name,
                )
            )

    # --- unexpected-resource check ---------------------------------------
    for name in sorted(actual_names - spec_names):
        report.add_issue(
            Issue(
                severity=Severity.WARNING,
                category=Category.STRUCTURAL,
                code="structural.unexpected_resource",
                message=(f"package source {src_label!r}: resource {name!r} was found but is not declared in the spec"),
                table=name,
                fix_hint=(
                    f"Add {name} to the spec, rename it to match an existing "
                    f"declared resource, or remove it from the source."
                ),
                extra={"path": actual_by_name[name].path, "format": actual_by_name[name].format},
            )
        )

    return report


# ---------------------------------------------------------------------------
# check_structural_from_source — convenience wrapper
# ---------------------------------------------------------------------------


def _scan_directory_of_known_formats(source_path: Path) -> ResourceListing:
    """Walk a directory, asking each known format adapter to scan child files.

    A directory of csvs (the Leavenworth ``csv/`` form) and a directory
    of parquet files (the Leavenworth ``parquet/`` form) both have no
    extension on the directory itself, so the format dispatcher can't
    resolve them directly. We iterate the registered adapters' single
    declared extensions and ask each whose extension matches a child
    to scan that child. Duplicate names (same stem under multiple
    extensions) collapse to the first hit, in adapter-registration
    order.

    Args:
        source_path: A local directory.

    Returns:
        A :class:`ResourceListing` aggregated from every recognizable
        child file in the directory. Empty if nothing matches.
    """
    listings: list[ResourceRef] = []
    seen: set[str] = set()

    # Map child extension → adapter (insertion-ordered).
    ext_owners: list[tuple[str, str]] = []  # (ext, adapter name)
    for adapter_name in list_adapters():
        adapter = get_adapter(adapter_name)
        for ext in adapter.extensions:
            ext_owners.append((ext.lower().lstrip("."), adapter_name))

    for child in sorted(source_path.iterdir()):
        if not child.is_file():
            continue
        name_lower = child.name.lower()
        for ext, adapter_name in ext_owners:
            if name_lower.endswith("." + ext):
                adapter = get_adapter(adapter_name)
                try:
                    refs = adapter.scan(child)
                except Exception:
                    # Skip unreadable files — better to under-report than
                    # to break the structural check on a transient I/O
                    # failure. The schema check (task 2.3) will surface
                    # the real read error when the file is actually
                    # opened.
                    refs = []
                for ref in refs:
                    if ref.name in seen:
                        continue
                    seen.add(ref.name)
                    listings.append(ref)
                break  # first matching adapter wins per file

    return listings


def check_structural_from_source(
    source: str | Path,
    *,
    spec: DataPackage | str | Path,
    engine: Engine | None = None,
    report: ValidationReport | None = None,
) -> ValidationReport:
    """One-call wrapper around :func:`check_structural`.

    Opens ``source`` via :func:`corral.io.dispatch`, calls the
    adapter's :meth:`~corral.io.base.FormatAdapter.scan` to discover
    actual resources, and runs :func:`check_structural` against
    ``spec``. Handles directories of csv/parquet files by walking
    children and scanning each with the appropriate adapter — those
    are the canonical persistent layout for GMNS, and no single
    :class:`~corral.io.base.FormatAdapter` owns the directory shape
    itself.

    Args:
        source: A path, URL, directory of csv/parquet files, ``.zip``,
            ``.duckdb`` — anything :func:`corral.io.dispatch` knows
            about, plus the directory-of-files convention.
        spec: Either an in-memory :class:`DataPackage`, or a path /
            URL to a ``datapackage.json`` that :func:`load_package`
            can read.
        engine: Optional engine for adapters that need one for
            metadata reads. Most don't; ``None`` is the right default.
        report: Existing :class:`ValidationReport` to append into.
            Created if not given.

    Returns:
        The :class:`ValidationReport`. When the source format cannot
        be detected at all — neither extension dispatch nor the
        directory-walk found anything readable — a single
        ``structural.missing_file`` issue covering the source is
        emitted (Severity.ERROR) and no per-resource checks run.

    Examples:
        Validate the bundled sample csv directory against its
        Frictionless datapackage.json:

        >>> from corral.fixtures import sample
        >>> from corral.spec.loader import load_package
        >>> from corral.validation import check_structural_from_source
        >>> pkg = load_package(sample.DATAPACKAGE)
        >>> report = check_structural_from_source(sample.csv_dir(), spec=pkg)
        >>> report.has_errors
        False
    """
    # 1. Resolve spec.
    package = spec if isinstance(spec, DataPackage) else load_package(spec)

    # 2. Build a string label that will appear in messages + the report header.
    source_str = str(source)
    source_path = Path(source_str)

    if report is None:
        report = ValidationReport(source=source_str)
    elif report.source is None:
        report.source = source_str

    # 3. Discover actual resources.
    #
    # Three branches, in order of preference:
    #   (a) Source is a local directory — walk children with adapter
    #       extension matching. This handles csv-dir and parquet-dir,
    #       which the format dispatcher can't resolve because the
    #       directory itself has no extension. (parquet *partitioned*
    #       directories — Hive-style key=value subdirs — are handled
    #       below by dispatch().)
    #   (b) Format dispatcher resolves the source — read scan() from
    #       the resolved adapter.
    #   (c) Neither — emit one cross-cutting ERROR and return.
    actual: ResourceListing | None
    if source_path.exists() and source_path.is_dir() and not _is_partitioned_parquet_dir(source_path):
        actual = _scan_directory_of_known_formats(source_path)
    else:
        try:
            adapter = dispatch(source)
        except FormatNotDetected:
            report.add_issue(
                Issue(
                    severity=Severity.ERROR,
                    category=Category.STRUCTURAL,
                    code="structural.missing_file",
                    message=(f"could not open source {source_str!r}: no registered format adapter recognizes it"),
                    fix_hint=(
                        "Confirm the path/URL is correct and the format is "
                        "one of: csv, parquet, duckdb, zipcsv, remote URL."
                    ),
                    extra={"source": source_str},
                )
            )
            return report
        actual = adapter.scan(source, engine=engine)

    # 4. Run the real compare.
    return check_structural(package, source_str, actual, report=report)


def _is_partitioned_parquet_dir(path: Path) -> bool:
    """Cheap probe: does ``path`` look like a Hive-partitioned parquet dataset?

    Used by :func:`check_structural_from_source` to choose between
    walking children manually (a plain directory of files) and letting
    :func:`~corral.io.dispatch` route to the parquet adapter (which
    knows partitioned layouts). We deliberately don't recurse — the
    parquet adapter's own ``probe`` does the heavyweight work.
    """
    if not path.is_dir():
        return False
    try:
        for child in path.iterdir():
            if child.is_dir() and "=" in child.name:
                return True
    except OSError:
        return False
    return False
