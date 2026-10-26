"""``netstead doctor`` — environment + install smoke checks (issue #87).

NB: this is an **install doctor**, not a network doctor — it checks
your Python install + vendored specs + optional extras. To diagnose
a specific GMNS network, use ``netstead validate <path>`` (spec /
schema / FK checks) or ``netstead quality <path>`` (data-quality rules).
"""

from __future__ import annotations

import os
import sys

import typer
from corral.cli.render import render_table

from netstead import Network
from netstead.extras_probe import EXTRA_PROBES, probe_extra
from netstead.spec import SUPPORTED_SPECS, load_gmns_spec

__all__ = ["register"]


#: Minimum supported Python — kept in lockstep with pyproject ``requires-python``.
#: Lifted to a constant so ``_check_python_version`` can format both the
#: comparison and the message from one source of truth.
_MIN_PYTHON: tuple[int, int] = (3, 11)


def register(app: typer.Typer) -> None:
    """Register the ``doctor`` command on ``app``."""

    @app.command(name="doctor")
    def doctor(
        json_out: bool = typer.Option(False, "--json", help="Emit checks as a JSON array."),
    ) -> None:
        r"""Check your netstead install — Python version, extras, vendored specs.

        \b
        This is an INSTALL doctor, not a network doctor. To diagnose a
        specific GMNS network, use:

          netstead validate <path>    spec / schema / FK / sync-state checks
          netstead quality  <path>    data-quality rules (high speed on residential, etc.)
          netstead info     <path>    table list + row counts

        Exits non-zero on any install failure.
        """
        checks: list[dict[str, object]] = [
            _check_python_version(),
            *_check_optional_extras(),
            *_check_spec_versions(),
            _check_leavenworth_loads(),
            _check_auto_approve_env(),
        ]
        render_table(checks, json_out=json_out, title="netstead doctor")
        if any(not c["ok"] for c in checks):
            raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# doctor check helpers
# ---------------------------------------------------------------------------


def _check_python_version() -> dict[str, object]:
    """Verify Python is at or above :data:`_MIN_PYTHON` — matches pyproject ``requires-python``."""
    ver = sys.version_info
    min_str = ".".join(str(p) for p in _MIN_PYTHON)
    ok = (ver.major, ver.minor) >= _MIN_PYTHON
    return {
        "name": "python_version",
        "ok": ok,
        "detail": f"{ver.major}.{ver.minor}.{ver.micro} ({'>=' + min_str if ok else 'requires >=' + min_str})",
    }


def _check_optional_extras() -> list[dict[str, object]]:
    """Probe each optional [extra] for importability. Adds ``installed: bool`` to each check.

    Schema (per check)::

        {
            "name": "extra:<extra-name>[<module>]",
            "ok": bool,         # True = check passed (importable OR allowed-absent)
            "installed": bool,  # True = the module was importable; False = absent
            "detail": str,      # human-readable status
        }

    All optional extras have ``ok=True`` regardless of installed state — being
    absent is not a failure for an OPTIONAL extra. But agents reading ``--json``
    can now branch on ``installed`` to find what's actually present (the literal
    presence/absence signal previously hidden inside the ``detail`` string).
    """
    out: list[dict[str, object]] = []
    for extra, module in EXTRA_PROBES:
        installed, error = probe_extra(module)
        if installed:
            out.append(
                {
                    "name": f"extra:{extra}[{module}]",
                    "ok": True,
                    "installed": True,
                    "detail": "importable",
                }
            )
        else:
            out.append(
                {
                    "name": f"extra:{extra}[{module}]",
                    "ok": True,  # optional — absence is not a failure
                    "installed": False,
                    "detail": f"not installed ({error}); install with `uv sync --extra {extra}`",
                }
            )
    return out


def _check_spec_versions() -> list[dict[str, object]]:
    """Each vendored spec version must parse without error."""
    out: list[dict[str, object]] = []
    for version in SUPPORTED_SPECS:
        try:
            pkg = load_gmns_spec(version)
            out.append(
                {
                    "name": f"spec:{version}",
                    "ok": True,
                    "detail": f"{len(pkg.resources)} resources",
                }
            )
        except Exception as exc:  # pragma: no cover - vendored data is checked in
            out.append({"name": f"spec:{version}", "ok": False, "detail": f"{exc.__class__.__name__}: {exc}"})
    return out


def _check_leavenworth_loads() -> dict[str, object]:
    """Smoke test: the Leavenworth fixture should load + report a link table."""
    try:
        from netstead.fixtures import leavenworth

        net = Network.from_source(leavenworth.csv_dir())
        link_count = net.safe_count("link")
        return {
            "name": "fixture:leavenworth",
            "ok": link_count is not None and link_count > 0,
            "detail": f"loaded {link_count} links from csv fixture",
        }
    except Exception as exc:
        return {
            "name": "fixture:leavenworth",
            "ok": False,
            "detail": f"{exc.__class__.__name__}: {exc}",
        }


def _check_auto_approve_env() -> dict[str, object]:
    """Informational: report whether CORRAL_AUTO_APPROVE is set."""
    value = os.environ.get("CORRAL_AUTO_APPROVE")
    return {
        "name": "env:CORRAL_AUTO_APPROVE",
        "ok": True,  # informational only
        "detail": f"set to {value!r}" if value is not None else "unset (interactive consent required)",
    }
