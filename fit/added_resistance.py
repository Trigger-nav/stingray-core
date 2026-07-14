"""Added-resistance fit (ticket 0.6, ROADMAP scope item a, part 2) --
fits `core.vessel_spec.AddedResistanceCoefficients` holding calm
resistance and SFOC fixed at `fit/calm_resistance.py`'s already-fitted
values (sequential, not joint -- see that module's docstring for why).
Matches `TECHNICAL_ARCHITECTURE.md`'s component table: calm resistance is
"learned from shaft power/fuel vs STW" (near-calm segments), added
resistance from "observed speed loss vs sea state" (all segments, calm
curve now known).

Same forward-model approach as `fit/calm_resistance.py`: predicts
`fuel_kg_per_h` via `core.twin.VesselTwin.fuel_rate` for a candidate
`added_resistance` (calm/SFOC fixed) and compares directly to observed --
no inversion to an "implied power" residual, which would need solving a
nonlinear equation per segment for no benefit. Same prior-regularised
least-squares as calm resistance, over `added_resistance`'s 4 parameters
only (calm/SFOC aren't free here, so no prior penalty on them at this
stage).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
from scipy.optimize import least_squares

from core.twin import VesselTwin
from core.vessel_spec import AddedResistanceCoefficients, VesselSpec
from core.weather import WeatherSample
from fit.priors import DEFAULT_ADDED_RESISTANCE_PRIORS, AddedResistancePriors
from fit.result import FitResult
from fit.segments import SteadyStateSegment

# Proportional, not fixed-absolute -- see fit/calm_resistance.py's
# equivalent constant for why.
DEFAULT_FUEL_NOISE_STD_FRACTION = 0.03
DEFAULT_FUEL_NOISE_FLOOR_KG_PER_H = 1.0

_PARAM_NAMES = ("scale", "period_reference_s", "head_factor", "following_factor")
_BOUNDS_LO = (0.0, 1.0, 0.0, 0.0)
_BOUNDS_HI = (5.0, 30.0, 5.0, 5.0)


@dataclass(frozen=True)
class AddedResistanceFit:
    added_resistance: AddedResistanceCoefficients
    result: FitResult


def _candidate_spec(x: np.ndarray, base_spec: VesselSpec) -> VesselSpec:
    # Plain float, not numpy.float64 -- see fit/calm_resistance.py's
    # equivalent cast for why.
    scale, period_reference_s, head_factor, following_factor = (float(v) for v in x)
    added = AddedResistanceCoefficients(
        scale=scale,
        period_reference_s=period_reference_s,
        head_factor=head_factor,
        following_factor=following_factor,
    )
    return replace(base_spec, added_resistance=added)


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


def fit_added_resistance(
    segments: list[SteadyStateSegment],
    base_spec: VesselSpec,
    *,
    prior: AddedResistancePriors = DEFAULT_ADDED_RESISTANCE_PRIORS,
    fuel_noise_std_fraction: float = DEFAULT_FUEL_NOISE_STD_FRACTION,
    fuel_noise_floor_kg_per_h: float = DEFAULT_FUEL_NOISE_FLOOR_KG_PER_H,
) -> AddedResistanceFit:
    """`base_spec` must already carry the fitted `calm_resistance` and
    engine SFOC values (from `fit_calm_resistance`) -- those are held
    fixed here, not re-fit."""
    if not segments:
        raise ValueError("no segments to fit added resistance from")

    priors = (
        prior.scale,
        prior.period_reference_s,
        prior.head_factor,
        prior.following_factor,
    )
    x0 = np.array([p.mean for p in priors])

    def residuals(x: np.ndarray) -> np.ndarray:
        spec = _candidate_spec(x, base_spec)
        data_res = []
        for seg in segments:
            # seg.fuel_noise_multiplier (ticket B7 Part 3) is 1.0 for every
            # segment that never passed through fit/import_pipeline.py's
            # stamp_segment_provenance -- a no-op for every pre-B7 caller.
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

    fitted_spec = _candidate_spec(fitted, base_spec)
    data_residuals = np.array(
        [_predict_fuel_kg_per_h(fitted_spec, seg) - seg.mean_fuel_kg_per_h for seg in segments]
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
        engine_configs_present=frozenset(s.active_engines for s in segments),
    )

    return AddedResistanceFit(added_resistance=fitted_spec.added_resistance, result=result)
