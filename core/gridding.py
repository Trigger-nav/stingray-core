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
