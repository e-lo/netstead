"""Lazy :class:`Package` / :class:`Table` / view surface (task 2.7 / issue #66).

This package is the user-facing **dataset surface** of corral. It
sits on top of three lower layers:

* :mod:`corral.engines` — the cross-engine TableExpr abstraction
  (ibis / polars / pandas);
* :mod:`corral.io` — the FormatAdapter registry that dispatches a
  source path/URL to the right reader/writer;
* :mod:`corral.spec` — the parsed Frictionless DataPackage / Resource
  / Schema models.

Two value types live here:

* :class:`Table` — wraps one engine-native expression plus the
  Frictionless schema, source locator, and a mutable ``dirty`` flag.
  Lazy ops (``filter`` / ``select`` / ``head``) return a new
  :class:`Table`; materialisation happens only at the
  :meth:`Table.to_pandas` / :meth:`Table.to_polars` / :meth:`Table.collect`
  boundary.
* :class:`Package` — wraps a :class:`~corral.spec.model.DataPackage`
  plus a dict of :class:`Table`s. Exposes dict-like access
  (``pkg["link"]``), the orchestrated validation pipeline
  (:meth:`Package.validate`), the table/column scope helper
  (:meth:`Package.scope`), and the format-aware writer
  (:meth:`Package.write`).

Both types compose with the sync-state tracker from task 2.6
(:class:`~corral.validation.sync_state.DirtyTracker`). The tracker
is optional — when not installed (or when the user opts out),
:meth:`Package.write` simply skips the sync-state check and
:meth:`Package.validate` no-ops the sync-state pass.

Geographic scope lands in task 2.8 (issue #67). The :mod:`corral.dataset.view`
stub documents the planned signatures so 2.8 has a clean target.

Examples:
    Load the bundled generic sample fixture via the default engine
    and run a full validation pass::

        >>> from corral.fixtures import sample
        >>> from corral.dataset import Package
        >>> pkg = Package.from_source(sample.csv_dir(), spec=sample.DATAPACKAGE)
        >>> "book" in pkg
        True
        >>> report = pkg.validate()
        >>> report.has_errors
        False
"""

# Package + sync-state errors land just below — import after Table so
# circular references (package.py imports Table) resolve cleanly.
from .errors import PackageError
from .package import OutOfSyncError, OutOfSyncWarning, Package, infer_write_format
from .table import Table

__all__ = ["OutOfSyncError", "OutOfSyncWarning", "Package", "PackageError", "Table", "infer_write_format"]
