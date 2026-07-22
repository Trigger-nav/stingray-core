#!/usr/bin/env python3
"""Merge a currents-only npz (`ingest/fetch_currents_cmems.py`) onto a
wind/wave npz's own grid + time axis (ticket C1) -- the file
`core.weather.GriddedWeatherField.from_npz` actually loads. Reads the
target's own `cycle` field (`"{cycle_date}_{cycle_hour}z"`, the exact
format every `fetch_grib_*.py` writes) to establish its absolute-UTC-time
reference, converts the currents-only npz's absolute `times` into that
reference's relative-hours convention, resamples current onto the
target's exact lat/lon grid (`xarray`'s `.interp()`, the same
WW3-onto-GFS / B7 ERA5-wave-onto-wind resampling pattern already used
twice in this codebase), and rewrites the target npz **in place** (every
other field copied through unchanged, `current_u_ms`/`current_v_ms`
replaced) via `write_npz_atomic`.

**Time-coverage policy (ticket C1 review, required amendment).**
`xarray`'s `.interp()` returns NaN for any query time outside the source
data's own covered range -- the same silent failure shape as
`core/weather.py`'s spatial `bilinear_masked` fix (this ticket), but on
the time axis: an out-of-range NaN current propagates to
`duration_h=inf` for the affected legs, silently, with nothing
explaining why a passage suddenly looks infeasible late in its own
planning horizon. A gap of <=`MAX_HOLD_NEAREST_GAP_H` at either end of
the weather npz's own hours range holds the nearest real currents sample
instead of extrapolating (logged at `WARNING`, recorded as a provenance
suffix on `current_source`) -- 6h is a small fraction of a semidiurnal
tidal cycle (~6.2h), a defensible "the tide hasn't moved far"
approximation, not an invented tolerance, but worth a second look once
real coverage-gap sizes between the two sources are observed in
practice. A gap beyond the tolerance is a **hard error** -- refuses to
write a merged file at all, rather than silently shipping a
partially-covered one; the pack's currents step then hits
`ingest/fetch_all_packs.py`'s failure isolation (wind/wave still
publishes, currents holds its previous value, `WARNING` logged there).

Usage:
    python3 -m ingest.merge_currents \\
        --weather-npz data/region_packs/<pack>/weather_<pack>.npz \\
        --currents-npz data/region_packs/<pack>/currents_<pack>.npz
"""

from __future__ import annotations

import argparse
import logging
from datetime import UTC, datetime

import numpy as np
import xarray as xr

from ingest.grib_common import write_npz_atomic

logger = logging.getLogger(__name__)

MAX_HOLD_NEAREST_GAP_H = 6.0


class CurrentsCoverageError(ValueError):
    """Raised when the currents-only npz's time coverage falls more than
    `MAX_HOLD_NEAREST_GAP_H` short of the weather npz's own hours range
    at either end -- refuses to silently ship a partially-covered merge."""


def _parse_cycle_to_utc(cycle: str) -> datetime:
    """`"{cycle_date}_{cycle_hour}z"` -> an absolute UTC datetime -- the
    exact format every `fetch_grib_*.py` writes (confirmed by reading
    `fetch_grib_nomads.py`'s `main()` directly:
    `cycle=f"{cycle_date}_{cycle_hour}z"`)."""
    date_part, hour_part = cycle.rstrip("z").split("_")
    return datetime.strptime(date_part + hour_part, "%Y%m%d%H").replace(tzinfo=UTC)


def resample_currents(
    weather_hours: np.ndarray,
    weather_cycle_start: datetime,
    currents_times: np.ndarray,
    current_u_ms: np.ndarray,
    current_v_ms: np.ndarray,
    weather_lats: np.ndarray,
    weather_lons: np.ndarray,
    currents_lat0: float,
    currents_dlat: float,
    currents_lon0: float,
    currents_dlon: float,
    *,
    pack_id: str = "",
) -> tuple[np.ndarray, np.ndarray, str]:
    """Pure resampling logic, separated from `main()`'s file I/O so it's
    directly testable against fabricated arrays. Returns
    `(resampled_current_u_ms, resampled_current_v_ms, coverage_note)` --
    `coverage_note` is `""` when the currents data fully covers the
    weather npz's hours range, else a human-readable held-nearest
    description for provenance. Raises `CurrentsCoverageError` if either
    end's gap exceeds `MAX_HOLD_NEAREST_GAP_H`, *before* calling
    `.interp()` at all."""
    n_lat_c, n_lon_c = current_u_ms.shape[1], current_u_ms.shape[2]
    currents_lats = currents_lat0 + currents_dlat * np.arange(n_lat_c)
    currents_lons = currents_lon0 + currents_dlon * np.arange(n_lon_c)

    weather_times_epoch = weather_cycle_start.timestamp() + np.asarray(weather_hours) * 3600.0

    gap_before_h = (currents_times[0] - weather_times_epoch[0]) / 3600.0
    gap_after_h = (weather_times_epoch[-1] - currents_times[-1]) / 3600.0

    coverage_note = ""
    for label, gap_h in (("start", gap_before_h), ("end", gap_after_h)):
        if gap_h > MAX_HOLD_NEAREST_GAP_H:
            raise CurrentsCoverageError(
                f"pack {pack_id!r}: currents data falls {gap_h:.1f}h short of the "
                f"weather npz's own hours range at the {label} -- exceeds the "
                f"{MAX_HOLD_NEAREST_GAP_H:.0f}h hold-nearest tolerance"
            )
        if gap_h > 0:
            logger.warning(
                "pack %r: currents data falls %.1fh short of the weather npz's "
                "hours range at the %s -- holding nearest available sample "
                "(within the %.0fh tolerance)",
                pack_id,
                gap_h,
                label,
                MAX_HOLD_NEAREST_GAP_H,
            )
            coverage_note += f" (held-nearest {gap_h:.1f}h at {label})"

    # Clipping the query times into the currents data's own real coverage
    # *is* the hold-nearest behaviour: `.interp()` on an in-range query
    # never returns NaN, and a gap beyond the tolerance above already
    # raised before reaching here.
    query_times = np.clip(weather_times_epoch, currents_times[0], currents_times[-1])

    u_da = xr.DataArray(
        current_u_ms,
        dims=("time", "latitude", "longitude"),
        coords={"time": currents_times, "latitude": currents_lats, "longitude": currents_lons},
    )
    v_da = xr.DataArray(
        current_v_ms,
        dims=("time", "latitude", "longitude"),
        coords={"time": currents_times, "latitude": currents_lats, "longitude": currents_lons},
    )
    u_resampled = u_da.interp(
        time=query_times, latitude=weather_lats, longitude=weather_lons
    ).values
    v_resampled = v_da.interp(
        time=query_times, latitude=weather_lats, longitude=weather_lons
    ).values
    return u_resampled, v_resampled, coverage_note


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weather-npz", required=True)
    parser.add_argument("--currents-npz", required=True)
    parser.add_argument("--pack-id", default="")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    weather = np.load(args.weather_npz, allow_pickle=False)
    currents = np.load(args.currents_npz, allow_pickle=False)

    weather_cycle = str(weather["cycle"])
    weather_cycle_start = _parse_cycle_to_utc(weather_cycle)
    weather_hours = weather["hours"]
    n_lat_w, n_lon_w = weather["hs_m"].shape[1], weather["hs_m"].shape[2]
    weather_lats = float(weather["lat0"]) + float(weather["dlat"]) * np.arange(n_lat_w)
    weather_lons = float(weather["lon0"]) + float(weather["dlon"]) * np.arange(n_lon_w)

    logger.info("merging CMEMS currents (%s) into %s ...", args.currents_npz, args.weather_npz)
    u_resampled, v_resampled, coverage_note = resample_currents(
        weather_hours,
        weather_cycle_start,
        currents["times"],
        currents["current_u_ms"],
        currents["current_v_ms"],
        weather_lats,
        weather_lons,
        float(currents["lat0"]),
        float(currents["dlat"]),
        float(currents["lon0"]),
        float(currents["dlon"]),
        pack_id=args.pack_id,
    )

    current_fetched = str(currents["fetched"]) if "fetched" in currents else ""
    current_source = str(currents["source"]) if "source" in currents else ""
    if coverage_note:
        current_source += coverage_note
    # CMEMS's analysis-forecast products don't have a discrete-cycle
    # concept the way GFS/ECMWF do -- there's no equivalent "cycle" value
    # to report beyond which day's run this represents.
    current_cycle = weather_cycle_start.strftime("%Y%m%d") + "_cmems"

    fields = {k: weather[k] for k in weather.files if k not in ("current_u_ms", "current_v_ms")}
    fields["current_u_ms"] = u_resampled
    fields["current_v_ms"] = v_resampled
    fields["current_cycle"] = current_cycle
    fields["current_fetched"] = current_fetched
    fields["current_source"] = current_source

    write_npz_atomic(args.weather_npz, **fields)
    print(f"merged currents into {args.weather_npz}")


if __name__ == "__main__":
    main()
