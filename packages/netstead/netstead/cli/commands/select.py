"""``netstead select`` — natural-language selection of GMNS elements.

Parses an utterance into a structured intent, resolves it against a network,
and emits a validated GMNS selection fragment (or the ambiguity/not-found
diagnostics). Selection only — no edit is applied.

Exit codes: 0 resolved; :data:`EXIT_NOT_FOUND` (1) nothing matched (also a
parser/LLM error); 2 invalid settings; :data:`EXIT_AMBIGUOUS` (3) an anchor
could be more than one node — the output lists each anchor's candidates, as
the Workbench's Selection panel does before you pin one.
"""

from __future__ import annotations

import json
from typing import Any

import typer
from corral.cli.render import render_dict

from netstead import Network

from ...config import SettingsError, load_settings
from ...llm import LLMError, build_registry
from ...select.emit import to_fragment
from ...select.parse import make_parser
from ...select.resolve import resolve
from ...select.result import SelectionResult
from .._helpers import resolve_engine

__all__ = ["EXIT_AMBIGUOUS", "EXIT_NOT_FOUND", "register"]

#: Exit code when the selection matched nothing (or the parser failed).
EXIT_NOT_FOUND = 1
#: Exit code when an anchor could be more than one node; the output lists the candidates.
EXIT_AMBIGUOUS = 3


def _make_parser(provider: str | None, model: str | None) -> Any:
    """The parser for ``--provider``/``--model`` over the layered settings (exit 2: bad settings; 1: no key)."""
    overrides = {k: v for k, v in {"select.provider": provider, "select.model": model}.items() if v is not None}
    try:
        settings = load_settings(overrides=overrides).settings
        return make_parser(settings.select, build_registry(settings))
    except SettingsError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from None
    except LLMError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None


def register(app: typer.Typer) -> None:
    """Register the ``select`` command on ``app``."""

    @app.command(name="select")
    def select(
        utterance: str = typer.Argument(..., help='e.g. "I-40 EB between Harrison Ave and NC 54".'),
        # str, not Path: a Path argument collapses "https://host/x" to "https:/host/x".
        source: str = typer.Argument(..., help="Path/URL to a GMNS network."),
        provider: str = typer.Option(
            None, "--provider", help="Parser: stub | anthropic | openai | gemini | ollama (default: settings)."
        ),
        model: str = typer.Option(
            None, "--model", help="Model id (default: settings, else the provider default; see `netstead llm status`)."
        ),
        engine: str = typer.Option(
            None, "--engine", help="Compute engine (DuckDB is the only one; kept for compatibility)."
        ),
        json_out: bool = typer.Option(False, "--json", help="Emit JSON on stdout."),
    ) -> None:
        """Resolve a natural-language selection to GMNS link/node ids.

        Prints the validated selection fragment on success (exit 0). On an
        ambiguous result it prints the status, the provisional link ids, each
        anchor with the other nodes it could be, and diagnostics (exit 3); on
        not-found it prints diagnostics (exit 1).
        """
        parser = _make_parser(provider, model)
        try:
            intent = parser.parse(utterance)
        except LLMError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(1) from None

        net = Network.from_source(source, engine=resolve_engine(engine))
        result = resolve(intent, net)

        if result.status == "not_found":
            _emit({"status": result.status, "diagnostics": result.diagnostics}, json_out)
            raise typer.Exit(code=EXIT_NOT_FOUND)
        if result.status == "ambiguous":
            payload = {
                "status": result.status,
                "link_ids": list(result.link_ids),
                "anchors": _anchors(result),
                "diagnostics": result.diagnostics,
            }
            _emit(payload, json_out)
            raise typer.Exit(code=EXIT_AMBIGUOUS)
        _emit(to_fragment(result), json_out)

    @app.command(name="select-serve")
    def select_serve(
        # str, not Path: a Path argument collapses "https://host/x" to "https:/host/x".
        source: str = typer.Argument(..., help="Path/URL to a GMNS network."),
        provider: str = typer.Option(
            None, "--provider", help="NL parser: stub | anthropic | openai | gemini | ollama (default: settings)."
        ),
        engine: str = typer.Option(
            None, "--engine", help="Ignored: DuckDB is the only engine (kept for compatibility)."
        ),
        host: str = typer.Option(None, "--host", help="Bind host."),
        port: int = typer.Option(None, "--port", help="Bind port."),
    ) -> None:
        """Serve the interactive selection map: now an alias of ``netstead app SOURCE``."""
        from .workbench import run_workbench

        del engine  # accepted for backward compatibility only
        typer.echo("note: `netstead select-serve` is now `netstead app`", err=True)
        run_workbench([str(source)], provider=provider, host=host, port=port)


def _anchors(result: SelectionResult) -> list[dict[str, Any]]:
    """Each located anchor (``from``/``to``): its text, node, kind, confidence and the other nodes it could be.

    The same per-anchor keys as the Workbench's selection payload, without map coordinates.
    """
    anchors = []
    for role, match in (("from", result.from_match), ("to", result.to_match)):
        if match is None or match.node_id is None:
            continue
        anchors.append(
            {
                "role": role,
                "anchor": match.anchor,
                "node_id": match.node_id,
                "kind": match.kind,
                "confidence": round(float(match.confidence), 3),
                "candidates": list(match.candidates),
                "detail": match.detail,
            }
        )
    return anchors


def _anchor_line(anchor: dict[str, Any]) -> str:
    """One readable line for an anchor: its pick, and the candidates it could be instead."""
    pick = f"node {anchor['node_id']} ({anchor['kind']}, confidence {anchor['confidence']})"
    line = f"{anchor['role']}: {anchor['anchor']!r} -> {pick}"
    if anchor["candidates"]:
        line += "; could also be node " + ", ".join(str(c) for c in anchor["candidates"])
    return line


def _emit(payload: dict[str, Any], json_out: bool) -> None:
    """JSON on stdout, or a two-column table (anchors and diagnostics one per line)."""
    if json_out:
        typer.echo(json.dumps(payload, default=str))
        return
    rows = dict(payload)
    if "anchors" in rows:
        rows["anchors"] = "\n".join(_anchor_line(a) for a in rows["anchors"])
    if "diagnostics" in rows:
        rows["diagnostics"] = "\n".join(rows["diagnostics"])
    render_dict(rows, json_out=False)
