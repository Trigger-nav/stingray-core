import copy

from core.vessel_spec import VesselSpec

DEFAULT_SPEC_PATH = "data/vessel_specs/mys_50m_default.yaml"


def _base_dict():
    return {
        "name": "test vessel",
        "provisional": True,
        "hull": {"length_wl_m": 45.0, "beam_wl_m": 9.0, "block_coefficient": 0.52},
        "calm_resistance": {
            "linear_coefficient": 5.0,
            "cubic_coefficient": 1.0,
            "steepening_coefficient": 1.5,
            "steepening_exponent": 4.0,
            "hull_speed_froude": 0.40,
        },
        "added_resistance": {
            "scale": 0.4,
            "period_reference_s": 7.5,
            "head_factor": 1.0,
            "following_factor": 0.25,
        },
        "engines": [
            {
                "name": "port",
                "mcr_kw": 1350.0,
                "sfoc_base_g_per_kwh": 195.0,
                "sfoc_min_load_fraction": 0.75,
                "sfoc_curvature": 2.0,
            },
            {
                "name": "starboard",
                "mcr_kw": 1350.0,
                "sfoc_base_g_per_kwh": 195.0,
                "sfoc_min_load_fraction": 0.75,
                "sfoc_curvature": 2.0,
            },
        ],
        "hotel_load_fuel_kg_per_h": 36.0,
        "fuel_density_kg_per_l": 0.85,
        "co2_per_kg_fuel": 3.15,
        "comfort": {
            "scale": 1.0,
            "hs_exponent": 1.7,
            "beam_base": 0.35,
            "beam_amplitude": 0.91,
            "head_bonus": 0.25,
            "head_bonus_threshold_deg": 150.0,
            "period_reference_s": 6.5,
            "speed_base": 0.5,
            "speed_scale_kn": 22.0,
        },
        "wear_policy": {
            "weight_eur_equivalent": 9.0,
            "max_continuous_load_fraction": 0.85,
            "slamming_hs_threshold_m": 1.8,
            "slamming_min_speed_ms": 6.69,
            "slamming_encounter_angle_deg": 140.0,
            "load_wear_scale": 1.2,
            "slam_wear_scale": 3.0,
            "single_engine_wear_bonus": 0.15,
            "load_cycling_limit": None,
        },
    }


def test_default_spec_loads_from_yaml():
    spec = VesselSpec.from_yaml(DEFAULT_SPEC_PATH)
    assert spec.name
    assert spec.provisional is True
    assert len(spec.engines) == 2
    assert spec.hull.length_wl_m == 45.0
    assert spec.wear_policy.max_continuous_load_fraction == 0.85


def test_from_dict_round_trips_values():
    spec = VesselSpec.from_dict(_base_dict())
    assert spec.calm_resistance.cubic_coefficient == 1.0
    assert spec.engines[0].mcr_kw == 1350.0
    assert spec.wear_policy.slamming_hs_threshold_m == 1.8


def test_changing_spec_changes_twin_behaviour():
    from core.twin import VesselTwin
    from core.weather import SyntheticWeatherField

    spec_a = VesselSpec.from_dict(_base_dict())
    data_b = copy.deepcopy(_base_dict())
    data_b["calm_resistance"]["cubic_coefficient"] = 5.0  # much higher resistance
    spec_b = VesselSpec.from_dict(data_b)

    twin_a = VesselTwin(spec_a)
    twin_b = VesselTwin(spec_b)
    weather = SyntheticWeatherField("calm")
    sample = weather.sample(42.0, 8.0, 0.0)

    result_a = twin_a.fuel_rate(v_ms=8.0, weather=sample, heading_deg=0.0, active_engines=2)
    result_b = twin_b.fuel_rate(v_ms=8.0, weather=sample, heading_deg=0.0, active_engines=2)
    assert result_b.fuel_kg_per_h > result_a.fuel_kg_per_h
