"""Documentation generators for human + AI consumers.

Architecture §6.9. Two clusters of generators:

* :mod:`corral.docgen.markdown` — v1.0 port of the v0.3
  ``netstead.schema.document_*_to_md`` functions, now generic over the
  spec model. Used by the mkdocs site for human-readable spec pages.
* :mod:`corral.docgen.llms` — AI-consumable static artifacts
  (``llms.txt``, ``llms-full.txt``, ``ai/api-index.json``) emitted at
  ``mkdocs build`` time via the project's mkdocs ``main.py`` hook.
* :mod:`corral.docgen.cli` and :mod:`corral.docgen.openapi` — reference
  pages generated from a live click/typer command tree and from a
  FastAPI app's OpenAPI document, also emitted from ``main.py`` so the
  CLI and HTTP references cannot drift from the code.
"""

from .cli import click_to_md
from .llms import (
    API_INDEX_SCHEMA_VERSION,
    generate_api_index_json,
    generate_llms_full_txt,
    generate_llms_txt,
)
from .markdown import field_to_md_row, package_to_md, schemas_to_md
from .openapi import RouteGroup, openapi_to_md

__all__ = [
    "API_INDEX_SCHEMA_VERSION",
    "RouteGroup",
    "click_to_md",
    "field_to_md_row",
    "generate_api_index_json",
    "generate_llms_full_txt",
    "generate_llms_txt",
    "openapi_to_md",
    "package_to_md",
    "schemas_to_md",
]
