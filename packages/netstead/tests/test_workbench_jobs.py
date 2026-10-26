"""Tests for the background JobRunner: lifecycle, events, cancellation, failure, concurrency."""

import threading
import time

import pytest
from netstead.workbench.errors import ActionError
from netstead.workbench.jobs import JobRunner

WAIT = 5.0  # generous upper bound; every wait below normally returns in milliseconds


@pytest.fixture
def events():
    return []


@pytest.fixture
def runner(events):
    lock = threading.Lock()

    def publish(event):
        with lock:
            events.append(event)

    return JobRunner(publish)


def test_success_runs_stages_and_finishes(runner, events):
    def fn(ctx):
        ctx.stage("query", progress=0.5, eta_s=3.0)
        return {"answer": 42}

    finished = []
    job = runner.submit("build_network", "build x", fn, on_finish=finished.append)
    assert job.wait(WAIT)
    snap = runner.snapshot(job)
    assert (snap["status"], snap["stage"], snap["progress"], snap["eta_s"]) == ("done", "done", 1.0, None)
    assert snap["result"] == {"answer": 42} and snap["finished"] is not None
    assert finished == [job]
    stages = [e["job"]["stage"] for e in events if e["type"] == "job"]
    assert stages[0] == "starting" and "query" in stages and stages[-1] == "done"
    assert "_cancel" not in snap and "_done" not in snap


def test_cancel_takes_effect_at_next_stage(runner):
    entered, release = threading.Event(), threading.Event()

    def fn(ctx):
        ctx.stage("query")
        entered.set()
        release.wait(WAIT)  # simulate an uninterruptible download
        ctx.stage("convert")  # checkpoint: raises JobCancelled
        raise AssertionError("must not get here")

    job = runner.submit("build_network", "build x", fn)
    assert entered.wait(WAIT)
    assert runner.cancel(job.id)["cancel_requested"] is True
    assert runner.snapshot(job)["status"] == "running"  # still inside the stage
    release.set()
    assert job.wait(WAIT)
    snap = runner.snapshot(job)
    assert snap["status"] == "canceled" and snap["error_type"] == "JobCancelled" and snap["stage"] == "query"


def test_cancel_finished_job_is_a_noop(runner):
    job = runner.submit("open_network", "open x", lambda ctx: None)
    assert job.wait(WAIT)
    assert runner.cancel(job.id)["status"] == "done"


def test_action_error_is_a_failure_with_payload(runner):
    class Gate(ActionError):
        pass

    def fn(ctx):
        exc = Gate("approval required")
        exc.payload = {"estimate": {"seconds": 999}}
        raise exc

    job = runner.submit("build_network", "b", fn)
    assert job.wait(WAIT)
    snap = runner.snapshot(job)
    assert (snap["status"], snap["error"], snap["error_type"]) == ("failed", "approval required", "Gate")
    assert snap["payload"] == {"estimate": {"seconds": 999}}


def test_crash_is_an_internal_error_not_a_dead_thread(runner):
    def fn(ctx):
        raise KeyError("boom")

    job = runner.submit("open_network", "o", fn)
    assert job.wait(WAIT)
    snap = runner.snapshot(job)
    assert snap["status"] == "failed" and snap["error_type"] == "InternalError" and "KeyError" in snap["error"]


def test_broken_on_finish_still_releases_waiters(runner):
    def on_finish(job):
        raise RuntimeError("callback bug")

    job = runner.submit("open_network", "o", lambda ctx: 1, on_finish=on_finish)
    assert job.wait(WAIT) and runner.snapshot(job)["status"] == "done"


def test_jobs_run_concurrently_and_list_newest_first(runner):
    both_running = threading.Barrier(2, timeout=WAIT)

    def fn(ctx):
        both_running.wait()  # deadlocks (BrokenBarrierError) unless both jobs run at once
        return "ok"

    a = runner.submit("open_network", "a", fn)
    b = runner.submit("open_network", "b", fn)
    assert a.wait(WAIT) and b.wait(WAIT)
    assert [j["status"] for j in runner.snapshots()] == ["done", "done"]
    assert [j["id"] for j in runner.snapshots()] == [b.id, a.id]


def test_terminal_event_is_published_before_wait_returns():
    events = []

    def slow_publish(event):
        time.sleep(0.01)  # widen the window between finishing and publishing
        events.append(event)

    job = JobRunner(slow_publish).submit("open_network", "o", lambda ctx: 1)
    assert job.wait(WAIT)
    assert events[-1]["job"]["status"] == "done"


def test_publish_failure_still_finishes_the_job():
    calls = []

    def publish(event):
        calls.append(event)
        if len(calls) == 1:
            raise RuntimeError("bus down")

    runner = JobRunner(publish)
    job = runner.submit("open_network", "o", lambda ctx: 1)
    assert job.wait(WAIT)
    snap = runner.snapshot(job)
    assert snap["status"] == "failed" and snap["error_type"] == "InternalError" and "bus down" in snap["error"]


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_base_exception_in_job_still_finishes_the_job(runner):
    def fn(ctx):
        raise SystemExit("bye")

    job = runner.submit("open_network", "o", fn)
    assert job.wait(WAIT)
    # the runner re-raises SystemExit after finishing the job; join so pytest reports it here, not in the next test
    for thread in threading.enumerate():
        if thread.name == f"netstead-{job.id}":
            thread.join(WAIT)
    snap = runner.snapshot(job)
    assert snap["status"] == "failed" and snap["error_type"] == "InternalError" and "SystemExit" in snap["error"]


def test_thread_start_failure_finishes_the_job(runner, monkeypatch):
    def refuse(self):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(threading.Thread, "start", refuse)
    with pytest.raises(RuntimeError, match="can't start"):
        runner.submit("open_network", "o", lambda ctx: 1)
    monkeypatch.undo()
    (snap,) = runner.snapshots()
    assert snap["status"] == "failed" and snap["finished"] is not None
    assert runner.get(snap["id"]).wait(0)


def test_cancel_after_finish_publishes_nothing(runner, events):
    job = runner.submit("open_network", "o", lambda ctx: 1)
    assert job.wait(WAIT)
    before = len(events)
    runner.cancel(job.id)
    assert len(events) == before


def test_unknown_job():
    with pytest.raises(KeyError, match="unknown job"):
        JobRunner(lambda e: None).get("job-99")
