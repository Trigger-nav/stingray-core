"""Calm-water resistance + SFOC fit (ticket 0.6, ROADMAP scope item a,
part 1) -- fits `core.vessel_spec.CalmResistanceCurve`'s free parameters
and a shared SFOC curve jointly from near-calm steady-state segments.

**Power/SFOC identifiability degeneracy.** `fuel_kg_per_h` is a function
of total power `P(v)` and each engine's load fraction `P(v)/(n*mcr)`
jointly, where `n = active_engines`. At a single fixed `n`, this collapses
to one curve in `v` -- and many `(P-shape, SFOC-shape)` combinations can
fit that one curve equally well (a slightly steeper power curve
compensated by a shifted SFOC curve is indistinguishable from the "true"
pair when `n` never varies). Segments spanning multiple `active_engines`
values at *overlapping* speeds break this: the same true `P(v)` then gets
sampled at a different SFOC load-fraction operating point for each `n`,
which is what actually separates the two curves (`fit/synthetic.py`'s
`default_synthetic_conditions` builds exactly this coverage by default).

This function does **not** refuse single-engine-config data -- real early
telemetry may well be single-config for a while, and refusing to fit
would throw away genuinely useful calm-resistance information even where
SFOC can't be pinned down. Instead, the prior-regularised least-squares
objective below is well-posed regardless: the per-parameter prior term
`(param - prior.mean) / prior.std` is its own full-rank quadratic in
parameter space (every parameter has a prior with `std > 0`), so a data
direction with zero curvature (the degenerate case) just leaves the fit
at the prior mean along that direction -- it cannot drift to an
overfit-but-meaningless solution just because the data can't identify it.
`FitResult.engine_configs_present` and `.prior_shift_sigma` are the
diagnostic: a fit from single-config data should show small SFOC
parameter shifts (evidence the regularisation held, not that the data
confirmed the prior) -- see `tests/test_fit_acceptance.py`'s degenerate
scenario.

Sequential, not joint with added resistance, by design: this stage fits
from near-calm segments only (`hs_m <= hs_threshold_m`) and holds
`added_resistance` at whatever `base_spec` already carries (its prior
mean, until `fit/added_resistance.py`'s stage fits it) -- standard
ship-trials-analysis practice (calm curve from calm-water runs first).
Reuses `core.twin.VesselTwin.fuel_rate` end-to-end rather than
re-deriving a "calm-only" formula, so the fit is evaluated with exactly
the same physics the optimiser will later use -- the small added-
resistance contribution the near-calm filter doesn't quite zero out is
included, not hand-waved away.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
from scipy.optimize import least_squares

from core.twin import VesselTwin
from core.vessel_spec import CalmResistanceCurve, VesselSpec
from core.weather import WeatherSample
from fit.priors import (
    DEFAULT_CALM_RESISTANCE_PRIORS,
    DEFAULT_SFOC_PRIORS,
    CalmResistancePriors,
    SfocPriors,
)
from fit.result import FitResult
from fit.segments import SteadyStateSegment

DEFAULT_HS_THRESHOLD_M = 0.3
# Proportional, not a fixed absolute kg/h -- fuel rate spans roughly an
# order of magnitude between low and high speed, and measurement noise
# (real flowmeter, or this module's own synthetic generator) scales with
# it. A fixed absolute std would implicitly overweight high-fuel-rate
# (high-speed) segments and underweight low-speed ones relative to their
# actual noise level.
DEFAULT_FUEL_NOISE_STD_FRACTION = 0.03
DEFAULT_FUEL_NOISE_FLOOR_KG_PER_H = 1.0

_PARAM_NAMES = (
    "linear_coefficient",
    "cubic_coefficient",
    "steepening_coefficient",
    "steepening_exponent",
    "sfoc_base_g_per_kwh",
    "sfoc_min_load_fraction",
    "sfoc_curvature",
)

_BOUNDS_LO = (0.0, 0.0, 0.0, 1.0, 50.0, 0.05, 0.0)
_BOUNDS_HI = (100.0, 20.0, 20.0, 10.0, 400.0, 0.99, 20.0)


@dataclass(frozen=True)
class CalmResistanceFit:
    calm_resistance: CalmResistanceCurve
    sfoc_base_g_per_kwh: float
    sfoc_min_load_fraction: float
    sfoc_curvature: float
    result: FitResult


def _candidate_spec(x: np.ndarray, base_spec: VesselSpec, hull_speed_froude: float) -> VesselSpec:
    # Cast every element to plain float -- scipy hands back numpy.float64,
    # which yaml.safe_dump (fit/cli.py's --out path) can't serialise, and
    # which shouldn't leak into core.vessel_spec dataclasses regardless
    # (core/ is meant to carry plain Python values, not numpy scalars).
    lin, cub, steep_c, steep_e, sfoc_base, sfoc_min, sfoc_curv = (float(v) for v in x)
    calm = CalmResistanceCurve(
        linear_coefficient=lin,
        cubic_coefficient=cub,
        steepening_coefficient=steep_c,
        steepening_exponent=steep_e,
        hull_speed_froude=hull_speed_froude,
    )
    engines = tuple(
        replace(
            e,
            sfoc_base_g_per_kwh=sfoc_base,
            sfoc_min_load_fraction=sfoc_min,
            sfoc_curvature=sfoc_curv,
        )
        for e in base_spec.engines
    )
    return replace(base_spec, calm_resistance=calm, engines=engines)


def _predict_fuel_kg_per_h(spec: VesselSpec, seg: SteadyStateSegment) -> float:
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
    result = VesselTwin(spec).fuel_rate(
        v_ms=seg.mean_stw_ms,
        weather=weather,
        heading_deg=seg.mean_heading_deg,
        active_engines=seg.active_engines,
    )
    return result.fuel_kg_per_h


def fit_calm_resistance(
    segments: list[SteadyStateSegment],
    base_spec: VesselSpec,
    *,
    calm_prior: CalmResistancePriors = DEFAULT_CALM_RESISTANCE_PRIORS,
    sfoc_prior: SfocPriors = DEFAULT_SFOC_PRIORS,
    hs_threshold_m: float = DEFAULT_HS_THRESHOLD_M,
    fuel_noise_std_fraction: float = DEFAULT_FUEL_NOISE_STD_FRACTION,
    fuel_noise_floor_kg_per_h: float = DEFAULT_FUEL_NOISE_FLOOR_KG_PER_H,
) -> CalmResistanceFit:
    calm_segments = [s for s in segments if s.mean_hs_m <= hs_threshold_m]
    if not calm_segments:
        raise ValueError(
            f"no segments with mean_hs_m <= {hs_threshold_m} -- cannot fit calm resistance "
            "without near-calm data"
        )

    hull_speed_froude = calm_prior.hull_speed_froude.mean
    priors = (
        calm_prior.linear_coefficient,
        calm_prior.cubic_coefficient,
        calm_prior.steepening_coefficient,
        calm_prior.steepening_exponent,
        sfoc_prior.sfoc_base_g_per_kwh,
        sfoc_prior.sfoc_min_load_fraction,
        sfoc_prior.sfoc_curvature,
    )
    x0 = np.array([p.mean for p in priors])

    def residuals(x: np.ndarray) -> np.ndarray:
        spec = _candidate_spec(x, base_spec, hull_speed_froude)
        data_res = []
        for seg in calm_segments:
            # seg.fuel_noise_multiplier (ticket B7 Part 3) is 1.0 for every
            # segment that never passed through fit/import_pipeline.py's
            # stamp_segment_provenance -- this is a no-op for every
            # pre-B7 caller, including fit/synthetic.py's generator.
            noise_std = max(
                fuel_noise_floor_kg_per_h,
                fuel_noise_std_fraction * seg.mean_fuel_kg_per_h * seg.fuel_noise_multiplier,
            )
            data_res.append(
                (_predict_fuel_kg_per_h(spec, seg) - seg.mean_fuel_kg_per_h) / noise_std
            )
        prior_res = [(x[i] - priors[i].mean) / priors[i].std for i in range(len(priors))]
        return np.array(data_res + prior_res)

    solution = least_squares(residuals, x0, bounds=(_BOUNDS_LO, _BOUNDS_HI))
    fitted = solution.x

    fitted_spec = _candidate_spec(fitted, base_spec, hull_speed_froude)
    data_residuals = np.array(
        [_predict_fuel_kg_per_h(fitted_spec, seg) - seg.mean_fuel_kg_per_h for seg in calm_segments]
    )
    rmse = float(np.sqrt(np.mean(data_residuals**2)))

    params = dict(zip(_PARAM_NAMES, (float(v) for v in fitted), strict=True))
    prior_shift_sigma = {
        name: (params[name] - priors[i].mean) / priors[i].std for i, name in enumerate(_PARAM_NAMES)
    }
    result = FitResult(
        params=params,
        prior_shift_sigma=prior_shift_sigma,
        residual_rmse=rmse,
        engine_configs_present=frozenset(s.active_engines for s in calm_segments),
    )

    return CalmResistanceFit(
        calm_resistance=fitted_spec.calm_resistance,
        sfoc_base_g_per_kwh=params["sfoc_base_g_per_kwh"],
        sfoc_min_load_fraction=params["sfoc_min_load_fraction"],
        sfoc_curvature=params["sfoc_curvature"],
        result=result,
    )
