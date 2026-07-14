"""Regression test for the RealGeography._check_in_bounds gap found
during ticket B7 planning: bounds must derive from the loaded instance's
own grid, not the module-level OPERATING_AREA_BBOX constant -- otherwise
a RealGeography pointed at a different bbox's data validates against the
wrong bounds.
"""

import json

import numpy as np
import pytest

from core.geography import OPERATING_AREA_BBOX, OutOfOperatingAreaError, RealGeography


@pytest.fixture
def synthetic_geography(tmp_path):
    """A tiny RealGeography loaded from a bbox disjoint from
    OPERATING_AREA_BBOX -- centred around (20.0, 20.0), far from the
    western Med (6.7-10.15 lon, 40.75-44.0 lat)."""
    coastline_path = tmp_path / "coastline.json"
    coastline_path.write_text(
        json.dumps({"source": "synthetic", "bbox_lon_lat": [], "polygons": []})
    )

    nogo_path = tmp_path / "nogo.json"
    nogo_path.write_text(json.dumps({"zones": []}))
    tss_path = tmp_path / "tss.json"
    tss_path.write_text(json.dumps({"zones": []}))

    bathymetry_path = tmp_path / "bathymetry.npz"
    nlat, nlon = 10, 10
    np.savez_compressed(
        bathymetry_path,
        lat0=19.0,
        dlat=0.1,
        lon0=19.0,
        dlon=0.1,
        nlat=nlat,
        nlon=nlon,
        elevation_m=np.full((nlat, nlon), -100.0),
    )
    return RealGeography(
        coastline_path=coastline_path,
        bathymetry_path=bathymetry_path,
        nogo_path=nogo_path,
        tss_path=tss_path,
    )


def test_point_inside_synthetic_bbox_but_outside_operating_area_bbox_is_accepted(
    synthetic_geography,
):
    # (19.5, 19.5) is inside the synthetic grid (lat/lon 19.0-19.9) and
    # nowhere near OPERATING_AREA_BBOX's western-Med box.
    assert synthetic_geography.depth_m(19.5, 19.5) == pytest.approx(100.0)


def test_point_inside_operating_area_bbox_but_outside_synthetic_bbox_is_rejected(
    synthetic_geography,
):
    lon_mid = (OPERATING_AREA_BBOX[0] + OPERATING_AREA_BBOX[2]) / 2
    lat_mid = (OPERATING_AREA_BBOX[1] + OPERATING_AREA_BBOX[3]) / 2
    with pytest.raises(OutOfOperatingAreaError):
        synthetic_geography.depth_m(lat_mid, lon_mid)


def test_default_real_geography_still_accepts_western_med_points():
    # regression guard: the default-constructed instance must still behave
    # exactly as before for the committed western-Med data.
    geo = RealGeography()
    lon_mid = (OPERATING_AREA_BBOX[0] + OPERATING_AREA_BBOX[2]) / 2
    lat_mid = (OPERATING_AREA_BBOX[1] + OPERATING_AREA_BBOX[3]) / 2
    assert geo.is_navigable(lat_mid, lon_mid) in (True, False)  # doesn't raise
