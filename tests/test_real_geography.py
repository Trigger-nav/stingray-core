import random

import pytest

from core.geography import OPERATING_AREA_BBOX, OutOfOperatingAreaError, RealGeography


@pytest.fixture(scope="module")
def geo():
    return RealGeography()


def test_corsica_interior_is_land(geo):
    assert geo.is_land(42.3, 9.0) is True
    assert geo.is_navigable(42.3, 9.0) is False


def test_sardinia_interior_is_land(geo):
    assert geo.is_land(40.9, 9.2) is True
    assert geo.is_navigable(40.9, 9.2) is False


def test_open_water_ligurian_basin_is_navigable_and_deep(geo):
    assert geo.is_navigable(43.0, 7.9) is True
    assert geo.depth_m(43.0, 7.9) > 1000.0


def test_scandola_reserve_is_still_synthetic_nogo(geo):
    # ticket 0.3 explicitly keeps NOGO synthetic (real chart no-go data is 0.8)
    assert geo.is_nogo(42.35, 8.55) is True
    assert geo.is_land(42.35, 8.55) is False
    assert geo.is_navigable(42.35, 8.55) is False


def test_bonifacio_strait_is_navigable_and_shallow_relative_to_basin(geo):
    assert geo.is_navigable(41.30, 9.20) is True
    strait_depth = geo.depth_m(41.30, 9.20)
    basin_depth = geo.depth_m(43.0, 7.9)
    assert strait_depth < basin_depth
    assert strait_depth < 200.0


def test_depth_on_land_is_zero_not_negative_or_nonsensical(geo):
    assert geo.depth_m(42.3, 9.0) == pytest.approx(0.0)


def test_depth_is_deterministic(geo):
    a = geo.depth_m(41.9, 8.3)
    b = geo.depth_m(41.9, 8.3)
    assert a == b


def test_raster_land_mask_is_loaded(geo):
    assert geo._land_mask is not None


def test_raster_land_mask_agrees_with_polygon_on_known_points(geo):
    for lat, lon in [(42.3, 9.0), (43.0, 7.9), (41.30, 9.20), (40.9, 9.2)]:
        assert geo.is_land(lat, lon) == geo.is_land_precise(lat, lon)


def test_raster_land_mask_closely_agrees_with_polygon_on_random_sample(geo):
    # small resolution-driven disagreement right at a coastline boundary is
    # expected and fine; gross misclassification (interior land vs interior
    # water) is not. 99% agreement over a random sweep is the bar.
    rng = random.Random(0)
    lon_min, lat_min, lon_max, lat_max = OPERATING_AREA_BBOX
    n, agree = 2000, 0
    for _ in range(n):
        lat = rng.uniform(lat_min, lat_max)
        lon = rng.uniform(lon_min, lon_max)
        if geo.is_land(lat, lon) == geo.is_land_precise(lat, lon):
            agree += 1
    assert agree / n >= 0.99


@pytest.mark.parametrize(
    "lat,lon",
    [
        (50.0, 8.0),  # north of the bbox
        (42.0, 20.0),  # east of the bbox
        (30.0, 8.0),  # south of the bbox
    ],
)
def test_out_of_bounds_queries_raise(geo, lat, lon):
    with pytest.raises(OutOfOperatingAreaError):
        geo.is_navigable(lat, lon)
    with pytest.raises(OutOfOperatingAreaError):
        geo.depth_m(lat, lon)
