import pytest

from core.twin import VesselTwin, added_power_kw, calm_power_kw, encounter_angle_deg
from core.units import kn_to_ms
from core.vessel_spec import VesselSpec
from core.weather import SyntheticWeatherField, WeatherSample

DEFAULT_SPEC_PATH = "data/vessel_specs/mys_50m_default.yaml"


@pytest.fixture
def spec():
    return VesselSpec.from_yaml(DEFAULT_SPEC_PATH)


@pytest.fixture
def twin(spec):
    return VesselTwin(spec)


def test_encounter_angle_convention():
    # heading and wave-from direction coincide -> enc=0; opposed -> enc=180;
    # perpendicular -> enc=90 (matches the demo's ported formula exactly).
    assert encounter_angle_deg(heading_deg=0, from_deg=0) == pytest.approx(0.0)
    assert encounter_angle_deg(heading_deg=0, from_deg=180) == pytest.approx(180.0)
    assert encounter_angle_deg(heading_deg=0, from_deg=90) == pytest.approx(90.0)
    assert encounter_angle_deg(heading_deg=0, from_deg=270) == pytest.approx(90.0)


def test_calm_power_monotonic_increasing(spec):
    speeds_kn = [4, 8, 12, 14, 16, 17]
    powers = [calm_power_kw(kn_to_ms(v), spec) for v in speeds_kn]
    assert powers == sorted(powers)
    assert all(p > 0 for p in powers[1:])


def test_calm_power_steepens_near_hull_speed_not_pure_cubic(spec):
    # equal-ratio speed pairs: one comfortably below hull speed, one straddling it.
    low_pair_ratio = calm_power_kw(kn_to_ms(8), spec) / calm_power_kw(kn_to_ms(4), spec)
    high_pair_ratio = calm_power_kw(kn_to_ms(17), spec) / calm_power_kw(kn_to_ms(8.5), spec)
    # a pure v^3 law would give the same ~8x ratio for any doubling of speed;
    # A1 requires the near-hull-speed doubling to grow noticeably faster.
    assert high_pair_ratio > low_pair_ratio


def test_added_resistance_vanishes_at_zero_hs(spec):
    assert added_power_kw(kn_to_ms(12), 0.0, 7.5, 180.0, spec) == pytest.approx(0.0)


def test_added_resistance_is_additive_not_multiplicative(spec):
    v = kn_to_ms(12)
    calm = calm_power_kw(v, spec)
    added = added_power_kw(v, 2.0, 7.5, 180.0, spec)
    # additive: calm term is unchanged by sea state, added term is on top of it
    assert added_power_kw(v, 0.0, 7.5, 180.0, spec) == pytest.approx(0.0)
    assert calm + added == pytest.approx(
        calm_power_kw(v, spec) + added_power_kw(v, 2.0, 7.5, 180.0, spec)
    )
    assert added > 0


def test_added_resistance_is_period_dependent(spec):
    v = kn_to_ms(12)
    at_reference = added_power_kw(v, 2.0, spec.added_resistance.period_reference_s, 180.0, spec)
    far_from_reference = added_power_kw(v, 2.0, 2.0, 180.0, spec)
    assert at_reference != pytest.approx(far_from_reference)
    assert at_reference > far_from_reference


def test_single_vs_twin_engine_advantage_emerges(twin):
    calm = SyntheticWeatherField("calm")
    weather = calm.sample(42.0, 8.0, 0.0)

    low_v = kn_to_ms(6)
    single_low = twin.fuel_rate(v_ms=low_v, weather=weather, heading_deg=0.0, active_engines=1)
    twin_low = twin.fuel_rate(v_ms=low_v, weather=weather, heading_deg=0.0, active_engines=2)
    assert single_low.fuel_kg_per_h < twin_low.fuel_kg_per_h

    high_v = kn_to_ms(16)
    single_high = twin.fuel_rate(v_ms=high_v, weather=weather, heading_deg=0.0, active_engines=1)
    twin_high = twin.fuel_rate(v_ms=high_v, weather=weather, heading_deg=0.0, active_engines=2)
    assert twin_high.fuel_kg_per_h < single_high.fuel_kg_per_h


def test_fuel_rate_rejects_invalid_engine_count(twin):
    calm = SyntheticWeatherField("calm")
    weather = calm.sample(42.0, 8.0, 0.0)
    with pytest.raises(ValueError):
        twin.fuel_rate(v_ms=kn_to_ms(10), weather=weather, heading_deg=0.0, active_engines=0)
    with pytest.raises(ValueError):
        twin.fuel_rate(v_ms=kn_to_ms(10), weather=weather, heading_deg=0.0, active_engines=3)


def test_motion_beam_seas_worse_than_aligned_or_opposed(twin):
    # enc=90 (beam) should score worse (higher comfort_rate) than either
    # end of the aligned(enc=0)/opposed(enc=180) axis (demo's "beam worst").
    weather = WeatherSample(
        hs_m=2.0,
        period_peak_s=6.5,
        period_mean_s=5.2,
        wave_from_deg=90.0,
        wind_u_ms=0.0,
        wind_v_ms=0.0,
        current_u_ms=0.0,
        current_v_ms=0.0,
    )
    v = kn_to_ms(12)
    beam = twin.motion(v_ms=v, weather=weather, heading_deg=0.0)
    aligned = twin.motion(
        v_ms=v, weather=WeatherSample(**{**weather.__dict__, "wave_from_deg": 0.0}), heading_deg=0.0
    )
    opposed = twin.motion(
        v_ms=v,
        weather=WeatherSample(**{**weather.__dict__, "wave_from_deg": 180.0}),
        heading_deg=0.0,
    )
    assert beam > opposed
    assert beam > aligned


def test_wear_slam_event_triggers_above_policy_thresholds(twin, spec):
    wp = spec.wear_policy
    # heading 0 vs wave-from 180 -> enc=180, i.e. the "worst" end of the
    # ported convention (see encounter_angle_deg), same end the demo's own
    # slam condition (enc>140) fires on.
    rough_head_fast = WeatherSample(
        hs_m=wp.slamming_hs_threshold_m + 1.0,
        period_peak_s=6.5,
        period_mean_s=5.2,
        wave_from_deg=180.0,
        wind_u_ms=0.0,
        wind_v_ms=0.0,
        current_u_ms=0.0,
        current_v_ms=0.0,
    )
    fast_v = wp.slamming_min_speed_ms + kn_to_ms(2)
    result = twin.wear(
        v_ms=fast_v, weather=rough_head_fast, heading_deg=0.0, load_fraction=0.5, active_engines=2
    )
    assert result.slam_event is True


def test_wear_no_slam_in_calm_conditions(twin, spec):
    calm = SyntheticWeatherField("calm")
    weather = calm.sample(42.0, 8.0, 0.0)
    result = twin.wear(
        v_ms=kn_to_ms(10), weather=weather, heading_deg=0.0, load_fraction=0.5, active_engines=2
    )
    assert result.slam_event is False


def test_wear_overload_flag(twin, spec):
    below = twin.wear(
        v_ms=kn_to_ms(10),
        weather=SyntheticWeatherField("calm").sample(42.0, 8.0, 0.0),
        heading_deg=0.0,
        load_fraction=spec.wear_policy.max_continuous_load_fraction - 0.05,
        active_engines=2,
    )
    above = twin.wear(
        v_ms=kn_to_ms(10),
        weather=SyntheticWeatherField("calm").sample(42.0, 8.0, 0.0),
        heading_deg=0.0,
        load_fraction=spec.wear_policy.max_continuous_load_fraction + 0.05,
        active_engines=2,
    )
    assert below.overload is False
    assert above.overload is True
