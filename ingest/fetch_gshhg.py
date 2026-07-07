#!/usr/bin/env python3
"""
Stingray ingestion layer — GSHHG coastline (production path, ticket 0.3),
plus a rasterised land mask for the `is_land` hot path (ticket 0.4 review
follow-up).

Downloads the GSHHG shapefile bundle (public domain, NOAA/SOEST), extracts
the high-resolution L1 (land/ocean boundary) layer, clips every intersecting
polygon to the western-Med corridor bbox, and writes a compact JSON polygon
list that `core/geography.py`'s `RealGeography` loads directly — no
shapefile parsing, no shapely, at runtime.

"High" resolution (not "intermediate") is used deliberately: the Bonifacio
Strait is narrow and CLAUDE.md already flags it as a fidelity-sensitive spot.

GSHHG's L1 layer represents Eurasia/Africa as a single continent-spanning
polygon (~140k points) — every matching polygon must be clipped to the bbox
with real geometry intersection (shapely), not just filtered by bounding-box
overlap, or the output would be enormous.

The rasterisation step (added ticket 0.4) reuses those same clipped polygons
— rasterised, via `shapely.contains_xy`, onto the *same grid geometry*
`ingest/fetch_gebco.py` already produced, so `RealGeography` only carries
one grid definition. This requires `data/geography/bathymetry_western_med.npz`
to already exist (run `fetch_gebco.py` first) — the land mask is written
into that same file, alongside `elevation_m`. `RealGeography.is_land` uses
it as the fast path; `is_land_precise` (the polygon ray-cast) stays
available and is what the raster is tested against, not replaced.

Requires pyshp + shapely (ingest-only deps, see pyproject.toml's `ingest`
extra) — core/ stays numpy+PyYAML only.

Usage: python3 -m ingest.fetch_gshhg [--cache-dir DIR] [--out PATH] [--bathymetry PATH]
"""

from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path
from urllib.request import urlretrieve

import numpy as np
import shapefile
from shapely import contains_xy
from shapely.geometry import Polygon, box, shape
from shapely.ops import unary_union

from core.geography import DEFAULT_BATHYMETRY_PATH, OPERATING_AREA_BBOX

GSHHG_URL = "https://www.soest.hawaii.edu/pwessel/gshhg/gshhg-shp-2.3.7.zip"
SHAPEFILE_MEMBER_PREFIX = "GSHHS_shp/h/GSHHS_h_L1"


def _ensure_shapefile(cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    zip_path = cache_dir / "gshhg-shp.zip"
    if not zip_path.exists():
        print(f"downloading {GSHHG_URL} -> {zip_path} (~150MB, one-time)")
        urlretrieve(GSHHG_URL, zip_path)
    shp_path = cache_dir / f"{SHAPEFILE_MEMBER_PREFIX}.shp"
    if not shp_path.exists():
        with zipfile.ZipFile(zip_path) as zf:
            for ext in ("shp", "shx", "dbf", "prj"):
                zf.extract(f"{SHAPEFILE_MEMBER_PREFIX}.{ext}", cache_dir)
    return shp_path


def clip_to_bbox(
    shp_path: Path, bbox: tuple[float, float, float, float]
) -> list[list[list[float]]]:
    lon_min, lat_min, lon_max, lat_max = bbox
    bbox_geom = box(lon_min, lat_min, lon_max, lat_max)
    sf = shapefile.Reader(str(shp_path))
    polygons: list[list[list[float]]] = []
    for s in sf.shapes():
        if s.bbox[2] < lon_min or s.bbox[0] > lon_max or s.bbox[3] < lat_min or s.bbox[1] > lat_max:
            continue
        geom = shape(s.__geo_interface__)
        clipped = geom.intersection(bbox_geom)
        if clipped.is_empty:
            continue
        pieces = list(clipped.geoms) if hasattr(clipped, "geoms") else [clipped]
        for piece in pieces:
            if piece.geom_type != "Polygon":
                continue
            # [lat, lon] pairs, matching SyntheticGeography's LAND convention.
            polygons.append([[lat, lon] for lon, lat in piece.exterior.coords])
    return polygons


def rasterize_onto_grid(
    polygons_lat_lon: list[list[list[float]]],
    lat0: float,
    dlat: float,
    lon0: float,
    dlon: float,
    nlat: int,
    nlon: int,
) -> np.ndarray:
    """Boolean (nlat, nlon) land mask, True where a grid-cell centre falls
    inside any of the clipped GSHHG polygons. Vectorised (`shapely.contains_xy`)
    — a naive per-cell ray-cast over ~650k cells would be far too slow for
    a script that's still meant to run in reasonable ingest time."""
    shapely_polys = [Polygon([(lon, lat) for lat, lon in poly]) for poly in polygons_lat_lon]
    union = unary_union(shapely_polys)
    lats = lat0 + dlat * np.arange(nlat)
    lons = lon0 + dlon * np.arange(nlon)
    lon_mesh, lat_mesh = np.meshgrid(lons, lats)
    return contains_xy(union, lon_mesh, lat_mesh)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", default=".cache/gshhg", help="local download/extract cache")
    parser.add_argument("--out", default="data/geography/coastline_western_med.json")
    parser.add_argument(
        "--bathymetry",
        default=DEFAULT_BATHYMETRY_PATH,
        help="existing bathymetry npz (run fetch_gebco.py first) to rasterize the land mask onto",
    )
    args = parser.parse_args()

    shp_path = _ensure_shapefile(Path(args.cache_dir))
    polygons = clip_to_bbox(shp_path, OPERATING_AREA_BBOX)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "source": "GSHHG 2.3.7, high resolution, L1 (land/ocean boundary), clipped to bbox",
                "bbox_lon_lat": list(OPERATING_AREA_BBOX),
                "polygons": polygons,
            }
        )
    )
    n_points = sum(len(p) for p in polygons)
    print(f"wrote {len(polygons)} polygons ({n_points} points) -> {out_path}")

    bathymetry_path = Path(args.bathymetry)
    if not bathymetry_path.exists():
        print(f"skipping land-mask rasterisation: {bathymetry_path} not found (run fetch_gebco.py)")
        return

    grid = dict(np.load(bathymetry_path))
    land_mask = rasterize_onto_grid(
        polygons,
        float(grid["lat0"]),
        float(grid["dlat"]),
        float(grid["lon0"]),
        float(grid["dlon"]),
        int(grid["nlat"]),
        int(grid["nlon"]),
    )
    grid["land_mask"] = land_mask
    np.savez_compressed(bathymetry_path, **grid)
    print(f"wrote {land_mask.sum()}/{land_mask.size} land cells -> {bathymetry_path} (land_mask)")


if __name__ == "__main__":
    main()
