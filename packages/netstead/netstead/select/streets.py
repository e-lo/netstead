"""A network's street names and route numbers, normalized once: the lookup side of Select.

:class:`StreetIndex` maps each normalized name (:func:`~netstead.select._support.norm_name`) and
each route-number token (:func:`~netstead.select._support.norm_ref`) to the rows of the links
frame that carry it. The resolver then finds a facility or an anchor with a dict lookup instead
of normalizing every link's name on every query. Build it once per network version.

A name the network doesn't spell the same way still matches, in this order (first hit wins):

1. **exact**: the same letters and digits, ignoring case, spaces and punctuation;
2. **spelling**: the same after expanding street-type and direction abbreviations
   (``"Hillsborough St"`` -> ``Hillsborough Street``, ``"N Main St"`` -> ``North Main Street``);
3. **directional**: the same once a leading or trailing direction is dropped from both
   (``"Peace Street"`` -> ``West Peace Street`` and ``East Peace Street``);
4. **fuzzy**: a misspelling, the closest name at least 85% similar once directions are dropped
   (``"Hillsboro St"`` -> ``Hillsborough Street``); names shorter than 5 letters never match this way.

A direction the request names never picks up a street on another side ("W Six Forks Rd" is not
East Six Forks Road).

:func:`grammar_first` uses the index to accept the offline grammar's reading of an utterance
only when every name in it is in the network, so a common request needs no model call.

Examples:
    >>> import pandas as pd
    >>> links = pd.DataFrame({"link_id": [1, 2, 3], "from_node_id": [1, 2, 3], "to_node_id": [2, 3, 4],
    ...                       "name": ["West Peace Street", "East Peace Street", "Glenwood Avenue"],
    ...                       "facility_type": ["secondary"] * 3})
    >>> index = StreetIndex.build(links)
    >>> index.lookup_name("Peace St")
    NameMatch(names=('East Peace Street', 'West Peace Street'), how='directional')
    >>> grammar_first("Glenwood Ave between Peace St and Oak Road", index) is None  # no Oak Road here
    True
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Any, Literal, NamedTuple

import numpy as np
import pandas as pd

from ._support import norm_name, norm_ref
from .errors import IntentError
from .intent import SelectionIntent
from .parse import StubParser

__all__ = ["NameMatch", "StreetIndex", "grammar_first", "spelling_key"]

# Street types, by their usual abbreviation (USPS Publication 28, the common ones).
_STREET_TYPES = {
    "aly": "alley",
    "av": "avenue",
    "ave": "avenue",
    "blvd": "boulevard",
    "cir": "circle",
    "ct": "court",
    "cv": "cove",
    "dr": "drive",
    "expy": "expressway",
    "fwy": "freeway",
    "hwy": "highway",
    "ln": "lane",
    "pkwy": "parkway",
    "pl": "place",
    "rd": "road",
    "sq": "square",
    "st": "street",
    "ter": "terrace",
    "trl": "trail",
    "xing": "crossing",
}
_DIRECTIONS = {
    "n": "north",
    "s": "south",
    "e": "east",
    "w": "west",
    "ne": "northeast",
    "nw": "northwest",
    "se": "southeast",
    "sw": "southwest",
}
_DIRECTION_WORDS = frozenset(_DIRECTIONS.values())
#: How alike (difflib ratio) a misspelled name must be to the network's, and the shortest it may be.
_FUZZY_CUTOFF = 0.85
_FUZZY_MIN_LENGTH = 5
#: Road types that never bridge a gap in a street (parking aisles, driveways, paths).
_NO_BRIDGE_TYPES = frozenset({"service", "footway", "path", "cycleway", "pedestrian", "steps", "track"})
#: A named link at most this long (meters) may bridge a gap: a cross street spanning a median.
_BRIDGE_MAX_LENGTH = 20.0


class NameMatch(NamedTuple):
    """The network's spellings of a name, and which rule found them (``None``: not in the network)."""

    names: tuple[str, ...]
    how: Literal["exact", "spelling", "directional", "fuzzy"] | None


def _words(text: str) -> list[str]:
    """Lowercase words of ``text`` with abbreviations expanded.

    A direction is expanded only as the first or last word, and a street type only as the last
    word (or the one before a trailing direction): "St Marys Street" keeps its "St".
    """
    words = re.findall(r"[a-z0-9]+", str(text).lower())
    if not words:
        return words
    if words[0] in _DIRECTIONS and len(words) > 1:
        words[0] = _DIRECTIONS[words[0]]
    if words[-1] in _DIRECTIONS and len(words) > 1:
        words[-1] = _DIRECTIONS[words[-1]]
    last = len(words) - 2 if len(words) > 2 and words[-1] in _DIRECTION_WORDS else len(words) - 1
    if last > 0 and words[last] in _STREET_TYPES:
        words[last] = _STREET_TYPES[words[last]]
    return words


def spelling_key(text: str) -> str:
    """``text`` normalized with abbreviations expanded: equal keys are the same street spelled two ways.

    >>> spelling_key("N. Hillsborough St") == spelling_key("North Hillsborough Street")
    True
    """
    return "".join(_words(text))


def _split_directions(text: str) -> tuple[str, frozenset[str]]:
    """``(spelling key without a leading/trailing direction, those directions)``.

    >>> _split_directions("W Peace St")
    ('peacestreet', frozenset({'west'}))
    """
    words = _words(text)
    dirs = set()
    if len(words) > 1 and words[0] in _DIRECTION_WORDS:
        dirs.add(words.pop(0))
    if len(words) > 1 and words[-1] in _DIRECTION_WORDS:
        dirs.add(words.pop())
    return "".join(words), frozenset(dirs)


def _group_rows(values: pd.Series, normalize) -> dict[str, np.ndarray]:
    """``{key: sorted row positions}`` for each non-empty key ``normalize`` gives a value of ``values``."""
    codes, uniques = pd.factorize(values, use_na_sentinel=True)
    order = np.argsort(codes, kind="stable")
    bounds = np.searchsorted(codes[order], np.arange(len(uniques) + 1))
    out: dict[str, list[np.ndarray]] = {}
    for code, value in enumerate(uniques):
        rows = order[bounds[code] : bounds[code + 1]]
        for key in normalize(value):
            out.setdefault(key, []).append(rows)
    return {key: np.sort(np.concatenate(parts)) for key, parts in out.items()}


@dataclass(frozen=True, eq=False)
class StreetIndex:
    """Street names and route numbers of one links frame, normalized once (see the module docstring).

    Row positions index the frame the index was built from (``links.iloc[rows]``);
    :meth:`check` refuses a different frame.
    """

    link_ids: np.ndarray
    from_nodes: np.ndarray
    to_nodes: np.ndarray
    row_names: np.ndarray  # norm_name of each row ("" for none)
    rows_by_name: dict[str, np.ndarray]  # norm_name -> rows
    rows_by_ref: dict[str, np.ndarray]  # norm_ref token -> rows
    names_by_key: dict[str, tuple[str, ...]]  # spelling_key -> norm_names
    names_by_base: dict[str, tuple[str, ...]]  # spelling key without directions -> norm_names
    directions: dict[str, frozenset[str]]  # norm_name -> its leading/trailing direction words
    spelled: dict[str, str]  # norm_name -> the network's own spelling
    ramp_rows: np.ndarray  # rows whose facility_type is a ramp/connector (``*_link``)
    bridge_rows: np.ndarray  # rows that may bridge a gap in a street (see :meth:`bridge_rows_near`)
    facility_types: frozenset[str]
    _fuzzy: dict[str, tuple[str, ...]] = field(default_factory=dict, repr=False)  # memo: base key -> names

    @classmethod
    def build(cls, links: pd.DataFrame) -> StreetIndex:
        """Index ``links`` (``name``, ``ref`` and ``facility_type`` are optional columns)."""
        blank = pd.Series(None, index=links.index, dtype="object")
        names = links["name"] if "name" in links.columns else blank
        rows_by_name = _group_rows(names, lambda value: [key] if (key := norm_name(value)) else [])
        rows_by_ref = _group_rows(links["ref"] if "ref" in links.columns else blank, norm_ref)
        codes, uniques = pd.factorize(names, use_na_sentinel=True)
        normalized = np.array([norm_name(v) for v in uniques] + [""], dtype=object)
        row_names = normalized[codes]  # code -1 (no name) picks the trailing ""

        spelled: dict[str, str] = {}
        for value, name in zip(uniques, normalized, strict=False):
            if name:
                spelled.setdefault(name, str(value))
        by_key: dict[str, set[str]] = {}
        by_base: dict[str, set[str]] = {}
        directions: dict[str, frozenset[str]] = {}
        for name, text in spelled.items():
            by_key.setdefault(spelling_key(text), set()).add(name)
            base, directions[name] = _split_directions(text)
            by_base.setdefault(base, set()).add(name)

        if "facility_type" in links.columns:
            kinds = links["facility_type"].astype("string")
            ramp_rows = np.flatnonzero(kinds.str.endswith("_link").fillna(False).to_numpy())
            facility_types = frozenset(str(k).lower() for k in kinds.dropna().unique())
            may_bridge = ~kinds.str.lower().isin(_NO_BRIDGE_TYPES).fillna(False).to_numpy()
        else:
            ramp_rows, facility_types = np.array([], dtype=np.int64), frozenset()
            may_bridge = np.ones(len(links), dtype=bool)
        short = np.zeros(len(links), dtype=bool)
        if "length" in links.columns:
            short = (pd.to_numeric(links["length"], errors="coerce") <= _BRIDGE_MAX_LENGTH).fillna(False).to_numpy()
        bridge_rows = np.flatnonzero(may_bridge & ((row_names == "") | short))
        return cls(
            link_ids=links["link_id"].to_numpy(),
            from_nodes=links["from_node_id"].to_numpy(),
            to_nodes=links["to_node_id"].to_numpy(),
            row_names=row_names,
            rows_by_name=rows_by_name,
            rows_by_ref=rows_by_ref,
            names_by_key={k: tuple(sorted(v)) for k, v in by_key.items()},
            names_by_base={k: tuple(sorted(v)) for k, v in by_base.items()},
            directions=directions,
            spelled=spelled,
            ramp_rows=ramp_rows,
            bridge_rows=bridge_rows,
            facility_types=facility_types,
        )

    def check(self, links: pd.DataFrame) -> None:
        """Raise ``ValueError`` unless ``links`` is the frame this index was built from (same link ids, same order)."""
        ids = links["link_id"].to_numpy()
        if len(ids) != len(self.link_ids) or not np.array_equal(ids, self.link_ids):
            raise ValueError("this StreetIndex was built from a different links frame; rebuild it")

    def lookup_name(self, text: Any) -> NameMatch:
        """The network's names that ``text`` stands for: exact, spelling, directional, then fuzzy (see module)."""
        exact = norm_name(text)
        if not exact:
            return NameMatch((), None)
        if exact in self.rows_by_name:
            return NameMatch((self.spelled[exact],), "exact")
        if found := self.names_by_key.get(spelling_key(text)):
            return NameMatch(tuple(self.spelled[n] for n in found), "spelling")
        base, asked = _split_directions(text)
        if found := self._same_side(self.names_by_base.get(base, ()), asked):
            return NameMatch(found, "directional")
        if len(base) >= _FUZZY_MIN_LENGTH and (found := self._same_side(self._closest(base), asked)):
            return NameMatch(found, "fuzzy")
        return NameMatch((), None)

    def _same_side(self, names, asked: frozenset[str]) -> tuple[str, ...]:
        """The network spellings of ``names`` that don't contradict the directions the request ``asked`` for.

        A direction the request names must not pick up the other side ("W Six Forks Rd" is not
        East Six Forks Road); one it leaves out matches every side ("Peace St" is East and West).
        """
        keep = [n for n in names if not asked or not self.directions[n] or self.directions[n] == asked]
        return tuple(self.spelled[n] for n in keep)

    def _closest(self, base: str) -> tuple[str, ...]:
        """The names whose direction-free key is most like ``base`` (ties all kept), if any reach the cutoff."""
        if base not in self._fuzzy:
            matcher = difflib.SequenceMatcher(b=base, autojunk=False)
            best, keys = _FUZZY_CUTOFF, []
            for key in self.names_by_base:
                matcher.set_seq1(key)
                if matcher.real_quick_ratio() < best or matcher.quick_ratio() < best:
                    continue
                score = matcher.ratio()
                if score > best:
                    best, keys = score, [key]
                elif score == best:
                    keys.append(key)
            self._fuzzy[base] = tuple(sorted({n for key in keys for n in self.names_by_base[key]}))
        return self._fuzzy[base]

    def name_rows(self, text: Any) -> tuple[np.ndarray, NameMatch]:
        """Rows of the links ``text`` names (see :meth:`lookup_name`), and the match."""
        match = self.lookup_name(text)
        return self._rows(self.rows_by_name, [norm_name(n) for n in match.names]), match

    def ref_rows(self, text: Any) -> np.ndarray:
        """Rows of the links whose route number shares a token with ``text`` (``"I-40"`` ~ ``"I 40;US 64"``)."""
        return self._rows(self.rows_by_ref, norm_ref(text))

    def street_rows(self, text: Any) -> np.ndarray:
        """Rows of the links ``text`` names as a street or as a route number (how an anchor matches)."""
        return np.union1d(self.name_rows(text)[0], self.ref_rows(text))

    def bridge_rows_near(self, nodes: set, hops: int = 3) -> np.ndarray:
        """Rows of the links that may bridge a gap in a street, up to ``hops`` links out from ``nodes``.

        A street's own name often stops short of a whole chain: a roundabout or a slip lane with no
        name, or, on a divided road, the few meters of a cross street that span the median. Those
        links (unnamed, or named but at most 20 m long) may bridge it; service roads, paths and
        tracks never do.
        """
        candidates = self.bridge_rows
        tails, heads = self.from_nodes[candidates], self.to_nodes[candidates]
        reached = np.fromiter(nodes, dtype=self.from_nodes.dtype)
        picked = np.zeros(len(candidates), dtype=bool)
        for _ in range(hops):
            touching = ~picked & (np.isin(tails, reached) | np.isin(heads, reached))
            if not touching.any():
                break
            picked |= touching
            reached = np.union1d(reached, np.concatenate([tails[touching], heads[touching]]))
        return candidates[picked]

    def nodes_of(self, rows: np.ndarray) -> set:
        """The end nodes of the links at ``rows``."""
        return set(self.from_nodes[rows].tolist()) | set(self.to_nodes[rows].tolist())

    def knows(self, text: Any) -> bool:
        """Whether ``text`` names a street or route number in the network."""
        return bool(self.lookup_name(text).names) or bool(norm_ref(text) & self.rows_by_ref.keys())

    def cross_streets(self, street: str) -> list[str]:
        """Sorted names of the other streets that share a node with ``street`` (the Find form's cross-street list).

        ``street`` matches exactly (case, spaces and punctuation ignored); one that is not in the
        network, or has no named neighbors, gives ``[]``.

        >>> links = pd.DataFrame({"link_id": [1, 2, 3, 4], "name": ["A St", "A St", "B St", "C St"],
        ...                       "from_node_id": [1, 2, 2, 8], "to_node_id": [2, 3, 5, 9]})
        >>> StreetIndex.build(links).cross_streets("a st")
        ['B St']
        """
        own = norm_name(street)
        rows = self.rows_by_name.get(own)
        if rows is None:
            return []
        nodes = np.fromiter(self.nodes_of(rows), dtype=self.from_nodes.dtype)
        near = np.isin(self.from_nodes, nodes) | np.isin(self.to_nodes, nodes)
        found = set(self.row_names[near].tolist()) - {own, ""}
        return sorted(self.spelled[name] for name in found)

    @staticmethod
    def _rows(table: dict[str, np.ndarray], keys) -> np.ndarray:
        parts = [table[k] for k in keys if k in table]
        if not parts:
            return np.array([], dtype=np.int64)
        return parts[0] if len(parts) == 1 else np.unique(np.concatenate(parts))


def grammar_first(utterance: str, index: StreetIndex) -> SelectionIntent | None:
    """The offline grammar's reading of ``utterance`` when the network knows every name in it, else ``None``.

    The grammar (:class:`~netstead.select.parse.StubParser`) reads any text as a facility name, so
    its answer counts only if the facility and both anchors are streets or route numbers in the
    network, and an ``all <type> links`` type is a ``facility_type`` that occurs. ``None`` means:
    ask the language model.

    >>> import pandas as pd
    >>> links = pd.DataFrame({"link_id": [1], "from_node_id": [1], "to_node_id": [2],
    ...                       "name": ["Glenwood Avenue"], "facility_type": ["primary"]})
    >>> grammar_first("Glenwood Ave northbound", StreetIndex.build(links)).facility.direction
    'NB'
    >>> grammar_first("bike lanes on Glenwood Avenue", StreetIndex.build(links)) is None
    True
    """
    try:
        intent = StubParser().parse(utterance)
    except IntentError:
        return None
    if intent.link_ids:
        return intent
    if intent.select_all:
        kinds = intent.conditions.get("facility_type", [])
        return intent if all(str(k).lower() in index.facility_types for k in kinds) else None
    names = [*intent.facility.refs(), *intent.facility.names(), intent.from_anchor, intent.to_anchor]
    return intent if all(index.knows(name) for name in names if name) else None
