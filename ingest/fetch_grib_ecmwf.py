#!/usr/bin/env python3
"""
Stingray ingestion layer — ECMWF open data IFS wind + wave GRIB2 (ticket 0.5,
production weather source #2 of 2 — see also `fetch_grib_nomads.py`).

ECMWF open data has **no server-side bbox subsetting** — every step is one
whole-globe GRIB2 file (confirmed live: the atmospheric `oper` stream is
132MB for a single step, bundling many unrelated fields). But every file
ships a `.index` sidecar (newline-delimited JSON: `{"param": ..., "_offset":
..., "_length": ...}`) enabling an HTTP Range request for a single parameter
— confirmed live: `10u`/`10v` (wind, `oper` stream) and `swh`/`mwd`/`pp1d`/
`mwp` (wave, `wave` stream) are each ~700-900KB, not 132MB. Confirmed cadence:
3-hourly. Both streams are at 0.25 deg, so (unlike NOMADS' GFS 0.25 deg /
WW3 0.16 deg mismatch) no cross-grid resampling is needed here — just
cropping the decoded whole-globe arrays to `OPERATING_AREA_BBOX` in memory
(trivial at this point size, unlike GEBCO's multi-GB problem).

Normalises to `core/weather.py`'s `GriddedWeatherField` schema via
`ingest/grib_common.py`: land cells in the *wave* fields become NaN (B2 —
wind is intentionally left unmasked, see `GriddedWeatherField`'s docstring).
Direction is a no-op here: ECMWF's `mwd` is WMO-standard from-convention,
confirmed live in scoping (see `ingest/grib_common.py`'s module docstring)
— unlike NOMADS' WW3 `DIRPW`, which is assumed-but-unverified.

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

Usage: python3 -m ingest.fetch_grib_ecmwf [--out PATH] [--horizon-h H]
       [--cycle-date YYYYMMDD --cycle-hour HH]
"""

from __future__ import annotations

import argparse
import json
import tempfile
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import xarray as xr

from core.geography import OPERATING_AREA_BBOX, RealGeography
from core.optimiser import DEFAULT_HORIZON_H
from ingest.grib_common import (
    latest_available_cycle_utc,
    mask_land_as_missing,
    normalise_and_sort_dataset,
    write_npz_atomic,
)

ECMWF_BASE = "https://data.ecmwf.int/forecasts"
STEP_H = 3  # confirmed cadence (scoping)

WIND_PARAMS = {"u10_ms": "10u", "v10_ms": "10v"}
WAVE_PARAMS = {"hs_m": "swh", "dir_deg": "mwd", "period_peak_s": "pp1d", "period_mean_s": "mwp"}


def _stream_url_base(cycle_date: str, cycle_hour: str, stream: str) -> str:
    return f"{ECMWF_BASE}/{cycle_date}/{cycle_hour}z/ifs/0p25/{stream}/{cycle_date}{cycle_hour}0000"


def _fetch_index(url: str) -> list[dict]:
    with urllib.request.urlopen(url, timeout=30) as resp:
        text = resp.read().decode("utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _fetch_param_message(base_url: str, step_h: int, stream_suffix: str, param: str) -> bytes:
    index_url = f"{base_url}-{step_h}h-{stream_suffix}-fc.index"
    grib_url = f"{base_url}-{step_h}h-{stream_suffix}-fc.grib2"
    index = _fetch_index(index_url)
    entry = next((e for e in index if e.get("param") == param), None)
    if entry is None:
        available = sorted({e.get("param") for e in index})
        raise KeyError(f"param {param!r} not found in {index_url}; available: {available}")
    start = entry["_offset"]
    end = start + entry["_length"] - 1
    req = urllib.request.Request(grib_url, headers={"Range": f"bytes={start}-{end}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def _open_single_param_grib(cache_dir: Path, name: str, data: bytes) -> xr.Dataset:
    dest = cache_dir / f"{name}.grib2"
    dest.write_bytes(data)
    return xr.open_dataset(dest, engine="cfgrib", backend_kwargs={"indexpath": ""})


def _crop_to_bbox(ds: xr.Dataset) -> xr.Dataset:
    lon_min, lat_min, lon_max, lat_max = OPERATING_AREA_BBOX
    ds = normalise_and_sort_dataset(ds)
    return ds.sel(latitude=slice(lat_min, lat_max), longitude=slice(lon_min, lon_max))


def fetch_step(
    cache_dir: Path, cycle_date: str, cycle_hour: str, step_h: int
) -> dict[str, xr.DataArray]:
    oper_base = _stream_url_base(cycle_date, cycle_hour, "oper")
    wave_base = _stream_url_base(cycle_date, cycle_hour, "wave")

    fields: dict[str, xr.DataArray] = {}
    for out_name, param in WIND_PARAMS.items():
        data = _fetch_param_message(oper_base, step_h, "oper", param)
        ds = _crop_to_bbox(_open_single_param_grib(cache_dir, f"wind_{param}_{step_h}", data))
        fields[out_name] = ds[next(iter(ds.data_vars))]
    for out_name, param in WAVE_PARAMS.items():
        data = _fetch_param_message(wave_base, step_h, "wave", param)
        ds = _crop_to_bbox(_open_single_param_grib(cache_dir, f"wave_{param}_{step_h}", data))
        fields[out_name] = ds[next(iter(ds.data_vars))]
    return fields


def build_grid(
    cycle_date: str, cycle_hour: str, horizon_h: float, geography: RealGeography
) -> dict:
    steps = list(range(0, int(horizon_h) + 1, STEP_H))
    hours = np.array(steps, dtype=float)

    lats = lons = None
    frames: dict[str, list[np.ndarray]] = {name: [] for name in (*WIND_PARAMS, *WAVE_PARAMS)}

    with tempfile.TemporaryDirectory() as tmp:
        cache_dir = Path(tmp)
        for step_h in steps:
            step_fields = fetch_step(cache_dir, cycle_date, cycle_hour, step_h)
            if lats is None:
                lats = step_fields["u10_ms"].latitude.values.astype(float)
                lons = step_fields["u10_ms"].longitude.values.astype(float)
            for name, da in step_fields.items():
                frames[name].append(da.values.astype(float))

    hs_m = mask_land_as_missing(np.stack(frames["hs_m"]), lats, lons, geography)
    period_peak_s = mask_land_as_missing(np.stack(frames["period_peak_s"]), lats, lons, geography)
    period_mean_s = mask_land_as_missing(np.stack(frames["period_mean_s"]), lats, lons, geography)
    # ECMWF's mwd is confirmed from-convention already -- no
    # direction_to_from_convention_deg flip needed here (see module docstring).
    wave_from_deg = mask_land_as_missing(np.stack(frames["dir_deg"]), lats, lons, geography)

    n_hours = len(steps)
    zeros = np.zeros((n_hours, len(lats), len(lons)))

    return {
        "lat0": float(lats[0]),
        "dlat": float(lats[1] - lats[0]),
        "lon0": float(lons[0]),
        "dlon": float(lons[1] - lons[0]),
        "hours": hours,
        "hs_m": hs_m,
        "period_peak_s": period_peak_s,
        "period_mean_s": period_mean_s,
        "wave_from_deg": wave_from_deg,
        "wind_u_ms": np.stack(frames["u10_ms"]),
        "wind_v_ms": np.stack(frames["v10_ms"]),
        "current_u_ms": zeros,
        "current_v_ms": zeros,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="data/weather/ecmwf_western_med.npz")
    parser.add_argument("--horizon-h", type=float, default=DEFAULT_HORIZON_H)
    parser.add_argument("--cycle-date", default=None, help="YYYYMMDD; default: latest available")
    parser.add_argument("--cycle-hour", default=None, help="00/06/12/18; default: latest available")
    args = parser.parse_args()

    now_utc = datetime.now(UTC)
    if args.cycle_date and args.cycle_hour:
        cycle_date, cycle_hour = args.cycle_date, args.cycle_hour
    else:
        cycle_date, cycle_hour = latest_available_cycle_utc(now_utc)

    print(f"fetching ECMWF IFS cycle {cycle_date} {cycle_hour}z, horizon {args.horizon_h}h ...")
    geography = RealGeography()
    grid = build_grid(cycle_date, cycle_hour, args.horizon_h, geography)

    write_npz_atomic(
        Path(args.out),
        **grid,
        cycle=f"{cycle_date}_{cycle_hour}z",
        fetched=now_utc.isoformat(),
        source="ECMWF open data IFS 0.25 (oper wind + wave streams)",
    )
    print(f"wrote {grid['hs_m'].shape} grid -> {args.out}")


if __name__ == "__main__":
    main()
