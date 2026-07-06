import pytest

from core.corridors import corridor_west
from core.geography import SyntheticGeography
from core.optimiser import PlanRequest, _dp_route, optimise
from core.twin import VesselTwin
from core.units import kn_to_ms
from core.vessel_spec import VesselSpec
from core.weather import SyntheticWeatherField, WeatherSample
from core.weights import combine_weights, weights_from_mission

DEFAULT_SPEC_PATH = "data/vessel_specs/mys_50m_default.yaml"


@pytest.fixture
def vessel():
    return VesselSpec.from_yaml(DEFAULT_SPEC_PATH)


@pytest.fixture
def geo():
    return SyntheticGeography()


class _CurrentField:
    def __init__(self, current_u_ms: float, current_v_ms: float):
        self.current_u_ms = current_u_ms
        self.current_v_ms = current_v_ms

    def sample(self, lat_deg, lon_deg, t_h):
        return WeatherSample(
            hs_m=0.5,
            period_peak_s=6.5,
            period_mean_s=5.2,
            wave_from_deg=220.0,
            wind_u_ms=0.0,
            wind_v_ms=0.0,
            current_u_ms=self.current_u_ms,
            current_v_ms=self.current_v_ms,
        )


def test_a4_current_changes_duration_not_fuel_rate(vessel, geo):
    """STW/SOG distinction: the twin's fuel *rate* at a commanded STW must
    be identical regardless of current (it never reads the current fields
    at all) — total trip fuel legitimately changes with duration (a
    following current gets you there faster, burning less total fuel at
    the same STW, which is correct physics, not a bug). Duration/ETA is
    what current is supposed to change."""
    twin = VesselTwin(vessel)
    weights = combine_weights(weights_from_mission(pace=50, comfort=50), vessel.wear_policy)
    corridor = corridor_west()
    stw_ms = kn_to_ms(12)

    no_current = _CurrentField(0.0, 0.0)
    following_current = _CurrentField(0.0, 1.5)  # north-ish current, corridor legs run south

    calm_weather = no_current.sample(0, 0, 0)
    rate_no_current = twin.fuel_rate(
        v_ms=stw_ms, weather=calm_weather, heading_deg=142.7, active_engines=2
    )
    rate_with_current = twin.fuel_rate(
        v_ms=stw_ms, weather=following_current.sample(0, 0, 0), heading_deg=142.7, active_engines=2
    )
    assert rate_no_current.fuel_kg_per_h == pytest.approx(rate_with_current.fuel_kg_per_h)

    r0 = _dp_route(corridor, stw_ms, 12, 2, no_current, geo, twin, weights, 0.0)
    r1 = _dp_route(corridor, stw_ms, 12, 2, following_current, geo, twin, weights, 0.0)
    assert r0 is not None and r1 is not None
    assert r0["duration_h"] != pytest.approx(r1["duration_h"])


def test_a4_cross_current_appears_in_cts_not_just_speed(vessel, geo):
    twin = VesselTwin(vessel)
    weights = combine_weights(weights_from_mission(pace=50, comfort=50), vessel.wear_policy)
    corridor = corridor_west()
    stw_ms = kn_to_ms(12)

    cross_current = _CurrentField(2.0, 0.0)
    result = _dp_route(corridor, stw_ms, 12, 2, cross_current, geo, twin, weights, 0.0)
    assert result is not None
    assert any(lt.cts_deg != pytest.approx(lt.course_deg) for lt in result["leg_targets"])


def test_a5_no_amount_of_weighting_routes_through_a_nogo_zone(vessel, geo):
    """Land/no-go must be a hard constraint: sweep extreme Pace/Comfort
    settings and confirm no candidate's track ever enters a no-go polygon
    or land, matching the same check for every corridor leg midpoint."""
    wx = SyntheticWeatherField("calm")
    for pace, comfort in [(0, 0), (0, 100), (100, 0), (100, 100)]:
        result = optimise(
            PlanRequest(weather=wx, geography=geo, vessel=vessel, pace=pace, comfort=comfort)
        )
        for candidate in (*result.candidates, result.baseline):
            for point in candidate.track:
                assert geo.is_navigable(point.lat_deg, point.lon_deg)


def test_b5_overload_speed_is_pruned_regardless_of_pace(vessel, geo):
    """An engine-overload speed (per VesselSpec.wear_policy.max_continuous_load_fraction)
    must be pruned even at maximum schedule pressure (Pace=100)."""
    wx = SyntheticWeatherField("calm")
    overload_kn = 17.0  # see test_optimiser_regression: ~107% MCR at this vessel spec
    for pace in (0, 50, 100):
        result = optimise(
            PlanRequest(weather=wx, geography=geo, vessel=vessel, pace=pace, comfort=0)
        )
        assert all(c.speed_kn != overload_kn for c in result.candidates)
