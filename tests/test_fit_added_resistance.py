from dataclasses import replace

import pytest

from core.twin import VesselTwin
from core.units import kn_to_ms
from core.vessel_spec import VesselSpec
from core.weather import WeatherSample
from fit.added_resistance import fit_added_resistance
from fit.segments import SteadyStateSegment

DEFAULT_SPEC_PATH = "data/vessel_specs/mys_50m_default.yaml"


@pytest.fixture
def spec():
    return VesselSpec.from_yaml(DEFAULT_SPEC_PATH)


def _segment(
    spec: VesselSpec,
    speed_kn: float,
    hs_m: float,
    period_peak_s: float,
    heading_deg: float,
    wave_from_deg: float,
    active_engines: int = 2,
) -> SteadyStateSegment:
    v_ms = kn_to_ms(speed_kn)
    weather = WeatherSample(
        hs_m=hs_m,
        period_peak_s=period_peak_s,
        period_mean_s=period_peak_s,
        wave_from_deg=wave_from_deg,
        wind_u_ms=0.0,
        wind_v_ms=0.0,
        current_u_ms=0.0,
        current_v_ms=0.0,
    )
    fuel = (
        VesselTwin(spec)
        .fuel_rate(
            v_ms=v_ms, weather=weather, heading_deg=heading_deg, active_engines=active_engines
        )
        .fuel_kg_per_h
    )
    return SteadyStateSegment(
        t_start_h=0.0,
        t_end_h=0.1,
        mean_stw_ms=v_ms,
        mean_heading_deg=heading_deg,
        active_engines=active_engines,
        mean_fuel_kg_per_h=fuel,
        mean_hs_m=hs_m,
        mean_period_peak_s=period_peak_s,
        mean_wave_from_deg=wave_from_deg,
        duration_h=0.1,
        n_samples=10,
    )


def test_fit_recovers_close_predictions_from_noiseless_data_with_calm_and_sfoc_fixed(spec):
    """`base_spec` here already has the "correct" calm/SFOC (this is the
    unit under test in isolation -- `fit/pipeline.py`'s integration with
    the calm-resistance stage is covered by the acceptance test)."""
    segments = [
        _segment(spec, v, hs, 7.0, heading_deg=180.0, wave_from_deg=0.0)
        for v in (10, 14)
        for hs in (0.0, 0.5, 1.0, 2.0)
    ] + [
        _segment(spec, 12, 1.5, 7.0, heading_deg=heading_deg, wave_from_deg=0.0)
        for heading_deg in (0.0, 90.0, 180.0)
    ]

    fit = fit_added_resistance(segments, spec)

    fitted_spec = replace(spec, added_resistance=fit.added_resistance)
    twin = VesselTwin(fitted_spec)
    for seg in segments:
        weather = WeatherSample(
            hs_m=seg.mean_hs_m,
            period_peak_s=seg.mean_period_peak_s,
            period_mean_s=seg.mean_period_peak_s,
            wave_from_deg=seg.mean_wave_from_deg,
            wind_u_ms=0.0,
            wind_v_ms=0.0,
            current_u_ms=0.0,
            current_v_ms=0.0,
        )
        predicted = twin.fuel_rate(
            v_ms=seg.mean_stw_ms,
            weather=weather,
            heading_deg=seg.mean_heading_deg,
            active_engines=seg.active_engines,
        ).fuel_kg_per_h
        assert predicted == pytest.approx(seg.mean_fuel_kg_per_h, rel=0.02)


def test_head_seas_predicted_to_cost_more_fuel_than_following_seas(spec):
    """Sanity check the fitted model is physically sensible, not just
    numerically close on the training set: head seas should show higher
    added resistance than following seas at the same sea state."""
    segments = [
        _segment(spec, v, hs, 7.0, heading_deg=heading_deg, wave_from_deg=0.0)
        for v in (10, 12, 14)
        for hs in (0.5, 1.0, 1.5, 2.0)
        for heading_deg in (0.0, 45.0, 135.0, 180.0)
    ]
    fit = fit_added_resistance(segments, spec)
    fitted_spec = replace(spec, added_resistance=fit.added_resistance)
    twin = VesselTwin(fitted_spec)

    weather = WeatherSample(
        hs_m=2.0,
        period_peak_s=7.0,
        period_mean_s=7.0,
        wave_from_deg=0.0,
        wind_u_ms=0.0,
        wind_v_ms=0.0,
        current_u_ms=0.0,
        current_v_ms=0.0,
    )
    # encounter_angle_deg is 0 when heading == wave_from (-> following_factor,
    # per core.twin's convention, ported from the validated demo) and 180
    # when heading is opposite wave_from (-> head_factor). wave_from_deg=0
    # above, so heading_deg=0 is following seas, heading_deg=180 is head seas.
    following_fuel = twin.fuel_rate(
        v_ms=kn_to_ms(12), weather=weather, heading_deg=0.0, active_engines=2
    ).fuel_kg_per_h
    head_fuel = twin.fuel_rate(
        v_ms=kn_to_ms(12), weather=weather, heading_deg=180.0, active_engines=2
    ).fuel_kg_per_h
    assert head_fuel > following_fuel


def test_fit_raises_on_empty_segments(spec):
    with pytest.raises(ValueError, match="no segments"):
        fit_added_resistance([], spec)
