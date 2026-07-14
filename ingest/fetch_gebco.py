#!/usr/bin/env python3
"""
Stingray ingestion layer — GEBCO_2024 bathymetry (production path, ticket 0.3).

The GEBCO_2024 grid (CEDA-hosted) is ~7GB at native 15 arc-second resolution
— far too large to download whole for a single corridor. CEDA doesn't expose
a real OPeNDAP endpoint for it, but the file is plain HTTP with Range-request
support, and it's HDF5-backed (netCDF4), so `fsspec` + `h5netcdf` can open it
lazily and pull only the chunks covering our bbox — verified in practice:
~80s one-time fetch for the western-Med corridor slice, not a multi-GB
download.

Requires xarray + h5netcdf + h5py + fsspec + aiohttp (ingest-only deps, see
pyproject.toml's `ingest` extra) — core/ stays numpy+PyYAML only.

Usage: python3 -m ingest.fetch_gebco [--out PATH]
       [--bbox LON_MIN LAT_MIN LON_MAX LAT_MAX]

`--bbox` defaults to `core.geography.OPERATING_AREA_BBOX` (the committed
western-Med corridor). Passing a *different* bbox (ticket B7's
track-driven ingest) requires `--out` to also be given explicitly, so a
second bbox's ingest run can't silently clobber the committed western-Med
`.npz` by falling back to the same default path. Run this *before*
`fetch_gshhg.py` for any new bbox -- that script rasterises its land mask
onto whatever grid geometry this one writes.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import fsspec
import numpy as np
import xarray as xr

from core.geography import OPERATING_AREA_BBOX

logger = logging.getLogger(__name__)

GEBCO_URL = (
    "https://dap.ceda.ac.uk/bodc/gebco/global/gebco_2024/"
    "ice_surface_elevation/netcdf/GEBCO_2024_CF.nc"
)

# The western-Med corridor bbox is ~11.2 deg^2 and its GEBCO fetch was
# verified live at ~80s (module docstring). A track-driven bbox could be
# much larger (e.g. a long ocean-crossing passage) with no size/time
# budget verified here -- flag, don't solve (ticket B7 Part 1): warn, not
# block, above 5x that reference area.
_REFERENCE_BBOX_AREA_DEG2 = (10.15 - 6.7) * (44.0 - 40.75)
_LARGE_BBOX_AREA_DEG2 = 5 * _REFERENCE_BBOX_AREA_DEG2


def _bbox_area_deg2(bbox: tuple[float, float, float, float]) -> float:
    lon_min, lat_min, lon_max, lat_max = bbox
    return abs(lon_max - lon_min) * abs(lat_max - lat_min)


def fetch_subset(
    bbox: tuple[float, float, float, float],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    lon_min, lat_min, lon_max, lat_max = bbox
    fs = fsspec.filesystem("http")
    f = fs.open(GEBCO_URL, mode="rb")
    ds = xr.open_dataset(f, engine="h5netcdf")
    sub = ds.sel(lat=slice(lat_min, lat_max), lon=slice(lon_min, lon_max))
    elevation_m = sub["elevation"].values.astype("float64")
    lats = sub["lat"].values
    lons = sub["lon"].values
    return lats, lons, elevation_m


_DEFAULT_OUT = "data/geography/bathymetry_western_med.npz"


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=_DEFAULT_OUT)
    parser.add_argument(
        "--bbox",
        type=float,
        nargs=4,
        metavar=("LON_MIN", "LAT_MIN", "LON_MAX", "LAT_MAX"),
        default=None,
        help="defaults to core.geography.OPERATING_AREA_BBOX (western Med)",
    )
    args = parser.parse_args()

    bbox = tuple(args.bbox) if args.bbox else OPERATING_AREA_BBOX
    if bbox != OPERATING_AREA_BBOX and args.out == _DEFAULT_OUT:
        parser.error(
            "--bbox differs from OPERATING_AREA_BBOX -- --out must be set explicitly "
            "too, so this run can't overwrite the committed western-Med data by "
            "falling back to its default path"
        )
    if _bbox_area_deg2(bbox) > _LARGE_BBOX_AREA_DEG2:
        logger.warning(
            "bbox %s is %.1f deg^2, over 5x the western-Med corridor's ~%.1f deg^2 "
            "reference fetch (verified ~80s there) -- this fetch's size/time budget "
            "is unverified for a bbox this large",
            bbox,
            _bbox_area_deg2(bbox),
            _REFERENCE_BBOX_AREA_DEG2,
        )

    print(f"fetching bbox {bbox} from {GEBCO_URL} (lazy HTTP range reads, ~1-2 min)...")
    lats, lons, elevation_m = fetch_subset(bbox)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        out_path,
        lat0=lats[0],
        dlat=lats[1] - lats[0],
        lon0=lons[0],
        dlon=lons[1] - lons[0],
        nlat=len(lats),
        nlon=len(lons),
        elevation_m=elevation_m,
        source="GEBCO_2024_CF.nc (CEDA), bbox-cropped, elevation in metres (negative=depth)",
    )
    print(f"wrote {elevation_m.shape} grid -> {out_path}")


if __name__ == "__main__":
    main()
