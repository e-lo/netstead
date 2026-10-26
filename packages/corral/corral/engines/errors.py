"""Structured exceptions raised by the corral engine layer.

Per ``docs/architecture.md`` §9 ("structured exceptions in errors.py;
use specific subclasses, not bare ``ValueError``"), engine modules
raise these typed subclasses instead of bare built-ins. Each subclass
inherits from a built-in (``ValueError`` / ``TypeError`` / ``RuntimeError``)
so existing ``pytest.raises(ValueError, match=...)`` patterns still
match — the subclass shape is additive for code that wants to catch
the engine-specific cases distinctly.

Subclasses
----------

- :class:`EngineNotAvailableError` (``RuntimeError``) — the requested
  engine is not registered or its optional dep is missing.
- :class:`InvalidEngineCallError` (``ValueError``) — engine method
  called with invalid arguments (missing required kwargs like
  ``table=``, etc.).
- :class:`UnsupportedSourceError` (``TypeError``) — engine cannot
  interpret the given source: unknown dict shape, wrong type, etc.
"""

from __future__ import annotations


class EngineNotAvailableError(RuntimeError):
    """Raised when a requested engine is not registered or its deps are missing.

    This is the single error type the registry raises for "I cannot give
    you that engine". Reasons include:

    - The engine's optional dependencies are not installed (e.g. user
      asked for ``"polars"`` without ``pip install dbcorral[polars]``).
    - The name is not registered (typo, or a module that failed to
      import at registration time).
    - The registry is empty (no engines successfully registered at
      import — usually means the default ibis install is broken).
    """


class InvalidEngineCallError(ValueError):
    """Engine method called with invalid arguments.

    Examples include missing required kwargs (``table=`` for a duckdb
    source), or invalid combinations of arguments. Inherits from
    ``ValueError`` so callers using ``pytest.raises(ValueError)`` or
    catching ``ValueError`` still see the same shape.
    """


class UnsupportedSourceError(TypeError):
    """Engine cannot interpret the given source.

    Raised when the source is the wrong type (unrecognized dict shape,
    not a path / dict / handle), as opposed to an unsupported *format*
    (which raises :class:`NotImplementedError` with a hint at the
    deferred FormatAdapter task). Inherits from ``TypeError`` so it
    still matches ``pytest.raises(TypeError)``.
    """


__all__ = [
    "EngineNotAvailableError",
    "InvalidEngineCallError",
    "UnsupportedSourceError",
]
