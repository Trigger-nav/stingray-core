"""Opens the real, committed GRIB2 fixtures (`tests/fixtures/grib/`,
downloaded live from NOMADS/ECMWF during ticket 0.5's scoping, see
docs/plans/ticket-0.5.md) with actual `cfgrib`. Skipped -- not failed --
wherever `cfgrib`/`eccodes` isn't installed (this sandbox included), but
real verification of the parsing shape both ingest scripts assume, the
moment this runs somewhere that has it (see CLAUDE.md's "first real run"
checklist).

Variable-name assertions for the NOMADS/WW3 fixtures are intentionally
permissive (checked against a short candidate list via
`ingest.fetch_grib_nomads._find_var`) since NOAA-vs-WMO shortName naming
for these exact fields wasn't confirmed without eccodes during scoping --
that's the one thing to tighten once someone runs this for real.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

xr = pytest.importorskip("xarray")
pytest.importorskip("cfgrib")

from core.geography import OPERATING_AREA_BBOX  # noqa: E402
from ingest.fetch_grib_nomads import (  # noqa: E402
    WAVE_VAR_CANDIDATES,
    WIND_VAR_CANDIDATES,
    _find_var,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "grib"


def _open(name: str) -> xr.Dataset:
    return xr.open_dataset(FIXTURE_DIR / name, engine="cfgrib", backend_kwargs={"indexpath": ""})


def test_gfs_wind_fixture_has_expected_variables():
    ds = _open("gfs_wind_sample.grib2")
    u10 = _find_var(ds, WIND_VAR_CANDIDATES["u10_ms"])
    v10 = _find_var(ds, WIND_VAR_CANDIDATES["v10_ms"])
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
    hs = _find_var(ds, WAVE_VAR_CANDIDATES["hs_m"])
    period = _find_var(ds, WAVE_VAR_CANDIDATES["period_s"])
    wave_dir = _find_var(ds, WAVE_VAR_CANDIDATES["dir_deg"])
    assert hs.values.size > 0
    assert period.values.size > 0
    assert wave_dir.values.size > 0
    ds.close()


def test_ecmwf_wind_fixture_decodes_10u():
    ds = _open("ecmwf_wind_sample.grib2")
    assert len(ds.data_vars) >= 1
    var = ds[next(iter(ds.data_vars))]
    assert var.values.size > 0
    ds.close()


def test_ecmwf_wave_fixture_decodes_swh():
    ds = _open("ecmwf_wave_sample.grib2")
    assert len(ds.data_vars) >= 1
    var = ds[next(iter(ds.data_vars))]
    values = var.values
    assert values.size > 0
    # significant wave height must be non-negative wherever it isn't a
    # fill/missing value -- a basic physical sanity check that doesn't
    # depend on knowing the exact variable name.
    finite = values[~np.isnan(values)]
    assert finite.size > 0
    assert (finite >= 0).all()
    ds.close()
