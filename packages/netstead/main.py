"""mkdocs-macros entry point for the netstead documentation site.

Loaded by ``mkdocs-macros-plugin`` from this directory because the
netstead ``mkdocs.yml`` lives alongside (`packages/netstead/mkdocs.yml`).
Three responsibilities:

1. **Spec macros** — :func:`local_frictionless_spec`,
   :func:`local_frictionless_schemas`, :func:`official_frictionless_spec`,
   :func:`official_frictionless_schemas`. Used inside markdown pages
   to render the vendored or upstream GMNS spec as tables.
2. **AI artifact emission** — emit ``llms.txt``, ``llms-full.txt``,
   ``ai/api-index.json`` per architecture §6.9 (netstead package only;
   the corral site emits its own from its own ``main.py``).
3. **File inclusion helper** — :func:`include_file` for embedding
   repo-root files (README, CONTRIBUTING, CHANGELOG, etc.) with link
   rewriting.
4. **Generated reference pages** — ``reference/cli.md`` from the live
   typer command tree and ``reference/workbench-http-api.md`` from the
   Workbench FastAPI app's OpenAPI document, written into the docs tree
   before the AI artifacts so ``llms-full.txt`` carries them too. Both
   are gitignored; the hand-written intros live in this file.

Per the docs v3 split: each package has its own self-contained docs
site with its own macros file. The shared logic was previously in a
repo-root ``main.py``; now lives in two parallel files (this one + the
corral one). The duplication is intentional — each site is
independently buildable.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

from corral.docgen import (
    RouteGroup,
    click_to_md,
    generate_api_index_json,
    generate_llms_full_txt,
    generate_llms_txt,
    openapi_to_md,
    package_to_md,
    schemas_to_md,
)
from corral.spec import SpecLoadError, load_package

logger = logging.getLogger(__name__)

# Resolve repo paths from this file's location (packages/netstead/).
PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parent.parent
VENDORED_SPEC_ROOT = PACKAGE_ROOT / "netstead" / "spec"
DEFAULT_LOCAL_VERSION = "0.97"
OFFICIAL_BASE = "https://raw.githubusercontent.com/zephyr-data-specs/GMNS"

# Link rewrites for include_file: pages that used to reference shared
# docs (CONTRIBUTING, LICENSE, etc.) get pointed at the umbrella or
# GitHub source. The corral site holds development.md; netstead
# cross-links via absolute URL.
FIND_REPLACE: dict[str, str] = {
    "<CONTRIBUTING.md>": "[Contributing](https://github.com/e-lo/netstead/blob/main/CONTRIBUTING.md)",
    "(CODE_OF_CONDUCT.md)": "(https://github.com/e-lo/netstead/blob/main/CODE_OF_CONDUCT.md)",
    "CONTRIBUTING.md)": "https://github.com/e-lo/netstead/blob/main/CONTRIBUTING.md)",
    "<LICENSE>": "[LICENSE](https://github.com/e-lo/netstead/blob/main/LICENSE)",
    "contributors.md)": "https://github.com/e-lo/netstead/blob/main/CONTRIBUTORS.md)",
    # The architecture lives on the corral site; netstead pages cross-link.
    "architecture.md)": "https://e-lo.github.io/netstead/corral/architecture/)",
    # Relative links inside packages/netstead/CHANGELOG.md (included on
    # changelog.md), resolved from the docs root or sent to GitHub.
    "(../corral/CHANGELOG.md)": "(https://github.com/e-lo/netstead/blob/main/packages/corral/CHANGELOG.md)",
    "(docs/migration/v0.3-to-v1.0.md)": "(migration/v0.3-to-v1.0.md)",
    "(netstead/fixtures/leavenworth/README.md)": (
        "(https://github.com/e-lo/netstead/blob/main/packages/netstead/netstead/fixtures/leavenworth/README.md)"
    ),
    "(../../skills/)": "(https://github.com/e-lo/netstead/tree/main/skills/)",
}

# Anchored to the line start so "## x" is not also matched as an H1 (the
# unanchored form shifted every included heading several levels).
_md_heading_re = {n: re.compile(rf"^(#{{{n}}}\s)(.*)", re.MULTILINE) for n in range(1, 6)}

# Static fallback nav matching packages/netstead/mkdocs.yml. The
# live-nav extractor below prefers env.conf["nav"] and only falls
# back to this when the macros plugin runs outside a normal build.
_DEFAULT_NAV: list[dict] = [
    {
        "section": "netstead docs",
        "pages": [
            {"title": "Home", "href": "index.md", "description": "netstead overview + install."},
            {
                "title": "Quickstart",
                "href": "quickstart.md",
                "description": "Load the bundled Leavenworth fixture and run validation in 5 minutes.",
            },
            {
                "title": "What is GMNS?",
                "href": "what-is-gmns.md",
                "description": "Plain-English intro to the spec.",
            },
            {
                "title": "Visual tour",
                "href": "visual-tour.md",
                "description": "Leavenworth rendered as map + validation + edit + scope.",
            },
            {
                "title": "Changelog",
                "href": "changelog.md",
                "description": "Every notable change to the netstead package, by release.",
            },
        ],
    },
    {
        "section": "Cookbook",
        "pages": [
            {
                "title": "Cookbook",
                "href": "cookbook/index.md",
                "description": "GMNS-specific recipes — validate, scope, edit, host, drive from AI.",
            },
        ],
    },
    {
        "section": "Reference",
        "pages": [
            {
                "title": "API",
                "href": "reference/api.md",
                "description": "Public netstead API symbols (Network, scope, clean, quality).",
            },
            {
                "title": "CLI",
                "href": "reference/cli.md",
                "description": "Every netstead command with its arguments and options.",
            },
            {
                "title": "Workbench HTTP API",
                "href": "reference/workbench-http-api.md",
                "description": "Every route of the Workbench server, from its OpenAPI document.",
            },
            {
                "title": "Schema",
                "href": "reference/spec.md",
                "description": "GMNS field-level reference per table.",
            },
            {
                "title": "Table of tables",
                "href": "reference/table-of-tables.md",
                "description": "Every GMNS table with purpose + FK diagram.",
            },
            {
                "title": "Glossary",
                "href": "reference/glossary.md",
                "description": "GMNS terms + project conventions.",
            },
        ],
    },
    {
        "section": "Migration + AI",
        "pages": [
            {
                "title": "Migration v0.3 → v1.0",
                "href": "migration/v0.3-to-v1.0.md",
                "description": "Side-by-side API mapping for v0.3 users.",
            },
            {
                "title": "MCP tools reference",
                "href": "ai/mcp-tools.md",
                "description": "MCP server tools shipped with netstead.",
            },
        ],
    },
]

# api-index covers ONLY netstead here. The corral site emits its own
# api-index covering corral + corral.reports.
_PACKAGES_FOR_API_INDEX = ["netstead"]

SITE_URL = "https://e-lo.github.io/netstead/netstead"

# ---------------------------------------------------------------------------
# Generated reference pages (CLI + Workbench HTTP API)
# ---------------------------------------------------------------------------
#
# The generated bodies sit inside {% raw %} blocks so the macros plugin
# never tries to parse a brace that came from a help string or a route
# path. The intros are hand-written here (not in a docs/_*.md include)
# so the generated file is self-contained for llms-full.txt.

_CLI_PAGE = """\
---
title: CLI reference
audience: both
kind: reference
summary: Every netstead command and sub-command with its arguments and options, generated from the CLI at build time.
---

# CLI reference

## Summary

`netstead` is the command-line front door to the library: inspect, validate, convert, build, scope,
clean and benchmark GMNS networks, and start the HTTP, MCP and Workbench servers. The command tree
below is generated from the live typer app (`netstead.cli.app`) on every docs build, so it cannot
drift from the installed version; `netstead --help` and `netstead <command> --help` print the same
text in the terminal.

Three conventions apply across the tree:

- **`--json`.** Every data command accepts `--json` and then writes exactly one JSON document to
  stdout, so the result pipes into `jq` or straight into an agent. The index table marks which
  commands take it.
- **`netstead doctor`** is an *install* doctor: Python version, optional extras, vendored spec
  versions, and whether the bundled Leavenworth fixture loads. To diagnose a network, use
  `netstead validate` (spec, schema and foreign-key checks) or `netstead quality` (data-quality rules).
- **Optional extras.** Commands that need an extra (`server`, `mcp`, `clean`, `osm`, `reports`, ...)
  are always listed in `--help`. When the extra is missing they exit with code 1 and one line on
  stderr, `netstead.<module> requires the [<extra>] extra: pip install 'netstead[<extra>]'`, which
  names the extra to install; `netstead doctor` shows which extras are present.

## Commands

{% raw %}
{commands}
{% endraw %}

## Examples

Check the install, then summarize and validate a network. `--json` turns the summary into one
JSON document; `--html` writes the interactive validation report next to the terminal output.

```bash
netstead doctor
netstead info ./my-network --json
netstead validate ./my-network --html report.html
```

## See also

- [API reference](api.md) — the Python surface the commands wrap.
- [Settings reference](settings.md) — `netstead.toml` keys the CLI and Workbench read.
- [Run the bench](../cookbook/run-bench.md), [Self-host the HTTP server](../cookbook/serve-http.md),
  [Wire up the MCP server](../cookbook/serve-mcp.md).
- [MCP tools](../ai/mcp-tools.md) — the same operations exposed to AI agents.
"""

_HTTP_PAGE = """\
---
title: Workbench HTTP API reference
audience: both
kind: reference
summary: Every route the Workbench server exposes, grouped by area, generated from its OpenAPI document at build time.
---

# Workbench HTTP API reference

## Summary

`netstead app` serves one Workbench `Session` as a local web app. The browser front end is only a
client of the routes below; anything it does, a script can do with `curl` or an HTTP library. The
route list is generated on every docs build from the FastAPI app's OpenAPI document
(`build_app(session).openapi()`), so it cannot drift from the code; a running server serves the same
document at `/openapi.json` and interactive docs at `/docs`.

Every state change goes through one route, `POST /api/actions`, whose body is one Action (a JSON
object discriminated on its `type`). The Action schemas are on the
[Workbench Actions](workbench-actions.md) page; `GET /api/actions` returns the JSON schema of every
Action the running session accepts, plugin Actions included. Reads are plain `GET`s and never
recorded in the history.

### Events (`GET /api/events`)

The stream is Server-Sent Events (`text/event-stream`). It opens with a `state` event carrying the
full session snapshot (the same payload as `GET /api/state`) and then carries every event the
session publishes, each frame named by its `type`:

- `state` — a fresh snapshot after any change (open, close, selection, settings, edits).
- `history` — one recorded Action, in the shape `GET /api/history` lists.
- `job` — a background job's snapshot at each stage change (open, build, validate, save and Ollama
  pulls run as jobs; `POST /api/actions` answers `202` with the job and the outcome arrives here).
- `llm` — provider and key status after a key is set or removed or a model pulled.
- `navigate` — a map navigation request raised by a `Navigate` Action.

A `: keepalive` comment is sent after 15 seconds of silence so proxies keep the connection open;
`?max_events=N` closes the stream after `N` events, for scripts and tests.

### Local-bind guards

On the default loopback bind the server answers `400` to any request whose `Host` header is not
`localhost`, `127.0.0.1` or `[::1]`, and `403` to any state-changing request that names a foreign
`Origin` or arrives `Sec-Fetch-Site: cross-site`; see
[Explore in the Workbench](../cookbook/workbench.md#quick-start) for what that means when you
expose the app.

### Plugin routes

Each installed plugin's router is mounted under `/api/plugins/<id>/` and its static files under
`/plugins/<id>/`. Those routes depend on what is installed, so they are not listed here;
`GET /api/plugins` names the installed plugins and their state.

## Routes

{% raw %}
{routes}
{% endraw %}

## Examples

Read the session state, apply a no-argument Action, and watch one event from the stream. The
server must be running (`netstead app`) on its default port.

```bash
curl http://127.0.0.1:8850/api/state
curl -X POST http://127.0.0.1:8850/api/actions \\
  -H 'Content-Type: application/json' -d '{"type": "clear_selection"}'
curl -N 'http://127.0.0.1:8850/api/events?max_events=1'
```

## See also

- [Workbench Actions](workbench-actions.md) — the Action schemas `POST /api/actions` accepts.
- [Explore in the Workbench](../cookbook/workbench.md) — the app this API serves.
- [Write a Workbench plugin](../cookbook/workbench-plugins.md) — adding routes under `/api/plugins/<id>/`.
- [Settings reference](settings.md) — `app.host`, `app.port` and `io.allowed_roots`.
"""

_HTTP_MISSING_SERVER_NOTE = """\
!!! warning "Route list not generated"

    The `server` extra (FastAPI) was not installed when this site was built, so the route list is
    missing from this page. Install it with `pip install 'netstead[server]'` and rebuild the docs.
"""


def _is_network_data_route(path: str) -> bool:
    return path.startswith("/api/n/") and not _is_validation_route(path)


def _is_validation_route(path: str) -> bool:
    return path.startswith("/api/n/") and path.endswith(
        ("/issues", "/issues/markers", "/report.html", "/edits", "/delete-plan")
    )


_SESSION_ROUTES = {"/api/state", "/api/config", "/api/settings", "/api/history", "/api/actions", "/api/events"}
_IO_ROUTES = {"/api/fs/list", "/api/check-url", "/api/geocode", "/api/estimate"}

_HTTP_GROUPS: list[RouteGroup] = [
    RouteGroup(
        "Session",
        "The session as a whole: its state, settings, history and the Action bus.",
        lambda path, _: path in _SESSION_ROUTES,
    ),
    RouteGroup(
        "Jobs",
        "Background jobs started by job Actions (open, build, validate, save) and by Ollama pulls.",
        lambda path, _: path.startswith("/api/jobs"),
    ),
    RouteGroup(
        "Files, URLs and build estimates",
        "What the **Open / Import** dialog calls: folders inside `io.allowed_roots`, remote GMNS URLs, "
        "place search, and sizing a `build_network` request before approving it.",
        lambda path, _: path in _IO_ROUTES,
    ),
    RouteGroup(
        "Network data",
        "Per open network. `{net_id}` is the id `GET /api/state` lists for the network; `{component}` is "
        "the component within it (`roadway` for the GMNS tables).",
        lambda path, _: _is_network_data_route(path),
    ),
    RouteGroup(
        "Validation and edits",
        "The last validation run's issues and report, and the pending-edit ledger.",
        lambda path, _: _is_validation_route(path),
    ),
    RouteGroup("Selection", "", lambda path, _: path.startswith("/api/selection")),
    RouteGroup(
        "Assistant",
        "The natural-language assistant: ask, then approve or reject the draft it proposes.",
        lambda path, _: path.startswith("/api/assistant"),
    ),
    RouteGroup(
        "Language models",
        "Provider status, model lists and write-only keys. Key writes are refused on a non-loopback bind.",
        lambda path, _: path.startswith("/api/llm"),
    ),
    RouteGroup("Plugins", "", lambda path, _: path.startswith("/api/plugins")),
]


def _render_cli_reference() -> str:
    """The CLI page: hand-written intro + the generated command tree."""
    from netstead.cli.app import app
    from typer.main import get_command

    commands = click_to_md(get_command(app), prog="netstead", heading_level=3)
    return _CLI_PAGE.replace("{commands}", commands.rstrip())


def _render_http_reference() -> str:
    """The HTTP page: hand-written intro + routes from the live OpenAPI document.

    Degrades to a visible warning (instead of failing the build) when the
    ``server`` extra is missing, since ``build_app`` needs FastAPI.
    """
    try:
        from netstead.workbench import Session, build_app

        spec = build_app(Session()).openapi()
    except ImportError as exc:
        logger.warning("Workbench HTTP API reference: route list skipped (%s)", exc)
        routes = _HTTP_MISSING_SERVER_NOTE
    else:
        routes = openapi_to_md(
            spec,
            groups=_HTTP_GROUPS,
            heading_level=3,
            skip=lambda path, _: path == "/" or path.startswith(("/static", "/plugins/")),
        )
    return _HTTP_PAGE.replace("{routes}", routes.rstrip())


def _write_reference_pages(docs_dir: Path) -> None:
    """Write ``reference/cli.md`` and ``reference/workbench-http-api.md`` into ``docs_dir``."""
    ref_dir = docs_dir / "reference"
    ref_dir.mkdir(exist_ok=True)
    (ref_dir / "cli.md").write_text(_render_cli_reference(), encoding="utf-8")
    (ref_dir / "workbench-http-api.md").write_text(_render_http_reference(), encoding="utf-8")


def _extract_live_nav(env_conf: dict) -> list[dict] | None:
    """Flatten ``env.conf["nav"]`` into {section, pages} shape.

    Recurses through nested dict/string/list entries. External URLs in
    the nav (used in this site to link to the corral site and the
    umbrella) are skipped — they don't belong in the llms.txt artifact
    for this site.
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
    """Shift every markdown heading down by one level (H1→H2, H2→H3, …)."""
    for n in (5, 4, 3, 2, 1):
        md = re.sub(_md_heading_re[n], r"#\1\2", md)
    return md


def _load_local_package(version: str):
    """Load a vendored ``packages/netstead/netstead/spec/<version>/datapackage.json``."""
    pkg_path = VENDORED_SPEC_ROOT / version / "datapackage.json"
    return load_package(pkg_path)


def _load_official_package(branch: str):
    """Load the upstream Frictionless package for a GMNS branch.

    Falls back to the vendored ``0.97`` (with a logger.warning) when the
    network load fails — keeps docs builds green in offline environments.
    """
    url = f"{OFFICIAL_BASE}/{branch}/Specification_md/datapackage.json"
    try:
        return load_package(url)
    except SpecLoadError as e:
        logger.warning("Falling back to vendored 0.97 for branch=%r: %s", branch, e)
        return _load_local_package(DEFAULT_LOCAL_VERSION)


def _docs_dir(env: Any) -> Path:
    """Resolve the mkdocs docs_dir from the macros env, with a fallback."""
    try:
        return Path(env.conf["docs_dir"])
    except (AttributeError, KeyError, TypeError):
        return PACKAGE_ROOT / "docs"


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
    """Register mkdocs-macros macros + emit AI-docgen artifacts at build time.

    Same shape as the repo-root ``main.py`` was, scoped now to the
    netstead site only. ``env`` is the ``MacrosPlugin`` instance.
    """
    docs_dir = _docs_dir(env)
    env_conf = getattr(env, "conf", {}) if hasattr(env, "conf") else {}
    site_url = (env_conf.get("site_url") if isinstance(env_conf, dict) else None) or SITE_URL
    nav = _extract_live_nav(env_conf) or _DEFAULT_NAV
    # Reference pages first: llms-full.txt reads page bodies from disk, so
    # they must exist before the AI artifacts are written. A failure here
    # is a real docs bug and should fail the build (no try/except).
    _write_reference_pages(docs_dir)
    try:
        _write_ai_artifacts(docs_dir, nav, site_url)
    except Exception as exc:
        logger.warning("AI docgen artifact emission failed: %s", exc)

    @env.macro
    def include_file(
        filename: str,
        downshift_h1: bool = True,
        start_line: int = 0,
        end_line: int | None = None,
    ) -> str:
        """Verbatim file inclusion macro with link rewriting.

        ``filename`` is resolved relative to the repo root so that
        README, CONTRIBUTING, LICENSE etc. can be pulled into docs.
        """
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

    @env.macro
    def local_frictionless_spec(version: str = DEFAULT_LOCAL_VERSION) -> str:
        """Package overview markdown for the vendored GMNS ``version``."""
        logger.info("local_frictionless_spec(version=%r)", version)
        return package_to_md(_load_local_package(version))

    @env.macro
    def local_frictionless_schemas(version: str = DEFAULT_LOCAL_VERSION) -> str:
        """Per-schema markdown tables for the vendored GMNS ``version``."""
        logger.info("local_frictionless_schemas(version=%r)", version)
        return schemas_to_md(_load_local_package(version))

    @env.macro
    def official_frictionless_spec(branch: str = "master") -> str:
        """Package overview for the upstream GMNS Frictionless package."""
        logger.info("official_frictionless_spec(branch=%r)", branch)
        return package_to_md(_load_official_package(branch))

    @env.macro
    def official_frictionless_schemas(branch: str = "master") -> str:
        """Per-schema tables for the upstream GMNS Frictionless package."""
        logger.info("official_frictionless_schemas(branch=%r)", branch)
        return schemas_to_md(_load_official_package(branch))
