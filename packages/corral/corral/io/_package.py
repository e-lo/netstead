"""Package-layout helpers shared by the stock adapters' ``write_package``.

Not part of the public API; the leading underscore makes that explicit.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Mapping
    from pathlib import Path

    from corral.engines.base import Engine, TableExpr
    from corral.io.base import DescriptorFactory, FormatAdapter

#: Name of the data-package descriptor written beside (or inside) a package's tables.
DESCRIPTOR_NAME = "datapackage.json"


def write_flat_package(
    adapter: FormatAdapter,
    tables: Mapping[str, TableExpr],
    dest: Path,
    engine: Engine,
    *,
    describe: DescriptorFactory,
) -> None:
    """Write ``dest/<table>.<adapter.name>`` per table plus a ``datapackage.json``.

    The layout of a csv or parquet directory. The descriptor makes the
    directory self-describing, so a source that carried its own
    descriptor keeps one when written back over itself.
    """
    dest.mkdir(parents=True, exist_ok=True)
    for name, expr in tables.items():
        adapter.write(expr, dest / f"{name}.{adapter.name}", engine=engine)
    (dest / DESCRIPTOR_NAME).write_text(json.dumps(describe(adapter.name), indent=2))
