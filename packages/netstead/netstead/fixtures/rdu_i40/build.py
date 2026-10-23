"""Build the committed rdu_i40 test fixture from OSM (data prep, not feature code).

Build the proven wider RTP bbox, then trim to an I-40-centric subnetwork: keep
everything within N graph-hops of the I-40 mainline so ramp->surface->mainline
connectivity at each interchange is intact, while dropping the far residential
sprawl. Carries `ref` (required to identify I-40).
"""

import collections
from pathlib import Path

from corral.engines.ibis_engine import IbisEngine

from netstead.osm.build import build_network_from_osm
from netstead.select._support import norm_ref

OUT = Path("packages/netstead/netstead/fixtures/rdu_i40")
bbox = (-78.850, 35.855, -78.760, 35.895)  # proven: I-40 stretch w/ S Miami / Page / Airport
HOPS = 4

net = build_network_from_osm(bbox, network_type="drive", extra_tags=["ref"], engine=IbisEngine())


def tp(t):
    """Materialize a table-like object to pandas (pass-through if already a frame)."""
    return t.to_pandas() if hasattr(t, "to_pandas") else t


links, nodes = tp(net.links), tp(net.nodes)
for c in ["destination", "junction"]:
    if c in links.columns:
        links = links.drop(columns=c)

# I-40 mainline nodes
i40 = links[(links.facility_type == "motorway") & links["ref"].apply(lambda r: "I40" in norm_ref(r))]
seed = set(i40.from_node_id) | set(i40.to_node_id)
print("I-40 mainline links:", len(i40), "seed nodes:", len(seed))

# BFS hop-buffer over the full undirected graph
adj = collections.defaultdict(list)
for _, r in links.iterrows():
    adj[r.from_node_id].append(r.to_node_id)
    adj[r.to_node_id].append(r.from_node_id)
keep = set(seed)
frontier = set(seed)
for _ in range(HOPS):
    nxt = set()
    for n in frontier:
        for m in adj[n]:
            if m not in keep:
                keep.add(m)
                nxt.add(m)
    frontier = nxt

links = links[links.from_node_id.isin(keep) & links.to_node_id.isin(keep)].reset_index(drop=True)
nodes = nodes[nodes.node_id.isin(set(links.from_node_id) | set(links.to_node_id))].reset_index(drop=True)
print("trimmed links:", links.shape, "nodes:", nodes.shape)
print(links.facility_type.value_counts().to_string())
for anc in ["South Miami Boulevard", "Airport Boulevard", "Page Road"]:
    n = (links.name == anc).sum()
    print(f"  anchor '{anc}': {n} links present")

(OUT / "csv").mkdir(parents=True, exist_ok=True)
(OUT / "parquet").mkdir(parents=True, exist_ok=True)
links.to_csv(OUT / "csv" / "link.csv", index=False)
nodes.to_csv(OUT / "csv" / "node.csv", index=False)
links.to_parquet(OUT / "parquet" / "link.parquet", index=False)
nodes.to_parquet(OUT / "parquet" / "node.parquet", index=False)
print("wrote fixture to", OUT)
