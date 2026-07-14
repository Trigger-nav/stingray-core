"""Geography interface (E1) and two backing implementations.

`SyntheticGeography` is ported from the demo's LAND/NOGO polygons and
chart-layer bathymetry heuristic (`prototype/stingray_planner.html`) —
still used by the optimiser regression/constraint tests, which are testing
optimiser logic against a known, deterministic geography, not coastline
fidelity.

`RealGeography` (ticket 0.3) is sourced from real public datasets — GSHHG
coastline (land/no-go) and GEBCO_2024 bathymetry (depth) — over the same
western-Med corridor bbox, produced by `ingest/fetch_gshhg.py` and
`ingest/fetch_gebco.py` into the two small files it loads. Both classes
satisfy the same `Geography` protocol, so `optimiser.py`/`twin.py` never
depend on which one is in use.

Explicit scope boundary (see the ticket 0.3 plan): this only sources real
land + depth *data*. `depth_m` is wired into the optimiser as a hard
constraint starting ticket 0.8 (`core/legs.py`), same as land/no-go (A5).

**Ticket 0.8:** `RealGeography` loads real (if not yet precisely-verified
— see each zone's `precise_boundary_verified` flag) no-go + TSS
separation-zone polygons from `data/geography/nogo_western_med.json` and
`tss_western_med.json`, replacing the synthetic `NOGO` boxes below for
real-geography use. `SyntheticGeography` keeps using the synthetic `NOGO`
list unchanged — it's a deterministic fixture for optimiser tests, not
meant to track real data.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Protocol

import numpy as np

from core.gridding import bilinear, grid_fracs, nearest

# Western-Med corridor bbox real data is cropped to: lon_min, lat_min,
# lon_max, lat_max. Shared with ingest/fetch_gshhg.py and
# ingest/fetch_gebco.py (both import this rather than redefining it) and
# with core/lattice.py (ticket 0.4), which clips the open lattice to stay
# inside it with margin — RealGeography's bounds check below is then a
# defensive backstop that should never fire in normal operation.
OPERATING_AREA_BBOX = (6.7, 40.75, 10.15, 44.0)


class OutOfOperatingAreaError(ValueError):
    """Raised when a `RealGeography` instance is queried outside the area
    its loaded data actually covers — real data doesn't exist there, so
    silently returning "not land"/a clamped depth would be misleading
    rather than merely imprecise. The default-constructed instance covers
    `OPERATING_AREA_BBOX` (western Med); an instance loaded from a
    different bbox's files (ticket B7 Part 1) covers whatever bbox that
    data was cropped to instead — see `RealGeography._check_in_bounds`."""


# Land polygons (lat, lon) — western Mediterranean corridor, synthetic/hand-drawn.
LAND: dict[str, list[tuple[float, float]]] = {
    "mainland": [
        (43.42, 6.70),
        (43.50, 6.95),
        (43.55, 7.01),
        (43.54, 7.13),
        (43.63, 7.19),
        (43.69, 7.29),
        (43.72, 7.35),
        (43.75, 7.42),
        (43.78, 7.53),
        (43.79, 7.61),
        (43.82, 7.78),
        (43.88, 8.03),
        (43.92, 8.15),
        (44.05, 8.23),
        (44.18, 8.40),
        (44.31, 8.48),
        (44.39, 8.77),
        (44.40, 8.93),
        (44.35, 9.10),
        (44.30, 9.21),
        (44.25, 9.38),
        (44.16, 9.65),
        (44.05, 9.83),
        (44.03, 9.99),
        (43.96, 10.13),
        (43.88, 10.15),
        (44.5, 10.15),
        (44.5, 6.70),
    ],
    "corsica": [
        (43.01, 9.36),
        (42.92, 9.34),
        (42.80, 9.33),
        (42.74, 9.29),
        (42.68, 9.30),
        (42.73, 9.21),
        (42.68, 9.05),
        (42.64, 8.94),
        (42.59, 8.83),
        (42.57, 8.75),
        (42.51, 8.66),
        (42.43, 8.60),
        (42.38, 8.55),
        (42.29, 8.57),
        (42.22, 8.55),
        (42.13, 8.60),
        (42.05, 8.68),
        (41.98, 8.63),
        (41.92, 8.74),
        (41.88, 8.70),
        (41.86, 8.61),
        (41.78, 8.68),
        (41.73, 8.78),
        (41.68, 8.90),
        (41.62, 8.85),
        (41.55, 8.79),
        (41.48, 8.90),
        (41.43, 9.02),
        (41.39, 9.10),
        (41.39, 9.16),
        (41.42, 9.22),
        (41.50, 9.27),
        (41.59, 9.33),
        (41.70, 9.40),
        (41.85, 9.41),
        (42.00, 9.49),
        (42.10, 9.51),
        (42.30, 9.54),
        (42.50, 9.53),
        (42.70, 9.45),
        (42.85, 9.47),
        (42.95, 9.46),
    ],
    "sardinia": [
        (41.24, 9.14),
        (41.18, 9.22),
        (41.16, 9.31),
        (41.13, 9.41),
        (41.15, 9.53),
        (41.11, 9.53),
        (41.05, 9.55),
        (41.00, 9.62),
        (40.95, 9.55),
        (40.92, 9.50),
        (40.88, 9.56),
        (40.80, 9.65),
        (40.75, 9.68),
        (40.75, 8.13),
        (40.84, 8.13),
        (40.93, 8.19),
        (40.96, 8.32),
        (40.92, 8.48),
        (40.92, 8.71),
        (40.99, 8.84),
        (41.02, 8.93),
        (41.10, 9.00),
        (41.17, 9.06),
    ],
    "asinara": [(41.03, 8.25), (41.08, 8.30), (41.12, 8.33), (41.09, 8.25), (41.05, 8.22)],
    "maddalena": [(41.20, 9.37), (41.23, 9.39), (41.24, 9.42), (41.215, 9.435), (41.19, 9.41)],
    "capraia": [(43.08, 9.83), (43.06, 9.86), (43.02, 9.85), (43.03, 9.81)],
    "elba_w": [(42.81, 10.10), (42.78, 10.15), (42.73, 10.15), (42.74, 10.08), (42.78, 10.06)],
    "pianosa": [(42.36, 10.08), (42.34, 10.10), (42.33, 10.06)],
}

# Marine-reserve routing exclusions (advisory no-go, not land) --
# SyntheticGeography only; RealGeography loads real (if not yet
# precisely-verified) zones from data/geography/nogo_western_med.json
# instead (ticket 0.8).
NOGO: list[dict] = [
    {
        "name": "Reserve de Scandola",
        "lat_min": 42.30,
        "lat_max": 42.42,
        "lon_min": 8.50,
        "lon_max": 8.65,
    },
    {
        "name": "Iles Lavezzi reserve",
        "lat_min": 41.29,
        "lat_max": 41.36,
        "lon_min": 9.21,
        "lon_max": 9.31,
    },
]

DEFAULT_NOGO_PATH = "data/geography/nogo_western_med.json"
DEFAULT_TSS_PATH = "data/geography/tss_western_med.json"


def _is_nogo_synthetic(lat: float, lon: float) -> bool:
    return any(
        b["lat_min"] <= lat <= b["lat_max"] and b["lon_min"] <= lon <= b["lon_max"] for b in NOGO
    )


def _load_nogo_polygons(*paths: str | Path) -> list[list[tuple[float, float]]]:
    """Loads zone polygons from one or more data files matching
    `data/geography/nogo_western_med.json`'s schema (a `"zones"` list,
    each with a `"polygon"` of `[lat, lon]` vertices) — used for both the
    no-go and TSS-separation-zone files, since they're the exact same
    hard-constraint mechanism (ticket 0.8's deliberate scope cut: a
    separation zone is a no-go, not a directional-lane rule)."""
    polygons: list[list[tuple[float, float]]] = []
    for path in paths:
        with open(path) as f:
            data = json.load(f)
        for zone in data["zones"]:
            polygons.append([(lat, lon) for lat, lon in zone["polygon"]])
    return polygons


def _point_in_polygon(lat: float, lon: float, poly: list[tuple[float, float]]) -> bool:
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        yi, xi = poly[i]
        yj, xj = poly[j]
        if ((yi > lat) != (yj > lat)) and (lon < (xj - xi) * (lat - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _segment_distance_nm(
    lat: float, lon: float, a: tuple[float, float], b: tuple[float, float], klon: float
) -> float:
    px, py = lon * 60.0 * klon, lat * 60.0
    ax, ay = a[1] * 60.0 * klon, a[0] * 60.0
    bx, by = b[1] * 60.0 * klon, b[0] * 60.0
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy or 1e-9
    u = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    return ((px - (ax + dx * u)) ** 2 + (py - (ay + dy * u)) ** 2) ** 0.5


class Geography(Protocol):
    def is_navigable(self, lat_deg: float, lon_deg: float) -> bool: ...
    def depth_m(self, lat_deg: float, lon_deg: float) -> float: ...


class SyntheticGeography:
    """Synthetic land/no-go/bathymetry, ported from the demo. Placeholder
    until ticket 0.3 (GSHHG coastline + GEBCO bathymetry)."""

    def __init__(self, ref_lat_deg: float = 42.3) -> None:
        self._klon = math.cos(math.radians(ref_lat_deg))

    def is_land(self, lat_deg: float, lon_deg: float) -> bool:
        return any(_point_in_polygon(lat_deg, lon_deg, poly) for poly in LAND.values())

    def is_nogo(self, lat_deg: float, lon_deg: float) -> bool:
        return _is_nogo_synthetic(lat_deg, lon_deg)

    def is_navigable(self, lat_deg: float, lon_deg: float) -> bool:
        return not (self.is_land(lat_deg, lon_deg) or self.is_nogo(lat_deg, lon_deg))

    def distance_to_land_nm(self, lat_deg: float, lon_deg: float) -> float:
        best = float("inf")
        for poly in LAND.values():
            n = len(poly)
            for i in range(n):
                a, b = poly[i], poly[(i + 1) % n]
                best = min(best, _segment_distance_nm(lat_deg, lon_deg, a, b, self._klon))
        return best

    def depth_m(self, lat_deg: float, lon_deg: float) -> float:
        """Synthetic shelf-slope approximation, capped by regional basin
        depths — provisional, replaced by real GEBCO bathymetry in 0.3."""
        shelf = self.distance_to_land_nm(lat_deg, lon_deg) ** 1.25 * 95.0
        cap = 2600.0
        if 41.15 < lat_deg < 41.55 and 8.80 < lon_deg < 9.80:
            cap = 110.0  # Strait of Bonifacio sill
        elif lat_deg > 42.4 and lon_deg > 9.4:
            cap = 480.0  # Corsican channel / Tuscan shelf
        elif lat_deg <= 42.4 and lon_deg > 9.6:
            cap = 1250.0  # north Tyrrhenian
        return min(cap, max(12.0, shelf))


DEFAULT_COASTLINE_PATH = "data/geography/coastline_western_med.json"
DEFAULT_BATHYMETRY_PATH = "data/geography/bathymetry_western_med.npz"


class RealGeography:
    """GSHHG coastline + GEBCO_2024 bathymetry over the western-Med corridor
    bbox (ticket 0.3). Loads committed, pre-cropped data files produced by
    `ingest/fetch_gshhg.py`/`ingest/fetch_gebco.py`/`ingest/fetch_nogo_polygons.py`
    — no network, no shapefile/netCDF parsing at runtime, just `json` +
    `numpy`.

    No-go + TSS separation zones (ticket 0.8) are real (if not yet
    precisely-verified — see each zone's `precise_boundary_verified` flag
    in the data files) polygons loaded from `nogo_path`/`tss_path`, not
    the synthetic `NOGO` boxes `SyntheticGeography` still uses.
    """

    def __init__(
        self,
        coastline_path: str | Path = DEFAULT_COASTLINE_PATH,
        bathymetry_path: str | Path = DEFAULT_BATHYMETRY_PATH,
        nogo_path: str | Path = DEFAULT_NOGO_PATH,
        tss_path: str | Path = DEFAULT_TSS_PATH,
    ) -> None:
        with open(coastline_path) as f:
            coastline = json.load(f)
        self._land_polygons: list[list[tuple[float, float]]] = [
            [(lat, lon) for lat, lon in poly] for poly in coastline["polygons"]
        ]
        self._nogo_polygons = _load_nogo_polygons(nogo_path, tss_path)

        grid = np.load(bathymetry_path)
        self._lat0 = float(grid["lat0"])
        self._dlat = float(grid["dlat"])
        self._lon0 = float(grid["lon0"])
        self._dlon = float(grid["dlon"])
        self._nlat = int(grid["nlat"])
        self._nlon = int(grid["nlon"])
        self._elevation_m = grid["elevation_m"]
        self._land_mask = grid["land_mask"] if "land_mask" in grid else None

    def _grid_fracs(self, lat_deg: float, lon_deg: float) -> tuple[float, float]:
        return grid_fracs(
            lat_deg, lon_deg, self._lat0, self._dlat, self._lon0, self._dlon, self._nlat, self._nlon
        )

    def _check_in_bounds(self, lat_deg: float, lon_deg: float) -> None:
        """Bounds derived from *this instance's own loaded grid*
        (`self._lat0`/`_dlat`/`_nlat`/`_lon0`/`_dlon`/`_nlon`, already
        computed in `__init__` from whatever `bathymetry_path` was
        loaded) — not the module-level `OPERATING_AREA_BBOX` constant.
        Found during ticket B7 planning: a `RealGeography` instance
        pointed at a *different* bbox's data files (Part 1, track-driven
        ingest) would otherwise validate against the wrong (western-Med)
        bounds regardless of what it actually loaded. `core/lattice.py`
        deliberately still hard-imports `OPERATING_AREA_BBOX` directly for
        lattice-clipping — a separate, untouched concern (routing/lattice
        feature freeze) — this bound method only affects `RealGeography`'s
        own point queries.

        Extended by half a grid step in each direction: `lat0_deg`/
        `lon0_deg` are the *first sample's* coordinate, not the covered
        area's edge — GEBCO/GSHHG ingest crops to a request bbox whose
        edge sits half a cell outside the first/last sample point
        (confirmed against the committed western-Med data:
        `lat0 - dlat/2 == OPERATING_AREA_BBOX`'s `lat_min` exactly). Also
        matches `core.gridding.grid_fracs`'s own clamping, which already
        tolerates queries into the outer half of the boundary cells."""
        lat_edge = self._lat0 + self._dlat * (self._nlat - 1)
        lon_edge = self._lon0 + self._dlon * (self._nlon - 1)
        half_dlat, half_dlon = abs(self._dlat) / 2, abs(self._dlon) / 2
        lat_min, lat_max = sorted((self._lat0 - half_dlat, lat_edge + half_dlat))
        lon_min, lon_max = sorted((self._lon0 - half_dlon, lon_edge + half_dlon))
        if not (lat_min <= lat_deg <= lat_max and lon_min <= lon_deg <= lon_max):
            raise OutOfOperatingAreaError(
                f"({lat_deg}, {lon_deg}) is outside this RealGeography instance's "
                f"loaded bounds (lat {lat_min}..{lat_max}, lon {lon_min}..{lon_max}) — "
                "no real GSHHG/GEBCO data covers this point"
            )

    def is_land(self, lat_deg: float, lon_deg: float) -> bool:
        """Rasterised lookup (fast hot path) — see `is_land_precise` for the
        GSHHG polygon ray-cast this raster was generated from."""
        self._check_in_bounds(lat_deg, lon_deg)
        if self._land_mask is None:
            return self.is_land_precise(lat_deg, lon_deg)
        fy, fx = self._grid_fracs(lat_deg, lon_deg)
        return bool(nearest(self._land_mask, fy, fx))

    def is_land_precise(self, lat_deg: float, lon_deg: float) -> bool:
        """GSHHG polygon ray-cast — precise, not the hot path. What
        `is_land`'s raster is generated from and tested against."""
        return any(_point_in_polygon(lat_deg, lon_deg, poly) for poly in self._land_polygons)

    def is_nogo(self, lat_deg: float, lon_deg: float) -> bool:
        return any(_point_in_polygon(lat_deg, lon_deg, poly) for poly in self._nogo_polygons)

    def is_navigable(self, lat_deg: float, lon_deg: float) -> bool:
        return not (self.is_land(lat_deg, lon_deg) or self.is_nogo(lat_deg, lon_deg))

    def depth_m(self, lat_deg: float, lon_deg: float) -> float:
        """GEBCO elevation is negative for depth, positive for land/above
        sea level; a positive-elevation grid cell (land, per the real
        dataset) reads as zero depth rather than a negative number."""
        self._check_in_bounds(lat_deg, lon_deg)
        fy, fx = self._grid_fracs(lat_deg, lon_deg)
        elevation_m = bilinear(self._elevation_m, fy, fx)
        return max(0.0, -float(elevation_m))
