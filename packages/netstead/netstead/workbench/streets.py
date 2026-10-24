"""Street names for the Find tab's guided form: which streets meet a given street (read-only, nothing recorded)."""

from __future__ import annotations

import pandas as pd

from netstead.select._support import norm_name

__all__ = ["cross_streets"]


def cross_streets(links: pd.DataFrame, street: str) -> list[str]:
    """The sorted names of the other streets that share a node with ``street``.

    ``street`` matches the way Select does (case, spaces and punctuation ignored). A street that
    is not in the network, or has no named neighbors, gives ``[]``.

    >>> links = pd.DataFrame({"name": ["A St", "A St", "B St", "C St"],
    ...                       "from_node_id": [1, 2, 2, 8], "to_node_id": [2, 3, 5, 9]})
    >>> cross_streets(links, "a st")
    ['B St']
    """
    want = norm_name(street)
    if not want or "name" not in links.columns:
        return []
    names = links["name"].map(norm_name)
    own = links[names == want]
    nodes = set(own["from_node_id"]) | set(own["to_node_id"])
    near = links[links["from_node_id"].isin(nodes) | links["to_node_id"].isin(nodes)]
    found = {str(n) for n in near["name"].dropna() if norm_name(n) not in ("", want)}
    return sorted(found)
