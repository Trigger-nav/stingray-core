"""Predictive-fidelity acceptance test for the import layer (ticket B7
Part 3), complementary to tests/test_fit_import_pipeline.py's amendment-1
regression tests (that file proves provenance/noise flow through; this
one proves the import layer doesn't silently corrupt data en route to the
unchanged fitting math). Mirrors ticket 0.6's own acceptance test
(tests/test_fit_acceptance.py): synthetic telemetry from a known,
deliberately off-prior VesselSpec -> round-tripped through a fabricated
monitoring-CSV file and MonitoringCsvAdapter -> fit -> predictive
agreement on a held-out eval grid, same MAX_RELATIVE_PREDICTION_ERROR.
"""

from __future__ import annotations

import csv
from dataclasses import replace
from datetime import UTC, datetime

import numpy as np
import pytest

from core.twin import VesselTwin
from core.units import kn_to_ms
from core.vessel_spec import VesselSpec
from core.weather import WeatherSample
from fit.import_adapters import MonitoringCsvAdapter
from fit.import_pipeline import canonical_rows_to_telemetry_samples
from fit.pipeline import fit_twin
from fit.synthetic import default_synthetic_conditions, generate_synthetic_telemetry

DEFAULT_SPEC_PATH = "data/vessel_specs/mys_50m_default.yaml"
EVAL_SPEEDS_KN = (9.0, 11.0, 13.0, 15.0, 17.0)
EVAL_HS_LEVELS_M = (0.0, 0.7, 1.2)
EVAL_ENGINE_CONFIGS = (1, 2)
MAX_RELATIVE_PREDICTION_ERROR = 0.15

MS_TO_KN = 1.943844


def _perturbed_ground_truth(base_spec: VesselSpec) -> VesselSpec:
    calm = replace(
        base_spec.calm_resistance,
        linear_coefficient=base_spec.calm_resistance.linear_coefficient * 1.15,
        cubic_coefficient=base_spec.calm_resistance.cubic_coefficient * 0.85,
        steepening_coefficient=base_spec.calm_resistance.steepening_coefficient * 1.2,
        steepening_exponent=base_spec.calm_resistance.steepening_exponent * 1.1,
    )
    added = replace(
        base_spec.added_resistance,
        scale=base_spec.added_resistance.scale * 1.25,
        period_reference_s=base_spec.added_resistance.period_reference_s * 0.9,
    )
    engines = tuple(
        replace(
            e,
            sfoc_base_g_per_kwh=e.sfoc_base_g_per_kwh * 1.08,
            sfoc_min_load_fraction=e.sfoc_min_load_fraction * 0.95,
            sfoc_curvature=e.sfoc_curvature * 1.2,
        )
        for e in base_spec.engines
    )
    return replace(base_spec, calm_resistance=calm, added_resistance=added, engines=engines)


def _predict(spec: VesselSpec, speed_kn: float, hs_m: float, active_engines: int) -> float:
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
    return (
        VesselTwin(spec)
        .fuel_rate(
            v_ms=kn_to_ms(speed_kn),
            weather=weather,
            heading_deg=180.0,
            active_engines=active_engines,
        )
        .fuel_kg_per_h
    )


def _max_relative_prediction_error(ground_truth: VesselSpec, fitted: VesselSpec) -> float:
    errors = [
        abs(_predict(fitted, v, hs, n) - _predict(ground_truth, v, hs, n))
        / _predict(ground_truth, v, hs, n)
        for v in EVAL_SPEEDS_KN
        for hs in EVAL_HS_LEVELS_M
        for n in EVAL_ENGINE_CONFIGS
    ]
    return max(errors)


_MONITORING_CSV_HEADER = [
    "timestamp_utc",
    "lat_deg",
    "lon_deg",
    "stw_kn",
    "heading_deg",
    "active_engines",
    "fuel_kg_per_h",
    "hs_m",
    "period_peak_s",
    "wave_from_deg",
]


def _write_monitoring_csv(path, samples, *, t0_epoch_s):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(_MONITORING_CSV_HEADER)
        for s in samples:
            ts = datetime.fromtimestamp(t0_epoch_s + s.t_h * 3600.0, tz=UTC).isoformat()
            w.writerow(
                [
                    ts,
                    "41.0",
                    "8.0",
                    f"{s.stw_ms * MS_TO_KN:.6f}",
                    f"{s.heading_deg:.6f}",
                    s.active_engines,
                    f"{s.fuel_kg_per_h:.6f}",
                    f"{s.hs_m:.6f}",
                    f"{s.period_peak_s:.6f}",
                    f"{s.wave_from_deg:.6f}",
                ]
            )


def test_round_trip_through_monitoring_csv_preserves_predictive_fidelity(tmp_path):
    base_spec = VesselSpec.from_yaml(DEFAULT_SPEC_PATH)
    ground_truth = _perturbed_ground_truth(base_spec)
    conditions = default_synthetic_conditions()
    samples, _junk_blocks = generate_synthetic_telemetry(
        ground_truth, conditions, rng=np.random.default_rng(5)
    )

    csv_path = tmp_path / "monitoring.csv"
    t0 = datetime(2026, 1, 1, tzinfo=UTC).timestamp()
    _write_monitoring_csv(csv_path, samples, t0_epoch_s=t0)

    rows = MonitoringCsvAdapter().parse(csv_path, vessel_id="mys50", passage_id="passage-1")
    round_tripped_samples = canonical_rows_to_telemetry_samples(rows)

    fitted = fit_twin(round_tripped_samples, base_spec, rng=np.random.default_rng(11))

    max_error = _max_relative_prediction_error(ground_truth, fitted.spec)
    assert max_error < MAX_RELATIVE_PREDICTION_ERROR, (
        f"round-tripping through the import layer degraded predictive fidelity: "
        f"max relative error {max_error:.3f} >= {MAX_RELATIVE_PREDICTION_ERROR}"
    )


def test_round_trip_preserves_sample_count_and_order(tmp_path):
    base_spec = VesselSpec.from_yaml(DEFAULT_SPEC_PATH)
    ground_truth = _perturbed_ground_truth(base_spec)
    conditions = default_synthetic_conditions()
    samples, _ = generate_synthetic_telemetry(
        ground_truth, conditions, rng=np.random.default_rng(3)
    )

    csv_path = tmp_path / "monitoring.csv"
    t0 = datetime(2026, 1, 1, tzinfo=UTC).timestamp()
    _write_monitoring_csv(csv_path, samples, t0_epoch_s=t0)

    rows = MonitoringCsvAdapter().parse(csv_path, vessel_id="mys50", passage_id="passage-1")
    round_tripped_samples = canonical_rows_to_telemetry_samples(rows)

    assert len(round_tripped_samples) == len(samples)
    for original, back in zip(samples, round_tripped_samples):
        assert back.stw_ms == pytest.approx(original.stw_ms, rel=1e-5)
        assert back.fuel_kg_per_h == pytest.approx(original.fuel_kg_per_h, rel=1e-5)
        assert back.active_engines == original.active_engines
