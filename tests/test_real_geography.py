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


def test_scandola_reserve_is_a_real_cited_nogo_zone(geo):
    # ticket 0.8: RealGeography loads real, cited zones (marineregions.org
    # MRGID 26848, World Heritage Marine Programme) from
    # data/geography/nogo_western_med.json instead of the synthetic boxes
    # -- a point well inside the real bounding box.
    assert geo.is_nogo(42.28, 8.58) is True
    assert geo.is_land(42.28, 8.58) is False
    assert geo.is_navigable(42.28, 8.58) is False


def test_iles_lavezzi_archipelago_is_a_real_cited_nogo_zone(geo):
    # marineregions.org MRGID 3457 (ASFA thesaurus) -- a point inside the
    # archipelago's real bounding box, distinct from the physical islets
    # themselves (already represented by the real GSHHG coastline).
    assert geo.is_nogo(41.34, 9.255) is True


def test_bonifacio_tss_separation_zone_is_a_hard_nogo(geo):
    # ticket 0.8: the placeholder TSS separation zone
    # (data/geography/tss_western_med.json) is a hard no-go, same
    # mechanism as a chart-derived reserve -- explicitly NOT a directional
    # lane rule (see the file's scope_note and CLAUDE.md's Bonifacio
    # gotcha follow-up). A point inside its bounding box.
    assert geo.is_nogo(41.38, 9.06) is True


def test_open_water_outside_any_nogo_zone_is_navigable(geo):
    assert geo.is_nogo(41.29, 9.08) is False
    assert geo.is_navigable(41.29, 9.08) is True


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
