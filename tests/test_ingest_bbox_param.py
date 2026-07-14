"""Ticket B7 Part 1: bbox-as-a-parameter across the four ingest scripts.

Matches this repo's existing precedent (ticket 0.5/0.3) of not exercising
real network I/O in tests -- `fetch_subset`/`clip_to_bbox` themselves stay
untested here (they always were); what's new and testable without network
is (a) the pure bbox-parametric helper functions inside the GRIB fetchers,
and (b) the CLI clobber-guard, which fires from argparse validation alone,
before any network call.
"""

import subprocess
import sys

import numpy as np
import pytest
import xarray as xr

from ingest.fetch_grib_ecmwf import _crop_to_bbox
from ingest.fetch_grib_nomads import _grib_filter_url


def test_ecmwf_crop_to_bbox_respects_bbox_param_not_the_module_default():
    lats = np.array([39.0, 40.0, 41.0, 42.0, 43.0])
    lons = np.array([5.0, 6.0, 7.0, 8.0, 9.0])
    ds = xr.Dataset(
        {"v": (["latitude", "longitude"], np.zeros((5, 5)))},
        coords={"latitude": lats, "longitude": lons},
    )
    cropped = _crop_to_bbox(ds, (6.0, 40.0, 8.0, 42.0))
    assert cropped.latitude.min() >= 40.0
    assert cropped.latitude.max() <= 42.0
    assert cropped.longitude.min() >= 6.0
    assert cropped.longitude.max() <= 8.0


def test_nomads_grib_filter_url_embeds_the_passed_bbox():
    url = _grib_filter_url(
        "filter_gfs_0p25_1hr.pl",
        dir_path="/gfs.20260101/00/atmos",
        file_name="gfs.t00z.pgrb2.0p25.f000",
        extra={},
        bbox=(1.0, 2.0, 3.0, 4.0),
    )
    assert "leftlon=1.0" in url
    assert "bottomlat=2.0" in url
    assert "rightlon=3.0" in url
    assert "toplat=4.0" in url


@pytest.mark.parametrize(
    "module,extra_args",
    [
        ("ingest.fetch_gshhg", []),
        ("ingest.fetch_gebco", []),
        ("ingest.fetch_grib_ecmwf", []),
        ("ingest.fetch_grib_nomads", []),
    ],
)
def test_differing_bbox_without_explicit_out_is_a_clear_cli_error(module, extra_args):
    # argparse validation (the clobber guard) fires before any network
    # call, so this is fast and offline-safe.
    result = subprocess.run(
        [sys.executable, "-m", module, "--bbox", "1", "1", "5", "5", *extra_args],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert "--out" in result.stderr
    assert "OPERATING_AREA_BBOX" in result.stderr
