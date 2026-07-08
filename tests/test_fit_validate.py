from dataclasses import replace

import numpy as np
import pytest

from core.twin import VesselTwin
from core.units import kn_to_ms
from core.vessel_spec import VesselSpec
from core.weather import WeatherSample
from fit.segments import SteadyStateSegment
from fit.validate import holdout_split, validate_fit

DEFAULT_SPEC_PATH = "data/vessel_specs/mys_50m_default.yaml"


@pytest.fixture
def spec():
    return VesselSpec.from_yaml(DEFAULT_SPEC_PATH)


def _segment_from_true_spec(spec: VesselSpec, speed_kn: float, hs_m: float) -> SteadyStateSegment:
    v_ms = kn_to_ms(speed_kn)
    weather = WeatherSample(
        hs_m=hs_m,
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
        .fuel_rate(v_ms=v_ms, weather=weather, heading_deg=180.0, active_engines=2)
        .fuel_kg_per_h
    )
    return SteadyStateSegment(
        t_start_h=0.0,
        t_end_h=0.1,
        mean_stw_ms=v_ms,
        mean_heading_deg=180.0,
        active_engines=2,
        mean_fuel_kg_per_h=fuel,
        mean_hs_m=hs_m,
        mean_period_peak_s=7.0,
        mean_wave_from_deg=0.0,
        duration_h=0.1,
        n_samples=10,
    )


def test_holdout_split_is_disjoint_and_covers_everything(spec):
    segments = [_segment_from_true_spec(spec, v, 0.5) for v in range(10, 20)]
    train, holdout = holdout_split(segments, holdout_fraction=0.3, rng=np.random.default_rng(0))
    assert set(id(s) for s in train).isdisjoint(id(s) for s in holdout)
    assert len(train) + len(holdout) == len(segments)
    assert len(holdout) >= 1


def test_holdout_split_is_reproducible_given_a_seed(spec):
    segments = [_segment_from_true_spec(spec, v, 0.5) for v in range(10, 20)]
    train1, holdout1 = holdout_split(segments, 0.3, rng=np.random.default_rng(42))
    train2, holdout2 = holdout_split(segments, 0.3, rng=np.random.default_rng(42))
    assert [s.mean_stw_ms for s in train1] == [s.mean_stw_ms for s in train2]
    assert [s.mean_stw_ms for s in holdout1] == [s.mean_stw_ms for s in holdout2]


def test_validate_fit_on_exact_spec_reports_near_zero_error(spec):
    holdout = [_segment_from_true_spec(spec, v, hs) for v in (10, 14, 17) for hs in (0.0, 1.0)]
    report = validate_fit(spec, holdout)
    assert report.n_holdout == len(holdout)
    assert report.rmse_kg_per_h == pytest.approx(0.0, abs=1e-6)
    assert report.error_band_kg_per_h == pytest.approx(0.0, abs=1e-6)


def test_validate_fit_on_a_wrong_spec_reports_large_error(spec):
    holdout = [_segment_from_true_spec(spec, v, hs) for v in (10, 14, 17) for hs in (0.0, 1.0)]
    wrong_spec = replace(
        spec, calm_resistance=replace(spec.calm_resistance, linear_coefficient=50.0)
    )
    report = validate_fit(wrong_spec, holdout)
    assert report.rmse_kg_per_h > 50.0


def test_validate_fit_raises_on_empty_holdout(spec):
    with pytest.raises(ValueError, match="no holdout"):
        validate_fit(spec, [])
