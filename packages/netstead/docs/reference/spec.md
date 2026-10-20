---
title: Schema reference
audience: both
kind: reference
summary: Field-level GMNS schema — every table and column of the official spec's master branch, rendered at build time (vendored 0.97 when offline).
---

# Official GMNS Specification

The GMNS specification is managed in the following Github repo: <https://github.com/zephyr-data-specs/GMNS>

This page renders the upstream `master` branch of that repository at docs build time: the macro fetches `Specification_md/datapackage.json` from GitHub and tabulates each resource and its schema. When the fetch fails (offline build, GitHub unreachable) it logs a warning and falls back to the vendored 0.97 spec at `packages/netstead/netstead/spec/0.97/`, so the tables below may be the vendored copy rather than the live branch.

| **GMNS Version** | **Description** |
| ----- | ----- |
| [development](../spec-versions/development.md) | Not yet in official spec. Hasn't been voted on by PMG |
| [local](../spec-versions/local.md) | Local copy of spec. May not be identical to official spec. |

GMNS is defined using the [Frictionless Framework](https://specs.frictionlessdata.io/#data-package-specs-suite) as a series of `tabular-data-resources`.

## Files in Specification

GMNS consists of a package of files as defined in the following table.

*The following tables are automatically generated from the official spec.*

{{ official_frictionless_spec('master') }}

## Data Table Schemas

{{ official_frictionless_schemas('master') }}
