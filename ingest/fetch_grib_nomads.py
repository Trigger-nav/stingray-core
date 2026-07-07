#!/usr/bin/env python3
"""
Stingray ingestion layer — NOAA NOMADS GFS wind + WW3 wave GRIB2 (ticket 0.5,
production weather source #1 of 2 — see also `fetch_grib_ecmwf.py`).

Fetches GFS 10m wind (UGRD/VGRD) and WW3 global.0p16 wave (HTSGW/PERPW/DIRPW)
via NOMADS' grib-filter CGI service, which subsets *server-side* by bbox and
variable — confirmed live during scoping: a 2-variable bbox slice over
`OPERATING_AREA_BBOX` is ~700B-7KB, not a global-grid download. Both products
are confirmed available hourly through at least 48h on the current cycle.

Normalises to `core/weather.py`'s `GriddedWeatherField` schema via
`ingest/grib_common.py`: land cells in the *wave* fields become NaN (B2 —
wind is intentionally left unmasked, see `GriddedWeatherField`'s docstring),
longitude is wrapped to [-180, 180), and wave direction is passed through
`direction_to_from_convention_deg` (currently identity —
`WW3_DIRECTION_IS_TO_CONVENTION` is unverified-but-assumed-False, see
`ingest/grib_common.py`'s module docstring).

**Not exercised end-to-end in CI or by the author** (no `eccodes`/`cfgrib`
available in the scoping sandbox — see CLAUDE.md's GRIB-conventions gotcha).
`tests/test_grib_parsing.py` opens committed real-sample fixtures with
`pytest.importorskip("cfgrib")`, so it verifies the parsing shape wherever
eccodes *is* installed, but a real end-to-end run + the cross-source check
(`ingest/verify_grib_consistency.py`) is still needed before trusting this
in production — see CLAUDE.md's "first real run" checklist.

Requires cfgrib + xarray (ingest-only deps, see pyproject.toml's `ingest`
extra) — core/ stays numpy+PyYAML only. cfgrib additionally requires the
system `eccodes` C library (`brew install eccodes` / `apt-get install
libeccodes-dev`) — not installable via pip alone.

Usage: python3 -m ingest.fetch_grib_nomads [--out PATH] [--horizon-h H]
       [--cycle-date YYYYMMDD --cycle-hour HH]
"""

from __future__ import annotations

import argparse
import tempfile
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import xarray as xr

from core.geography import OPERATING_AREA_BBOX, RealGeography
from core.optimiser import DEFAULT_HORIZON_H
from ingest.grib_common import (
    WW3_DIRECTION_IS_TO_CONVENTION,
    direction_to_from_convention_deg,
    latest_available_cycle_utc,
    mask_land_as_missing,
    normalise_and_sort_dataset,
    write_npz_atomic,
)

NOMADS_BASE = "https://nomads.ncep.noaa.gov/cgi-bin"
STEP_H = 1  # confirmed hourly GFS wind + WW3 wave availability through 48h+ (scoping)

WIND_VAR_CANDIDATES = {"u10_ms": ["u10", "10u"], "v10_ms": ["v10", "10v"]}
WAVE_VAR_CANDIDATES = {
    "hs_m": ["swh", "htsgw"],
    "period_s": ["mwp", "perpw"],
    "dir_deg": ["mwd", "dirpw"],
}


def _find_var(ds: xr.Dataset, candidates: list[str]) -> xr.DataArray:
    """Locate a variable by GRIB shortName, checked defensively against
    both the xarray variable key and the raw GRIB attrs cfgrib exposes —
    NOAA-vs-WMO shortName naming for these exact wave fields wasn't
    confirmed without eccodes during scoping (see module docstring), so
    this fails loudly (listing what *was* found) rather than silently
    grabbing the wrong variable."""
    candidates_lower = {c.lower() for c in candidates}
    for name, da in ds.data_vars.items():
        keys = {str(name).lower()}
        for attr in ("GRIB_shortName", "GRIB_cfVarName", "GRIB_name"):
            if attr in da.attrs:
                keys.add(str(da.attrs[attr]).lower())
        if keys & candidates_lower:
            return da
    raise KeyError(f"none of {candidates} found in dataset variables {list(ds.data_vars)}")


def _grib_filter_url(script: str, *, dir_path: str, file_name: str, extra: dict) -> str:
    lon_min, lat_min, lon_max, lat_max = OPERATING_AREA_BBOX
    params = {
        "dir": dir_path,
        "file": file_name,
        "subregion": "",
        "leftlon": lon_min,
        "rightlon": lon_max,
        "toplat": lat_max,
        "bottomlat": lat_min,
        **extra,
    }
    return f"{NOMADS_BASE}/{script}?{urllib.parse.urlencode(params)}"


def _download(url: str, dest: Path) -> None:
    with urllib.request.urlopen(url, timeout=60) as resp, open(dest, "wb") as f:
        f.write(resp.read())


def _open_grib(path: Path) -> xr.Dataset:
    return xr.open_dataset(path, engine="cfgrib", backend_kwargs={"indexpath": ""})


def fetch_wind_step(cache_dir: Path, cycle_date: str, cycle_hour: str, step_h: int) -> xr.Dataset:
    url = _grib_filter_url(
        "filter_gfs_0p25_1hr.pl",
        dir_path=f"/gfs.{cycle_date}/{cycle_hour}/atmos",
        file_name=f"gfs.t{cycle_hour}z.pgrb2.0p25.f{step_h:03d}",
        extra={"var_UGRD": "on", "var_VGRD": "on", "lev_10_m_above_ground": "on"},
    )
    dest = cache_dir / f"wind_f{step_h:03d}.grib2"
    _download(url, dest)
    return _open_grib(dest)


def fetch_wave_step(cache_dir: Path, cycle_date: str, cycle_hour: str, step_h: int) -> xr.Dataset:
    url = _grib_filter_url(
        "filter_gfswave.pl",
        dir_path=f"/gfs.{cycle_date}/{cycle_hour}/wave/gridded",
        file_name=f"gfswave.t{cycle_hour}z.global.0p16.f{step_h:03d}.grib2",
        extra={"var_HTSGW": "on", "var_PERPW": "on", "var_DIRPW": "on"},
    )
    dest = cache_dir / f"wave_f{step_h:03d}.grib2"
    _download(url, dest)
    return _open_grib(dest)


def build_grid(
    cycle_date: str, cycle_hour: str, horizon_h: float, geography: RealGeography
) -> dict:
    """GFS wind (0.25°) and WW3 wave (0.16°) are different native grids —
    `GriddedWeatherField`/the npz schema need exactly one shared (lat, lon)
    grid for every field, so each step's wave dataset is linearly
    resampled (`xr.Dataset.interp`) onto that step's wind grid before
    extraction. Wind's coarser 0.25° becomes the combined grid's
    resolution — downsampling wave rather than inventing spatial detail
    upsampling wind would."""
    steps = list(range(0, int(horizon_h) + 1, STEP_H))
    hours = np.array(steps, dtype=float)

    lats = lons = None
    wind_u_frames, wind_v_frames = [], []
    hs_frames, period_frames, dir_frames = [], [], []

    with tempfile.TemporaryDirectory() as tmp:
        cache_dir = Path(tmp)
        for step_h in steps:
            wind_ds = normalise_and_sort_dataset(
                fetch_wind_step(cache_dir, cycle_date, cycle_hour, step_h)
            )
            wave_ds = normalise_and_sort_dataset(
                fetch_wave_step(cache_dir, cycle_date, cycle_hour, step_h)
            )
            wave_ds = wave_ds.interp(latitude=wind_ds.latitude, longitude=wind_ds.longitude)

            u10 = _find_var(wind_ds, WIND_VAR_CANDIDATES["u10_ms"])
            v10 = _find_var(wind_ds, WIND_VAR_CANDIDATES["v10_ms"])
            hs = _find_var(wave_ds, WAVE_VAR_CANDIDATES["hs_m"])
            period = _find_var(wave_ds, WAVE_VAR_CANDIDATES["period_s"])
            wave_dir = _find_var(wave_ds, WAVE_VAR_CANDIDATES["dir_deg"])

            if lats is None:
                lats = wind_ds.latitude.values.astype(float)
                lons = wind_ds.longitude.values.astype(float)

            wind_u_frames.append(u10.values.astype(float))
            wind_v_frames.append(v10.values.astype(float))
            hs_frames.append(hs.values.astype(float))
            period_frames.append(period.values.astype(float))
            dir_frames.append(
                direction_to_from_convention_deg(
                    wave_dir.values.astype(float),
                    source_is_to_convention=WW3_DIRECTION_IS_TO_CONVENTION,
                )
            )
            wind_ds.close()
            wave_ds.close()

    hs_m = mask_land_as_missing(np.stack(hs_frames), lats, lons, geography)
    period_s = mask_land_as_missing(np.stack(period_frames), lats, lons, geography)
    dir_deg = mask_land_as_missing(np.stack(dir_frames), lats, lons, geography)

    n_hours = len(steps)
    zeros = np.zeros((n_hours, len(lats), len(lons)))

    return {
        "lat0": float(lats[0]),
        "dlat": float(lats[1] - lats[0]),
        "lon0": float(lons[0]),
        "dlon": float(lons[1] - lons[0]),
        "hours": hours,
        "hs_m": hs_m,
        "period_peak_s": period_s,
        "period_mean_s": period_s,  # NOMADS PERPW is the only period field fetched (see docstring)
        "wave_from_deg": dir_deg,
        "wind_u_ms": np.stack(wind_u_frames),
        "wind_v_ms": np.stack(wind_v_frames),
        "current_u_ms": zeros,
        "current_v_ms": zeros,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="data/weather/nomads_western_med.npz")
    parser.add_argument("--horizon-h", type=float, default=DEFAULT_HORIZON_H)
    parser.add_argument("--cycle-date", default=None, help="YYYYMMDD; default: latest available")
    parser.add_argument("--cycle-hour", default=None, help="00/06/12/18; default: latest available")
    args = parser.parse_args()

    now_utc = datetime.now(UTC)
    if args.cycle_date and args.cycle_hour:
        cycle_date, cycle_hour = args.cycle_date, args.cycle_hour
    else:
        cycle_date, cycle_hour = latest_available_cycle_utc(now_utc)

    print(f"fetching GFS+WW3 cycle {cycle_date} {cycle_hour}z, horizon {args.horizon_h}h ...")
    geography = RealGeography()
    grid = build_grid(cycle_date, cycle_hour, args.horizon_h, geography)

    write_npz_atomic(
        Path(args.out),
        **grid,
        cycle=f"{cycle_date}_{cycle_hour}z",
        fetched=now_utc.isoformat(),
        source="NOAA NOMADS GFS 0.25 (wind) + WW3 global 0.16 (wave)",
    )
    print(f"wrote {grid['hs_m'].shape} grid -> {args.out}")


if __name__ == "__main__":
    main()
