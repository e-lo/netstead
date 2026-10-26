"""Strip secrets from URLs before they reach a message, a label, or the browser.

Remote sources and backend errors can carry credentials: presigned-URL signatures and SAS tokens
in the query string, ``user:pass@`` or ``access-key:secret@`` userinfo in the netloc. Every
``scheme://...`` substring in a text keeps only its scheme, host, and path; the query string,
fragment, and userinfo are dropped. Plain local paths contain no ``://`` and are left alone.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from pydantic import ValidationError

from netstead.llm.secrets import redact as redact_keys

__all__ = ["MAX_ERROR_LEN", "describe_error", "safe_scrub", "scrub", "scrub_source"]

#: Default cap on a scrubbed message's length (short enough to show in a toast).
MAX_ERROR_LEN = 200
#: Any ``scheme://...`` substring, not just http(s): s3/az/gs URLs embed secrets too.
_URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s'\"<>]+")


def _strip_url(match: re.Match[str]) -> str:
    raw = match.group(0)
    scheme = raw.split("://", 1)[0]
    try:
        parts = urlsplit(raw)
    except ValueError:
        # e.g. "https://[x" -- malformed IPv6 host. Don't try to salvage it; just redact.
        return f"{scheme}://<redacted>"
    netloc = parts.netloc.rsplit("@", 1)[-1]  # drop userinfo (user:pass@ or access-key:secret@)
    return f"{parts.scheme}://{netloc}{parts.path}"


def scrub(text: str, limit: int | None = MAX_ERROR_LEN) -> str:
    """Strip query strings, fragments, and userinfo from every URL in ``text``; cap it at ``limit`` chars.

    Backend exceptions (S3, Azure, GCS, HTTP clients) routinely embed the failing URL, including
    presigned-URL signatures or ``user:pass@host`` credentials. Only scheme/host/path survive.
    ``limit=None`` keeps the full length (for labels and sources rather than error toasts). Never
    raises: a text this function cannot process is replaced rather than passed through.
    """
    try:
        out = _URL_RE.sub(_strip_url, str(text))
    except Exception:  # pragma: no cover - scrubbing must never be the reason a caller raises
        return "<unscrubbable error>"
    return out if limit is None else out[:limit]


def safe_scrub(text: str, limit: int | None = MAX_ERROR_LEN) -> str:
    """:func:`scrub` that never raises: a text it cannot process is replaced, never passed through."""
    try:
        return scrub(text, limit)
    except Exception:  # pragma: no cover - defence in depth around an already-defensive function
        return "<unscrubbable>"


def scrub_source(source: str) -> str:
    """A user-typed source made safe to show, even when it is not a well-formed URL.

    :func:`scrub` only recognizes ``scheme://`` URLs, but rejected sources are often malformed
    (``https:/nohost?sig=...``, ``s3://b::https://u:p@h``). So strip by hand: every
    ``user[:password]@`` token anywhere, then everything from the first ``?`` or ``#``.
    """
    cleaned = re.sub(r"[^\s/:@]+(?::[^\s/@]*)?@", "", str(source))
    return scrub(re.split(r"[?#]", cleaned, maxsplit=1)[0], limit=None)


def describe_error(exc: BaseException) -> str:
    """``"<Type>: <message>"`` for an exception raised by code we don't control, safe to show in the browser.

    URLs in the message lose their secrets (:func:`safe_scrub`), API-key-shaped tokens are redacted, and the
    message is capped. A pydantic
    ``ValidationError`` would print the rejected input, so it is described by where and what failed only.
    """
    if isinstance(exc, ValidationError):
        errors = exc.errors(include_url=False, include_context=False, include_input=False)
        where = "; ".join(".".join(str(p) for p in e["loc"]) + f" ({e['msg']})" for e in errors)
        message = f"{exc.error_count()} validation error(s) for {exc.title}: {where}"
    else:
        message = str(exc)
    return f"{type(exc).__name__}: {safe_scrub(redact_keys(message))}"
