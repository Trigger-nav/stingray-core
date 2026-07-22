import math

import pytest

from core.corridors import PORTS, corridor_east, corridor_west
from core.geography import OPERATING_AREA_BBOX, RealGeography
from core.lattice import (
    ALONG_TRACK_STEP_FRACTION,
    DEFAULT_ALONG_TRACK_STEP_NM,
    DEFAULT_CROSS_TRACK_STEP_NM,
    DEFAULT_MIN_REFINEMENT_STEP_NM,
    LANE_TURN_RATE_NM,
    REF_LAT_DEG,
    RefinementDiagnostic,
    _turn_range,
    build_lattice,
)
from core.regionpack import RegionPack
from core.units import LatLon, distance_m, m_to_nm


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
    half_width_nm = lattice.max_lane_per_stage[stage] * lattice.cross_track_step_nm[stage]
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


def test_uniform_step_when_no_geography_given(lattice):
    # the pre-0.8 default: no adaptive refinement without a Geography.
    assert all(s == DEFAULT_CROSS_TRACK_STEP_NM for s in lattice.cross_track_step_nm)
    assert lattice.refinement_diagnostics == ()


def test_along_track_step_derives_and_floors_exactly_to_default_on_the_med_passage():
    """Ticket L2 §2a: ALONG_TRACK_STEP_FRACTION reduces bit-exactly to
    DEFAULT_ALONG_TRACK_STEP_NM on the Med's own real ~179.5508nm passage
    -- direct algebra, independent of build_lattice, plus a build_lattice
    cross-check that leaving along_track_step_nm unset produces byte-
    identical stage_centres to passing the literal default explicitly."""
    med_nm = m_to_nm(distance_m(PORTS["antibes"], PORTS["portocervo"], REF_LAT_DEG))
    derived = max(DEFAULT_ALONG_TRACK_STEP_NM, med_nm * ALONG_TRACK_STEP_FRACTION)
    assert derived == DEFAULT_ALONG_TRACK_STEP_NM

    implicit = build_lattice(PORTS["antibes"], PORTS["portocervo"])
    explicit = build_lattice(
        PORTS["antibes"], PORTS["portocervo"], along_track_step_nm=DEFAULT_ALONG_TRACK_STEP_NM
    )
    assert implicit.stage_centres == explicit.stage_centres


def test_along_track_step_derives_and_floors_exactly_to_default_on_the_uk_passage():
    """Same proof for the UK pack: the formula's own unfloored output
    (~1.228nm) sits squarely in ticket L2's own found-infeasible zone --
    the floor is what keeps the shipped UK pack at exactly today's
    working 6.0nm value, not a silent regression."""
    pack = RegionPack.from_yaml("data/region_packs/uk_sw.yaml")
    uk_nm = m_to_nm(
        distance_m(pack.default_origin, pack.default_destination, pack.ref_lat_deg)
    )
    unfloored = uk_nm * ALONG_TRACK_STEP_FRACTION
    assert unfloored < DEFAULT_ALONG_TRACK_STEP_NM  # would be infeasible if not floored
    derived = max(DEFAULT_ALONG_TRACK_STEP_NM, unfloored)
    assert derived == DEFAULT_ALONG_TRACK_STEP_NM


def test_along_track_step_coarsens_for_a_passage_much_longer_than_the_med():
    """A ~360nm passage (2x the Med reference length) should derive a
    coarser-than-default along_track_step_nm, keeping n_stages roughly
    constant relative to the Med rather than growing linearly the way a
    fixed 6.0nm step would -- the real, if narrow, behaviour change this
    ticket makes (docs/plans/ticket-L2.md §4)."""
    a, b = LatLon(40.0, 8.0), LatLon(46.0, 8.0)
    long_nm = m_to_nm(distance_m(a, b, REF_LAT_DEG))
    assert long_nm == pytest.approx(360.0, abs=0.5)

    derived_lattice = build_lattice(a, b)
    fixed_lattice = build_lattice(a, b, along_track_step_nm=DEFAULT_ALONG_TRACK_STEP_NM)
    # Derived spacing is coarser -> fewer stages than the fixed default would give.
    assert derived_lattice.n_stages < fixed_lattice.n_stages
    # Roughly halved (2x passage length -> ~2x step -> ~half the stages),
    # not merely "fewer" -- the real numbers found during planning: 31 vs 61.
    assert derived_lattice.n_stages == pytest.approx(fixed_lattice.n_stages / 2, rel=0.1)


def test_adaptive_refinement_off_switch_leaves_step_uniform():
    real_geo = RealGeography()
    lattice = build_lattice(
        PORTS["antibes"], PORTS["portocervo"], geography=real_geo, adaptive_refinement=False
    )
    assert all(s == DEFAULT_CROSS_TRACK_STEP_NM for s in lattice.cross_track_step_nm)


@pytest.fixture(scope="module")
def real_lattice():
    return build_lattice(PORTS["antibes"], PORTS["portocervo"], geography=RealGeography())


@pytest.mark.slow
def test_adaptive_refinement_is_finer_at_degraded_bonifacio_stages_and_coarse_elsewhere(
    real_lattice,
):
    # Bonifacio Strait's genuine scattered-islet degradation (ROADMAP.md
    # ticket 0.8) sits around stages 27-28 on the real Antibes/Porto Cervo
    # passage -- these must refine below the coarse default, while most of
    # the passage (open water) stays at the cheap default.
    steps = real_lattice.cross_track_step_nm
    assert steps[27] < DEFAULT_CROSS_TRACK_STEP_NM
    assert steps[28] < DEFAULT_CROSS_TRACK_STEP_NM
    n_coarse = sum(1 for s in steps if s == DEFAULT_CROSS_TRACK_STEP_NM)
    assert n_coarse > real_lattice.n_stages // 2


@pytest.mark.slow
def test_multi_pass_refinement_stops_at_the_step_floor_and_reports_a_diagnostic(real_lattice):
    # amendment 2: a stage that's still below the navigability threshold
    # after hitting max_refinement_passes/min_refinement_step_nm is
    # reported, not silently accepted -- Bonifacio's worst real stages
    # (scattered islets, not a resolution problem refinement alone can fix)
    # are exactly this case.
    assert real_lattice.refinement_diagnostics
    refined_stage_indices = {d.stage for d in real_lattice.refinement_diagnostics}
    assert {27, 28} <= refined_stage_indices
    for d in real_lattice.refinement_diagnostics:
        assert isinstance(d, RefinementDiagnostic)
        assert d.final_step_nm == pytest.approx(DEFAULT_MIN_REFINEMENT_STEP_NM)
        assert real_lattice.cross_track_step_nm[d.stage] == pytest.approx(d.final_step_nm)


def test_turn_range_never_exceeds_the_physical_lane_turn_rate_coarse_to_fine():
    steps = [5.0, 0.5]
    max_lane_per_stage = [10, 200]
    from_lane = 2  # physical position = 10.0nm at the coarse stage
    from_physical_nm = from_lane * steps[0]

    r = _turn_range(steps, max_lane_per_stage, 0, from_lane)
    assert len(r) > 0
    for next_lane in r:
        next_physical_nm = next_lane * steps[1]
        assert abs(next_physical_nm - from_physical_nm) <= LANE_TURN_RATE_NM + 1e-9
    # one lane beyond either edge must exceed the allowance -- proves the
    # range isn't just narrower than necessary.
    assert abs((r.start - 1) * steps[1] - from_physical_nm) > LANE_TURN_RATE_NM - 1e-9
    assert abs((r.stop) * steps[1] - from_physical_nm) > LANE_TURN_RATE_NM - 1e-9


def test_turn_range_never_exceeds_the_physical_lane_turn_rate_fine_to_coarse():
    steps = [0.5, 5.0]
    max_lane_per_stage = [200, 10]
    from_lane = 20  # physical position = 10.0nm at the fine stage
    from_physical_nm = from_lane * steps[0]

    r = _turn_range(steps, max_lane_per_stage, 0, from_lane)
    assert len(r) > 0
    for next_lane in r:
        next_physical_nm = next_lane * steps[1]
        assert abs(next_physical_nm - from_physical_nm) <= LANE_TURN_RATE_NM + 1e-9
    assert abs((r.start - 1) * steps[1] - from_physical_nm) > LANE_TURN_RATE_NM - 1e-9
    assert abs((r.stop) * steps[1] - from_physical_nm) > LANE_TURN_RATE_NM - 1e-9


def test_turn_range_is_clamped_to_max_lane_per_stage():
    steps = [5.0, 5.0]
    max_lane_per_stage = [10, 1]  # target stage only has lanes -1..1
    r = _turn_range(steps, max_lane_per_stage, 0, 0)
    assert r.start >= -1
    assert r.stop - 1 <= 1


def test_turn_range_uniform_step_matches_simple_symmetric_range():
    # sanity check against the pre-0.8 uniform-step case: a symmetric
    # +-N lane window around from_lane, same as the old fixed-index scheme.
    steps = [5.0, 5.0]
    max_lane_per_stage = [20, 20]
    r = _turn_range(steps, max_lane_per_stage, 0, 3)
    expected_half_width = int(LANE_TURN_RATE_NM // 5.0)
    assert list(r) == list(range(3 - expected_half_width, 3 + expected_half_width + 1))
