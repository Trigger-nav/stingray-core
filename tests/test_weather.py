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


def test_gridded_land_adjacent_point_blends_valid_neighbours_not_missing():
    """B6 follow-up (ticket 0.5): a point just offshore of a single land
    (NaN) cell -- an anchorage approach is exactly this shape -- must still
    get a real wave estimate from its valid neighbours, not read as
    missing just because one stencil corner is land. This used to assert
    the opposite (`is_missing`); that was too conservative for anchorage
    routing to depend on -- see `bilinear_masked` (core/gridding.py) and
    `GriddedWeatherField`'s docstring."""
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
    # (42.5, 9.5) -> fy=fx=1.5: stencil corners are hs[1,1]=1.3, hs[2,1]=1.4,
    # hs[1,2]=nan (land), hs[2,2]=1.6 -- three valid, one land.
    s = field.sample(42.5, 9.5, 0.0)
    assert not s.is_missing
    assert s.hs_m == pytest.approx((1.3 + 1.4 + 1.6) / 3)


def test_gridded_point_fully_surrounded_by_land_is_missing():
    """The genuine "no data nearby" case -- every stencil corner is land --
    must still read as missing, not silently fall back to some default."""
    nlat, nlon = 3, 3
    hs = np.array(
        [
            [
                [1.0, 1.2, 1.4],
                [1.1, np.nan, np.nan],
                [1.2, np.nan, np.nan],
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
    # (42.5, 9.5) -> fy=fx=1.5: all four corners (hs[1,1], hs[2,1], hs[1,2],
    # hs[2,2]) are nan at both time steps (only one hour in this fixture).
    s = field.sample(42.5, 9.5, 0.0)
    assert s.is_missing


def test_gridded_wind_is_never_land_masked():
    """Wind isn't land-masked at ingest (ticket 0.5 amendment) -- an
    over-land model wind value is a real output, not a hardcoded-calm
    artefact -- so sampling right next to (or over) a "land" wave cell
    must still return a real wind value even where wave reads missing."""
    nlat, nlon = 3, 3
    hs = _uniform_grid(1, nlat, nlon, np.nan)  # entire wave field "land"
    other = _uniform_grid(1, nlat, nlon, 6.0)
    wave_dir = _uniform_grid(1, nlat, nlon, 0.0)
    wind_u = _uniform_grid(1, nlat, nlon, 3.0)
    wind_v = _uniform_grid(1, nlat, nlon, -1.0)
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
        wind_u_ms=wind_u,
        wind_v_ms=wind_v,
        current_u_ms=zeros,
        current_v_ms=zeros,
    )
    s = field.sample(42.5, 9.5, 0.0)
    assert s.is_missing  # wave: correctly missing, no nearby water data
    assert s.wind_u_ms == pytest.approx(3.0)  # wind: unaffected
    assert s.wind_v_ms == pytest.approx(-1.0)
    assert math.isnan(s.hs_m)
    # the dangerous bug this guards against: land silently reading as calm (hs=0)
    assert s.hs_m != 0.0


def test_gridded_current_land_adjacent_point_blends_valid_neighbours_not_missing():
    """Ticket C1: current, unlike wind but like wave, IS land-masked at
    ingest (a real ocean-current product has no over-land meaning) -- so
    it must use `bilinear_masked`, the same as wave, not propagate a
    single NaN stencil corner to a fully-missing result. Mirrors
    `test_gridded_land_adjacent_point_blends_valid_neighbours_not_missing`
    exactly, for current instead of wave."""
    nlat, nlon = 3, 3
    current_u = np.array(
        [
            [
                [1.0, 1.2, 1.4],
                [1.1, 1.3, np.nan],
                [1.2, 1.4, 1.6],
            ]
        ]
    )
    current_v = np.array(
        [
            [
                [0.1, 0.2, 0.3],
                [0.2, 0.3, np.nan],
                [0.3, 0.4, 0.5],
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
        hs_m=other,
        period_peak_s=other,
        period_mean_s=other,
        wave_from_deg=wave_dir,
        wind_u_ms=zeros,
        wind_v_ms=zeros,
        current_u_ms=current_u,
        current_v_ms=current_v,
    )
    # (42.5, 9.5) -> fy=fx=1.5: stencil corners are [1,1], [2,1], [1,2] (nan),
    # [2,2] -- three valid, one land, same shape as the wave-equivalent test.
    s = field.sample(42.5, 9.5, 0.0)
    assert not math.isnan(s.current_u_ms)
    assert s.current_u_ms == pytest.approx((1.3 + 1.4 + 1.6) / 3)
    assert not math.isnan(s.current_v_ms)
    assert s.current_v_ms == pytest.approx((0.3 + 0.4 + 0.5) / 3)


def test_gridded_current_fully_surrounded_by_land_is_missing():
    """The genuine "no data nearby" case for current -- mirrors
    `test_gridded_point_fully_surrounded_by_land_is_missing`."""
    nlat, nlon = 3, 3
    current_u = np.array(
        [
            [
                [1.0, 1.2, 1.4],
                [1.1, np.nan, np.nan],
                [1.2, np.nan, np.nan],
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
        hs_m=other,
        period_peak_s=other,
        period_mean_s=other,
        wave_from_deg=wave_dir,
        wind_u_ms=zeros,
        wind_v_ms=zeros,
        current_u_ms=current_u,
        current_v_ms=zeros,
    )
    s = field.sample(42.5, 9.5, 0.0)
    assert math.isnan(s.current_u_ms)


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


def test_gridded_weather_field_from_npz_round_trips(tmp_path):
    """Mirrors ingest's npz schema (ticket 0.5) -- `from_npz` must load
    exactly what an ingest script would have written, including the
    provenance fields."""
    nlat, nlon = 2, 2
    hs = _uniform_grid(1, nlat, nlon, 1.5)
    other = _uniform_grid(1, nlat, nlon, 6.0)
    wave_dir = _uniform_grid(1, nlat, nlon, 45.0)
    wind_u = _uniform_grid(1, nlat, nlon, 2.0)
    wind_v = _uniform_grid(1, nlat, nlon, 1.0)
    zeros = _uniform_grid(1, nlat, nlon, 0.0)

    npz_path = tmp_path / "test_western_med.npz"
    with open(npz_path, "wb") as f:
        np.savez_compressed(
            f,
            lat0=41.0,
            dlat=1.0,
            lon0=8.0,
            dlon=1.0,
            hours=np.array([0.0]),
            hs_m=hs,
            period_peak_s=other,
            period_mean_s=other,
            wave_from_deg=wave_dir,
            wind_u_ms=wind_u,
            wind_v_ms=wind_v,
            current_u_ms=zeros,
            current_v_ms=zeros,
            cycle="20260707_00z",
            fetched="2026-07-07T00:12:00+00:00",
            source="test fixture",
        )

    field = GriddedWeatherField.from_npz(npz_path)
    assert field.cycle == "20260707_00z"
    assert field.fetched == "2026-07-07T00:12:00+00:00"
    assert field.source == "test fixture"

    s = field.sample(41.5, 8.5, 0.0)
    assert not s.is_missing
    assert s.hs_m == pytest.approx(1.5)
    assert s.wind_u_ms == pytest.approx(2.0)
    assert s.wind_v_ms == pytest.approx(1.0)
    assert s.wave_from_deg == pytest.approx(45.0)


def test_gridded_weather_field_from_npz_tolerates_pre_c1_schema_missing_current_provenance(
    tmp_path,
):
    """Ticket C1 minor flag: an npz written before this ticket (or by an
    older fetch_grib_*.py mid-upgrade -- a real, not just theoretical,
    mixed-version scenario for a cloud/vessel pair) has none of
    current_cycle/current_fetched/current_source at all. `from_npz` must
    load it as "not modelled" (None), not raise KeyError. This fixture
    (deliberately identical in shape to
    test_gridded_weather_field_from_npz_round_trips above) IS exactly
    that pre-C1 schema."""
    nlat, nlon = 2, 2
    grid = _uniform_grid(1, nlat, nlon, 1.0)
    zeros = _uniform_grid(1, nlat, nlon, 0.0)
    npz_path = tmp_path / "pre_c1.npz"
    with open(npz_path, "wb") as f:
        np.savez_compressed(
            f,
            lat0=41.0,
            dlat=1.0,
            lon0=8.0,
            dlon=1.0,
            hours=np.array([0.0]),
            hs_m=grid,
            period_peak_s=grid,
            period_mean_s=grid,
            wave_from_deg=zeros,
            wind_u_ms=zeros,
            wind_v_ms=zeros,
            current_u_ms=zeros,
            current_v_ms=zeros,
            cycle="20260707_00z",
            fetched="2026-07-07T00:12:00+00:00",
            source="test fixture",
            # deliberately no current_cycle/current_fetched/current_source
        )

    field = GriddedWeatherField.from_npz(npz_path)
    assert field.current_cycle is None
    assert field.current_fetched is None
    assert field.current_source is None
    # the pre-existing wind/wave provenance is unaffected by the new fields' absence.
    assert field.cycle == "20260707_00z"


def test_gridded_weather_field_from_npz_round_trips_current_provenance(tmp_path):
    """The post-C1-merge schema -- current_cycle/current_fetched/
    current_source populated (ingest/merge_currents.py always writes
    all three when it runs) -- must round-trip distinctly from the
    wind/wave triple."""
    nlat, nlon = 2, 2
    grid = _uniform_grid(1, nlat, nlon, 1.0)
    current = _uniform_grid(1, nlat, nlon, 0.3)
    npz_path = tmp_path / "post_c1.npz"
    with open(npz_path, "wb") as f:
        np.savez_compressed(
            f,
            lat0=41.0,
            dlat=1.0,
            lon0=8.0,
            dlon=1.0,
            hours=np.array([0.0]),
            hs_m=grid,
            period_peak_s=grid,
            period_mean_s=grid,
            wave_from_deg=grid,
            wind_u_ms=grid,
            wind_v_ms=grid,
            current_u_ms=current,
            current_v_ms=current,
            cycle="20260707_00z",
            fetched="2026-07-07T00:12:00+00:00",
            source="nomads",
            current_cycle="20260707_cmems",
            current_fetched="2026-07-07T01:00:00+00:00",
            current_source="cmems_mod_nws_phy-cur_anfc_1.5km-2D_PT1H-i",
        )

    field = GriddedWeatherField.from_npz(npz_path)
    assert field.source == "nomads"
    assert field.current_source == "cmems_mod_nws_phy-cur_anfc_1.5km-2D_PT1H-i"
    assert field.current_cycle == "20260707_cmems"
    assert field.current_fetched == "2026-07-07T01:00:00+00:00"
