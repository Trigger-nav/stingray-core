"""Holdout validation (ticket 0.6, ROADMAP scope item d) -- fit quality
reported as error bands from held-out data, not in-sample point
estimates. `fit/calm_resistance.py`/`fit/added_resistance.py`'s
`FitResult.residual_rmse` is training-set only and answers "did the
optimiser converge"; this module answers "how good is this fit,
honestly" -- the number that actually matters.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from core.twin import VesselTwin
from core.vessel_spec import VesselSpec
from core.weather import WeatherSample
from fit.segments import SteadyStateSegment

DEFAULT_HOLDOUT_FRACTION = 0.2
# Normal-approximation 90%-coverage half-width (1.645 sigma) -- chosen
# over empirical percentiles for stability with small holdout sets, which
# is the common case this early (few sea-days of telemetry).
NINETY_PCT_Z = 1.645


@dataclass(frozen=True)
class ValidationReport:
    n_holdout: int
    rmse_kg_per_h: float
    mean_bias_kg_per_h: float
    error_band_kg_per_h: float  # +/- half-width, 90% coverage (normal approx)


def holdout_split(
    segments: list[SteadyStateSegment],
    holdout_fraction: float = DEFAULT_HOLDOUT_FRACTION,
    rng: np.random.Generator | None = None,
) -> tuple[list[SteadyStateSegment], list[SteadyStateSegment]]:
    if rng is None:
        rng = np.random.default_rng()
    n_holdout = max(1, round(len(segments) * holdout_fraction)) if segments else 0
    indices = rng.permutation(len(segments))
    holdout_idx = set(indices[:n_holdout].tolist())
    train = [s for i, s in enumerate(segments) if i not in holdout_idx]
    holdout = [s for i, s in enumerate(segments) if i in holdout_idx]
    return train, holdout


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


def validate_fit(
    fitted_spec: VesselSpec, holdout_segments: list[SteadyStateSegment]
) -> ValidationReport:
    if not holdout_segments:
        raise ValueError("no holdout segments to validate against")
    residuals = np.array(
        [
            _predict_fuel_kg_per_h(fitted_spec, seg) - seg.mean_fuel_kg_per_h
            for seg in holdout_segments
        ]
    )
    rmse = float(np.sqrt(np.mean(residuals**2)))
    bias = float(np.mean(residuals))
    std = float(np.std(residuals))
    return ValidationReport(
        n_holdout=len(holdout_segments),
        rmse_kg_per_h=rmse,
        mean_bias_kg_per_h=bias,
        error_band_kg_per_h=NINETY_PCT_Z * std,
    )
