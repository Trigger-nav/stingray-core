#!/usr/bin/env python3
"""
Stingray ingestion layer — ERA5 reanalysis track annotator (ticket B7
Part 2): given a historical `(t, lat, lon)` track (`core.track.TrackPoint`,
`ingest/track_io.py`'s CSV format), appends hs/period/wave-dir/wind from
ERA5 reanalysis at each point's own position and time.

**Genuinely new integration, not an extension of `fetch_grib_ecmwf.py`.**
That script targets ECMWF *open data* (anonymous, synchronous HTTP Range-
GET against a public `.index`-addressed file). ERA5 is the Copernicus
Climate Data Store's reanalysis product, retrieved via the `cdsapi`
package against a **registered** account: sign up at
https://cds.climate.copernicus.eu, accept the ERA5 single-levels
dataset's licence, and generate an API key into `~/.cdsapirc` (a
`url:`/`key:` pair) — this cannot be automated or verified in CI, and is
a one-time manual setup step distinct from `cfgrib`'s system-library
caveat. `cdsapi.Client().retrieve(...)` submits an async job the CDS
backend queues and processes — nothing like open data's immediate
synchronous GET.

**Design — zero new interpolation code, the strongest reuse win found in
planning.** CDS's request grain is a spatial grid (bbox + variable list +
time list), not point extraction, so this module (1) computes one
covering bbox+time-span for the *whole* track (`core.track.covering_bbox`/
`covering_time_range_s` — Part 1's bbox-from-track machinery, reused
literally, not reimplemented), (2) issues one CDS request for that
bbox+span, (3) builds an in-memory `core.weather.GriddedWeatherField`
directly from the decoded response (`GriddedWeatherField.__init__`, no
temp npz needed), then (4) calls that field's existing, **completely
unchanged** `.sample(lat_deg, lon_deg, t_h)` once per track row — the
same real bilinear-in-space + linear-in-time interpolation the optimiser
itself uses. `ingest/verify_grib_consistency.py` is the closest existing
precedent for "loop over points/times, call `.sample()`" (it diffs two
fields for a fixed check-point list; this appends to track rows instead).

**Conventions — verified live against a real CDS response (2026-07-14,
this ticket's manual verification step)**, following ticket 0.5's own
precedent (see CLAUDE.md's GRIB-conventions gotcha for the template this
follows). Findings, all confirmed against a real request (not assumed):
(1) decoded variable short names are exactly `swh`/`mwd`/`pp1d`/`mwp`/
`u10`/`v10` as guessed by analogy with `fetch_grib_ecmwf.py`'s open-data
naming. (2) The time coordinate is `valid_time`, not `time` --
`_time_coord_name`'s fallback (kept, not simplified) already covers this.
(3) **A real combined wind+wave request comes back as a zip archive**,
not a raw NetCDF, containing two *separate* per-stream files
(`data_stream-oper_stepType-instant.nc` for wind, `data_stream-wave_
stepType-instant.nc` for wave) -- `_open_era5_response` below handles
this (found live, not documented anywhere in `cdsapi`'s own docs at
scoping time). (4) **ERA5's wave stream (WAM model) is natively on a
coarser grid than the 0.25deg wind/atmospheric stream** -- confirmed live
(oper 0.25deg vs wave ~0.5deg for the same request), the same real
GFS-wind/WW3-wave resolution mismatch `fetch_grib_nomads.py` already
resamples via `.interp()`; `_merge_era5_streams`/`_resample_onto_grid`
below do the same, with a broadcast fallback (linear interpolation is
undefined with only one source point along an axis) for small-bbox
requests whose wave grid degenerates to a single cell. (5) `mwd`'s
from-convention cross-checked live against the already-committed real
ECMWF open-data npz (`data/weather/ecmwf_western_med.npz`,
ticket 0.5's own real fetch) at the same real point/time (42.5N, 8.5E,
2026-07-08T00:00Z) -- see `docs/plans/ticket-B7.md`'s "Live ERA5
verification result" for the actual numbers; `ERA5_MWD_IS_TO_CONVENTION`
stays `False`, confirmed not just assumed. Longitude convention (0-360
native) held for the real response as expected.

Requires `cdsapi` (ingest-only dep, see pyproject.toml's `ingest` extra).

Usage: python3 -m ingest.fetch_era5_track TRACK_CSV --out OUT_CSV
       [--margin-deg DEG] [--coastline-path PATH] [--bathymetry-path PATH]
       [--nogo-path PATH] [--tss-path PATH]
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import tempfile
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import xarray as xr

from core.geography import RealGeography
from core.track import TrackPoint, covering_bbox, covering_time_range_s
from core.weather import GriddedWeatherField
from ingest.grib_common import (
    apply_coastal_fill,
    coastal_fill_mask,
    direction_to_from_convention_deg,
    mask_land_as_missing,
    normalise_and_sort_dataset,
)
from ingest.track_io import read_track_csv, write_track_csv

logger = logging.getLogger(__name__)

CDS_DATASET = "reanalysis-era5-single-levels"

# CDS request-variable names -> expected decoded NetCDF short names.
# Mirrors fetch_grib_ecmwf.py's WAVE_PARAMS short-name mapping for the
# wave fields (pp1d/mwp/mwd/swh) by analogy -- ERA5 is a different
# product from ECMWF open data, so this is an assumption pending live
# verification (see module docstring), not inherited fact.
CDS_VARIABLES: dict[str, str] = {
    "swh": "significant_height_of_combined_wind_waves_and_swell",
    "mwd": "mean_wave_direction",
    "pp1d": "peak_wave_period",  # amendment 2: the load-bearing period field
    "mwp": "mean_wave_period",  # carried alongside, additive (WeatherSample's peak/mean pair)
    "u10": "10m_u_component_of_wind",
    "v10": "10m_v_component_of_wind",
}

# Assumption by analogy with ECMWF open data's confirmed-from-convention
# `mwd` (CLAUDE.md's GRIB-conventions gotcha) -- flip after live
# verification if a real run disagrees.
ERA5_MWD_IS_TO_CONVENTION = False

# Minor flag (ticket B7 review): noon-report-derived tracks can span a
# whole season -- warn, don't block, before issuing one monolithic
# request for that whole span (matching Part 1's GEBCO large-bbox-size
# guard's style).
LARGE_SPAN_WARNING_DAYS = 30.0


def _build_cds_request(
    bbox: tuple[float, float, float, float], t_min_s: float, t_max_s: float
) -> dict:
    """One CDS ERA5 single-levels request dict covering the whole track's
    bbox+time span. `area` is `[North, West, South, East]` per the CDS
    API's documented order. `year`/`month`/`day` are independent lists
    (not an explicit date list) -- CDS's backend resolves the cross
    product to just the real calendar dates that exist, the standard
    idiomatic multi-day ERA5 request shape."""
    lon_min, lat_min, lon_max, lat_max = bbox
    start = datetime.fromtimestamp(t_min_s, tz=UTC)
    end = datetime.fromtimestamp(t_max_s, tz=UTC)
    days = []
    d = start.date()
    while d <= end.date():
        days.append(d)
        d += timedelta(days=1)
    return {
        "product_type": "reanalysis",
        "variable": list(CDS_VARIABLES.values()),
        "year": sorted({f"{d.year:04d}" for d in days}),
        "month": sorted({f"{d.month:02d}" for d in days}),
        "day": sorted({f"{d.day:02d}" for d in days}),
        "time": [f"{h:02d}:00" for h in range(24)],
        "area": [lat_max, lon_min, lat_min, lon_max],
        "format": "netcdf",
    }


def fetch_era5_netcdf(
    bbox: tuple[float, float, float, float], t_min_s: float, t_max_s: float, dest: Path
) -> None:
    """Issues the real CDS request. Deferred `cdsapi` import -- a
    credentialed, ingest-only dependency; importing it at module scope
    would make every other function in this module (and anything mocking
    `cdsapi.Client` in tests) pay for a real `~/.cdsapirc` check that
    isn't needed until a request actually fires."""
    import cdsapi

    request = _build_cds_request(bbox, t_min_s, t_max_s)
    client = cdsapi.Client()
    client.retrieve(CDS_DATASET, request, str(dest))


def _find_var(ds: xr.Dataset, short_name: str) -> xr.DataArray:
    if short_name not in ds.data_vars:
        raise KeyError(
            f"{short_name!r} not found in ERA5 response variables {list(ds.data_vars)} -- "
            "CDS_VARIABLES' short-name mapping may not match a real response "
            "(see module docstring's conventions caveat)"
        )
    return ds[short_name]


def _time_coord_name(ds: xr.Dataset) -> str:
    """CDS's netCDF time coordinate has historically been `time`; a real
    response (verified live, 2026-07-14) uses `valid_time` -- try both
    rather than assuming either is universal."""
    for name in ("time", "valid_time"):
        if name in ds.coords:
            return name
    raise KeyError(f"no 'time'/'valid_time' coordinate in ERA5 response coords {list(ds.coords)}")


def _open_era5_response(path: Path) -> list[xr.Dataset]:
    """A real combined wind+wave ERA5 request comes back as a **zip
    archive** (found live, 2026-07-14 -- not documented anywhere in
    `cdsapi`'s own docs at scoping time), containing one NetCDF file per
    stream (`data_stream-oper_stepType-instant.nc` for wind,
    `data_stream-wave_stepType-instant.nc` for wave). `.load()`s each
    dataset into memory before the extraction tempdir is cleaned up.
    Falls back to opening `path` directly for a raw (non-zip) response,
    in case a future/different request shape ever returns one."""
    if not zipfile.is_zipfile(path):
        return [xr.open_dataset(path)]
    datasets = []
    with zipfile.ZipFile(path) as zf, tempfile.TemporaryDirectory() as tmp:
        zf.extractall(tmp)
        for name in zf.namelist():
            datasets.append(xr.open_dataset(Path(tmp) / name).load())
    return datasets


def _resample_onto_grid(
    source: xr.Dataset, target_lat: xr.DataArray, target_lon: xr.DataArray
) -> xr.Dataset:
    """Resamples `source` onto `target_lat`/`target_lon`. Per-axis: more
    than one source point along that axis -> linear `.interp()` (the same
    resampling `fetch_grib_nomads.py` already does for its own GFS-wind/
    WW3-wave grid mismatch); exactly one source point -> broadcast that
    value across the target axis instead, since linear interpolation is
    mathematically undefined with a single source point (found live,
    2026-07-14: a small-bbox track request can make ERA5's coarser-native
    wave grid degenerate to one cell -- `.interp()` alone returns all-NaN
    there). Broadcasting is the physically reasonable treatment: the
    source genuinely has no finer spatial detail to offer within that
    one native grid cell."""
    result = source
    if source.sizes.get("latitude", 0) > 1:
        result = result.interp(latitude=target_lat)
    else:
        result = result.isel(latitude=0, drop=True).expand_dims(latitude=target_lat)
    if source.sizes.get("longitude", 0) > 1:
        result = result.interp(longitude=target_lon)
    else:
        result = result.isel(longitude=0, drop=True).expand_dims(longitude=target_lon)
    return result


def _merge_era5_streams(datasets: list[xr.Dataset]) -> xr.Dataset:
    """Merges one or more per-stream datasets (`_open_era5_response`)
    onto one shared grid -- the finest (most lat/lon points) stream's
    grid becomes the combined grid, downsampling coarser streams rather
    than inventing spatial detail upsampling them (same rationale
    `fetch_grib_nomads.py`'s `build_grid` already documents for its own
    wind/wave mismatch)."""
    sorted_ds = [normalise_and_sort_dataset(ds) for ds in datasets]
    if len(sorted_ds) == 1:
        return sorted_ds[0]
    reference = max(
        sorted_ds, key=lambda ds: ds.sizes.get("latitude", 0) * ds.sizes.get("longitude", 0)
    )
    merged = reference
    for ds in sorted_ds:
        if ds is reference:
            continue
        resampled = _resample_onto_grid(ds, reference.latitude, reference.longitude)
        # compat="override": streams have disjoint variable names (wind
        # vs wave), so there's never a real conflict to resolve -- pins
        # xarray's default explicitly rather than relying on a value
        # that's set to change in a future xarray version.
        merged = merged.merge(resampled, compat="override")
    return merged


def build_grid_from_netcdf(
    nc_path: Path, geography: RealGeography, reference_epoch_s: float
) -> GriddedWeatherField:
    """Decodes the CDS response (`_open_era5_response`/
    `_merge_era5_streams` -- handles the real zip-archive, split-stream,
    mismatched-resolution response shape found live) into an in-memory
    `GriddedWeatherField`, `hours` measured relative to
    `reference_epoch_s` (the track's own earliest timestamp) so
    `.sample(lat, lon, t_h)` can be called with each track row's own
    `t_h` computed the same way."""
    ds = _merge_era5_streams(_open_era5_response(nc_path))
    time_name = _time_coord_name(ds)
    ds = ds.transpose(time_name, "latitude", "longitude")
    times_s = ds[time_name].values.astype("datetime64[s]").astype(float)
    hours = (times_s - reference_epoch_s) / 3600.0

    lats = ds.latitude.values.astype(float)
    lons = ds.longitude.values.astype(float)

    hs_m = _find_var(ds, "swh").values.astype(float)
    period_peak_s = _find_var(ds, "pp1d").values.astype(float)
    period_mean_s = _find_var(ds, "mwp").values.astype(float)
    wave_from_deg = direction_to_from_convention_deg(
        _find_var(ds, "mwd").values.astype(float), source_is_to_convention=ERA5_MWD_IS_TO_CONVENTION
    )
    wind_u_ms = _find_var(ds, "u10").values.astype(float)
    wind_v_ms = _find_var(ds, "v10").values.astype(float)

    # Land -> NaN for wave fields only, not wind (B2's convention,
    # reused unchanged -- see core.weather.GriddedWeatherField's
    # docstring). This grid IS still spatial-grid-shaped at this point
    # (unlike the final per-row track output), so mask_land_as_missing
    # applies directly, same as fetch_grib_ecmwf.py/fetch_grib_nomads.py.
    hs_m = mask_land_as_missing(hs_m, lats, lons, geography)
    period_peak_s = mask_land_as_missing(period_peak_s, lats, lons, geography)
    period_mean_s = mask_land_as_missing(period_mean_s, lats, lons, geography)
    wave_from_deg = mask_land_as_missing(wave_from_deg, lats, lons, geography)

    # Ticket W1: coastal fill, same shared geometry as fetch_grib_ecmwf.py/
    # fetch_grib_nomads.py.
    ref_lat_deg = float(np.mean(lats))
    fill_mask = coastal_fill_mask(lats, lons, geography)
    hs_m, wave_filled_cells, _ = apply_coastal_fill(
        hs_m, lats, lons, fill_mask, ref_lat_deg=ref_lat_deg, field_name="hs_m"
    )
    period_peak_s, _, _ = apply_coastal_fill(
        period_peak_s, lats, lons, fill_mask, ref_lat_deg=ref_lat_deg, field_name="period_peak_s"
    )
    period_mean_s, _, _ = apply_coastal_fill(
        period_mean_s, lats, lons, fill_mask, ref_lat_deg=ref_lat_deg, field_name="period_mean_s"
    )
    wave_from_deg, _, _ = apply_coastal_fill(
        wave_from_deg, lats, lons, fill_mask, ref_lat_deg=ref_lat_deg, field_name="wave_from_deg"
    )

    n_hours = len(hours)
    zeros = np.zeros((n_hours, len(lats), len(lons)))

    return GriddedWeatherField(
        lat0_deg=float(lats[0]),
        dlat_deg=float(lats[1] - lats[0]),
        lon0_deg=float(lons[0]),
        dlon_deg=float(lons[1] - lons[0]),
        hours=list(hours),
        hs_m=hs_m,
        period_peak_s=period_peak_s,
        period_mean_s=period_mean_s,
        wave_from_deg=wave_from_deg,
        wind_u_ms=wind_u_ms,
        wind_v_ms=wind_v_ms,
        current_u_ms=zeros,
        current_v_ms=zeros,
        source="ERA5 reanalysis (CDS reanalysis-era5-single-levels)",
        wave_filled_cells=wave_filled_cells,
    )


def annotate_track(
    points: list[TrackPoint], field: GriddedWeatherField, reference_epoch_s: float
) -> list[TrackPoint]:
    """Loop-and-sample: the one genuinely new per-track-row step, calling
    `field.sample()` unchanged once per row."""
    annotated = []
    for p in points:
        t_h = (p.t_epoch_s - reference_epoch_s) / 3600.0
        sample = field.sample(p.lat_deg, p.lon_deg, t_h)
        annotated.append(
            dataclasses.replace(
                p,
                hs_m=sample.hs_m,
                period_peak_s=sample.period_peak_s,
                period_mean_s=sample.period_mean_s,
                wave_from_deg=sample.wave_from_deg,
                wind_u_ms=sample.wind_u_ms,
                wind_v_ms=sample.wind_v_ms,
            )
        )
    return annotated


def _geography_kwargs_from_args(args: argparse.Namespace) -> dict[str, str]:
    kwargs = {}
    if args.coastline_path:
        kwargs["coastline_path"] = args.coastline_path
    if args.bathymetry_path:
        kwargs["bathymetry_path"] = args.bathymetry_path
    if args.nogo_path:
        kwargs["nogo_path"] = args.nogo_path
    if args.tss_path:
        kwargs["tss_path"] = args.tss_path
    return kwargs


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("track_csv", help="a core.track.TrackPoint CSV (ingest/track_io.py)")
    parser.add_argument("--out", required=True, help="annotated track CSV output path")
    parser.add_argument("--margin-deg", type=float, default=0.25)
    parser.add_argument("--coastline-path", default=None)
    parser.add_argument("--bathymetry-path", default=None)
    parser.add_argument("--nogo-path", default=None)
    parser.add_argument("--tss-path", default=None)
    args = parser.parse_args()

    points = read_track_csv(args.track_csv)
    bbox = covering_bbox(points, margin_deg=args.margin_deg)
    t_min_s, t_max_s = covering_time_range_s(points)
    span_days = (t_max_s - t_min_s) / 86400.0
    if span_days > LARGE_SPAN_WARNING_DAYS:
        logger.warning(
            "track spans %.1f days (>%.0f) -- issuing one monolithic CDS "
            "request for the whole span; cost/latency for a span this "
            "large is unverified (flagged, not solved -- see module docstring)",
            span_days,
            LARGE_SPAN_WARNING_DAYS,
        )

    geography = RealGeography(**_geography_kwargs_from_args(args))
    with tempfile.TemporaryDirectory() as tmp:
        nc_path = Path(tmp) / "era5_track.nc"
        print(
            f"requesting ERA5 for bbox {bbox}, {len(points)} track points "
            f"({span_days:.1f} days)..."
        )
        fetch_era5_netcdf(bbox, t_min_s, t_max_s, nc_path)
        field = build_grid_from_netcdf(nc_path, geography, reference_epoch_s=t_min_s)

    annotated = annotate_track(points, field, reference_epoch_s=t_min_s)
    write_track_csv(args.out, annotated)
    print(f"wrote {len(annotated)} annotated points -> {args.out}")


if __name__ == "__main__":
    main()
