"""Cross-cutting public type aliases for corral.

This module is the **single source of truth** for type aliases that are
shared across more than one corral subpackage (e.g. ``io`` and
``engines``). Keeping them here avoids a) defining the same alias twice
with divergent contracts in sibling packages and b) creating a
``io ↔ engines`` import cycle just to share a type.

Today the only resident is :data:`SourceRef`; other cross-cutting
aliases (engine-agnostic frame types, etc.) may move here later if a
second subpackage needs them.

Design notes:
    - This module must have *no* third-party imports and no corral
      siblings as imports. That guarantee is what lets every other
      corral module pull from here without risking a cycle.
    - Anything added here should already be (or imminently be) consumed
      by two or more sibling subpackages. Single-consumer aliases stay
      colocated with their consumer.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["SourceRef"]


SourceRef = str | Path | dict
"""A reference to a tabular data source.

Accepts:
    - ``str``  -- a filesystem path or URL in fsspec form
      (``s3://bucket/key``, ``gs://...``, ``https://...``,
      ``duckdb://path/to.duckdb``, ...).
    - ``Path`` -- a local filesystem path.
    - ``dict`` -- a structured handle for sources that need richer
      payloads than a single string (e.g. ``{"format": "duckdb",
      "path": "...", "table": "link"}`` or a partitioned-parquet handle
      with predicate hints).

Most :class:`~corral.io.FormatAdapter` implementations only
meaningfully accept the ``str`` / ``Path`` arms; the dict arm exists for
engines / adapters that legitimately need it (duckdb table refs,
partitioned-parquet handles). Adapters that cannot handle a dict should
raise a clear :class:`TypeError` rather than silently mis-routing it;
the :func:`corral.io.dispatch` helper does the same at the dispatch
layer (via :func:`corral.io._paths.normalize_to_str`).
"""
