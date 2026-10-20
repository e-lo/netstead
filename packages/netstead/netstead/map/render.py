"""Composition layer over :class:`netstead.map.NetworkMap`.

These functions are thin wrappers around the embeddable component:

* :func:`render_network_html` — full HTML doc of just the network
  (delegates to :meth:`NetworkMap.to_html`).
* :func:`render_validation_html` — full validation report page composing
  a NetworkMap with the findings-table chrome (counts chips, filter bar,
  table, unlocated sidebar).

The composition is intentionally narrow: every cross-cutting feature
(layers, tooltips, marker popups, OSM deep links) lives on
:class:`NetworkMap`. The findings-table HTML is the only thing this
module owns, because it is specific to validation reports and not part
of the embeddable map.
"""

from __future__ import annotations

from importlib import resources
from typing import TYPE_CHECKING, Any

from corral.reports.grouping import group_issues, resource_inventory, strip_source_prefix

from .component import NetworkMap, _severity_counts

if TYPE_CHECKING:  # pragma: no cover - typing only
    from corral.reports import Issue, ValidationReport

    from netstead.network import Network

__all__ = ["render_network_html", "render_validation_html"]


def render_network_html(
    network: Network,
    issues: list[Issue] | None = None,
    *,
    title: str | None = None,
    tile_provider: str = "esri",
    osm_editor: str = "id",
) -> str:
    """Render a single self-contained HTML map of ``network``.

    Thin convenience over :meth:`NetworkMap.to_html`. For a richer
    page composed with the findings table, see
    :func:`render_validation_html`.
    """
    return NetworkMap(
        network,
        issues=issues,
        title=title,
        tile_provider=tile_provider,
        osm_editor=osm_editor,
    ).to_html()


def render_validation_html(
    network: Network,
    report: ValidationReport,
    **opts: Any,
) -> str:
    """Render a validation report page: NetworkMap + findings table.

    Equivalent to::

        m = NetworkMap(network, issues=report.issues, **opts)
        # ...then composed with a findings table around it.

    The findings table chrome (severity chips, filter bar, the issue
    rows themselves, the "unlocated findings" block) is rendered in
    :file:`templates/validation_report.html.j2` — see there for the
    structure. The map fragment is dropped in via
    :meth:`NetworkMap.body_fragment` so the same component you embed
    elsewhere (notebook, custom page) is the one in the report.

    Two things keep a partial package's report readable (2026-10-20
    beta feedback): a "Resources" table built from
    :func:`corral.reports.grouping.resource_inventory` says which spec
    tables are present instead of one finding per absent table, and
    the collapsed "Unlocated findings" block folds structural findings
    that differ only by resource into one line
    (:func:`corral.reports.grouping.group_issues`). The source path is
    printed once, in the subtitle; the ``package source '…': `` lead-in
    is stripped from every message.
    """
    try:
        from jinja2 import Environment, StrictUndefined
    except ImportError as e:  # pragma: no cover - defensive
        raise ImportError(
            "netstead.map.render_validation_html requires jinja2 from the [reports] extra: "
            "pip install 'netstead[reports]'"
        ) from e

    title = opts.pop("title", None) or "validation report"
    issues = list(report.issues)
    component = NetworkMap(network, issues=issues, title=title, **opts)

    enriched = component.enriched_issues()
    for entry in enriched:
        entry["message"] = strip_source_prefix(entry["message"], report.source)
    located = [e for e in enriched if e["located"]]
    unlocated = [e for e in enriched if not e["located"]]
    counts = _severity_counts(enriched)
    resources_rows = resource_inventory(network)
    filter_options = {
        "severity": sorted({e["severity"] for e in enriched}),
        "table": sorted({e["table"] for e in enriched if e["table"]}),
    }

    template_source = (resources.files("netstead.map") / "templates" / "validation_report.html.j2").read_text(
        encoding="utf-8"
    )
    env = Environment(autoescape=True, undefined=StrictUndefined)
    template = env.from_string(template_source)
    return template.render(
        title=title,
        meta_subtitle=_validation_subtitle(network, report),
        counts=counts,
        head_assets=NetworkMap.head_assets(),
        body_fragment=component.body_fragment(),
        issues=enriched,
        filter_options=filter_options,
        located_count=len(located),
        unlocated=unlocated,
        unlocated_groups=_unlocated_groups(issues, enriched, source=report.source),
        resources=resources_rows,
        resources_present=sum(1 for r in resources_rows if r["present"]),
    )


def _unlocated_groups(
    issues: list[Issue],
    enriched: list[dict[str, Any]],
    *,
    source: str | None,
) -> list[dict[str, Any]]:
    """One entry per line of the "Unlocated findings" block.

    Groups the unlocated issues with :func:`group_issues`; a grouped
    entry carries the full resource list (no ``(+N more)`` — it is a
    web page) and links to the findings-table row of its first member.
    ``enriched[i]`` describes ``issues[i]`` (both come from the same
    ``NetworkMap.issues`` list), which is how an issue maps back to its
    ``issue_id``.
    """
    unlocated_pairs = [(issue, entry) for issue, entry in zip(issues, enriched, strict=True) if not entry["located"]]
    issue_id_of = {id(issue): entry["issue_id"] for issue, entry in unlocated_pairs}
    groups: list[dict[str, Any]] = []
    for group in group_issues([issue for issue, _ in unlocated_pairs]):
        first = group.issues[0]
        groups.append(
            {
                "issue_id": issue_id_of[id(first)],
                "severity": group.severity.value,
                "code": group.code,
                "count": group.count,
                "grouped": group.groupable,
                "message": (
                    group.summary(limit=len(group.table_names))
                    if group.groupable
                    else strip_source_prefix(first.message, source)
                ),
            }
        )
    return groups


def _validation_subtitle(network: Network, report: ValidationReport) -> str | None:
    """Subtitle for the validation report header — version + source."""
    bits: list[str] = []
    spec = getattr(report, "spec_version", None) or getattr(network, "spec_version", None)
    if spec:
        bits.append(f"GMNS {spec}")
    if report.source:
        bits.append(str(report.source))
    return " · ".join(bits) if bits else None
