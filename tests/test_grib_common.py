import logging
import math
import urllib.error
from datetime import UTC, datetime

import numpy as np
import pytest
import xarray as xr

from core.units import LatLon, distance_m, m_to_nm
from ingest.grib_common import (
    apply_coastal_fill,
    coastal_fill_mask,
    direction_to_from_convention_deg,
    fetch_with_cycle_fallback,
    latest_available_cycle_utc,
    mask_land_as_missing,
    normalise_and_sort_dataset,
    normalise_longitude_deg,
    previous_cycle_utc,
    write_npz_atomic,
)


@pytest.mark.parametrize(
    "lon_in,expected",
    [
        (0.0, 0.0),
        (200.0, -160.0),
        (-170.0, -170.0),
        (360.0, 0.0),
        (-180.0, -180.0),
        (180.0, -180.0),  # open interval at +180 -> wraps to the lower bound
        (359.999, -0.001),
    ],
)
def test_normalise_longitude_deg(lon_in, expected):
    assert normalise_longitude_deg(lon_in) == pytest.approx(expected, abs=1e-6)


def test_normalise_longitude_deg_elementwise_on_array():
    result = normalise_longitude_deg(np.array([0.0, 200.0, 360.0]))
    np.testing.assert_allclose(result, [0.0, -160.0, 0.0])


def test_direction_to_from_convention_identity_when_already_from():
    assert direction_to_from_convention_deg(45.0, source_is_to_convention=False) == pytest.approx(
        45.0
    )
    assert direction_to_from_convention_deg(0.0, source_is_to_convention=False) == pytest.approx(
        0.0
    )


def test_direction_to_from_convention_flips_when_to_convention():
    assert direction_to_from_convention_deg(0.0, source_is_to_convention=True) == pytest.approx(
        180.0
    )
    assert direction_to_from_convention_deg(315.0, source_is_to_convention=True) == pytest.approx(
        135.0
    )


class _StubGeography:
    """Land at every (lat, lon) with lon >= 9.0 -- a simple hand-drawn
    stub, independent of the committed real coastline data."""

    def is_land_precise(self, lat_deg: float, lon_deg: float) -> bool:
        return lon_deg >= 9.0


def test_mask_land_as_missing_sets_land_columns_to_nan():
    lats = np.array([41.0, 42.0])
    lons = np.array([8.0, 9.0, 10.0])
    values = np.full((2, 2, 3), 1.5)  # (n_hours=2, n_lat=2, n_lon=3)

    masked = mask_land_as_missing(values, lats, lons, _StubGeography())

    # lon=8.0 is water -> untouched at every hour/lat.
    assert np.all(masked[:, :, 0] == 1.5)
    # lon=9.0 and lon=10.0 are "land" per the stub -> nan at every hour/lat.
    assert np.all(np.isnan(masked[:, :, 1]))
    assert np.all(np.isnan(masked[:, :, 2]))


def test_mask_land_as_missing_does_not_mutate_input():
    lats = np.array([41.0])
    lons = np.array([9.0])
    values = np.full((1, 1, 1), 1.5)
    mask_land_as_missing(values, lats, lons, _StubGeography())
    assert values[0, 0, 0] == 1.5


@pytest.mark.parametrize(
    "now_iso,delay_h,expected",
    [
        # 14:13 - 5h = 09:13 -> latest 6h boundary <= 9 is 06z, same day.
        ("2026-07-07T14:13:00+00:00", 5.0, ("20260707", "06")),
        # 02:00 - 5h = previous day 21:00 -> 18z, previous day (date rollback).
        ("2026-07-07T02:00:00+00:00", 5.0, ("20260706", "18")),
        # exactly on a cycle boundary after the delay.
        ("2026-07-07T11:00:00+00:00", 5.0, ("20260707", "06")),
    ],
)
def test_latest_available_cycle_utc(now_iso, delay_h, expected):
    now_utc = datetime.fromisoformat(now_iso)
    assert latest_available_cycle_utc(now_utc, delay_h=delay_h) == expected


def test_latest_available_cycle_utc_default_delay_is_deterministic():
    now_utc = datetime(2026, 7, 7, 12, 0, tzinfo=UTC)
    date_str, hour_str = latest_available_cycle_utc(now_utc)
    assert date_str == "20260707"
    assert hour_str in {"00", "06", "12", "18"}


def test_latest_available_cycle_utc_respects_restricted_valid_hours():
    """The exact live-observed failure (2026-07-13 Hetzner deploy): under
    the old fixed-6-hourly logic, 17:16 UTC with a 9h delay would land on
    08:16 available -> round to 06z, which ECMWF's oper/wave streams
    never publish. With valid_hours=(0, 12), it must select 00z."""
    now_utc = datetime(2026, 7, 13, 17, 16, tzinfo=UTC)
    assert latest_available_cycle_utc(now_utc, delay_h=9.0, valid_hours=(0, 12)) == (
        "20260713",
        "00",
    )


@pytest.mark.parametrize(
    "now_iso",
    [
        "2026-07-13T00:00:00+00:00",
        "2026-07-13T05:59:00+00:00",
        "2026-07-13T11:59:00+00:00",
        "2026-07-13T17:59:00+00:00",
        "2026-07-13T23:59:00+00:00",
    ],
)
def test_latest_available_cycle_utc_never_selects_06_or_18_for_restricted_set(now_iso):
    now_utc = datetime.fromisoformat(now_iso)
    _, hour_str = latest_available_cycle_utc(now_utc, delay_h=0.0, valid_hours=(0, 12))
    assert hour_str in {"00", "12"}


def test_latest_available_cycle_utc_restricted_set_day_rollback():
    # 02:00 - 9h = previous day 17:00 -> latest of (0, 12) at/before 17 is 12z, previous day.
    now_utc = datetime(2026, 7, 13, 2, 0, tzinfo=UTC)
    assert latest_available_cycle_utc(now_utc, delay_h=9.0, valid_hours=(0, 12)) == (
        "20260712",
        "12",
    )


@pytest.mark.parametrize(
    "cycle_date,cycle_hour,valid_hours,expected",
    [
        ("20260713", "12", (0, 12), ("20260713", "00")),
        ("20260713", "00", (0, 12), ("20260712", "12")),
        ("20260713", "00", (0, 6, 12, 18), ("20260712", "18")),
        ("20260713", "18", (0, 6, 12, 18), ("20260713", "12")),
    ],
)
def test_previous_cycle_utc(cycle_date, cycle_hour, valid_hours, expected):
    assert previous_cycle_utc(cycle_date, cycle_hour, valid_hours=valid_hours) == expected


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="http://example.invalid", code=code, msg="", hdrs=None, fp=None
    )


def test_fetch_with_cycle_fallback_steps_back_on_404_until_success():
    calls: list[tuple[str, str]] = []

    def attempt(date: str, hour: str) -> str:
        calls.append((date, hour))
        if (date, hour) == ("20260713", "00"):
            return "ok"
        raise _http_error(404)

    date, hour, result = fetch_with_cycle_fallback(
        "20260713",
        "12",
        valid_hours=(0, 12),
        max_attempts=3,
        attempt=attempt,
    )

    assert (date, hour, result) == ("20260713", "00", "ok")
    assert calls == [("20260713", "12"), ("20260713", "00")]


def test_fetch_with_cycle_fallback_logs_each_fallback_step(caplog):
    def attempt(date: str, hour: str) -> str:
        if (date, hour) == ("20260713", "00"):
            return "ok"
        raise _http_error(404)

    with caplog.at_level(logging.WARNING, logger="ingest.grib_common"):
        fetch_with_cycle_fallback(
            "20260713", "12", valid_hours=(0, 12), max_attempts=3, attempt=attempt
        )

    assert len(caplog.records) == 1
    message = caplog.records[0].getMessage()
    assert "20260713" in message and "12" in message  # old cycle
    assert "404" in message  # reason
    assert "00" in message  # new cycle


def test_fetch_with_cycle_fallback_propagates_non_404_immediately():
    calls = []

    def attempt(date: str, hour: str) -> str:
        calls.append((date, hour))
        raise _http_error(500)

    with pytest.raises(urllib.error.HTTPError) as excinfo:
        fetch_with_cycle_fallback(
            "20260713", "12", valid_hours=(0, 12), max_attempts=3, attempt=attempt
        )

    assert excinfo.value.code == 500
    assert calls == [("20260713", "12")]  # no retry on a non-404 failure


def test_fetch_with_cycle_fallback_reraises_after_exhausting_max_attempts():
    calls = []

    def attempt(date: str, hour: str) -> str:
        calls.append((date, hour))
        raise _http_error(404)

    with pytest.raises(urllib.error.HTTPError) as excinfo:
        fetch_with_cycle_fallback(
            "20260713", "12", valid_hours=(0, 12), max_attempts=3, attempt=attempt
        )

    assert excinfo.value.code == 404
    assert len(calls) == 3  # max_attempts, not swallowed forever


def test_write_npz_atomic_round_trips_and_leaves_no_tmp_file(tmp_path):
    out_path = tmp_path / "sub" / "field.npz"
    write_npz_atomic(out_path, a=np.array([1.0, 2.0]), label="hello")

    assert out_path.exists()
    assert not (tmp_path / "sub" / "field.npz.tmp").exists()

    loaded = np.load(out_path)
    np.testing.assert_allclose(loaded["a"], [1.0, 2.0])
    assert str(loaded["label"]) == "hello"


def test_write_npz_atomic_overwrites_existing_file(tmp_path):
    out_path = tmp_path / "field.npz"
    write_npz_atomic(out_path, a=np.array([1.0]))
    write_npz_atomic(out_path, a=np.array([2.0, 3.0]))

    loaded = np.load(out_path)
    np.testing.assert_allclose(loaded["a"], [2.0, 3.0])


def test_direction_to_from_convention_elementwise_on_array():
    arr = np.array([0.0, 90.0, 180.0])
    result = direction_to_from_convention_deg(arr, source_is_to_convention=True)
    np.testing.assert_allclose(result, [180.0, 270.0, 0.0])


# --- Ticket W1: coastal_fill_mask / apply_coastal_fill --------------------


class _AlwaysWaterGeography:
    def is_land_precise(self, lat_deg: float, lon_deg: float) -> bool:
        return False


class _AlwaysLandGeography:
    def is_land_precise(self, lat_deg: float, lon_deg: float) -> bool:
        return True


class _LonThresholdGeography:
    """Land for lon_deg >= threshold_lon_deg -- lets a test pick exactly
    where the threshold falls relative to a footprint's sampled offsets,
    for an exact, hand-verified water fraction."""

    def __init__(self, threshold_lon_deg: float) -> None:
        self.threshold_lon_deg = threshold_lon_deg

    def is_land_precise(self, lat_deg: float, lon_deg: float) -> bool:
        return lon_deg >= self.threshold_lon_deg


def test_coastal_fill_mask_true_when_footprint_is_pure_water():
    lats = np.array([41.0, 41.25])
    lons = np.array([9.0, 9.25])
    mask = coastal_fill_mask(lats, lons, _AlwaysWaterGeography())
    assert mask.all()


def test_coastal_fill_mask_false_when_footprint_is_pure_land():
    lats = np.array([41.0, 41.25])
    lons = np.array([9.0, 9.25])
    mask = coastal_fill_mask(lats, lons, _AlwaysLandGeography())
    assert not mask.any()


def test_coastal_fill_mask_respects_water_fraction_threshold():
    """The cell at (41.0, 9.0) with dlat=dlon=0.25 has a footprint
    spanning lon in [8.875, 9.125]; sampled on a 9-point grid
    (`linspace(-0.125, 0.125, 9)`), exactly 4 of the 9 lon offsets land
    below the threshold_lon_deg=9.0 boundary (water) and 5 land at/above
    it -- a real, hand-verified 4/9 ~= 0.444 water fraction, independent
    of the lat dimension since this stub's land rule ignores latitude.
    Below the default 0.5 threshold (correctly False); above a lowered
    0.4 threshold (correctly True) -- proves the threshold is load-
    bearing, not a passthrough."""
    lats = np.array([41.0, 41.25])
    lons = np.array([9.0, 9.25])
    geo = _LonThresholdGeography(threshold_lon_deg=9.0)

    default_mask = coastal_fill_mask(lats, lons, geo)
    assert not default_mask[0, 0]  # 0.444 < 0.5 default threshold

    lowered_mask = coastal_fill_mask(lats, lons, geo, water_fraction_threshold=0.4)
    assert lowered_mask[0, 0]  # 0.444 >= 0.4


def test_apply_coastal_fill_copies_nearest_valid_neighbour():
    lats = np.array([41.0])
    lons = np.array([9.0, 9.6, 11.0])  # middle cell's nearest neighbour is the left one
    values = np.array([[[10.0, np.nan, 999.0]]])
    fill_mask = np.array([[True, True, True]])

    filled, n_filled, n_skipped = apply_coastal_fill(
        values, lats, lons, fill_mask, ref_lat_deg=41.0
    )

    assert filled[0, 0, 1] == 10.0  # copied from the nearer (left) neighbour, not the farther
    assert n_filled == 1
    assert n_skipped == 0
    # never mutates already-real cells even though they're in fill_mask too.
    assert filled[0, 0, 0] == 10.0
    assert filled[0, 0, 2] == 999.0


def test_apply_coastal_fill_never_overwrites_real_data():
    lats = np.array([41.0])
    lons = np.array([9.0])
    values = np.array([[[5.0]]])
    fill_mask = np.array([[True]])  # True, but the cell is already real data

    filled, n_filled, n_skipped = apply_coastal_fill(
        values, lats, lons, fill_mask, ref_lat_deg=41.0
    )

    assert filled[0, 0, 0] == 5.0
    assert n_filled == 0
    assert n_skipped == 0


def test_apply_coastal_fill_skips_cells_not_in_fill_mask():
    lats = np.array([41.0])
    lons = np.array([9.0, 9.6])
    values = np.array([[[10.0, np.nan]]])
    fill_mask = np.array([[True, False]])  # the NaN cell is NOT eligible

    filled, n_filled, n_skipped = apply_coastal_fill(
        values, lats, lons, fill_mask, ref_lat_deg=41.0
    )

    assert np.isnan(filled[0, 0, 1])
    assert n_filled == 0
    assert n_skipped == 0


def test_apply_coastal_fill_leaves_cell_untouched_beyond_max_radius():
    """The only real (non-NaN) cell is 63.4nm away (real distance, via
    core.units.distance_m) -- farther than the default 60.0nm max radius
    -- so the fillable NaN cell must stay NaN and count as skipped, not
    filled from an implausibly distant sample."""
    lats = np.array([41.0])
    lons = np.array([9.0, 9.6, 11.0])
    assert m_to_nm(distance_m(LatLon(41.0, 9.6), LatLon(41.0, 11.0), 41.0)) > 60.0
    values = np.array([[[np.nan, np.nan, 999.0]]])
    fill_mask = np.array([[False, True, False]])

    filled, n_filled, n_skipped = apply_coastal_fill(
        values, lats, lons, fill_mask, ref_lat_deg=41.0
    )

    assert np.isnan(filled[0, 0, 1])
    assert n_filled == 0
    assert n_skipped == 1


def test_apply_coastal_fill_warns_past_warn_radius_but_still_fills(caplog):
    """Nearest valid neighbour is 45.3nm away (real distance) -- past the
    default 30.0nm warn radius but within the 60.0nm max radius: the fill
    still happens (the ceiling is unchanged), but a WARNING is logged
    naming the cell and the real distance."""
    lats = np.array([41.0])
    lons = np.array([9.0, 9.6, 10.6])
    d_nm = m_to_nm(distance_m(LatLon(41.0, 9.6), LatLon(41.0, 10.6), 41.0))
    assert 30.0 < d_nm < 60.0
    values = np.array([[[np.nan, np.nan, 999.0]]])
    fill_mask = np.array([[False, True, False]])

    with caplog.at_level(logging.WARNING):
        filled, n_filled, n_skipped = apply_coastal_fill(
            values, lats, lons, fill_mask, ref_lat_deg=41.0, field_name="hs_m"
        )

    assert filled[0, 0, 1] == 999.0
    assert n_filled == 1
    assert n_skipped == 0
    assert any("hs_m" in r.message and "away" in r.message for r in caplog.records)


def test_normalise_longitude_deg_no_nan_leak():
    assert not math.isnan(normalise_longitude_deg(0.0))


def _dataset(lats, lons, values):
    return xr.Dataset(
        {"v": (["latitude", "longitude"], values)},
        coords={"latitude": lats, "longitude": lons},
    )


def test_normalise_and_sort_dataset_flips_descending_latitude():
    # descending lats (north-to-south, common GRIB convention), ascending lons
    lats = [43.0, 42.0, 41.0]
    lons = [8.0, 9.0]
    # values chosen so each cell encodes its (lat_index, lon_index) for
    # unambiguous tracking through the reorder.
    values = np.array([[0, 1], [10, 11], [20, 21]], dtype=float)

    sorted_ds = normalise_and_sort_dataset(_dataset(lats, lons, values))

    np.testing.assert_allclose(sorted_ds.latitude.values, [41.0, 42.0, 43.0])
    np.testing.assert_allclose(sorted_ds.longitude.values, [8.0, 9.0])
    # row that was at lat=41.0 (originally index 2, value 20/21) is now first.
    np.testing.assert_allclose(sorted_ds["v"].values, [[20, 21], [10, 11], [0, 1]])


def test_normalise_and_sort_dataset_is_a_no_op_when_already_ascending():
    lats = [41.0, 42.0, 43.0]
    lons = [8.0, 9.0]
    values = np.array([[0, 1], [10, 11], [20, 21]], dtype=float)

    sorted_ds = normalise_and_sort_dataset(_dataset(lats, lons, values))

    np.testing.assert_allclose(sorted_ds.latitude.values, lats)
    np.testing.assert_allclose(sorted_ds.longitude.values, lons)
    np.testing.assert_allclose(sorted_ds["v"].values, values)


def test_normalise_and_sort_dataset_wraps_longitude_before_sorting():
    lats = [41.0]
    lons = [200.0, 8.0]  # 200 -> -160 after normalising, so it should end up first
    values = np.array([[100.0, 1.0]])

    sorted_ds = normalise_and_sort_dataset(_dataset(lats, lons, values))

    np.testing.assert_allclose(sorted_ds.longitude.values, [-160.0, 8.0])
    np.testing.assert_allclose(sorted_ds["v"].values, [[100.0, 1.0]])


def test_normalise_and_sort_dataset_result_has_monotonic_coords_for_interp():
    # exactly what fetch_grib_nomads.py needs before xr.Dataset.interp
    lats = [44.0, 43.0, 42.0, 41.0]
    lons = [10.0, 9.0, 8.0]
    values = np.arange(12, dtype=float).reshape(4, 3)

    sorted_ds = normalise_and_sort_dataset(_dataset(lats, lons, values))

    assert np.all(np.diff(sorted_ds.latitude.values) > 0)
    assert np.all(np.diff(sorted_ds.longitude.values) > 0)
