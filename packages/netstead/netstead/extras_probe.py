"""Which optional extras are installed, and the command that installs one.

Shared by ``netstead doctor`` and the Workbench's ``GET /api/extras``. Importing is the test: an extra counts as
installed when the packages that *define* it import (never a transitive dependency).
"""

from __future__ import annotations

import importlib

__all__ = ["EXTRA_PROBES", "extra_installed", "install_commands", "probe_extra"]

# Optional-extra -> import probe: (extra-name, module-to-import). Keep in lockstep with
# ``[project.optional-dependencies]`` in pyproject.toml: each probe is the package that *defines* the extra (the one
# whose absence means the extra is not installed), not a transitive dependency.
EXTRA_PROBES: tuple[tuple[str, str], ...] = (
    ("clean", "shapely"),
    ("clean", "geopandas"),
    ("clean", "pyproj"),
    ("graph", "scipy"),
    ("server", "fastapi"),
    ("mcp", "mcp"),
    ("notebook", "ipywidgets"),
    ("osm", "requests"),
    ("osm", "yaml"),
    ("overture", "yaml"),
    ("reports", "openpyxl"),
    ("reports", "jinja2"),
    ("nl", "jsonschema"),
    ("nl", "keyring"),
    ("bench", "psutil"),
)

# The beta installs from GitHub only. These are the same direct references as the install snippets in
# docs/quickstart-workbench.md and docs/install.md: after the PyPI release, change them all together
# (``uv add 'netstead[osm]'`` / ``pip install 'netstead[osm]'``).
_CORRAL_REF = "dbcorral @ git+https://github.com/e-lo/netstead.git@main#subdirectory=packages/corral"
_NETSTEAD_REF = "netstead[{extras}] @ git+https://github.com/e-lo/netstead.git@main#subdirectory=packages/netstead"


def probe_extra(module: str) -> tuple[bool, str]:
    """``(importable, error class name)`` for one probe module."""
    try:
        importlib.import_module(module)
    except ImportError as exc:
        return False, exc.__class__.__name__
    return True, ""


def extra_installed(extra: str) -> bool:
    """Whether every probe module of ``extra`` imports."""
    return all(probe_extra(module)[0] for name, module in EXTRA_PROBES if name == extra)


def install_commands(*extras: str) -> dict[str, str]:
    """The ``uv`` and ``pip`` commands that add ``extras`` (while the beta is GitHub-only)."""
    netstead = _NETSTEAD_REF.format(extras=",".join(extras))
    return {"uv": f'uv add "{_CORRAL_REF}" "{netstead}"', "pip": f'pip install "{_CORRAL_REF}" "{netstead}"'}
