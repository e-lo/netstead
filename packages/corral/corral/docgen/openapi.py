"""Markdown reference for an OpenAPI 3 document.

:func:`openapi_to_md` turns the dict a FastAPI app returns from
``app.openapi()`` into a markdown route reference: routes grouped by
caller-supplied :class:`RouteGroup` predicates (or by tag when none are
given), one sub-heading per operation with its description, parameters,
request-body schema and response codes, then one section per component
schema with a field table. Pure dict-walking; nothing is imported from
FastAPI, so the generator runs wherever the document does.

Examples:
    >>> spec = {
    ...     "paths": {"/api/state": {"get": {"summary": "State", "responses": {"200": {"description": "OK"}}}}},
    ...     "components": {"schemas": {}},
    ... }
    >>> md = openapi_to_md(spec, groups=[RouteGroup("Session", "", lambda path, method: path == "/api/state")])
    >>> "### Session" in md and "`GET /api/state`" in md
    True
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from ._text import escape_md, paragraphs

__all__ = ["RouteGroup", "openapi_to_md"]

#: Component schemas FastAPI adds for its own 422 payload; not worth a section.
_FRAMEWORK_SCHEMAS = {"HTTPValidationError", "ValidationError"}

_METHOD_ORDER = ("get", "post", "put", "patch", "delete", "head", "options")


@dataclass(frozen=True)
class RouteGroup:
    """A named group of routes: ``match(path, method)`` says whether a route belongs."""

    title: str
    description: str
    match: Callable[[str, str], bool]


def _schema_name(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def _schema_link(name: str) -> str:
    return f"[`{name}`](#{name.lower()})"


def _type_of(schema: dict[str, Any]) -> str:
    """A short human type for a JSON-schema fragment (``string``, ``array of integer``, ``A or null``)."""
    if "$ref" in schema:
        return _schema_link(_schema_name(schema["$ref"]))
    if "anyOf" in schema or "oneOf" in schema:
        variants = schema.get("anyOf") or schema.get("oneOf") or []
        return " or ".join(_type_of(v) for v in variants)
    if "enum" in schema:
        return " / ".join(f"`{v}`" for v in schema["enum"])
    kind = schema.get("type")
    if kind == "array":
        return f"array of {_type_of(schema.get('items', {}))}"
    if kind is None:
        return "any"
    return str(kind)


def _default_cell(schema: dict[str, Any]) -> str:
    return f"`{escape_md(repr(schema['default']))}`" if "default" in schema else ""


def _operations(spec: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    """Every ``(path, METHOD, operation)`` in document order, methods in conventional order."""
    out: list[tuple[str, str, dict[str, Any]]] = []
    for path, item in spec.get("paths", {}).items():
        for method in _METHOD_ORDER:
            if method in item:
                out.append((path, method.upper(), item[method]))
    return out


def _request_body(op: dict[str, Any]) -> str:
    body = op.get("requestBody")
    if not body:
        return ""
    content = body.get("content", {})
    schema = content.get("application/json", {}).get("schema") or next(iter(content.values()), {}).get("schema") or {}
    if "$ref" in schema:
        return f"Request body: {_schema_link(_schema_name(schema['$ref']))} (JSON)."
    if schema.get("type") == "array":
        return f"Request body: JSON {_type_of(schema)}."
    return "Request body: a JSON object (free-form; see the description)."


def _operation_md(path: str, method: str, op: dict[str, Any], level: int) -> list[str]:
    lines = [f"{'#' * level} `{method} {path}`", ""]
    description = op.get("description") or ""
    if description:
        for para in paragraphs(description):
            lines += [escape_md(para), ""]
    elif op.get("summary"):
        lines += [escape_md(op["summary"]) + ".", ""]
    params = op.get("parameters", [])
    if params:
        lines += ["| Parameter | In | Type | Required | Default |", "|---|---|---|---|---|"]
        for p in params:
            schema = p.get("schema", {})
            lines.append(
                f"| `{p['name']}` | {p.get('in', '')} | {_type_of(schema)} | "
                f"{'yes' if p.get('required') else 'no'} | {_default_cell(schema)} |"
            )
        lines.append("")
    body = _request_body(op)
    if body:
        lines += [body, ""]
    responses = op.get("responses", {})
    if responses:
        cells = [f"`{code}` {escape_md(r.get('description', ''))}".rstrip() for code, r in responses.items()]
        lines += ["Responses: " + " · ".join(cells), ""]
    return lines


def _schema_md(name: str, schema: dict[str, Any], level: int) -> list[str]:
    lines = [f"{'#' * level} `{name}`", ""]
    if schema.get("description"):
        for para in paragraphs(schema["description"]):
            lines += [escape_md(para), ""]
    props = schema.get("properties", {})
    required = set(schema.get("required", []))
    if props:
        lines += ["| Field | Type | Required | Default | Description |", "|---|---|---|---|---|"]
        for field, fschema in props.items():
            lines.append(
                f"| `{field}` | {_type_of(fschema)} | {'yes' if field in required else 'no'} | "
                f"{_default_cell(fschema)} | {escape_md(fschema.get('description', ''))} |"
            )
        lines.append("")
    return lines


def openapi_to_md(
    spec: dict[str, Any],
    *,
    groups: Sequence[RouteGroup] | None = None,
    heading_level: int = 3,
    skip: Callable[[str, str], bool] | None = None,
    schemas_title: str = "Request schemas",
) -> str:
    """Render an OpenAPI document as a grouped markdown route reference.

    Args:
        spec: The OpenAPI 3 document (``app.openapi()`` for a FastAPI app).
        groups: Ordered groups; the first whose ``match(path, method)`` is
            true claims the route. Routes no group claims land in a final
            "Other routes" group. ``None`` groups by the operation's first
            tag (untagged routes under "Routes").
        heading_level: Level of the group headings; operations and schemas
            sit one level deeper.
        skip: ``skip(path, method)`` true drops a route (the HTML front end,
            static files).
        schemas_title: Heading for the component-schema section.

    Returns:
        Markdown with one section per group and one per component schema
        (FastAPI's own validation-error schemas are left out).
    """
    ops = [(p, m, op) for p, m, op in _operations(spec) if not (skip and skip(p, m))]
    buckets: list[tuple[RouteGroup, list[tuple[str, str, dict[str, Any]]]]]
    if groups is None:
        by_tag: dict[str, list[tuple[str, str, dict[str, Any]]]] = {}
        for p, m, op in ops:
            by_tag.setdefault((op.get("tags") or ["Routes"])[0], []).append((p, m, op))
        buckets = [(RouteGroup(tag, "", lambda *_: True), routes) for tag, routes in by_tag.items()]
    else:
        buckets = [(g, []) for g in groups]
        other: list[tuple[str, str, dict[str, Any]]] = []
        for p, m, op in ops:
            for group, routes in buckets:
                if group.match(p, m):
                    routes.append((p, m, op))
                    break
            else:
                other.append((p, m, op))
        if other:
            buckets.append((RouteGroup("Other routes", "", lambda *_: True), other))

    lines: list[str] = []
    for group, routes in buckets:
        if not routes:
            continue
        lines += [f"{'#' * heading_level} {group.title}", ""]
        if group.description:
            lines += [group.description.strip(), ""]
        for p, m, op in routes:
            lines += _operation_md(p, m, op, heading_level + 1)

    schemas = {k: v for k, v in spec.get("components", {}).get("schemas", {}).items() if k not in _FRAMEWORK_SCHEMAS}
    if schemas:
        lines += [f"{'#' * heading_level} {schemas_title}", ""]
        for name, schema in schemas.items():
            lines += _schema_md(name, schema, heading_level + 1)
    return "\n".join(lines).rstrip() + "\n"
