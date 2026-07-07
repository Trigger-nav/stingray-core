import pytest

from core.corridors import PORTS, corridor_east, corridor_west
from core.geography import RealGeography, SyntheticGeography
from core.isochrone import time_optimal_route
from core.optimiser import (
    TEST_SPEEDS_KN,
    PlanRequest,
    _dp_route,
    build_lattice,
    feasible_speeds_kn,
    optimise,
)
from core.twin import VesselTwin
from core.units import distance_m, interpolate_point, kn_to_ms, m_to_nm
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
def test_charter_window_infeasible_reflects_vessel_envelope_not_an_arbitrary_number(
    vessel, real_geo
):
    """Charter-window regression, grounded in real physics rather than an
    arbitrary too-small number (the 0.5h case above): against
    `RealGeography`, a window tighter than the vessel's own envelope-capped
    fastest passage (`feasible_speeds_kn`'s ceiling, ~16kn on this spec —
    see `test_optimiser_constraints.py`) must still come back flagged and
    fastest-first, and every returned candidate must respect that ceiling —
    the search isn't quietly reaching for a speed the vessel can't
    sustain to try to satisfy an impossible window."""
    wx = SyntheticWeatherField("calm")
    ceiling_kn = max(feasible_speeds_kn(vessel))
    result = optimise(
        PlanRequest(
            weather=wx,
            geography=real_geo,
            vessel=vessel,
            pace=100,
            comfort=0,
            latest_arrival_h=13.5,  # tighter than the fastest achievable at this ceiling
        )
    )
    assert result.missed_window is True
    assert any(d.code == "eta_window_infeasible" for d in result.diagnostics)
    durations = [c.duration_h for c in result.candidates]
    assert durations == sorted(durations)
    assert all(c.speed_kn <= ceiling_kn for c in result.candidates)


@pytest.mark.slow
def test_bonifacio_unreachable_at_current_lattice_resolution_is_diagnosed(vessel, real_geo):
    """ROADMAP.md ticket 0.8 finding: the open lattice can't thread the real
    Bonifacio Strait / Iles Lavezzi channel at its current 5nm lane spacing,
    so every plan on this passage currently goes east-about only — this
    used to be silent (see CLAUDE.md's Bonifacio gotcha). A generous,
    perfectly achievable window (so this isn't conflated with the
    ETA-window-infeasible case above) should still surface a machine-
    readable diagnostic explaining the missing W-side option, not just
    quietly return only E-side candidates."""
    wx = SyntheticWeatherField("calm")
    result = optimise(
        PlanRequest(weather=wx, geography=real_geo, vessel=vessel, pace=50, comfort=50)
    )
    assert result.missed_window is False
    assert {c.side for c in result.candidates} == {"E"}
    assert any(d.code == "route_side_unreachable" and d.side == "W" for d in result.diagnostics)


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
