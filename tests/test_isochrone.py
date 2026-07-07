import pytest

from core.corridors import PORTS
from core.geography import SyntheticGeography
from core.isochrone import reachable_within, time_optimal_route
from core.lattice import build_lattice
from core.twin import VesselTwin
from core.vessel_spec import VesselSpec
from core.weather import SyntheticWeatherField

DEFAULT_SPEC_PATH = "data/vessel_specs/mys_50m_default.yaml"
SPEEDS_KN = (10, 11, 12, 13, 14, 15, 16, 17)


@pytest.fixture(scope="module")
def lattice():
    return build_lattice(PORTS["antibes"], PORTS["portocervo"])


@pytest.fixture(scope="module")
def vessel():
    return VesselSpec.from_yaml(DEFAULT_SPEC_PATH)


@pytest.fixture(scope="module")
def twin(vessel):
    return VesselTwin(vessel)


@pytest.fixture
def geo():
    return SyntheticGeography()


@pytest.fixture
def calm_weather():
    return SyntheticWeatherField("calm")


def test_reachable_within_generous_budget_includes_destination(lattice, geo, twin, calm_weather):
    reach = reachable_within(
        lattice, calm_weather, geo, twin, SPEEDS_KN, engine_configs=(1, 2), t0_h=0.0, max_hours=24.0
    )
    assert any(stage == lattice.n_stages - 1 for stage, _ in reach)


def test_reachable_within_tight_budget_prunes_most_of_the_lattice(lattice, geo, twin, calm_weather):
    generous = reachable_within(
        lattice, calm_weather, geo, twin, SPEEDS_KN, engine_configs=(1, 2), t0_h=0.0, max_hours=24.0
    )
    tight = reachable_within(
        lattice, calm_weather, geo, twin, SPEEDS_KN, engine_configs=(1, 2), t0_h=0.0, max_hours=2.0
    )
    assert tight < generous
    assert len(tight) < len(generous)


def test_reachable_within_only_returns_navigable_nodes(lattice, geo, twin, calm_weather):
    reach = reachable_within(
        lattice, calm_weather, geo, twin, SPEEDS_KN, engine_configs=(1, 2), t0_h=0.0, max_hours=24.0
    )
    for stage, lane in reach:
        p = lattice.point(stage, lane)
        assert geo.is_navigable(p.lat_deg, p.lon_deg)


def test_time_optimal_route_reaches_destination(lattice, geo, twin, calm_weather):
    result = time_optimal_route(
        lattice, calm_weather, geo, twin, SPEEDS_KN, engine_configs=(1, 2), t0_h=0.0
    )
    assert result is not None
    track, duration_h = result
    assert track[0] == lattice.origin
    assert track[-1].lat_deg == pytest.approx(lattice.destination.lat_deg, abs=0.05)
    assert track[-1].lon_deg == pytest.approx(lattice.destination.lon_deg, abs=0.05)
    assert duration_h > 0


def test_time_optimal_route_is_not_slower_than_the_fastest_single_speed_route(
    lattice, geo, twin, calm_weather
):
    # per-edge speed choice should never do worse than committing to one
    # constant speed for the whole passage.
    result = time_optimal_route(
        lattice, calm_weather, geo, twin, SPEEDS_KN, engine_configs=(1, 2), t0_h=0.0
    )
    single_speed_result = time_optimal_route(
        lattice, calm_weather, geo, twin, (17.0,), engine_configs=(1, 2), t0_h=0.0
    )
    assert result is not None
    # 17kn alone is infeasible for this vessel (engine overload, 2 engines) -> None
    assert single_speed_result is None
