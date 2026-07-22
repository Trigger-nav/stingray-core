"""ingest/merge_currents.py (ticket C1) -- pure resampling logic
(`resample_currents`) plus a full on-disk round trip through `main()`'s
own npz-loading path. All fixtures are small, fabricated arrays with
known analytic values -- never real shipped data, per the no-invented-
numbers constraint.
"""

from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime

import numpy as np
import pytest

import ingest.merge_currents as merge_currents
from ingest.merge_currents import (
    MAX_HOLD_NEAREST_GAP_H,
    CurrentsCoverageError,
    _parse_cycle_to_utc,
    resample_currents,
)


def test_parse_cycle_to_utc():
    assert _parse_cycle_to_utc("20260714_06z") == datetime(2026, 7, 14, 6, tzinfo=UTC)


WEATHER_CYCLE_START = datetime(2026, 7, 14, 0, tzinfo=UTC)
WEATHER_HOURS = np.array([0.0, 1.0, 2.0, 3.0])
WEATHER_LATS = np.array([41.0, 42.0])
WEATHER_LONS = np.array([8.0, 9.0])


def _currents_grid(
    times_h_from_cycle_start: np.ndarray, value_u: float = 1.0, value_v: float = 0.5
):
    """Currents-only grid on the same 2x2 lat/lon as the weather fixture,
    at the given hours-from-weather-cycle-start (converted to absolute
    epoch), each timestep filled with a known constant."""
    times_epoch = WEATHER_CYCLE_START.timestamp() + times_h_from_cycle_start * 3600.0
    n_t = len(times_epoch)
    current_u = np.full((n_t, 2, 2), value_u)
    current_v = np.full((n_t, 2, 2), value_v)
    return times_epoch, current_u, current_v


def test_resample_currents_full_coverage_no_gap_no_warning(caplog):
    # currents span -1h to 4h -- fully covers the weather npz's 0..3h range.
    times_epoch, current_u, current_v = _currents_grid(np.array([-1.0, 0.0, 1.0, 2.0, 3.0, 4.0]))
    with caplog.at_level(logging.WARNING):
        u, v, note = resample_currents(
            WEATHER_HOURS,
            WEATHER_CYCLE_START,
            times_epoch,
            current_u,
            current_v,
            WEATHER_LATS,
            WEATHER_LONS,
            currents_lat0=41.0,
            currents_dlat=1.0,
            currents_lon0=8.0,
            currents_dlon=1.0,
            pack_id="test",
        )
    assert note == ""
    assert not caplog.records
    assert u.shape == (4, 2, 2)
    assert np.all(u == pytest.approx(1.0))
    assert np.all(v == pytest.approx(0.5))


def test_resample_currents_within_gap_holds_nearest_and_warns(caplog):
    # currents span 2h to 5h -- missing the first 2h of the weather npz's
    # 0..3h range (within the 6h tolerance) at the start, no gap at the end.
    times_epoch, current_u, current_v = _currents_grid(np.array([2.0, 3.0, 4.0, 5.0]))
    with caplog.at_level(logging.WARNING):
        u, v, note = resample_currents(
            WEATHER_HOURS,
            WEATHER_CYCLE_START,
            times_epoch,
            current_u,
            current_v,
            WEATHER_LATS,
            WEATHER_LONS,
            currents_lat0=41.0,
            currents_dlat=1.0,
            currents_lon0=8.0,
            currents_dlon=1.0,
            pack_id="test-pack",
        )
    assert "held-nearest" in note
    assert "start" in note
    assert any("test-pack" in r.message and "start" in r.message for r in caplog.records)
    # weather hour 0.0 and 1.0 both fall before the currents data's own
    # earliest real sample (hour 2.0) -- held at the nearest (constant
    # value here, so trivially equal; the real assertion is that it's a
    # real number, not NaN).
    assert not np.any(np.isnan(u))
    assert np.all(u == pytest.approx(1.0))


def test_resample_currents_beyond_gap_raises_and_writes_nothing():
    # currents span 10h to 13h -- entirely outside the weather npz's 0..3h
    # range, an 10h gap at the start, far beyond the 6h tolerance.
    times_epoch, current_u, current_v = _currents_grid(np.array([10.0, 11.0, 12.0, 13.0]))
    with pytest.raises(CurrentsCoverageError, match="exceeds the"):
        resample_currents(
            WEATHER_HOURS,
            WEATHER_CYCLE_START,
            times_epoch,
            current_u,
            current_v,
            WEATHER_LATS,
            WEATHER_LONS,
            currents_lat0=41.0,
            currents_dlat=1.0,
            currents_lon0=8.0,
            currents_dlon=1.0,
            pack_id="test",
        )


def test_resample_currents_gap_exactly_at_tolerance_boundary_holds_nearest():
    gap_h = MAX_HOLD_NEAREST_GAP_H
    times_epoch, current_u, current_v = _currents_grid(np.array([gap_h, gap_h + 1.0, gap_h + 2.0]))
    u, v, note = resample_currents(
        WEATHER_HOURS,
        WEATHER_CYCLE_START,
        times_epoch,
        current_u,
        current_v,
        WEATHER_LATS,
        WEATHER_LONS,
        currents_lat0=41.0,
        currents_dlat=1.0,
        currents_lon0=8.0,
        currents_dlon=1.0,
    )
    assert "held-nearest" in note
    assert not np.any(np.isnan(u))


def test_resample_currents_interpolates_between_real_samples():
    # a genuine mid-range interpolation, not just a constant-value sanity
    # check: currents_u ramps 0.0 -> 3.0 over hours 0..3, weather asks for
    # hour 1.5 (between real samples) -> should read ~1.5.
    times_epoch = WEATHER_CYCLE_START.timestamp() + np.array([0.0, 1.0, 2.0, 3.0]) * 3600.0
    current_u = np.zeros((4, 2, 2))
    for t in range(4):
        current_u[t] = float(t)
    current_v = np.zeros((4, 2, 2))
    weather_hours = np.array([1.5])
    u, _v, note = resample_currents(
        weather_hours,
        WEATHER_CYCLE_START,
        times_epoch,
        current_u,
        current_v,
        WEATHER_LATS,
        WEATHER_LONS,
        currents_lat0=41.0,
        currents_dlat=1.0,
        currents_lon0=8.0,
        currents_dlon=1.0,
    )
    assert note == ""
    assert u[0, 0, 0] == pytest.approx(1.5)


def _write_fabricated_weather_npz(path):
    nlat, nlon, n_hours = 2, 2, 4
    grid = np.full((n_hours, nlat, nlon), 1.5)
    zeros = np.zeros((n_hours, nlat, nlon))
    np.savez_compressed(
        path,
        lat0=41.0,
        dlat=1.0,
        lon0=8.0,
        dlon=1.0,
        hours=np.array([0.0, 1.0, 2.0, 3.0]),
        hs_m=grid,
        period_peak_s=grid,
        period_mean_s=grid,
        wave_from_deg=zeros,
        wind_u_ms=grid,
        wind_v_ms=grid,
        current_u_ms=zeros,  # pre-merge: still zero, exactly today's shape
        current_v_ms=zeros,
        cycle="20260714_00z",
        fetched="2026-07-14T00:30:00+00:00",
        source="nomads",
    )


def _write_fabricated_currents_npz(path):
    hours = np.array([-1.0, 0.0, 1.0, 2.0, 3.0, 4.0])
    times_epoch = WEATHER_CYCLE_START.timestamp() + hours * 3600.0
    current_u = np.full((6, 2, 2), 0.7)
    current_v = np.full((6, 2, 2), -0.3)
    np.savez_compressed(
        path,
        lat0=41.0,
        dlat=1.0,
        lon0=8.0,
        dlon=1.0,
        times=times_epoch,
        current_u_ms=current_u,
        current_v_ms=current_v,
        fetched="2026-07-14T01:00:00+00:00",
        source="cmems_mod_nws_phy-cur_anfc_1.5km-2D_PT1H-i",
    )


def test_main_merges_currents_into_weather_npz_in_place(tmp_path):
    weather_path = tmp_path / "weather_uk_sw.npz"
    currents_path = tmp_path / "currents_uk_sw.npz"
    _write_fabricated_weather_npz(weather_path)
    _write_fabricated_currents_npz(currents_path)

    sys.argv = [
        "merge_currents",
        "--weather-npz",
        str(weather_path),
        "--currents-npz",
        str(currents_path),
        "--pack-id",
        "uk_sw",
    ]
    merge_currents.main()

    merged = np.load(weather_path, allow_pickle=False)
    # current_u_ms/current_v_ms are no longer zero.
    assert np.all(merged["current_u_ms"] == pytest.approx(0.7))
    assert np.all(merged["current_v_ms"] == pytest.approx(-0.3))
    # every other field survives untouched.
    assert np.all(merged["hs_m"] == pytest.approx(1.5))
    assert str(merged["cycle"]) == "20260714_00z"
    assert str(merged["source"]) == "nomads"
    # new provenance fields, distinct from the wind/wave source's own.
    assert str(merged["current_source"]) == "cmems_mod_nws_phy-cur_anfc_1.5km-2D_PT1H-i"
    assert str(merged["current_cycle"]) == "20260714_cmems"
    assert str(merged["current_fetched"]) == "2026-07-14T01:00:00+00:00"


def test_main_beyond_gap_raises_and_leaves_weather_npz_untouched(tmp_path):
    weather_path = tmp_path / "weather_uk_sw.npz"
    currents_path = tmp_path / "currents_uk_sw.npz"
    _write_fabricated_weather_npz(weather_path)
    # currents span hour 20-23 -- nowhere near the weather npz's 0..3h.
    times_epoch = WEATHER_CYCLE_START.timestamp() + np.array([20.0, 21.0, 22.0, 23.0]) * 3600.0
    np.savez_compressed(
        currents_path,
        lat0=41.0,
        dlat=1.0,
        lon0=8.0,
        dlon=1.0,
        times=times_epoch,
        current_u_ms=np.full((4, 2, 2), 0.7),
        current_v_ms=np.full((4, 2, 2), -0.3),
        fetched="2026-07-14T01:00:00+00:00",
        source="cmems_mod_nws_phy-cur_anfc_1.5km-2D_PT1H-i",
    )
    original_bytes = weather_path.read_bytes()

    sys.argv = [
        "merge_currents",
        "--weather-npz",
        str(weather_path),
        "--currents-npz",
        str(currents_path),
    ]
    with pytest.raises(CurrentsCoverageError):
        merge_currents.main()

    # the weather npz is completely untouched -- no partial/tmp write survives.
    assert weather_path.read_bytes() == original_bytes
    assert not (tmp_path / "weather_uk_sw.npz.tmp").exists()
