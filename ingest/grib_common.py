"""Shared, pure normalisation helpers for the GRIB ingest scripts
(`fetch_grib_nomads.py`, `fetch_grib_ecmwf.py`, ticket 0.5) — CORE_PORTING_NOTES.md's
B2 says to "normalise at the ingest boundary and test it"; this module is
where that happens exactly once, independent of any network/GRIB-parsing
I/O, so it's fully unit-testable without cfgrib/eccodes.

**Direction convention (CLAUDE.md's GRIB-conventions gotcha).** Confirmed
live during scoping: ECMWF open data's wave stream `mwd` ("mean wave
direction") is WMO-standard from-convention — same as wind, usable as-is.
NOAA's own GRIB2 parameter tables for WW3's `DIRPW`/`WVDIR`/`SWDIR` give
units ("degree true") but no explicit from/to statement, and every WW3
product description found describes it the same way as wind — but this is
**not empirically verified against a real decoded file** (no eccodes in
the scoping sandbox). `WW3_DIRECTION_IS_TO_CONVENTION` is the single place
that assumption lives — flip it once someone confirms for real (the
cross-source consistency check in `ingest/verify_grib_consistency.py` is
built to do exactly that empirically, by comparing WW3 and ECMWF wave
direction for the same real sea state).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

import numpy as np
import xarray as xr

WW3_DIRECTION_IS_TO_CONVENTION = False


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


def latest_available_cycle_utc(now_utc: datetime, delay_h: float = 5.0) -> tuple[str, str]:
    """Latest `00/06/12/18z` model cycle that should be fully published by
    `now_utc`, given a `delay_h` publication-lag safety margin. Takes
    `now_utc` as a parameter rather than reading the clock internally so
    it's unit-testable without wall-clock dependence."""
    available = now_utc - timedelta(hours=delay_h)
    cycle_hour = (available.hour // 6) * 6
    cycle_dt = available.replace(hour=cycle_hour, minute=0, second=0, microsecond=0)
    return cycle_dt.strftime("%Y%m%d"), f"{cycle_hour:02d}"


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
