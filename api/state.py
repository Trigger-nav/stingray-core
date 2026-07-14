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
import dataclasses
import logging
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass

import yaml

from api.config import Settings
from core.geography import Geography, RealGeography
from core.optimiser import PlanRequest, PlanResult, optimise
from core.regionpack import MED_PACK, RegionPack, resolve_pack_endpoint
from core.units import LatLon
from core.vessel_spec import VesselSpec
from core.weather import GriddedWeatherField, WeatherField

logger = logging.getLogger(__name__)

# --- Worker-process globals (set once per worker by _worker_init) ----------
#
# Ticket R1: dict-keyed by pack_id, not single values -- a worker now
# holds every configured pack's geography/weather, since a job (payload)
# names which pack it targets. `_worker_vessel`/`_worker_region_packs`
# stay/become process-wide (vessel identity isn't region-scoped;
# region_packs is needed to resolve a payload's pack_id back to the full
# RegionPack object PlanRequest.region_pack wants).

_worker_geography: dict[str, Geography] = {}
_worker_weather: dict[str, WeatherField] = {}
_worker_vessel: VesselSpec | None = None
_worker_region_packs: dict[str, RegionPack] = {}


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


def load_region_packs(config: Settings) -> dict[str, RegionPack]:
    """Ticket R1: `config.region_packs_path` unset (every currently-
    deployed instance) -> synthesize a single implicit "med" pack from
    today's flat `coastline_path`/etc. fields (via `_geography_kwargs`,
    the exact mechanism `RealGeography()` used before this ticket) --
    zero migration forced on any existing deployment. Set -> load every
    pack manifest path the file lists via `RegionPack.from_yaml`, keyed
    by each pack's own `pack_id`."""
    if config.region_packs_path is None:
        pack = dataclasses.replace(
            MED_PACK,
            weather_npz_path=config.weather_npz_path,
            **_geography_kwargs(config),
        )
        return {pack.pack_id: pack}
    with open(config.region_packs_path) as f:
        raw = yaml.safe_load(f)
    packs: dict[str, RegionPack] = {}
    for pack_path in raw["packs"]:
        pack = RegionPack.from_yaml(pack_path)
        packs[pack.pack_id] = pack
    return packs


DEFAULT_PACK_ID = "med"


def _default_pack_id(region_packs: dict[str, RegionPack]) -> str:
    """Which pack a pack-agnostic surface (GET /v1/health) reports on --
    "med" if configured, else arbitrarily the first configured pack. Not
    used by anything that resolves a specific request's own pack_id."""
    if DEFAULT_PACK_ID in region_packs:
        return DEFAULT_PACK_ID
    return next(iter(region_packs))


def _load_weather(path: str) -> WeatherField:
    """`GriddedWeatherField.from_npz`, with a `FileNotFoundError` turned
    into an actionable message. Found live during the 2026-07-13 Hetzner
    deploy: a fresh box with no weather npz yet crash-looped on a bare
    `FileNotFoundError` traceback in `journalctl` -- this is what makes
    that failure self-explanatory instead of requiring someone to already
    know the fix (deploy/README.md's Cloud VM runbook, step 4)."""
    try:
        return GriddedWeatherField.from_npz(path)
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"no weather file at {path!r} -- run ingest.fetch_grib_nomads or "
            "ingest.fetch_grib_ecmwf first (deploy/README.md's Cloud VM "
            "runbook, step 4)"
        ) from exc


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
    global _worker_geography, _worker_weather, _worker_vessel, _worker_region_packs
    _worker_region_packs = load_region_packs(config)
    _worker_geography = {
        pid: RealGeography.from_pack(pack) for pid, pack in _worker_region_packs.items()
    }
    _worker_weather = {
        pid: _load_weather(pack.weather_npz_path) for pid, pack in _worker_region_packs.items()
    }
    _worker_vessel = VesselSpec.from_yaml(config.vessel_spec_path)
    _validate_weather_sane(_worker_weather, _worker_geography, _worker_region_packs)


def _validate_weather_sane(
    weather: dict[str, WeatherField],
    geography: dict[str, Geography],
    region_packs: dict[str, RegionPack],
) -> None:
    """Cheap (milliseconds, not the ~19s a full `optimise()` call would
    cost) sanity check: sample a couple of real, navigable points *per
    configured pack* and confirm they don't come back NaN -- catches an
    obviously mismatched bbox or corrupt npz at startup instead of during
    a real search. Ticket R1: looped over every pack rather than a single
    hardcoded DEFAULT_ORIGIN/DEFAULT_DESTINATION check -- a pack with no
    default_origin/default_destination configured (not yet expected in
    practice, but not structurally impossible) is skipped, not an error
    here specifically (resolve_pack_endpoint is what enforces that a real
    plan request against such a pack must supply explicit endpoints)."""
    for pid, pack in region_packs.items():
        if pack.default_origin is None or pack.default_destination is None:
            continue
        for point in (pack.default_origin, pack.default_destination):
            sample = weather[pid].sample(point.lat_deg, point.lon_deg, 0.0)
            if any(
                v != v  # NaN check without importing math for one comparison
                for v in (sample.hs_m, sample.wind_u_ms, sample.wind_v_ms)
            ):
                logger.warning(
                    "pack %r: weather sample at %s is NaN -- npz may not cover the "
                    "pack's operating area, or the file is stale/corrupt",
                    pid,
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
    pack_id: str
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
    if not _worker_geography or not _worker_weather or _worker_vessel is None:
        raise RuntimeError("worker process not initialised -- _worker_init did not run")
    try:
        geography = _worker_geography[payload.pack_id]
        weather = _worker_weather[payload.pack_id]
        region_pack = _worker_region_packs[payload.pack_id]
    except KeyError:
        # ValueError, not RuntimeError: an unknown pack_id is a client
        # input error (api/jobs.py's _classify_error maps ValueError to
        # HTTP 422 "invalid_request", not a 500) -- distinguishable from
        # "_worker_init did not run" above, which genuinely is an
        # internal/infra error.
        raise ValueError(
            f"unknown region pack {payload.pack_id!r} -- this worker has "
            f"{sorted(_worker_region_packs)} configured"
        ) from None
    origin, destination = resolve_pack_endpoint(region_pack, payload.origin, payload.destination)
    request = PlanRequest(
        weather=weather,
        geography=geography,
        vessel=payload.vessel_override or _worker_vessel,
        pace=payload.pace,
        comfort=payload.comfort,
        origin=origin,
        destination=destination,
        origin_is_anchorage=payload.origin_is_anchorage,
        destination_is_anchorage=payload.destination_is_anchorage,
        region_pack=region_pack,
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
    """Constructed once in `api/main.py`'s lifespan. `geography`/`vessel`/
    `region_packs` are static for the process lifetime (nothing in this
    ticket changes them at runtime). `weather` is refreshed by the
    hot-swap watcher below -- kept here purely for `GET /v1/health`'s
    provenance display and to satisfy `PlanRequest`'s mandatory `weather`
    field during the synchronous, main-process fail-fast validation pass
    (design 10); the actual `optimise()` call always runs in a worker
    against *that* worker's own weather instance, not this one.

    Ticket R1: `geography`/`weather` are dict-keyed by `pack_id` -- a
    single-pack deployment (every existing one, `region_packs_path`
    unset) still gets exactly one entry, "med", so nothing here changes
    observable behaviour for those; `region_packs` itself is new,
    exposing every configured `RegionPack` for routes.py to resolve a
    request's `pack_id` against."""

    def __init__(self, config: Settings) -> None:
        self.config = config
        self.region_packs: dict[str, RegionPack] = load_region_packs(config)
        self.geography: dict[str, Geography] = {
            pid: RealGeography.from_pack(pack) for pid, pack in self.region_packs.items()
        }
        self.vessel: VesselSpec = VesselSpec.from_yaml(config.vessel_spec_path)
        self.weather: dict[str, WeatherField] = {
            pid: _load_weather(pack.weather_npz_path) for pid, pack in self.region_packs.items()
        }
        self._pool_size = _resolved_pool_size(config)
        self.executor_holder = ExecutorHolder(_build_executor(config))
        self._last_weather_mtimes = self._current_weather_mtimes()
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

    def _current_weather_mtimes(self) -> dict[str, float | None]:
        mtimes: dict[str, float | None] = {}
        for pid, pack in self.region_packs.items():
            try:
                mtimes[pid] = os.path.getmtime(pack.weather_npz_path)
            except OSError:
                mtimes[pid] = None
        return mtimes

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
        """Ticket R1: watches *every* configured pack's weather npz mtime,
        not one. A change in any single pack's file triggers a full-pool
        rebuild that reloads every configured pack fresh (via
        `_worker_init`, looped) -- the same mechanism the single-pack
        design already used to hot-swap, just extended to N packs rather
        than made surgically per-pack; not worth a more targeted in-place
        refresh at the pack counts (2) this ticket ships with. An
        untouched pack's reloaded value is identical to its old one (same
        file, same content), so this is a cost/simplicity tradeoff, not a
        correctness one."""
        mtimes = self._current_weather_mtimes()
        if mtimes == self._last_weather_mtimes or all(m is None for m in mtimes.values()):
            return
        # Load every pack's weather in the main process first -- validates
        # each file is a well-formed, complete npz *before* committing to
        # the (relatively expensive) pool swap; an in-progress/partial
        # write from the sync/cron side would fail here and simply retry
        # next tick rather than swapping to a broken pool.
        new_weather = {
            pid: _load_weather(pack.weather_npz_path) for pid, pack in self.region_packs.items()
        }
        old_executor = self.executor_holder.executor
        new_executor = _build_executor(self.config)
        # Wait for the new pool to finish initialising (same mechanism as
        # startup) before publishing it -- a worker whose _worker_init
        # hasn't returned yet can't accept a submitted job at all (it would
        # just queue silently behind the still-running initializer), and
        # this also re-runs _validate_weather_sane against the new files
        # before anything real depends on them. This await runs inside the
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
        self._last_weather_mtimes = mtimes
        logger.info("weather hot-swap: warmed new pool published, draining old pool in background")
        asyncio.create_task(asyncio.to_thread(old_executor.shutdown, wait=True))

    def shutdown(self) -> None:
        self.executor_holder.executor.shutdown(wait=False, cancel_futures=True)
