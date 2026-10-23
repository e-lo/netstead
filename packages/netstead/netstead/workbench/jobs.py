"""Background jobs: one thread per job, staged progress pushed as ``job`` events, cooperative cancel.

A job function receives a :class:`JobContext`. It calls ``ctx.stage(name, ...)`` at each stage
boundary; that publishes progress and is also the cancellation checkpoint (it raises
:class:`~netstead.workbench.errors.JobCancelled` once :meth:`JobRunner.cancel` was called). Work
inside one stage (an Overpass download, a DuckDB read) is not interrupted, so a cancel takes
effect at the next boundary. A cancel that arrives after the last checkpoint does not undo the
work: the job ends ``status="done"`` with ``cancel_requested=True``.

A job's ``result`` is published in every later event and kept for the session's lifetime, so it
must be small, JSON-able, and never mutated after the job function returns.

Thread-safety: every mutation of a :class:`Job` and every snapshot of it (``to_dict``) happens
under the runner's lock. The runner never touches session state; the ``on_finish`` callback
(run on the job thread, before the job is marked finished for :meth:`Job.wait`) is where the
session records history.

Events: ``_emit`` snapshots and publishes under a separate emit lock, so events go out in the
order their snapshots were taken (a stale ``running`` snapshot can never follow the terminal
one). The runner lock is never held while publishing. ``publish`` must therefore be
non-blocking and must not call back into the runner or take the session lock
(``EventBus.publish`` qualifies). The terminal event is published before :meth:`Job.wait`
returns. ``cancel`` on a finished job publishes nothing, so every terminal event is published
after ``on_finish`` ran and carries the ``history_seq`` it set.

A job can never stay ``running`` forever: whatever fails (``publish``, starting the thread, the
job function, even with a ``BaseException``), the job ends ``failed`` and :meth:`Job.wait`
returns.
"""

from __future__ import annotations

import itertools
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from .errors import ActionError, JobCancelled

__all__ = ["Job", "JobContext", "JobRunner", "JobStatus"]

logger = logging.getLogger(__name__)

JobStatus = Literal["running", "done", "failed", "canceled"]


@dataclass
class Job:
    """One background job's observable state."""

    id: str
    kind: str
    label: str
    status: JobStatus = "running"
    stage: str = "starting"
    progress: float | None = None
    eta_s: float | None = None
    error: str | None = None
    error_type: str | None = None
    payload: dict[str, Any] | None = None
    result: Any = None
    history_seq: int | None = None
    started: float = field(default_factory=time.time)
    finished: float | None = None
    cancel_requested: bool = False
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    _done: threading.Event = field(default_factory=threading.Event, repr=False)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe public fields (call through :meth:`JobRunner.snapshot` for a consistent view)."""
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}

    def wait(self, timeout: float | None = None) -> bool:
        """Block until the job finished (and ``on_finish`` ran); ``False`` on timeout."""
        return self._done.wait(timeout)


class JobContext:
    """What a job function sees: stage reporting and the cancellation checkpoint."""

    def __init__(self, job: Job, runner: JobRunner) -> None:
        """Bind to ``job`` on ``runner``."""
        self._job = job
        self._runner = runner

    @property
    def job_id(self) -> str:
        """The id of the job this context belongs to."""
        return self._job.id

    @property
    def canceled(self) -> bool:
        """Whether cancellation was requested."""
        return self._job._cancel.is_set()

    def check(self) -> None:
        """Raise :class:`JobCancelled` if cancellation was requested."""
        if self.canceled:
            raise JobCancelled(f"{self._job.label}: canceled")

    def stage(self, name: str, *, progress: float | None = None, eta_s: float | None = None) -> None:
        """Enter stage ``name`` (a cancellation checkpoint) and publish the job's new state."""
        self.check()
        self._runner.update(self._job, stage=name, progress=progress, eta_s=eta_s)


class JobRunner:
    """Start, track, and cancel background jobs; publish ``{"type": "job", "job": ...}`` events."""

    def __init__(self, publish: Callable[[dict[str, Any]], None]) -> None:
        """Publish job events through ``publish`` (the session's ``EventBus.publish``)."""
        self._publish = publish
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._emit_lock = threading.Lock()  # orders publishes; always taken before ``_lock``
        self._ids = itertools.count(1)

    def submit(
        self,
        kind: str,
        label: str,
        fn: Callable[[JobContext], Any],
        *,
        on_finish: Callable[[Job], None] | None = None,
    ) -> Job:
        """Start ``fn(ctx)`` on a new daemon thread and return its :class:`Job` immediately."""
        with self._lock:
            job = Job(id=f"job-{next(self._ids)}", kind=kind, label=label)
            self._jobs[job.id] = job
        thread = threading.Thread(target=self._run, args=(job, fn, on_finish), name=f"netstead-{job.id}", daemon=True)
        try:
            thread.start()
        except BaseException as exc:
            self._settle(job, _internal_error(exc))
            job._done.set()
            raise
        return job

    def get(self, job_id: str) -> Job:
        """The job with ``job_id`` (``KeyError("unknown job ...")`` if none)."""
        with self._lock:
            try:
                return self._jobs[job_id]
            except KeyError:
                raise KeyError(f"unknown job {job_id!r}") from None

    def snapshot(self, job: Job) -> dict[str, Any]:
        """A consistent JSON-safe copy of ``job``."""
        with self._lock:
            return job.to_dict()

    def snapshots(self) -> list[dict[str, Any]]:
        """Snapshots of every job, newest first."""
        with self._lock:
            return [j.to_dict() for j in reversed(self._jobs.values())]

    def cancel(self, job_id: str) -> dict[str, Any]:
        """Request cancellation (no-op for a finished job) and return the job's snapshot."""
        job = self.get(job_id)
        with self._lock:
            running = job.finished is None
            if running:
                job.cancel_requested = True
                job._cancel.set()
        if running:
            self._emit(job)
        return self.snapshot(job)

    def update(self, job: Job, **changes: Any) -> None:
        """Set fields on ``job`` under the lock and publish it (used by stages and by ``on_finish``)."""
        with self._lock:
            for key, value in changes.items():
                setattr(job, key, value)
        self._emit(job)

    # ------------------------------------------------------------------ internals

    def _emit(self, job: Job) -> None:
        with self._emit_lock:
            self._publish({"type": "job", "job": self.snapshot(job)})

    def _settle(self, job: Job, outcome: dict[str, Any]) -> None:
        with self._lock:
            for key, value in outcome.items():
                setattr(job, key, value)
            job.eta_s = None
            job.finished = time.time()

    def _run(self, job: Job, fn: Callable[[JobContext], Any], on_finish: Callable[[Job], None] | None) -> None:
        reraise: BaseException | None = None
        try:
            outcome: dict[str, Any]
            try:
                self._emit(job)  # "starting", on this thread so it precedes the first stage event
                result = fn(JobContext(job, self))
                outcome = {"status": "done", "stage": "done", "progress": 1.0, "result": result}
            except JobCancelled as exc:
                outcome = {"status": "canceled", "error": str(exc), "error_type": "JobCancelled"}
                outcome["payload"] = exc.payload
            except ActionError as exc:
                outcome = {"status": "failed", "error": str(exc), "error_type": type(exc).__name__}
                outcome["payload"] = exc.payload
            except BaseException as exc:  # boundary: a crashed job is a reported failure, never a dead thread
                logger.exception("workbench job %s (%s) crashed", job.id, job.kind)
                outcome = _internal_error(exc)
                if not isinstance(exc, Exception):
                    reraise = exc  # SystemExit etc.: record the failure, then let it propagate
            self._settle(job, outcome)
            try:
                if on_finish is not None:
                    on_finish(job)
            except Exception:  # boundary: a broken callback must not leave waiters blocked forever
                logger.exception("workbench job %s on_finish failed", job.id)
            self._emit(job)  # before ``_done``: a waiter sees the terminal event already published
        finally:
            job._done.set()
        if reraise is not None:
            raise reraise


def _internal_error(exc: BaseException) -> dict[str, Any]:
    return {"status": "failed", "error": f"internal error: {type(exc).__name__}: {exc}", "error_type": "InternalError"}
