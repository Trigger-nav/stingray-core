from dataclasses import replace

import pytest

from core.twin import VesselTwin
from core.units import kn_to_ms
from core.vessel_spec import VesselSpec
from core.weather import WeatherSample
from fit.calm_resistance import fit_calm_resistance
from fit.segments import SteadyStateSegment

DEFAULT_SPEC_PATH = "data/vessel_specs/mys_50m_default.yaml"


@pytest.fixture
def spec():
    return VesselSpec.from_yaml(DEFAULT_SPEC_PATH)


def _calm_segment(spec: VesselSpec, speed_kn: float, active_engines: int) -> SteadyStateSegment:
    """A noiseless near-calm segment computed directly from the twin --
    fitting against this should recover close to `spec`'s own values."""
    v_ms = kn_to_ms(speed_kn)
    weather = WeatherSample(
        hs_m=0.0,
        period_peak_s=7.0,
        period_mean_s=7.0,
        wave_from_deg=0.0,
        wind_u_ms=0.0,
        wind_v_ms=0.0,
        current_u_ms=0.0,
        current_v_ms=0.0,
    )
    fuel = (
        VesselTwin(spec)
        .fuel_rate(v_ms=v_ms, weather=weather, heading_deg=180.0, active_engines=active_engines)
        .fuel_kg_per_h
    )
    return SteadyStateSegment(
        t_start_h=0.0,
        t_end_h=0.1,
        mean_stw_ms=v_ms,
        mean_heading_deg=180.0,
        active_engines=active_engines,
        mean_fuel_kg_per_h=fuel,
        mean_hs_m=0.0,
        mean_period_peak_s=7.0,
        mean_wave_from_deg=0.0,
        duration_h=0.1,
        n_samples=10,
    )


def test_fit_recovers_close_predictions_from_noiseless_multi_config_data(spec):
    segments = [
        _calm_segment(spec, v, n) for n in (1, 2) for v in (6, 8, 10, 12, 13, 14, 15, 16, 17, 18)
    ]
    fit = fit_calm_resistance(segments, spec)

    assert fit.result.engine_configs_present == frozenset({1, 2})

    # noiseless training data -> predictions from the fitted spec should
    # closely match the training observations (near-exact recovery).
    fitted_spec = replace(
        spec,
        calm_resistance=fit.calm_resistance,
        engines=tuple(
            replace(
                e,
                sfoc_base_g_per_kwh=fit.sfoc_base_g_per_kwh,
                sfoc_min_load_fraction=fit.sfoc_min_load_fraction,
                sfoc_curvature=fit.sfoc_curvature,
            )
            for e in spec.engines
        ),
    )
    twin = VesselTwin(fitted_spec)
    weather = WeatherSample(
        hs_m=0.0,
        period_peak_s=7.0,
        period_mean_s=7.0,
        wave_from_deg=0.0,
        wind_u_ms=0.0,
        wind_v_ms=0.0,
        current_u_ms=0.0,
        current_v_ms=0.0,
    )
    for seg in segments:
        predicted = twin.fuel_rate(
            v_ms=seg.mean_stw_ms,
            weather=weather,
            heading_deg=seg.mean_heading_deg,
            active_engines=seg.active_engines,
        ).fuel_kg_per_h
        assert predicted == pytest.approx(seg.mean_fuel_kg_per_h, rel=0.02)


def test_fit_raises_without_any_near_calm_segments(spec):
    rough_segment = SteadyStateSegment(
        t_start_h=0.0,
        t_end_h=0.1,
        mean_stw_ms=kn_to_ms(12),
        mean_heading_deg=180.0,
        active_engines=2,
        mean_fuel_kg_per_h=200.0,
        mean_hs_m=2.5,  # well above the default hs_threshold_m
        mean_period_peak_s=7.0,
        mean_wave_from_deg=0.0,
        duration_h=0.1,
        n_samples=10,
    )
    with pytest.raises(ValueError, match="near-calm"):
        fit_calm_resistance([rough_segment], spec)


def test_engine_configs_present_reflects_only_calm_filtered_segments(spec):
    """A single-engine-config segment that's above the calm threshold
    shouldn't count towards `engine_configs_present` -- it's excluded
    from the fit entirely."""
    calm_seg_2engine = _calm_segment(spec, 12, 2)
    rough_seg_1engine = SteadyStateSegment(
        t_start_h=0.0,
        t_end_h=0.1,
        mean_stw_ms=kn_to_ms(12),
        mean_heading_deg=180.0,
        active_engines=1,
        mean_fuel_kg_per_h=150.0,
        mean_hs_m=2.5,
        mean_period_peak_s=7.0,
        mean_wave_from_deg=0.0,
        duration_h=0.1,
        n_samples=10,
    )
    fit = fit_calm_resistance([calm_seg_2engine, rough_seg_1engine], spec)
    assert fit.result.engine_configs_present == frozenset({2})
