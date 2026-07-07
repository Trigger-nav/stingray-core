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
"""

from __future__ import annotations

import argparse
from pathlib import Path

import fsspec
import numpy as np
import xarray as xr

GEBCO_URL = (
    "https://dap.ceda.ac.uk/bodc/gebco/global/gebco_2024/"
    "ice_surface_elevation/netcdf/GEBCO_2024_CF.nc"
)

# Western-Med corridor bbox: lon_min, lat_min, lon_max, lat_max — matches
# ingest/fetch_gshhg.py and core/corridors.py's operating area.
BBOX = (6.7, 40.75, 10.15, 44.0)


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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="data/geography/bathymetry_western_med.npz")
    args = parser.parse_args()

    print(f"fetching bbox {BBOX} from {GEBCO_URL} (lazy HTTP range reads, ~1-2 min)...")
    lats, lons, elevation_m = fetch_subset(BBOX)

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
