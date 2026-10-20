"""Spec version primitives.

A small dataclass for representing semantic-style spec versions (e.g.
``0.97`` or ``0.97.1``) and helpers for comparing them and parsing them
from filesystem paths.

Corral targets pre-1.0 Frictionless-style specifications where the
*minor* component is treated like a major version: ``0.96`` and ``0.97``
are NOT compatible by default.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .errors import InvalidSpecVersionError

__all__ = ["SpecVersion", "compatible", "parse_version_dir"]


@dataclass(frozen=True, order=True)
class SpecVersion:
    """A simple semantic version triple for data specifications.

    Supports two- or three-component dotted strings (``"0.97"`` or
    ``"0.97.1"``). Missing components default to zero. Instances are
    frozen and hashable so they can be used as dict keys, and ordered
    lexicographically by ``(major, minor, patch)``.

    Attributes:
        major: Major version component.
        minor: Minor version component.
        patch: Patch version component (defaults to 0 when not given).

    Examples:
        >>> v = SpecVersion.from_str("0.97")
        >>> v.major, v.minor, v.patch
        (0, 97, 0)
        >>> SpecVersion.from_str("0.97.1") > SpecVersion.from_str("0.97")
        True
        >>> str(SpecVersion(0, 97, 0))
        '0.97.0'
    """

    major: int
    minor: int
    patch: int = 0

    @classmethod
    def from_str(cls, value: str) -> SpecVersion:
        """Parse a dotted version string into a :class:`SpecVersion`.

        Args:
            value: Version string with two or three dot-separated integer
                components (e.g. ``"0.97"`` or ``"0.97.1"``). Leading/
                trailing whitespace and a leading ``v`` are tolerated.

        Returns:
            The parsed version.

        Raises:
            InvalidSpecVersionError: If ``value`` is not a valid two- or
                three-component dotted integer string. This is a subclass
                of :class:`~corral.spec.errors.SpecLoadError` so
                catch-all spec-loading handlers still see it.

        Examples:
            >>> SpecVersion.from_str("0.97")
            SpecVersion(major=0, minor=97, patch=0)
            >>> SpecVersion.from_str("v1.2.3")
            SpecVersion(major=1, minor=2, patch=3)
        """
        text = value.strip().lstrip("vV")
        parts = text.split(".")
        if len(parts) not in (2, 3):
            raise InvalidSpecVersionError(f"Expected 'MAJOR.MINOR' or 'MAJOR.MINOR.PATCH', got: {value!r}")
        try:
            ints = [int(p) for p in parts]
        except ValueError as e:
            raise InvalidSpecVersionError(f"Non-integer component in version: {value!r}") from e
        if len(ints) == 2:
            ints.append(0)
        return cls(major=ints[0], minor=ints[1], patch=ints[2])

    def __str__(self) -> str:
        """Return canonical ``MAJOR.MINOR.PATCH`` string form."""
        return f"{self.major}.{self.minor}.{self.patch}"


def compatible(a: SpecVersion, b: SpecVersion) -> bool:
    """Return ``True`` if two versions are considered compatible.

    Compatibility rule (pre-1.0 semver-style): two versions are compatible
    if they share both ``major`` and ``minor`` components. Patch
    differences are tolerated. ``0.96`` and ``0.97`` are NOT compatible.

    Args:
        a: First version.
        b: Second version.

    Returns:
        ``True`` when the two versions can be safely interchanged.

    Examples:
        >>> compatible(SpecVersion(0, 97, 0), SpecVersion(0, 97, 1))
        True
        >>> compatible(SpecVersion(0, 96, 0), SpecVersion(0, 97, 0))
        False
        >>> compatible(SpecVersion(1, 0, 0), SpecVersion(1, 0, 5))
        True
    """
    return a.major == b.major and a.minor == b.minor


def parse_version_dir(path: Path) -> SpecVersion:
    """Parse the trailing component of a path as a :class:`SpecVersion`.

    Useful for discovering vendored spec versions from a layout like
    ``spec/0.97/`` or ``spec/0.97.1/``.

    Args:
        path: A filesystem path whose final component is a dotted
            version string.

    Returns:
        The parsed version.

    Raises:
        InvalidSpecVersionError: If the trailing component cannot be parsed.

    Examples:
        >>> from pathlib import Path
        >>> parse_version_dir(Path("packages/netstead/netstead/spec/0.97"))
        SpecVersion(major=0, minor=97, patch=0)
        >>> parse_version_dir(Path("foo/bar/0.97.1/"))
        SpecVersion(major=0, minor=97, patch=1)
    """
    name = Path(path).name or Path(path).parent.name
    return SpecVersion.from_str(name)
