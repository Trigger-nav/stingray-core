"""The acceptance test ROADMAP.md's 0.6 row names explicitly: "generate
synthetic telemetry from a twin with known parameters + realistic noise +
injected junk segments -> pipeline recovers the known parameters within
stated tolerance and rejects the junk." Needs no real data.

Two scenarios: the main one (multi-engine-config, the identifiable
regime) and the degenerate one (single-config, amendment 1's required
graceful-degradation check).

The ground-truth spec used here is deliberately perturbed *away* from
`fit/priors.py`'s prior means (which happen to equal the shipped default
YAML's values) -- fitting a ground truth that already equals the prior
would make "recovery" trivially pass without the pipeline doing any real
work. Predictive agreement, not raw parameter closeness, is asserted:
found empirically while building this test that individual parameters
(calm power and SFOC in particular) can be poorly identified even in the
non-degenerate regime -- multiple correlated parameter combinations
produce nearly the same fuel-vs-conditions surface -- while the fitted
*function* still predicts accurately. That's the meaningful, robust
check; see fit/calm_resistance.py's docstring for the related
identifiability discussion.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from core.twin import VesselTwin
from core.units import kn_to_ms
from core.vessel_spec import VesselSpec
from core.weather import WeatherSample
from fit.pipeline import fit_twin
from fit.segments import extract_steady_state_segments
from fit.synthetic import default_synthetic_conditions, generate_synthetic_telemetry

DEFAULT_SPEC_PATH = "data/vessel_specs/mys_50m_default.yaml"

# Evaluation grid deliberately off the training conditions' exact
# speeds/sea-states (fit/synthetic.py's DEFAULT_SPEEDS_KN/DEFAULT_HS_LEVELS_M)
# -- this tests generalisation, not memorisation of training points.
EVAL_SPEEDS_KN = (9.0, 11.0, 13.0, 15.0, 17.0)
EVAL_HS_LEVELS_M = (0.0, 0.7, 1.2)
EVAL_ENGINE_CONFIGS = (1, 2)
MAX_RELATIVE_PREDICTION_ERROR = 0.15


@pytest.fixture
def base_spec():
    return VesselSpec.from_yaml(DEFAULT_SPEC_PATH)


def _perturbed_ground_truth(base_spec: VesselSpec) -> VesselSpec:
    """~10-25% off the priors in various directions -- plausible ("the
    naval-arch prior is roughly right, not exact"), but far enough that
    matching it demonstrates the fit used the data, not just the prior."""
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


def _segment_overlaps_block(seg, block) -> bool:
    return not (seg.t_end_h <= block.t_start_h or seg.t_start_h >= block.t_end_h)


def test_main_scenario_recovers_accurate_predictions_and_rejects_junk(base_spec):
    ground_truth = _perturbed_ground_truth(base_spec)
    conditions = default_synthetic_conditions()  # both engine configs, overlapping speeds

    samples, junk_blocks = generate_synthetic_telemetry(
        ground_truth, conditions, rng=np.random.default_rng(5)
    )
    fitted = fit_twin(samples, base_spec, rng=np.random.default_rng(11))

    # (1) junk rejection: no fitted segment overlaps a manoeuvring block
    # (every sample in one is corrupted, so nothing from it should survive
    # extraction intact -- tank-transfer blocks legitimately contribute
    # partial segments around the single corrupted sample, see
    # fit/synthetic.py's docstring, so they're not checked here).
    segments = extract_steady_state_segments(samples)
    manoeuvre_blocks = [b for b in junk_blocks if b.kind == "manoeuvre"]
    for block in manoeuvre_blocks:
        assert not any(_segment_overlaps_block(seg, block) for seg in segments), (
            f"a segment survived extraction fully inside a manoeuvring-junk block {block}"
        )

    # (2) predictive agreement on a held-out evaluation grid.
    max_err = _max_relative_prediction_error(ground_truth, fitted.spec)
    assert max_err < MAX_RELATIVE_PREDICTION_ERROR, f"max relative prediction error {max_err:.3f}"

    # (3) holdout validation error is consistent with real recovery, not
    # wildly overfit/underfit -- checked relative to the *overall* segment
    # population's mean fuel rate (stable across runs) rather than an
    # absolute kg/h number or a single reference point: which specific
    # segments the random holdout split picked varies run to run, and a
    # holdout that happens to land on high-speed/high-Hs (hence
    # high-absolute-fuel-rate) segments would otherwise read as a much
    # "worse" fit than an equally-good one whose holdout landed on
    # low-speed segments.
    mean_fuel = sum(s.mean_fuel_kg_per_h for s in segments) / len(segments)
    v = fitted.fit_report.validation
    assert v.rmse_kg_per_h < 0.3 * mean_fuel


def test_degenerate_single_engine_config_scenario_degrades_gracefully(base_spec):
    """Amendment 1b: single-engine-config data can't identify SFOC
    independently of the calm-power curve (see
    fit/calm_resistance.py's docstring). The fit must not silently
    produce a confident-looking but meaningless result -- the confounded
    SFOC parameters should stay close to their priors."""
    ground_truth = _perturbed_ground_truth(base_spec)
    conditions = default_synthetic_conditions(engine_configs=(2,))

    samples, _ = generate_synthetic_telemetry(
        ground_truth, conditions, rng=np.random.default_rng(3)
    )
    fitted = fit_twin(samples, base_spec, rng=np.random.default_rng(9))

    calm_result = fitted.fit_report.calm_resistance_result
    assert calm_result.engine_configs_present == frozenset({2})

    sfoc_params = ("sfoc_base_g_per_kwh", "sfoc_min_load_fraction", "sfoc_curvature")
    for name in sfoc_params:
        shift = abs(calm_result.prior_shift_sigma[name])
        assert shift < 0.5, f"{name} shifted {shift:.2f} sigma from its prior on single-config data"
