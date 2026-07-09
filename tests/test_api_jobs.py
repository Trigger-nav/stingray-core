"""Job-store lifecycle + eviction tests (ticket B1 contract point 1,
plan addition #3). Real end-to-end submission uses a short custom
origin/destination against RealGeography (found empirically during
implementation: a short custom route bypasses the legacy corridor DP grid
entirely and keeps the lattice tiny -- ~0.03-0.2s per call, vs. tens of
seconds for the full default Antibes<->Porto Cervo passage -- see
docs/plans/ticket-B1.md's implementation notes). Eviction-sweep tests
manipulate `JobStore._records` directly with hand-built `Future`s so they
don't need any real search to run at all.
"""

from __future__ import annotations

import dataclasses
import time
from concurrent.futures import Future

import pytest

from api.config import Settings
from api.jobs import JobError, JobRecord, JobStore, QueueFullError
from api.state import AppState, PlanJobPayload
from core.units import LatLon

# A short, real, navigable pair well clear of any coastline -- keeps every
# real end-to-end job in this file fast (see module docstring).
SHORT_ORIGIN = LatLon(43.0, 7.9)
SHORT_DESTINATION = LatLon(42.8, 8.1)


def _settings(**overrides) -> Settings:
    defaults = dict(
        role="vessel",
        weather_npz_path="data/weather/ecmwf_western_med.npz",
        pool_size=1,
        auth_user="u",
        auth_password="p",
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _short_payload(**overrides) -> PlanJobPayload:
    defaults = dict(
        pace=50.0,
        comfort=50.0,
        origin=SHORT_ORIGIN,
        destination=SHORT_DESTINATION,
        origin_is_anchorage=False,
        destination_is_anchorage=False,
        latest_arrival_h=None,
        departure_t0_h=0.0,
        speeds_kn=(12.0,),
        vessel_override=None,
    )
    defaults.update(overrides)
    return PlanJobPayload(**defaults)


@pytest.fixture
def app_state():
    state = AppState(_settings())
    yield state
    state.shutdown()


def test_submit_returns_a_job_id_immediately(app_state):
    store = JobStore(app_state.executor_holder, app_state.config)
    record = store.submit(_short_payload())
    assert record.job_id
    assert record.status in ("queued", "running")


def test_job_ids_are_unique(app_state):
    store = JobStore(app_state.executor_holder, app_state.config)
    ids = {store.submit(_short_payload()).job_id for _ in range(5)}
    assert len(ids) == 5


def test_get_unknown_job_returns_none(app_state):
    store = JobStore(app_state.executor_holder, app_state.config)
    assert store.get("does-not-exist") is None


def test_job_completes_with_a_real_result(app_state):
    store = JobStore(app_state.executor_holder, app_state.config)
    record = store.submit(_short_payload())
    job_id = record.job_id
    for _ in range(200):
        got = store.get(job_id)
        if got.status in ("done", "failed"):
            break
        time.sleep(0.05)
    assert got.status == "done", got.error
    assert got.result is not None
    assert got.result.candidates
    assert got.started_at is not None
    assert got.finished_at is not None
    assert got.finished_at >= got.started_at


def test_invalid_request_surfaces_as_invalid_request_error(app_state):
    # Corsica interior -- not navigable. PlanRequestIn/route-level fail-fast
    # validation (api/routes.py) normally catches this before submission;
    # this test exercises the worker-side classification path directly
    # (design 10's "failures inside a worker" branch) by submitting a
    # payload that bypasses that pre-check.
    store = JobStore(app_state.executor_holder, app_state.config)
    record = store.submit(_short_payload(origin=LatLon(42.3, 9.0)))
    job_id = record.job_id
    for _ in range(200):
        got = store.get(job_id)
        if got.status in ("done", "failed"):
            break
        time.sleep(0.05)
    assert got.status == "failed"
    assert got.error.code == "invalid_request"
    assert "not navigable" in got.error.message


def _make_finished_record(job_id: str, finished_at: float, failed: bool = False) -> JobRecord:
    future: Future = Future()
    if failed:
        future.set_exception(ValueError("boom"))
    else:
        future.set_result(None)
    record = JobRecord(job_id=job_id, submitted_at=finished_at - 1.0, future=future)
    # _refresh() (called by sweep()) sets finished_at from time.time() --
    # backdate it directly here so eviction-age tests are deterministic
    # rather than racing a real clock.
    record.finished_at = finished_at
    if failed:
        record.error = JobError(code="internal_error", message="boom")
    return record


def _bare_job_store(app_state) -> JobStore:
    return JobStore(app_state.executor_holder, app_state.config)


def test_sweep_evicts_records_past_ttl(app_state):
    store = _bare_job_store(app_state)
    store._config = dataclasses.replace(app_state.config, job_ttl_s=1.0, job_max_size=1000)
    now = time.time()
    with store._lock:
        store._records["old"] = _make_finished_record("old", finished_at=now - 10.0)
        store._records["fresh"] = _make_finished_record("fresh", finished_at=now)
    evicted = store.sweep()
    assert evicted == 1
    assert store.get("old") is None
    assert store.get("fresh") is not None


def test_sweep_never_evicts_queued_or_running(app_state):
    store = _bare_job_store(app_state)
    store._config = dataclasses.replace(app_state.config, job_ttl_s=0.001, job_max_size=1000)
    never_done_future: Future = Future()  # never resolved -> stays queued/running forever
    record = JobRecord(job_id="stuck", submitted_at=time.time() - 999, future=never_done_future)
    with store._lock:
        store._records["stuck"] = record
    time.sleep(0.01)
    evicted = store.sweep()
    assert evicted == 0
    assert store.get("stuck") is not None
    assert store.get("stuck").status in ("queued", "running")


def test_sweep_max_size_backstop_evicts_oldest_finished_first(app_state):
    store = _bare_job_store(app_state)
    store._config = dataclasses.replace(app_state.config, job_ttl_s=10_000.0, job_max_size=2)
    now = time.time()
    with store._lock:
        store._records["oldest"] = _make_finished_record("oldest", finished_at=now - 30)
        store._records["middle"] = _make_finished_record("middle", finished_at=now - 20)
        store._records["newest"] = _make_finished_record("newest", finished_at=now - 10)
    evicted = store.sweep()
    assert evicted == 1
    assert store.get("oldest") is None
    assert store.get("middle") is not None
    assert store.get("newest") is not None


def test_sweep_max_size_backstop_never_evicts_queued_or_running(app_state):
    # a huge backlog of finished jobs plus one still-running job: the
    # backstop must never touch the running one even if max_size is
    # already exceeded by finished records alone.
    store = _bare_job_store(app_state)
    store._config = dataclasses.replace(app_state.config, job_ttl_s=10_000.0, job_max_size=1)
    now = time.time()
    running_future: Future = Future()
    with store._lock:
        store._records["finished"] = _make_finished_record("finished", finished_at=now)
        store._records["running"] = JobRecord(
            job_id="running", submitted_at=now, future=running_future
        )
    store.sweep()
    assert store.get("running") is not None
    assert store.get("running").status in ("queued", "running")


def test_submit_raises_queue_full_at_the_configured_depth(app_state):
    # ticket B2 amendment: caps queued+running jobs, not the total stored
    # record count (job_max_size already bounds that separately).
    store = _bare_job_store(app_state)
    store._config = dataclasses.replace(app_state.config, max_queue_depth=2)
    now = time.time()
    with store._lock:
        store._records["a"] = JobRecord(job_id="a", submitted_at=now, future=Future())
        store._records["b"] = JobRecord(job_id="b", submitted_at=now, future=Future())
    with pytest.raises(QueueFullError):
        store.submit(_short_payload())


def test_submit_does_not_count_done_or_failed_jobs_toward_the_cap(app_state):
    store = _bare_job_store(app_state)
    store._config = dataclasses.replace(app_state.config, max_queue_depth=1)
    now = time.time()
    with store._lock:
        store._records["done"] = _make_finished_record("done", finished_at=now)
        store._records["failed"] = _make_finished_record("failed", finished_at=now, failed=True)
    # neither "done" nor "failed" counts as in-flight -- submission
    # succeeds despite two records already present.
    record = store.submit(_short_payload())
    assert record.job_id


def test_submit_refreshes_before_counting_so_a_completed_unpolled_job_does_not_overcount(app_state):
    # a future that has already resolved, but whose record has never been
    # polled (finished_at still None) -- JobRecord.status alone would
    # misread this as "queued" without submit()'s pre-count refresh.
    store = _bare_job_store(app_state)
    store._config = dataclasses.replace(app_state.config, max_queue_depth=1)
    resolved_future: Future = Future()
    resolved_future.set_result(None)
    with store._lock:
        store._records["stale"] = JobRecord(
            job_id="stale", submitted_at=time.time(), future=resolved_future
        )
    # would raise QueueFullError if "stale" were miscounted as in-flight.
    record = store.submit(_short_payload())
    assert record.job_id
