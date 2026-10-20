"""Text helpers shared by the reference generators (CLI, OpenAPI).

Help strings and OpenAPI descriptions are written for a terminal or a
docstring, not for markdown: ``[clean]`` would become an empty link,
``<path>`` an HTML tag, ``|`` a table-cell break, and ``:meth:`X``` a
leftover rst role. :func:`escape_md` makes such text safe to drop into a
markdown paragraph or table cell while leaving inline code spans alone.
"""

from __future__ import annotations

import re

__all__ = ["escape_md", "paragraphs"]

# Inline code spans (single or double backticks) stay verbatim.
_CODE_SPAN_RE = re.compile(r"(``[^`]+``|`[^`]+`)")
# rst roles such as :meth:`Network.from_source` → `Network.from_source`.
_RST_ROLE_RE = re.compile(r":[a-z:]+:(`[^`]+`)")
# Click's "do not rewrap" marker, which can also arrive as a literal
# backslash-b from a raw docstring.
_NOWRAP_RE = re.compile(r"(?:\\b|\x08)\s*")

_REPLACEMENTS = (
    ("<", "&lt;"),
    (">", "&gt;"),
    ("[", "\\["),
    ("]", "\\]"),
    ("|", "\\|"),
)


def escape_md(text: str, *, collapse: bool = True) -> str:
    """Return ``text`` safe for a markdown paragraph (or, with ``collapse``, a table cell).

    Outside inline code spans, ``<``/``>`` become entities and ``[``/``]``/``|``
    are backslash-escaped. rst roles are reduced to their code span and
    click's no-rewrap marker is dropped. With ``collapse`` (the default) all
    whitespace runs, newlines included, become one space.
    """
    text = _RST_ROLE_RE.sub(r"\1", _NOWRAP_RE.sub("", text or ""))
    out: list[str] = []
    for i, part in enumerate(_CODE_SPAN_RE.split(text)):
        if i % 2 == 1:  # a code span
            out.append(part)
            continue
        for old, new in _REPLACEMENTS:
            part = part.replace(old, new)
        out.append(part)
    joined = "".join(out)
    return " ".join(joined.split()) if collapse else joined.strip()


def paragraphs(text: str) -> list[str]:
    """Split ``text`` on blank lines, dropping empty paragraphs."""
    return [p.strip("\n") for p in re.split(r"\n[ \t]*\n", text or "") if p.strip()]
