"""Job-shaped submission/polling (ticket B1 contract point 1): submit ->
job id -> poll for result. No Celery/Redis -- the "queue" is a
`ProcessPoolExecutor`'s own task queue (api/state.py) plus this in-memory
`dict[str, JobRecord]`.

TTL/max-size eviction (plan addition #3): bridge PCs run for weeks without
a restart, so an unbounded dict would leak for the life of the process.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Literal

from api.config import Settings
from api.state import ExecutorHolder, PlanJobPayload, run_plan_job
from core.optimiser import PlanResult

logger = logging.getLogger(__name__)

JobStatus = Literal["queued", "running", "done", "failed"]


@dataclass
class JobError:
    code: Literal["invalid_request", "internal_error"]
    message: str


@dataclass
class JobRecord:
    job_id: str
    submitted_at: float
    future: Future = field(repr=False)
    started_at: float | None = None
    finished_at: float | None = None
    result: PlanResult | None = None
    error: JobError | None = None

    @property
    def status(self) -> JobStatus:
        if self.finished_at is not None:
            return "failed" if self.error is not None else "done"
        if self.future.running():
            return "running"
        return "queued"


def _classify_error(exc: BaseException) -> JobError:
    # ValueError also covers core.geography.OutOfOperatingAreaError, which
    # subclasses it -- same fail-fast classification as the synchronous
    # 422 path (api/errors.py), just reached asynchronously here since this
    # exception surfaced inside a worker, after submission already
    # succeeded (design 10).
    if isinstance(exc, ValueError):
        return JobError(code="invalid_request", message=str(exc))
    logger.exception("job failed with an unexpected error", exc_info=exc)
    return JobError(code="internal_error", message="internal error -- see server logs")


class JobStore:
    def __init__(self, executor_holder: ExecutorHolder, config: Settings) -> None:
        self._executor_holder = executor_holder
        self._config = config
        self._records: dict[str, JobRecord] = {}
        self._lock = threading.Lock()
        self._sweep_task: asyncio.Task | None = None

    async def start_eviction_loop(self) -> None:
        self._sweep_task = asyncio.create_task(self._eviction_loop())

    async def stop_eviction_loop(self) -> None:
        if self._sweep_task is not None:
            self._sweep_task.cancel()
            try:
                await self._sweep_task
            except asyncio.CancelledError:
                pass

    async def _eviction_loop(self) -> None:
        while True:
            await asyncio.sleep(self._config.job_eviction_interval_s)
            try:
                evicted = self.sweep()
                if evicted:
                    logger.info("job store eviction: removed %d expired record(s)", evicted)
            except Exception:
                logger.exception("job store eviction sweep failed, will retry")

    def submit(self, payload: PlanJobPayload) -> JobRecord:
        job_id = uuid.uuid4().hex
        future = self._executor_holder.executor.submit(run_plan_job, payload)
        record = JobRecord(job_id=job_id, submitted_at=time.time(), future=future)
        with self._lock:
            self._records[job_id] = record
        return record

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock:
            record = self._records.get(job_id)
        if record is not None:
            self._refresh(record)
        return record

    def _refresh(self, record: JobRecord) -> None:
        """Lazily resolves a completed future into result/error --
        `Future.done()`/`.result()` are the only synchronization primitives
        needed at this scale (no separate poller process)."""
        if record.finished_at is not None or not record.future.done():
            return
        record.finished_at = time.time()
        if record.started_at is None:
            record.started_at = record.finished_at
        try:
            record.result = record.future.result()
        except Exception as exc:  # noqa: BLE001 -- classified below, not swallowed
            record.error = _classify_error(exc)

    def sweep(self) -> int:
        """Refreshes every still-open record (so a job nobody ever polled
        still gets `finished_at` populated -- otherwise it would never
        become TTL-eligible) then evicts `done`/`failed` records past the
        TTL or beyond `job_max_size` (oldest-`finished_at`-first).
        `queued`/`running` records are never evicted regardless of age.
        Returns the number of records evicted."""
        now = time.time()
        with self._lock:
            for record in self._records.values():
                self._refresh(record)

            finished = [
                r for r in self._records.values() if r.finished_at is not None
            ]
            to_evict: set[str] = set()

            for record in finished:
                if now - record.finished_at > self._config.job_ttl_s:
                    to_evict.add(record.job_id)

            surviving_finished = [r for r in finished if r.job_id not in to_evict]
            total_after_ttl = len(self._records) - len(to_evict)
            if total_after_ttl > self._config.job_max_size:
                overflow = total_after_ttl - self._config.job_max_size
                surviving_finished.sort(key=lambda r: r.finished_at)
                for record in surviving_finished[:overflow]:
                    to_evict.add(record.job_id)

            for job_id in to_evict:
                del self._records[job_id]

        return len(to_evict)
