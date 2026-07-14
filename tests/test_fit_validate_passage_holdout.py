"""tests for fit.validate.passage_holdout_split (ticket B7 Part 4)."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from core.twin import VesselTwin
from core.units import kn_to_ms
from core.vessel_spec import VesselSpec
from core.weather import WeatherSample
from fit.calm_resistance import DEFAULT_HS_THRESHOLD_M, fit_calm_resistance
from fit.priors import DEFAULT_CALM_RESISTANCE_PRIORS, DEFAULT_SFOC_PRIORS
from fit.segments import SteadyStateSegment
from fit.validate import holdout_split, passage_holdout_split, validate_fit

DEFAULT_SPEC_PATH = "data/vessel_specs/mys_50m_default.yaml"


def _seg(passage_id="a", vessel_id=None, **overrides):
    fields = dict(
        t_start_h=0, t_end_h=1, mean_stw_ms=5.0, mean_heading_deg=0.0, active_engines=1,
        mean_fuel_kg_per_h=10.0, mean_hs_m=1.0, mean_period_peak_s=6.0, mean_wave_from_deg=0.0,
        duration_h=1.0, n_samples=1, passage_id=passage_id, vessel_id=vessel_id,
    )
    fields.update(overrides)
    return SteadyStateSegment(**fields)


# --- mechanics ---------------------------------------------------------


def test_no_group_ever_splits_across_train_and_holdout():
    segments = [_seg(passage_id=pid) for pid in ("a", "a", "b", "b", "c", "c", "c")]
    train, holdout = passage_holdout_split(
        segments, holdout_fraction=0.4, rng=np.random.default_rng(0)
    )
    train_ids = {s.passage_id for s in train}
    holdout_ids = {s.passage_id for s in holdout}
    assert train_ids.isdisjoint(holdout_ids)


def test_reproducible_given_a_fixed_seed():
    segments = [_seg(passage_id=pid) for pid in ("a", "b", "c", "d", "e")]
    t1, h1 = passage_holdout_split(segments, holdout_fraction=0.4, rng=np.random.default_rng(42))
    t2, h2 = passage_holdout_split(segments, holdout_fraction=0.4, rng=np.random.default_rng(42))
    assert [s.passage_id for s in t1] == [s.passage_id for s in t2]
    assert [s.passage_id for s in h1] == [s.passage_id for s in h2]


def test_raises_on_mixed_none_and_populated_group_ids():
    segments = [_seg(passage_id="a"), _seg(passage_id=None)]
    with pytest.raises(ValueError, match="mixed"):
        passage_holdout_split(segments, rng=np.random.default_rng(0))


def test_raises_on_fewer_than_two_groups():
    segments = [_seg(passage_id="a"), _seg(passage_id="a")]
    with pytest.raises(ValueError, match="at least 2"):
        passage_holdout_split(segments, rng=np.random.default_rng(0))


def test_raises_on_degenerate_case_named_in_review_3_passages_fraction_0_25():
    segments = [_seg(passage_id=pid) for pid in ("a", "b", "c")]
    with pytest.raises(ValueError, match="rounds down to 0"):
        passage_holdout_split(segments, holdout_fraction=0.25, rng=np.random.default_rng(0))


def test_same_3_passages_at_a_fraction_that_truncates_to_at_least_1_succeeds():
    segments = [_seg(passage_id=pid) for pid in ("a", "b", "c")]
    train, holdout = passage_holdout_split(
        segments, holdout_fraction=0.4, rng=np.random.default_rng(0)
    )
    assert len(holdout) >= 1
    assert len(train) >= 1


def test_group_by_vessel_id_variant():
    segments = [
        _seg(passage_id="p1", vessel_id="v1"),
        _seg(passage_id="p2", vessel_id="v1"),
        _seg(passage_id="p3", vessel_id="v2"),
        _seg(passage_id="p4", vessel_id="v2"),
    ]
    train, holdout = passage_holdout_split(
        segments, group_by="vessel_id", holdout_fraction=0.5, rng=np.random.default_rng(1)
    )
    train_vessels = {s.vessel_id for s in train}
    holdout_vessels = {s.vessel_id for s in holdout}
    assert train_vessels.isdisjoint(holdout_vessels)


# --- empirical demonstration: flat split flatters autocorrelated data --


def _passage_segments(
    passage_id, bias_factor, base_spec, rng, speeds_kn=(8.0, 10.0, 12.0, 14.0, 16.0)
):
    weather = WeatherSample(
        hs_m=0.1, period_peak_s=6.0, period_mean_s=6.0, wave_from_deg=0.0,
        wind_u_ms=0.0, wind_v_ms=0.0, current_u_ms=0.0, current_v_ms=0.0,
    )
    segments = []
    for v_kn in speeds_kn:
        v_ms = kn_to_ms(v_kn)
        true_fuel = VesselTwin(base_spec).fuel_rate(
            v_ms=v_ms, weather=weather, heading_deg=0.0, active_engines=2
        ).fuel_kg_per_h
        # a fixed per-passage bias (e.g. flowmeter miscalibration/fouling
        # state that day) plus small per-segment noise on top.
        biased_fuel = true_fuel * bias_factor * (1.0 + rng.normal(0.0, 0.01))
        segments.append(
            SteadyStateSegment(
                t_start_h=0.0, t_end_h=1.0, mean_stw_ms=v_ms, mean_heading_deg=0.0,
                active_engines=2, mean_fuel_kg_per_h=biased_fuel, mean_hs_m=0.1,
                mean_period_peak_s=6.0, mean_wave_from_deg=0.0, duration_h=1.0,
                n_samples=50, passage_id=passage_id,
            )
        )
    return segments


def _fit_calm_spec(base_spec, train_segments):
    fit = fit_calm_resistance(
        train_segments,
        base_spec,
        calm_prior=DEFAULT_CALM_RESISTANCE_PRIORS,
        sfoc_prior=DEFAULT_SFOC_PRIORS,
        hs_threshold_m=DEFAULT_HS_THRESHOLD_M,
    )
    return replace(
        base_spec,
        calm_resistance=fit.calm_resistance,
        engines=tuple(
            replace(
                e,
                sfoc_base_g_per_kwh=fit.sfoc_base_g_per_kwh,
                sfoc_min_load_fraction=fit.sfoc_min_load_fraction,
                sfoc_curvature=fit.sfoc_curvature,
            )
            for e in base_spec.engines
        ),
    )


def test_flat_holdout_split_hides_the_true_run_to_run_risk_a_passage_split_reveals():
    """The direct empirical demonstration of "segment-level holdout
    flatters autocorrelated historical data" (ROADMAP's B7 row) --
    six synthetic passages, each with its own fixed fuel-residual bias
    (simulating e.g. a day's flowmeter miscalibration or fouling state).

    Found empirically while building this test (not theorised in
    advance, matching this repo's own discipline): with the real,
    low-degrees-of-freedom parametric calm-resistance fit, the flat and
    grouped splits' *mean* RMSE across seeds isn't reliably ordered
    either way -- the physical curve is too smooth/global to locally
    "memorise" one passage's bias just because same-passage points leak
    into training, so there's no consistent mean-RMSE gap to assert on.
    What *is* large, consistent, and mechanistically sound: the flat
    split's reported RMSE is nearly the same **every run**, regardless of
    which specific passage happens to land in holdout (an individual-
    level random sample always blends a bit of every passage's bias) --
    while the passage-grouped split's RMSE swings widely from run to run,
    from "held out an easy, near-nominal passage" to "held out the most
    extreme outlier passage". That's the real content of "flatters": a
    flat-split error band looks falsely stable/reliable regardless of
    which real, unseen passage a deployment will actually face; a
    passage-grouped band honestly exposes that the answer depends heavily
    on which passage you get."""
    base_spec = VesselSpec.from_yaml(DEFAULT_SPEC_PATH)
    bias_factors = [0.75, 0.9, 1.0, 1.1, 1.25, 1.4]
    rng_data = np.random.default_rng(0)
    all_segments = []
    for i, bias in enumerate(bias_factors):
        all_segments += _passage_segments(f"passage-{i}", bias, base_spec, rng_data)

    flat_rmses = []
    grouped_rmses = []
    n_seeds = 12
    for seed in range(n_seeds):
        rng = np.random.default_rng(seed)
        flat_train, flat_holdout = holdout_split(all_segments, holdout_fraction=0.3, rng=rng)
        flat_spec = _fit_calm_spec(base_spec, flat_train)
        flat_report = validate_fit(flat_spec, flat_holdout)
        flat_rmses.append(flat_report.rmse_kg_per_h)

        rng2 = np.random.default_rng(seed)
        grouped_train, grouped_holdout = passage_holdout_split(
            all_segments, holdout_fraction=0.3, rng=rng2
        )
        grouped_spec = _fit_calm_spec(base_spec, grouped_train)
        grouped_report = validate_fit(grouped_spec, grouped_holdout)
        grouped_rmses.append(grouped_report.rmse_kg_per_h)

    std_flat = float(np.std(flat_rmses))
    std_grouped = float(np.std(grouped_rmses))
    assert std_grouped > 1.5 * std_flat, (
        f"expected the passage-grouped split's run-to-run RMSE spread ({std_grouped:.2f}) "
        f"to be much larger than the flat split's ({std_flat:.2f}) -- the flat split "
        "should look artificially stable regardless of which passage lands in holdout"
    )
    assert max(grouped_rmses) > max(flat_rmses), (
        "the passage-grouped split should surface a worse-case scenario "
        "the flat split's blended sampling never reveals"
    )
