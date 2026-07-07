import pytest

from core.geography import RealGeography


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
