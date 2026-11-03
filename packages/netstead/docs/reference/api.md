---
title: netstead API reference
audience: both
kind: reference
summary: Core public netstead symbols, auto-generated from docstrings. Stable anchors match the codes in ai/api-index.json. For the generic corral API see the corral reference.
stability: stable
---

# netstead API reference

GMNS-specific surface. For the generic Frictionless data-package primitives (`Package`, `Table`, validation, editing framework, HTTP + MCP factories) see the [corral API reference](https://e-lo.github.io/netstead/corral/reference/api/).

Auto-generated from package docstrings via [mkdocstrings](https://mkdocstrings.github.io/). Stable anchors match the codes in [`ai/api-index.json`](../ai/api-index.json). This page covers the core surface — `Network`, spec, semantics, scope, quality, indexes, graph, clean — plus the builders, selection, map, settings and Workbench entry points; smaller helpers are documented in their modules' docstrings.

## Network — the GMNS-aware Package

::: netstead.Network
    options:
      members:
        - from_source
        - validate
        - links
        - nodes
        - geometry
        - lanes
        - link_tod
        - segments
        - spec_version
      show_root_heading: true

::: netstead.NetworkError

## Spec

::: netstead.spec.SUPPORTED_SPECS

::: netstead.spec.DEFAULT_SPEC

::: netstead.spec.load_gmns_spec

## Semantics

::: netstead.semantics.is_connected

::: netstead.semantics.connected_components

::: netstead.semantics.assemble_link_geometry

::: netstead.semantics.resolve_link_attrs_at

## Scope

::: netstead.scope.NetworkScope
    options:
      members:
        - apply
        - union
        - intersect
        - subtract
        - buffer_network
        - buffer_spatial

::: netstead.scope.from_nodes

::: netstead.scope.from_node

::: netstead.scope.from_link

::: netstead.scope.from_point

::: netstead.scope.connected_component

::: netstead.scope.from_zone

## Quality (GMNS rule pack)

::: netstead.quality.register_all

The rule classes:

::: netstead.quality.HighSpeedResidentialRule
::: netstead.quality.DisconnectedComponentsRule
::: netstead.quality.LaneCountMismatchRule
::: netstead.quality.DuplicateNearNodesRule
::: netstead.quality.SharpAngleBendsRule
::: netstead.quality.ImplausibleVcRule
::: netstead.quality.MissingCriticalFieldsRule
::: netstead.quality.NonUniqueCombinationRule
::: netstead.quality.OverlappingLinksRule

## Indexes (optional `[clean]` extra)

::: netstead.indexes.SpatialIndex

::: netstead.indexes.build_indexes

## Graph / routing (optional `[graph]` extra)

::: netstead.graph.GMNSGraph

::: netstead.graph.S2Overlay

## Clean (optional `[clean]` extra)

::: netstead.clean.simplify_geometry

::: netstead.clean.merge_close_nodes

::: netstead.clean.remove_orphans

::: netstead.clean.recompute_lengths

## Build from OpenStreetMap (optional `[osm]` extra)

::: netstead.osm.build_network_from_osm

::: netstead.osm.build_network_from_osm_file

::: netstead.osm.network_from_records

::: netstead.osm.osm_edit_url

::: netstead.osm.issue_osm_edit_url

## Build from Overture Maps (optional `[overture]` extra)

::: netstead.overture.build_network_from_overture

## Natural-language selection (optional `[nl]` extra)

::: netstead.select.make_parser

::: netstead.select.resolve

::: netstead.select.resolve_frames

::: netstead.select.to_fragment

::: netstead.select.to_projectcard

::: netstead.select.validate_fragment

::: netstead.select.SelectionIntent

::: netstead.select.SelectionResult

::: netstead.select.StubParser

::: netstead.select.StreetIndex

::: netstead.select.grammar_first

## Map

::: netstead.map.NetworkMap

::: netstead.map.render_network_html

::: netstead.map.render_validation_html

## Settings

See [Settings reference](settings.md) for every key, default and precedence rule.

::: netstead.config.Settings

::: netstead.config.load_settings

::: netstead.config.save_setting

::: netstead.config.get_value

::: netstead.config.user_config_path

::: netstead.config.project_config_path

::: netstead.config.SettingsError

## Workbench

See [Workbench Actions reference](workbench-actions.md) for every Action's fields and replay form.

::: netstead.workbench.Session
    options:
      members:
        - dispatch
        - dispatch_recorded
        - submit
        - add_network
        - network
        - state
        - settings
        - history

::: netstead.workbench.actions.BaseAction

::: netstead.workbench.actions.CORE_ACTIONS

::: netstead.workbench.actions.to_python

::: netstead.workbench.actions.parse_action

::: netstead.workbench.ActionError

::: netstead.workbench.ApprovalRequired

::: netstead.workbench.PathNotAllowed

::: netstead.workbench.NotSupportedYet

::: netstead.workbench.JobCancelled

## See also

* [corral API reference](https://e-lo.github.io/netstead/corral/reference/api/) — the generic primitives netstead is built on.
* [Schema reference](spec.md) — GMNS field-level reference.
* [Table of tables](table-of-tables.md) — "which table do I use?" entry point.
* [Glossary](glossary.md) — terms used in this API.
