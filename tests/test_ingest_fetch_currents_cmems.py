"""ingest/fetch_currents_cmems.py's pure grid-building logic (ticket C1)
-- no real network/credentials call in CI, matching ticket B7's ERA5
mocking precedent. `_grid_from_dataset` is tested directly against a
fabricated, known-analytic `xr.Dataset`, never real CMEMS output.
"""

from __future__ import annotations

import numpy as np
import pytest
import xarray as xr

from ingest.fetch_currents_cmems import _grid_from_dataset


class _StubGeography:
    """Land at every (lat, lon) with lon >= 9.0 -- matches
    tests/test_grib_common.py's own stub shape."""

    def is_land_precise(self, lat_deg: float, lon_deg: float) -> bool:
        return lon_deg >= 9.0


def _fabricated_dataset(n_time: int = 2) -> xr.Dataset:
    lats = np.array([41.0, 42.0])
    lons = np.array([8.0, 9.0])
    times = np.array(
        ["2026-07-14T00:00:00", "2026-07-14T01:00:00"][:n_time], dtype="datetime64[ns]"
    )
    # Known analytic values: uo = 0.1 * time_index + 0.01 * lat_index, so
    # assertions can check exact expected numbers, not just "not NaN".
    uo = np.zeros((n_time, 2, 2))
    vo = np.zeros((n_time, 2, 2))
    for t in range(n_time):
        for iy in range(2):
            for ix in range(2):
                uo[t, iy, ix] = 0.1 * t + 0.01 * iy
                vo[t, iy, ix] = -0.05 * t + 0.02 * ix
    return xr.Dataset(
        {
            "uo": (("time", "latitude", "longitude"), uo),
            "vo": (("time", "latitude", "longitude"), vo),
        },
        coords={"time": times, "latitude": lats, "longitude": lons},
    )


def test_grid_from_dataset_extracts_known_values():
    ds = _fabricated_dataset()
    grid = _grid_from_dataset(ds, _StubGeography())
    assert grid["lat0"] == pytest.approx(41.0)
    assert grid["dlat"] == pytest.approx(1.0)
    assert grid["lon0"] == pytest.approx(8.0)
    assert grid["dlon"] == pytest.approx(1.0)
    assert grid["current_u_ms"].shape == (2, 2, 2)
    # lon=8.0 column is water -> real value at t=0, iy=0: uo=0.0
    assert grid["current_u_ms"][0, 0, 0] == pytest.approx(0.0)
    assert grid["current_u_ms"][1, 1, 0] == pytest.approx(0.1 * 1 + 0.01 * 1)


def test_grid_from_dataset_land_masks_current():
    ds = _fabricated_dataset()
    grid = _grid_from_dataset(ds, _StubGeography())
    # lon=9.0 column is "land" per the stub -> nan at every time/lat.
    assert np.all(np.isnan(grid["current_u_ms"][:, :, 1]))
    assert np.all(np.isnan(grid["current_v_ms"][:, :, 1]))
    # lon=8.0 column is water -> untouched.
    assert not np.any(np.isnan(grid["current_u_ms"][:, :, 0]))


def test_grid_from_dataset_times_are_absolute_utc_epoch_not_relative_hours():
    ds = _fabricated_dataset()
    grid = _grid_from_dataset(ds, _StubGeography())
    # 2026-07-14T00:00:00Z and T01:00:00Z as epoch seconds, 3600s apart --
    # absolute, not "0.0, 1.0" the way every other ingest npz's `hours`
    # array would represent this (see module docstring for why).
    assert grid["times"][1] - grid["times"][0] == pytest.approx(3600.0)
    # a real 2026-ish epoch second, not a small relative index like "0.0"
    assert grid["times"][0] > 1_700_000_000


def test_grid_from_dataset_handles_a_single_timestep():
    ds = _fabricated_dataset(n_time=1)
    grid = _grid_from_dataset(ds, _StubGeography())
    assert grid["current_u_ms"].shape == (1, 2, 2)
    assert grid["times"].shape == (1,)
