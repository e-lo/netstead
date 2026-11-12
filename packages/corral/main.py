"""Zensical macros module for the corral documentation site.

Two responsibilities:

1. **AI artifact emission** — emit ``llms.txt``, ``llms-full.txt``,
   ``ai/api-index.json`` per architecture §6.9. Covers the corral
   package only; the netstead site emits its own from its own
   ``main.py``.
2. **File inclusion helper** — :func:`include_file` for pulling repo-
   root files (CONTRIBUTING, LICENSE) into pages with link rewriting.

Spec macros (``local_frictionless_spec``, etc.) live only in the
netstead site's ``main.py`` — they're GMNS-specific.

Per the docs v3 split: each package has its own self-contained docs
site with its own macros file. The duplication with the netstead
``main.py`` is intentional — each site is independently buildable.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml
from corral.docgen import (
    generate_api_index_json,
    generate_llms_full_txt,
    generate_llms_txt,
)

logger = logging.getLogger(__name__)

PACKAGE_ROOT = Path(__file__).resolve().parent
DOCS_DIR = PACKAGE_ROOT / "docs"
REPO_ROOT = PACKAGE_ROOT.parent.parent

# Link rewrites for include_file: repo-root files pulled into pages.
FIND_REPLACE: dict[str, str] = {
    "<CONTRIBUTING.md>": "[Contributing](https://github.com/e-lo/netstead/blob/main/CONTRIBUTING.md)",
    "(CODE_OF_CONDUCT.md)": "(https://github.com/e-lo/netstead/blob/main/CODE_OF_CONDUCT.md)",
    "CONTRIBUTING.md)": "https://github.com/e-lo/netstead/blob/main/CONTRIBUTING.md)",
    "<LICENSE>": "[LICENSE](https://github.com/e-lo/netstead/blob/main/LICENSE)",
    "contributors.md)": "https://github.com/e-lo/netstead/blob/main/CONTRIBUTORS.md)",
    "architecture.md)": "architecture)",  # this site's own architecture page
}

_md_heading_re = {n: re.compile(rf"(#{{{n}}}\s)(.*)") for n in range(1, 6)}

# Static fallback nav matching packages/corral/mkdocs.yml.
_DEFAULT_NAV: list[dict] = [
    {
        "section": "corral docs",
        "pages": [
            {"title": "Home", "href": "index.md", "description": "corral overview + install."},
            {
                "title": "Quickstart",
                "href": "quickstart.md",
                "description": "Install, load any Frictionless package, validate it in 5 minutes.",
            },
        ],
    },
    {
        "section": "Cookbook",
        "pages": [
            {
                "title": "Cookbook",
                "href": "cookbook/index.md",
                "description": "Generic recipes — S3, format conversion, spatial scope.",
            },
        ],
    },
    {
        "section": "Reference",
        "pages": [
            {
                "title": "API",
                "href": "reference/api.md",
                "description": "Public corral API symbols.",
            },
        ],
    },
    {
        "section": "Concepts",
        "pages": [
            {
                "title": "Frictionless data packages",
                "href": "concepts/frictionless.md",
                "description": "The spec both packages build on.",
            },
            {
                "title": "When to use ibis vs pandas vs polars",
                "href": "concepts/engines.md",
                "description": "The compute engine (DuckDB) and pandas/polars/Arrow dataframe formats.",
            },
        ],
    },
    {
        "section": "AI surface",
        "pages": [
            {
                "title": "Overview",
                "href": "ai/index.md",
                "description": "llms.txt + api-index.json + Skills + MCP.",
            },
            {
                "title": "Drive the CLI from an agent",
                "href": "ai/json-cli.md",
                "description": "--json contract patterns for tool-call loops.",
            },
        ],
    },
    {
        "section": "Project",
        "pages": [
            {
                "title": "Architecture",
                "href": "architecture.md",
                "description": "Single source of truth for the v1.0 design.",
            },
            {
                "title": "Development",
                "href": "development.md",
                "description": "Contributor workflow for the monorepo.",
            },
        ],
    },
]

_PACKAGES_FOR_API_INDEX = ["corral", "corral.reports"]

SITE_URL = "https://e-lo.github.io/netstead/corral"


def _extract_live_nav(env_conf: dict) -> list[dict] | None:
    """Flatten ``env.conf["nav"]`` into the {section, pages} shape.

    External URLs (cross-site links to netstead / umbrella) are skipped
    so they don't pollute this site's llms.txt.
    """
    raw_nav = env_conf.get("nav") if isinstance(env_conf, dict) else None
    if not raw_nav:
        return None

    def _is_external(href: str) -> bool:
        return href.startswith(("http://", "https://"))

    def _walk(entries: list, default_title: str | None = None) -> list[dict]:
        pages: list[dict] = []
        for entry in entries:
            if isinstance(entry, str):
                if not _is_external(entry):
                    pages.append({"title": default_title or entry, "href": entry, "description": ""})
            elif isinstance(entry, dict) and len(entry) == 1:
                title, body = next(iter(entry.items()))
                if isinstance(body, str):
                    if not _is_external(body):
                        pages.append({"title": title, "href": body, "description": ""})
                elif isinstance(body, list):
                    pages.extend(_walk(body, default_title=title))
        return pages

    sections: list[dict] = []
    for entry in raw_nav:
        if isinstance(entry, str):
            if not _is_external(entry):
                sections.append({"section": entry, "pages": [{"title": entry, "href": entry, "description": ""}]})
        elif isinstance(entry, dict) and len(entry) == 1:
            title, body = next(iter(entry.items()))
            if isinstance(body, str):
                if not _is_external(body):
                    sections.append({"section": title, "pages": [{"title": title, "href": body, "description": ""}]})
            elif isinstance(body, list):
                pages = _walk(body, default_title=title)
                if pages:
                    sections.append({"section": title, "pages": pages})
    return sections or None


def _downshift_md(md: str) -> str:
    """Shift every markdown heading down by one level."""
    for n in (5, 4, 3, 2, 1):
        md = re.sub(_md_heading_re[n], r"#\1\2", md)
    return md


def _config_nav() -> list | None:
    """Read the raw ``nav`` list from this site's ``mkdocs.yml``.

    The config carries ``!!python/name`` tags for Markdown extensions; they
    are irrelevant here, so any tagged node loads as ``None``.
    """

    class _TolerantLoader(yaml.SafeLoader):
        pass

    _TolerantLoader.add_multi_constructor("", lambda loader, suffix, node: None)
    config = yaml.load((PACKAGE_ROOT / "mkdocs.yml").read_text(encoding="utf-8"), Loader=_TolerantLoader)
    return config.get("nav") if isinstance(config, dict) else None


def _write_ai_artifacts(docs_dir: Path, nav: list[dict], site_url: str) -> None:
    """Write llms.txt, llms-full.txt, ai/api-index.json into ``docs_dir``."""

    def _page_loader(href: str) -> str:
        page = docs_dir / href
        if page.exists():
            return page.read_text(encoding="utf-8")
        return f"<!-- page {href} not found at build time -->"

    (docs_dir / "llms.txt").write_text(
        generate_llms_txt(site_url=site_url, nav=nav),
        encoding="utf-8",
    )
    (docs_dir / "llms-full.txt").write_text(
        generate_llms_full_txt(site_url=site_url, nav=nav, page_loader=_page_loader),
        encoding="utf-8",
    )
    ai_dir = docs_dir / "ai"
    ai_dir.mkdir(exist_ok=True)
    (ai_dir / "api-index.json").write_text(
        generate_api_index_json(packages=_PACKAGES_FOR_API_INDEX),
        encoding="utf-8",
    )


def define_env(env: Any) -> None:
    """Register the page macros.

    Zensical re-runs this for every Markdown conversion, so it must stay
    cheap and side-effect free. Generated files come from :func:`generate_docs`.
    """

    @env.macro
    def include_file(
        filename: str,
        downshift_h1: bool = True,
        start_line: int = 0,
        end_line: int | None = None,
    ) -> str:
        """Verbatim file inclusion macro with link rewriting."""
        full_filename = os.path.join(REPO_ROOT, filename)
        with open(full_filename, encoding="utf-8") as f:
            lines = f.readlines()
        line_range = lines[start_line:end_line]
        content = "".join(line_range)

        if _md_heading_re[1].search(content) and downshift_h1:
            content = _downshift_md(content)

        page_base = getattr(getattr(env, "page", None), "file", None)
        page_url = getattr(page_base, "url", "") if page_base else ""
        for find, replace in FIND_REPLACE.items():
            if page_url and page_url in replace:
                replace = replace.replace(page_url, "")
            content = content.replace(find, replace)
        return content


def generate_docs() -> None:
    """Write the build-time generated files into ``docs/``.

    Run before every build (``uv run python packages/corral/main.py``): the
    generated pages are gitignored and must exist when Zensical scans the
    docs folder.
    """
    nav = _extract_live_nav({"nav": _config_nav()}) or _DEFAULT_NAV
    _write_ai_artifacts(DOCS_DIR, nav, SITE_URL)


if __name__ == "__main__":
    generate_docs()
