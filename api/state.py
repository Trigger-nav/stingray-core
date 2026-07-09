"""Shared singleton state (ticket B1 design 5): geography/weather/vessel
are constructed once and reused -- `core/legs.py`'s `_navigable_along_leg`/
`_leg_depth_ok` are `@lru_cache`d keyed on the `Geography` instance itself,
so reusing the same object across requests is what lets that cache pay off
cumulatively (per-request construction would reset it every call).

Worker-process wrinkle: `ProcessPoolExecutor` workers are separate OS
processes that don't share this module's state with the parent -- each
worker constructs its own copy once, via `_worker_init` as the executor's
`initializer`, into this same module's globals *within that worker
process*. Nothing is passed per-task except the small request-only fields
(`PlanJobPayload`) -- re-pickling ~1MB of bathymetry/weather grids on every
single job would defeat the point of reusing the cache at all.
"""

from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass

from api.config import Settings
from core.geography import Geography, RealGeography
from core.optimiser import DEFAULT_DESTINATION, DEFAULT_ORIGIN, PlanRequest, PlanResult, optimise
from core.units import LatLon
from core.vessel_spec import VesselSpec
from core.weather import GriddedWeatherField, WeatherField

logger = logging.getLogger(__name__)

# --- Worker-process globals (set once per worker by _worker_init) ----------

_worker_geography: Geography | None = None
_worker_weather: WeatherField | None = None
_worker_vessel: VesselSpec | None = None


def _geography_kwargs(config: Settings) -> dict[str, str]:
    kwargs = {}
    if config.coastline_path:
        kwargs["coastline_path"] = config.coastline_path
    if config.bathymetry_path:
        kwargs["bathymetry_path"] = config.bathymetry_path
    if config.nogo_path:
        kwargs["nogo_path"] = config.nogo_path
    if config.tss_path:
        kwargs["tss_path"] = config.tss_path
    return kwargs


def _worker_init(config: Settings) -> None:
    """`ProcessPoolExecutor(initializer=...)` target -- runs once per
    worker process before it accepts any task.

    **A full `optimise()` warm-up call was tried here and removed** (found
    empirically during implementation, corrected after further profiling --
    worth recording since the first, wrong conclusion briefly shipped in
    this function): a single cold `optimise()` call against real geography/
    weather costs ~19s regardless of process warmth. `core/legs.py`'s
    `_navigable_along_leg`/`_leg_depth_ok` `lru_cache`s do warm up
    genuinely (millions of hits within and across calls) and `build_lattice`
    itself drops from ~0.4s to ~0.02s once warm -- but neither is the
    dominant cost. Profiling (`cProfile`) showed the real bottleneck is
    `core/weather.py`'s `GriddedWeatherField.sample()`/`core/gridding.py`'s
    bilinear interpolation, called once per `evaluate_leg` (~690k calls
    across the full default speed grid) -- and this is *not* cacheable the
    way navigability/depth are, since each call samples weather at a
    different position *and time* (ETA varies by candidate speed/route --
    the whole point of the time-expanded search, B2). So a warm-up call
    here would cost ~19s per worker for no measurable benefit to the next
    real job.

    What *is* still worth doing cheaply: validating the weather file
    actually loaded sane data, so a malformed/corrupt npz is caught at
    startup (`wait_until_ready`, below, still gates readiness on this)
    rather than surfacing as a confusing mid-search failure on a real
    user's first request."""
    global _worker_geography, _worker_weather, _worker_vessel
    _worker_geography = RealGeography(**_geography_kwargs(config))
    _worker_weather = GriddedWeatherField.from_npz(config.weather_npz_path)
    _worker_vessel = VesselSpec.from_yaml(config.vessel_spec_path)
    _validate_weather_sane(_worker_weather, _worker_geography)


def _validate_weather_sane(weather: WeatherField, geography: Geography) -> None:
    """Cheap (milliseconds, not the ~19s a full `optimise()` call would
    cost) sanity check: sample a couple of real, navigable points and
    confirm they don't come back NaN -- catches an obviously mismatched
    bbox or corrupt npz at startup instead of during a real search."""
    for point in (DEFAULT_ORIGIN, DEFAULT_DESTINATION):
        sample = weather.sample(point.lat_deg, point.lon_deg, 0.0)
        if any(
            v != v  # NaN check without importing math for one comparison
            for v in (sample.hs_m, sample.wind_u_ms, sample.wind_v_ms)
        ):
            logger.warning(
                "weather sample at %s is NaN -- npz may not cover the "
                "default operating area, or the file is stale/corrupt",
                point,
            )


@dataclass(frozen=True)
class PlanJobPayload:
    """Picklable, request-only fields sent across the process boundary --
    deliberately excludes weather/geography (worker-global singleton state
    above, not re-sent per job). `vessel_override` is a plain `VesselSpec`
    (a frozen dataclass of floats/strs/tuples -- cheaply picklable), not a
    pydantic model, since api/convert.py already did that conversion
    before submission."""

    pace: float
    comfort: float
    origin: LatLon | None
    destination: LatLon | None
    origin_is_anchorage: bool
    destination_is_anchorage: bool
    latest_arrival_h: float | None
    departure_t0_h: float
    speeds_kn: tuple[float, ...] | None
    vessel_override: VesselSpec | None


def run_plan_job(payload: PlanJobPayload) -> PlanResult:
    """The function actually submitted to the `ProcessPoolExecutor` --
    must be a module-level function (picklable) and must only read this
    module's worker-process-local globals, never a reference into the
    parent process's memory."""
    if _worker_geography is None or _worker_weather is None or _worker_vessel is None:
        raise RuntimeError("worker process not initialised -- _worker_init did not run")
    request = PlanRequest(
        weather=_worker_weather,
        geography=_worker_geography,
        vessel=payload.vessel_override or _worker_vessel,
        pace=payload.pace,
        comfort=payload.comfort,
        origin=payload.origin or DEFAULT_ORIGIN,
        destination=payload.destination or DEFAULT_DESTINATION,
        origin_is_anchorage=payload.origin_is_anchorage,
        destination_is_anchorage=payload.destination_is_anchorage,
        latest_arrival_h=payload.latest_arrival_h,
        departure_t0_h=payload.departure_t0_h,
        speeds_kn=payload.speeds_kn,
    )
    return optimise(request)


# --- Main-process shared state ----------------------------------------------


class ExecutorHolder:
    """Mutable indirection so a hot-swap can replace the active executor
    without every caller re-fetching a reference each time -- job
    submission always reads `.executor` at call time."""

    def __init__(self, executor: ProcessPoolExecutor) -> None:
        self.executor = executor


def _resolved_pool_size(config: Settings) -> int:
    return config.pool_size or os.cpu_count() or 1


def _build_executor(config: Settings) -> ProcessPoolExecutor:
    return ProcessPoolExecutor(
        max_workers=_resolved_pool_size(config),
        initializer=_worker_init,
        initargs=(config,),
    )


def _noop() -> bool:
    return True


def _wait_pool_ready(executor: ProcessPoolExecutor, pool_size: int) -> None:
    """Blocks (call via `asyncio.to_thread` from async contexts, never
    directly on the event loop) until every worker's `_worker_init` --
    including `_validate_weather_sane`'s data check -- has completed.

    `ProcessPoolExecutor` spawns worker processes asynchronously: the
    constructor returns immediately, and a worker only starts accepting
    tasks *after* its own `initializer` call returns (found empirically --
    an executor is not actually ready to serve the instant it's
    constructed). Submitting exactly `pool_size` trivial tasks and waiting
    for all of them can only complete once every worker has finished
    initializing, since each worker handles one task at a time and can't
    pick up a trivial task before its own initializer is done -- this is
    what lets `AppState.wait_until_ready`/the hot-swap path (below) know
    every worker has a validated weather/geography/vessel instance loaded
    before the service reports itself ready or a swap is published."""
    futures = [executor.submit(_noop) for _ in range(pool_size)]
    for future in futures:
        future.result()


class AppState:
    """Constructed once in `api/main.py`'s lifespan. `geography`/`vessel`
    are static for the process lifetime (nothing in this ticket changes
    them at runtime). `weather` is refreshed by the hot-swap watcher below
    -- kept here purely for `GET /v1/health`'s provenance display and to
    satisfy `PlanRequest`'s mandatory `weather` field during the
    synchronous, main-process fail-fast validation pass (design 10); the
    actual `optimise()` call always runs in a worker against *that*
    worker's own weather instance, not this one."""

    def __init__(self, config: Settings) -> None:
        self.config = config
        self.geography: Geography = RealGeography(**_geography_kwargs(config))
        self.vessel: VesselSpec = VesselSpec.from_yaml(config.vessel_spec_path)
        self.weather: WeatherField = GriddedWeatherField.from_npz(config.weather_npz_path)
        self._pool_size = _resolved_pool_size(config)
        self.executor_holder = ExecutorHolder(_build_executor(config))
        self._last_weather_mtime = self._current_weather_mtime()
        self._watch_task: asyncio.Task | None = None

    async def wait_until_ready(self) -> None:
        """Call once during startup, awaited *before* the app starts
        accepting HTTP traffic (api/main.py's lifespan): confirms every
        worker actually finished loading and sanity-checking its
        geography/weather/vessel data (cheap, milliseconds per worker --
        `optimise()` itself is not called here, see `_worker_init`'s
        docstring for why a full warm-up call was tried and dropped) --
        a malformed data file surfaces as a startup failure, not a
        confusing mid-search error on a real user's first request. Off
        the event loop via `asyncio.to_thread`, same discipline as the
        hot-swap path below."""
        await asyncio.to_thread(_wait_pool_ready, self.executor_holder.executor, self._pool_size)

    def _current_weather_mtime(self) -> float | None:
        try:
            return os.path.getmtime(self.config.weather_npz_path)
        except OSError:
            return None

    async def start_weather_watch(self) -> None:
        self._watch_task = asyncio.create_task(self._weather_watch_loop())

    async def stop_weather_watch(self) -> None:
        if self._watch_task is not None:
            self._watch_task.cancel()
            try:
                await self._watch_task
            except asyncio.CancelledError:
                pass

    async def _weather_watch_loop(self) -> None:
        while True:
            await asyncio.sleep(self.config.weather_watch_interval_s)
            try:
                await self._check_and_swap()
            except Exception:
                logger.exception("weather hot-swap check failed, will retry")

    async def _check_and_swap(self) -> None:
        mtime = self._current_weather_mtime()
        if mtime is None or mtime == self._last_weather_mtime:
            return
        # Load in the main process first -- validates the file is a
        # well-formed, complete npz *before* committing to the (relatively
        # expensive) pool swap; an in-progress/partial write from the
        # sync/cron side would fail here and simply retry next tick rather
        # than swapping to a broken pool.
        new_weather = GriddedWeatherField.from_npz(self.config.weather_npz_path)
        old_executor = self.executor_holder.executor
        new_executor = _build_executor(self.config)
        # Wait for the new pool to finish initialising (same mechanism as
        # startup) before publishing it -- a worker whose _worker_init
        # hasn't returned yet can't accept a submitted job at all (it would
        # just queue silently behind the still-running initializer), and
        # this also re-runs _validate_weather_sane against the new file
        # before anything real depends on it. This await runs inside the
        # background weather-watch task, off the event loop already (see
        # _weather_watch_loop) -- nothing else stalls while it happens;
        # the *old* pool keeps serving all traffic in the meantime, exactly
        # as it did before this check ran.
        await asyncio.to_thread(_wait_pool_ready, new_executor, self._pool_size)
        # Publish the new pool *before* draining the old one: new
        # submissions must never have nowhere to go. Calling
        # ProcessPoolExecutor.shutdown() makes further submit() calls raise
        # -- publishing first means no submission-gap while the old pool
        # drains, and draining off-thread (not awaited before publishing)
        # means the event loop never stalls on shutdown(wait=True) either.
        self.executor_holder.executor = new_executor
        self.weather = new_weather
        self._last_weather_mtime = mtime
        logger.info("weather hot-swap: warmed new pool published, draining old pool in background")
        asyncio.create_task(asyncio.to_thread(old_executor.shutdown, wait=True))

    def shutdown(self) -> None:
        self.executor_holder.executor.shutdown(wait=False, cancel_futures=True)
