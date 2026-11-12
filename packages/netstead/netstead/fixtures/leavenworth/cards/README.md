# Sample ProjectCards for Leavenworth

Nine small [ProjectCards](https://network-wrangler.github.io/projectcard/) written against the bundled Leavenworth network's real link and node ids. Open them in the Workbench's **Cards** panel with **Sample cards (Leavenworth)** (the Leavenworth example network must be open for them to be checked), or find the folder with `netstead.fixtures.leavenworth.cards_dir()`.

The catalog is **read-only** inside the Workbench: its files can be read, inspected, previewed and (with `enable_apply`) applied, but not renamed, edited or deleted. To change a card, copy this folder to a folder of your own and open that.

Every card has a year tag (`year:2027`, `year:2030`) and a theme tag (`safety`, `bike`, `transit`, `maintenance`). Speeds are in the network's units (km/h in this fixture).

| File | What it does |
| ---- | ------------ |
| `commercial-street-road-diet.yml` | Road diet: `lanes` 2 to 1 on Commercial Street's six two-lane links (a Link list, GMNS `model_link_id`). |
| `downtown-slow-streets.yml` | Speed reduction: `free_speed` 40 to 30 on Cedar and Division Streets (Link list, 18 links). |
| `park-avenue-bike-lanes.yml` | Bike lanes: `bike_facility` `none` to `unseparated bike lane` (a GMNS allowed value) on Park Avenue (Link list). |
| `front-street-pedestrian-improvements.yml` | A **Segment**: Front Street from node 13 to node 87, with the `netstead.resolved[0]` provenance note; parking removed and speed lowered. |
| `ski-hill-drive-safety-osm.yml` | **OpenStreetMap ids**: bike lanes on OSM way 5848236 (`osm_link_id`) and an all-way stop at OSM node 46817728 (`osm_node_id`). |
| `mine-street-alley-closure.yml` | `roadway_deletion`: closes the 17.6 m Mine Street stub (links 203 and 233). |
| `nordic-circle-connector.yml` | `roadway_addition`: a new two-way connector, links 340 and 341 (above the network's highest, 339), between existing nodes 91 and 115. |
| `commercial-street-bike-lanes.yml` | Has `dependencies`: the Commercial Street road diet is a prerequisite, and Park Avenue bike lanes are listed as a conflict. |
| `cedar-street-road-diet-stale.yml` | **Intentionally does not fit**: it expects 2 lanes where Cedar Street has 1, so the panel shows "Doesn't fit" with the reason. |
