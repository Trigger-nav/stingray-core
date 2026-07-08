import pytest

from core.corridors import PORTS, corridor_east, corridor_west
from core.geography import RealGeography, SyntheticGeography
from core.isochrone import time_optimal_route
from core.optimiser import (
    TEST_SPEEDS_KN,
    PlanRequest,
    _distinguishing_region,
    _dp_route,
    _side_diversity_filter,
    build_lattice,
    feasible_speeds_kn,
    optimise,
)
from core.twin import VesselTwin
from core.units import LatLon, distance_m, interpolate_point, kn_to_ms, m_to_nm
from core.vessel_spec import VesselSpec
from core.weather import SyntheticWeatherField, WeatherSample
from core.weights import combine_weights, weights_from_mission

FINE_SAMPLE_INTERVAL_NM = 0.25
REF_LAT_DEG = 42.3

DEFAULT_SPEC_PATH = "data/vessel_specs/mys_50m_default.yaml"


@pytest.fixture
def vessel():
    return VesselSpec.from_yaml(DEFAULT_SPEC_PATH)


@pytest.fixture
def geo():
    return SyntheticGeography()


@pytest.fixture(scope="module")
def real_geo():
    return RealGeography()


def _fine_sample_track_is_navigable(track, geography, interval_nm=FINE_SAMPLE_INTERVAL_NM):
    """Independent of (and finer than, or equal to) whatever interval
    `core.legs._navigable_along_leg` itself uses — this is the check that
    would have caught both ticket 0.4 review findings: a lattice route
    clipping land in a fixed-fraction sampling gap, and the old
    straight-centreline baseline crossing real coastline outright."""
    for i in range(1, len(track)):
        p, q = track[i - 1], track[i]
        leg_nm = m_to_nm(distance_m(p, q, REF_LAT_DEG))
        n = max(1, int(leg_nm / interval_nm))
        for f in (j / n for j in range(n + 1)):
            point = interpolate_point(p, q, f)
            if geography.is_land(point.lat_deg, point.lon_deg):
                return False, (i, f, point)
    return True, None


@pytest.mark.slow
def test_every_returned_track_is_navigable_at_fine_resolution(vessel, real_geo):
    """Regression for both ticket 0.4 review findings: (1) evaluate_leg's
    navigability sampling missing a narrow headland/islet on a long lattice
    leg, and (2) the baseline crossing real coastline outright. Sweeps a
    representative set of scenarios/presets against RealGeography and
    fine-samples every leg of every returned track (candidates *and*
    baseline) at a finer resolution than production sampling itself uses,
    so it can't pass merely by coincidentally matching production's own
    sample points."""
    scenarios = ("mistral", "calm", "easterly")
    presets = [(0, 100), (25, 90), (50, 50), (100, 0)]
    for scenario in scenarios:
        wx = SyntheticWeatherField(scenario)
        for pace, comfort in presets:
            result = optimise(
                PlanRequest(
                    weather=wx, geography=real_geo, vessel=vessel, pace=pace, comfort=comfort
                )
            )
            for candidate in (*result.candidates, result.baseline):
                ok, bad = _fine_sample_track_is_navigable(candidate.track, real_geo)
                assert ok, (
                    f"{scenario} pace={pace} comfort={comfort}: "
                    f"{candidate.corridor_name} crosses land at {bad}"
                )


def test_mistral_high_comfort_routes_lee_side(vessel, geo):
    wx = SyntheticWeatherField("mistral")
    result = optimise(PlanRequest(weather=wx, geography=geo, vessel=vessel, pace=0, comfort=100))
    assert result.candidates[0].side == "E"


def test_calm_corridors_converge_and_speed_varies_by_pace(vessel, geo):
    wx = SyntheticWeatherField("calm")
    economy = optimise(PlanRequest(weather=wx, geography=geo, vessel=vessel, pace=0, comfort=50))
    schedule = optimise(PlanRequest(weather=wx, geography=geo, vessel=vessel, pace=100, comfort=50))

    economy_best = min(economy.candidates, key=lambda c: c.score_eur)
    schedule_best = min(schedule.candidates, key=lambda c: c.score_eur)
    assert schedule_best.speed_kn > economy_best.speed_kn

    # corridors "converge": in calm weather, both sides should appear
    # somewhere in a wide-enough candidate pool (no weather-driven bias).
    all_sides = {c.side for c in economy.candidates} | {c.side for c in schedule.candidates}
    assert all_sides == {"W", "E"}


def test_pure_schedule_weights_converge_to_isochrone_time_optimal(vessel, geo):
    """New C/§5 decision-record cross-check: under pure-schedule weights
    (Pace=100, Comfort=0 — fuel/comfort/wear barely score at all), the
    production search's top pick should converge to the isochrone
    time-optimal oracle's duration (`core.isochrone.time_optimal_route`,
    which ignores fuel/comfort/wear entirely). Not exact equality — pace=100
    still carries a small residual fuel weight (see `weights_from_mission`),
    so a small gap is expected, not a bug."""
    wx = SyntheticWeatherField("calm")
    result = optimise(PlanRequest(weather=wx, geography=geo, vessel=vessel, pace=100, comfort=0))
    top = result.candidates[0]

    twin = VesselTwin(vessel)
    lattice = build_lattice(PORTS["antibes"], PORTS["portocervo"])
    oracle = time_optimal_route(lattice, wx, geo, twin, TEST_SPEEDS_KN, (1, 2), 0.0)
    assert oracle is not None
    _, oracle_duration_h = oracle

    assert top.duration_h == pytest.approx(oracle_duration_h, rel=0.05)


def test_impossible_eta_window_flags_and_orders_fastest_first(vessel, geo):
    wx = SyntheticWeatherField("calm")
    result = optimise(
        PlanRequest(
            weather=wx, geography=geo, vessel=vessel, pace=50, comfort=50, latest_arrival_h=0.5
        )
    )
    assert result.missed_window is True
    durations = [c.duration_h for c in result.candidates]
    assert durations == sorted(durations)


@pytest.mark.slow
def test_charter_window_reflects_vessel_envelope_not_an_arbitrary_number(vessel, real_geo):
    """Charter-window regression, grounded in real physics rather than an
    arbitrary too-small number (the 0.5h case above). ROADMAP.md ticket 0.8
    restored a genuinely feasible W-side (via-Bonifacio) route against
    `RealGeography` (~12.0h, vs. the E-only ~13.9h this used to be limited
    to — see CLAUDE.md's now-resolved Bonifacio gotcha): a window of 13.0h
    is tighter than E's fastest but comfortably inside W's, so it must come
    back genuinely feasible via a W candidate, not merely "still infeasible
    for a different reason". Also confirms no candidate quietly exceeds the
    vessel's own envelope-capped speed ceiling to make the window."""
    wx = SyntheticWeatherField("calm")
    ceiling_kn = max(feasible_speeds_kn(vessel))
    result = optimise(
        PlanRequest(
            weather=wx,
            geography=real_geo,
            vessel=vessel,
            pace=100,
            comfort=0,
            latest_arrival_h=13.0,  # tighter than E's fastest (~13.9h), inside W's (~12.0h)
        )
    )
    assert result.missed_window is False
    assert any(c.side == "W" for c in result.candidates)
    assert all(c.speed_kn <= ceiling_kn for c in result.candidates)


@pytest.mark.slow
def test_bonifacio_strait_transit_is_reachable_at_current_lattice_resolution(vessel, real_geo):
    """ROADMAP.md ticket 0.8: the open lattice can now thread the real
    Bonifacio Strait / Iles Lavezzi channel — adaptive per-stage lattice
    refinement (`core.lattice.build_lattice`) handles the genuine
    scattered-islet resolution need, and a side-diversity-filter bug (which
    had been constraining lane sign across the *whole* passage rather than
    just the Corsica-spanning "distinguishing region", silently forcing the
    W-side search onto real Sardinian coastline at the final approach) is
    fixed. This route used to be diagnosed as W-side-unreachable (see
    CLAUDE.md's now-resolved Bonifacio gotcha) — both sides should be
    genuinely reachable now, with no diagnostic."""
    wx = SyntheticWeatherField("calm")
    result = optimise(
        PlanRequest(weather=wx, geography=real_geo, vessel=vessel, pace=50, comfort=50)
    )
    assert result.missed_window is False
    assert {c.side for c in result.candidates} == {"W", "E"}
    assert not any(d.code == "route_side_unreachable" for d in result.diagnostics)


class _ConstantRoughHeadSeas:
    """Rough enough, and opposed enough to the corridor headings (wave_from
    chosen so most legs land encounter angle > 140 on both corridors — see
    the ticket 0.2 plan's B5 test), to trigger the slamming hard constraint
    at higher speeds while staying feasible at lower ones."""

    def __init__(self, hs_m: float):
        self.hs_m = hs_m

    def sample(self, lat_deg, lon_deg, t_h):
        return WeatherSample(
            hs_m=self.hs_m,
            period_peak_s=6.5,
            period_mean_s=5.2,
            wave_from_deg=330.0,
            wind_u_ms=0.0,
            wind_v_ms=0.0,
            current_u_ms=0.0,
            current_v_ms=0.0,
        )


def test_slamming_speed_is_pruned_outright_not_downranked(vessel, geo):
    """B5: at a speed/sea-state combination that triggers slamming, the DP
    must return infeasible (None) for that speed — pruned outright, not
    merely scored worse than an alternative. Checked directly against
    `_dp_route`, below the diversity-filtered top-3 `optimise()` returns,
    since a speed absent from the top-3 could just mean it scored worse."""
    twin = VesselTwin(vessel)
    weights = combine_weights(weights_from_mission(pace=100, comfort=0), vessel.wear_policy)
    rough = _ConstantRoughHeadSeas(hs_m=vessel.wear_policy.slamming_hs_threshold_m + 1.5)
    calm = _ConstantRoughHeadSeas(hs_m=0.3)
    # 16 kn: comfortably under the engine-load hard constraint in calm seas
    # (so any pruning at this speed is attributable to slamming, not
    # overload), and above the slamming minimum speed.
    fast_kn = 16.0

    for corridor_fn in (corridor_west, corridor_east):
        corridor = corridor_fn()
        infeasible_in_rough_seas = _dp_route(
            corridor, kn_to_ms(fast_kn), fast_kn, 2, rough, geo, twin, weights, 0.0
        )
        feasible_in_calm_seas = _dp_route(
            corridor, kn_to_ms(fast_kn), fast_kn, 2, calm, geo, twin, weights, 0.0
        )
        assert infeasible_in_rough_seas is None
        assert feasible_in_calm_seas is not None

    # and it should be genuinely absent from the top-level candidate pool too
    result = optimise(PlanRequest(weather=rough, geography=geo, vessel=vessel, pace=100, comfort=0))
    assert all(c.speed_kn != fast_kn for c in result.candidates)


# a real, navigable origin/destination pair entirely south of
# CORSICA_LAT_BAND (41.3-43.0) -- every stage's centre stays in that
# southern strip, so there's no "west/east of Corsica" concept for this
# passage at all (ticket 0.8 amendment 3).
FAR_FROM_CORSICA_ORIGIN = LatLon(40.80, 7.0)
FAR_FROM_CORSICA_DESTINATION = LatLon(40.90, 8.0)


def test_distinguishing_region_is_none_for_a_passage_nowhere_near_corsica():
    lattice = build_lattice(FAR_FROM_CORSICA_ORIGIN, FAR_FROM_CORSICA_DESTINATION)
    assert _distinguishing_region(lattice) is None


def test_side_diversity_filter_is_none_when_no_distinguishing_region():
    lattice = build_lattice(FAR_FROM_CORSICA_ORIGIN, FAR_FROM_CORSICA_DESTINATION)
    assert _side_diversity_filter(lattice, "W") is None
    assert _side_diversity_filter(lattice, "E") is None


@pytest.mark.slow
def test_optimise_does_not_crash_for_a_passage_with_no_distinguishing_region(vessel):
    """amendment 3: optimise() must handle an origin/destination pair with
    no Corsica-relative "side" concept gracefully -- no crash, and the
    side-diversity mechanism simply doesn't constrain anything (there's
    nothing to be diverse *about* for a passage that never goes near
    Corsica)."""
    real_geo = RealGeography()
    wx = SyntheticWeatherField("calm")
    result = optimise(
        PlanRequest(
            weather=wx,
            geography=real_geo,
            vessel=vessel,
            pace=50,
            comfort=50,
            origin=FAR_FROM_CORSICA_ORIGIN,
            destination=FAR_FROM_CORSICA_DESTINATION,
        )
    )
    assert result.candidates
