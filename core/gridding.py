"""Shared regular lat/lon grid interpolation primitives.

Used by `core/weather.py`'s `GriddedWeatherField` and `core/geography.py`'s
`RealGeography` bathymetry lookup — both are "sample a bilinear value off a
regular lat/lon grid" problems, so the interpolation math lives in one
place rather than being duplicated per grid type.
"""

from __future__ import annotations

import math

import numpy as np


def grid_fracs(
    lat_deg: float,
    lon_deg: float,
    lat0_deg: float,
    dlat_deg: float,
    lon0_deg: float,
    dlon_deg: float,
    nlat: int,
    nlon: int,
) -> tuple[float, float]:
    """Fractional (row, col) index of (lat_deg, lon_deg) into a regular grid
    with `nlat` rows / `nlon` cols starting at (lat0_deg, lon0_deg) with
    step (dlat_deg, dlon_deg). Clamped to the grid so `bilinear` always has
    a valid four-corner stencil (off-grid queries clamp to the edge)."""
    fy = (lat_deg - lat0_deg) / dlat_deg
    fx = (lon_deg - lon0_deg) / dlon_deg
    fy = max(0.0, min(nlat - 1.001, fy))
    fx = max(0.0, min(nlon - 1.001, fx))
    return fy, fx


def bilinear(grid_2d: np.ndarray, fy: float, fx: float) -> float:
    """Bilinear value at fractional (row, col) = (fy, fx). NaN in any of the
    four corners propagates to the result (land-as-missing, B2) rather than
    silently blending in a neighbouring value."""
    y0, x0 = int(math.floor(fy)), int(math.floor(fx))
    wy, wx = fy - y0, fx - x0
    y1 = min(y0 + 1, grid_2d.shape[0] - 1)
    x1 = min(x0 + 1, grid_2d.shape[1] - 1)
    v00, v10 = grid_2d[y0, x0], grid_2d[y1, x0]
    v01, v11 = grid_2d[y0, x1], grid_2d[y1, x1]
    return v00 * (1 - wy) * (1 - wx) + v10 * wy * (1 - wx) + v01 * (1 - wy) * wx + v11 * wy * wx


def bilinear_masked(grid_2d: np.ndarray, fy: float, fx: float) -> float:
    """Bilinear value at fractional (row, col) = (fy, fx), renormalising
    the weights over whichever corners aren't NaN rather than propagating
    any single NaN corner to a fully-missing result the way plain
    `bilinear` does.

    Plain `bilinear`'s any-corner-NaN-propagates behaviour is too
    conservative for a land-masked weather grid (core/weather.py, B2): a
    query point just offshore — an anchorage approach, say — sits in a
    stencil with at least one land (NaN) corner far more often than not,
    and treating that as "no data" would make near-shore sampling report
    missing almost everywhere anchorage routing actually needs a value.
    Only returns NaN when *every* corner is NaN (genuinely no nearby data,
    e.g. a query deep inside a landmass)."""
    y0, x0 = int(math.floor(fy)), int(math.floor(fx))
    wy, wx = fy - y0, fx - x0
    y1 = min(y0 + 1, grid_2d.shape[0] - 1)
    x1 = min(x0 + 1, grid_2d.shape[1] - 1)
    corners = (
        (grid_2d[y0, x0], (1 - wy) * (1 - wx)),
        (grid_2d[y1, x0], wy * (1 - wx)),
        (grid_2d[y0, x1], (1 - wy) * wx),
        (grid_2d[y1, x1], wy * wx),
    )
    valid = [(float(v), w) for v, w in corners if not math.isnan(v)]
    if not valid:
        return float("nan")
    total_w = sum(w for _, w in valid)
    if total_w <= 1e-12:
        return valid[0][0]
    return sum(v * w for v, w in valid) / total_w


def nearest(grid_2d: np.ndarray, fy: float, fx: float):
    """Nearest-cell value at fractional (row, col) = (fy, fx) — for grids
    where the value isn't a quantity to smooth (e.g. a land/water mask;
    bilinear-blending a boolean makes no sense at the boundary)."""
    y = min(grid_2d.shape[0] - 1, int(round(fy)))
    x = min(grid_2d.shape[1] - 1, int(round(fx)))
    return grid_2d[y, x]
