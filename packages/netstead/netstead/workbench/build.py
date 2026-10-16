"""The ``BuildNetwork`` pipeline, staged for a background job: plan → estimate → query → convert → write.

The session's job function calls these in order and owns the final open + register (which needs the
session lock). Each stage boundary is a :class:`~netstead.workbench.jobs.JobContext` checkpoint, so a
cancel stops the build between stages. Query, convert, and write run on a private
:class:`~corral.engines.ibis_engine.IbisEngine` that the caller closes afterwards, so a build never
shares a DuckDB connection with the networks the browser is reading.

A build never leaves a partial output behind and never overwrites one: it writes into a hidden
staging sibling of the destination (:func:`staging`), and only :func:`promote` moves the finished
output to its final name. The staging path is removed on any failure, including a cancel.

Staging outputs are hidden siblings named ``.partial-<8 hex>-<dest name>`` (plus a DuckDB
``.wal``). Only a hard crash of the process (power loss, ``kill -9``) can leave one behind;
:func:`remove_stale_partials` (called by the build job, never by the side-effect-free estimate)
deletes any such ``.partial-<8 hex>-*`` entry in the output folder older than 24 hours.

The OSM/Overture modules are imported at run time via :func:`~netstead.workbench.extras.optional_module`:
they need the ``[osm]`` / ``[overture]`` extras.
"""

from __future__ import annotations

import errno
import logging
import os
import re
import shutil
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from corral.engines.ibis_engine import IbisEngine

from netstead._network_build import network_from_records
from netstead.config import Settings
from netstead.network import Network
from netstead.overture.layout import LOCAL_SNAPSHOT_FILES, is_local_snapshot

from .actions import BuildNetwork
from .errors import ActionError
from .estimate import Estimate, SourceKind, count_osm, count_overture, estimate_build
from .extras import optional_module
from .jobs import JobContext
from .paths import classify_source

__all__ = [
    "WORLD_BBOX",
    "BuildPlan",
    "estimate_for",
    "fetch_and_convert",
    "output_path",
    "plan_build",
    "promote",
    "remove_stale_partials",
    "staging",
    "write_output",
]

#: The whole world: the Overture read bbox when a local snapshot is imported in full.
WORLD_BBOX = (-180.0, -90.0, 180.0, 90.0)
_SUFFIX = {"parquet": "", "csv": "", "duckdb": ".duckdb", "zip": ".zip"}
_FILE_KIND: dict[str, SourceKind] = {"osm": "osm_file", "overture": "overture_file"}
_OSM_FILE_SUFFIXES = (".osm", ".json")
#: The optional-extra modules each source's build imports; resolved up front by :func:`plan_build`.
_EXTRA_MODULES = {
    "osm": ("netstead.osm.query", "netstead.osm.local", "netstead.osm.convert"),
    "overture": ("netstead.overture.query", "netstead.overture.convert"),
}
#: Staging leftovers older than this (from a crashed build) are deleted by the next build in that folder.
STALE_PARTIAL_S = 24 * 3600
#: What :func:`staging` names its output: ``.partial-<uuid4 hex[:8]>-<dest name>``.
_STAGING_NAME = re.compile(r"\.partial-[0-9a-f]{8}-.+")
Records = tuple[list[dict[str, Any]], list[dict[str, Any]]]
Tick = Callable[[str, float], None]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BuildPlan:
    """Resolved, checked locations for one build."""

    kind: SourceKind
    dest: Path
    input_path: Path | None


def _local(path: str, what: str, settings: Settings) -> Path:
    """``path`` resolved inside ``io.allowed_roots``; a remote URL is an :class:`ActionError`."""
    kind, target = classify_source(path, settings)
    if kind == "remote":
        raise ActionError(f"{what} must be a local path, not a URL: {path}")
    return Path(target)


def output_path(output_dir: str, name: str, output_format: str, settings: Settings, *, what: str = "builds") -> Path:
    """Where ``name`` lands in ``output_dir`` (inside ``io.allowed_roots``); never an existing path.

    Raises:
        PathNotAllowed: ``output_dir`` is outside ``io.allowed_roots``.
        ActionError: ``output_dir`` is a URL, the folder does not exist, or the destination already does.
    """
    out_dir = _local(output_dir, "output_dir", settings)
    if not out_dir.is_dir():
        raise ActionError(f"output folder does not exist: {output_dir}")
    suffix = _SUFFIX[output_format]
    dest = out_dir / (name if name.lower().endswith(suffix) else f"{name}{suffix}")
    if dest.exists():
        raise ActionError(f"{dest} already exists; choose another name ({what} never overwrite)")
    return dest


def plan_build(action: BuildNetwork, settings: Settings) -> BuildPlan:
    """Check the action against the filesystem before any work: allowed roots, inputs, a free destination.

    Read-only (``/api/estimate`` calls it too); the build job cleans old staging leftovers itself.

    Raises:
        PathNotAllowed: the output folder or input file is outside ``io.allowed_roots``.
        ActionError: missing output folder, existing destination, an input of the wrong kind, or the
            source's ``[osm]``/``[overture]`` extra is not installed.
    """
    for module in _EXTRA_MODULES[action.source]:
        optional_module(module, action.source)  # fail now, not as an "unavailable" estimate later
    dest = output_path(action.output_dir, action.name, action.output_format, settings)
    if action.input_file is None:
        if action.source == "overture":
            _overture_data_root(settings)  # a local data_root must be inside the roots too: fail before any work
        return BuildPlan(kind=action.source, dest=dest, input_path=None)
    src = _local(action.input_file, "input_file", settings)
    if action.source == "osm" and not (src.is_file() and src.suffix.lower() in _OSM_FILE_SUFFIXES):
        raise ActionError(f"{action.input_file} is not a local .osm or Overpass .json file")
    if action.source == "overture" and not is_local_snapshot(src):
        raise ActionError(f"{action.input_file} is not a local Overture snapshot ({' + '.join(LOCAL_SNAPSHOT_FILES)})")
    return BuildPlan(kind=_FILE_KIND[action.source], dest=dest, input_path=src)


def _osm_options(settings: Settings) -> dict[str, Any]:
    osm_query = optional_module("netstead.osm.query", "osm")
    return {
        "endpoint": settings.osm.endpoint or osm_query.OVERPASS_URL,
        "user_agent": settings.osm.user_agent or osm_query.USER_AGENT,
    }


def _overture_data_root(settings: Settings) -> str | None:
    """``overture.data_root``: a remote URL as is, a local path resolved and checked against the roots.

    Raises :class:`~netstead.workbench.errors.PathNotAllowed` for a local root outside ``io.allowed_roots``
    (or an unsupported scheme), exactly like any other local path a build reads.
    """
    root = settings.overture.data_root
    return None if root is None else str(classify_source(root, settings)[1])


def _overture_read(action: BuildNetwork, plan: BuildPlan, settings: Settings) -> dict[str, Any]:
    """bbox, release, and data root for an Overture read (a local snapshot is read in full)."""
    overture_query = optional_module("netstead.overture.query", "overture")
    if plan.input_path is not None:
        bbox, data_root = WORLD_BBOX, str(plan.input_path)
    else:
        assert action.area is not None  # guaranteed by BuildNetwork's validator
        bbox, data_root = action.area.to_bbox(), _overture_data_root(settings)
    release = action.overture_release or settings.overture.release or overture_query.OVERTURE_RELEASE
    return {"bbox": bbox, "overture_release": release, "data_root": data_root}


def _count(action: BuildNetwork, plan: BuildPlan, settings: Settings, http: Any, engine: Any) -> int:
    if plan.input_path is not None and plan.kind == "osm_file":
        return plan.input_path.stat().st_size
    if plan.kind == "osm":
        assert action.area is not None
        return count_osm(action.area, network_type=action.network_type, http=http, **_osm_options(settings))
    read = _overture_read(action, plan, settings)
    return count_overture(read.pop("bbox"), network_type=action.network_type, engine=engine, **read)


def estimate_for(
    action: BuildNetwork, plan: BuildPlan, settings: Settings, *, http: Any = None, engine: Any = None
) -> Estimate:
    """Pre-query + cost model. Any failure becomes ``Estimate(seconds=None, basis="unavailable: ...")``."""
    own_engine = engine is None and plan.kind in ("overture", "overture_file")
    if own_engine:
        engine = IbisEngine()
    try:
        n = _count(action, plan, settings, http, engine)
    except ActionError:  # a missing extra is a real error, not an unknown size
        raise
    except Exception as exc:  # boundary: a failed pre-query is reported; the user may still choose to run
        return Estimate(
            seconds=None, out_bytes=None, n_elements=None, basis=f"unavailable: {type(exc).__name__}: {exc}"
        )
    finally:
        if own_engine:
            engine.close()
    return estimate_build(plan.kind, n, output_format=action.output_format)


def _osm_records(action: BuildNetwork, plan: BuildPlan, settings: Settings, http: Any, tick: Tick) -> Records:
    convert = optional_module("netstead.osm.convert", "osm")
    local = optional_module("netstead.osm.local", "osm")
    query = optional_module("netstead.osm.query", "osm")
    if plan.input_path is not None:
        nodes, ways = local.read_osm_file(plan.input_path, network_type=action.network_type)
    else:
        assert action.area is not None
        q = query.build_overpass_query(
            bbox=action.area.to_bbox(),
            polygon=action.area.to_polygon(),
            network_type=action.network_type,
            timeout=settings.osm.timeout,
        )
        options = {"timeout": settings.osm.timeout, "retries": settings.osm.retries, **_osm_options(settings)}
        nodes, ways = query.parse_overpass_elements(query.fetch_osm(q, session=http, **options))
    tick("convert", 0.5)
    return convert.build_node_link_tables(nodes, ways, extra_tags=action.extra_tags)


def _overture_records(action: BuildNetwork, plan: BuildPlan, settings: Settings, engine: Any, tick: Tick) -> Records:
    convert = optional_module("netstead.overture.convert", "overture")
    query = optional_module("netstead.overture.query", "overture")
    read = _overture_read(action, plan, settings)
    bbox = read.pop("bbox")
    segments = query.read_segments(
        bbox, network_type=action.network_type, extra_tags=action.extra_tags, engine=engine, **read
    )
    connectors = query.read_connectors(segments, engine=engine, **read)
    tick("convert", 0.5)
    return convert.build_node_link_tables(segments, connectors, extra_tags=action.extra_tags)


def fetch_and_convert(
    action: BuildNetwork,
    plan: BuildPlan,
    settings: Settings,
    ctx: JobContext,
    *,
    estimate: Estimate,
    http: Any,
    engine: Any,
) -> Network:
    """Stages ``query`` and ``convert``: read the source and assemble the GMNS network on ``engine``.

    Raises:
        ActionError: the fetch/read failed, or nothing matched ``network_type`` in the area.
    """
    started = time.time()

    def tick(stage: str, progress: float) -> None:
        eta = None if estimate.seconds is None else max(estimate.seconds - (time.time() - started), 0.0)
        ctx.stage(stage, progress=progress, eta_s=eta)

    try:
        tick("query", 0.1)
        if action.source == "osm":
            node_records, link_records = _osm_records(action, plan, settings, http, tick)
        else:
            node_records, link_records = _overture_records(action, plan, settings, engine, tick)
    except (ValueError, LookupError, OSError) as exc:
        # The same set `netstead build` reports: bad network_type / malformed input / missing connector
        # (ValueError, LookupError) and Overpass or object-store I/O (requests' errors subclass OSError).
        raise ActionError(f"build failed: {exc}") from exc
    if not link_records:
        raise ActionError(f"nothing in this {action.source} area matched network_type={action.network_type!r}")
    return network_from_records(
        node_records,
        link_records,
        spec_version=action.spec_version or settings.io.spec_version,
        engine=engine,
        dataset_name=f"{action.source}_export",
    )


def _remove(path: Path) -> None:
    """Delete ``path`` (file, folder, or link) and a DuckDB ``.wal`` beside it; missing is fine."""
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)
    path.with_name(f"{path.name}.wal").unlink(missing_ok=True)


def remove_stale_partials(out_dir: Path) -> None:
    """Delete staging leftovers in ``out_dir`` older than :data:`STALE_PARTIAL_S`.

    Only names :func:`staging` generates (``.partial-<8 hex>-<name>``, plus their ``.wal``) count: a
    user's own ``.partial-notes`` or ``.partial-old`` in the output folder is never touched.
    """
    cutoff = time.time() - STALE_PARTIAL_S
    for leftover in out_dir.glob(".partial-*"):
        if not _STAGING_NAME.fullmatch(leftover.name):
            continue
        try:
            if leftover.lstat().st_mtime < cutoff:
                _remove(leftover)
        except OSError:  # vanished or not ours to delete: never block a build on cleanup
            logger.warning("could not remove stale build leftover %s", leftover)


@contextmanager
def staging(dest: Path) -> Iterator[Path]:
    """Yield a hidden sibling of ``dest`` to write into; it is removed on exit unless :func:`promote` moved it.

    The staging name keeps ``dest``'s suffix (``.partial-<id>-tiny.duckdb``), so format detection still works.
    """
    tmp = dest.with_name(f".partial-{uuid.uuid4().hex[:8]}-{dest.name}")
    try:
        yield tmp
    finally:
        _remove(tmp)


def write_output(net: Network, tmp: Path, output_format: str, ctx: JobContext) -> None:
    """Stage ``write``: persist ``net`` at the staging path ``tmp`` (from :func:`staging`).

    The output is a new copy, never the source, so corral's :class:`~corral.OutOfSyncWarning` (an edited
    table is "stale" against the source it was read from) does not apply: the write goes through a proxy
    whose tables are flagged clean, and a caller that wants to say which tables were edited reads
    ``Table.dirty`` itself. A filter or ``warnings.catch_warnings`` could not do this: corral attributes
    the warning to :mod:`netstead.network`, and ``catch_warnings`` is process-global, so on a job thread
    it would swallow other threads' warnings.

    Raises:
        ActionError: the write failed with an ``OSError`` (the caller's :func:`staging` cleans up).
    """
    ctx.stage("write", progress=0.75)
    clean = replace(net, tables={name: replace(t, dirty=False) for name, t in net.tables.items()})
    try:
        clean.write(tmp, format=output_format)
    except OSError as exc:  # disk full, permissions: user-facing, not an internal error
        raise ActionError(f"could not write the {output_format} output: {exc}") from exc


#: ``os.link`` errors meaning "this filesystem has no hard links" (Linux reports EPERM for FAT/vfat).
_NO_HARD_LINKS = frozenset({errno.ENOTSUP, errno.EOPNOTSUPP, errno.EPERM})


def _claim_then_replace(tmp: Path, dest: Path) -> None:
    """Move file ``tmp`` to ``dest`` without hard links, still never clobbering an existing ``dest``.

    ``O_CREAT | O_EXCL`` claims ``dest`` atomically (``FileExistsError`` if anything is there), then
    ``os.replace`` swaps the finished file over our own empty placeholder. Only that placeholder can
    be replaced; if the swap fails it is removed again.
    """
    os.close(os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644))
    try:
        os.replace(tmp, dest)
    except OSError:
        dest.unlink(missing_ok=True)
        raise


def promote(tmp: Path, dest: Path) -> None:
    """Move the finished staging output to ``dest``, never clobbering anything that appeared there meanwhile.

    A file output (DuckDB, zip) is hard-linked to ``dest``, which fails atomically if ``dest`` exists,
    and then the staging name is unlinked. On a filesystem without hard links (exFAT, some network
    shares) it falls back to :func:`_claim_then_replace`. A folder output (Parquet, CSV) is checked,
    then renamed.

    Raises:
        ActionError: ``dest`` already exists, or the move failed.
    """
    taken = f"{dest} already exists; choose another name (builds never overwrite)"
    try:
        if tmp.is_dir():
            if dest.exists() or dest.is_symlink():  # rename() would replace an empty folder
                raise ActionError(taken)
            os.replace(tmp, dest)
        else:
            try:
                os.link(tmp, dest)
            except OSError as exc:
                if exc.errno not in _NO_HARD_LINKS:
                    raise
                _claim_then_replace(tmp, dest)
            else:
                tmp.unlink()
    except FileExistsError as exc:
        raise ActionError(taken) from exc
    except OSError as exc:
        if dest.exists():
            raise ActionError(taken) from exc
        raise ActionError(f"could not move the finished output to {dest}: {exc}") from exc
