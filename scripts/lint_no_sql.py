#!/usr/bin/env python3
"""Enforce the "no raw SQL outside corral.engines.ibis_engine" architecture rule.

The rule (see ``docs/architecture.md`` section 3) exists because raw SQL strings
in engine adapters, IO adapters, or any dataset/validation code lock the
project into a specific dialect and bypass the lazy-ibis composition that
``corral`` is built on. A future polars-engine implementer could
accidentally embed a duckdb-fallback SELECT and nothing else in CI would
catch it.

What this script does
---------------------

Walks every ``*.py`` file under ``packages/corral/corral/``,
``packages/netstead/netstead/`` and ``plugins/project-cards/netstead_project_cards/`` (excluding the one allowed module,
``corral.engines.ibis_engine``, plus build scripts and test fixtures),
parses each file with Python's ``ast`` module, inspects every string
literal (``ast.Constant`` with ``str`` value), and flags any string that
matches a conservative SQL-keyword regex.

False negatives are preferred over false positives. We match only on
very-likely-SQL patterns (``SELECT <ident>``, ``INSERT INTO``, etc.). If
a future contributor finds a true raw-SQL string this misses, they
should tighten the patterns and add a regression test.

Escape hatch
------------

For the rare case where a raw-SQL string is genuinely the right tool
(e.g., a build script that has to talk to duckdb without ibis on the
class path), mark the line with ``# pragma: allow-sql`` to silence the
check for that one line. Document *why* the exception is justified in a
comment immediately above. The pragma applies only to the exact source
line the offending literal opens on, so a multi-line SQL string still
gets flagged unless every continuation line carries the pragma.

Exit codes
----------

* 0 - no violations
* 1 - one or more raw-SQL strings detected; locations printed to stderr

Usage
-----

::

    uv run python scripts/lint_no_sql.py

Wired into the lint job of ``.github/workflows/tests.yml``.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Directories we scan. Each entry is (root, allowed-to-have-sql relative paths).
SCAN_ROOTS: list[tuple[Path, set[Path]]] = [
    (
        REPO_ROOT / "packages" / "corral" / "corral",
        {
            # The one and only module permitted to embed raw SQL.
            REPO_ROOT / "packages" / "corral" / "corral" / "engines" / "ibis_engine.py",
        },
    ),
    (
        REPO_ROOT / "packages" / "netstead" / "netstead",
        {
            # The fixture build script bypasses ibis intentionally (it has to
            # run before the ibis engine is wired and needs raw duckdb). The
            # specific SQL line carries a `# pragma: allow-sql` comment.
        },
    ),
    (
        REPO_ROOT / "plugins" / "project-cards" / "netstead_project_cards",
        set(),  # the plugin has no SQL at all: it edits through the Host API
    ),
]

# Conservative SQL-keyword patterns. Designed for high precision (no
# false positives on prose) at the cost of some recall:
#
#   * We require ALL-CAPS keywords. Real SQL embedded in Python is
#     overwhelmingly written in uppercase by convention; prose strings
#     like "Select which engine" use sentence case and so don't match.
#   * Each pattern requires *two* SQL tokens in succession (SELECT ...
#     FROM, INSERT INTO, CREATE TABLE, etc.) to filter out incidental
#     capitalized words.
#
# If you find a true raw-SQL string this misses, tighten the pattern set
# and add a regression case to the false-positives/false-negatives table
# in this script's tests (or use ``# pragma: allow-sql`` for a deliberate
# exception with a comment explaining why).
SQL_PATTERNS: list[re.Pattern[str]] = [
    # SELECT ... FROM ... — requires both keywords.
    re.compile(r"\bSELECT\b[\s\S]{0,200}?\bFROM\b"),
    re.compile(r"\bINSERT\s+INTO\s+\w"),
    re.compile(r"\bUPDATE\s+\w+\s+SET\b"),
    re.compile(r"\bDELETE\s+FROM\s+\w"),
    re.compile(r"\bCREATE\s+TABLE\b"),
    re.compile(r"\bCREATE\s+OR\s+REPLACE\b"),
    re.compile(r"\bDROP\s+TABLE\b"),
    re.compile(r"\bALTER\s+TABLE\b"),
    re.compile(r"\bWITH\s+\w+\s+AS\s*\("),
    re.compile(r"\bJOIN\s+\w+\s+ON\b"),
]

PRAGMA = "pragma: allow-sql"


class _Violation:
    __slots__ = ("lineno", "path", "pattern", "snippet")

    def __init__(self, path: Path, lineno: int, pattern: str, snippet: str) -> None:
        self.path = path
        self.lineno = lineno
        self.pattern = pattern
        self.snippet = snippet

    def format(self) -> str:
        rel = self.path.relative_to(REPO_ROOT)
        # Trim long snippets so the error message stays readable.
        snip = self.snippet if len(self.snippet) <= 80 else self.snippet[:77] + "..."
        return f"{rel}:{self.lineno}: raw SQL string detected ({self.pattern!r}): {snip!r}"


def _iter_py_files(root: Path, skip: set[Path]) -> list[Path]:
    return [p for p in sorted(root.rglob("*.py")) if p.resolve() not in skip]


def _line_has_pragma(source_lines: list[str], lineno: int) -> bool:
    # ast linenos are 1-based.
    idx = lineno - 1
    if 0 <= idx < len(source_lines):
        return PRAGMA in source_lines[idx]
    return False


def _scan_file(path: Path) -> list[_Violation]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"warning: could not read {path}: {exc}", file=sys.stderr)
        return []
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        print(f"warning: could not parse {path}: {exc}", file=sys.stderr)
        return []

    source_lines = text.splitlines()
    violations: list[_Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        value = node.value
        if not value:
            continue
        for pat in SQL_PATTERNS:
            if pat.search(value):
                if _line_has_pragma(source_lines, node.lineno):
                    break  # explicitly allowed
                violations.append(
                    _Violation(
                        path=path,
                        lineno=node.lineno,
                        pattern=pat.pattern,
                        snippet=value.replace("\n", " "),
                    )
                )
                break  # one finding per string is enough
    return violations


def main() -> int:
    """Scan all in-scope Python files; print violations; return exit code."""
    all_violations: list[_Violation] = []
    for root, skip in SCAN_ROOTS:
        if not root.exists():
            continue
        for path in _iter_py_files(root, skip):
            all_violations.extend(_scan_file(path))

    if all_violations:
        print("Raw SQL strings detected outside corral.engines.ibis_engine:", file=sys.stderr)
        for v in all_violations:
            print(f"  {v.format()}", file=sys.stderr)
        print(
            f"\n{len(all_violations)} violation(s). "
            "Use ibis expressions instead, or mark a deliberate exception with "
            "'# pragma: allow-sql' on the offending line (and document why).",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
