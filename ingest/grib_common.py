"""Shared, pure normalisation helpers for the GRIB ingest scripts
(`fetch_grib_nomads.py`, `fetch_grib_ecmwf.py`, ticket 0.5) — CORE_PORTING_NOTES.md's
B2 says to "normalise at the ingest boundary and test it"; this module is
where that happens exactly once, independent of any network/GRIB-parsing
I/O, so it's fully unit-testable without cfgrib/eccodes.

**Direction convention (CLAUDE.md's GRIB-conventions gotcha).** Confirmed
live during scoping: ECMWF open data's wave stream `mwd` ("mean wave
direction") is WMO-standard from-convention — same as wind, usable as-is.
NOAA's own GRIB2 parameter tables for WW3's `DIRPW`/`WVDIR`/`SWDIR` give
units ("degree true") but no explicit from/to statement. **Empirically
confirmed from-convention as of the 2026-07-07 first real run**: NOMADS
and ECMWF wave direction for the same real sea state agreed to within 16°
mean (`ingest/verify_grib_consistency.py`), far closer to 0° than the
~180° a convention mismatch would produce. `WW3_DIRECTION_IS_TO_CONVENTION`
stays `False` — the single place this lives, flip it if a future run ever
disagrees.
"""

from __future__ import annotations

import logging
import os
import urllib.error
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol, TypeVar

import numpy as np
import xarray as xr

from core.units import LatLon, distance_m, m_to_nm

WW3_DIRECTION_IS_TO_CONVENTION = False

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


class _LandChecker(Protocol):
    def is_land_precise(self, lat_deg: float, lon_deg: float) -> bool: ...


def normalise_longitude_deg(lon_deg):
    """Wrap into [-180, 180) — GFS/WW3/IFS grids are natively 0-360.
    Works elementwise on numpy arrays as well as plain floats."""
    return ((lon_deg + 180.0) % 360.0) - 180.0


def direction_to_from_convention_deg(direction_deg, *, source_is_to_convention: bool):
    """Normalise a direction to the "coming from" convention
    `WeatherSample.wave_from_deg` uses. Identity when the source already
    reports from-convention (the default for both our sources, see module
    docstring); flips (+180 mod 360) when `source_is_to_convention=True`.
    Works elementwise on numpy arrays as well as plain floats."""
    if not source_is_to_convention:
        return direction_deg % 360.0
    return (direction_deg + 180.0) % 360.0


def mask_land_as_missing(
    values: np.ndarray, lats: np.ndarray, lons: np.ndarray, geography: _LandChecker
) -> np.ndarray:
    """Per B2: land cells -> NaN, never calm — "the most dangerous silent
    bug in the demo". `values` shaped (n_hours, n_lat, n_lon); the land
    mask is time-invariant so it's applied once per (lat, lon) column.
    Uses `geography.is_land_precise` (the GSHHG polygon ray-cast, not the
    rasterised hot-path lookup) — ingest is a one-time cost, not a hot
    path, so the precise check is preferable here. Ingest-side, this is
    applied to wave fields only (not wind) — see
    `core.weather.GriddedWeatherField`'s docstring for why."""
    masked = np.asarray(values, dtype=float).copy()
    for iy, lat in enumerate(lats):
        for ix, lon in enumerate(lons):
            if geography.is_land_precise(float(lat), float(lon)):
                masked[:, iy, ix] = np.nan
    return masked


# Ticket W1: default fill-eligibility/behaviour parameters. See
# coastal_fill_mask/apply_coastal_fill docstrings for the full reasoning;
# real supporting numbers (real UK pack masked-cell survey) recorded in
# docs/plans/ticket-W1.md and CLAUDE.md's W1 gotcha.
COASTAL_FILL_WATER_FRACTION_THRESHOLD = 0.5
COASTAL_FILL_SAMPLE_GRID_N = 9
COASTAL_FILL_MAX_RADIUS_NM = 60.0
# Below MAX_RADIUS_NM (the fill still happens), but far enough that a
# single fill is worth surfacing in cron logs rather than passing
# silently -- signed off as a discretionary addition, not a second hard
# bound: the ceiling itself stays 60.0nm.
COASTAL_FILL_WARN_RADIUS_NM = 30.0


def coastal_fill_mask(
    lats: np.ndarray,
    lons: np.ndarray,
    geography: _LandChecker,
    *,
    water_fraction_threshold: float = COASTAL_FILL_WATER_FRACTION_THRESHOLD,
    sample_grid_n: int = COASTAL_FILL_SAMPLE_GRID_N,
) -> np.ndarray:
    """Ticket W1: (nlat, nlon) bool array, True where a cell's own
    footprint (bounded by half this grid's own `dlat`/`dlon` in each
    direction around its reference point) is majority real navigable
    water per GSHHG (`geography.is_land_precise`, sampled on a
    `sample_grid_n` x `sample_grid_n` sub-grid) -- independent of whether
    the cell is currently masked, or why.

    Deliberately gated on the *cell's own geometry*, not on "was
    `mask_land_as_missing` the reason this cell is NaN": a real survey of
    the UK South-West pack's masked `hs_m` cells found only 14 of 31 are
    actually land at their own reference point -- the other 17 are NaN
    despite reading as open water there, because NOAA WW3 carries its own
    internal nearshore land-sea mask, independently of GSHHG, and
    under-resolves complex coastal geometry (a real source-model
    limitation, not an ingest bug -- `mask_land_as_missing` only ever adds
    NaN, never removes an upstream one). Every downstream consumer of a
    masked cell (`core.legs.evaluate_leg`'s `non_finite_cost`, this
    project's lattice search) sees only "NaN here," not why -- gating the
    fill on cell geometry catches both cases with identical mechanics,
    where gating on cause would miss the majority (17-of-31) case
    entirely. See `docs/plans/ticket-W1.md` for the full real-numbers
    trace.

    `water_fraction_threshold=0.5` (majority water, not "any
    intersection"): verified against the real UK pack that "any water at
    all" is too permissive -- it would accept 27 of 31 masked cells,
    including several as low as 7-21% water (overwhelmingly inland
    Dartmoor with a coastal sliver in one corner); 0.5 drops this to 18,
    all genuinely majority-water, without losing the motivating case (the
    cell nearest the real Plymouth-Sound origin clears 0.5 comfortably at
    0.827). Ingest-time only (not a hot path), the same precision-over-
    speed tradeoff `mask_land_as_missing` already makes via
    `is_land_precise` rather than the rasterised hot-path lookup."""
    nlat, nlon = len(lats), len(lons)
    dlat = float(lats[1] - lats[0]) if nlat > 1 else 0.25
    dlon = float(lons[1] - lons[0]) if nlon > 1 else 0.25
    half_dlat, half_dlon = abs(dlat) / 2, abs(dlon) / 2
    offsets_y = np.linspace(-half_dlat, half_dlat, sample_grid_n)
    offsets_x = np.linspace(-half_dlon, half_dlon, sample_grid_n)

    mask = np.zeros((nlat, nlon), dtype=bool)
    for iy, lat in enumerate(lats):
        for ix, lon in enumerate(lons):
            water = 0
            for sy in offsets_y:
                for sx in offsets_x:
                    if not geography.is_land_precise(float(lat + sy), float(lon + sx)):
                        water += 1
            mask[iy, ix] = (water / (sample_grid_n * sample_grid_n)) >= water_fraction_threshold
    return mask


def apply_coastal_fill(
    values: np.ndarray,
    lats: np.ndarray,
    lons: np.ndarray,
    fill_mask: np.ndarray,
    *,
    ref_lat_deg: float,
    max_fill_radius_nm: float = COASTAL_FILL_MAX_RADIUS_NM,
    warn_radius_nm: float = COASTAL_FILL_WARN_RADIUS_NM,
    field_name: str = "field",
) -> tuple[np.ndarray, int, int]:
    """Ticket W1: for every `(iy, ix)` where `fill_mask[iy, ix]` is True
    and `values[:, iy, ix]` is currently all-NaN (mirrors
    `mask_land_as_missing`'s own time-invariant-mask assumption), find the
    nearest cell -- by real distance (`core.units.distance_m` at
    `ref_lat_deg`), among cells whose `values[0]` is NOT NaN in the
    *original* array -- and copy its whole time series. A cell already
    non-NaN is never touched, even if `fill_mask` happens to be True
    there; a cell in `fill_mask` whose nearest real-data neighbour is
    farther than `max_fill_radius_nm` is left untouched (still NaN) --
    the land=missing-never-calm convention wins over an unreliably
    distant substitute, the same real/bounded-tolerance shape ticket C1's
    6-hour current time-coverage cutoff already established.

    Every filled value is real, already-fetched data -- never synthetic,
    interpolated, or zero. Open water generally has equal-or-greater
    Hs/current speed than a sheltered inshore position at the same
    moment (less fetch-limited, less land-shadowed), so substituting a
    nearby open-water sample for a sheltered coastal cell tends to
    overstate sea state there -- the safer direction for a routing/
    comfort tool to err. This is a directional tendency, a modelling
    judgment with real limits (a sheltered tidal narrows can, per ticket
    C1, see current *exceed* nearby open water), not a guarantee.

    Returns `(filled_values, n_filled, n_skipped_too_far)`. Any
    individual fill farther than `warn_radius_nm` (default half of
    `max_fill_radius_nm`) logs a WARNING naming `field_name`, the cell,
    and the real distance -- makes long-range-but-still-within-bounds
    fills visible in cron logs without lowering `max_fill_radius_nm`
    itself."""
    filled = np.asarray(values, dtype=float).copy()
    nlat, nlon = filled.shape[1], filled.shape[2]
    original_valid = ~np.isnan(values[0])

    valid_cells = [(jy, jx) for jy in range(nlat) for jx in range(nlon) if original_valid[jy, jx]]

    n_filled = 0
    n_skipped = 0
    for iy in range(nlat):
        for ix in range(nlon):
            if not fill_mask[iy, ix] or original_valid[iy, ix]:
                continue
            if not np.all(np.isnan(values[:, iy, ix])):
                continue
            here = LatLon(float(lats[iy]), float(lons[ix]))
            best_nm: float | None = None
            best_cell: tuple[int, int] | None = None
            for jy, jx in valid_cells:
                d_nm = m_to_nm(
                    distance_m(here, LatLon(float(lats[jy]), float(lons[jx])), ref_lat_deg)
                )
                if best_nm is None or d_nm < best_nm:
                    best_nm, best_cell = d_nm, (jy, jx)
            if best_nm is None or best_nm > max_fill_radius_nm:
                n_skipped += 1
                continue
            jy, jx = best_cell  # type: ignore[misc]
            filled[:, iy, ix] = values[:, jy, jx]
            n_filled += 1
            if best_nm > warn_radius_nm:
                logger.warning(
                    "coastal fill: %s cell (%.3f, %.3f) filled from %.1fnm away "
                    "(exceeds %.0fnm warn threshold, within %.0fnm max radius)",
                    field_name,
                    lats[iy],
                    lons[ix],
                    best_nm,
                    warn_radius_nm,
                    max_fill_radius_nm,
                )
    return filled, n_filled, n_skipped


def latest_available_cycle_utc(
    now_utc: datetime,
    *,
    delay_h: float = 5.0,
    valid_hours: tuple[int, ...] = (0, 6, 12, 18),
) -> tuple[str, str]:
    """Latest model cycle (from `valid_hours`) that should be fully
    published by `now_utc`, given a `delay_h` publication-lag safety
    margin. Takes `now_utc` as a parameter rather than reading the clock
    internally so it's unit-testable without wall-clock dependence.

    `valid_hours` defaults to NOMADS' 6-hourly cadence -- **must** be
    overridden for sources with a different cycle set (e.g. ECMWF open
    data's `oper`/`wave` streams only ever publish 00z/12z; the old
    hard-coded `(available.hour // 6) * 6` could silently pick a 06z/18z
    that never exists for it -- finding #2, 2026-07-13 Hetzner deploy).
    """
    available = now_utc - timedelta(hours=delay_h)
    hours = sorted(valid_hours)
    eligible = [h for h in hours if h <= available.hour]
    if eligible:
        cycle_dt = available.replace(hour=eligible[-1], minute=0, second=0, microsecond=0)
    else:
        cycle_dt = (available - timedelta(days=1)).replace(
            hour=hours[-1], minute=0, second=0, microsecond=0
        )
    return cycle_dt.strftime("%Y%m%d"), f"{cycle_dt.hour:02d}"


def previous_cycle_utc(
    cycle_date: str, cycle_hour: str, *, valid_hours: tuple[int, ...] = (0, 6, 12, 18)
) -> tuple[str, str]:
    """The cycle immediately before `(cycle_date, cycle_hour)` in
    `valid_hours`'s cadence -- used by `fetch_with_cycle_fallback` to step
    back one cycle at a time on a 404 (finding #3, 2026-07-13 Hetzner
    deploy: a 404 on the selected cycle should self-heal, not die)."""
    dt = datetime.strptime(f"{cycle_date}{cycle_hour}", "%Y%m%d%H")
    hours = sorted(valid_hours)
    idx = hours.index(dt.hour)
    if idx == 0:
        prev_dt = (dt - timedelta(days=1)).replace(hour=hours[-1])
    else:
        prev_dt = dt.replace(hour=hours[idx - 1])
    return prev_dt.strftime("%Y%m%d"), f"{prev_dt.hour:02d}"


def fetch_with_cycle_fallback(
    cycle_date: str,
    cycle_hour: str,
    *,
    valid_hours: tuple[int, ...],
    max_attempts: int,
    attempt: Callable[[str, str], _T],
) -> tuple[str, str, _T]:
    """Try `attempt(cycle_date, cycle_hour)`; on an HTTP 404 (cycle not
    yet published -- the observed live failure mode, ECMWF's `.index`
    sidecar during the 2026-07-13 Hetzner deploy), step back to the
    previous valid cycle and retry, up to `max_attempts` cycles total, so
    a cron job self-heals past a publication-timing race instead of
    dying with nothing flagging the staleness. Any other exception (a
    real network/parse failure, not "this cycle doesn't exist yet")
    propagates immediately -- silently retrying past a genuine error
    would mask it, not fix it. Every fallback step is logged loudly (old
    cycle, reason, new cycle) so cron logs make the self-heal visible
    rather than a job quietly having served older weather. Returns
    `(date, hour, attempt(date, hour))` for whichever cycle succeeded."""
    date, hour = cycle_date, cycle_hour
    for attempt_no in range(max_attempts):
        try:
            return date, hour, attempt(date, hour)
        except urllib.error.HTTPError as exc:
            if exc.code != 404 or attempt_no == max_attempts - 1:
                raise
            new_date, new_hour = previous_cycle_utc(date, hour, valid_hours=valid_hours)
            logger.warning(
                "cycle %s %sz not available (HTTP 404, attempt %d/%d) -- "
                "falling back to previous cycle %s %sz",
                date,
                hour,
                attempt_no + 1,
                max_attempts,
                new_date,
                new_hour,
            )
            date, hour = new_date, new_hour
    raise AssertionError("unreachable")  # loop always returns or re-raises


def normalise_and_sort_dataset(ds: xr.Dataset) -> xr.Dataset:
    """Wrap longitude to [-180, 180) and sort both (lat, lon) axes
    ascending. GRIB output commonly orders latitude north-to-south
    (descending); `core/gridding.py`'s `grid_fracs`/`bilinear` assume
    ascending lat0+dlat (a positive step), the same convention
    `ingest/fetch_gebco.py`'s GEBCO output already uses. Also a
    prerequisite for `xr.Dataset.interp` (used to resample WW3's wave grid
    onto GFS's wind grid in `fetch_grib_nomads.py` — `interp` requires
    monotonic coordinates). GFS/WW3/IFS ordering wasn't confirmed without
    eccodes during scoping (see module docstring), so this sorts by the
    actual decoded coordinate values rather than assuming a fixed
    convention either way."""
    ds = ds.assign_coords(longitude=normalise_longitude_deg(ds.longitude.values))
    return ds.sortby(["latitude", "longitude"])


def write_npz_atomic(out_path: str | Path, **fields) -> None:
    """`np.savez_compressed` via a `.tmp` sibling + `os.replace()`, so a
    crash mid-write never leaves a corrupt/partial npz at the path
    `core.weather.GriddedWeatherField.from_npz` will next load. Writes to
    an explicitly-opened file object (not the path form) — numpy's path
    form auto-appends `.npz` if the given name doesn't already end with
    it, which would otherwise fight the `.tmp` suffix."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_name(out_path.name + ".tmp")
    with open(tmp_path, "wb") as f:
        np.savez_compressed(f, **fields)
    os.replace(tmp_path, out_path)
