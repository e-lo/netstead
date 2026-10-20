"""Compute engine (DuckDB via ibis) for lazy + eager table ops.

**DuckDB is the single compute engine.** pandas / polars / pyarrow are
input/output *formats* (see :meth:`Engine.to_pandas` / :meth:`Engine.to_polars`
and the Arrow input path), not compute backends — matching ibis's own move to
drop its non-SQL execution backends in 10.0.

The public surface is small:

- ``Engine`` — the structural protocol the engine implements.
- ``EngineNotAvailableError`` — raised when a backend dependency is missing.
- ``register_engine`` / ``get_engine`` / ``set_default_engine`` /
  ``list_engines`` — the in-process registry (one engine).

The engine implementation lives in ``ibis_engine.py`` (ibis on duckdb).
"""

from __future__ import annotations

from .base import (
    Engine,
    EngineNotAvailableError,
    InvalidEngineCallError,
    NativeFrame,
    SourceRef,
    TableExpr,
    UnsupportedSourceError,
)

# ---------------------------------------------------------------------------
# Registry state (module-level singleton, in-process)
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, Engine] = {}
_DEFAULT: str | None = None


def register_engine(engine: Engine, *, default: bool = False) -> None:
    """Register an engine instance under its ``name`` attribute.

    Re-registering an existing name overwrites the previous registration
    (no warning). This is intentional — it lets tests and notebooks swap
    in fakes without ceremony, and lets a downstream package replace
    the stock implementation.

    Args:
        engine: An instance satisfying the ``Engine`` protocol. Must
            expose a non-empty ``name`` string and the engine methods
            (``scan`` / ``materialize`` / ``to_pandas`` / ``to_polars``
            / ``to_arrow`` / ``write``).
        default: If ``True``, also make this the default engine
            returned by ``get_engine()`` with no argument.

    Raises:
        TypeError: If ``engine`` does not satisfy the ``Engine``
            protocol (missing one or more required methods).
        ValueError: If ``engine.name`` is empty.

    Examples:
        Register a minimal fake engine and look it up. The fake uses a
        unique name to avoid colliding with auto-registered engines:

        >>> from corral.engines import (
        ...     register_engine, get_engine, list_engines
        ... )
        >>> class _DoctestEngine:
        ...     name = "doctest-register"
        ...     def read_csv(self, source, schema=None, **kw): return None
        ...     def read_parquet(self, source, schema=None, **kw): return None
        ...     def read_duckdb_table(self, source, table, schema=None, **kw): return None
        ...     def from_records(self, records, schema=None): return None
        ...     def from_arrow(self, arrow_table): return None
        ...     def write_csv(self, expr, dest, **kw): return None
        ...     def write_parquet(self, expr, dest, **kw): return None
        ...     def write_duckdb_table(self, expr, dest, table, **kw): return None
        ...     def cast_schema(self, expr, schema): return expr
        ...     def scan(self, source, schema=None, **kw): return None
        ...     def write(self, expr, dest, fmt, **kw): return None
        ...     def materialize(self, expr): return None
        ...     def to_pandas(self, expr): return None
        ...     def to_polars(self, expr): return None
        ...     def to_arrow(self, expr): return None
        ...     def columns(self, expr): return []
        ...     def count(self, expr): return 0
        ...     def head(self, expr, n): return expr
        ...     def select(self, expr, columns): return expr
        ...     def order_by(self, expr, cols, descending=False): return expr
        ...     def limit(self, expr, n, offset=0): return expr
        >>> fake = _DoctestEngine()
        >>> try:
        ...     register_engine(fake)
        ...     get_engine("doctest-register") is fake
        ... finally:
        ...     from corral import engines as _eng
        ...     _ = _eng._REGISTRY.pop("doctest-register", None)
        True
    """
    global _DEFAULT
    if not isinstance(engine, Engine):
        raise TypeError(
            f"{engine!r} does not satisfy the Engine protocol "
            "(needs a 'name' attribute plus per-format primitives "
            "read_csv/read_parquet/read_duckdb_table/from_records/from_arrow and "
            "write_csv/write_parquet/write_duckdb_table, plus cast_schema, "
            "scan, write, materialize, to_pandas, to_polars, to_arrow, "
            "and the lazy-introspection methods columns/count/head/select/order_by/limit); "
            f"missing: {', '.join(_missing_members(engine))}"
        )
    if not getattr(engine, "name", None):
        raise ValueError("Engine.name must be a non-empty string")
    _REGISTRY[engine.name] = engine
    if default or _DEFAULT is None:
        _DEFAULT = engine.name


def _missing_members(engine: object) -> list[str]:
    """The :class:`Engine` protocol members ``engine`` lacks (absent, or ``None`` / not callable for a method)."""
    methods = sorted(n for n, v in vars(Engine).items() if callable(v) and not n.startswith("_"))
    missing = [] if hasattr(engine, "name") else ["name"]
    return missing + [n for n in methods if not callable(getattr(engine, n, None))]


def get_engine(name: str | None = None) -> Engine:
    """Return the registered engine for ``name``, or the default if ``None``.

    Args:
        name: The engine name (``"ibis"`` / ``"duckdb"`` — the one compute
            engine). ``None`` returns the default.

    Returns:
        The registered engine instance.

    Raises:
        EngineNotAvailableError: If the registry is empty, or the
            requested name is not registered. The message lists the
            currently-registered engine names so the caller can correct
            the typo.

    Examples:
        Look up the auto-registered default (``ibis`` on duckdb):

        >>> from corral.engines import get_engine
        >>> default = get_engine()
        >>> default.name
        'ibis'

        Looking up a name that is not registered raises a clear error:

        >>> from corral.engines import EngineNotAvailableError
        >>> try:
        ...     get_engine("not-a-real-engine")
        ... except EngineNotAvailableError as exc:
        ...     "not-a-real-engine" in str(exc)
        True
    """
    if not _REGISTRY:
        raise EngineNotAvailableError(
            "no engines registered — install corral with at least one engine "
            "(default install includes ibis-framework[duckdb])"
        )
    key = name if name is not None else _DEFAULT
    if key is None:  # pragma: no cover - guarded by the empty-registry check above
        raise EngineNotAvailableError("no default engine set and no name provided")
    if key not in _REGISTRY:
        available = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise EngineNotAvailableError(
            f"engine {key!r} is not registered (available: {available}). "
            f"DuckDB is the only compute engine; pandas/polars/arrow are I/O "
            f"formats (Table.to_pandas()/to_polars())."
        )
    return _REGISTRY[key]


def set_default_engine(name: str) -> None:
    """Set the default engine returned by ``get_engine()`` with no argument.

    Args:
        name: The name of an already-registered engine.

    Raises:
        EngineNotAvailableError: If ``name`` is not registered.

    Examples:
        Swap the default to a freshly-registered fake, then restore:

        >>> from corral.engines import (
        ...     register_engine, set_default_engine, get_engine
        ... )
        >>> from corral import engines as _eng
        >>> class _DoctestEngine:
        ...     name = "doctest-default"
        ...     def read_csv(self, source, schema=None, **kw): return None
        ...     def read_parquet(self, source, schema=None, **kw): return None
        ...     def read_duckdb_table(self, source, table, schema=None, **kw): return None
        ...     def from_records(self, records, schema=None): return None
        ...     def from_arrow(self, arrow_table): return None
        ...     def write_csv(self, expr, dest, **kw): return None
        ...     def write_parquet(self, expr, dest, **kw): return None
        ...     def write_duckdb_table(self, expr, dest, table, **kw): return None
        ...     def cast_schema(self, expr, schema): return expr
        ...     def scan(self, source, schema=None, **kw): return None
        ...     def write(self, expr, dest, fmt, **kw): return None
        ...     def materialize(self, expr): return None
        ...     def to_pandas(self, expr): return None
        ...     def to_polars(self, expr): return None
        ...     def to_arrow(self, expr): return None
        ...     def columns(self, expr): return []
        ...     def count(self, expr): return 0
        ...     def head(self, expr, n): return expr
        ...     def select(self, expr, columns): return expr
        ...     def order_by(self, expr, cols, descending=False): return expr
        ...     def limit(self, expr, n, offset=0): return expr
        >>> previous = _eng._DEFAULT
        >>> try:
        ...     register_engine(_DoctestEngine())
        ...     set_default_engine("doctest-default")
        ...     get_engine().name
        ... finally:
        ...     _ = _eng._REGISTRY.pop("doctest-default", None)
        ...     _eng._DEFAULT = previous
        'doctest-default'
    """
    global _DEFAULT
    if name not in _REGISTRY:
        available = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise EngineNotAvailableError(f"cannot set default to {name!r}: not registered (available: {available})")
    _DEFAULT = name


def list_engines() -> list[str]:
    """Return the sorted list of currently-registered engine names.

    Examples:
        The default install auto-registers at least ``ibis``:

        >>> from corral.engines import list_engines
        >>> names = list_engines()
        >>> "ibis" in names
        True
        >>> names == sorted(names)
        True
    """
    return sorted(_REGISTRY)


# ---------------------------------------------------------------------------
# Auto-registration of stock engines.
#
# Each block is wrapped in try/except ImportError so a missing optional
# dep (polars, pandas) never blocks importing this module. A *broken*
# install of a registered engine surfaces at use-time via
# NotImplementedError (stubs) or the engine's own error path (real
# impls in 1.3 / 1.4 / 1.5).
# ---------------------------------------------------------------------------

# DuckDB (via ibis) is the single compute engine. pandas / polars / pyarrow
# are input/output *formats* (see Engine.to_pandas / to_polars and the Arrow
# input path), not compute backends.
from .ibis_engine import IbisEngine  # noqa: E402  (deferred: after registry fns, avoids circular import)

register_engine(IbisEngine(), default=True)


# ---------------------------------------------------------------------------
# CLI-facing convenience: resolve a user-typed name to an Engine instance.
# ---------------------------------------------------------------------------


def resolve_engine(name: str | None) -> Engine:
    """Return the compute :class:`Engine` (DuckDB via ibis).

    DuckDB is the single compute engine; pandas / polars / pyarrow are I/O
    *formats*, not compute backends. ``None``, ``"ibis"`` and ``"duckdb"`` all
    return the one engine; any other name raises :class:`ValueError`.

    Args:
        name: ``None`` / ``"ibis"`` / ``"duckdb"``.

    Returns:
        The registered DuckDB engine.

    Raises:
        ValueError: If ``name`` is not one of the accepted values.

    Examples:
        >>> from corral.engines import resolve_engine
        >>> isinstance(resolve_engine(None), Engine)
        True
    """
    if name is None:
        return get_engine()
    key = name.strip().lower()
    if key not in {"ibis", "duckdb"}:
        raise ValueError(
            f"unknown engine {name!r}; DuckDB is the only compute engine "
            "(use 'ibis'/'duckdb', or None). pandas/polars/arrow are output "
            "formats via Table.to_pandas()/to_polars()."
        )
    return get_engine()


__all__ = [
    "Engine",
    "EngineNotAvailableError",
    "InvalidEngineCallError",
    "NativeFrame",
    "SourceRef",
    "TableExpr",
    "UnsupportedSourceError",
    "get_engine",
    "list_engines",
    "register_engine",
    "resolve_engine",
    "set_default_engine",
]
