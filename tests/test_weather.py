import math

import numpy as np
import pytest

from core.weather import GriddedWeatherField, SyntheticWeatherField


@pytest.mark.parametrize("scenario", ["mistral", "calm", "easterly"])
def test_synthetic_scenarios_return_sane_ranges(scenario):
    wx = SyntheticWeatherField(scenario)
    s = wx.sample(42.0, 8.5, 5.0)
    assert s.hs_m >= 0
    assert s.period_peak_s > 0
    assert s.period_mean_s > 0
    assert s.current_u_ms == 0.0
    assert s.current_v_ms == 0.0
    assert not s.is_missing


def test_unknown_scenario_raises():
    with pytest.raises(ValueError):
        SyntheticWeatherField("hurricane")


def _uniform_grid(nt, nlat, nlon, value):
    return np.full((nt, nlat, nlon), value, dtype=float)


def test_gridded_interior_point_blends_valid_neighbours():
    nlat, nlon = 3, 3
    hs = np.array(
        [
            [
                [1.0, 1.2, 1.4],
                [1.1, 1.3, np.nan],
                [1.2, 1.4, 1.6],
            ]
        ]
    )
    other = _uniform_grid(1, nlat, nlon, 6.0)
    wave_dir = _uniform_grid(1, nlat, nlon, 0.0)
    zeros = _uniform_grid(1, nlat, nlon, 0.0)
    field = GriddedWeatherField(
        lat0_deg=41.0,
        dlat_deg=1.0,
        lon0_deg=8.0,
        dlon_deg=1.0,
        hours=[0.0],
        hs_m=hs,
        period_peak_s=other,
        period_mean_s=other,
        wave_from_deg=wave_dir,
        wind_u_ms=zeros,
        wind_v_ms=zeros,
        current_u_ms=zeros,
        current_v_ms=zeros,
    )
    s = field.sample(41.5, 8.5, 0.0)
    assert not s.is_missing
    assert s.hs_m == pytest.approx((1.0 + 1.1 + 1.2 + 1.3) / 4, abs=1e-9)


def test_gridded_land_adjacent_point_is_missing_not_calm():
    nlat, nlon = 3, 3
    hs = np.array(
        [
            [
                [1.0, 1.2, 1.4],
                [1.1, 1.3, np.nan],
                [1.2, 1.4, 1.6],
            ]
        ]
    )
    other = _uniform_grid(1, nlat, nlon, 6.0)
    wave_dir = _uniform_grid(1, nlat, nlon, 0.0)
    zeros = _uniform_grid(1, nlat, nlon, 0.0)
    field = GriddedWeatherField(
        lat0_deg=41.0,
        dlat_deg=1.0,
        lon0_deg=8.0,
        dlon_deg=1.0,
        hours=[0.0],
        hs_m=hs,
        period_peak_s=other,
        period_mean_s=other,
        wave_from_deg=wave_dir,
        wind_u_ms=zeros,
        wind_v_ms=zeros,
        current_u_ms=zeros,
        current_v_ms=zeros,
    )
    s = field.sample(42.5, 9.5, 0.0)
    assert s.is_missing
    assert math.isnan(s.hs_m)
    # the dangerous bug this guards against: land silently reading as calm (hs=0)
    assert s.hs_m != 0.0


def test_gridded_direction_interpolates_via_vectors_across_wrap():
    hs = _uniform_grid(1, 2, 2, 2.0)
    other = _uniform_grid(1, 2, 2, 6.0)
    zeros = _uniform_grid(1, 2, 2, 0.0)
    wave_from = np.array([[[350.0, 10.0], [350.0, 10.0]]])
    field = GriddedWeatherField(
        lat0_deg=42.0,
        dlat_deg=1.0,
        lon0_deg=8.0,
        dlon_deg=1.0,
        hours=[0.0],
        hs_m=hs,
        period_peak_s=other,
        period_mean_s=other,
        wave_from_deg=wave_from,
        wind_u_ms=zeros,
        wind_v_ms=zeros,
        current_u_ms=zeros,
        current_v_ms=zeros,
    )
    s = field.sample(42.5, 8.5, 0.0)
    # vector average of 350 deg and 10 deg should land near 0 deg, NOT the
    # naive-degree-average wrong answer of 180 deg.
    diff = (s.wave_from_deg - 0.0 + 180) % 360 - 180
    assert abs(diff) < 1.0


def test_gridded_time_interpolation_is_linear():
    hs_t0 = _uniform_grid(1, 2, 2, 1.0)[0]
    hs_t1 = _uniform_grid(1, 2, 2, 3.0)[0]
    hs = np.stack([hs_t0, hs_t1])
    other = _uniform_grid(2, 2, 2, 6.0)
    wave_dir = _uniform_grid(2, 2, 2, 0.0)
    zeros = _uniform_grid(2, 2, 2, 0.0)
    field = GriddedWeatherField(
        lat0_deg=42.0,
        dlat_deg=1.0,
        lon0_deg=8.0,
        dlon_deg=1.0,
        hours=[0.0, 2.0],
        hs_m=hs,
        period_peak_s=other,
        period_mean_s=other,
        wave_from_deg=wave_dir,
        wind_u_ms=zeros,
        wind_v_ms=zeros,
        current_u_ms=zeros,
        current_v_ms=zeros,
    )
    s = field.sample(42.0, 8.0, 1.0)
    assert s.hs_m == pytest.approx(2.0)
