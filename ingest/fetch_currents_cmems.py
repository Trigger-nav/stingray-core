#!/usr/bin/env python3
"""Fetch surface ocean currents from the Copernicus Marine Service
(CMEMS) for a region pack's weather field (ticket C1) -- the
`current_u_ms`/`current_v_ms` fields `core/weather.py` has carried since
ticket B2, previously always written as zeros by every fetcher
(`ingest/fetch_grib_nomads.py`/`fetch_grib_ecmwf.py`).

Real dataset ids, verified live during planning and again against the
real `copernicusmarine.describe()` API during implementation --
`docs/plans/ticket-C1.md` has the full trace:
- **UK South-West / Channel (tidal signal)**:
  `cmems_mod_nws_phy-cur_anfc_1.5km-2D_PT1H-i` (product
  `NWSHELF_ANALYSISFORECAST_PHY_004_013`, hourly, 2D surface, 1.5km,
  includes tides -- confirmed live not superseded/retired,
  `admp_retired_date` is `null`).
- **Med** (not enabled for any pack in this ticket,
  `docs/plans/ticket-C1.md` Sec 5): `cmems_mod_med_phy-cur_anfc_4.2km-2D_PT1H-m`
  (product `MEDSEA_ANALYSISFORECAST_PHY_006_013`).

Variables `uo`/`vo` (eastward/northward sea water velocity, m/s,
confirmed via a real `copernicusmarine.describe()` call, not assumed) --
a physical "to" vector (the direction the water is moving toward),
matching `core.units.resolve_ground_speed_ms`'s own expectation exactly
(no sign flip needed) -- unlike WW3's wave direction, which is a "from"
convention (ticket 0.5). This is stated here from the CF/NEMO
`standard_name` convention (`eastward_sea_water_velocity`, unambiguous by
definition); cross-checked against a real fetched sample during the UK
acceptance run (`docs/plans/ticket-C1.md` Sec 6), not assumed by
inspection alone.

Auth: a free Copernicus Marine Service account
(https://data.marine.copernicus.eu), then either
`COPERNICUSMARINE_SERVICE_USERNAME`/`COPERNICUSMARINE_SERVICE_PASSWORD`
env vars (this repo's preferred form, matching `Settings.from_env()`'s
own env-var-first convention) or `copernicusmarine.login()`'s
`~/.copernicusmarine` credentials file -- cannot be automated or tested
in CI, same shape as ticket B7's CDS/`~/.cdsapirc` precedent.

Output schema is deliberately **not** this codebase's usual cycle-relative
`hours` array -- `times` (absolute UTC epoch floats) instead, because
CMEMS's daily-update cadence essentially never coincides with GFS/ECMWF's
own cycle starts, and merging two cycle-relative axes without first
converting to a shared absolute frame is a real ambiguity, not a detail
to wave away (`ingest/merge_currents.py`'s docstring has the full
reasoning). Nothing downstream sees this schema directly except that
merge step.

Usage:
    python3 -m ingest.fetch_currents_cmems --bbox LON_MIN LAT_MIN LON_MAX LAT_MAX \\
        --dataset-id cmems_mod_nws_phy-cur_anfc_1.5km-2D_PT1H-i \\
        --horizon-h 48 --out data/region_packs/<pack>/currents_<pack>.npz
"""

from __future__ import annotations

import argparse
import logging
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import xarray as xr

from core.geography import RealGeography
from ingest.grib_common import mask_land_as_missing, write_npz_atomic

logger = logging.getLogger(__name__)

DEFAULT_HORIZON_H = 48.0
VARIABLES = ["uo", "vo"]


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


def fetch_currents(
    dataset_id: str,
    bbox: tuple[float, float, float, float],
    start: datetime,
    end: datetime,
    geography: RealGeography,
    *,
    cache_dir: Path,
) -> dict:
    """One `copernicusmarine.subset()` call -> a currents-only grid dict
    (`lat0`/`dlat`/`lon0`/`dlon`/`times`/`current_u_ms`/`current_v_ms`),
    land-masked against `geography` the same way wave data already is
    (`ingest/grib_common.mask_land_as_missing`) -- current is ocean-only
    data with no over-land meaning, same reasoning as
    `core/weather.py`'s `bilinear_masked` switch (this ticket). Imports
    `copernicusmarine` locally, not at module level, so this module can
    be imported (e.g. by tests exercising the pure functions below)
    without the package/credentials being present."""
    import copernicusmarine

    lon_min, lat_min, lon_max, lat_max = bbox
    out_path = cache_dir / "currents.nc"
    copernicusmarine.subset(
        dataset_id=dataset_id,
        variables=VARIABLES,
        minimum_longitude=lon_min,
        maximum_longitude=lon_max,
        minimum_latitude=lat_min,
        maximum_latitude=lat_max,
        start_datetime=start.isoformat(),
        end_datetime=end.isoformat(),
        output_directory=str(cache_dir),
        output_filename=out_path.name,
        overwrite=True,
        disable_progress_bar=True,
    )
    with xr.open_dataset(out_path) as ds:
        return _grid_from_dataset(ds, geography)


def _grid_from_dataset(ds: xr.Dataset, geography: RealGeography) -> dict:
    """Split out from `fetch_currents`'s network call so it's directly
    testable against a fabricated in-memory `xr.Dataset` (no real
    network/credentials needed for this half)."""
    lats = ds["latitude"].values.astype(float)
    lons = ds["longitude"].values.astype(float)
    times_epoch = ds["time"].values.astype("datetime64[s]").astype(float)
    uo = np.asarray(ds["uo"].values, dtype=float)
    vo = np.asarray(ds["vo"].values, dtype=float)
    # The "2D" (surface) product variant has no depth axis -- (time, lat,
    # lon) -- but squeeze defensively in case a depth dimension of size 1
    # survives xarray's own selection, rather than assuming the raw shape.
    uo = np.squeeze(uo)
    vo = np.squeeze(vo)
    if uo.ndim == 2:  # a single-timestep fetch collapses the time axis
        uo = uo[np.newaxis, ...]
        vo = vo[np.newaxis, ...]

    current_u = mask_land_as_missing(uo, lats, lons, geography)
    current_v = mask_land_as_missing(vo, lats, lons, geography)

    return {
        "lat0": float(lats[0]),
        "dlat": float(lats[1] - lats[0]) if len(lats) > 1 else 0.0,
        "lon0": float(lons[0]),
        "dlon": float(lons[1] - lons[0]) if len(lons) > 1 else 0.0,
        "times": times_epoch,
        "current_u_ms": current_u,
        "current_v_ms": current_v,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bbox",
        type=float,
        nargs=4,
        required=True,
        metavar=("LON_MIN", "LAT_MIN", "LON_MAX", "LAT_MAX"),
    )
    parser.add_argument(
        "--dataset-id",
        required=True,
        help="e.g. cmems_mod_nws_phy-cur_anfc_1.5km-2D_PT1H-i -- see module docstring",
    )
    parser.add_argument("--horizon-h", type=float, default=DEFAULT_HORIZON_H)
    parser.add_argument("--out", required=True)
    parser.add_argument("--coastline-path", default=None)
    parser.add_argument("--bathymetry-path", default=None)
    parser.add_argument("--nogo-path", default=None)
    parser.add_argument("--tss-path", default=None)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    bbox = tuple(args.bbox)
    geography = RealGeography(**_geography_kwargs_from_args(args))

    now_utc = datetime.now(UTC)
    start = now_utc
    end = now_utc + timedelta(hours=args.horizon_h)

    logger.info(
        "fetching CMEMS currents %s, bbox %s, %s to %s ...",
        args.dataset_id,
        bbox,
        start.isoformat(),
        end.isoformat(),
    )
    with tempfile.TemporaryDirectory() as tmp:
        grid = fetch_currents(args.dataset_id, bbox, start, end, geography, cache_dir=Path(tmp))

    write_npz_atomic(
        args.out,
        lat0=grid["lat0"],
        dlat=grid["dlat"],
        lon0=grid["lon0"],
        dlon=grid["dlon"],
        times=grid["times"],
        current_u_ms=grid["current_u_ms"],
        current_v_ms=grid["current_v_ms"],
        fetched=now_utc.isoformat(),
        source=args.dataset_id,
    )
    print(f"wrote {grid['current_u_ms'].shape} current grid -> {args.out}")


if __name__ == "__main__":
    main()
