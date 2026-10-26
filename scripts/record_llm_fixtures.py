"""Re-record the LLM contract fixtures from the live provider APIs.

    NETSTEAD_RECORD_LLM_FIXTURES=1 uv run --all-extras python scripts/record_llm_fixtures.py anthropic [openai gemini ollama]

Uses your configured keys -- environment variables, then the OS keyring, both through
:class:`~netstead.llm.secrets.SecretStore` (never a plaintext file) -- and each fixture's model and
utterance. Writes only the response JSON and the request's top-level body keys, and scrubs the
API key (and anything key-shaped) out of every string in the response before it is written, so no
key reaches disk even if a provider ever echoed part of one back. Request and response HEADERS
are never written to the fixture at all: they are the one place the key definitely travels, so
this script never even looks at them beyond handing them to ``httpx`` for the live call.

Running this hits every live provider API named on the command line and overwrites its committed
fixture, so it refuses to run at all unless ``NETSTEAD_RECORD_LLM_FIXTURES=1`` is set -- that is the
only guard against an accidental invocation (a stray "run this" in an editor, a copy-pasted
command) spending real tokens and quota and silently rewriting the contract fixtures.

Review the diff (and re-check "expected") before committing.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
from corral.io.credentials import system_keyring
from netstead.config import load_settings
from netstead.llm import build_registry
from netstead.llm.secrets import redact
from netstead.select.parse import LLMParser

FIXTURES = Path(__file__).resolve().parents[1] / "packages" / "netstead" / "tests" / "fixtures" / "llm"
#: Set this to run at all: see the module docstring for why.
OPT_IN_ENV_VAR = "NETSTEAD_RECORD_LLM_FIXTURES"


class _Recording(httpx.BaseTransport):
    """Pass requests to the network and keep the last exchange (request + decoded response).

    Headers travel with ``self.last`` only long enough for :func:`record` to read the request's
    JSON body; nothing here, and nothing :func:`record` writes to a fixture, ever serializes a
    header.
    """

    def __init__(self) -> None:
        """Wrap a real ``httpx.HTTPTransport``."""
        self._inner = httpx.HTTPTransport()
        self.last: tuple[httpx.Request, httpx.Response] | None = None

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        """Forward ``request`` to the network and remember it alongside the decoded response."""
        live = self._inner.handle_request(request)
        live.read()
        copy = httpx.Response(live.status_code, headers={"content-type": "application/json"}, content=live.content)
        self.last = (request, copy)
        return copy


def scrub_recorded(value: Any, secret: str) -> Any:
    """``value`` with every string redacted via :func:`~netstead.llm.secrets.redact` (recursive).

    Applied to the decoded response JSON before it is written to a fixture: defence in depth
    against a provider ever echoing all or part of ``secret`` back in an error message or a
    field value. Dict keys are left alone (providers don't name fields after the caller's key);
    only string values are scrubbed.

    Args:
        value: Any JSON-decoded value: a ``dict``, a ``list``, a string, or another JSON scalar.
        secret: The API key used for this exchange. Redacted verbatim, on top of the key-shaped
            pattern :func:`~netstead.llm.secrets.redact` always checks.

    Returns:
        A value of the same shape as ``value`` with every string scrubbed.
    """
    if isinstance(value, str):
        return redact(value, secret)
    if isinstance(value, dict):
        return {key: scrub_recorded(item, secret) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub_recorded(item, secret) for item in value]
    return value


def record(provider: str) -> Path:
    """Replace ``provider``'s fixture with one real exchange; return the path written."""
    path = FIXTURES / f"{provider}_select.json"
    fixture = json.loads(path.read_text())
    recorder = _Recording()
    registry = build_registry(load_settings().settings, keyring=system_keyring(), transport=recorder)
    info = registry.catalog[provider]
    secret = "" if info.kind == "local" else registry.secrets.get(registry.slot(provider), info.label)
    LLMParser(registry.provider(provider), fixture["model"], max_repairs=0).parse(fixture["utterance"])
    if recorder.last is None:
        raise SystemExit(f"{provider}: no exchange was recorded")
    request, response = recorder.last
    fixture.update(
        _recorded=True,
        source=f"Recorded from the live API by scripts/record_llm_fixtures.py ({provider}).",
        endpoint=f"{request.method} {request.url.path}",
        request_keys=sorted(json.loads(request.content)),
        response=scrub_recorded(response.json(), secret),
    )
    path.write_text(json.dumps(fixture, indent=2) + "\n")
    return path


def main(argv: list[str], *, environ: dict[str, str] | None = None) -> None:
    """Record ``argv`` (or every provider) after checking the opt-in; raises :class:`SystemExit` without it.

    Args:
        argv: Provider names to record (``sys.argv[1:]``); empty means every provider.
        environ: Environment to check :data:`OPT_IN_ENV_VAR` against. Defaults to ``os.environ``;
            tests pass a plain ``dict`` so they never depend on, or mutate, the real environment.
    """
    env = os.environ if environ is None else environ
    if env.get(OPT_IN_ENV_VAR) != "1":
        raise SystemExit(
            f"refusing to run: this hits every live provider API named below and overwrites its "
            f"committed fixture. Set {OPT_IN_ENV_VAR}=1 to confirm, e.g.:\n"
            f"  {OPT_IN_ENV_VAR}=1 uv run --all-extras python scripts/record_llm_fixtures.py "
            f"{' '.join(argv) or 'anthropic openai gemini ollama'}"
        )
    for name in argv or ["anthropic", "openai", "gemini", "ollama"]:
        print(f"recorded {record(name)}")


if __name__ == "__main__":
    main(sys.argv[1:])
