import math

import numpy as np
import pytest

from core.gridding import bilinear, grid_fracs


def test_grid_fracs_interior_point():
    fy, fx = grid_fracs(
        41.5, 8.5, lat0_deg=41.0, dlat_deg=1.0, lon0_deg=8.0, dlon_deg=1.0, nlat=3, nlon=3
    )
    assert fy == pytest.approx(0.5)
    assert fx == pytest.approx(0.5)


def test_grid_fracs_clamps_to_grid_edges():
    fy, fx = grid_fracs(
        100.0, -100.0, lat0_deg=41.0, dlat_deg=1.0, lon0_deg=8.0, dlon_deg=1.0, nlat=3, nlon=3
    )
    assert 0.0 <= fy <= 2.0
    assert 0.0 <= fx <= 2.0
    # lat way below lat0 -> clamps low; lon way above the grid's upper bound -> clamps high
    fy2, fx2 = grid_fracs(
        -100.0, 100.0, lat0_deg=41.0, dlat_deg=1.0, lon0_deg=8.0, dlon_deg=1.0, nlat=3, nlon=3
    )
    assert fy2 == pytest.approx(0.0)
    assert fx2 == pytest.approx(3 - 1.001)


def test_bilinear_exact_corner_returns_corner_value():
    grid = np.array([[1.0, 2.0], [3.0, 4.0]])
    assert bilinear(grid, 0.0, 0.0) == pytest.approx(1.0)
    assert bilinear(grid, 1.0, 0.0) == pytest.approx(3.0)
    assert bilinear(grid, 0.0, 1.0) == pytest.approx(2.0)


def test_bilinear_interior_point_averages_four_corners():
    grid = np.array([[1.0, 2.0], [3.0, 4.0]])
    assert bilinear(grid, 0.5, 0.5) == pytest.approx((1.0 + 2.0 + 3.0 + 4.0) / 4)


def test_bilinear_nan_corner_propagates():
    grid = np.array([[1.0, 2.0], [3.0, np.nan]])
    assert math.isnan(bilinear(grid, 0.5, 0.5))
    # exactly on the one valid corner still touches the NaN corner's zero-weight
    # term (0 * nan = nan in IEEE754) -- conservative by design, see core/weather.py.
    assert math.isnan(bilinear(grid, 0.0, 0.0))
