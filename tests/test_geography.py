import pytest

from core.geography import SyntheticGeography


@pytest.fixture
def geo():
    return SyntheticGeography()


def test_corsica_interior_is_land(geo):
    assert geo.is_land(42.3, 9.0) is True
    assert geo.is_navigable(42.3, 9.0) is False


def test_open_water_is_not_land(geo):
    # mid-strait / open water point, well clear of any polygon
    assert geo.is_land(41.9, 8.3) is False


def test_scandola_reserve_is_nogo_but_not_land(geo):
    assert geo.is_nogo(42.35, 8.55) is True
    assert geo.is_land(42.35, 8.55) is False
    assert geo.is_navigable(42.35, 8.55) is False


def test_open_water_far_from_reserves_is_navigable(geo):
    assert geo.is_navigable(42.0, 7.9) is True


def test_depth_is_shallower_near_coast_than_mid_basin(geo):
    near_coast = geo.depth_m(43.5, 7.05)
    mid_basin = geo.depth_m(43.0, 7.9)
    assert near_coast < mid_basin


def test_depth_is_positive_and_deterministic(geo):
    d1 = geo.depth_m(41.9, 8.3)
    d2 = geo.depth_m(41.9, 8.3)
    assert d1 == d2
    assert d1 > 0


def test_bonifacio_strait_depth_is_capped_shallow(geo):
    # inside the strait sill bounding box defined in depth_m
    d = geo.depth_m(41.30, 9.20)
    assert d <= 110.0
