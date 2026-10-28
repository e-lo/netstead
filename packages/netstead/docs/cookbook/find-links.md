---
title: Find links by describing them
audience: users
kind: howto
summary: What you can type in the Find tab or `netstead select`, which requests need a language model and which don't, and how fast each is.
---

# Find links by describing them

## When to use this

You want the links of a street, a stretch of a street between two cross streets, every link of a road type, or a
handful of link ids, and would rather type it than click it. The same requests work in the Workbench's **Find**
tab, in `netstead select` from a terminal, and in Python.

## Quick example

With the bundled Leavenworth network open in the Workbench (`uv run netstead app`, then **Included examples**),
type in **Find**:

```text
Cedar Street between Central Avenue and Price Avenue
```

Two links (57 and 129) are selected, with no language model involved. From a terminal:

```bash
LEAVENWORTH=$(uv run python -c "from netstead.fixtures import leavenworth; print(leavenworth.csv_dir())")
uv run netstead select "Cedar Street between Central Avenue and Price Avenue" "$LEAVENWORTH"
```

Or in Python:

```python
from netstead import Network
from netstead.fixtures import leavenworth
from netstead.select import StubParser, resolve

net = Network.from_source(leavenworth.csv_dir())
result = resolve(StubParser().parse("Cedar Street between Central Avenue and Price Avenue"), net)
print(result.status, result.link_ids)  # resolved [57, 129]
```

## What you can type

These forms are read by netstead's built-in pattern parser. They work with no language model at all, and with
one chosen, they are answered without calling it (see [When the model is called](#when-the-model-is-called)).

| Form | Example (Leavenworth) | Selects |
| --- | --- | --- |
| A street | `Cedar Street` | every link named Cedar Street |
| A street and a direction | `Front Street eastbound` (also `EB`, `westbound`, `NB`, `SB`, …) | its links heading within 75° of east |
| A stretch of a street | `Cedar Street between Central Avenue and Price Avenue`, or `… from Central Avenue to Price Avenue` | the shortest path along Cedar Street between the two intersections |
| A route number | `I-40 EB between South Miami Boulevard and Airport Boulevard` (route numbers starting `I`, `US`, `SR`, `NC`, `CR`, `SH`, `CA` or `TX`) | the same, on a freeway, from the off-ramp to the on-ramp |
| Every link | `all links` | the whole network |
| Every link of a type | `all residential links` | links whose `facility_type` is `residential` |
| Link ids | `links 57, 129` | those links |

The direction word goes **after** the street.

Anything else needs a language model. For example:

| Request | Why the pattern parser can't read it |
| --- | --- |
| `bike lanes on Cedar Street` | a travel mode |
| `Cedar Street with 2 lanes` | an attribute filter |
| `northbound Cedar Street` | the direction comes first |
| `the stretch of Cedar near the school` | free phrasing |

To choose a model, see [Language models](workbench.md#language-models-natural-language-selection) (cloud
providers) or [Run language models locally with Ollama](local-llm-ollama.md).

## How names match

You don't need to spell a street the way the network does. A name is looked up in this order, and the first rule
that finds something wins:

| Rule | You type | Network has | Notes |
| --- | --- | --- | --- |
| Exact | `cedar street` | Cedar Street | case, spaces and punctuation ignored |
| Abbreviation | `Cedar St`, `N Main St` | Cedar Street, North Main Street | street types (St, Ave, Rd, Blvd, Dr, Ln, Ct, Pl, Pkwy, Hwy, …) and directions (N, S, E, W, NE, …) |
| Direction left out | `Peace Street` | West Peace Street, East Peace Street | selects every side; `W Peace St` selects only the West side |
| Close spelling | `Ceder Street`, `Hilsborough St` | Cedar Street, Hillsborough Street | the closest name at least 85% alike; never for names under 5 letters |

Whenever a rule other than the exact one was used, the selection's diagnostics say so, for example
`matched 'Ceder Street' to 'Cedar Street' (a close spelling)`. Cross-street names match the same way.

A route number matches the network's `ref` column, so `I-40` finds a link whose `ref` is `I 40;US 64`.
Networks without a `ref` column (including those `netstead build` makes from OpenStreetMap today) can only be
searched by street name.

## How a stretch between two cross streets is found

1. Each cross street is placed where it meets the street: an at-grade intersection, or for a freeway, the
   off-ramp where you leave (the first cross street) and the on-ramp where you join (the second).
2. The selection is the shortest path along the street between those two nodes.
3. A street that is partly a freeway (a trunk road with signals, like many US routes through town) tries its
   at-grade intersections when no ramp connects.
4. When the street's own links don't join the two cross streets, the path may cross the short gaps where its name
   breaks off: unnamed links (a roundabout, a slip lane) or named links up to 20 m long (a cross street spanning a
   divided road's median), within 3 links of the street. Service roads, paths and tracks never count. The
   diagnostics say how many links were used this way.
5. If a cross street meets the street at more than one node, the selection is **ambiguous**: the nearest is used
   and the others are listed. See [Verify a selection](workbench.md#verify-a-selection) to pin one.

## When the model is called

With a language model chosen, every request is first read by the pattern parser. The model is called only when:

- the pattern parser can't read the request at all,
- it reads it, but a street, cross street, route number or road type in it is not in the network (by the rules
  in [How names match](#how-names-match)), or
- its reading selects nothing.

A request answered this way records `parsed_by: {"provider": "stub", "mode": "pattern", "grammar_first": true}`
in the history, so you can tell which requests went to the model.

**Ask** (the assistant) works the same way. A question the offline assistant reads in full (`select Cedar St
between Central Ave and Price Ave and color by lanes`, `filter lanes >= 2`, `zoom to the selection`, `save the
selection as downtown`), naming only streets and columns the network has, is planned without the model. Anything
else (`color by speed` when the column is `free_speed`, `which streets have the most lanes?`) goes to the model.

Turn this off to send every request to the model:

```toml
# netstead.toml, or ~/.config/netstead/config.toml
[select]
grammar_first = false
```

## Performance

Measured on an Apple M3 laptop (16 GB) with an OpenStreetMap Raleigh–Durham roads network (303,000 links) and
`qwen2.5:7b` in Ollama on the same machine, already loaded:

| Request | Pattern parser | Local model (before this release, every request) |
| --- | --- | --- |
| `Hillsborough Street` | 1 ms | 6.7 s |
| `Capital Boulevard northbound` | 8 ms | 2.8 s |
| `Western Boulevard between Gorman Street and Avent Ferry Road` | 4 ms | 4.1 s |
| `all residential links` (94,000 links) | 32 ms | 5.5 s |
| `Hillsborough St between Oberlin Rd and Gorman St` | 16 ms | 4.7 s, not found |
| `Glenwood Avenue between Peace Street and Wade Avenue` | 31 ms | 13.6 s, not found (two model calls) |
| `bike lanes on Hillsborough Street` | needs the model: 2.1 s | 2.7 s |

- **The model's time is almost all generating its answer**, about 2–5 s per call for a 7-billion-parameter model
  on a laptop. The first request after Ollama loads a model also waits for the load: 35–40 s here.
- **Looking up names is fast in both cases.** The network's street names are indexed once per network version
  (0.16 s for this network) and then each lookup takes well under a millisecond, where the previous release spent
  0.2–0.8 s per request re-reading every link's name.
- **A cloud model** skips the load wait but adds a network round trip; we haven't measured one here. It costs
  little: a Find or Ask call sends about 5,000 tokens and gets a few hundred back, about $0.0007 on Haiku 5.5
  (Anthropic's default) and $0.013 on Sonnet 5.5, less when the shared start of the prompt is cached.

## Pitfalls

- **An exact name always wins.** Leavenworth has both Center Street and West Center Street; `Center Street`
  selects only Center Street. Likewise a misspelling that happens to be another street's real name finds that
  street, not the one you meant.
- **A close spelling is a guess.** When the diagnostics say `(a close spelling)`, check the map.
- **Route numbers need a `ref` column.** Without one, `I-40` goes to the model, which can't find it either.

## See also

- [Explore networks in the Netstead Workbench](workbench.md#find-the-offline-form-and-the-language-model-box): the Find tab's offline form and verifying a selection.
- [Run language models locally with Ollama](local-llm-ollama.md)
- [Settings reference](../reference/settings.md): `select.provider`, `select.model`, `select.grammar_first`.
