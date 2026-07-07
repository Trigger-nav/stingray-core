import math

import pytest

from core.corridors import PORTS, corridor_east, corridor_west
from core.geography import OPERATING_AREA_BBOX
from core.lattice import REF_LAT_DEG, build_lattice


@pytest.fixture(scope="module")
def lattice():
    return build_lattice(PORTS["antibes"], PORTS["portocervo"])


def _cross_along_nm(lattice, point):
    """Decompose `point` into (along_track_nm, cross_track_nm) relative to
    the lattice's origin->destination line -- independent re-derivation of
    the offset geometry, for verifying containment rather than reusing
    core/lattice.py's own internals."""
    a, b = lattice.origin, lattice.destination
    klon = math.cos(math.radians(REF_LAT_DEG))
    brg = math.atan2((b.lon_deg - a.lon_deg) * klon, b.lat_deg - a.lat_deg)
    track_dx, track_dy = math.sin(brg), math.cos(brg)
    perp_dx, perp_dy = -track_dy, track_dx
    dx = (point.lon_deg - a.lon_deg) * klon * 60.0
    dy = (point.lat_deg - a.lat_deg) * 60.0
    return dx * track_dx + dy * track_dy, dx * perp_dx + dy * perp_dy


def _fits_in_lattice(lattice, point, tolerance_nm=2.0):
    along_nm, cross_nm = _cross_along_nm(lattice, point)
    total_nm = _cross_along_nm(lattice, lattice.destination)[0]
    frac = max(0.0, min(1.0, along_nm / total_nm))
    stage = round(frac * (lattice.n_stages - 1))
    half_width_nm = lattice.max_lane_per_stage[stage] * lattice.cross_track_step_nm
    return abs(cross_nm) <= half_width_nm + tolerance_nm


def test_lattice_spans_expected_stage_count(lattice):
    assert lattice.n_stages > 10


def test_lattice_widens_in_the_middle_and_tapers_at_the_ends(lattice):
    mid = lattice.n_stages // 2
    assert lattice.max_lane_per_stage[mid] > lattice.max_lane_per_stage[0]
    assert lattice.max_lane_per_stage[mid] > lattice.max_lane_per_stage[-1]


def test_lattice_lanes_stay_within_operating_area_bbox(lattice):
    lon_min, lat_min, lon_max, lat_max = OPERATING_AREA_BBOX
    for i in range(lattice.n_stages):
        max_lane = lattice.max_lane_per_stage[i]
        for k in (max_lane, -max_lane):
            p = lattice.point(i, k)
            assert lon_min <= p.lon_deg <= lon_max
            assert lat_min <= p.lat_deg <= lat_max


def test_west_corridor_waypoints_fit_within_lattice(lattice):
    for p in corridor_west().points:
        assert _fits_in_lattice(lattice, p), f"west corridor point {p} falls outside the lattice"


def test_east_corridor_waypoints_fit_within_lattice(lattice):
    for p in corridor_east().points:
        assert _fits_in_lattice(lattice, p), f"east corridor point {p} falls outside the lattice"
