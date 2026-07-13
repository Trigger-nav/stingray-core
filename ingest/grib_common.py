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
