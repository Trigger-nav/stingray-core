"""Opens the real, committed GRIB2 fixtures (`tests/fixtures/grib/`,
downloaded live from NOMADS/ECMWF during ticket 0.5's scoping, see
docs/plans/ticket-0.5.md) with actual `cfgrib`. Skipped -- not failed --
wherever `cfgrib`/`eccodes` isn't installed, but real verification of the
parsing shape both ingest scripts assume wherever it is (this sandbox now
included, as of the 2026-07-07 first real run -- see CLAUDE.md's
GRIB-conventions gotcha).

Variable-name assertions are exact, not permissive: the real cfgrib
shortNames for every field both ingest scripts use are now confirmed
against these exact fixtures (`ingest.fetch_grib_nomads.WIND_VARS`/
`WAVE_VARS`, `ingest.fetch_grib_ecmwf.WIND_PARAMS`/`WAVE_PARAMS`), so this
is what tightened once someone ran it for real.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

xr = pytest.importorskip("xarray")
pytest.importorskip("cfgrib")

from core.geography import OPERATING_AREA_BBOX  # noqa: E402
from ingest.fetch_grib_nomads import WAVE_VARS, WIND_VARS, _get_var  # noqa: E402

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "grib"


def _open(name: str) -> xr.Dataset:
    return xr.open_dataset(FIXTURE_DIR / name, engine="cfgrib", backend_kwargs={"indexpath": ""})


def test_gfs_wind_fixture_has_expected_variables():
    ds = _open("gfs_wind_sample.grib2")
    assert set(ds.data_vars) == {"u10", "v10"}
    u10 = _get_var(ds, WIND_VARS["u10_ms"])
    v10 = _get_var(ds, WIND_VARS["v10_ms"])
    assert u10.values.size > 0
    assert v10.values.size > 0
    ds.close()


def test_gfs_wind_fixture_is_bbox_cropped():
    ds = _open("gfs_wind_sample.grib2")
    lon_min, lat_min, lon_max, lat_max = OPERATING_AREA_BBOX
    lats = ds.latitude.values
    lons = ds.longitude.values
    margin = 1.0  # NOMADS' filter service may snap to the nearest grid cell
    assert lats.min() >= lat_min - margin
    assert lats.max() <= lat_max + margin
    assert lons.min() >= lon_min - margin
    assert lons.max() <= lon_max + margin
    ds.close()


def test_ww3_wave_fixture_has_expected_variables():
    ds = _open("ww3_wave_sample.grib2")
    hs = _get_var(ds, WAVE_VARS["hs_m"])
    period = _get_var(ds, WAVE_VARS["period_s"])
    wave_dir = _get_var(ds, WAVE_VARS["dir_deg"])
    assert hs.values.size > 0
    assert period.values.size > 0
    assert wave_dir.values.size > 0
    ds.close()


def test_ww3_perpw_is_confirmed_mean_not_peak_period():
    """Ground-truth check backing the `period_peak_s`/`period_mean_s`
    comment in `fetch_grib_nomads.build_grid` -- PERPW's own GRIB_name
    says "mean", not "peak"; NOMADS has no separate peak-period wave
    field, unlike ECMWF's pp1d."""
    ds = _open("ww3_wave_sample.grib2")
    assert "mean" in ds["perpw"].attrs["GRIB_name"].lower()
    ds.close()


def test_ecmwf_wind_fixture_decodes_10u():
    ds = _open("ecmwf_wind_sample.grib2")
    assert set(ds.data_vars) == {"u10"}
    assert ds["u10"].values.size > 0
    ds.close()


def test_ecmwf_wave_fixture_decodes_swh():
    ds = _open("ecmwf_wave_sample.grib2")
    assert set(ds.data_vars) == {"swh"}
    values = ds["swh"].values
    assert values.size > 0
    # significant wave height must be non-negative wherever it isn't a
    # fill/missing value.
    finite = values[~np.isnan(values)]
    assert finite.size > 0
    assert (finite >= 0).all()
    ds.close()
