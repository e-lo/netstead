---
title: Frictionless data packages
audience: both
kind: concept
summary: What the Frictionless Data Package format is, how corral + netstead use it, what each Frictionless term maps to in this codebase, and how to handle data that isn't (yet) a Frictionless package.
---

# Frictionless data packages

This page explains the data-description format that sits underneath every `Package` and `Network` in corral and netstead. If you've ever wondered why `datapackage.json` exists or what a "table schema" formally is, start here.

## What it is

The [Frictionless Data Package](https://datapackage.org/) specification is a small, JSON-based standard for describing a directory of tabular files — what tables it contains, what columns each table has, and how the tables relate to each other through foreign keys. It is maintained by the [Open Knowledge Foundation](https://okfn.org/) and is the de-facto common language for data-publishing organisations that need machine-readable structure (Datahub.io, the EU Open Data Portal, the Frictionless Repository ecosystem) and for domain specs that need a portable model — the [General Modeling Network Specification (GMNS)](https://github.com/zephyr-data-specs/GMNS) being the canonical example for transportation networks. corral's `Package` type and netstead's `Network` type are both thin wrappers around a resolved Frictionless Data Package — when you load a network, you are loading a Data Package whose Resources happen to be GMNS link / node / segment tables.

## Why we have it

Frictionless solves the problem of "here is a folder of CSVs, what *is* it?" without inventing a new format for every domain. Every domain spec that needs structural metadata — column types, foreign keys, required-vs-optional resources — would otherwise have to ship its own description format and its own parser. By standing on Frictionless, GMNS (and any future spec corral serves) gets a portable, tooling-rich substrate for free, and corral gets to write *one* spec loader that handles every domain spec built on top.

## The four moving parts

Four concepts compose a Frictionless package. Each maps cleanly onto a type in corral / netstead.

### Data Package

A Data Package is a directory containing a `datapackage.json` manifest plus the data files the manifest describes. The manifest names the package and lists its resources; cross-resource constraints (foreign keys) live inside each resource's Table Schema, and GMNS adds a package-level `shared_categories.json` for shared enumerations.

```json
{
  "name": "leavenworth-gmns",
  "title": "Leavenworth WA — bundled GMNS fixture",
  "profile": "tabular-data-package",
  "resources": [
    { "name": "link", "path": "link.csv", "schema": "link.schema.json" },
    { "name": "node", "path": "node.csv", "schema": "node.schema.json" }
  ]
}
```

In corral this is `corral.Package`; in netstead this is `netstead.Network` (a `Package` plus GMNS-aware accessors like `.links`, `.nodes`).

### Resource

A Resource is one table in the package — a single file (or set of files, for partitioned formats) with a path, a name, and a schema. Resources are the unit of read and write: `pkg.tables["link"]` and `engine.scan(resource)` both operate on a single Resource.

```json
{
  "name": "link",
  "path": "link.csv",
  "format": "csv",
  "schema": "link.schema.json",
  "profile": "tabular-data-resource"
}
```

In corral this is `pkg.tables["link"]` (returning a `corral.dataset.Table`); in netstead this is `net.tables["link"]` (same object, exposed alongside the GMNS-named accessor `net.links`).

### Table Schema

A Table Schema describes the fields of one Resource — name, type, constraints, missing-value tokens. It can live inline inside `datapackage.json` or in a sibling `<name>.schema.json` file (the latter is what GMNS does). corral reads both forms; `Package.write` emits no schema files at all for Parquet / CSV / DuckDB output, and the zip output inlines the schemas into its `datapackage.json`.

```json
{
  "primaryKey": "link_id",
  "fields": [
    { "name": "link_id",      "type": "integer", "constraints": { "required": true } },
    { "name": "from_node_id", "type": "integer", "constraints": { "required": true } },
    { "name": "to_node_id",   "type": "integer", "constraints": { "required": true } },
    { "name": "length",       "type": "number" },
    { "name": "facility_type","type": "string"  }
  ]
}
```

In corral this is `net.tables["link"].schema` (a Pydantic v2 `Schema` model from `corral.spec`). The schema is what `schema_check` validates row dtypes against and what the docgen pipeline renders into reference pages.

### Foreign Keys

Foreign keys are declared inside each resource's Table Schema (the `foreignKeys` array), whether that schema is inline in `datapackage.json` or in a sibling `.schema.json` file. A foreign key says "column X in resource Y must reference an existing value of column Z in resource W." This is what lets GMNS state "every `link.from_node_id` must exist in `node.node_id`" once, in machine-readable form, instead of in prose buried in a PDF.

```json
{
  "name": "link",
  "schema": {
    "foreignKeys": [
      {
        "fields": "from_node_id",
        "reference": { "resource": "node", "fields": "node_id" }
      }
    ]
  }
}
```

In corral this is walked by `net.validate()`'s FK pass (see `corral.validation.foreign_keys`). The same pass also stamps source + target content hashes into the `DirtyTracker` so a later edit + write can warn on out-of-sync FKs (see [architecture §6.3](../architecture.md#63-validation-sync-state)).

## GMNS-to-Frictionless mapping table

Every GMNS concept lands on a Frictionless concept and a concrete name in corral / netstead:

| GMNS concept | Frictionless concept | corral / netstead name |
|---|---|---|
| The whole network | Data Package | `netstead.Network` / `corral.Package` |
| `link` / `node` / `segment` / `lane` table | Resource | `net.tables["link"]` |
| `link.schema.json` field definitions | Table Schema | `net.tables["link"].schema` |
| `link.from_node_id → node.node_id` | Foreign Key | walked by `net.validate()` |
| `signal_phase_mvmt.timeday_id → time_set_definitions.timeday_id` | Composite-ref Foreign Key | walked by `net.validate()` (composite-key path) |
| Allowed `facility_type` values | Shared Categories (`shared_categories.json`) | resolved by `corral.spec.loader` |
| Vendored spec versions (`0.95/`, `0.96/`, `0.97/`) | Per-version Data Package definitions | `netstead.spec.load_gmns_spec(version)` |
| Missing-value tokens (e.g. `-99`) | `missingValues` on the Table Schema | modelled as `Schema.missing_values` in `corral.spec`; **not yet applied on read** — the tokens come through as-is |

## What Frictionless gives you (and what it doesn't)

What you get by standing on Frictionless:

- **Cross-tool interop.** Any Frictionless-aware tool (the OKFN CLI, OpenRefine, several R packages, the dataset publishers above) can read a corral-emitted package without writing custom code.
- **Machine-readable schemas.** Field types, primary keys, foreign keys, and required-vs-optional are all declarative JSON — not English prose. This is what makes `--json` CLI output, MCP tools, and AI agents possible.
- **Declarative FKs.** The relational structure is in the manifest, not scattered across reader code. `validate()` walks it; renderers display it; agents can reason about it.
- **Public-domain spec + tooling ecosystem.** OKFN maintains the spec; the surrounding ecosystem is permissively licensed. No vendor risk on the format itself.
- **Composable spec versioning.** netstead supports multiple GMNS versions side-by-side because each version is just a different Data Package definition that corral's version-agnostic loader reads (see [architecture §7](../architecture.md#7-spec-sync-strategy)).

What you *don't* get, and where the rest of the stack picks up:

- **Semantics.** Frictionless is structural only. It knows `from_node_id` is an integer that references `node.node_id`; it does not know what a "node" *means*. Domain semantics (connectivity, geometry assembly, TOD resolution) live in `netstead.semantics`.
- **Data-quality rules.** Frictionless validates structure — "is this column an integer?". It does not validate plausibility — "is a 65 mph residential street suspicious?". That's the rule-pack pattern in `corral.quality` + the `netstead.quality` GMNS rule pack.
- **Units and measurement conventions.** Frictionless can say `length` is a `number`; it cannot say it must be in metres. GMNS layers a units convention on top; corral does not police it.

## Handling non-Frictionless data — the design seam

Real GMNS-adjacent data is not always shipped as a Frictionless package. Modellers receive GTFS feeds, OGC GeoPackages, shapefiles, OSM-derived parquet, raw CSV directories with no manifest, and Avro-with-metadata blobs from upstream systems. The toolkit has a tiered story for handling these:

### 1. Today's escape valve — pass `spec=` explicitly

When a source has tabular files but no `datapackage.json`, the caller passes the spec directly:

```python
from corral import Package
from netstead.spec import load_gmns_spec

pkg = Package.from_source(
    "./my-csv-directory/",
    spec=load_gmns_spec("0.97"),
)
```

This says "the data is in the directory, the schema is in my toolchain, glue them together." It is the right escape hatch when the user knows what spec the directory was written against and the file layout matches what the spec expects. This is the only path that exists today, and it is enough for the GMNS use case.

### 2. Future extension surface — `SchemaProbe` registry (v1.1+)

For sources whose schema can be *inferred* from the source itself — GTFS (canonical filename set), OGC GeoPackage (schema in `gpkg_contents` metadata table), Avro (schema in file header), JSON Schema sidecar files — the long-term plan is a `SchemaProbe` registry that mirrors the existing `FormatAdapter` registry in `corral.io`. Each probe declares "if you see an X-shaped source, the schema is Y," and `Package.from_source` walks the registry when no explicit `spec=` is passed.

This is filed as [issue #177 — Design SchemaProbe registry pattern (parallel to FormatAdapter)](https://github.com/e-lo/netstead/issues/177) for v1.1. The shape is deliberately deferred until we have a second real probe target (GTFS or GeoPackage) driving the design — one-consumer abstractions tend to ossify around the first consumer's quirks.

### 3. Schema translator pattern

For sources that *carry their own schema description* in a non-Frictionless format (Avro headers, OGC GeoPackage metadata tables, JSON Schema files, GraphQL schemas), the right shape is a translator that converts the source's native schema into a Frictionless `DataPackage` at load time. The probe pattern in (2) is the registration mechanism; the translator is the conversion logic each probe implements.

Both (2) and (3) keep the rest of corral ignorant of where the schema came from — once the `DataPackage` is in hand, validation, scoping, editing, and rendering all behave identically regardless of whether the schema was hand-written, loaded from `datapackage.json`, or synthesised by a probe.

## See also

* [Architecture §6.1 (Engine + I/O)](../architecture.md#61-engine-io) — how Frictionless `Resource`s flow through the engine + adapter layer.
* [Architecture §6.3 (Validation + sync state)](../architecture.md#63-validation-sync-state) — how the FK graph is walked and how `DirtyTracker` stamps hashes per Resource.
* [Architecture §7 (Spec sync strategy)](../architecture.md#7-spec-sync-strategy) — how multiple GMNS spec versions live side-by-side as separate Data Packages.
* [netstead Schema reference](https://e-lo.github.io/netstead/netstead/reference/spec/) — the resolved GMNS schemas as corral sees them.
* [Issue #177 — SchemaProbe registry (v1.1)](https://github.com/e-lo/netstead/issues/177) — the planned extension surface for non-Frictionless sources.

## Further reading

* [Frictionless Data Package spec](https://datapackage.org/) — the canonical spec, OKFN-maintained.
* [Frictionless Table Schema spec](https://datapackage.org/standard/table-schema/) — the field-level schema spec referenced by Table Schemas.
* [Frictionless Python library](https://framework.frictionlessdata.io/) — note that corral uses the *spec*, not this library; the dep audit during Phase 1 concluded that a direct Pydantic v2 implementation (in `corral.spec`) gave better error messages and tighter typing than the upstream library, and avoided a heavy transitive dep tree.
* [GMNS upstream repository](https://github.com/zephyr-data-specs/GMNS) — the Zephyr Foundation specification that netstead vendors per-version.
