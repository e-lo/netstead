"""Markdown documentation generators for Frictionless data packages.

Pure functions over the :mod:`corral.spec` model: given a
:class:`~corral.spec.DataPackage`, :class:`~corral.spec.Schema`,
or :class:`~corral.spec.Field`, return a markdown string. No I/O, no
engine, no pandas, no ``frictionless`` library dependency — the v0.3
docgen leaned on ``frictionless.Schema(...).to_markdown()`` and a
pandas ``to_markdown(index=False)``; this port reproduces the same
output shape from string concatenation alone, which keeps the function
trivially callable from anywhere (notebook, mkdocs macro, CLI).

The renderer covers the same surface the v0.3 generators produced:

- One H2 section per resource with a markdown field-table (name, type,
  constraints, description).
- Foreign keys listed as a bullet list under the resource's H2 when
  present.
- A top-level package overview with name / title / version / profile
  and a resources list.

What is **not** generated here lives in sibling docgen modules:

- ``llms.txt`` / ``llms-full.txt`` / ``ai/api-index.json`` →
  :mod:`corral.docgen.llms`.
- Interactive HTML reports → :mod:`corral.reports.render`.

Examples:
    Render the package overview and per-schema tables for a small
    inline data package:

    >>> from corral.spec import (
    ...     DataPackage, Resource, Schema, Field, Constraints,
    ... )
    >>> pkg = DataPackage(
    ...     name="demo",
    ...     resources=[
    ...         Resource(
    ...             name="node",
    ...             path="node.csv",
    ...             schema=Schema(
    ...                 fields=[
    ...                     Field(name="node_id", type="any",
    ...                           constraints=Constraints(required=True)),
    ...                 ],
    ...                 primary_key="node_id",
    ...             ),
    ...         ),
    ...     ],
    ... )
    >>> "## node" in schemas_to_md(pkg)
    True
    >>> "demo" in package_to_md(pkg)
    True
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from corral.spec import DataPackage, Field, Schema

__all__ = ["field_to_md_row", "package_to_md", "schemas_to_md"]


# ---------------------------------------------------------------------------
# Field-level helpers
# ---------------------------------------------------------------------------


def _format_constraints(field: Field) -> str:
    """Compactly summarize a field's constraints in one cell.

    Returns the empty string if no constraints are set. Each present
    facet contributes one comma-separated fragment: ``required``,
    ``unique``, ``min=N``, ``max=N``, ``len=[a..b]``, ``pattern=…``,
    ``enum=[a, b, c]``. Long enums are truncated to keep the table
    readable; the full list lives in the underlying schema JSON.
    """
    c = field.constraints
    if c is None:
        return ""
    parts: list[str] = []
    if c.required:
        parts.append("required")
    if c.unique:
        parts.append("unique")
    if c.minimum is not None:
        parts.append(f"min={c.minimum}")
    if c.maximum is not None:
        parts.append(f"max={c.maximum}")
    if c.min_length is not None or c.max_length is not None:
        lo = "" if c.min_length is None else str(c.min_length)
        hi = "" if c.max_length is None else str(c.max_length)
        parts.append(f"len=[{lo}..{hi}]")
    if c.pattern:
        parts.append(f"pattern=`{c.pattern}`")
    if c.enum is not None:
        # Render up to 6 enum values inline; longer lists become a count
        # so the table stays scannable. Values are stringified by their
        # natural repr-less form (no quotes around strings, no list
        # brackets-with-quotes that Python's repr would emit).
        vals = list(c.enum)
        shown = ", ".join(str(v) for v in vals[:6])
        if len(vals) > 6:
            shown += f", … (+{len(vals) - 6} more)"
        parts.append(f"enum=[{shown}]")
    return "; ".join(parts)


def _escape_cell(text: str) -> str:
    """Escape a value for safe inclusion in a markdown table cell.

    Pipes break tables, newlines break rows; both get neutralized. We
    intentionally do **not** HTML-escape — markdown renderers handle
    inline formatting on their own, and the descriptions in real-world
    Frictionless specs use markdown intentionally.
    """
    return text.replace("|", r"\|").replace("\n", " ").replace("\r", " ")


def field_to_md_row(field: Field) -> str:
    """Render a :class:`~corral.spec.Field` as one markdown table row.

    Columns (in order): ``Field``, ``Type``, ``Constraints``,
    ``Description``. The returned string starts and ends with a pipe
    and contains no trailing newline so callers can join rows with
    a newline separator.

    Examples:
        >>> from corral.spec import Field, Constraints
        >>> field_to_md_row(Field(name="x", type="number"))
        '| `x` | number |  |  |'
        >>> "required" in field_to_md_row(
        ...     Field(name="id", type="any",
        ...           constraints=Constraints(required=True))
        ... )
        True
    """
    name = f"`{field.name}`"
    type_ = field.type or ""
    constraints = _format_constraints(field)
    description = _escape_cell(field.description or "")
    return f"| {name} | {type_} | {constraints} | {description} |"


# ---------------------------------------------------------------------------
# Schema-level helpers
# ---------------------------------------------------------------------------


_FIELD_TABLE_HEADER = "| Field | Type | Constraints | Description |\n| --- | --- | --- | --- |"


def _fk_target(fields: str | list[str]) -> str:
    """Stringify a foreign-key fields value (str or list)."""
    if isinstance(fields, list):
        return ", ".join(f"`{f}`" for f in fields)
    return f"`{fields}`"


def _schema_to_md(name: str, schema: Schema, *, description: str | None = None) -> str:
    """Render one named schema as ``## {name}`` + description + table + FK list.

    ``description`` (when provided) is the resource-level description
    from the data-package descriptor; we prefer it over the schema's
    own description because data packages typically document the table
    on the resource entry.
    """
    lines: list[str] = [f"## {name}", ""]

    desc = description or schema.description
    if desc:
        lines.append(desc.strip())
        lines.append("")

    if schema.primary_key is not None:
        pk = schema.primary_key
        pk_str = ", ".join(f"`{f}`" for f in pk) if isinstance(pk, list) else f"`{pk}`"
        lines.append(f"**Primary key:** {pk_str}")
        lines.append("")

    lines.append(_FIELD_TABLE_HEADER)
    for field in schema.fields:
        lines.append(field_to_md_row(field))

    if schema.foreign_keys:
        lines.append("")
        lines.append("**Foreign keys:**")
        lines.append("")
        for fk in schema.foreign_keys:
            local = _fk_target(fk.fields)
            target_resource = fk.reference.resource or name  # "" = self-ref
            target_fields = _fk_target(fk.reference.fields)
            lines.append(f"- {local} → `{target_resource}`.{target_fields}")

    lines.append("")
    return "\n".join(lines)


def schemas_to_md(package: DataPackage) -> str:
    """Render every :class:`~corral.spec.Schema` in ``package`` as markdown.

    Output shape: one ``## <resource-name>`` section per resource, each
    containing the resource description (when present), the primary
    key, a markdown field-table, and a foreign-keys bullet list (when
    present). Resources whose ``table_schema`` is missing or is still
    an unresolved string reference are skipped silently — the public
    loader resolves these on load, so a string reference at this point
    indicates a hand-constructed package that didn't go through
    :func:`~corral.spec.load_package`.

    Args:
        package: A fully resolved :class:`~corral.spec.DataPackage`.

    Returns:
        A single markdown string. Sections are separated by blank
        lines; the result has no leading whitespace but ends with a
        trailing newline so concatenation with other markdown blocks
        is well-behaved.

    Examples:
        >>> from corral.spec import (
        ...     DataPackage, Resource, Schema, Field,
        ... )
        >>> pkg = DataPackage(
        ...     name="demo",
        ...     resources=[
        ...         Resource(name="x", path="x.csv",
        ...                  schema=Schema(fields=[Field(name="a", type="any")])),
        ...     ],
        ... )
        >>> md = schemas_to_md(pkg)
        >>> "## x" in md and "`a`" in md
        True
    """
    # Local import keeps the module import-cycle-safe: spec models import
    # nothing from docgen, but docgen tests construct spec instances.
    from corral.spec import Schema

    sections: list[str] = []
    for resource in package.resources:
        schema = resource.table_schema
        if not isinstance(schema, Schema):
            # Either None or an unresolved string reference; skip.
            continue
        sections.append(_schema_to_md(resource.name, schema, description=resource.description))
    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Package-level overview
# ---------------------------------------------------------------------------


def package_to_md(package: DataPackage) -> str:
    """Render the package-level overview as markdown.

    The overview contains:

    - An H1 with the package title (or ``name`` if title is absent).
    - A metadata block listing name, version, profile, and homepage
      when present.
    - The package description as a paragraph.
    - A resources table: name, required (when known), path, short
      description (first sentence / first 120 chars of resource
      description).

    Per-table field documentation lives in :func:`schemas_to_md` — this
    function is the top-of-page summary; the schemas section follows
    underneath in the mkdocs macro wiring.

    Args:
        package: A loaded :class:`~corral.spec.DataPackage`.

    Returns:
        A markdown string, no leading whitespace, trailing newline.

    Examples:
        >>> from corral.spec import DataPackage, Resource
        >>> pkg = DataPackage(
        ...     name="demo",
        ...     title="Demo",
        ...     version="0.1",
        ...     resources=[Resource(name="x", path="x.csv")],
        ... )
        >>> md = package_to_md(pkg)
        >>> "Demo" in md and "0.1" in md and "x" in md
        True
    """
    lines: list[str] = []

    title = package.title or package.name or "Data Package"
    lines.append(f"# {title}")
    lines.append("")

    meta: list[str] = []
    if package.name and package.name != title:
        meta.append(f"**Name:** `{package.name}`")
    if package.version:
        meta.append(f"**Version:** `{package.version}`")
    if package.profile:
        meta.append(f"**Profile:** `{package.profile}`")
    if package.homepage:
        meta.append(f"**Homepage:** <{package.homepage}>")
    if meta:
        lines.extend(meta)
        lines.append("")

    if package.description:
        lines.append(package.description.strip())
        lines.append("")

    lines.append("## Resources")
    lines.append("")
    lines.append("| Resource | Required | Path | Description |")
    lines.append("| --- | --- | --- | --- |")
    for r in package.resources:
        # schemas_to_md renders each resource as ``## <name>``, and the
        # toc slug keeps underscores, so the anchor is the name verbatim.
        name_cell = f"[`{r.name}`](#{r.name})"
        required_cell = "yes" if r.required else ""
        path_cell = _format_path(r.path)
        desc = _escape_cell(_short_description(r.description or ""))
        lines.append(f"| {name_cell} | {required_cell} | {path_cell} | {desc} |")

    lines.append("")
    return "\n".join(lines)


def _format_path(path: str | list[str] | None) -> str:
    """Render a resource path (string or list) as a markdown cell."""
    if path is None:
        return ""
    if isinstance(path, list):
        return ", ".join(f"`{p}`" for p in path)
    return f"`{path}`"


def _short_description(text: str) -> str:
    """First sentence or first 160 chars of ``text``, whichever is shorter.

    Resource descriptions in real-world specs (GMNS in particular) are
    multi-sentence prose with embedded HTML; we want a one-line cell.
    Anything past the first sentence is dropped — the per-resource
    schemas section repeats the full description.
    """
    text = text.strip()
    if not text:
        return ""
    # Sentence boundary: ``. `` after a non-space character. Falls back
    # to the 160-char cap if no boundary appears.
    cap = 160
    end = -1
    for marker in (". ", "! ", "? "):
        idx = text.find(marker)
        if idx != -1 and (end == -1 or idx < end):
            end = idx + 1
    if end == -1 or end > cap:
        end = cap
    short = text[:end].rstrip()
    if len(text) > end:
        short += "…"
    return short
