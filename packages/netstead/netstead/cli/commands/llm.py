"""``netstead llm``: language-model providers for the natural-language features.

Status, setting and removing API keys, connection tests, model lists and pulling a local
Ollama model, from the terminal. Keys are read from a hidden prompt (or stdin with ``--stdin``), never from a
command-line argument, so they stay out of shell history and ``ps``. This is also how to
manage keys when the Workbench is bound to a non-local address, since its key routes
refuse writes then.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import typer

__all__ = ["register"]


def _registry() -> Any:
    from netstead.config import SettingsError, load_settings
    from netstead.llm import build_registry

    try:
        settings = load_settings().settings
    except SettingsError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from None
    return build_registry(settings)


def _known(registry: Any, provider: str) -> Any:
    if provider not in registry.names():
        typer.echo(f"error: unknown provider {provider!r}; choose one of: {', '.join(registry.names())}", err=True)
        raise typer.Exit(2)
    return registry.catalog[provider]


def _remote(registry: Any, provider: str) -> Any:
    info = _known(registry, provider)
    if info.kind == "local":
        typer.echo(f"error: {info.label} needs no API key", err=True)
        raise typer.Exit(2)
    return info


def _status_line(row: dict[str, Any]) -> str:
    if row["kind"] == "local":
        state = f"running, {row['models']} model(s)" if row["usable"] else (row["error"] or "not reachable")
        if row["usable"] and not row["error"]:
            stand_in = f" ({row['default_model']} is not installed)" if row["model"] != row["default_model"] else ""
            state += f"; selections use {row['model']}{stand_in}"
    else:
        state = row["error"] or (f"key set ({row['source']})" if row["configured"] else "no key")
    return f"{'*' if row['usable'] else '-'} {row['label']:<16} {state}  [{row['base_url']}]"


def _next_step(row: dict[str, Any]) -> str | None:
    """A one-line next step for a local provider that can't be used yet (Ollama down, no models, no usable model)."""
    if row["kind"] != "local":
        return None
    if row["usable"]:  # running with models, but maybe none that can stand in for the default
        return f"note: {row['error']}" if row["error"] else None
    if row["models"] is None:  # the probe failed: not running (or not installed)
        return (
            "next step: start Ollama (open the app, or run: ollama serve); not installed? https://ollama.com/download"
        )
    return f"next step: run: netstead llm pull {row['default_model']}"


def _size_text(registry: Any, model: str) -> str:
    known = registry.catalog["ollama"].model(model)
    return f"about {known.size_gb:g} GB" if known and known.size_gb else "several GB"


def _is_installed(model: str, installed: list[str]) -> bool:
    """Whether ``model`` matches a tag in ``installed``; a bare name (no ``:tag``) also matches ``name:latest``."""
    return model in installed or (":" not in model and f"{model}:latest" in installed)


def _pull_with_progress(registry: Any, model: str) -> None:
    """Stream the pull to a rich progress bar on stderr; LLMError propagates to the caller."""
    from rich.console import Console
    from rich.progress import BarColumn, DownloadColumn, Progress, TaskProgressColumn, TextColumn, TransferSpeedColumn

    from netstead.llm.providers.ollama import PullTracker

    tracker = PullTracker()
    columns = (
        TextColumn("{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
    )
    with Progress(*columns, console=Console(stderr=True)) as bar:
        task = bar.add_task(f"{model}: starting", total=None)
        for event in registry.pull(model):
            tracker.update(event)
            bar.update(
                task,
                description=f"{model}: {tracker.stage}",
                total=tracker.total or None,
                completed=tracker.total if event.status == "success" else tracker.completed,
            )


def register(app: typer.Typer) -> None:
    """Register the ``llm`` sub-app on ``app``."""
    llm_app = typer.Typer(no_args_is_help=True, help="Language-model providers for natural-language features.")
    app.add_typer(llm_app, name="llm")

    @llm_app.command(name="status")
    def status(json_out: bool = typer.Option(False, "--json", help="Emit JSON on stdout.")) -> None:
        """Show which providers are usable and where each key comes from (never the key)."""
        registry = _registry()
        rows = registry.status()
        if json_out:
            typer.echo(json.dumps({"providers": rows, "keyring": registry.secrets.keyring_available}, indent=2))
            return
        for row in rows:
            typer.echo(_status_line(row))
            step = _next_step(row)
            if step:
                typer.echo(f"  {step}")
        where = (
            "OS keyring" if registry.secrets.keyring_available else "none (no OS keyring): use environment variables"
        )
        typer.echo(f"key storage: {where}")

    @llm_app.command(name="set-key")
    def set_key(
        provider: str = typer.Argument(..., help="anthropic | openai | gemini"),
        stdin: bool = typer.Option(False, "--stdin", help="Read the key from stdin instead of a hidden prompt."),
    ) -> None:
        """Store an API key for PROVIDER's endpoint: prompted and hidden, never an argument."""
        from netstead.llm import SecretStoreError

        registry = _registry()
        info = _remote(registry, provider)
        key = sys.stdin.readline() if stdin else typer.prompt(f"{info.label} API key", hide_input=True)
        try:
            source = registry.secrets.set(registry.slot(provider), key)
        except SecretStoreError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(1) from None
        typer.echo(f"stored the {info.label} key in the {source}")

    @llm_app.command(name="remove-key")
    def remove_key(provider: str = typer.Argument(..., help="anthropic | openai | gemini")) -> None:
        """Delete PROVIDER's key from the OS keyring. Env vars are yours to unset."""
        from netstead.llm import SecretStoreError

        registry = _registry()
        info = _remote(registry, provider)
        slot = registry.slot(provider)
        try:
            removed = registry.secrets.remove(slot)
            still = registry.secrets.status(slot)["source"]
        except SecretStoreError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(1) from None
        typer.echo(
            f"removed the {info.label} key from: {', '.join(removed)}" if removed else f"no stored {info.label} key"
        )
        if still == "env":
            typer.echo(f"note: a {info.label} key is still set in the environment")

    @llm_app.command(name="test")
    def test_connection(
        provider: str = typer.Argument(..., help="anthropic | openai | gemini | ollama"),
        model: str = typer.Option(None, "--model", help="Also check that this model is served."),
    ) -> None:
        """Make an authenticated call that spends no tokens (list models); exit 1 on failure."""
        registry = _registry()
        _known(registry, provider)
        result = registry.test(provider, model)
        typer.echo(result["message"])
        if result.get("catalog_missing"):
            typer.echo(f"catalog ids not served here (check models.toml): {', '.join(result['catalog_missing'])}")
        if not result["ok"]:
            raise typer.Exit(1)

    @llm_app.command(name="models")
    def models(provider: str = typer.Argument(..., help="anthropic | openai | gemini | ollama")) -> None:
        """List the models offered for PROVIDER: the catalog, or the installed models for Ollama."""
        from netstead.llm import LLMError

        registry = _registry()
        _known(registry, provider)
        try:
            rows = registry.models(provider)
        except LLMError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(1) from None
        for row in rows:
            typer.echo(f"{row['id']:<32} {row['label']:<24} {row['tier'] or '-':<9} tools={row['tools']}")

    @llm_app.command(name="pull")
    def pull(
        model: str = typer.Argument(..., help="An Ollama model, e.g. qwen3:4b"),
        yes: bool = typer.Option(False, "--yes", "-y", help="Download without asking first."),
    ) -> None:
        """Download MODEL into the configured Ollama server (llm.ollama.base_url), with progress.

        Exit 1 if Ollama can't be reached or the pull fails; exit 2 for a bad model name or provider setup.
        """
        from netstead.llm import LLMError, ProviderUnavailable
        from netstead.llm.providers.ollama import setup_hint, valid_model_name

        registry = _registry()
        info = _known(registry, "ollama")
        if not valid_model_name(model):
            typer.echo(f"error: {model!r} is not an Ollama model name (e.g. qwen3:4b or user/model:tag)", err=True)
            raise typer.Exit(2)
        base = registry.base_url("ollama")
        try:
            installed = registry.provider("ollama").list_models()
        except ProviderUnavailable:
            typer.echo(f"error: {setup_hint(base)}", err=True)
            raise typer.Exit(1) from None
        except LLMError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(1) from None
        where = "" if registry.is_local("ollama") else f" on {base} (a remote server)"
        if _is_installed(model, installed):
            typer.echo(f"{model} is already installed{where}; pulling again checks for an update.")
        if not yes and not typer.confirm(f"Download {model} ({_size_text(registry, model)}) with {info.label}{where}?"):
            typer.echo("canceled")
            raise typer.Exit(1)
        try:
            _pull_with_progress(registry, model)
        except LLMError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(1) from None
        typer.echo(f"pulled {model}. Use it with: netstead select ... --provider ollama --model {model}")
