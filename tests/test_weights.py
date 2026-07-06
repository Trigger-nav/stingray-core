import pytest

from core.vessel_spec import WearPolicy
from core.weights import (
    MISSION_PRESETS,
    combine_weights,
    weights_from_mission,
)


def test_weights_from_mission_no_wear_parameter():
    import inspect

    params = list(inspect.signature(weights_from_mission).parameters)
    assert params == ["pace", "comfort"]


def test_economy_pace_favours_fuel_over_time():
    economy = weights_from_mission(pace=0, comfort=50)
    schedule = weights_from_mission(pace=100, comfort=50)
    assert economy.fuel_eur_per_kg > schedule.fuel_eur_per_kg
    assert economy.time_eur_per_min < schedule.time_eur_per_min


def test_comfort_scales_with_slider():
    low = weights_from_mission(pace=50, comfort=0)
    high = weights_from_mission(pace=50, comfort=100)
    assert high.comfort_eur_per_index_point > low.comfort_eur_per_index_point
    assert low.comfort_eur_per_index_point == pytest.approx(0.0)


@pytest.mark.parametrize("pace,comfort", [(-1, 50), (101, 50), (50, -1), (50, 101)])
def test_out_of_range_sliders_raise(pace, comfort):
    with pytest.raises(ValueError):
        weights_from_mission(pace, comfort)


def test_mission_presets_have_only_pace_and_comfort():
    for name, preset in MISSION_PRESETS.items():
        assert hasattr(preset, "pace")
        assert hasattr(preset, "comfort")
        assert not hasattr(preset, "wear"), f"preset {name} should not carry a wear slider"


def test_combine_weights_uses_fixed_vessel_wear_weight():
    mission = weights_from_mission(pace=50, comfort=50)
    wear_policy = WearPolicy(
        weight_eur_equivalent=9.0,
        max_continuous_load_fraction=0.85,
        slamming_hs_threshold_m=1.8,
        slamming_min_speed_ms=6.69,
        slamming_encounter_angle_deg=140.0,
        load_wear_scale=1.2,
        slam_wear_scale=3.0,
        single_engine_wear_bonus=0.15,
    )
    combined = combine_weights(mission, wear_policy)
    assert combined.wear_eur_per_index_point == 9.0
    assert combined.fuel_eur_per_kg == mission.fuel_eur_per_kg
    assert combined.time_eur_per_min == mission.time_eur_per_min
    assert combined.comfort_eur_per_index_point == mission.comfort_eur_per_index_point
