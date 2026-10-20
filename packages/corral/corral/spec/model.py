"""Pydantic v2 models for the Frictionless data-package object graph.

These models cover the subset of the Frictionless Table Schema and Data
Package specifications that corral cares about, plus the common
``shared_categories.json`` convention used by some pre-1.0 data
packages (notably GMNS 0.97+).

All models permit unknown properties (``extra="allow"``) so that custom
Frictionless extensions and forward-compatible additions do not raise.
JSON aliases are declared where the on-disk spec uses camelCase
(``primaryKey``, ``foreignKeys``, ``missingValues``, ``fieldsMatch``).

References:
    https://specs.frictionlessdata.io/table-schema/
    https://datapackage.org/standard/data-package/
"""

from __future__ import annotations

import warnings
from typing import Any

from pydantic import BaseModel, ConfigDict
from pydantic import Field as PydField

__all__ = [
    "Constraints",
    "DataPackage",
    "Field",
    "ForeignKey",
    "ForeignKeyReference",
    "MissingValues",
    "Resource",
    "Schema",
    "SharedCategory",
]


# Frictionless allows ``missingValues`` to be a plain JSON array of
# strings interpreted as nulls. We expose a thin alias so callers can
# reference a name rather than ``list[str]``; the loader carries values
# through unchanged.
MissingValues = list[str]


class _Base(BaseModel):
    """Common Pydantic config for all spec models.

    - ``extra="allow"`` keeps unknown properties around in
      ``model.__pydantic_extra__`` rather than raising; this is essential
      for forward-compatible parsing of new Frictionless properties or
      vendor extensions.
    - ``populate_by_name=True`` lets us accept either the alias
      (camelCase JSON keys) or the snake_case Python attribute name
      when constructing instances in tests and round-trips.
    - ``str_strip_whitespace=True`` is conservative cleanup on string
      fields without changing semantics.
    """

    model_config = ConfigDict(
        extra="allow",
        populate_by_name=True,
        str_strip_whitespace=True,
    )


class Constraints(_Base):
    """Field-level validation constraints (Frictionless ``constraints``).

    All attributes are optional; only the ones present in the source
    JSON are populated. Unknown keys are preserved by ``extra="allow"``.

    Attributes:
        required: When true, the field must be present and non-null.
        unique: When true, all non-null values must be unique.
        minimum: Inclusive lower bound (numeric or temporal).
        maximum: Inclusive upper bound (numeric or temporal).
        min_length: Minimum string length.
        max_length: Maximum string length.
        pattern: Regular expression the value must match.
        enum: Permitted values. May be empty (no values allowed).

    Examples:
        >>> Constraints(required=True, enum=["a", "b"]).enum
        ['a', 'b']
        >>> Constraints().required is None
        True
    """

    required: bool | None = None
    unique: bool | None = None
    minimum: float | int | str | None = None
    maximum: float | int | str | None = None
    min_length: int | None = PydField(default=None, alias="minLength")
    max_length: int | None = PydField(default=None, alias="maxLength")
    pattern: str | None = None
    enum: list[Any] | None = None


class Field(_Base):
    """A single column in a table schema (Frictionless ``Field``).

    Attributes:
        name: Column name.
        type: Frictionless type name (``string``, ``integer``,
            ``number``, ``boolean``, ``any``, etc.).
        format: Type-specific format hint (e.g. ``"default"``, ``"uri"``).
        title: Human-readable label.
        description: Long-form description.
        constraints: Optional :class:`Constraints` block.

    Examples:
        >>> f = Field(name="link_id", type="any", constraints=Constraints(required=True))
        >>> f.name, f.type, f.constraints.required
        ('link_id', 'any', True)
    """

    name: str
    type: str | None = None
    format: str | None = None
    title: str | None = None
    description: str | None = None
    constraints: Constraints | None = None


class ForeignKeyReference(_Base):
    """The ``reference`` block inside a foreign key.

    A ``resource`` of ``""`` (empty string) denotes a self-reference
    (the foreign key points at another row of the same resource).

    Attributes:
        resource: Target resource name, or ``""`` for self-reference.
        fields: Target field name or list of field names.

    Examples:
        >>> ForeignKeyReference(resource="node", fields="node_id").resource
        'node'
        >>> ForeignKeyReference(resource="", fields="link_id").resource
        ''
    """

    resource: str
    fields: str | list[str]


class ForeignKey(_Base):
    """A foreign-key declaration on a table schema.

    Attributes:
        fields: Local field name or list of names participating in the
            foreign key.
        reference: The :class:`ForeignKeyReference` describing the target.

    Examples:
        >>> fk = ForeignKey(
        ...     fields="from_node_id",
        ...     reference=ForeignKeyReference(resource="node", fields="node_id"),
        ... )
        >>> fk.fields
        'from_node_id'
        >>> fk.reference.resource
        'node'
    """

    fields: str | list[str]
    reference: ForeignKeyReference


class Schema(_Base):
    """A Frictionless Table Schema describing one tabular resource.

    Attributes:
        fields: Ordered list of column declarations.
        primary_key: One field name or a list of names forming the
            primary key. May be ``None`` for schemaless tables.
        foreign_keys: List of foreign-key declarations. Defaults to an
            empty list rather than ``None`` so callers can iterate
            without a None-check.
        missing_values: Strings the reader should interpret as null.
            Like ``foreign_keys`` it is always a list, but its default is
            Frictionless's ``[""]`` (an empty cell is null), not ``[]``.
            The two differ on the wire: an absent ``missingValues`` gets
            the default, while an explicit ``missingValues: []`` means
            no string is read as null.
        name: Optional schema name (some specs include this for
            convenience; not part of strict Frictionless).
        title: Human-readable title.
        description: Long-form description.

    Examples:
        >>> s = Schema(fields=[Field(name="id", type="integer")], primary_key="id")
        >>> [f.name for f in s.fields]
        ['id']
        >>> s.primary_key
        'id'
        >>> Schema(fields=[]).foreign_keys
        []
        >>> Schema(fields=[]).missing_values
        ['']
    """

    fields: list[Field]
    primary_key: str | list[str] | None = PydField(default=None, alias="primaryKey")
    foreign_keys: list[ForeignKey] = PydField(default_factory=list, alias="foreignKeys")
    missing_values: MissingValues = PydField(default_factory=lambda: [""], alias="missingValues")
    name: str | None = None
    title: str | None = None
    description: str | None = None


class Resource(_Base):
    """One tabular resource within a data package.

    **JSON key vs. Python attribute (read this).** The on-disk JSON
    uses Frictionless's name ``"schema"``, but in Python the attribute
    is named :attr:`table_schema`. The rename avoids shadowing
    Pydantic v2's deprecated ``BaseModel.schema()`` method, which
    would otherwise mean ``resource.schema`` silently returns a bound
    method object instead of the schema data. Reading the raw JSON,
    it is natural to write ``r.schema`` — but that returns the
    deprecated method, NOT the schema. Use :attr:`table_schema` in
    Python; use ``model_dump(by_alias=True)["schema"]`` when
    round-tripping to JSON. Accessing ``r.schema`` on an instance
    emits a :class:`FutureWarning` pointing at the correct name.

    The ``table_schema`` attribute may be either a string reference (a
    path to a sibling ``.schema.json`` file as it appears in the raw
    JSON) or an inline :class:`Schema`. The loader resolves string
    references and replaces them with the parsed :class:`Schema`
    before returning.

    Attributes:
        name: Resource name (unique within the package).
        path: Relative file path or list of paths.
        table_schema: A schema reference string or an inline
            :class:`Schema`. JSON alias: ``"schema"``.
        type: Resource type hint (e.g. ``"table"``).
        format: File format (e.g. ``"csv"``).
        mediatype: MIME type.
        encoding: Character encoding.
        title: Human-readable title.
        description: Long-form description.
        required: Whether this resource is required by the package.
        dialect: Frictionless dialect (CSV delimiters, etc.) — kept as
            a free-form mapping to avoid pulling in the full dialect
            spec.

    Examples:
        Construct with the JSON key ``schema=`` (alias) and read with
        the Python attribute :attr:`table_schema`:

        >>> r = Resource(name="link", path="link.csv", schema=Schema(fields=[]))
        >>> r.name, r.path
        ('link', 'link.csv')
        >>> r.table_schema is not None
        True

        Round-trip to JSON uses the alias key ``"schema"``:

        >>> r.model_dump(by_alias=True)["schema"] is not None
        True
    """

    # The attribute is exposed as ``schema`` in the on-disk JSON
    # (Frictionless's name) but bound to ``table_schema`` in Python to
    # avoid shadowing the deprecated ``BaseModel.schema()`` method,
    # which Pydantic v2 still warns about. Callers should use
    # ``Resource(schema=...)`` and ``resource.table_schema`` (or
    # ``model_dump(by_alias=True)`` to round-trip to the original key).
    name: str
    path: str | list[str] | None = None
    table_schema: str | Schema | None = PydField(default=None, alias="schema")
    type: str | None = None
    format: str | None = None
    mediatype: str | None = None
    encoding: str | None = None
    title: str | None = None
    description: str | None = None
    required: bool | None = None
    dialect: dict[str, Any] | None = None

    def __getattribute__(self, name: str) -> Any:
        """Intercept ``.schema`` access to warn about the rename footgun.

        Falls through to default attribute access for every other name.
        See class docstring for the rationale.
        """
        # Warn loudly when a caller reads ``.schema`` on an instance:
        # they almost certainly meant ``.table_schema`` (the JSON key
        # is ``"schema"``, and reading the raw JSON makes the mistake
        # natural). Without this guard the access silently returns
        # the bound ``BaseModel.schema`` method — no exception, no
        # warning, no value. Pydantic v2 never touches ``.schema`` on
        # instances during serialization (verified), so this override
        # has no false positives on normal model use.
        if name == "schema":
            warnings.warn(
                "Resource.schema returns the deprecated Pydantic "
                "BaseModel.schema method, not the schema data. Use "
                "Resource.table_schema instead (or "
                "model_dump(by_alias=True)['schema'] for the JSON form).",
                FutureWarning,
                stacklevel=2,
            )
        return super().__getattribute__(name)


class DataPackage(_Base):
    """A Frictionless Data Package.

    Attributes:
        name: Short package name (slug).
        title: Human-readable title.
        description: Long-form description.
        version: Package version string (free-form, not enforced).
        profile: Frictionless profile identifier.
        homepage: Project URL.
        resources: List of :class:`Resource` entries.
        licenses: License declarations.
        sources: Provenance / source list.
        contributors: Contributor list.
        keywords: Keyword tags.

    Examples:
        >>> pkg = DataPackage(
        ...     name="example",
        ...     resources=[Resource(name="link", path="link.csv")],
        ... )
        >>> pkg.name, len(pkg.resources)
        ('example', 1)
    """

    name: str | None = None
    title: str | None = None
    description: str | None = None
    version: str | None = None
    profile: str | None = None
    homepage: str | None = None
    resources: list[Resource]
    licenses: list[dict[str, Any]] | None = None
    sources: list[dict[str, Any]] | None = None
    contributors: list[dict[str, Any]] | None = None
    keywords: list[str] | None = None


class SharedCategory(_Base):
    """A named, reusable enum from a ``shared_categories.json`` file.

    Pre-1.0 data packages sometimes ship a sibling
    ``shared_categories.json`` defining named groups of allowed values
    that schemas reference via JSON pointer (e.g.
    ``"$ref": "shared_categories.json#/ctrl_type/categories"``). This
    model captures one such group.

    The ``categories`` payload may be either a flat list of scalar
    values or a list of ``{"value": ..., "label": ...}`` objects;
    the loader normalizes references to a concrete list before storing
    them on a :class:`Field`'s ``constraints.enum``.

    Attributes:
        categories: The list of allowed values (scalars or value/label
            objects).
        description: Human-readable description.
        source: Citation or upstream source URL.

    Examples:
        >>> SharedCategory(categories=["a", "b"], description="x").categories
        ['a', 'b']
    """

    categories: list[Any]
    description: str | None = None
    source: str | None = None
