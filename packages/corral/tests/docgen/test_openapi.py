"""Tests for :func:`corral.docgen.openapi_to_md` — route reference from an OpenAPI document."""

from __future__ import annotations

from corral.docgen import RouteGroup, openapi_to_md

SPEC = {
    "openapi": "3.1.0",
    "paths": {
        "/": {"get": {"summary": "Index", "responses": {"200": {"description": "Successful Response"}}}},
        "/api/state": {"get": {"summary": "State", "responses": {"200": {"description": "Successful Response"}}}},
        "/api/actions": {
            "post": {
                "summary": "Actions",
                "description": "Apply an action.\n\nA job action answers 202.",
                "requestBody": {"content": {"application/json": {"schema": {"title": "Body"}}}},
                "responses": {
                    "200": {"description": "Successful Response"},
                    "422": {"description": "Validation Error"},
                },
            }
        },
        "/api/n/{net_id}/rows": {
            "post": {
                "summary": "Rows",
                "parameters": [{"name": "net_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                "requestBody": {
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/RowsQuery"}}}
                },
                "responses": {"200": {"description": "Successful Response"}},
            }
        },
        "/api/misc": {"get": {"summary": "Misc", "responses": {"200": {"description": "Successful Response"}}}},
    },
    "components": {
        "schemas": {
            "RowsQuery": {
                "title": "RowsQuery",
                "description": "One page of a table.",
                "type": "object",
                "required": ["sort"],
                "properties": {
                    "offset": {"type": "integer", "default": 0},
                    "sort": {"anyOf": [{"type": "string"}, {"type": "null"}], "description": "Sort <column>."},
                    "dir": {"type": "string", "enum": ["asc", "desc"], "default": "asc"},
                    "ids": {"anyOf": [{"type": "array", "items": {"type": "integer"}}, {"type": "null"}]},
                },
            },
            "HTTPValidationError": {"type": "object"},
            "ValidationError": {"type": "object"},
        }
    },
}

GROUPS = [
    RouteGroup("Session", "The session as a whole.", lambda path, _: path in {"/api/state", "/api/actions"}),
    RouteGroup("Network data", "", lambda path, _: path.startswith("/api/n/")),
]


def test_groups_in_order_with_other_bucket() -> None:
    md = openapi_to_md(SPEC, groups=GROUPS, skip=lambda path, _: path == "/")
    assert md.index("### Session") < md.index("### Network data") < md.index("### Other routes")
    assert "#### `GET /api/state`" in md
    assert "#### `GET /api/misc`" in md
    assert "`GET /`" not in md
    assert "The session as a whole." in md


def test_operation_details() -> None:
    md = openapi_to_md(SPEC, groups=GROUPS)
    assert "Apply an action.\n\nA job action answers 202." in md
    assert "Request body: a JSON object (free-form; see the description)." in md
    assert "Request body: [`RowsQuery`](#rowsquery) (JSON)." in md
    assert "| `net_id` | path | string | yes |  |" in md
    assert "Responses: `200` Successful Response · `422` Validation Error" in md


def test_schema_section_skips_framework_schemas() -> None:
    md = openapi_to_md(SPEC, groups=GROUPS)
    assert "### Request schemas" in md
    assert "#### `RowsQuery`" in md
    assert "HTTPValidationError" not in md
    assert "| `offset` | integer | no | `0` |  |" in md
    assert "| `sort` | string or null | yes |  | Sort &lt;column&gt;. |" in md
    assert "| `dir` | `asc` / `desc` | no | `'asc'` |  |" in md
    assert "| `ids` | array of integer or null | no |  |  |" in md


def test_groups_by_tag_when_no_groups_given() -> None:
    spec = {"paths": {"/x": {"get": {"tags": ["Things"], "responses": {}}}, "/y": {"get": {"responses": {}}}}}
    md = openapi_to_md(spec)
    assert "### Things" in md and "### Routes" in md
