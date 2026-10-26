"""Contract test: the Colab quickstart notebook must actually run.

``packages/netstead/docs/notebooks/quickstart.ipynb`` is linked from the
package README and the docs landing page ("Open in Colab"), and Colab
loads it straight from ``main``. Nothing else executes it, so it drifted
silently: the 2026-10 docs pass found its load cell pointing at a raw
GitHub *directory* URL that ``Network.from_source`` cannot detect a
format for.

This test executes every code cell in order, in one namespace, the way
Colab does, with two substitutions so it runs offline and leaves nothing
behind:

- IPython line magics and shell escapes (``%pip install …``, ``!…``) are
  dropped: the test environment already has the package.
- The HTTPS URL of the fixture zip is replaced with the bundled copy's
  local path, so the cell exercises the same zip-CSV adapter without a
  network round-trip.

Any other change to the notebook is executed as written.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from netstead.fixtures import leavenworth

pytestmark = pytest.mark.slow

NOTEBOOK = Path(__file__).resolve().parents[1] / "docs" / "notebooks" / "quickstart.ipynb"

_FIXTURE_ZIP_URL = re.compile(r"https://raw\.githubusercontent\.com/\S*?leavenworth\.csv\.zip")


def _code_cells() -> list[str]:
    nb = json.loads(NOTEBOOK.read_text())
    cells = []
    for cell in nb["cells"]:
        if cell["cell_type"] != "code":
            continue
        lines = [ln for ln in "".join(cell["source"]).splitlines() if not ln.lstrip().startswith(("%", "!"))]
        cells.append("\n".join(lines))
    return cells


def test_notebook_loads_the_fixture_over_https_from_the_zip() -> None:
    """The load cell must point at a file the remote adapter can open, not a directory."""
    source = NOTEBOOK.read_text()
    assert _FIXTURE_ZIP_URL.search(source), "load cell should fetch leavenworth.csv.zip over HTTPS"
    assert 'fixtures/leavenworth/csv"' not in source, "a raw GitHub directory URL cannot be loaded"


def test_notebook_code_cells_run_in_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    namespace: dict[str, object] = {}
    for index, cell in enumerate(_code_cells()):
        cell = _FIXTURE_ZIP_URL.sub(leavenworth.zip_path().as_posix(), cell)
        try:
            exec(compile(cell, f"{NOTEBOOK.name}#cell{index}", "exec"), namespace)
        except Exception as exc:  # pragma: no cover - failure path
            pytest.fail(f"notebook code cell {index} raised {type(exc).__name__}: {exc}\n---\n{cell}")
    assert namespace["net"].links.count() == 339
    assert len(namespace["report"].issues) == 16
